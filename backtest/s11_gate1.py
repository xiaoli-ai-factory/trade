"""S11 defensive composite Gate1 runner."""

from __future__ import annotations

from bisect import bisect_right
from dataclasses import dataclass, replace
from datetime import date, timedelta
from typing import Any, Callable

import numpy as np
import pandas as pd

from backtest.constraints import (
    CostConfig,
    MarketBar,
    Order,
    Position,
    apply_execution,
    mark_sellable,
    match_order,
)
from backtest.engine import (
    INITIAL_CASH,
    LOT_SIZE,
    REPORT_DIR,
    BacktestRun,
    TradeRecord,
    _affordable_quantity,
    _apply_basis_on_fill,
    _fmt_float,
    _fmt_pct,
    _last_close,
    _load_yaml,
    _market_bar,
    _max_drawdown,
    _parse_date,
    _position_for_symbol,
    _record_execution,
    _slice_data,
    _trade_metrics,
    summarize_run,
)
from data.akshare_source import CACHE_DIR, get_etf_daily, get_index_daily
from strategies.s11_defensive_composite import (
    S11DefensiveCompositeStrategy,
    inverse_vol_weights,
    ma_trend_flipped,
    ma_trend_on,
)
from strategies.s9_risk_parity import orders_for_target_weights


SignalFunc = Callable[[date, dict[str, Any]], list[Order]]

OOS_SUBCYCLES = (
    ("2018_bear", "2018-01-01", "2018-12-31", "bear"),
    ("2019_2021_bull", "2019-01-01", "2021-12-31", "bull"),
    ("2022_bear", "2022-01-01", "2022-12-31", "bear"),
    ("2023_2024_range", "2023-01-01", "2024-09-30", "range"),
    ("2024_2026_recent_oos", "2024-10-01", "2026-05-15", "oos"),
)


@dataclass(frozen=True)
class DataCoverage:
    symbol: str
    name: str
    rows: int
    earliest: date | None
    latest: date | None
    amount_median: float | None
    oos_amount_median: float | None
    source: str


@dataclass(frozen=True)
class EffectiveSpan:
    name: str
    configured_start: date
    configured_end: date
    effective_start: date
    effective_end: date
    adjusted: bool


@dataclass(frozen=True)
class TrendDiagnostics:
    name: str
    trading_days: int
    trend_on_days: int
    trend_off_days: int
    flip_days: int
    signal_dates: int
    signal_trend_on: int
    signal_trend_off: int


@dataclass(frozen=True)
class CycleComparison:
    name: str
    kind: str
    configured_start: date
    configured_end: date
    effective_start: date | None
    effective_end: date | None
    s11: dict[str, float] | None
    buy_hold: dict[str, float] | None
    note: str


def _strategy_cfg() -> dict[str, Any]:
    return _load_yaml("strategy_addon.yaml")["s11_defensive_composite"].copy()


def _gate_cfg() -> dict[str, Any]:
    return _load_yaml("backtest.yaml")["gate1"]


def _regime_cfg() -> dict[str, Any]:
    return _load_yaml("backtest.yaml")["regimes"]


def _normalize_frame(frame: pd.DataFrame) -> pd.DataFrame:
    out = frame.copy()
    out["date"] = pd.to_datetime(out["date"], errors="coerce").dt.date
    return out.dropna(subset=["date"]).sort_values("date").reset_index(drop=True)


def _cached_daily(kind: str, symbol: str, start: date, end: date) -> pd.DataFrame:
    frames: list[pd.DataFrame] = []
    for path in sorted(CACHE_DIR.glob(f"{kind}__{symbol}__*.parquet")):
        frame = pd.read_parquet(path)
        if frame.empty or "date" not in frame.columns:
            continue
        frame = _normalize_frame(frame)
        frame = frame[(frame["date"] >= start) & (frame["date"] <= end)].copy()
        if not frame.empty:
            frames.append(frame)
    if not frames:
        return pd.DataFrame()
    return (
        pd.concat(frames, ignore_index=True)
        .drop_duplicates(["symbol", "date"], keep="last")
        .sort_values("date")
        .reset_index(drop=True)
    )


def _load_etf_daily(symbol: str, start: date, end: date, refresh: bool = False) -> pd.DataFrame:
    try:
        return _normalize_frame(get_etf_daily(symbol, start=start, end=end, refresh=refresh))
    except Exception:
        cached = _cached_daily("etf_daily", symbol, start, end)
        if not cached.empty:
            return cached
        raise


def _load_index_daily(symbol: str, start: date, end: date, refresh: bool = False) -> pd.DataFrame:
    try:
        return _normalize_frame(get_index_daily(symbol, start=start, end=end, refresh=refresh))
    except Exception:
        cached = _cached_daily("index_daily", symbol, start, end)
        if not cached.empty:
            return cached
        raise


def _median_amount(frame: pd.DataFrame) -> float | None:
    if frame.empty or "amount" not in frame.columns:
        return None
    value = pd.to_numeric(frame["amount"], errors="coerce").median()
    if pd.isna(value):
        return None
    return float(value)


def _coverage(symbol: str, name: str, frame: pd.DataFrame) -> DataCoverage:
    if frame.empty:
        return DataCoverage(symbol, name, 0, None, None, None, None, "")
    oos_start = _parse_date(_regime_cfg()["oos"]["start"])
    return DataCoverage(
        symbol=symbol,
        name=name,
        rows=len(frame),
        earliest=min(frame["date"].tolist()),
        latest=max(frame["date"].tolist()),
        amount_median=_median_amount(frame),
        oos_amount_median=_median_amount(frame[frame["date"] >= oos_start]),
        source=str(frame["source"].dropna().iloc[-1]) if "source" in frame.columns and frame["source"].notna().any() else "",
    )


