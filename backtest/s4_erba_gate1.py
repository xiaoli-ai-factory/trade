"""S4 Erba monthly rotation Gate1 runner."""

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
from data.akshare_source import get_etf_daily, get_index_daily
from strategies.s4_erba_rotation import S4ErbaRotationStrategy


SignalFunc = Callable[[date, dict[str, Any]], list[Order]]


@dataclass(frozen=True)
class DataCoverage:
    kind: str
    symbol: str
    name: str
    rows: int
    earliest: date | None
    latest: date | None
    source: str


@dataclass(frozen=True)
class EffectiveSpan:
    regime: str
    configured_start: date
    configured_end: date
    effective_start: date
    effective_end: date
    adjusted: bool


def _normalize_frame(frame: pd.DataFrame) -> pd.DataFrame:
    out = frame.copy()
    out["date"] = pd.to_datetime(out["date"], errors="coerce").dt.date
    return out.dropna(subset=["date"]).sort_values("date").reset_index(drop=True)


def _coverage(kind: str, symbol: str, name: str, frame: pd.DataFrame) -> DataCoverage:
    if frame.empty:
        return DataCoverage(kind, symbol, name, 0, None, None, "")
    dates = list(frame["date"])
    source = str(frame["source"].dropna().iloc[-1]) if "source" in frame.columns and frame["source"].notna().any() else ""
    return DataCoverage(kind, symbol, name, len(frame), min(dates), max(dates), source)


def _strategy_cfg() -> dict[str, Any]:
    return _load_yaml("strategy_addon.yaml")["s4_erba_rotation"]


def _gate_cfg() -> dict[str, Any]:
    return _load_yaml("backtest.yaml")["gate1"]


def _regime_cfg() -> dict[str, Any]:
    return _load_yaml("backtest.yaml")["regimes"]


def load_s4_data(refresh: bool = False) -> tuple[dict[str, pd.DataFrame], dict[str, pd.DataFrame], list[DataCoverage]]:
    cfg = _strategy_cfg()
    regimes = _regime_cfg()
    global_start = min(_parse_date(item["start"]) for item in regimes.values())
    global_end = max(_parse_date(item["end"]) for item in regimes.values())
    lookback_days = int(cfg["lookback_days"])
    query_start = global_start - timedelta(days=max(lookback_days * 5, 90))

    index_data: dict[str, pd.DataFrame] = {}
    etf_data: dict[str, pd.DataFrame] = {}
    coverages: list[DataCoverage] = []
    for item in cfg["pool"]:
        index_symbol = str(item["code"])
        etf_symbol = str(item["etf_code"])
        index_frame = _normalize_frame(get_index_daily(index_symbol, start=query_start, end=global_end, refresh=refresh))
        etf_frame = _normalize_frame(get_etf_daily(etf_symbol, start=query_start, end=global_end, refresh=refresh))
        index_data[index_symbol] = index_frame
        etf_data[etf_symbol] = etf_frame
        coverages.append(_coverage("index_signal", index_symbol, str(item["name"]), index_frame))
        coverages.append(_coverage("etf_execution", etf_symbol, str(item["etf_name"]), etf_frame))
    return index_data, etf_data, coverages


def _parse_date(value: str | date | pd.Timestamp) -> date:
    if isinstance(value, pd.Timestamp):
        return value.date()
    if isinstance(value, date):
        return value
    return pd.Timestamp(str(value)).date()


def _common_dates(*datasets: dict[str, pd.DataFrame]) -> list[date]:
    date_sets: list[set[date]] = []
    for data in datasets:
        for frame in data.values():
            if frame.empty:
                date_sets.append(set())
            else:
                date_sets.append(set(frame["date"].tolist()))
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
        raise RuntimeError(f"No common S4 ETF/index dates for {regime}")
    effective = EffectiveSpan(
        regime=regime,
        configured_start=configured_start,
        configured_end=configured_end,
        effective_start=dates[0],
        effective_end=dates[-1],
        adjusted=dates[0] != configured_start or dates[-1] != configured_end,
    )
    return effective


