"""S9 risk parity / low-volatility Gate1 runner."""

from __future__ import annotations

import math
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
    RANDOM_SEED,
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
    _metric_ratio,
    _position_for_symbol,
    _record_execution,
    _ratio_note,
    _slice_data,
    _trade_metrics,
    summarize_run,
)
from data.akshare_source import CACHE_DIR, get_etf_daily
from strategies.s9_risk_parity import S9RiskParityStrategy, orders_for_target_weights


BOND_PRIMARY = "511010"
BOND_FALLBACK = "511260"
SENSITIVITY_LOOKBACKS = (30, 60, 90, 120)

SignalFunc = Callable[[date, dict[str, Any]], list[Order]]


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
class BondProbe:
    symbol: str
    rows: int
    earliest: date | None
    latest: date | None
    amount_median: float | None
    oos_amount_median: float | None
    source: str
    error: str | None = None


@dataclass(frozen=True)
class EffectiveSpan:
    regime: str
    configured_start: date
    configured_end: date
    effective_start: date
    effective_end: date
    adjusted: bool


def _strategy_cfg() -> dict[str, Any]:
    return _load_yaml("strategy_addon.yaml")["s9_risk_parity"].copy()


def _gate_cfg() -> dict[str, Any]:
    return _load_yaml("backtest.yaml")["gate1"]


def _regime_cfg() -> dict[str, Any]:
    return _load_yaml("backtest.yaml")["regimes"]


def _parse_date(value: str | date | pd.Timestamp) -> date:
    if isinstance(value, pd.Timestamp):
        return value.date()
    if isinstance(value, date):
        return value
    return pd.Timestamp(str(value)).date()


def _normalize_frame(frame: pd.DataFrame) -> pd.DataFrame:
    out = frame.copy()
    out["date"] = pd.to_datetime(out["date"], errors="coerce").dt.date
    return out.dropna(subset=["date"]).sort_values("date").reset_index(drop=True)


def _cached_etf_daily(symbol: str, start: date, end: date) -> pd.DataFrame:
    frames: list[pd.DataFrame] = []
    for path in sorted(CACHE_DIR.glob(f"etf_daily__{symbol}__*.parquet")):
        frame = pd.read_parquet(path)
        if frame.empty or "date" not in frame.columns:
            continue
        frame = _normalize_frame(frame)
        frame = frame[(frame["date"] >= start) & (frame["date"] <= end)].copy()
        if not frame.empty:
            frames.append(frame)
    if not frames:
        return pd.DataFrame()
    return pd.concat(frames, ignore_index=True).drop_duplicates(["symbol", "date"], keep="last").sort_values("date").reset_index(drop=True)


def _load_etf_daily(symbol: str, start: date, end: date, refresh: bool = False) -> pd.DataFrame:
    if not refresh:
        cached = _cached_etf_daily(symbol, start, end)
        if not cached.empty:
            return cached
    try:
        return _normalize_frame(get_etf_daily(symbol, start=start, end=end, refresh=refresh))
    except Exception:
        cached = _cached_etf_daily(symbol, start, end)
        if not cached.empty:
            return cached
        raise


def _probe_etf(symbol: str, start: date, end: date, refresh: bool = False) -> BondProbe:
    try:
        frame = _load_etf_daily(symbol, start, end, refresh=refresh)
    except Exception as exc:
        return BondProbe(symbol, 0, None, None, None, None, "", f"{type(exc).__name__}: {exc}")
    if frame.empty:
        return BondProbe(symbol, 0, None, None, None, None, "", None)
    return BondProbe(
        symbol=symbol,
        rows=len(frame),
        earliest=min(frame["date"].tolist()),
        latest=max(frame["date"].tolist()),
        amount_median=_median_amount(frame),
        oos_amount_median=_median_amount(frame[frame["date"] >= _parse_date(_regime_cfg()["oos"]["start"])]),
        source=str(frame["source"].dropna().iloc[-1]) if "source" in frame.columns and frame["source"].notna().any() else "",
    )


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
    return DataCoverage(
        symbol=symbol,
        name=name,
        rows=len(frame),
        earliest=min(frame["date"].tolist()),
        latest=max(frame["date"].tolist()),
        amount_median=_median_amount(frame),
        oos_amount_median=_median_amount(frame[frame["date"] >= _parse_date(_regime_cfg()["oos"]["start"])]),
        source=str(frame["source"].dropna().iloc[-1]) if "source" in frame.columns and frame["source"].notna().any() else "",
    )