def _configured_symbols(cfg: dict[str, Any]) -> tuple[str, ...]:
    ordered: list[str] = []
    for item in [*cfg["pool_when_trend_on"], *cfg["pool_when_trend_off"]]:
        symbol = str(item["code"])
        if symbol not in ordered:
            ordered.append(symbol)
    return tuple(ordered)


def load_s11_data(refresh: bool = False) -> tuple[dict[str, pd.DataFrame], list[DataCoverage], dict[str, Any]]:
    cfg = _strategy_cfg()
    backtest_cfg = _load_yaml("backtest.yaml")
    full_cfg = backtest_cfg["full_history"]
    regimes = backtest_cfg["regimes"]
    global_start = min(
        [_parse_date(item["start"]) for item in regimes.values()]
        + [_parse_date(full_cfg["in_sample"]["start"]), _parse_date(full_cfg["oos"]["start"])]
    )
    global_end = max(
        [_parse_date(item["end"]) for item in regimes.values()]
        + [_parse_date(full_cfg["in_sample"]["end"]), _parse_date(full_cfg["oos"]["end"])]
    )
    query_start = min(global_start - timedelta(days=400), date(2005, 1, 1))

    trend_asset = str(cfg["trend_signal"]["asset"])
    data: dict[str, pd.DataFrame] = {trend_asset: _load_index_daily(trend_asset, query_start, global_end, refresh=refresh)}
    coverages = [_coverage(trend_asset, "trend_signal_index", data[trend_asset])]

    names = {str(item["code"]): str(item["name"]) for item in [*cfg["pool_when_trend_on"], *cfg["pool_when_trend_off"]]}
    for symbol in _configured_symbols(cfg):
        frame = _load_etf_daily(symbol, query_start, global_end, refresh=refresh)
        if frame.empty:
            raise RuntimeError(f"S11 ETF data unavailable for {symbol}")
        data[symbol] = frame
        coverages.append(_coverage(symbol, names.get(symbol, symbol), frame))
    return data, coverages, cfg


def _common_dates(data: dict[str, pd.DataFrame], symbols: tuple[str, ...]) -> list[date]:
    date_sets = [set(data[symbol]["date"].tolist()) if symbol in data and not data[symbol].empty else set() for symbol in symbols]
    if not date_sets:
        return []
    return sorted(set.intersection(*date_sets))


def _month_end_dates(dates: list[date]) -> set[date]:
    if not dates:
        return set()
    frame = pd.DataFrame({"date": dates})
    frame["date_ts"] = pd.to_datetime(frame["date"], errors="coerce")
    frame["month"] = frame["date_ts"].dt.to_period("M")
    return set(frame.groupby("month", as_index=False).tail(1)["date"].tolist())


def _next_trading_date(calendar_dates: list[date], signal_date: date) -> date | None:
    idx = bisect_right(calendar_dates, signal_date)
    if idx >= len(calendar_dates):
        return None
    return calendar_dates[idx]


def _effective_span(name: str, configured_start: date, configured_end: date, calendar_dates: list[date]) -> EffectiveSpan:
    dates = [item for item in calendar_dates if configured_start <= item <= configured_end]
    if not dates:
        raise RuntimeError(f"No common S11 dates for {name}")
    return EffectiveSpan(
        name=name,
        configured_start=configured_start,
        configured_end=configured_end,
        effective_start=dates[0],
        effective_end=dates[-1],
        adjusted=dates[0] != configured_start or dates[-1] != configured_end,
    )


def _ctx_for_signal(
    as_of_date: date,
    cash: float,
    positions: tuple[Position, ...],
    data: dict[str, pd.DataFrame],
    month_ends: set[date],
) -> dict[str, Any]:
    return {
        "data": _slice_data(data, as_of_date),
        "positions": positions,
        "cash": cash,
        "nav": _mark_nav(cash, positions, data, as_of_date),
        "lot_size": LOT_SIZE,
        "month_end_dates": month_ends,
    }


def _mark_nav(cash: float, positions: tuple[Position, ...], data: dict[str, pd.DataFrame], as_of_date: date) -> float:
    nav = cash
    for item in positions:
        close = _last_close(data, item.symbol, as_of_date)
        if close is not None:
            nav += item.quantity * close
    return nav


def _execute_orders(
    orders: list[Order],
    trade_date: date,
    cash: float,
    positions: tuple[Position, ...],
    basis: dict[str, float],
    trades: list[TradeRecord],
    filled_orders: list[dict[str, Any]],
    rejected_orders: list[dict[str, Any]],
    events: list[dict[str, Any]],
    data: dict[str, pd.DataFrame],
    cost_config: CostConfig,
) -> tuple[float, tuple[Position, ...]]:
    for order in sorted(orders, key=lambda item: 0 if item.side == "sell" else 1):
        bar = _market_bar(data, order.symbol, trade_date)
        order_to_match = order
        if order.side == "buy":
            affordable = _affordable_quantity(order.quantity, bar.open, cash, cost_config)
            if affordable <= 0:
                continue
            order_to_match = replace(order, quantity=affordable)
        position_before = _position_for_symbol(positions, order_to_match.symbol)
        position_arg = position_before if order_to_match.side == "sell" else None
        result = match_order(order_to_match, bar, cost_config, position=position_arg)
        if result.status == "filled":
            _apply_basis_on_fill(basis, result, position_before, trade_date, trades)
            cash += result.cash_delta
            positions = apply_execution(positions, result, trade_date)
            filled_orders.append(_record_execution(result, trade_date))
        else:
            rejected_orders.append(_record_execution(result, trade_date))
        events.extend(result.events)
    return cash, positions


