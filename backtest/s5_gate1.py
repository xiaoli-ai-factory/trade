"""S5 classic small-cap monthly strategy Gate1 runner."""

from __future__ import annotations

import argparse
import math
from bisect import bisect_right
from dataclasses import asdict, dataclass, replace
from datetime import date, timedelta
from typing import Any, Callable

import numpy as np
import pandas as pd

from backtest.constraints import CostConfig, MarketBar, Order, Position, apply_execution, mark_sellable, match_order
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
    _gate1_table,
    _gate_checks,
    _last_close,
    _load_yaml,
    _market_bar,
    _mark_nav,
    _max_drawdown,
    _metric_ratio,
    _position_for_symbol,
    _record_execution,
    _ratio_note,
    _trade_metrics,
    summarize_run,
)
from backtest.s2_gate1 import DEFAULT_TOP_N, DEFAULT_WORKERS, _data_by_symbol, _load_or_build_panel
from data.akshare_source import get_etf_daily
from strategies.s5_small_cap import S5SmallCapStrategy


SignalFunc = Callable[[date, dict[str, Any]], list[Order]]


@dataclass(frozen=True)
class S5DataInfo:
    panel_rows: int
    panel_symbols: int
    active_symbols: int
    delisted_symbols: int
    query_start: str
    query_end: str
    month_end_count: int
    candidate_rows: int
    candidate_dates: int
    candidate_min: int
    candidate_median: float
    candidate_max: int
    selected_rows: int
    selected_unique_symbols: int
    selected_delisted_symbols: int
    selected_st_rows: int
    selected_min_mv: float
    selected_median_mv: float
    selected_median_amount: float
    selected_min_amount: float
    pit_mv_assertion_rows: int
    st_during_hold_rows: int
    st_during_hold_symbols: int


@dataclass(frozen=True)
class TurnoverInfo:
    regime: str
    rebalance_count: int
    avg_target_turnover: float
    avg_filled_orders: float


def _parse_date(value: str | date | pd.Timestamp) -> date:
    if isinstance(value, pd.Timestamp):
        return value.date()
    if isinstance(value, date):
        return value
    return pd.Timestamp(str(value)).date()


def _strategy_cfg() -> dict[str, Any]:
    return _load_yaml("strategy_addon.yaml")["s5_small_cap"]


def _regimes() -> dict[str, Any]:
    return _load_yaml("backtest.yaml")["regimes"]


def _month_end_dates(dates: list[date]) -> set[date]:
    if not dates:
        return set()
    frame = pd.DataFrame({"date": pd.to_datetime(pd.Series(dates))})
    frame["month"] = frame["date"].dt.to_period("M")
    return set(frame.groupby("month", sort=True)["date"].max().dt.date.tolist())


def _next_trading_date(calendar_dates: list[date], signal_date: date) -> date | None:
    index = bisect_right(calendar_dates, signal_date)
    if index >= len(calendar_dates):
        return None
    return calendar_dates[index]


def _effective_dates(start: date, end: date, calendar_dates: list[date]) -> list[date]:
    return [item for item in calendar_dates if start <= item <= end]


def _prepare_panel(panel: pd.DataFrame) -> pd.DataFrame:
    df = panel.copy()
    df["symbol"] = df["symbol"].astype(str).str.zfill(6)
    df["date"] = pd.to_datetime(df["date"], errors="coerce").dt.date
    df["list_date"] = pd.to_datetime(df["list_date"], errors="coerce").dt.date
    df["delist_date"] = pd.to_datetime(df["delist_date"], errors="coerce").dt.date
    df = df.dropna(subset=["date"]).sort_values(["symbol", "date"]).reset_index(drop=True)
    for col in ("open", "high", "low", "close", "amount", "turnover", "float_mv", "limit_up_price", "limit_down_price"):
        df[col] = pd.to_numeric(df[col], errors="coerce")
    for col in ("is_st", "is_suspended", "is_delisted"):
        df[col] = df[col].astype(bool)
    grouped = df.groupby("symbol", sort=False)
    df["age_days"] = grouped.cumcount() + 1
    df["mv_date"] = grouped["date"].shift(1)
    df["prev_float_mv"] = grouped["float_mv"].shift(1)
    df["as_of_date"] = df["date"]
    return df


