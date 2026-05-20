"""S6 dual moving-average crossover Gate1 runner."""

from __future__ import annotations

import argparse
import math
from dataclasses import dataclass
from datetime import date
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
    _load_yaml,
    _metric_ratio,
    _max_drawdown,
    _parse_date,
    _position_for_symbol,
    _ratio_note,
    _record_execution,
    summarize_run,
)
from data.akshare_source import get_index_daily
from strategies.s3b_trend import S3BTrendStrategy
from strategies.s3c_trend_monthly import S3CTrendMonthlyStrategy
from strategies.s6_dual_ma import S6DualMAStrategy


DEPTH_START = "1990-01-01"
S6_SERIES = (
    ("sh000300", "沪深300"),
    ("sh000905", "中证500"),
    ("sh000001", "上证综指"),
)
SENSITIVITY_SHORT = (5, 10, 20)
SENSITIVITY_LONG = (20, 30, 60)

SignalFunc = Callable[[date, dict[str, Any]], list[Order]]
FastSignalFunc = Callable[[date, float, tuple[Position, ...]], list[Order]]


@dataclass(frozen=True)
class SeriesDepth:
    symbol: str
    name: str
    rows: int
    earliest: date | None
    latest: date | None
    source: str
    error: str | None = None


@dataclass(frozen=True)
class CycleResult:
    symbol: str
    name: str
    start: date
    end: date
    s6: dict[str, float]
    buy_hold: dict[str, float]
    s3b: dict[str, float]
    s3c: dict[str, float]
    passed: bool
    criterion: str


def _strategy_cfg() -> dict[str, Any]:
    return _load_yaml("strategy_addon.yaml")["s6_dual_ma_crossover"].copy()


def _cost_cfg() -> CostConfig:
    return CostConfig.from_mapping(_load_yaml("cost.yaml"))


def _none_if_nan(value: Any) -> float | None:
    if value is None or pd.isna(value):
        return None
    return float(value)


def _normalize_frame(frame: pd.DataFrame) -> pd.DataFrame:
    out = frame.copy()
    out["date"] = pd.to_datetime(out["date"], errors="coerce").dt.date
    return out.dropna(subset=["date"]).sort_values("date").reset_index(drop=True)


def _load_series_data(symbol: str, end: date, refresh: bool = False) -> pd.DataFrame:
    frame = get_index_daily(symbol, start=DEPTH_START, end=end, refresh=refresh)
    if frame.empty:
        raise RuntimeError(f"No index data for {symbol}")
    return _normalize_frame(frame)


def _coverage(symbol: str, name: str, frame: pd.DataFrame, error: str | None = None) -> SeriesDepth:
    if frame.empty:
        return SeriesDepth(symbol, name, 0, None, None, "", error=error)
    dates = list(frame["date"])
    source = str(frame["source"].dropna().iloc[-1]) if "source" in frame.columns and frame["source"].notna().any() else ""
    return SeriesDepth(symbol, name, len(frame), min(dates), max(dates), source, error=error)


def _single_asset_buy_hold_signal(asset: str) -> SignalFunc:
    def _signal(as_of_date: date, ctx: dict[str, Any]) -> list[Order]:
        positions: tuple[Position, ...] = tuple(ctx.get("positions", ()))
        if any(item.symbol == asset and item.quantity > 0 for item in positions):
            return []
        frame = ctx["data"].get(asset)
        if frame is None or frame.empty:
            return []
        close = float(frame.iloc[-1]["close"])
        quantity = int(math.floor((float(ctx["nav"]) / close) / LOT_SIZE) * LOT_SIZE)
        if quantity <= 0:
            return []
        return [Order(symbol=asset, side="buy", quantity=quantity, submitted_date=as_of_date)]

    return _signal


def _floor_to_lot(quantity: float) -> int:
    if quantity <= 0:
        return 0
    return int(math.floor(quantity / LOT_SIZE) * LOT_SIZE)


def _market_bar_from_row(symbol: str, trade_date: date, row: pd.Series) -> MarketBar:
    return MarketBar(
        symbol=symbol,
        date=trade_date,
        open=float(row["open"]),
        limit_up_price=_none_if_nan(row.get("limit_up_price")),
        limit_down_price=_none_if_nan(row.get("limit_down_price")),
        is_suspended=bool(row.get("is_suspended", False)),
    )


def _close_map(frame: pd.DataFrame) -> dict[date, float]:
    return {item.date: float(item.close) for item in frame[["date", "close"]].itertuples(index=False)}


def _position_quantity(positions: tuple[Position, ...], symbol: str) -> int:
    return sum(item.quantity for item in positions if item.symbol == symbol and item.quantity > 0)