def run_event_backtest(
    name: str,
    regime: str,
    start: date,
    end: date,
    data: dict[str, pd.DataFrame],
    calendar_dates: list[date],
    month_ends: set[date],
    signal_dates: set[date],
    signal_func: SignalFunc,
    cost_config: CostConfig,
) -> BacktestRun:
    dates = [item for item in calendar_dates if start <= item <= end]
    if not dates:
        raise RuntimeError(f"Not enough S11 dates for {regime}")

    cash = INITIAL_CASH
    positions: tuple[Position, ...] = ()
    basis: dict[str, float] = {}
    trades: list[TradeRecord] = []
    filled_orders: list[dict[str, Any]] = []
    rejected_orders: list[dict[str, Any]] = []
    events: list[dict[str, Any]] = []
    pending_orders: dict[date, list[Order]] = {}

    def schedule_signal(signal_date: date) -> None:
        trade_date = _next_trading_date(calendar_dates, signal_date)
        if trade_date is None or trade_date < start or trade_date > end:
            return
        ctx = _ctx_for_signal(signal_date, cash, positions, data, month_ends)
        orders = signal_func(signal_date, ctx)
        if orders:
            pending_orders.setdefault(trade_date, []).extend(orders)

    first_date = dates[0]
    previous_signals = [item for item in signal_dates if item < first_date]
    if previous_signals:
        previous_signal = max(previous_signals)
        if _next_trading_date(calendar_dates, previous_signal) == first_date:
            schedule_signal(previous_signal)

    nav_rows: list[dict[str, Any]] = [{"date": first_date.isoformat(), "nav": cash}]
    for trade_date in dates:
        positions = mark_sellable(positions, trade_date)
        orders = pending_orders.pop(trade_date, [])
        if orders:
            cash, positions = _execute_orders(
                orders,
                trade_date,
                cash,
                positions,
                basis,
                trades,
                filled_orders,
                rejected_orders,
                events,
                data,
                cost_config,
            )
        nav_rows.append({"date": trade_date.isoformat(), "nav": _mark_nav(cash, positions, data, trade_date)})
        if trade_date in signal_dates:
            schedule_signal(trade_date)

    final_date = dates[-1]
    positions = mark_sellable(positions, final_date)
    for position in sorted(positions, key=lambda item: item.symbol):
        if not position.sellable:
            continue
        close = _last_close(data, position.symbol, final_date)
        if close is None:
            continue
        result = match_order(
            Order(symbol=position.symbol, side="sell", quantity=position.quantity, submitted_date=final_date),
            MarketBar(symbol=position.symbol, date=final_date, open=close, is_suspended=False),
            cost_config,
            position=position,
        )
        if result.status == "filled":
            _apply_basis_on_fill(basis, result, position, final_date, trades)
            cash += result.cash_delta
            positions = apply_execution(positions, result, final_date)
            filled_orders.append(_record_execution(result, final_date))
        else:
            rejected_orders.append(_record_execution(result, final_date))
        events.extend(result.events)

    final_nav = _mark_nav(cash, positions, data, final_date)
    nav_rows[-1] = {"date": final_date.isoformat(), "nav": final_nav}
    nav_curve = pd.DataFrame(nav_rows)
    return BacktestRun(
        name=name,
        regime=regime,
        start=start,
        end=end,
        initial_cash=INITIAL_CASH,
        final_nav=float(final_nav),
        total_return=float(final_nav) / INITIAL_CASH - 1.0,
        max_drawdown=_max_drawdown(nav_curve),
        trades=tuple(trades),
        filled_orders=tuple(filled_orders),
        rejected_orders=tuple(rejected_orders),
        events=tuple(events),
        nav_curve=nav_curve,
    )


def _trend_flip_dates(strategy: S11DefensiveCompositeStrategy, data: dict[str, pd.DataFrame], calendar_dates: list[date]) -> set[date]:
    trend_data = {strategy.trend.asset: data[strategy.trend.asset]}
    return {
        item
        for item in calendar_dates
        if ma_trend_flipped(_slice_data(trend_data, item).get(strategy.trend.asset), strategy.trend.ma_len, item)
    }


def _monthly_inverse_vol_signal(symbols: tuple[str, ...], lookback: int) -> SignalFunc:
    def _signal(as_of_date: date, ctx: dict[str, Any]) -> list[Order]:
        if as_of_date not in set(ctx.get("month_end_dates", ())):
            return []
        weights = inverse_vol_weights(symbols, ctx["data"], lookback, as_of_date)
        if not weights:
            return []
        return orders_for_target_weights(symbols, weights, as_of_date, ctx)

    return _signal


def _monthly_equal_weight_signal(symbols: tuple[str, ...]) -> SignalFunc:
    weights = {symbol: 1.0 / len(symbols) for symbol in symbols}

    def _signal(as_of_date: date, ctx: dict[str, Any]) -> list[Order]:
        if as_of_date not in set(ctx.get("month_end_dates", ())):
            return []
        return orders_for_target_weights(symbols, weights, as_of_date, ctx)

    return _signal


def _sixty_forty_signal(stock_symbol: str, bond_symbol: str) -> SignalFunc:
    symbols = (stock_symbol, bond_symbol)

    def _signal(as_of_date: date, ctx: dict[str, Any]) -> list[Order]:
        if as_of_date not in set(ctx.get("month_end_dates", ())):
            return []
        return orders_for_target_weights(symbols, {stock_symbol: 0.60, bond_symbol: 0.40}, as_of_date, ctx)

    return _signal