def _build_candidates(panel: pd.DataFrame, month_ends: set[date], cfg: dict[str, Any]) -> pd.DataFrame:
    rows = panel[panel["date"].isin(month_ends)].copy()
    if rows.empty:
        return rows
    as_of = pd.to_datetime(rows["as_of_date"], errors="coerce")
    list_dates = pd.to_datetime(rows["list_date"], errors="coerce")
    delist_dates = pd.to_datetime(rows["delist_date"], errors="coerce")
    mv_dates = pd.to_datetime(rows["mv_date"], errors="coerce")
    eligible = (
        list_dates.notna()
        & (list_dates <= as_of)
        & (delist_dates.isna() | (delist_dates > as_of))
        & (pd.to_numeric(rows["age_days"], errors="coerce") >= int(cfg["exclude_new_days"]))
        & (pd.to_numeric(rows["close"], errors="coerce") >= float(cfg["min_price"]))
        & np.isfinite(pd.to_numeric(rows["prev_float_mv"], errors="coerce"))
        & (pd.to_numeric(rows["prev_float_mv"], errors="coerce") > 0)
        & mv_dates.notna()
        & (mv_dates < as_of)
    )
    if bool(cfg["exclude_st"]):
        eligible &= ~rows["is_st"].astype(bool)
    out = rows[eligible].copy()
    if out.empty:
        return out
    out["market_cap_rank"] = out.groupby("as_of_date")["prev_float_mv"].rank(method="first", ascending=True)
    rank_pct = cfg.get("max_market_cap_rank_pct")
    if rank_pct is not None:
        pct = float(rank_pct)
        counts = out.groupby("as_of_date")["symbol"].transform("count")
        out = out[out["market_cap_rank"] <= counts * pct].copy()
    assert (pd.to_datetime(out["mv_date"], errors="coerce") < pd.to_datetime(out["as_of_date"], errors="coerce")).all()
    return out.sort_values(["as_of_date", "market_cap_rank", "symbol"]).reset_index(drop=True)


def _selected_table(candidates: pd.DataFrame, hold_n: int) -> pd.DataFrame:
    if candidates.empty:
        return candidates.copy()
    return candidates.sort_values(["as_of_date", "prev_float_mv", "symbol"]).groupby("as_of_date", group_keys=False).head(hold_n).copy()


def _candidate_lookup(candidates: pd.DataFrame) -> dict[date, pd.DataFrame]:
    out: dict[date, pd.DataFrame] = {}
    if candidates.empty:
        return out
    rows = candidates.copy()
    rows["as_of_date"] = pd.to_datetime(rows["as_of_date"], errors="coerce").dt.date
    for as_of, frame in rows.groupby("as_of_date", sort=False):
        out[as_of] = frame.copy()
    return out


def _selection_sets(selected: pd.DataFrame) -> dict[date, set[str]]:
    out: dict[date, set[str]] = {}
    if selected.empty:
        return out
    rows = selected.copy()
    rows["as_of_date"] = pd.to_datetime(rows["as_of_date"], errors="coerce").dt.date
    for as_of, frame in rows.groupby("as_of_date", sort=True):
        out[as_of] = set(frame["symbol"].astype(str))
    return out


def _ctx_data_for_positions(data: dict[str, pd.DataFrame], positions: tuple[Position, ...], signal_date: date) -> dict[str, pd.DataFrame]:
    symbols = {item.symbol for item in positions if item.quantity > 0}
    return {symbol: data[symbol][data[symbol]["date"] <= signal_date].copy() for symbol in symbols if symbol in data}


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