def _run_fast_backtest(
    name: str,
    start: date,
    end: date,
    data: dict[str, pd.DataFrame],
    symbol: str,
    signal_func: FastSignalFunc,
) -> BacktestRun:
    frame = data[symbol].sort_values("date").reset_index(drop=True)
    dates = [item for item in frame["date"].tolist() if start <= item <= end]
    if len(dates) < 2:
        raise RuntimeError(f"Not enough S6 dates for {symbol} {start}..{end}")

    by_date = frame.set_index("date", drop=False)
    close_by_date = _close_map(frame)
    cost_config = _cost_cfg()
    cash = INITIAL_CASH
    positions: tuple[Position, ...] = ()
    basis: dict[str, float] = {}
    trades: list[TradeRecord] = []
    filled_orders: list[dict[str, Any]] = []
    rejected_orders: list[dict[str, Any]] = []
    events: list[dict[str, Any]] = []
    nav_rows = [{"date": dates[0].isoformat(), "nav": cash}]

    def mark_nav(on_date: date) -> float:
        nav = cash
        close = close_by_date.get(on_date)
        if close is None:
            return nav
        for item in positions:
            if item.symbol == symbol:
                nav += item.quantity * close
        return nav

    for signal_date, trade_date in zip(dates[:-1], dates[1:], strict=False):
        positions = mark_sellable(positions, trade_date)
        orders = signal_func(signal_date, mark_nav(signal_date), positions)
        for order in sorted(orders, key=lambda item: 0 if item.side == "sell" else 1):
            row = by_date.loc[trade_date]
            if isinstance(row, pd.DataFrame):
                row = row.iloc[-1]
            bar = _market_bar_from_row(symbol, trade_date, row)
            order_to_match = order
            if order.side == "buy":
                affordable = _affordable_quantity(order.quantity, bar.open, cash, cost_config)
                if affordable <= 0:
                    continue
                order_to_match = Order(order.symbol, order.side, affordable, order.submitted_date)
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
        nav_rows.append({"date": trade_date.isoformat(), "nav": mark_nav(trade_date)})

    final_date = dates[-1]
    positions = mark_sellable(positions, final_date)
    close = close_by_date.get(final_date)
    if close is not None:
        for position in sorted(positions, key=lambda item: item.symbol):
            if position.symbol != symbol or not position.sellable:
                continue
            result = match_order(
                Order(symbol=symbol, side="sell", quantity=position.quantity, submitted_date=final_date),
                MarketBar(symbol=symbol, date=final_date, open=close, is_suspended=False),
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
    final_nav = cash
    if close is not None:
        for position in positions:
            if position.symbol == symbol:
                final_nav += position.quantity * close
    nav_rows[-1] = {"date": final_date.isoformat(), "nav": final_nav}
    nav_curve = pd.DataFrame(nav_rows)
    return BacktestRun(
        name=name,
        regime="s6_gate1",
        start=start,
        end=end,
        initial_cash=INITIAL_CASH,
        final_nav=float(final_nav),
        total_return=float(final_nav / INITIAL_CASH - 1.0),
        max_drawdown=_max_drawdown(nav_curve),
        trades=tuple(trades),
        filled_orders=tuple(filled_orders),
        rejected_orders=tuple(rejected_orders),
        events=tuple(events),
        nav_curve=nav_curve,
    )


def _s6_fast_signal(symbol: str, frame: pd.DataFrame, short_ma: int, long_ma: int) -> FastSignalFunc:
    rows = frame.sort_values("date").reset_index(drop=True)
    close = pd.to_numeric(rows["close"], errors="coerce")
    short = close.rolling(short_ma).mean()
    long = close.rolling(long_ma).mean()
    signals: dict[date, str] = {}
    for idx in range(1, len(rows)):
        prev_short = short.iloc[idx - 1]
        prev_long = long.iloc[idx - 1]
        curr_short = short.iloc[idx]
        curr_long = long.iloc[idx]
        if any(pd.isna(item) for item in (prev_short, prev_long, curr_short, curr_long)):
            continue
        current_date = rows.iloc[idx]["date"]
        if float(prev_short) < float(prev_long) and float(curr_short) >= float(curr_long):
            signals[current_date] = "buy"
        elif float(prev_short) >= float(prev_long) and float(curr_short) < float(curr_long):
            signals[current_date] = "sell"
    closes = _close_map(rows)

    def _signal(as_of_date: date, nav: float, positions: tuple[Position, ...]) -> list[Order]:
        side = signals.get(as_of_date)
        quantity = _position_quantity(positions, symbol)
        if side == "buy" and quantity <= 0:
            close_value = closes.get(as_of_date)
            if close_value is None:
                return []
            target = _floor_to_lot(nav / close_value)
            return [Order(symbol=symbol, side="buy", quantity=target, submitted_date=as_of_date)] if target > 0 else []
        if side == "sell" and quantity > 0:
            return [Order(symbol=symbol, side="sell", quantity=quantity, submitted_date=as_of_date)]
        return []

    return _signal


def _buy_hold_fast_signal(symbol: str, frame: pd.DataFrame) -> FastSignalFunc:
    closes = _close_map(frame)

    def _signal(as_of_date: date, nav: float, positions: tuple[Position, ...]) -> list[Order]:
        if _position_quantity(positions, symbol) > 0:
            return []
        close_value = closes.get(as_of_date)
        if close_value is None:
            return []
        quantity = _floor_to_lot(nav / close_value)
        return [Order(symbol=symbol, side="buy", quantity=quantity, submitted_date=as_of_date)] if quantity > 0 else []

    return _signal


def _s3b_fast_signal(symbol: str, frame: pd.DataFrame) -> FastSignalFunc:
    ma_len = int(_load_yaml("strategy.yaml")["s3b_trend"]["ma_len"])
    rows = frame.sort_values("date").reset_index(drop=True)
    close = pd.to_numeric(rows["close"], errors="coerce")
    ma = close.rolling(ma_len).mean()
    should_hold = {
        rows.iloc[idx]["date"]: bool(pd.notna(ma.iloc[idx]) and pd.notna(close.iloc[idx]) and float(close.iloc[idx]) > float(ma.iloc[idx]))
        for idx in range(len(rows))
    }
    closes = _close_map(rows)

    def _signal(as_of_date: date, nav: float, positions: tuple[Position, ...]) -> list[Order]:
        quantity = _position_quantity(positions, symbol)
        hold = should_hold.get(as_of_date, False)
        if hold and quantity <= 0:
            close_value = closes.get(as_of_date)
            if close_value is None:
                return []
            target = _floor_to_lot(nav / close_value)
            return [Order(symbol=symbol, side="buy", quantity=target, submitted_date=as_of_date)] if target > 0 else []
        if not hold and quantity > 0:
            return [Order(symbol=symbol, side="sell", quantity=quantity, submitted_date=as_of_date)]
        return []

    return _signal


def _s3c_fast_signal(symbol: str, frame: pd.DataFrame) -> FastSignalFunc:
    ma_len = int(_load_yaml("strategy.yaml")["s3c_trend_monthly"]["ma_len_months"])
    monthly = _monthly_close_frame(frame)
    close = pd.to_numeric(monthly["close"], errors="coerce")
    ma = close.rolling(ma_len).mean()
    should_hold = {
        monthly.iloc[idx]["date"]: bool(pd.notna(ma.iloc[idx]) and pd.notna(close.iloc[idx]) and float(close.iloc[idx]) > float(ma.iloc[idx]))
        for idx in range(len(monthly))
    }
    month_ends = set(should_hold)
    closes = _close_map(frame)

    def _signal(as_of_date: date, nav: float, positions: tuple[Position, ...]) -> list[Order]:
        if as_of_date not in month_ends:
            return []
        quantity = _position_quantity(positions, symbol)
        hold = should_hold.get(as_of_date, False)
        if hold and quantity <= 0:
            close_value = closes.get(as_of_date)
            if close_value is None:
                return []
            target = _floor_to_lot(nav / close_value)
            return [Order(symbol=symbol, side="buy", quantity=target, submitted_date=as_of_date)] if target > 0 else []
        if not hold and quantity > 0:
            return [Order(symbol=symbol, side="sell", quantity=quantity, submitted_date=as_of_date)]
        return []

    return _signal


def _monthly_close_frame(frame: pd.DataFrame) -> pd.DataFrame:
    rows = frame.copy()
    rows["date_ts"] = pd.to_datetime(rows["date"], errors="coerce")
    rows = rows.dropna(subset=["date_ts", "close"]).sort_values("date_ts")
    rows["month"] = rows["date_ts"].dt.to_period("M")
    monthly = rows.groupby("month", as_index=False).tail(1).copy()
    monthly["date"] = monthly["date_ts"].dt.date
    return monthly[["date", "close"]].sort_values("date").reset_index(drop=True)


def _monthly_signal_wrapper(asset: str, data: dict[str, pd.DataFrame], strategy: S3CTrendMonthlyStrategy) -> SignalFunc:
    monthly = _monthly_close_frame(data[asset])
    month_end_dates = set(monthly["date"].tolist())

    def _signal(as_of_date: date, ctx: dict[str, Any]) -> list[Order]:
        if as_of_date not in month_end_dates:
            return []
        ctx2 = dict(ctx)
        ctx2["monthly_data"] = {asset: monthly[monthly["date"] <= as_of_date].copy()}
        return strategy.generate_signals(as_of_date, ctx2)

    return _signal


def _run_s6(
    symbol: str,
    name: str,
    start: date,
    end: date,
    data: dict[str, pd.DataFrame],
    short_ma: int | None = None,
    long_ma: int | None = None,
) -> BacktestRun:
    cfg = _strategy_cfg()
    cfg["asset"] = symbol
    if short_ma is not None:
        cfg["short_ma"] = int(short_ma)
    if long_ma is not None:
        cfg["long_ma"] = int(long_ma)
    S6DualMAStrategy(cfg)
    return _run_fast_backtest(
        name,
        start,
        end,
        data,
        symbol,
        _s6_fast_signal(symbol, data[symbol], int(cfg["short_ma"]), int(cfg["long_ma"])),
    )


def _run_buy_hold(symbol: str, name: str, start: date, end: date, data: dict[str, pd.DataFrame]) -> BacktestRun:
    return _run_fast_backtest(name, start, end, data, symbol, _buy_hold_fast_signal(symbol, data[symbol]))


def _run_s3b(symbol: str, name: str, start: date, end: date, data: dict[str, pd.DataFrame]) -> BacktestRun:
    cfg = _load_yaml("strategy.yaml")["s3b_trend"].copy()
    cfg["asset"] = symbol
    S3BTrendStrategy(cfg)
    return _run_fast_backtest(name, start, end, data, symbol, _s3b_fast_signal(symbol, data[symbol]))


def _run_s3c(symbol: str, name: str, start: date, end: date, data: dict[str, pd.DataFrame]) -> BacktestRun:
    cfg = _load_yaml("strategy.yaml")["s3c_trend_monthly"].copy()
    cfg["asset"] = symbol
    S3CTrendMonthlyStrategy(cfg)
    return _run_fast_backtest(name, start, end, data, symbol, _s3c_fast_signal(symbol, data[symbol]))


def _run_quad(
    symbol: str,
    label: str,
    start: date,
    end: date,
    data: dict[str, pd.DataFrame],
) -> tuple[dict[str, float], dict[str, float], dict[str, float], dict[str, float]]:
    s6 = summarize_run(_run_s6(symbol, f"s6_{label}", start, end, data))
    buy_hold = summarize_run(_run_buy_hold(symbol, f"buy_hold_{label}", start, end, data))
    s3b = summarize_run(_run_s3b(symbol, f"failed_s3b_{label}", start, end, data))
    s3c = summarize_run(_run_s3c(symbol, f"failed_s3c_{label}", start, end, data))
    return s6, buy_hold, s3b, s3c


def _cycle_result(
    symbol: str,
    name: str,
    start: date,
    end: date,
    data: dict[str, pd.DataFrame],
    kind: str,
    max_drawdown: float,
) -> CycleResult:
    s6, buy_hold, s3b, s3c = _run_quad(symbol, f"{symbol}_{name}", start, end, data)
    if kind == "bull":
        passed = s6["return"] > 0.0 and s6["max_drawdown"] <= max_drawdown
        criterion = "S6 return>0 and S6 DD<=20%"
    elif kind == "bear":
        passed = s6["max_drawdown"] < buy_hold["max_drawdown"] and s6["max_drawdown"] <= max_drawdown
        criterion = "S6 DD < buy_hold DD and S6 DD<=20%"
    else:
        raise ValueError(f"Unsupported cycle kind: {kind}")
    return CycleResult(symbol, name, start, end, s6, buy_hold, s3b, s3c, bool(passed), criterion)


def _price_percentile(frame: pd.DataFrame, on_date: date, lookback: int) -> float:
    rows = frame[frame["date"] <= on_date].tail(max(lookback, 2)).copy()
    if rows.empty:
        return math.nan
    close = pd.to_numeric(rows["close"], errors="coerce")
    current = float(close.iloc[-1])
    low = float(close.min())
    high = float(close.max())
    if not np.isfinite(current) or not np.isfinite(low) or not np.isfinite(high) or high <= low:
        return math.nan
    return (current - low) / (high - low)


def _lag_diagnostic(run: BacktestRun, frame: pd.DataFrame, lookback: int) -> dict[str, Any]:
    losses = [item for item in run.trades if item.pnl < 0]
    entry_pct = [_price_percentile(frame, item.entry_date, lookback) for item in losses]
    exit_pct = [_price_percentile(frame, item.exit_date, lookback) for item in losses]
    hold_days = [(item.exit_date - item.entry_date).days for item in run.trades]
    worst = min(run.trades, key=lambda item: item.pnl / item.entry_basis) if run.trades else None
    return {
        "trades": len(run.trades),
        "losses": len(losses),
        "loss_rate": len(losses) / len(run.trades) if run.trades else 0.0,
        "median_hold_days": float(np.median(hold_days)) if hold_days else 0.0,
        "loss_entry_pct": float(np.nanmedian(entry_pct)) if entry_pct else math.nan,
        "loss_exit_pct": float(np.nanmedian(exit_pct)) if exit_pct else math.nan,
        "worst_trade": worst,
        "worst_pct": worst.pnl / worst.entry_basis if worst is not None and worst.entry_basis else math.nan,
    }


def _sensitivity_rows(symbol: str, data: dict[str, pd.DataFrame]) -> list[dict[str, Any]]:
    full_cfg = _load_yaml("backtest.yaml")["full_history"]
    start = _parse_date(full_cfg["in_sample"]["start"])
    end = _parse_date(full_cfg["in_sample"]["end"])
    rows: list[dict[str, Any]] = []
    for short_ma in SENSITIVITY_SHORT:
        for long_ma in SENSITIVITY_LONG:
            if short_ma >= long_ma:
                rows.append({"symbol": symbol, "short_ma": short_ma, "long_ma": long_ma, "valid": False})
                continue
            run = _run_s6(symbol, f"{symbol}_s6_{short_ma}_{long_ma}_is", start, end, data, short_ma, long_ma)
            s6 = summarize_run(run)
            buy_hold = summarize_run(_run_buy_hold(symbol, f"{symbol}_bh_{short_ma}_{long_ma}_is", start, end, data))
            rows.append(
                {
                    "symbol": symbol,
                    "short_ma": short_ma,
                    "long_ma": long_ma,
                    "valid": True,
                    "s6": s6,
                    "buy_hold": buy_hold,
                    "loss_rate": 1.0 - s6["win_rate"] if s6["trades"] else 0.0,
                }
            )
    return rows


def _series_report(symbol: str, label: str, data: dict[str, pd.DataFrame]) -> dict[str, Any]:
    backtest_cfg = _load_yaml("backtest.yaml")
    regimes = backtest_cfg["regimes"]
    full_cfg = backtest_cfg["full_history"]
    gate1 = backtest_cfg["gate1"]
    max_dd = float(gate1["max_drawdown_max"])
    long_ma = int(_strategy_cfg()["long_ma"])

    standard = {}
    for regime, span in regimes.items():
        start = _parse_date(span["start"])
        end = _parse_date(span["end"])
        standard[regime] = _run_quad(symbol, f"{label}_{regime}", start, end, data)

    in_start = _parse_date(full_cfg["in_sample"]["start"])
    in_end = _parse_date(full_cfg["in_sample"]["end"])
    oos_start = _parse_date(full_cfg["oos"]["start"])
    oos_end = _parse_date(full_cfg["oos"]["end"])
    full_start = _parse_date(full_cfg["asset_min_start"])

    bull_cycles = [
        _cycle_result(symbol, f"bull_{idx}", _parse_date(left), _parse_date(right), data, "bull", max_dd)
        for idx, (left, right) in enumerate(full_cfg["bull_cycles"], start=1)
    ]
    bear_cycles = [
        _cycle_result(symbol, f"bear_{idx}", _parse_date(left), _parse_date(right), data, "bear", max_dd)
        for idx, (left, right) in enumerate(full_cfg["bear_cycles"], start=1)
    ]
    in_sample = _run_quad(symbol, f"{label}_in_sample", in_start, in_end, data)
    oos = _run_quad(symbol, f"{label}_full_oos", oos_start, oos_end, data)
    full_total = _run_quad(symbol, f"{label}_full_total", full_start, oos_end, data)
    oos_s6 = oos[0]
    oos_pass = (
        oos_s6["expectancy"] > float(gate1["expectancy_after_cost_gt"])
        and oos_s6["profit_factor"] >= float(gate1["profit_factor_min"])
        and oos_s6["max_drawdown"] <= max_dd
    )
    bull_pass = all(item.passed for item in bull_cycles)
    bear_pass = all(item.passed for item in bear_cycles)
    full_run = _run_s6(symbol, f"{label}_lag_full", full_start, oos_end, data)
    return {
        "symbol": symbol,
        "label": label,
        "standard": standard,
        "in_sample": in_sample,
        "oos": (*oos, bool(oos_pass)),
        "full_total": full_total,
        "bull_cycles": bull_cycles,
        "bear_cycles": bear_cycles,
        "bull_pass": bool(bull_pass),
        "bear_pass": bool(bear_pass),
        "oos_pass": bool(oos_pass),
        "passed": bool(bull_pass and bear_pass and oos_pass),
        "lag": _lag_diagnostic(full_run, data[symbol], long_ma),
        "sensitivity": _sensitivity_rows(symbol, data),
    }


def _summary_table(results: list[dict[str, Any]]) -> str:
    lines = [
        "| series | regime | S6_return | S6_DD | trades | expectancy | PF | win_rate | fee_ratio |",
        "|---|---|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for result in results:
        for regime in ("bull", "bear", "range", "oos"):
            s6 = result["standard"][regime][0]
            lines.append(
                f"| {result['label']}/{result['symbol']} | {regime} | {_fmt_pct(s6['return'])} | "
                f"{_fmt_pct(s6['max_drawdown'])} | {int(s6['trades'])} | {s6['expectancy']:.2f} | "
                f"{_fmt_float(s6['profit_factor'])} | {_fmt_pct(s6['win_rate'])} | {_fmt_pct(s6['fee_ratio'])} |"
            )
    return "\n".join(lines)


def _control_table(results: list[dict[str, Any]]) -> str:
    lines = [
        "| series | span | S6_return | BH_return | failed_S3b_return | failed_S3c_return | S6/BH | S6/S3b | S6/S3c | S6_DD | BH_DD | S6_trades | note |",
        "|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---|",
    ]
    for result in results:
        rows = [(name, *quad) for name, quad in result["standard"].items()]
        rows.extend(
            [
                ("full_in_sample", *result["in_sample"]),
                ("full_oos", *result["oos"][:4]),
                ("full_total", *result["full_total"]),
            ]
        )
        for span, s6, bh, s3b, s3c in rows:
            ratios = (_metric_ratio(s6["return"], bh["return"]), _metric_ratio(s6["return"], s3b["return"]), _metric_ratio(s6["return"], s3c["return"]))
            lines.append(
                f"| {result['label']}/{result['symbol']} | {span} | {_fmt_pct(s6['return'])} | {_fmt_pct(bh['return'])} | "
                f"{_fmt_pct(s3b['return'])} | {_fmt_pct(s3c['return'])} | {_fmt_float(ratios[0])} | "
                f"{_fmt_float(ratios[1])} | {_fmt_float(ratios[2])} | {_fmt_pct(s6['max_drawdown'])} | "
                f"{_fmt_pct(bh['max_drawdown'])} | {int(s6['trades'])} | {_ratio_note(*ratios)} |"
            )
    return "\n".join(lines)


def _lag_table(results: list[dict[str, Any]]) -> str:
    lines = [
        "| series | trades | loss_trades | loss_rate | median_hold_days | losing_entry_pct_in_MA_window | losing_exit_pct_in_MA_window | worst_trade | worst_pnl_pct |",
        "|---|---:|---:|---:|---:|---:|---:|---|---:|",
    ]
    for result in results:
        lag = result["lag"]
        worst = lag["worst_trade"]
        worst_text = f"{worst.entry_date}->{worst.exit_date}" if worst is not None else "NA"
        lines.append(
            f"| {result['label']}/{result['symbol']} | {lag['trades']} | {lag['losses']} | {_fmt_pct(lag['loss_rate'])} | "
            f"{lag['median_hold_days']:.1f} | {_fmt_float(lag['loss_entry_pct'])} | {_fmt_float(lag['loss_exit_pct'])} | "
            f"{worst_text} | {_fmt_pct(lag['worst_pct'])} |"
        )
    return "\n".join(lines)


def _sensitivity_table(results: list[dict[str, Any]]) -> str:
    lines = [
        "| series | short_ma | long_ma | in_sample_return | BH_return | DD | trades | loss_rate | PF | win_rate | verdict |",
        "|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---|",
    ]
    for result in results:
        for row in result["sensitivity"]:
            if not row["valid"]:
                lines.append(f"| {result['label']}/{result['symbol']} | {row['short_ma']} | {row['long_ma']} | NA | NA | NA | NA | NA | NA | NA | invalid short>=long |")
                continue
            s6 = row["s6"]
            bh = row["buy_hold"]
            verdict = "beats_BH" if s6["return"] > bh["return"] else "lags_BH"
            lines.append(
                f"| {result['label']}/{result['symbol']} | {row['short_ma']} | {row['long_ma']} | "
                f"{_fmt_pct(s6['return'])} | {_fmt_pct(bh['return'])} | {_fmt_pct(s6['max_drawdown'])} | "
                f"{int(s6['trades'])} | {_fmt_pct(row['loss_rate'])} | {_fmt_float(s6['profit_factor'])} | "
                f"{_fmt_pct(s6['win_rate'])} | {verdict} |"
            )
    return "\n".join(lines)


def _whipsaw_by_short_table(results: list[dict[str, Any]]) -> str:
    grouped: dict[int, list[dict[str, Any]]] = {item: [] for item in SENSITIVITY_SHORT}
    for result in results:
        for row in result["sensitivity"]:
            if row["valid"]:
                grouped[int(row["short_ma"])].append(row)
    lines = [
        "| short_ma | valid_runs | avg_trades | avg_loss_rate | avg_return | avg_DD |",
        "|---:|---:|---:|---:|---:|---:|",
    ]
    for short_ma in SENSITIVITY_SHORT:
        rows = grouped[short_ma]
        if not rows:
            lines.append(f"| {short_ma} | 0 | NA | NA | NA | NA |")
            continue
        trades = float(np.mean([row["s6"]["trades"] for row in rows]))
        loss_rate = float(np.mean([row["loss_rate"] for row in rows]))
        returns = float(np.mean([row["s6"]["return"] for row in rows]))
        dds = float(np.mean([row["s6"]["max_drawdown"] for row in rows]))
        lines.append(f"| {short_ma} | {len(rows)} | {trades:.1f} | {_fmt_pct(loss_rate)} | {_fmt_pct(returns)} | {_fmt_pct(dds)} |")
    return "\n".join(lines)


def _magic_parameter_text(results: list[dict[str, Any]]) -> str:
    gate1 = _load_yaml("backtest.yaml")["gate1"]
    max_dd = float(gate1["max_drawdown_max"])
    grouped: dict[tuple[int, int], list[dict[str, Any]]] = {}
    for result in results:
        for row in result["sensitivity"]:
            if row["valid"]:
                grouped.setdefault((int(row["short_ma"]), int(row["long_ma"])), []).append(row)
    winners = []
    for pair, rows in sorted(grouped.items()):
        if len(rows) != len(results):
            continue
        ok = all(
            row["s6"]["return"] > row["buy_hold"]["return"]
            and row["s6"]["profit_factor"] >= float(gate1["profit_factor_min"])
            and row["s6"]["max_drawdown"] <= max_dd
            for row in rows
        )
        if ok:
            winners.append(f"{pair[0]}/{pair[1]}")
    if winners:
        return "in_sample 出现同时满足三标的 beat BH、PF>=1.3、DD<=20% 的参数：" + ", ".join(winners) + "；但未触碰 OOS，不能据此改默认参数。"
    return "in_sample 未发现同时满足三标的 beat BH、PF>=1.3、DD<=20% 的魔法参数；未触碰 OOS，默认 5/20 不变。"


def _cycle_table(results: list[dict[str, Any]]) -> str:
    lines = [
        "| series | cycle | start | end | S6_return | S6_DD | S6_trades | BH_return | BH_DD | failed_S3b_return | failed_S3c_return | result | criterion |",
        "|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---|---|",
    ]
    for result in results:
        for item in [*result["bull_cycles"], *result["bear_cycles"]]:
            lines.append(
                f"| {result['label']}/{result['symbol']} | {item.name} | {item.start} | {item.end} | "
                f"{_fmt_pct(item.s6['return'])} | {_fmt_pct(item.s6['max_drawdown'])} | {int(item.s6['trades'])} | "
                f"{_fmt_pct(item.buy_hold['return'])} | {_fmt_pct(item.buy_hold['max_drawdown'])} | "
                f"{_fmt_pct(item.s3b['return'])} | {_fmt_pct(item.s3c['return'])} | {'PASS' if item.passed else 'FAIL'} | {item.criterion} |"
            )
    return "\n".join(lines)


def _abc_table(results: list[dict[str, Any]]) -> str:
    lines = [
        "| series | A:bull_cycles_all_pass | B:bear_cycles_all_pass | C:full_history_oos_overall | final |",
        "|---|---|---|---|---|",
    ]
    for result in results:
        lines.append(
            f"| {result['label']}/{result['symbol']} | {'PASS' if result['bull_pass'] else 'FAIL'} | "
            f"{'PASS' if result['bear_pass'] else 'FAIL'} | {'PASS' if result['oos_pass'] else 'FAIL'} | "
            f"{'PASS' if result['passed'] else 'FAIL'} |"
        )
    final_pass = all(result["passed"] for result in results)
    lines.append(f"| TOTAL | - | - | - | {'PASS' if final_pass else 'FAIL'} |")
    return "\n".join(lines)


def _oos_detail_table(results: list[dict[str, Any]]) -> str:
    gate1 = _load_yaml("backtest.yaml")["gate1"]
    lines = [
        "| series | expectancy>0 | PF>=1.3 | DD<=20% | actual_expectancy | actual_PF | actual_DD | OOS_result |",
        "|---|---|---|---|---:|---:|---:|---|",
    ]
    for result in results:
        s6 = result["oos"][0]
        exp_ok = s6["expectancy"] > float(gate1["expectancy_after_cost_gt"])
        pf_ok = s6["profit_factor"] >= float(gate1["profit_factor_min"])
        dd_ok = s6["max_drawdown"] <= float(gate1["max_drawdown_max"])
        lines.append(
            f"| {result['label']}/{result['symbol']} | {'PASS' if exp_ok else 'FAIL'} | {'PASS' if pf_ok else 'FAIL'} | "
            f"{'PASS' if dd_ok else 'FAIL'} | {s6['expectancy']:.2f} | {_fmt_float(s6['profit_factor'])} | "
            f"{_fmt_pct(s6['max_drawdown'])} | {'PASS' if result['oos_pass'] else 'FAIL'} |"
        )
    return "\n".join(lines)


def render_report(depths: list[SeriesDepth], results: list[dict[str, Any]]) -> str:
    cfg = _strategy_cfg()
    final_pass = all(result["passed"] for result in results)
    total_trades = sum(int(result["full_total"][0]["trades"]) for result in results)
    lines = [
        "# S6 Dual MA Crossover Gate1 Report",
        "",
        f"规则：短均线上穿长均线买入、下穿卖出/空仓；默认 short_ma={cfg['short_ma']}、long_ma={cfg['long_ma']}，D 日收盘后判信号，D+1 开盘撮合。",
        "撮合复用 backtest/constraints.py：成本、滑点、T+1、涨跌停/停牌拒单同一套实现。指数 limit_up/down 多为 NaN，偏差方向是略乐观。",
        "",
        "## 数据深度实证",
        "| symbol | name | rows | earliest | latest | source | error |",
        "|---|---|---:|---:|---:|---|---|",
    ]
    for item in depths:
        lines.append(
            f"| {item.symbol} | {item.name} | {item.rows} | {item.earliest or 'NA'} | {item.latest or 'NA'} | {item.source} | {item.error or ''} |"
        )
    lines.extend(
        [
            "",
            "## S6 分段关键指标",
            _summary_table(results),
            "",
            "## 对照组 ratio 表",
            _control_table(results),
            "",
            "对照组结论：S6 与同序列买入持有、已 FAIL 的 S3b(MA200)、已 FAIL 的 S3c(月频 Faber)在同一数据序列和同一撮合约束下重跑。ratio>2x 只标记调查，不作为调参依据。",
            "",
            "## 反假设列表",
            "1. 双均线 lag 导致买在山顶卖在山脚：以下为事后诊断，只分析已发生交易，不参与信号。",
            _lag_table(results),
            "2. 参数敏感性：short_ma∈{5,10,20} × long_ma∈{20,30,60}，仅 full_history in_sample 展示；short>=long 组合无效；未用 OOS 选参。",
            _sensitivity_table(results),
            "短均线越短的 whipsaw 汇总：",
            _whipsaw_by_short_table(results),
            _magic_parameter_text(results),
            "3. 三个标的差异：sh000300、sh000905、sh000001 分别独立判定；任一标的无法跨 bull/bear/OOS 一致通过，TOTAL 即不能 PASS。",
            "",
            "## flag/参数调查记录",
            "- 未调参、未碰 OOS。",
            "- 默认 short_ma/long_ma 保持 configs/strategy_addon.yaml 的 5/20；敏感性表只用 in_sample，不用于改参数。",
            "- full_history bull/bear cycles 完全来自 configs/backtest.yaml，未事后增删。",
            "- 标准 regimes 与 full_history 均重置初始资金独立回测；这是 Gate1 检验口径，不是连续实盘净值。",
            "",
            "## low_freq_significance 跨 cycle 一致性判定表",
            _cycle_table(results),
            "",
            "## A/B/C 判定",
            _abc_table(results),
            "",
            "### C/OOS overall 判据明细",
            _oos_detail_table(results),
            "",
            f"total_default_s6_full_history_trades={total_trades}",
            f"最终判定：{'PASS' if final_pass else 'FAIL'}",
        ]
    )
    return "\n".join(lines) + "\n"


def run(refresh: bool = False) -> dict[str, Any]:
    backtest_cfg = _load_yaml("backtest.yaml")
    end = _parse_date(backtest_cfg["full_history"]["oos"]["end"])
    datasets: dict[str, dict[str, pd.DataFrame]] = {}
    depths: list[SeriesDepth] = []
    for symbol, label in S6_SERIES:
        try:
            frame = _load_series_data(symbol, end, refresh=refresh)
            datasets[symbol] = {symbol: frame}
            depths.append(_coverage(symbol, label, frame))
        except Exception as exc:
            datasets[symbol] = {symbol: pd.DataFrame()}
            depths.append(_coverage(symbol, label, pd.DataFrame(), error=f"{type(exc).__name__}: {exc}"))

    results = []
    for symbol, label in S6_SERIES:
        data = datasets[symbol]
        if data[symbol].empty:
            raise RuntimeError(f"S6 data unavailable for {symbol}")
        results.append(_series_report(symbol, label, data))

    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    path = REPORT_DIR / "s6_dual_ma_gate1.md"
    path.write_text(render_report(depths, results), encoding="utf-8")
    return {
        "path": path,
        "depths": depths,
        "results": results,
        "final_pass": all(result["passed"] for result in results),
        "total_trades": sum(int(result["full_total"][0]["trades"]) for result in results),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Run S6 dual MA Gate1")
    parser.add_argument("--refresh", action="store_true", help="refresh AkShare cache")
    args = parser.parse_args()
    result = run(refresh=args.refresh)
    print(f"wrote {result['path']}")
    for item in result["depths"]:
        print(f"{item.symbol}: rows={item.rows} earliest={item.earliest} latest={item.latest} source={item.source} error={item.error}")
    for item in result["results"]:
        print(
            f"{item['label']}/{item['symbol']}: A_bull={'PASS' if item['bull_pass'] else 'FAIL'} "
            f"B_bear={'PASS' if item['bear_pass'] else 'FAIL'} C_oos={'PASS' if item['oos_pass'] else 'FAIL'} "
            f"final={'PASS' if item['passed'] else 'FAIL'}"
        )
    print(f"S6 trades={result['total_trades']} final={'PASS' if result['final_pass'] else 'FAIL'}")


if __name__ == "__main__":
    main()