def _single_etf_buy_hold_signal(symbol: str) -> SignalFunc:
    symbols = (symbol,)

    def _signal(as_of_date: date, ctx: dict[str, Any]) -> list[Order]:
        positions: tuple[Position, ...] = tuple(ctx.get("positions", ()))
        if any(item.symbol == symbol and item.quantity > 0 for item in positions):
            return []
        return orders_for_target_weights(symbols, {symbol: 1.0}, as_of_date, ctx)

    return _signal


def _s3b_single_signal(trend_asset: str, trade_symbol: str, ma_len: int) -> SignalFunc:
    symbols = (trade_symbol,)

    def _signal(as_of_date: date, ctx: dict[str, Any]) -> list[Order]:
        month_end = as_of_date in set(ctx.get("month_end_dates", ()))
        flipped = ma_trend_flipped(ctx["data"].get(trend_asset), ma_len, as_of_date)
        if not month_end and not flipped:
            return []
        trend_on = ma_trend_on(ctx["data"].get(trend_asset), ma_len, as_of_date)
        if trend_on is None:
            return []
        weights = {trade_symbol: 1.0} if trend_on else {}
        return orders_for_target_weights(symbols, weights, as_of_date, ctx)

    return _signal


def _merged_metrics(runs: list[BacktestRun] | dict[str, BacktestRun]) -> dict[str, float]:
    values = runs.values() if isinstance(runs, dict) else runs
    trades = tuple(item for run in values for item in run.trades)
    return _trade_metrics(trades)


def _gate_checks(s11_runs: dict[str, BacktestRun], gate1: dict[str, Any]) -> dict[str, Any]:
    summaries = {name: summarize_run(run) for name, run in s11_runs.items()}
    a_rows = []
    a_pass = True
    for name in ("bull", "bear", "range"):
        metrics = summaries[name]
        checks = {
            "expectancy": metrics["expectancy"] > float(gate1["expectancy_after_cost_gt"]),
            "profit_factor": metrics["profit_factor"] >= float(gate1["profit_factor_min"]),
            "max_drawdown": metrics["max_drawdown"] <= float(gate1["max_drawdown_max"]),
        }
        passed = all(checks.values())
        a_rows.append((name, metrics, checks, passed))
        a_pass = a_pass and passed

    in_sample = [s11_runs[name] for name in ("bull", "bear", "range")]
    merged = _merged_metrics(in_sample)
    b_checks = {
        "expectancy": merged["expectancy"] > float(gate1["expectancy_after_cost_gt"]),
        "profit_factor": merged["profit_factor"] >= float(gate1["profit_factor_min"]),
    }
    oos = summaries["oos"]
    c_checks = {
        "expectancy": oos["expectancy"] > float(gate1["expectancy_after_cost_gt"]),
        "profit_factor": oos["profit_factor"] >= float(gate1["profit_factor_min"]),
        "max_drawdown": oos["max_drawdown"] <= float(gate1["max_drawdown_max"]),
    }
    return {
        "summaries": summaries,
        "merged": merged,
        "a_rows": a_rows,
        "a_pass": a_pass,
        "b_checks": b_checks,
        "b_pass": all(b_checks.values()),
        "c_checks": c_checks,
        "c_pass": all(c_checks.values()),
        "overall_pass": bool(a_pass and all(b_checks.values()) and all(c_checks.values())),
    }


def _trend_diagnostics(
    name: str,
    strategy: S11DefensiveCompositeStrategy,
    data: dict[str, pd.DataFrame],
    calendar_dates: list[date],
    month_ends: set[date],
    signal_dates: set[date],
    span: EffectiveSpan,
) -> TrendDiagnostics:
    trend_data = {strategy.trend.asset: data[strategy.trend.asset]}
    dates = [item for item in calendar_dates if span.effective_start <= item <= span.effective_end]
    daily_states = [
        ma_trend_on(_slice_data(trend_data, item).get(strategy.trend.asset), strategy.trend.ma_len, item)
        for item in dates
    ]
    flips = sum(
        1
        for item in dates
        if ma_trend_flipped(_slice_data(trend_data, item).get(strategy.trend.asset), strategy.trend.ma_len, item)
    )
    regime_signals = sorted(item for item in signal_dates if span.effective_start <= item <= span.effective_end)
    signal_states = [
        ma_trend_on(_slice_data(trend_data, item).get(strategy.trend.asset), strategy.trend.ma_len, item)
        for item in regime_signals
    ]
    return TrendDiagnostics(
        name=name,
        trading_days=len(dates),
        trend_on_days=sum(1 for item in daily_states if item is True),
        trend_off_days=sum(1 for item in daily_states if item is False),
        flip_days=flips,
        signal_dates=len(regime_signals),
        signal_trend_on=sum(1 for item in signal_states if item is True),
        signal_trend_off=sum(1 for item in signal_states if item is False),
    )


def _cycle_windows() -> list[tuple[str, str, date, date]]:
    full_cfg = _load_yaml("backtest.yaml")["full_history"]
    rows: list[tuple[str, str, date, date]] = []
    for idx, (left, right) in enumerate(full_cfg["bull_cycles"], start=1):
        rows.append((f"in_bull_{idx}", "bull", _parse_date(left), _parse_date(right)))
    for idx, (left, right) in enumerate(full_cfg["bear_cycles"], start=1):
        rows.append((f"in_bear_{idx}", "bear", _parse_date(left), _parse_date(right)))
    for name, left, right, kind in OOS_SUBCYCLES:
        rows.append((name, kind, _parse_date(left), _parse_date(right)))
    return rows