def _ctx_for_signal(
    as_of_date: date,
    cash: float,
    positions: tuple[Position, ...],
    etf_data: dict[str, pd.DataFrame],
    index_data: dict[str, pd.DataFrame],
    month_ends: set[date],
) -> dict[str, Any]:
    return {
        "data": _slice_data(etf_data, as_of_date),
        "index_data": _slice_data(index_data, as_of_date),
        "positions": positions,
        "cash": cash,
        "nav": _mark_nav(cash, positions, etf_data, as_of_date),
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
    etf_data: dict[str, pd.DataFrame],
    cost_config: CostConfig,
) -> tuple[float, tuple[Position, ...]]:
    for order in sorted(orders, key=lambda item: 0 if item.side == "sell" else 1):
        bar = _market_bar(etf_data, order.symbol, trade_date)
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
    etf_data: dict[str, pd.DataFrame],
    index_data: dict[str, pd.DataFrame],
    calendar_dates: list[date],
    month_ends: set[date],
    signal_func: SignalFunc,
    cost_config: CostConfig,
) -> BacktestRun:
    dates = [item for item in calendar_dates if start <= item <= end]
    if not dates:
        raise RuntimeError(f"Not enough S4 dates for {regime}")

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
        ctx = _ctx_for_signal(signal_date, cash, positions, etf_data, index_data, month_ends)
        orders = signal_func(signal_date, ctx)
        if orders:
            pending_orders.setdefault(trade_date, []).extend(orders)

    first_date = dates[0]
    previous_signals = [item for item in month_ends if item < first_date]
    if previous_signals:
        previous_month_end = max(previous_signals)
        if _next_trading_date(calendar_dates, previous_month_end) == first_date:
            schedule_signal(previous_month_end)

    nav_rows = [{"date": first_date.isoformat(), "nav": cash}]
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
                etf_data,
                cost_config,
            )
        nav_rows.append({"date": trade_date.isoformat(), "nav": _mark_nav(cash, positions, etf_data, trade_date)})
        if trade_date in month_ends:
            schedule_signal(trade_date)

    final_date = dates[-1]
    positions = mark_sellable(positions, final_date)
    for position in sorted(positions, key=lambda item: item.symbol):
        if not position.sellable:
            continue
        close = _last_close(etf_data, position.symbol, final_date)
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

    final_nav = _mark_nav(cash, positions, etf_data, final_date)
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


def _floor_to_lot(quantity: float) -> int:
    if quantity <= 0:
        return 0
    return int(math.floor(quantity / LOT_SIZE) * LOT_SIZE)


def _latest_close(symbol: str, ctx: dict[str, Any]) -> float | None:
    frame = ctx["data"].get(symbol)
    if not isinstance(frame, pd.DataFrame) or frame.empty:
        return None
    close = pd.to_numeric(frame["close"], errors="coerce").iloc[-1]
    if pd.isna(close) or float(close) <= 0:
        return None
    return float(close)


def _full_weight_signal(symbols: tuple[str, ...], target_symbol: str, as_of_date: date, ctx: dict[str, Any]) -> list[Order]:
    positions: tuple[Position, ...] = tuple(ctx.get("positions", ()))
    current_qty = {item.symbol: item.quantity for item in positions if item.symbol in symbols and item.quantity > 0}
    orders: list[Order] = []
    for symbol, quantity in sorted(current_qty.items()):
        if symbol != target_symbol:
            orders.append(Order(symbol=symbol, side="sell", quantity=quantity, submitted_date=as_of_date))
    close = _latest_close(target_symbol, ctx)
    if close is None:
        return orders
    target_quantity = _floor_to_lot(float(ctx["nav"]) / close)
    diff = target_quantity - current_qty.get(target_symbol, 0)
    if diff > 0:
        orders.append(Order(symbol=target_symbol, side="buy", quantity=diff, submitted_date=as_of_date))
    elif diff < 0:
        orders.append(Order(symbol=target_symbol, side="sell", quantity=abs(diff), submitted_date=as_of_date))
    return sorted(orders, key=lambda item: 0 if item.side == "sell" else 1)