def _select_bond_cfg(cfg: dict[str, Any], probes: dict[str, BondProbe]) -> tuple[dict[str, Any], str]:
    primary = probes.get(BOND_PRIMARY)
    if primary is not None and primary.rows > 0:
        return cfg, f"{BOND_PRIMARY} get_etf_daily 可得，按预注册配置使用。"

    fallback = probes.get(BOND_FALLBACK)
    if fallback is None or fallback.rows <= 0:
        raise RuntimeError(f"Neither {BOND_PRIMARY} nor {BOND_FALLBACK} bond ETF data is available")

    selected = cfg.copy()
    pool = []
    for item in cfg["pool"]:
        if str(item["code"]) == BOND_PRIMARY:
            pool.append({"code": BOND_FALLBACK, "name": "国债ETF(511260替代)"})
        else:
            pool.append(dict(item))
    selected["pool"] = pool
    return selected, f"{BOND_PRIMARY} get_etf_daily 不可得，使用 {BOND_FALLBACK} 替代；替代基于可得性/流动性而非收益。"


def load_s9_data(refresh: bool = False) -> tuple[dict[str, pd.DataFrame], list[DataCoverage], dict[str, BondProbe], dict[str, Any], str]:
    base_cfg = _strategy_cfg()
    regimes = _regime_cfg()
    global_start = min(_parse_date(item["start"]) for item in regimes.values())
    global_end = max(_parse_date(item["end"]) for item in regimes.values())
    max_lookback = max(max(SENSITIVITY_LOOKBACKS), int(base_cfg["lookback_vol_days"]))
    query_start = max(global_start - timedelta(days=max(max_lookback * 5, 365)), date(2019, 1, 1))

    probes = {
        BOND_PRIMARY: _probe_etf(BOND_PRIMARY, query_start, global_end, refresh=refresh),
        BOND_FALLBACK: _probe_etf(BOND_FALLBACK, query_start, global_end, refresh=refresh),
    }
    cfg, bond_note = _select_bond_cfg(base_cfg, probes)

    data: dict[str, pd.DataFrame] = {}
    coverages: list[DataCoverage] = []
    for item in cfg["pool"]:
        symbol = str(item["code"])
        name = str(item["name"])
        frame = _load_etf_daily(symbol, query_start, global_end, refresh=refresh)
        if frame.empty:
            raise RuntimeError(f"S9 ETF data unavailable for {symbol}")
        data[symbol] = frame
        coverages.append(_coverage(symbol, name, frame))
    return data, coverages, probes, cfg, bond_note


def _common_dates(data: dict[str, pd.DataFrame]) -> list[date]:
    date_sets = [set(frame["date"].tolist()) if not frame.empty else set() for frame in data.values()]
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


def _effective_span(regime: str, calendar_dates: list[date]) -> EffectiveSpan:
    span = _regime_cfg()[regime]
    configured_start = _parse_date(span["start"])
    configured_end = _parse_date(span["end"])
    dates = [item for item in calendar_dates if configured_start <= item <= configured_end]
    if not dates:
        raise RuntimeError(f"No common S9 ETF dates for {regime}")
    return EffectiveSpan(
        regime=regime,
        configured_start=configured_start,
        configured_end=configured_end,
        effective_start=dates[0],
        effective_end=dates[-1],
        adjusted=dates[0] != configured_start or dates[-1] != configured_end,
    )


def _mark_nav(cash: float, positions: tuple[Position, ...], data: dict[str, pd.DataFrame], as_of_date: date) -> float:
    nav = cash
    for item in positions:
        close = _last_close(data, item.symbol, as_of_date)
        if close is not None:
            nav += item.quantity * close
    return nav


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