def _run_monthly_stock_backtest(
    name: str,
    regime: str,
    start: date,
    end: date,
    data: dict[str, pd.DataFrame],
    candidates_by_date: dict[date, pd.DataFrame],
    calendar_dates: list[date],
    month_ends: set[date],
    signal_func: SignalFunc,
    cost_config: CostConfig,
) -> BacktestRun:
    dates = _effective_dates(start, end, calendar_dates)
    if len(dates) < 2:
        raise RuntimeError(f"Not enough S5 dates for {regime}")
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
        current_candidates = candidates_by_date.get(signal_date, pd.DataFrame())
        ctx = {
            "data": _ctx_data_for_positions(data, positions, signal_date),
            "positions": positions,
            "cash": cash,
            "nav": _mark_nav(cash, positions, data, signal_date),
            "lot_size": LOT_SIZE,
            "rebalance_dates": {signal_date},
            "candidates": current_candidates,
        }
        orders = signal_func(signal_date, ctx)
        if orders:
            pending_orders.setdefault(trade_date, []).extend(orders)

    first_date = dates[0]
    previous_signals = [item for item in month_ends if item < first_date]
    if previous_signals:
        previous = max(previous_signals)
        if _next_trading_date(calendar_dates, previous) == first_date:
            schedule_signal(previous)

    nav_rows = [{"date": first_date.isoformat(), "nav": cash}]
    for trade_date in dates:
        positions = mark_sellable(positions, trade_date)
        orders = pending_orders.pop(trade_date, [])
        if orders:
            cash, positions = _execute_orders(
                orders, trade_date, cash, positions, basis, trades, filled_orders, rejected_orders, events, data, cost_config
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
        start=dates[0],
        end=final_date,
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


def _random_10_signal(hold_n: int) -> SignalFunc:
    def _signal(as_of_date: date, ctx: dict[str, Any]) -> list[Order]:
        candidates = ctx.get("candidates")
        if not isinstance(candidates, pd.DataFrame) or candidates.empty:
            return []
        rows = candidates.copy()
        size = min(hold_n, len(rows))
        rng = np.random.default_rng(RANDOM_SEED + as_of_date.toordinal())
        chosen = set(rng.choice(rows["symbol"].astype(str).to_numpy(), size=size, replace=False).tolist())
        selected = rows[rows["symbol"].astype(str).isin(chosen)].copy()
        return _orders_for_rows(selected, as_of_date, ctx)

    return _signal


def _orders_for_rows(selected: pd.DataFrame, as_of_date: date, ctx: dict[str, Any]) -> list[Order]:
    positions: tuple[Position, ...] = tuple(ctx.get("positions", ()))
    current_qty = {item.symbol: item.quantity for item in positions if item.quantity > 0}
    selected_symbols = set(selected["symbol"].astype(str))
    orders: list[Order] = []
    for symbol, quantity in sorted(current_qty.items()):
        if symbol not in selected_symbols:
            orders.append(Order(symbol=symbol, side="sell", quantity=quantity, submitted_date=as_of_date))
    if selected.empty:
        return orders
    target_value = float(ctx["nav"]) / len(selected)
    for row in selected.sort_values("symbol").itertuples(index=False):
        close = float(row.close)
        if close <= 0 or not np.isfinite(close):
            continue
        target_quantity = int(math.floor((target_value / close) / LOT_SIZE) * LOT_SIZE)
        diff = target_quantity - current_qty.get(str(row.symbol), 0)
        if diff > 0:
            orders.append(Order(symbol=str(row.symbol), side="buy", quantity=diff, submitted_date=as_of_date))
        elif diff < 0:
            orders.append(Order(symbol=str(row.symbol), side="sell", quantity=abs(diff), submitted_date=as_of_date))
    return sorted(orders, key=lambda item: 0 if item.side == "sell" else 1)


def _normalize_etf_frame(frame: pd.DataFrame) -> pd.DataFrame:
    out = frame.copy()
    out["date"] = pd.to_datetime(out["date"], errors="coerce").dt.date
    return out.dropna(subset=["date"]).sort_values("date").reset_index(drop=True)


def _run_etf_buy_hold(symbol: str, regime: str, start: date, end: date, frame: pd.DataFrame, cost_config: CostConfig) -> BacktestRun:
    data = {symbol: frame}
    dates = [item for item in frame["date"].tolist() if start <= item <= end]
    if len(dates) < 2:
        raise RuntimeError(f"Not enough ETF dates for {symbol} {regime}")
    first = dates[0]
    cash = INITIAL_CASH
    positions: tuple[Position, ...] = ()
    basis: dict[str, float] = {}
    trades: list[TradeRecord] = []
    filled_orders: list[dict[str, Any]] = []
    rejected_orders: list[dict[str, Any]] = []
    events: list[dict[str, Any]] = []

    bar = _market_bar(data, symbol, first)
    qty = _affordable_quantity(int((cash / max(bar.open, 1e-9)) // LOT_SIZE * LOT_SIZE), bar.open, cash, cost_config)
    if qty > 0:
        result = match_order(Order(symbol=symbol, side="buy", quantity=qty, submitted_date=first), bar, cost_config)
        if result.status == "filled":
            _apply_basis_on_fill(basis, result, None, first, trades)
            cash += result.cash_delta
            positions = apply_execution(positions, result, first)
            filled_orders.append(_record_execution(result, first))
        else:
            rejected_orders.append(_record_execution(result, first))
        events.extend(result.events)

    nav_rows = []
    for trade_date in dates:
        positions = mark_sellable(positions, trade_date)
        nav_rows.append({"date": trade_date.isoformat(), "nav": _mark_nav(cash, positions, data, trade_date)})

    final = dates[-1]
    positions = mark_sellable(positions, final)
    for position in positions:
        close = _last_close(data, position.symbol, final)
        if close is None:
            continue
        result = match_order(
            Order(symbol=position.symbol, side="sell", quantity=position.quantity, submitted_date=final),
            MarketBar(symbol=position.symbol, date=final, open=close, is_suspended=False),
            cost_config,
            position=position,
        )
        if result.status == "filled":
            _apply_basis_on_fill(basis, result, position, final, trades)
            cash += result.cash_delta
            positions = apply_execution(positions, result, final)
            filled_orders.append(_record_execution(result, final))
        else:
            rejected_orders.append(_record_execution(result, final))
        events.extend(result.events)
    final_nav = _mark_nav(cash, positions, data, final)
    nav_rows[-1] = {"date": final.isoformat(), "nav": final_nav}
    nav_curve = pd.DataFrame(nav_rows)
    return BacktestRun(
        name=f"{symbol}_buy_hold",
        regime=regime,
        start=first,
        end=final,
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


def _run_universe_equal_synthetic(
    regime: str,
    start: date,
    end: date,
    panel: pd.DataFrame,
    candidates_by_date: dict[date, pd.DataFrame],
    calendar_dates: list[date],
    month_ends: set[date],
) -> dict[str, float]:
    dates = _effective_dates(start, end, calendar_dates)
    if not dates:
        return {"return": 0.0, "max_drawdown": 0.0, "trades": 0.0, "fee_ratio": 0.0}
    rows = panel[panel["date"].isin(dates)][["date", "symbol", "open", "close", "is_suspended"]].copy()
    by_date = {item_date: frame.set_index("symbol") for item_date, frame in rows.groupby("date", sort=False)}
    trade_signal: dict[date, date] = {}
    first = dates[0]
    previous = [item for item in month_ends if item < first]
    if previous and _next_trading_date(calendar_dates, max(previous)) == first:
        trade_signal[first] = max(previous)
    for signal_date in sorted(item for item in month_ends if start <= item <= end):
        trade_date = _next_trading_date(calendar_dates, signal_date)
        if trade_date is not None and start <= trade_date <= end:
            trade_signal[trade_date] = signal_date

    nav = INITIAL_CASH
    shares: dict[str, float] = {}
    last_prices: dict[str, float] = {}
    nav_rows: list[dict[str, Any]] = []
    rebalance_count = 0
    for trade_date in dates:
        if trade_date in trade_signal:
            signal_date = trade_signal[trade_date]
            candidates = candidates_by_date.get(signal_date, pd.DataFrame())
            day_rows = by_date.get(trade_date, pd.DataFrame())
            if not candidates.empty and not day_rows.empty:
                symbols = candidates["symbol"].astype(str).tolist()
                entries = day_rows.reindex(symbols)
                entries = entries[(~entries["is_suspended"].fillna(True).astype(bool)) & (pd.to_numeric(entries["open"], errors="coerce") > 0)].copy()
                if not entries.empty:
                    target = nav / len(entries)
                    shares = {str(symbol): float(target / float(row.open)) for symbol, row in entries.iterrows()}
                    last_prices = {str(symbol): float(row.open) for symbol, row in entries.iterrows()}
                    rebalance_count += 1
        day_rows = by_date.get(trade_date, pd.DataFrame())
        if shares and not day_rows.empty:
            marked = day_rows.reindex(list(shares))
            close = pd.to_numeric(marked["close"], errors="coerce")
            for symbol, price in close.dropna().items():
                if price > 0:
                    last_prices[str(symbol)] = float(price)
            nav = sum(qty * last_prices.get(symbol, 0.0) for symbol, qty in shares.items())
        nav_rows.append({"date": trade_date.isoformat(), "nav": nav})
    nav_curve = pd.DataFrame(nav_rows)
    return {
        "return": float(nav / INITIAL_CASH - 1.0),
        "max_drawdown": _max_drawdown(nav_curve),
        "trades": float(rebalance_count),
        "fee_ratio": 0.0,
    }


def _selected_holding_st_stats(selected: pd.DataFrame, panel: pd.DataFrame, calendar_dates: list[date]) -> tuple[int, int]:
    if selected.empty:
        return 0, 0
    st_panel = panel[panel["is_st"].astype(bool)].copy()
    if st_panel.empty:
        return 0, 0
    selected = selected.copy()
    selected["as_of_date"] = pd.to_datetime(selected["as_of_date"], errors="coerce").dt.date
    month_ends = sorted(_month_end_dates(calendar_dates))
    st_rows = []
    for row in selected[["as_of_date", "symbol"]].itertuples(index=False):
        trade_date = _next_trading_date(calendar_dates, row.as_of_date)
        if trade_date is None:
            continue
        next_signal = _next_month_end_after(row.as_of_date, month_ends)
        end_date = _next_trading_date(calendar_dates, next_signal) if next_signal is not None else None
        if end_date is None:
            end_date = max(calendar_dates)
        mask = (st_panel["symbol"] == row.symbol) & (st_panel["date"] >= trade_date) & (st_panel["date"] <= end_date)
        if mask.any():
            st_rows.append((row.symbol, trade_date))
    return len(st_rows), len({item[0] for item in st_rows})


def _next_month_end_after(signal_date: date, month_ends: list[date]) -> date | None:
    for item in month_ends:
        if item > signal_date:
            return item
    return None


def _data_info(panel: pd.DataFrame, candidates: pd.DataFrame, selected: pd.DataFrame, month_ends: set[date], calendar_dates: list[date]) -> S5DataInfo:
    counts = candidates.groupby("as_of_date")["symbol"].nunique() if not candidates.empty else pd.Series(dtype="float64")
    st_rows, st_symbols = _selected_holding_st_stats(selected, panel, calendar_dates)
    selected_mv = pd.to_numeric(selected.get("prev_float_mv", pd.Series(dtype="float64")), errors="coerce")
    selected_amount = pd.to_numeric(selected.get("amount", pd.Series(dtype="float64")), errors="coerce")
    return S5DataInfo(
        panel_rows=len(panel),
        panel_symbols=int(panel["symbol"].nunique()),
        active_symbols=int((~panel.groupby("symbol")["is_delisted"].max().astype(bool)).sum()),
        delisted_symbols=int(panel.groupby("symbol")["is_delisted"].max().astype(bool).sum()),
        query_start=str(min(panel["date"])) if not panel.empty else "NA",
        query_end=str(max(panel["date"])) if not panel.empty else "NA",
        month_end_count=len(month_ends),
        candidate_rows=len(candidates),
        candidate_dates=int(candidates["as_of_date"].nunique()) if not candidates.empty else 0,
        candidate_min=int(counts.min()) if not counts.empty else 0,
        candidate_median=float(counts.median()) if not counts.empty else 0.0,
        candidate_max=int(counts.max()) if not counts.empty else 0,
        selected_rows=len(selected),
        selected_unique_symbols=int(selected["symbol"].nunique()) if not selected.empty else 0,
        selected_delisted_symbols=int(selected[selected["is_delisted"].astype(bool)]["symbol"].nunique()) if not selected.empty else 0,
        selected_st_rows=int(selected["is_st"].astype(bool).sum()) if not selected.empty else 0,
        selected_min_mv=float(selected_mv.min()) if not selected_mv.empty else math.nan,
        selected_median_mv=float(selected_mv.median()) if not selected_mv.empty else math.nan,
        selected_median_amount=float(selected_amount.median()) if not selected_amount.empty else math.nan,
        selected_min_amount=float(selected_amount.min()) if not selected_amount.empty else math.nan,
        pit_mv_assertion_rows=len(candidates),
        st_during_hold_rows=st_rows,
        st_during_hold_symbols=st_symbols,
    )


def _turnover_info(regime: str, start: date, end: date, selected_sets: dict[date, set[str]], runs: dict[str, BacktestRun], calendar_dates: list[date], month_ends: set[date]) -> TurnoverInfo:
    signal_dates: list[date] = []
    first = next(item for item in calendar_dates if item >= start)
    previous = [item for item in month_ends if item < first]
    if previous and _next_trading_date(calendar_dates, max(previous)) == first:
        signal_dates.append(max(previous))
    signal_dates.extend(sorted(item for item in month_ends if start <= item <= end and _next_trading_date(calendar_dates, item) is not None and _next_trading_date(calendar_dates, item) <= end))
    turnovers = []
    prev: set[str] | None = None
    for signal_date in signal_dates:
        current = selected_sets.get(signal_date, set())
        if prev is not None and current:
            turnovers.append(1.0 - len(prev & current) / len(current))
        prev = current
    filled = len(runs["s5"].filled_orders)
    count = max(1, len(signal_dates))
    return TurnoverInfo(regime, len(signal_dates), float(np.mean(turnovers)) if turnovers else 0.0, filled / count)


def _run_summary_table(runs: dict[str, BacktestRun]) -> str:
    lines = [
        "| regime | return | max_drawdown | trades | expectancy_after_cost | profit_factor | win_rate | fee_ratio | forced_hold | filled_orders |",
        "|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for name in ("bull", "bear", "range", "oos"):
        metrics = summarize_run(runs[name])
        forced = sum(1 for item in runs[name].events if item.get("type") == "forced_hold")
        lines.append(
            f"| {name} | {_fmt_pct(metrics['return'])} | {_fmt_pct(metrics['max_drawdown'])} | {int(metrics['trades'])} | "
            f"{metrics['expectancy']:.2f} | {_fmt_float(metrics['profit_factor'])} | {_fmt_pct(metrics['win_rate'])} | "
            f"{_fmt_pct(metrics['fee_ratio'])} | {forced} | {len(runs[name].filled_orders)} |"
        )
    return "\n".join(lines)


def _in_oos_table(runs: dict[str, BacktestRun]) -> str:
    in_trades = tuple(item for name in ("bull", "bear", "range") for item in runs[name].trades)
    in_metrics = _trade_metrics(in_trades)
    oos = summarize_run(runs["oos"])
    avg_in = float(np.mean([runs[name].total_return for name in ("bull", "bear", "range")]))
    worst_dd = max(runs[name].max_drawdown for name in ("bull", "bear", "range"))
    lines = [
        "| span | avg/period_return | trades | expectancy | profit_factor | win_rate | max_drawdown_worst |",
        "|---|---:|---:|---:|---:|---:|---:|",
        f"| in_sample(bull+bear+range) | {_fmt_pct(avg_in)} | {int(in_metrics['trades'])} | {in_metrics['expectancy']:.2f} | {_fmt_float(in_metrics['profit_factor'])} | {_fmt_pct(in_metrics['win_rate'])} | {_fmt_pct(worst_dd)} |",
        f"| oos | {_fmt_pct(oos['return'])} | {int(oos['trades'])} | {oos['expectancy']:.2f} | {_fmt_float(oos['profit_factor'])} | {_fmt_pct(oos['win_rate'])} | {_fmt_pct(oos['max_drawdown'])} |",
    ]
    return "\n".join(lines)


def _fmt_metric(metrics: dict[str, float], key: str, pct: bool) -> str:
    return _fmt_pct(metrics[key]) if pct else _fmt_float(metrics[key])


def _comparison_table(all_runs: dict[str, dict[str, Any]]) -> str:
    lines = [
        "| regime | metric | S5 | HS300ETF_BH | CSI500ETF_BH | universe_all_equal_synth | random10_monthly | S5/HS300 | S5/CSI500 | note |",
        "|---|---|---:|---:|---:|---:|---:|---:|---:|---|",
    ]
    for regime in ("bull", "bear", "range", "oos"):
        s5 = summarize_run(all_runs[regime]["s5"])
        hs300 = summarize_run(all_runs[regime]["hs300"])
        csi500 = summarize_run(all_runs[regime]["csi500"])
        uni = all_runs[regime]["universe_equal"]
        rnd = summarize_run(all_runs[regime]["random10"])
        for metric, pct in (("return", True), ("max_drawdown", True), ("trades", False), ("fee_ratio", True)):
            r1 = _metric_ratio(s5[metric], hs300[metric])
            r2 = _metric_ratio(s5[metric], csi500[metric])
            fmt = _fmt_pct if pct else _fmt_float
            lines.append(
                f"| {regime} | {metric} | {fmt(s5[metric])} | {fmt(hs300[metric])} | {fmt(csi500[metric])} | "
                f"{fmt(uni[metric])} | {fmt(rnd[metric])} | {_fmt_float(r1)} | {_fmt_float(r2)} | {_ratio_note(r1, r2)} |"
            )
    return "\n".join(lines)


def _turnover_table(turnovers: dict[str, TurnoverInfo]) -> str:
    lines = ["| regime | rebalance_count | avg_target_turnover | avg_filled_orders_per_rebalance |", "|---|---:|---:|---:|"]
    for name in ("bull", "bear", "range", "oos"):
        item = turnovers[name]
        lines.append(f"| {name} | {item.rebalance_count} | {_fmt_pct(item.avg_target_turnover)} | {item.avg_filled_orders:.2f} |")
    return "\n".join(lines)


def _data_info_lines(info: S5DataInfo) -> list[str]:
    return [
        f"- panel={info.panel_rows} rows / {info.panel_symbols} symbols；active={info.active_symbols}, delisted={info.delisted_symbols}。",
        f"- panel span={info.query_start}..{info.query_end}；month_end_count={info.month_end_count}。",
        f"- PIT candidate rows={info.candidate_rows}, dates={info.candidate_dates}, per-month universe min/median/max={info.candidate_min}/{info.candidate_median:.1f}/{info.candidate_max}。",
        f"- selected rows={info.selected_rows}, unique_symbols={info.selected_unique_symbols}, selected_delisted_symbols={info.selected_delisted_symbols}, selected_ST_rows_at_signal={info.selected_st_rows}。",
        f"- D-1 market-cap assertion rows={info.pit_mv_assertion_rows}; all candidates satisfy mv_date < as_of_date。",
        f"- selected prev_float_mv min/median={info.selected_min_mv:.0f}/{info.selected_median_mv:.0f}; D-day amount min/median={info.selected_min_amount:.0f}/{info.selected_median_amount:.0f}。",
        f"- ST during holding window rows/symbols={info.st_during_hold_rows}/{info.st_during_hold_symbols}。",
        "- ST caveat: panel ST flag is the best available AkShare-derived approximation; historical point-in-time ST transitions are not complete in free data.",
    ]


def render_report(
    all_runs: dict[str, dict[str, Any]],
    data_info: S5DataInfo,
    turnovers: dict[str, TurnoverInfo],
    candidates: pd.DataFrame,
) -> str:
    s5_runs = {name: runs["s5"] for name, runs in all_runs.items()}
    checks = _gate_checks(s5_runs, _load_yaml("backtest.yaml")["gate1"])
    final = "PASS" if checks["overall_pass"] else "FAIL"
    cfg = _strategy_cfg()
    bull = summarize_run(s5_runs["bull"])
    oos = summarize_run(s5_runs["oos"])
    decay_note = (
        "bull 明显强于 OOS，支持 2017 后小市值溢价衰减/不稳定假设。"
        if bull["return"] > oos["return"]
        else "本次 bull 未强于 OOS；不能用本段证明 2024 后衰减，但仍必须看 A/B/C 是否过关。"
    )
    forced_hold = sum(1 for run in s5_runs.values() for item in run.events if item.get("type") == "forced_hold")
    sell_rejections = sum(1 for run in s5_runs.values() for item in run.rejected_orders if item.get("side") == "sell")
    forced_ratio = forced_hold / sell_rejections if sell_rejections else 0.0
    lines = [
        "# S5 Small-Cap Gate1 Report",
        "",
        f"规则：月末 D 收盘后，在 PIT 全市场 panel 中剔除 ST={cfg['exclude_st']}、上市不足 {cfg['exclude_new_days']} 个交易日、价格低于 {cfg['min_price']} 的股票；按 D-1 `prev_float_mv` 升序取 hold_n={cfg['hold_n']}，下月首个交易日开盘等权调仓。",
        "关键无未来函数约束：排名只用 `mv_date < as_of_date` 的 float_mv，严禁 D 日 close 计算市值；D 日 close 只用于月末已知的价格过滤和目标股数估算。",
        "",
        "## 数据与 universe",
        *_data_info_lines(data_info),
        "",
        "## S5 分段关键指标",
        _run_summary_table(s5_runs),
        "",
        "## 月均换手率",
        _turnover_table(turnovers),
        "",
        "## in-sample vs OOS 差异",
        _in_oos_table(s5_runs),
        "",
        "## 对照组 ratio 表",
        _comparison_table(all_runs),
        "",
        "## 反假设列表",
        f"- 小市值溢价是否是 2017 前 phenomenon：Gate1 panel 从 2019-10 开始，无法直接验证 2017 前；用 post-2019 的 bull(2020-07..2021-02) vs OOS(2024-10..) 看衰减。bull return={_fmt_pct(bull['return'])}/PF={_fmt_float(bull['profit_factor'])}，OOS return={_fmt_pct(oos['return'])}/PF={_fmt_float(oos['profit_factor'])}。{decay_note}",
        f"- 流动性陷阱：最小市值 10 只的 selected median D-day amount={data_info.selected_median_amount:.0f}，min amount={data_info.selected_min_amount:.0f}；0.2% 滑点可能严重低估真实冲击成本，尤其在涨跌停/停牌和小成交额月份。",
        f"- ST/退市风险：信号时剔除 ST，但 selected_delisted_symbols={data_info.selected_delisted_symbols}（退市前仍可入池），持仓窗口内 ST rows/symbols={data_info.st_during_hold_rows}/{data_info.st_during_hold_symbols}；forced_hold={forced_hold}, sell_rejections={sell_rejections}, forced_hold占卖单拒单={_fmt_pct(forced_ratio)}。",
        "- 同 universe 全量等权是 synthetic no-cost benchmark，用于看小市值池整体漂移，不代表散户可逐只实盘复制；S5/random10/ETF 对照均走 constraints.py。",
        "",
        "## flag/参数调查记录",
        "- 未调 hold_n，固定使用 strategy_addon.yaml 的 hold_n=10。",
        "- 未碰 OOS 调参；OOS 只在固定规则跑完后用于 C 组最终裁决。",
        "- 未用 D 日 close 计算市值；候选表断言 `mv_date < as_of_date`。",
        "- 未修改成本、滑点、regime 或 Gate1 阈值。",
        "",
        "## Gate1 判定表",
        _gate1_table(checks, _load_yaml("backtest.yaml")["gate1"]),
        "",
        f"最终判定：{final}，按高换手标准。",
    ]
    return "\n".join(lines) + "\n"


def _load_etf_data(start: date, end: date) -> dict[str, pd.DataFrame]:
    return {
        "510300": _normalize_etf_frame(get_etf_daily("510300", start=start - timedelta(days=10), end=end, refresh=False)),
        "510500": _normalize_etf_frame(get_etf_daily("510500", start=start - timedelta(days=10), end=end, refresh=False)),
    }


def run(refresh: bool = False, workers: int = DEFAULT_WORKERS) -> dict[str, Any]:
    cfg = _strategy_cfg()
    regimes = _regimes()
    global_start = min(_parse_date(span["start"]) for span in regimes.values())
    global_end = max(_parse_date(span["end"]) for span in regimes.values())
    raw_panel, _s2_info = _load_or_build_panel(global_start, global_end, DEFAULT_TOP_N, workers, refresh)
    panel = _prepare_panel(raw_panel)
    calendar_dates = sorted(panel["date"].dropna().unique().tolist())
    month_ends = _month_end_dates(calendar_dates)
    candidates = _build_candidates(panel, month_ends, cfg)
    selected = _selected_table(candidates, int(cfg["hold_n"]))
    candidates_by_date = _candidate_lookup(candidates)
    selected_sets = _selection_sets(selected)
    data = _data_by_symbol(panel)
    cost_config = CostConfig.from_mapping(_load_yaml("cost.yaml"))
    etf_data = _load_etf_data(global_start, global_end)

    strategy = S5SmallCapStrategy(cfg)
    all_runs: dict[str, dict[str, Any]] = {}
    turnovers: dict[str, TurnoverInfo] = {}
    for regime, span in regimes.items():
        start = _parse_date(span["start"])
        end = _parse_date(span["end"])
        all_runs[regime] = {
            "s5": _run_monthly_stock_backtest(
                "s5",
                regime,
                start,
                end,
                data,
                candidates_by_date,
                calendar_dates,
                month_ends,
                strategy.generate_signals,
                cost_config,
            ),
            "random10": _run_monthly_stock_backtest(
                "s5_random10",
                regime,
                start,
                end,
                data,
                candidates_by_date,
                calendar_dates,
                month_ends,
                _random_10_signal(int(cfg["hold_n"])),
                cost_config,
            ),
            "hs300": _run_etf_buy_hold("510300", regime, start, end, etf_data["510300"], cost_config),
            "csi500": _run_etf_buy_hold("510500", regime, start, end, etf_data["510500"], cost_config),
            "universe_equal": _run_universe_equal_synthetic(regime, start, end, panel, candidates_by_date, calendar_dates, month_ends),
        }
        turnovers[regime] = _turnover_info(regime, start, end, selected_sets, all_runs[regime], calendar_dates, month_ends)

    data_info = _data_info(panel, candidates, selected, month_ends, calendar_dates)
    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    path = REPORT_DIR / "s5_small_cap_gate1.md"
    path.write_text(render_report(all_runs, data_info, turnovers, candidates), encoding="utf-8")
    return {
        "path": path,
        "runs": all_runs,
        "data_info": data_info,
        "turnovers": turnovers,
        "candidates": candidates,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Run S5 small-cap Gate1")
    parser.add_argument("--refresh", action="store_true")
    parser.add_argument("--workers", type=int, default=DEFAULT_WORKERS)
    args = parser.parse_args()
    result = run(refresh=args.refresh, workers=args.workers)
    s5_runs = {regime: runs["s5"] for regime, runs in result["runs"].items()}
    checks = _gate_checks(s5_runs, _load_yaml("backtest.yaml")["gate1"])
    trades = int(sum(len(run.trades) for run in s5_runs.values()))
    print(f"wrote {result['path']}")
    print(f"S5 trades={trades} final={'PASS' if checks['overall_pass'] else 'FAIL'}")
    print(_run_summary_table(s5_runs))
    print(_in_oos_table(s5_runs))
    print(_gate1_table(checks, _load_yaml("backtest.yaml")["gate1"]))
    print("data_info=" + str(asdict(result["data_info"])))


if __name__ == "__main__":
    main()