def _single_etf_buy_hold_signal(symbol: str) -> SignalFunc:
    symbols = (symbol,)

    def _signal(as_of_date: date, ctx: dict[str, Any]) -> list[Order]:
        positions: tuple[Position, ...] = tuple(ctx.get("positions", ()))
        if any(item.symbol == symbol and item.quantity > 0 for item in positions):
            return []
        return _full_weight_signal(symbols, symbol, as_of_date, ctx)

    return _signal


def _fifty_fifty_monthly_signal(symbols: tuple[str, ...]) -> SignalFunc:
    def _signal(as_of_date: date, ctx: dict[str, Any]) -> list[Order]:
        positions: tuple[Position, ...] = tuple(ctx.get("positions", ()))
        current_qty = {item.symbol: item.quantity for item in positions if item.symbol in symbols and item.quantity > 0}
        target_value = float(ctx["nav"]) / len(symbols)
        orders: list[Order] = []
        for symbol, quantity in sorted(current_qty.items()):
            if symbol not in symbols:
                orders.append(Order(symbol=symbol, side="sell", quantity=quantity, submitted_date=as_of_date))
        for symbol in symbols:
            close = _latest_close(symbol, ctx)
            if close is None:
                continue
            target_quantity = _floor_to_lot(target_value / close)
            diff = target_quantity - current_qty.get(symbol, 0)
            if diff > 0:
                orders.append(Order(symbol=symbol, side="buy", quantity=diff, submitted_date=as_of_date))
            elif diff < 0:
                orders.append(Order(symbol=symbol, side="sell", quantity=abs(diff), submitted_date=as_of_date))
        return sorted(orders, key=lambda item: 0 if item.side == "sell" else 1)

    return _signal


def _random_monthly_signal(symbols: tuple[str, ...]) -> SignalFunc:
    def _signal(as_of_date: date, ctx: dict[str, Any]) -> list[Order]:
        rng = np.random.default_rng(RANDOM_SEED + as_of_date.toordinal())
        target = str(rng.choice(list(symbols)))
        return _full_weight_signal(symbols, target, as_of_date, ctx)

    return _signal


def _merged_metrics(runs: dict[str, BacktestRun] | list[BacktestRun]) -> dict[str, float]:
    values = runs.values() if isinstance(runs, dict) else runs
    trades = tuple(item for run in values for item in run.trades)
    return _trade_metrics(trades)


def _low_freq_gate_checks(s4_runs: dict[str, BacktestRun], gate1: dict[str, Any]) -> dict[str, Any]:
    summaries = {name: summarize_run(run) for name, run in s4_runs.items()}
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

    b_rows = []
    b_pass = True
    for name in ("bull", "bear", "range"):
        metrics = summaries[name]
        checks = {
            "expectancy": metrics["expectancy"] > float(gate1["expectancy_after_cost_gt"]),
            "profit_factor": metrics["profit_factor"] >= float(gate1["profit_factor_min"]),
        }
        passed = all(checks.values())
        b_rows.append((name, metrics, checks, passed))
        b_pass = b_pass and passed

    oos = summaries["oos"]
    c_checks = {
        "expectancy": oos["expectancy"] > float(gate1["expectancy_after_cost_gt"]),
        "profit_factor": oos["profit_factor"] >= float(gate1["profit_factor_min"]),
        "max_drawdown": oos["max_drawdown"] <= float(gate1["max_drawdown_max"]),
    }
    c_pass = all(c_checks.values())
    return {
        "summaries": summaries,
        "merged": _merged_metrics(s4_runs),
        "a_rows": a_rows,
        "a_pass": a_pass,
        "b_rows": b_rows,
        "b_pass": b_pass,
        "c_checks": c_checks,
        "c_pass": c_pass,
        "overall_pass": a_pass and b_pass and c_pass,
    }


def _gate_row(label: str, metric: str, actual: float, threshold: float, passed: bool) -> str:
    return f"| {label} | {metric} | {_fmt_float(actual)} | {_fmt_float(threshold)} | {'PASS' if passed else 'FAIL'} |"