def run_monthly_backtest(
    name: str,
    regime: str,
    start: date,
    end: date,
    data: dict[str, pd.DataFrame],
    calendar_dates: list[date],
    month_ends: set[date],
    signal_func: SignalFunc,
    cost_config: CostConfig,
) -> BacktestRun:
    dates = [item for item in calendar_dates if start <= item <= end]
    if not dates:
        raise RuntimeError(f"Not enough S9 dates for {regime}")

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
    previous_signals = [item for item in month_ends if item < first_date]
    if previous_signals:
        previous_month_end = max(previous_signals)
        if _next_trading_date(calendar_dates, previous_month_end) == first_date:
            schedule_signal(previous_month_end)

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
        if trade_date in month_ends:
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


def _monthly_weights_signal(symbols: tuple[str, ...], weights_func: Callable[[date], dict[str, float]]) -> SignalFunc:
    def _signal(as_of_date: date, ctx: dict[str, Any]) -> list[Order]:
        if as_of_date not in set(ctx.get("month_end_dates", ())):
            return []
        weights = weights_func(as_of_date)
        return orders_for_target_weights(symbols, weights, as_of_date, ctx)

    return _signal


def _equal_weight_signal(symbols: tuple[str, ...]) -> SignalFunc:
    weight = 1.0 / len(symbols)
    return _monthly_weights_signal(symbols, lambda _date: {symbol: weight for symbol in symbols})


def _sixty_forty_signal(symbols: tuple[str, ...], bond_symbol: str) -> SignalFunc:
    def _weights(_date: date) -> dict[str, float]:
        return {"510300": 0.60, bond_symbol: 0.40}

    return _monthly_weights_signal(symbols, _weights)


def _random_weight_signal(symbols: tuple[str, ...]) -> SignalFunc:
    def _weights(as_of_date: date) -> dict[str, float]:
        rng = np.random.default_rng(RANDOM_SEED + as_of_date.toordinal())
        raw = rng.random(len(symbols))
        total = float(raw.sum())
        return {symbol: float(value / total) for symbol, value in zip(symbols, raw, strict=True)}

    return _monthly_weights_signal(symbols, _weights)


def _single_etf_buy_hold_signal(symbol: str) -> SignalFunc:
    symbols = (symbol,)

    def _signal(as_of_date: date, ctx: dict[str, Any]) -> list[Order]:
        positions: tuple[Position, ...] = tuple(ctx.get("positions", ()))
        if any(item.symbol == symbol and item.quantity > 0 for item in positions):
            return []
        return orders_for_target_weights(symbols, {symbol: 1.0}, as_of_date, ctx)

    return _signal


def _merged_metrics(runs: list[BacktestRun] | dict[str, BacktestRun]) -> dict[str, float]:
    values = runs.values() if isinstance(runs, dict) else runs
    trades = tuple(item for run in values for item in run.trades)
    return _trade_metrics(trades)


def _gate_checks(s9_runs: dict[str, BacktestRun], gate1: dict[str, Any]) -> dict[str, Any]:
    summaries = {name: summarize_run(run) for name, run in s9_runs.items()}
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

    in_sample = [s9_runs[name] for name in ("bull", "bear", "range")]
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