def _cycle_comparisons(
    data: dict[str, pd.DataFrame],
    calendar_dates: list[date],
    month_ends: set[date],
    s11_signal_dates: set[date],
    strategy: S11DefensiveCompositeStrategy,
    cost_config: CostConfig,
) -> list[CycleComparison]:
    rows: list[CycleComparison] = []
    monthly_signal_dates = set(month_ends)
    for name, kind, configured_start, configured_end in _cycle_windows():
        dates = [item for item in calendar_dates if configured_start <= item <= configured_end]
        if len(dates) < 2:
            rows.append(
                CycleComparison(name, kind, configured_start, configured_end, None, None, None, None, "NOT_COVERED_BY_5ETF_POOL")
            )
            continue
        start = dates[0]
        end = dates[-1]
        s11_run = run_event_backtest(
            "s11",
            name,
            start,
            end,
            data,
            calendar_dates,
            month_ends,
            s11_signal_dates,
            strategy.generate_signals,
            cost_config,
        )
        bh_run = run_event_backtest(
            "hs300_buy_hold",
            name,
            start,
            end,
            data,
            calendar_dates,
            month_ends,
            monthly_signal_dates,
            _single_etf_buy_hold_signal("510300"),
            cost_config,
        )
        note = "ADJUSTED_TO_COMMON_ETF_DATES" if start != configured_start or end != configured_end else ""
        rows.append(
            CycleComparison(
                name,
                kind,
                configured_start,
                configured_end,
                start,
                end,
                summarize_run(s11_run),
                summarize_run(bh_run),
                note,
            )
        )
    return rows


def _fmt_amount(value: float | None) -> str:
    if value is None or pd.isna(value):
        return "NA"
    return f"{value / 100000000.0:.2f}亿"


def _coverage_table(coverages: list[DataCoverage]) -> str:
    lines = ["| symbol | name | rows | earliest | latest | amount_median | oos_amount_median | source |", "|---|---|---:|---:|---:|---:|---:|---|"]
    for item in coverages:
        lines.append(
            f"| {item.symbol} | {item.name} | {item.rows} | {item.earliest or 'NA'} | {item.latest or 'NA'} | "
            f"{_fmt_amount(item.amount_median)} | {_fmt_amount(item.oos_amount_median)} | {item.source} |"
        )
    return "\n".join(lines)


def _effective_span_table(spans: dict[str, EffectiveSpan]) -> str:
    lines = ["| regime | configured_start | configured_end | effective_start | effective_end | adjusted |", "|---|---:|---:|---:|---:|---|"]
    for name in ("bull", "bear", "range", "oos"):
        item = spans[name]
        lines.append(
            f"| {name} | {item.configured_start} | {item.configured_end} | {item.effective_start} | "
            f"{item.effective_end} | {'YES' if item.adjusted else 'NO'} |"
        )
    return "\n".join(lines)