def _gate_table(checks: dict[str, Any], gate1: dict[str, Any]) -> str:
    lines = ["| group | metric | actual | threshold | result |", "|---|---:|---:|---:|---|"]
    for name, metrics, item_checks, _passed in checks["a_rows"]:
        lines.append(_gate_row(f"A/{name}", "expectancy_after_cost", metrics["expectancy"], gate1["expectancy_after_cost_gt"], item_checks["expectancy"]))
        lines.append(_gate_row(f"A/{name}", "profit_factor", metrics["profit_factor"], gate1["profit_factor_min"], item_checks["profit_factor"]))
        lines.append(_gate_row(f"A/{name}", "max_drawdown", metrics["max_drawdown"], gate1["max_drawdown_max"], item_checks["max_drawdown"]))
    for name, metrics, item_checks, _passed in checks["b_rows"]:
        lines.append(_gate_row(f"B/low_freq/{name}", "expectancy_after_cost", metrics["expectancy"], gate1["expectancy_after_cost_gt"], item_checks["expectancy"]))
        lines.append(_gate_row(f"B/low_freq/{name}", "profit_factor", metrics["profit_factor"], gate1["profit_factor_min"], item_checks["profit_factor"]))
    oos = checks["summaries"]["oos"]
    c = checks["c_checks"]
    lines.append(_gate_row("C/oos", "expectancy_after_cost", oos["expectancy"], gate1["expectancy_after_cost_gt"], c["expectancy"]))
    lines.append(_gate_row("C/oos", "profit_factor", oos["profit_factor"], gate1["profit_factor_min"], c["profit_factor"]))
    lines.append(_gate_row("C/oos", "max_drawdown", oos["max_drawdown"], gate1["max_drawdown_max"], c["max_drawdown"]))
    lines.append(f"| C/oos | trades | {_fmt_float(oos['trades'])} | 低频不适用{oos['trades']:.0f}<{gate1['oos_min_trades']} | N/A |")
    lines.append(f"| TOTAL | A+B+C(低频显著性) | - | - | {'PASS' if checks['overall_pass'] else 'FAIL'} |")
    return "\n".join(lines)