def _summary_table(s9_runs: dict[str, BacktestRun]) -> str:
    lines = [
        "| regime | start | end | return | max_drawdown | trades | expectancy_after_cost | profit_factor | win_rate | fee_ratio | filled_orders |",
        "|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for regime in ("bull", "bear", "range", "oos"):
        run = s9_runs[regime]
        metrics = summarize_run(run)
        lines.append(
            f"| {regime} | {run.start} | {run.end} | {_fmt_pct(metrics['return'])} | {_fmt_pct(metrics['max_drawdown'])} | "
            f"{int(metrics['trades'])} | {metrics['expectancy']:.2f} | {_fmt_float(metrics['profit_factor'])} | "
            f"{_fmt_pct(metrics['win_rate'])} | {_fmt_pct(metrics['fee_ratio'])} | {len(run.filled_orders)} |"
        )
    return "\n".join(lines)


def _comparison_table(regime_runs: dict[str, BacktestRun]) -> str:
    s9_m = summarize_run(regime_runs["s9"])
    equal_m = summarize_run(regime_runs["equal_weight_monthly"])
    sixty_m = summarize_run(regime_runs["sixty_forty_monthly"])
    hs300_m = summarize_run(regime_runs["hs300_buy_hold"])
    random_m = summarize_run(regime_runs["random_weight_monthly"])
    rows = [
        ("return", s9_m["return"], equal_m["return"], sixty_m["return"], hs300_m["return"], random_m["return"], True),
        ("max_drawdown", s9_m["max_drawdown"], equal_m["max_drawdown"], sixty_m["max_drawdown"], hs300_m["max_drawdown"], random_m["max_drawdown"], True),
        ("trades", s9_m["trades"], equal_m["trades"], sixty_m["trades"], hs300_m["trades"], random_m["trades"], False),
        ("fee_ratio", s9_m["fee_ratio"], equal_m["fee_ratio"], sixty_m["fee_ratio"], hs300_m["fee_ratio"], random_m["fee_ratio"], True),
    ]
    lines = [
        "| metric | S9 | equal_weight_monthly | 60_40_monthly | HS300ETF_BH | random_weight_monthly | S9/EW | S9/60_40 | S9/HS300 | S9/random | note |",
        "|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---|",
    ]
    for metric, s9_v, equal_v, sixty_v, hs300_v, random_v, pct in rows:
        ratios = (
            _metric_ratio(s9_v, equal_v),
            _metric_ratio(s9_v, sixty_v),
            _metric_ratio(s9_v, hs300_v),
            _metric_ratio(s9_v, random_v),
        )
        fmt = _fmt_pct if pct else _fmt_float
        lines.append(
            f"| {metric} | {fmt(s9_v)} | {fmt(equal_v)} | {fmt(sixty_v)} | {fmt(hs300_v)} | {fmt(random_v)} | "
            f"{_fmt_float(ratios[0])} | {_fmt_float(ratios[1])} | {_fmt_float(ratios[2])} | {_fmt_float(ratios[3])} | {_ratio_note(*ratios)} |"
        )
    return "\n".join(lines)


def _coverage_table(coverages: list[DataCoverage]) -> str:
    lines = ["| symbol | name | rows | earliest | latest | amount_median | oos_amount_median | source |", "|---|---|---:|---:|---:|---:|---:|---|"]
    for item in coverages:
        lines.append(
            f"| {item.symbol} | {item.name} | {item.rows} | {item.earliest or 'NA'} | {item.latest or 'NA'} | "
            f"{_fmt_amount(item.amount_median)} | {_fmt_amount(item.oos_amount_median)} | {item.source} |"
        )
    return "\n".join(lines)


def _bond_probe_table(probes: dict[str, BondProbe]) -> str:
    lines = ["| symbol | rows | earliest | latest | amount_median | oos_amount_median | source | error |", "|---|---:|---:|---:|---:|---:|---|---|"]
    for symbol in (BOND_PRIMARY, BOND_FALLBACK):
        item = probes[symbol]
        lines.append(
            f"| {item.symbol} | {item.rows} | {item.earliest or 'NA'} | {item.latest or 'NA'} | "
            f"{_fmt_amount(item.amount_median)} | {_fmt_amount(item.oos_amount_median)} | {item.source} | {item.error or ''} |"
        )
    return "\n".join(lines)


def _fmt_amount(value: float | None) -> str:
    if value is None or pd.isna(value):
        return "NA"
    return f"{value / 100000000.0:.2f}亿"


def _effective_span_table(spans: dict[str, EffectiveSpan]) -> str:
    lines = ["| regime | configured_start | configured_end | effective_start | effective_end | adjusted |", "|---|---:|---:|---:|---:|---|"]
    for name in ("bull", "bear", "range", "oos"):
        item = spans[name]
        lines.append(
            f"| {name} | {item.configured_start} | {item.configured_end} | {item.effective_start} | "
            f"{item.effective_end} | {'YES' if item.adjusted else 'NO'} |"
        )
    return "\n".join(lines)


def _insample_oos_table(s9_runs: dict[str, BacktestRun]) -> str:
    in_runs = [s9_runs[name] for name in ("bull", "bear", "range")]
    in_metrics = _merged_metrics(in_runs)
    oos = summarize_run(s9_runs["oos"])
    avg_return = float(np.mean([item.total_return for item in in_runs]))
    worst_dd = max(item.max_drawdown for item in in_runs)
    lines = [
        "| span | avg/period_return | trades | expectancy | profit_factor | win_rate | worst/max_drawdown |",
        "|---|---:|---:|---:|---:|---:|---:|",
        f"| in_sample(bull+bear+range) | {_fmt_pct(avg_return)} | {int(in_metrics['trades'])} | {in_metrics['expectancy']:.2f} | {_fmt_float(in_metrics['profit_factor'])} | {_fmt_pct(in_metrics['win_rate'])} | {_fmt_pct(worst_dd)} |",
        f"| oos | {_fmt_pct(oos['return'])} | {int(oos['trades'])} | {oos['expectancy']:.2f} | {_fmt_float(oos['profit_factor'])} | {_fmt_pct(oos['win_rate'])} | {_fmt_pct(oos['max_drawdown'])} |",
    ]
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
    lines.append(f"| B/low_freq | trades | {_fmt_float(merged['trades'])} | 月频策略不按200笔卡死 | N/A |")
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


def _buy_hold_return(data: dict[str, pd.DataFrame], symbol: str, start: date, end: date) -> float | None:
    frame = data[symbol]
    rows = frame[(frame["date"] >= start) & (frame["date"] <= end)].sort_values("date")
    if len(rows) < 2:
        return None
    first = pd.to_numeric(rows["close"], errors="coerce").iloc[0]
    last = pd.to_numeric(rows["close"], errors="coerce").iloc[-1]
    if pd.isna(first) or pd.isna(last) or float(first) <= 0.0:
        return None
    return float(last) / float(first) - 1.0


def _weight_diagnostic_table(
    cfg: dict[str, Any],
    data: dict[str, pd.DataFrame],
    calendar_dates: list[date],
    month_ends: set[date],
    spans: dict[str, EffectiveSpan],
) -> str:
    strategy = S9RiskParityStrategy(cfg)
    start = spans["bull"].effective_start
    end = spans["oos"].effective_end
    weight_rows: list[dict[str, float]] = []
    for signal_date in sorted(item for item in month_ends if start <= item <= end):
        ctx = {
            "data": _slice_data(data, signal_date),
            "positions": (),
            "cash": INITIAL_CASH,
            "nav": INITIAL_CASH,
            "lot_size": LOT_SIZE,
            "month_end_dates": month_ends,
        }
        weights = strategy.target_weights(signal_date, ctx)
        if weights:
            weight_rows.append(weights)
    avg_weights = {symbol: float(np.mean([row.get(symbol, 0.0) for row in weight_rows])) if weight_rows else 0.0 for symbol in strategy.symbols}
    lines = [
        "| symbol | name | avg_S9_weight | buy_hold_return_gate_span | amount_median |",
        "|---|---|---:|---:|---:|",
    ]
    names = {str(item["code"]): str(item["name"]) for item in cfg["pool"]}
    for symbol, weight in sorted(avg_weights.items(), key=lambda item: item[1], reverse=True):
        amount = _median_amount(data[symbol][(data[symbol]["date"] >= start) & (data[symbol]["date"] <= end)])
        lines.append(
            f"| {symbol} | {names.get(symbol, symbol)} | {_fmt_pct(weight)} | "
            f"{_fmt_pct(_buy_hold_return(data, symbol, start, end))} | {_fmt_amount(amount)} |"
        )
    heaviest = max(avg_weights.items(), key=lambda item: item[1])[0] if avg_weights else "NA"
    chip_return = _buy_hold_return(data, "159995", start, end) if "159995" in data else None
    heavy_return = _buy_hold_return(data, heaviest, start, end) if heaviest in data else None
    lines.extend(
        [
            "",
            f"低波动陷阱检查：平均权重最高={heaviest}，Gate span 买入持有收益={_fmt_pct(heavy_return)}；159995 芯片ETF同期={_fmt_pct(chip_return)}。若最高权重资产长期收益低于高波动芯片，S9 的收益拖累来自机制本身而非调参问题。",
        ]
    )
    return "\n".join(lines)


def _sensitivity_table(
    cfg: dict[str, Any],
    data: dict[str, pd.DataFrame],
    calendar_dates: list[date],
    month_ends: set[date],
    spans: dict[str, EffectiveSpan],
    cost_config: CostConfig,
) -> str:
    lines = [
        "| lookback_vol_days | in_sample_avg_return | in_sample_worst_DD | trades | expectancy | profit_factor | win_rate |",
        "|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for lookback in SENSITIVITY_LOOKBACKS:
        test_cfg = cfg.copy()
        test_cfg["lookback_vol_days"] = lookback
        strategy = S9RiskParityStrategy(test_cfg)
        runs = []
        for regime in ("bull", "bear", "range"):
            span = spans[regime]
            runs.append(
                run_monthly_backtest(
                    "s9_sensitivity",
                    regime,
                    span.effective_start,
                    span.effective_end,
                    data,
                    calendar_dates,
                    month_ends,
                    strategy.generate_signals,
                    cost_config,
                )
            )
        metrics = _merged_metrics(runs)
        lines.append(
            f"| {lookback} | {_fmt_pct(float(np.mean([item.total_return for item in runs])))} | "
            f"{_fmt_pct(max(item.max_drawdown for item in runs))} | {int(metrics['trades'])} | "
            f"{metrics['expectancy']:.2f} | {_fmt_float(metrics['profit_factor'])} | {_fmt_pct(metrics['win_rate'])} |"
        )
    return "\n".join(lines)


def render_report(
    runs: dict[str, dict[str, BacktestRun]],
    checks: dict[str, Any],
    coverages: list[DataCoverage],
    probes: dict[str, BondProbe],
    cfg: dict[str, Any],
    bond_note: str,
    spans: dict[str, EffectiveSpan],
    data: dict[str, pd.DataFrame],
    calendar_dates: list[date],
    month_ends: set[date],
) -> str:
    gate1 = _gate_cfg()
    cost_config = CostConfig.from_mapping(_load_yaml("cost.yaml"))
    s9_runs = {name: values["s9"] for name, values in runs.items()}
    final = "PASS" if checks["overall_pass"] else "FAIL"
    symbols = ", ".join(str(item["code"]) for item in cfg["pool"])
    lines = [
        "# S9 Risk Parity / 低波动策略 Gate1 Report",
        "",
        f"规则：月末 D 收盘后，对池中 ETF 过去 lookback_vol_days={cfg['lookback_vol_days']} 个日日收益计算标准差 sigma_i，目标权重 w_i=(1/sigma_i)/sum(1/sigma_j)，下月首个交易日开盘只交易目标持仓与当前持仓差额。",
        f"资产池：{symbols}。本次使用低频显著性原则；月频策略不按高换手 trades>=200 / oos_min_trades=60 卡死，但笔数如实列示。",
        "PIT：策略文件在 target_weights/generate_signals 中断言所有输入 data.date<=as_of_date，波动率只由 D 及以前的收盘收益计算。",
        "",
        "## 数据覆盖",
        "### 国债 ETF get_etf_daily 实测",
        _bond_probe_table(probes),
        "",
        bond_note,
        "",
        "### S9 ETF 数据覆盖",
        _coverage_table(coverages),
        "",
        "### regime 实际可得区间",
        _effective_span_table(spans),
        "",
        "## S9 分段关键指标",
        _summary_table(s9_runs),
        "",
        "## in-sample vs OOS 差异",
        _insample_oos_table(s9_runs),
        "",
        "## 对照组 ratio 表",
    ]
    for regime in ("bull", "bear", "range", "oos"):
        lines.extend([f"### {regime}", _comparison_table(runs[regime]), ""])
    lines.extend(
        [
            "对照组定义：等权月度再平衡、60/40(510300/国债ETF)月度再平衡、510300ETF 买入持有、随机权重月度再平衡。ratio>2x 只标注调查，不作为调参依据。",
            "",
            "## 反假设列表",
            "1. A股低波动陷阱：低波动资产若长期跑输高波动资产，反波动权重会把资金压到低收益资产上；下表用固定规则生成后的平均权重与同期买入持有收益检查。",
            _weight_diagnostic_table(cfg, data, calendar_dates, month_ends, spans),
            f"2. 国债ETF流动性是否真够撑 risk parity：上方数据覆盖表列出全段与 OOS 成交额中位数；本次 511010 OOS 成交额中位数={_fmt_amount(probes[BOND_PRIMARY].oos_amount_median)}，对 100 万初始资金不构成主要约束，但大资金冲击成本未建模。",
            "3. lookback 敏感性 [30,60,90,120]：以下只跑 in-sample(bull/bear/range)，未触碰 OOS，不用于选择参数。",
            _sensitivity_table(cfg, data, calendar_dates, month_ends, spans, cost_config),
            "4. ETF limit 字段为 NaN 时 constraints.py 不触发一字涨跌停拒单；对 ETF 月频影响较小，但方向仍是略乐观。",
            "",
            "## flag/参数调查记录",
            "- 未调参、未碰OOS、国债ETF 选择基于流动性而非收益。",
            f"- 固定使用 strategy_addon.yaml 的 lookback_vol_days={cfg['lookback_vol_days']}，未因结果修改 lookback 或资产池。",
            "- OOS 只在规则完全固定后用于 C 组最终裁决；敏感性表不包含 OOS。",
            "- 未修改成本、滑点、regime 或 Gate1 阈值。",
            "",
            "## Gate1 判定表",
            _gate_table(checks, gate1),
            "",
            "## 与已 FAIL 的 8 个策略对比",
            f"S1-S8 既有 Gate1 报告最终均为 FAIL；S3b/S3c 低频趋势衍生也为 FAIL。S9 本轮最终判定={final}，因此" + ("是本项目首次通过 Gate1 的策略。" if final == "PASS" else "不是首次过 Gate，A/B/C 仍未同时成立。"),
            "",
            f"最终判定：{final}，按低频显著性原则。",
        ]
    )
    return "\n".join(lines) + "\n"


def run(refresh: bool = False) -> dict[str, Any]:
    cost_config = CostConfig.from_mapping(_load_yaml("cost.yaml"))
    data, coverages, probes, cfg, bond_note = load_s9_data(refresh=refresh)
    calendar_dates = _common_dates(data)
    if not calendar_dates:
        raise RuntimeError("No common S9 ETF calendar dates")
    month_ends = _month_end_dates(calendar_dates)
    spans = {name: _effective_span(name, calendar_dates) for name in ("bull", "bear", "range", "oos")}
    strategy = S9RiskParityStrategy(cfg)
    symbols = strategy.symbols
    bond_symbol = next(str(item["code"]) for item in cfg["pool"] if str(item["code"]).startswith("511"))

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
            "s9": run_monthly_backtest("s9", regime, *common_args, strategy.generate_signals, cost_config),
            "equal_weight_monthly": run_monthly_backtest("equal_weight_monthly", regime, *common_args, _equal_weight_signal(symbols), cost_config),
            "sixty_forty_monthly": run_monthly_backtest("sixty_forty_monthly", regime, *common_args, _sixty_forty_signal(symbols, bond_symbol), cost_config),
            "hs300_buy_hold": run_monthly_backtest("hs300_buy_hold", regime, *common_args, _single_etf_buy_hold_signal("510300"), cost_config),
            "random_weight_monthly": run_monthly_backtest("random_weight_monthly", regime, *common_args, _random_weight_signal(symbols), cost_config),
        }

    s9_runs = {name: values["s9"] for name, values in all_runs.items()}
    checks = _gate_checks(s9_runs, _gate_cfg())
    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    path = REPORT_DIR / "s9_risk_parity_gate1.md"
    path.write_text(
        render_report(all_runs, checks, coverages, probes, cfg, bond_note, spans, data, calendar_dates, month_ends),
        encoding="utf-8",
    )
    return {
        "path": path,
        "runs": all_runs,
        "checks": checks,
        "coverages": coverages,
        "probes": probes,
        "cfg": cfg,
        "bond_note": bond_note,
        "spans": spans,
    }


def main() -> None:
    result = run(refresh=False)
    s9_runs = {name: values["s9"] for name, values in result["runs"].items()}
    total_trades = int(sum(summarize_run(run)["trades"] for run in s9_runs.values()))
    final = "PASS" if result["checks"]["overall_pass"] else "FAIL"
    print(f"wrote {result['path']}")
    print(f"S9 trades={total_trades} final={final}")
    print(result["bond_note"])
    for regime in ("bull", "bear", "range", "oos"):
        metrics = summarize_run(s9_runs[regime])
        print(
            f"{regime}: return={metrics['return']:.4%} dd={metrics['max_drawdown']:.4%} "
            f"trades={int(metrics['trades'])} expectancy={metrics['expectancy']:.2f} "
            f"pf={_fmt_float(metrics['profit_factor'])}"
        )


if __name__ == "__main__":
    main()