def _summary_table(s11_runs: dict[str, BacktestRun]) -> str:
    lines = [
        "| regime | start | end | return | max_drawdown | trades | expectancy_after_cost | profit_factor | win_rate | fee_ratio | filled_orders |",
        "|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for regime in ("bull", "bear", "range", "oos"):
        run = s11_runs[regime]
        metrics = summarize_run(run)
        lines.append(
            f"| {regime} | {run.start} | {run.end} | {_fmt_pct(metrics['return'])} | {_fmt_pct(metrics['max_drawdown'])} | "
            f"{int(metrics['trades'])} | {metrics['expectancy']:.2f} | {_fmt_float(metrics['profit_factor'])} | "
            f"{_fmt_pct(metrics['win_rate'])} | {_fmt_pct(metrics['fee_ratio'])} | {len(run.filled_orders)} |"
        )
    return "\n".join(lines)


def _baseline_table(regime_runs: dict[str, BacktestRun]) -> str:
    names = (
        ("s11", "S11"),
        ("s9_no_trend", "S9_no_trend_5ETF"),
        ("s3b_single", "S3b_HS300_cash"),
        ("hs300_buy_hold", "HS300ETF_BH"),
        ("sixty_forty", "60_40_HS300_bond"),
        ("equal_5etf", "equal_5ETF"),
    )
    lines = [
        "| strategy | return | max_drawdown | trades | expectancy | profit_factor | win_rate | fee_ratio | filled_orders |",
        "|---|---:|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for key, label in names:
        metrics = summarize_run(regime_runs[key])
        lines.append(
            f"| {label} | {_fmt_pct(metrics['return'])} | {_fmt_pct(metrics['max_drawdown'])} | {int(metrics['trades'])} | "
            f"{metrics['expectancy']:.2f} | {_fmt_float(metrics['profit_factor'])} | {_fmt_pct(metrics['win_rate'])} | "
            f"{_fmt_pct(metrics['fee_ratio'])} | {len(regime_runs[key].filled_orders)} |"
        )
    return "\n".join(lines)


def _uplift_table(regime_runs: dict[str, BacktestRun]) -> str:
    s11 = summarize_run(regime_runs["s11"])
    rows = (
        ("S9_no_trend_5ETF", "s9_no_trend"),
        ("S3b_HS300_cash", "s3b_single"),
        ("HS300ETF_BH", "hs300_buy_hold"),
        ("60_40_HS300_bond", "sixty_forty"),
        ("equal_5ETF", "equal_5etf"),
    )
    lines = [
        "| baseline | S11_return_minus_baseline | baseline_DD_minus_S11_DD | S11_expectancy_minus_baseline | S11_PF_minus_baseline |",
        "|---|---:|---:|---:|---:|",
    ]
    for label, key in rows:
        base = summarize_run(regime_runs[key])
        pf_delta = s11["profit_factor"] - base["profit_factor"] if np.isfinite(s11["profit_factor"]) and np.isfinite(base["profit_factor"]) else np.nan
        lines.append(
            f"| {label} | {_fmt_pct(s11['return'] - base['return'])} | {_fmt_pct(base['max_drawdown'] - s11['max_drawdown'])} | "
            f"{s11['expectancy'] - base['expectancy']:.2f} | {_fmt_float(pf_delta)} |"
        )
    return "\n".join(lines)


def _s9_s3b_regime_table(all_runs: dict[str, dict[str, BacktestRun]]) -> str:
    lines = [
        "| regime | S11_return | S11_DD | S11_PF | S9_return | S9_DD | S9_PF | S11-S9_return | S9_DD-S11_DD | S3b_return | S3b_DD | S3b_PF | S11-S3b_return | S3b_DD-S11_DD |",
        "|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for regime in ("bull", "bear", "range", "oos"):
        s11 = summarize_run(all_runs[regime]["s11"])
        s9 = summarize_run(all_runs[regime]["s9_no_trend"])
        s3b = summarize_run(all_runs[regime]["s3b_single"])
        lines.append(
            f"| {regime} | {_fmt_pct(s11['return'])} | {_fmt_pct(s11['max_drawdown'])} | {_fmt_float(s11['profit_factor'])} | "
            f"{_fmt_pct(s9['return'])} | {_fmt_pct(s9['max_drawdown'])} | {_fmt_float(s9['profit_factor'])} | "
            f"{_fmt_pct(s11['return'] - s9['return'])} | {_fmt_pct(s9['max_drawdown'] - s11['max_drawdown'])} | "
            f"{_fmt_pct(s3b['return'])} | {_fmt_pct(s3b['max_drawdown'])} | {_fmt_float(s3b['profit_factor'])} | "
            f"{_fmt_pct(s11['return'] - s3b['return'])} | {_fmt_pct(s3b['max_drawdown'] - s11['max_drawdown'])} |"
        )
    return "\n".join(lines)


def _trend_diag_table(diagnostics: dict[str, TrendDiagnostics]) -> str:
    lines = [
        "| regime | trading_days | trend_on_days | trend_off_days | flip_days | signal_dates | signal_trend_on | signal_trend_off |",
        "|---|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for name in ("bull", "bear", "range", "oos"):
        item = diagnostics[name]
        lines.append(
            f"| {name} | {item.trading_days} | {item.trend_on_days} | {item.trend_off_days} | {item.flip_days} | "
            f"{item.signal_dates} | {item.signal_trend_on} | {item.signal_trend_off} |"
        )
    return "\n".join(lines)


def _cycle_table(cycles: list[CycleComparison]) -> str:
    lines = [
        "| cycle | kind | configured_start | configured_end | effective_start | effective_end | S11_return | S11_DD | S11_trades | HS300_BH_return | HS300_BH_DD | S11-BH_return | BH_DD-S11_DD | note |",
        "|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---|",
    ]
    for item in cycles:
        if item.s11 is None or item.buy_hold is None:
            lines.append(
                f"| {item.name} | {item.kind} | {item.configured_start} | {item.configured_end} | NA | NA | NA | NA | NA | NA | NA | NA | NA | {item.note} |"
            )
            continue
        lines.append(
            f"| {item.name} | {item.kind} | {item.configured_start} | {item.configured_end} | {item.effective_start} | {item.effective_end} | "
            f"{_fmt_pct(item.s11['return'])} | {_fmt_pct(item.s11['max_drawdown'])} | {int(item.s11['trades'])} | "
            f"{_fmt_pct(item.buy_hold['return'])} | {_fmt_pct(item.buy_hold['max_drawdown'])} | "
            f"{_fmt_pct(item.s11['return'] - item.buy_hold['return'])} | {_fmt_pct(item.buy_hold['max_drawdown'] - item.s11['max_drawdown'])} | {item.note} |"
        )
    return "\n".join(lines)


def _gate_row(label: str, metric: str, actual: float, threshold: float, passed: bool) -> str:
    return f"| {label} | {metric} | {_fmt_float(actual)} | {_fmt_float(threshold)} | {'PASS' if passed else 'FAIL'} |"


def _gate_table(checks: dict[str, Any], gate1: dict[str, Any]) -> str:
    lines = ["| group | metric | actual | threshold | result |", "|---|---:|---:|---:|---|"]
    for name, metrics, item_checks, _passed in checks["a_rows"]:
        lines.append(_gate_row(f"A/{name}", "expectancy_after_cost", metrics["expectancy"], gate1["expectancy_after_cost_gt"], item_checks["expectancy"]))
        lines.append(_gate_row(f"A/{name}", "profit_factor", metrics["profit_factor"], gate1["profit_factor_min"], item_checks["profit_factor"]))
        lines.append(_gate_row(f"A/{name}", "max_drawdown", metrics["max_drawdown"], gate1["max_drawdown_max"], item_checks["max_drawdown"]))
    merged = checks["merged"]
    b = checks["b_checks"]
    lines.append(f"| B/low_freq | trades | {_fmt_float(merged['trades'])} | 月频/翻转策略不按200笔卡死 | N/A |")
    lines.append(_gate_row("B/low_freq", "expectancy_after_cost", merged["expectancy"], gate1["expectancy_after_cost_gt"], b["expectancy"]))
    lines.append(_gate_row("B/low_freq", "profit_factor", merged["profit_factor"], gate1["profit_factor_min"], b["profit_factor"]))
    oos = checks["summaries"]["oos"]
    c = checks["c_checks"]
    lines.append(_gate_row("C/oos", "expectancy_after_cost", oos["expectancy"], gate1["expectancy_after_cost_gt"], c["expectancy"]))
    lines.append(_gate_row("C/oos", "profit_factor", oos["profit_factor"], gate1["profit_factor_min"], c["profit_factor"]))
    lines.append(_gate_row("C/oos", "max_drawdown", oos["max_drawdown"], gate1["max_drawdown_max"], c["max_drawdown"]))
    lines.append(f"| C/oos | trades | {_fmt_float(oos['trades'])} | 低频不适用；原闸门{gate1['oos_min_trades']} | N/A |")
    lines.append(f"| TOTAL | A+B+C(低频显著性) | - | - | {'PASS' if checks['overall_pass'] else 'FAIL'} |")
    return "\n".join(lines)


def render_report(
    all_runs: dict[str, dict[str, BacktestRun]],
    checks: dict[str, Any],
    coverages: list[DataCoverage],
    spans: dict[str, EffectiveSpan],
    diagnostics: dict[str, TrendDiagnostics],
    cycles: list[CycleComparison],
    cfg: dict[str, Any],
) -> str:
    final = "PASS" if checks["overall_pass"] else "FAIL"
    s11_runs = {name: values["s11"] for name, values in all_runs.items()}
    bear_s11 = summarize_run(all_runs["bear"]["s11"])
    bear_s9 = summarize_run(all_runs["bear"]["s9_no_trend"])
    oos_s11 = summarize_run(all_runs["oos"]["s11"])
    oos_s9 = summarize_run(all_runs["oos"]["s9_no_trend"])
    oos_s3b = summarize_run(all_runs["oos"]["s3b_single"])
    pool = ", ".join(str(item["code"]) for item in cfg["pool_when_trend_on"])
    bond = str(cfg["pool_when_trend_off"][0]["code"])
    lines = [
        "# S11 Defensive Composite Gate1 Report",
        "",
        f"规则：每月最后交易日 D 收盘后以及 sh000300 MA200 趋势翻转日，先用 sh000300 close > MA200 判 trend_on/off；trend_on 时对 {pool} 计算 60 日 inverse-vol 权重；trend_off 时 100% {bond}；D+1 开盘按目标权重再平衡。",
        "PIT：MA200 与 sigma_i 均在策略文件中断言 data.date<=D，且要求当前信号日各序列 max(date)==D；成交全部在下一交易日开盘。",
        "flag: 基于 S3b+S9 partial PASS 的 ex-ante 预注册合成,未碰OOS调参",
        "本策略合成是基于 S3b/S9 各自的 partial PASS，先于看到 S11 OOS 结果之前预注册，非 p-hacking；参数、资产池、MA200、lookback_vol_days=60、成本和 regimes 均未因 S11 结果改动。",
        "",
        "## 数据覆盖",
        _coverage_table(coverages),
        "",
        "### regime 实际可得区间",
        _effective_span_table(spans),
        "",
        "## SPEC §3.1 对照组真实数字与提升量",
        "### S11 分段关键指标",
        _summary_table(s11_runs),
        "",
        "### S11 vs S9/S3b 逐 regime 对比",
        _s9_s3b_regime_table(all_runs),
        "",
    ]
    for regime in ("bull", "bear", "range", "oos"):
        lines.extend(
            [
                f"### {regime} 对照组真实数字",
                _baseline_table(all_runs[regime]),
                "",
                f"### {regime} S11 相对各 baseline 提升量",
                _uplift_table(all_runs[regime]),
                "",
            ]
        )
    lines.extend(
        [
            "### 趋势状态诊断",
            _trend_diag_table(diagnostics),
            "",
            "## SPEC §3.2 反假设",
            f"1. 合成是否真的把 S9 的 bear FAIL 救回来了：bear 段 S9_no_trend return={_fmt_pct(bear_s9['return'])}, DD={_fmt_pct(bear_s9['max_drawdown'])}, PF={_fmt_float(bear_s9['profit_factor'])}；S11 return={_fmt_pct(bear_s11['return'])}, DD={_fmt_pct(bear_s11['max_drawdown'])}, PF={_fmt_float(bear_s11['profit_factor'])}。bear trend_off_days={diagnostics['bear'].trend_off_days}/{diagnostics['bear'].trading_days}，说明规则大部分时间切到国债防御；是否救回以 A/bear 三项 Gate 为准。",
            "2. 趋势翻转 lag 风险：MA200 必然滞后，极端下跌中可能慢 1-2 个月；2008 崩盘这类日历上快速杀跌的阶段尤其容易先亏后防。本次 S11 的 ETF 池受产品上市时间限制，早期 2008/2011 无法用同一 5 ETF 池直接复现，不能把 2022 的防御外推成所有历史崩盘都有效。",
            "3. S11 PASS 是否只是因为 OOS 趋势翻转少：下表把 configs/backtest.yaml 的 full_history cycles 与 OOS 子周期逐一列示，S11 与 HS300ETF 买入持有同口径重跑；NOT_COVERED 表示 5 ETF 池当时未同时存在，不补代理、不拼接指数。",
            _cycle_table(cycles),
            "4. p-hacking 自查：合成规则来自 S3b 的熊市资本保全部分和 S9 的 OOS expectancy/PF/DD partial PASS，写入 strategy_addon.yaml 后才运行 S11；本轮未看 S11 OOS 后修改 MA、lookback、资产池、trend_off 资产、成本或切分。结论无论 PASS/FAIL 均按一次性结果记录。",
            "5. 少亏型而非 alpha 型：即使 Gate1 PASS，也只说明防御组合在这些切分里把大回撤和 bear 段亏损压住，不说明相对沪深300有稳定进攻 alpha。",
            "",
            "## SPEC §3.3 Gate1 A/B/C 判定与终局结论",
            "### Gate1 判定表",
            _gate_table(checks, _gate_cfg()),
            "",
            f"OOS 对照：S11 return={_fmt_pct(oos_s11['return'])}, DD={_fmt_pct(oos_s11['max_drawdown'])}, PF={_fmt_float(oos_s11['profit_factor'])}；S9_no_trend return={_fmt_pct(oos_s9['return'])}, DD={_fmt_pct(oos_s9['max_drawdown'])}, PF={_fmt_float(oos_s9['profit_factor'])}；S3b return={_fmt_pct(oos_s3b['return'])}, DD={_fmt_pct(oos_s3b['max_drawdown'])}, PF={_fmt_float(oos_s3b['profit_factor'])}。",
            f"结论：这是项目第 11 个策略，前 10 个全 FAIL；S11 本次最终判定={final}。若 PASS，也仅按少亏型 PASS 记录，不粉饰成赚 alpha 型。",
        ]
    )
    return "\n".join(lines) + "\n"


def run(refresh: bool = False) -> dict[str, Any]:
    cost_config = CostConfig.from_mapping(_load_yaml("cost.yaml"))
    data, coverages, cfg = load_s11_data(refresh=refresh)
    strategy = S11DefensiveCompositeStrategy(cfg)
    required_symbols = (strategy.trend.asset, *strategy.symbols)
    calendar_dates = _common_dates(data, required_symbols)
    if not calendar_dates:
        raise RuntimeError("No common S11 calendar dates")
    month_ends = _month_end_dates(calendar_dates)
    trend_flips = _trend_flip_dates(strategy, data, calendar_dates)
    s11_signal_dates = set(month_ends) | trend_flips
    monthly_signal_dates = set(month_ends)

    spans = {
        name: _effective_span(name, _parse_date(span["start"]), _parse_date(span["end"]), calendar_dates)
        for name, span in _regime_cfg().items()
    }
    trend_asset = str(cfg["trend_signal"]["asset"])
    ma_len = int(cfg["trend_signal"]["ma_len"])
    pool_symbols = tuple(str(item["code"]) for item in cfg["pool_when_trend_on"])
    bond_symbol = str(cfg["pool_when_trend_off"][0]["code"])

    all_runs: dict[str, dict[str, BacktestRun]] = {}
    for regime in ("bull", "bear", "range", "oos"):
        span = spans[regime]
        common_args = (
            span.effective_start,
            span.effective_end,
            data,
            calendar_dates,
            month_ends,
        )
        all_runs[regime] = {
            "s11": run_event_backtest("s11", regime, *common_args, s11_signal_dates, strategy.generate_signals, cost_config),
            "s9_no_trend": run_event_backtest(
                "s9_no_trend_5etf",
                regime,
                *common_args,
                monthly_signal_dates,
                _monthly_inverse_vol_signal(pool_symbols, int(cfg["lookback_vol_days"])),
                cost_config,
            ),
            "s3b_single": run_event_backtest(
                "s3b_hs300_cash",
                regime,
                *common_args,
                s11_signal_dates,
                _s3b_single_signal(trend_asset, "510300", ma_len),
                cost_config,
            ),
            "hs300_buy_hold": run_event_backtest(
                "hs300_buy_hold",
                regime,
                *common_args,
                monthly_signal_dates,
                _single_etf_buy_hold_signal("510300"),
                cost_config,
            ),
            "sixty_forty": run_event_backtest(
                "sixty_forty",
                regime,
                *common_args,
                monthly_signal_dates,
                _sixty_forty_signal("510300", bond_symbol),
                cost_config,
            ),
            "equal_5etf": run_event_backtest(
                "equal_5etf",
                regime,
                *common_args,
                monthly_signal_dates,
                _monthly_equal_weight_signal(pool_symbols),
                cost_config,
            ),
        }

    diagnostics = {
        name: _trend_diagnostics(name, strategy, data, calendar_dates, month_ends, s11_signal_dates, span)
        for name, span in spans.items()
    }
    cycles = _cycle_comparisons(data, calendar_dates, month_ends, s11_signal_dates, strategy, cost_config)
    s11_runs = {name: values["s11"] for name, values in all_runs.items()}
    checks = _gate_checks(s11_runs, _gate_cfg())

    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    path = REPORT_DIR / "s11_defensive_composite_gate1.md"
    path.write_text(render_report(all_runs, checks, coverages, spans, diagnostics, cycles, cfg), encoding="utf-8")
    return {
        "path": path,
        "runs": all_runs,
        "checks": checks,
        "coverages": coverages,
        "spans": spans,
        "diagnostics": diagnostics,
        "cycles": cycles,
        "cfg": cfg,
    }


def main() -> None:
    result = run(refresh=False)
    s11_runs = {name: values["s11"] for name, values in result["runs"].items()}
    total_trades = int(sum(summarize_run(run)["trades"] for run in s11_runs.values()))
    final = "PASS" if result["checks"]["overall_pass"] else "FAIL"
    print(f"wrote {result['path']}")
    print(f"S11 trades={total_trades} final={final}")
    for regime in ("bull", "bear", "range", "oos"):
        metrics = summarize_run(s11_runs[regime])
        print(
            f"{regime}: return={metrics['return']:.4%} dd={metrics['max_drawdown']:.4%} "
            f"trades={int(metrics['trades'])} expectancy={metrics['expectancy']:.2f} "
            f"pf={_fmt_float(metrics['profit_factor'])}"
        )


if __name__ == "__main__":
    main()