def _summary_table(s4_runs: dict[str, BacktestRun]) -> str:
    lines = [
        "| regime | start | end | return | max_drawdown | trades | expectancy_after_cost | profit_factor | win_rate | fee_ratio | filled_orders |",
        "|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for regime in ("bull", "bear", "range", "oos"):
        run = s4_runs[regime]
        metrics = summarize_run(run)
        lines.append(
            f"| {regime} | {run.start} | {run.end} | {_fmt_pct(metrics['return'])} | {_fmt_pct(metrics['max_drawdown'])} | "
            f"{int(metrics['trades'])} | {metrics['expectancy']:.2f} | {_fmt_float(metrics['profit_factor'])} | "
            f"{_fmt_pct(metrics['win_rate'])} | {_fmt_pct(metrics['fee_ratio'])} | {len(run.filled_orders)} |"
        )
    return "\n".join(lines)


def _comparison_table(regime_runs: dict[str, BacktestRun]) -> str:
    s4_m = summarize_run(regime_runs["s4"])
    hs300_m = summarize_run(regime_runs["hs300_buy_hold"])
    csi500_m = summarize_run(regime_runs["csi500_buy_hold"])
    fifty_m = summarize_run(regime_runs["fifty_fifty_monthly"])
    random_m = summarize_run(regime_runs["random_monthly"])
    rows = [
        ("return", s4_m["return"], hs300_m["return"], csi500_m["return"], fifty_m["return"], random_m["return"], True),
        ("max_drawdown", s4_m["max_drawdown"], hs300_m["max_drawdown"], csi500_m["max_drawdown"], fifty_m["max_drawdown"], random_m["max_drawdown"], True),
        ("trades", s4_m["trades"], hs300_m["trades"], csi500_m["trades"], fifty_m["trades"], random_m["trades"], False),
        ("fee_ratio", s4_m["fee_ratio"], hs300_m["fee_ratio"], csi500_m["fee_ratio"], fifty_m["fee_ratio"], random_m["fee_ratio"], True),
    ]
    lines = [
        "| metric | S4 | HS300ETF_BH | CSI500ETF_BH | 50/50_monthly | random_monthly | S4/50_50 | note |",
        "|---|---:|---:|---:|---:|---:|---:|---|",
    ]
    for metric, s4_v, hs300_v, csi500_v, fifty_v, random_v, pct in rows:
        ratio = _metric_ratio(s4_v, fifty_v)
        fmt = _fmt_pct if pct else _fmt_float
        lines.append(
            f"| {metric} | {fmt(s4_v)} | {fmt(hs300_v)} | {fmt(csi500_v)} | {fmt(fifty_v)} | "
            f"{fmt(random_v)} | {_fmt_float(ratio)} | {_ratio_note(ratio)} |"
        )
    return "\n".join(lines)


def _coverage_table(coverages: list[DataCoverage]) -> str:
    lines = ["| kind | symbol | name | rows | earliest | latest | source |", "|---|---|---|---:|---:|---:|---|"]
    for item in coverages:
        lines.append(
            f"| {item.kind} | {item.symbol} | {item.name} | {item.rows} | "
            f"{item.earliest or 'NA'} | {item.latest or 'NA'} | {item.source} |"
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


def _insample_oos_table(s4_runs: dict[str, BacktestRun]) -> str:
    in_runs = [s4_runs[name] for name in ("bull", "bear", "range")]
    in_metrics = _merged_metrics(in_runs)
    oos = summarize_run(s4_runs["oos"])
    avg_return = float(np.mean([item.total_return for item in in_runs]))
    worst_dd = max(item.max_drawdown for item in in_runs)
    lines = [
        "| span | avg/period_return | trades | expectancy | profit_factor | win_rate | worst/max_drawdown |",
        "|---|---:|---:|---:|---:|---:|---:|",
        f"| in_sample(bull+bear+range) | {_fmt_pct(avg_return)} | {int(in_metrics['trades'])} | {in_metrics['expectancy']:.2f} | {_fmt_float(in_metrics['profit_factor'])} | {_fmt_pct(in_metrics['win_rate'])} | {_fmt_pct(worst_dd)} |",
        f"| oos | {_fmt_pct(oos['return'])} | {int(oos['trades'])} | {oos['expectancy']:.2f} | {_fmt_float(oos['profit_factor'])} | {_fmt_pct(oos['win_rate'])} | {_fmt_pct(oos['max_drawdown'])} |",
    ]
    return "\n".join(lines)


def _rebalance_decisions(start: date, end: date, calendar_dates: list[date], month_ends: set[date]) -> int:
    count = 0
    first_date = next(item for item in calendar_dates if item >= start)
    previous = [item for item in month_ends if item < first_date]
    if previous and _next_trading_date(calendar_dates, max(previous)) == first_date:
        count += 1
    for signal_date in sorted(item for item in month_ends if start <= item <= end):
        next_date = _next_trading_date(calendar_dates, signal_date)
        if next_date is not None and start <= next_date <= end:
            count += 1
    return count


def _cost_drag_text(
    s4_runs: dict[str, BacktestRun],
    spans: dict[str, EffectiveSpan],
    calendar_dates: list[date],
    month_ends: set[date],
) -> str:
    total_cost = sum(summarize_run(run)["total_cost"] for run in s4_runs.values())
    traded_amount = sum(summarize_run(run)["traded_amount"] for run in s4_runs.values())
    gross_profit = _merged_metrics(s4_runs)["gross_profit"]
    filled_orders = sum(len(run.filled_orders) for run in s4_runs.values())
    days = sum((span.effective_end - span.effective_start).days + 1 for span in spans.values())
    years = days / 365.25 if days else 0.0
    decisions = sum(
        _rebalance_decisions(span.effective_start, span.effective_end, calendar_dates, month_ends)
        for span in spans.values()
    )
    decisions_per_year = decisions / years if years else 0.0
    orders_per_year = filled_orders / years if years else 0.0
    cost_profit_ratio = total_cost / gross_profit if gross_profit > 0 else math.nan
    fee_ratio = total_cost / traded_amount if traded_amount else 0.0
    return (
        f"月频决策数={decisions}，约{decisions_per_year:.1f}次/年；filled_orders={filled_orders}，约{orders_per_year:.1f}笔/年。"
        f"总成本={total_cost:.2f}，成交额成本率={_fmt_pct(fee_ratio)}，成本/毛盈利={_fmt_float(cost_profit_ratio)}。"
    )


def _sensitivity_table(
    base_cfg: dict[str, Any],
    etf_data: dict[str, pd.DataFrame],
    index_data: dict[str, pd.DataFrame],
    calendar_dates: list[date],
    month_ends: set[date],
    spans: dict[str, EffectiveSpan],
    cost_config: CostConfig,
) -> str:
    base = int(base_cfg["lookback_days"])
    lookbacks = [max(1, base // 2), base, base * 2, base * 3]
    lines = [
        "| lookback_days | in_sample_avg_return | in_sample_worst_DD | trades | expectancy | profit_factor | win_rate |",
        "|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for lookback in lookbacks:
        cfg = dict(base_cfg)
        cfg["lookback_days"] = lookback
        strategy = S4ErbaRotationStrategy(cfg)
        runs = []
        for regime in ("bull", "bear", "range"):
            span = spans[regime]
            runs.append(
                run_monthly_backtest(
                    "s4_sensitivity",
                    regime,
                    span.effective_start,
                    span.effective_end,
                    etf_data,
                    index_data,
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
    spans: dict[str, EffectiveSpan],
    etf_data: dict[str, pd.DataFrame],
    index_data: dict[str, pd.DataFrame],
    calendar_dates: list[date],
    month_ends: set[date],
) -> str:
    cfg = _strategy_cfg()
    gate1 = _gate_cfg()
    s4_runs = {name: values["s4"] for name, values in runs.items()}
    final = "PASS" if checks["overall_pass"] else "FAIL"
    first_pair, second_pair = cfg["pool"]
    lines = [
        "# S4 二八轮动 Gate1 Report",
        "",
        f"规则：月末收盘后比较 {first_pair['code']} 与 {second_pair['code']} 最近 lookback_days={cfg['lookback_days']} 个交易日累计收益；次月首个交易日开盘持有动量更强者。",
        f"信号用指数：{first_pair['code']}/{second_pair['code']}；成交用 ETF：{first_pair['etf_code']}/{second_pair['etf_code']}。trend_filter_ma={cfg['trend_filter_ma']}，allow_cash={cfg['allow_cash']}。",
        "本次使用低频显著性原则；月频策略不按高换手 trades>=200 / oos_min_trades=60 卡死。",
        "",
        "## 数据覆盖",
        _coverage_table(coverages),
        "",
        "### regime 实际可得区间",
        _effective_span_table(spans),
        "",
        "若 effective_start 晚于 configured_start，报告按真实可得 ETF/指数共同交易日起算；本次无需伪造补齐。",
        "",
        "## S4 分段关键指标",
        _summary_table(s4_runs),
        "",
        "## in-sample vs OOS 差异",
        _insample_oos_table(s4_runs),
        "",
        "## 对照组 ratio 表",
    ]
    for regime in ("bull", "bear", "range", "oos"):
        lines.extend([f"### {regime}", _comparison_table(runs[regime]), ""])
    lines.extend(
        [
            "重点对照：50/50 月度再平衡是静态等权持有的可执行近似；若 S4 不能稳定优于该组，不能宣称轮动有独立 alpha。",
            "",
            "## 反假设列表",
            "- lookback_days=20 是否过拟合：以下敏感性只跑 in-sample(bull/bear/range)，未触碰 OOS，不用于选择参数。",
            _sensitivity_table(cfg, etf_data, index_data, calendar_dates, month_ends, spans, CostConfig.from_mapping(_load_yaml("cost.yaml"))),
            "- 指数信号到 ETF 成交偏差：指数序列更长、更干净且无盘口折溢价；实际 ETF 有跟踪误差、折溢价、分红除权和盘口流动性，方向偏向高估信号可迁移性。PnL 已用 ETF 价格成交，但选谁仍来自更理想的指数。",
            f"- 月频换仓成本拖累：{_cost_drag_text(s4_runs, spans, calendar_dates, month_ends)}",
            "- ETF/指数 limit 字段为 NaN 时 constraints.py 不触发一字涨跌停拒单；这会高估极端开盘日的可成交性，方向为乐观偏差。",
            "",
            "## flag/参数调查记录",
            "- 未调 lookback，固定使用 strategy_addon.yaml 的 lookback_days=20。",
            "- 未碰 OOS 调参；OOS 只在固定规则跑完后用于 C 组最终裁决。",
            "- 未加入 trend filter，未允许空仓，未修改成本、滑点、regime 或 Gate1 阈值。",
            "",
            "## Gate1 判定表",
            _gate_table(checks, gate1),
            "",
            f"最终判定：{final}，按低频显著性原则。",
        ]
    )
    return "\n".join(lines) + "\n"


def run(refresh: bool = False) -> dict[str, Any]:
    cfg = _strategy_cfg()
    cost_config = CostConfig.from_mapping(_load_yaml("cost.yaml"))
    index_data, etf_data, coverages = load_s4_data(refresh=refresh)
    calendar_dates = _common_dates(index_data, etf_data)
    if not calendar_dates:
        raise RuntimeError("No common S4 calendar dates")
    month_ends = _month_end_dates(calendar_dates)
    spans = {name: _effective_span(name, calendar_dates) for name in ("bull", "bear", "range", "oos")}
    strategy = S4ErbaRotationStrategy(cfg)
    etf_symbols = strategy.etf_symbols

    all_runs: dict[str, dict[str, BacktestRun]] = {}
    for regime in ("bull", "bear", "range", "oos"):
        span = spans[regime]
        common_args = (
            span.effective_start,
            span.effective_end,
            etf_data,
            index_data,
            calendar_dates,
            month_ends,
        )
        all_runs[regime] = {
            "s4": run_monthly_backtest("s4", regime, *common_args, strategy.generate_signals, cost_config),
            "hs300_buy_hold": run_monthly_backtest("hs300_buy_hold", regime, *common_args, _single_etf_buy_hold_signal(etf_symbols[0]), cost_config),
            "csi500_buy_hold": run_monthly_backtest("csi500_buy_hold", regime, *common_args, _single_etf_buy_hold_signal(etf_symbols[1]), cost_config),
            "fifty_fifty_monthly": run_monthly_backtest("fifty_fifty_monthly", regime, *common_args, _fifty_fifty_monthly_signal(etf_symbols), cost_config),
            "random_monthly": run_monthly_backtest("random_monthly", regime, *common_args, _random_monthly_signal(etf_symbols), cost_config),
        }

    s4_runs = {name: values["s4"] for name, values in all_runs.items()}
    checks = _low_freq_gate_checks(s4_runs, _gate_cfg())
    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    path = REPORT_DIR / "s4_erba_rotation_gate1.md"
    path.write_text(
        render_report(all_runs, checks, coverages, spans, etf_data, index_data, calendar_dates, month_ends),
        encoding="utf-8",
    )
    return {
        "path": path,
        "runs": all_runs,
        "checks": checks,
        "coverages": coverages,
        "spans": spans,
    }


def main() -> None:
    result = run(refresh=False)
    s4_runs = {name: values["s4"] for name, values in result["runs"].items()}
    total_trades = int(sum(summarize_run(run)["trades"] for run in s4_runs.values()))
    final = "PASS" if result["checks"]["overall_pass"] else "FAIL"
    print(f"wrote {result['path']}")
    print(f"S4 trades={total_trades} final={final}")
    for regime in ("bull", "bear", "range", "oos"):
        metrics = summarize_run(s4_runs[regime])
        print(
            f"{regime}: return={metrics['return']:.4%} dd={metrics['max_drawdown']:.4%} "
            f"trades={int(metrics['trades'])} expectancy={metrics['expectancy']:.2f} "
            f"pf={_fmt_float(metrics['profit_factor'])}"
        )


if __name__ == "__main__":
    main()
