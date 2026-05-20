"""S7 limit-up follow-up Gate1 runner."""

from __future__ import annotations

import argparse
import math
from dataclasses import asdict, dataclass
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
    _parse_date,
    _record_execution,
    _ratio_note,
    _trade_metrics,
    summarize_run,
)
from backtest.s2_gate1 import DEFAULT_TOP_N, DEFAULT_WORKERS, _data_by_symbol, _load_or_build_panel
from data.akshare_source import get_etf_daily
from strategies.s7_limit_up_followup import S7LimitUpFollowupStrategy


SignalFunc = Callable[[date, dict[str, Any]], list[Order]]
AMOUNT_SENSITIVITY = (1.0e8, 5.0e8, 1.0e9)


@dataclass(frozen=True)
class S7DataInfo:
    panel_rows: int
    panel_symbols: int
    active_symbols: int
    delisted_symbols: int
    query_start: str
    query_end: str
    calendar_dates: int
    base_rows: int
    base_dates: int
    limit_up_rows: int
    limit_up_dates: int
    selected_rows: int
    selected_unique_symbols: int
    selected_delisted_symbols: int
    selected_st_rows: int
    amount_min: float
    amount_median: float
    amount_max: float


@dataclass
class ExecutionStats:
    buy_orders: int = 0
    buy_filled: int = 0
    buy_rejected: int = 0
    buy_rejected_limit_up: int = 0
    buy_skipped_cash: int = 0
    sell_orders: int = 0
    sell_filled: int = 0
    sell_rejected: int = 0
    sell_rejected_limit_down: int = 0
    sell_rejected_suspended: int = 0
    forced_hold_events: int = 0

    @property
    def limit_up_reject_ratio(self) -> float:
        return self.buy_rejected_limit_up / self.buy_orders if self.buy_orders else 0.0

    @property
    def forced_hold_sell_order_ratio(self) -> float:
        return self.forced_hold_events / self.sell_orders if self.sell_orders else 0.0

    @property
    def forced_hold_sell_reject_ratio(self) -> float:
        return self.forced_hold_events / self.sell_rejected if self.sell_rejected else 0.0


def _strategy_cfg(amount_override: float | None = None) -> dict[str, Any]:
    cfg = _load_yaml("strategy_addon.yaml")["s7_limit_up_followup"].copy()
    if amount_override is not None:
        cfg["prefilter_min_amount"] = float(amount_override)
    return cfg


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
    df["age_days"] = df.groupby("symbol", sort=False).cumcount() + 1
    return df


def _is_limit_up(rows: pd.DataFrame) -> pd.Series:
    close = pd.to_numeric(rows["close"], errors="coerce").round(2)
    limit_up = pd.to_numeric(rows["limit_up_price"], errors="coerce").round(2)
    return close.notna() & limit_up.notna() & (close == limit_up)


def _base_universe(panel: pd.DataFrame, cfg: dict[str, Any]) -> pd.DataFrame:
    rows = panel.copy()
    row_dates = pd.to_datetime(rows["date"], errors="coerce")
    list_dates = pd.to_datetime(rows["list_date"], errors="coerce")
    delist_dates = pd.to_datetime(rows["delist_date"], errors="coerce")
    eligible = (
        list_dates.notna()
        & (list_dates <= row_dates)
        & (delist_dates.isna() | (delist_dates > row_dates))
        & (pd.to_numeric(rows["age_days"], errors="coerce") >= int(cfg["exclude_new_days"]))
        & (~rows["is_suspended"].astype(bool))
        & (pd.to_numeric(rows["close"], errors="coerce") > 0)
        & (pd.to_numeric(rows["amount"], errors="coerce") >= float(cfg["prefilter_min_amount"]))
    )
    if bool(cfg["exclude_st"]):
        eligible &= ~rows["is_st"].astype(bool)
    out = rows[eligible].copy()
    out["as_of_date"] = out["date"]
    return out.sort_values(["as_of_date", "symbol"]).reset_index(drop=True)


def _build_limit_up_candidates(panel: pd.DataFrame, cfg: dict[str, Any]) -> pd.DataFrame:
    base = _base_universe(panel, cfg)
    if base.empty:
        return base
    out = base[_is_limit_up(base)].copy()
    return out.sort_values(["as_of_date", "amount", "symbol"], ascending=[True, False, True]).reset_index(drop=True)


def _selected_table(candidates: pd.DataFrame, max_positions: int) -> pd.DataFrame:
    if candidates.empty:
        return candidates.copy()
    return (
        candidates.sort_values(["as_of_date", "amount", "symbol"], ascending=[True, False, True])
        .groupby("as_of_date", group_keys=False)
        .head(max_positions)
        .copy()
    )


def _candidate_lookup(candidates: pd.DataFrame) -> dict[date, pd.DataFrame]:
    if candidates.empty:
        return {}
    out = {}
    rows = candidates.copy()
    rows["as_of_date"] = pd.to_datetime(rows["as_of_date"], errors="coerce").dt.date
    for item_date, frame in rows.groupby("as_of_date", sort=False):
        out[item_date] = frame.copy()
    return out


def _effective_dates(start: date, end: date, calendar_dates: list[date]) -> list[date]:
    return [item for item in calendar_dates if start <= item <= end]


def _ctx_data_frame(candidates: pd.DataFrame) -> dict[str, pd.DataFrame]:
    if candidates.empty:
        return {"candidates": pd.DataFrame(columns=["date"])}
    frame = candidates[["as_of_date"]].rename(columns={"as_of_date": "date"}).copy()
    return {"candidates": frame}


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
    stats: ExecutionStats,
) -> tuple[float, tuple[Position, ...]]:
    for order in sorted(orders, key=lambda item: 0 if item.side == "sell" else 1):
        if order.side == "buy":
            stats.buy_orders += 1
        else:
            stats.sell_orders += 1
        bar = _market_bar(data, order.symbol, trade_date)
        order_to_match = order
        if order.side == "buy":
            affordable = _affordable_quantity(order.quantity, bar.open, cash, cost_config)
            if affordable <= 0:
                stats.buy_skipped_cash += 1
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
            if order.side == "buy":
                stats.buy_filled += 1
            else:
                stats.sell_filled += 1
        else:
            rejected_orders.append(_record_execution(result, trade_date))
            if order.side == "buy":
                stats.buy_rejected += 1
                if result.reason == "limit_up_open":
                    stats.buy_rejected_limit_up += 1
            else:
                stats.sell_rejected += 1
                if result.reason == "limit_down_open":
                    stats.sell_rejected_limit_down += 1
                elif result.reason == "suspended":
                    stats.sell_rejected_suspended += 1
        stats.forced_hold_events += sum(1 for event in result.events if event.get("type") == "forced_hold")
        events.extend(result.events)
    return cash, positions


def _position_for_symbol(positions: tuple[Position, ...], symbol: str) -> Position | None:
    for item in positions:
        if item.symbol == symbol:
            return item
    return None


def _run_daily_followup_backtest(
    name: str,
    regime: str,
    start: date,
    end: date,
    data: dict[str, pd.DataFrame],
    candidates_by_date: dict[date, pd.DataFrame],
    calendar_dates: list[date],
    signal_func: SignalFunc,
    cost_config: CostConfig,
) -> tuple[BacktestRun, ExecutionStats]:
    dates = _effective_dates(start, end, calendar_dates)
    if len(dates) < 2:
        raise RuntimeError(f"Not enough S7 dates for {regime}")
    cash = INITIAL_CASH
    positions: tuple[Position, ...] = ()
    basis: dict[str, float] = {}
    trades: list[TradeRecord] = []
    filled_orders: list[dict[str, Any]] = []
    rejected_orders: list[dict[str, Any]] = []
    events: list[dict[str, Any]] = []
    stats = ExecutionStats()
    nav_rows = [{"date": dates[0].isoformat(), "nav": cash}]

    for signal_date, trade_date in zip(dates[:-1], dates[1:], strict=False):
        positions = mark_sellable(positions, trade_date)
        current_candidates = candidates_by_date.get(signal_date, pd.DataFrame())
        ctx = {
            "data": _ctx_data_frame(current_candidates),
            "positions": positions,
            "cash": cash,
            "nav": _mark_nav(cash, positions, data, signal_date),
            "lot_size": LOT_SIZE,
            "candidates": current_candidates,
        }
        orders = signal_func(signal_date, ctx)
        if orders:
            cash, positions = _execute_orders(
                orders, trade_date, cash, positions, basis, trades, filled_orders, rejected_orders, events, data, cost_config, stats
            )
        nav_rows.append({"date": trade_date.isoformat(), "nav": _mark_nav(cash, positions, data, trade_date)})

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
    run = BacktestRun(
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
    return run, stats


def _random_followup_signal(max_positions: int) -> SignalFunc:
    def _signal(as_of_date: date, ctx: dict[str, Any]) -> list[Order]:
        candidates = ctx.get("candidates")
        positions: tuple[Position, ...] = tuple(ctx.get("positions", ()))
        orders = [
            Order(symbol=item.symbol, side="sell", quantity=item.quantity, submitted_date=as_of_date)
            for item in sorted(positions, key=lambda pos: pos.symbol)
            if item.quantity > 0 and item.sellable and item.buy_date <= as_of_date
        ]
        if not isinstance(candidates, pd.DataFrame) or candidates.empty:
            return orders
        rows = candidates.copy()
        size = min(max_positions, len(rows))
        rng = np.random.default_rng(RANDOM_SEED + as_of_date.toordinal())
        chosen = set(rng.choice(rows["symbol"].astype(str).to_numpy(), size=size, replace=False).tolist())
        selected = rows[rows["symbol"].astype(str).isin(chosen)].copy()
        nav = float(ctx["nav"])
        target_value = nav / max_positions
        current = {item.symbol: item.quantity for item in positions if item.quantity > 0}
        for row in selected.sort_values("symbol").itertuples(index=False):
            symbol = str(row.symbol)
            if current.get(symbol, 0) > 0:
                continue
            close = float(row.close)
            if close <= 0 or not np.isfinite(close):
                continue
            qty = int(math.floor((target_value / close) / LOT_SIZE) * LOT_SIZE)
            if qty > 0:
                orders.append(Order(symbol=symbol, side="buy", quantity=qty, submitted_date=as_of_date))
        return sorted(orders, key=lambda item: 0 if item.side == "sell" else 1)

    return _signal


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
    desired = int((cash / max(bar.open, 1e-9)) // LOT_SIZE * LOT_SIZE)
    qty = _affordable_quantity(desired, bar.open, cash, cost_config)
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


def _load_etf_data(start: date, end: date) -> pd.DataFrame:
    return _normalize_etf_frame(get_etf_daily("510300", start=start - timedelta(days=10), end=end, refresh=False))


def _merged_metrics(runs: dict[str, BacktestRun], names: tuple[str, ...]) -> dict[str, float]:
    return _trade_metrics(tuple(item for name in names for item in runs[name].trades))


def _summary_table(runs: dict[str, BacktestRun], stats: dict[str, ExecutionStats]) -> str:
    lines = [
        "| regime | return | max_drawdown | trades | expectancy_after_cost | profit_factor | win_rate | fee_ratio | buy_limit_up_reject | forced_hold/sell_orders |",
        "|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for name in ("bull", "bear", "range", "oos"):
        metrics = summarize_run(runs[name])
        item = stats[name]
        lines.append(
            f"| {name} | {_fmt_pct(metrics['return'])} | {_fmt_pct(metrics['max_drawdown'])} | {int(metrics['trades'])} | "
            f"{metrics['expectancy']:.2f} | {_fmt_float(metrics['profit_factor'])} | {_fmt_pct(metrics['win_rate'])} | "
            f"{_fmt_pct(metrics['fee_ratio'])} | {_fmt_pct(item.limit_up_reject_ratio)} | {_fmt_pct(item.forced_hold_sell_order_ratio)} |"
        )
    return "\n".join(lines)


def _execution_reality_table(stats: dict[str, ExecutionStats]) -> str:
    lines = [
        "| regime | buy_orders | buy_filled | buy_rejected_limit_up | limit_up_reject_ratio | buy_skipped_cash | sell_orders | sell_rejected | forced_hold | forced/sell_orders | forced/sell_rejections |",
        "|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for name in ("bull", "bear", "range", "oos"):
        item = stats[name]
        lines.append(
            f"| {name} | {item.buy_orders} | {item.buy_filled} | {item.buy_rejected_limit_up} | {_fmt_pct(item.limit_up_reject_ratio)} | "
            f"{item.buy_skipped_cash} | {item.sell_orders} | {item.sell_rejected} | {item.forced_hold_events} | "
            f"{_fmt_pct(item.forced_hold_sell_order_ratio)} | {_fmt_pct(item.forced_hold_sell_reject_ratio)} |"
        )
    total = ExecutionStats()
    for item in stats.values():
        for key, value in asdict(item).items():
            setattr(total, key, getattr(total, key) + int(value))
    lines.append(
        f"| TOTAL | {total.buy_orders} | {total.buy_filled} | {total.buy_rejected_limit_up} | {_fmt_pct(total.limit_up_reject_ratio)} | "
        f"{total.buy_skipped_cash} | {total.sell_orders} | {total.sell_rejected} | {total.forced_hold_events} | "
        f"{_fmt_pct(total.forced_hold_sell_order_ratio)} | {_fmt_pct(total.forced_hold_sell_reject_ratio)} |"
    )
    return "\n".join(lines)


def _in_oos_table(runs: dict[str, BacktestRun]) -> str:
    in_metrics = _merged_metrics(runs, ("bull", "bear", "range"))
    oos = summarize_run(runs["oos"])
    avg_in_return = float(np.mean([runs[name].total_return for name in ("bull", "bear", "range")]))
    worst_in_dd = max(runs[name].max_drawdown for name in ("bull", "bear", "range"))
    lines = [
        "| span | avg/period_return | trades | expectancy | profit_factor | win_rate | max_drawdown_worst |",
        "|---|---:|---:|---:|---:|---:|---:|",
        f"| in_sample(bull+bear+range) | {_fmt_pct(avg_in_return)} | {int(in_metrics['trades'])} | {in_metrics['expectancy']:.2f} | {_fmt_float(in_metrics['profit_factor'])} | {_fmt_pct(in_metrics['win_rate'])} | {_fmt_pct(worst_in_dd)} |",
        f"| oos | {_fmt_pct(oos['return'])} | {int(oos['trades'])} | {oos['expectancy']:.2f} | {_fmt_float(oos['profit_factor'])} | {_fmt_pct(oos['win_rate'])} | {_fmt_pct(oos['max_drawdown'])} |",
    ]
    return "\n".join(lines)


def _comparison_table(all_runs: dict[str, dict[str, Any]]) -> str:
    lines = [
        "| regime | metric | S7 | random3_same_universe | HS300ETF_BH | S7/random | S7/HS300 | note |",
        "|---|---|---:|---:|---:|---:|---:|---|",
    ]
    for regime in ("bull", "bear", "range", "oos"):
        s7 = summarize_run(all_runs[regime]["s7"])
        rnd = summarize_run(all_runs[regime]["random"])
        hs300 = summarize_run(all_runs[regime]["hs300"])
        for metric, pct in (("return", True), ("max_drawdown", True), ("trades", False), ("fee_ratio", True)):
            r1 = _metric_ratio(s7[metric], rnd[metric])
            r2 = _metric_ratio(s7[metric], hs300[metric])
            fmt = _fmt_pct if pct else _fmt_float
            lines.append(
                f"| {regime} | {metric} | {fmt(s7[metric])} | {fmt(rnd[metric])} | {fmt(hs300[metric])} | "
                f"{_fmt_float(r1)} | {_fmt_float(r2)} | {_ratio_note(r1, r2)} |"
            )
    return "\n".join(lines)


def _delisted_loss_table(runs: dict[str, BacktestRun], delisted_symbols: set[str]) -> str:
    lines = [
        "| regime | delisted_trades | delisted_pnl | delisted_losing_trades | all_pnl | delisted_loss_share_of_abs_losses |",
        "|---|---:|---:|---:|---:|---:|",
    ]
    for name in ("bull", "bear", "range", "oos"):
        trades = [item for item in runs[name].trades if item.symbol in delisted_symbols]
        all_pnl = sum(item.pnl for item in runs[name].trades)
        delisted_pnl = sum(item.pnl for item in trades)
        delisted_losses = abs(sum(item.pnl for item in trades if item.pnl < 0))
        all_losses = abs(sum(item.pnl for item in runs[name].trades if item.pnl < 0))
        ratio = delisted_losses / all_losses if all_losses else 0.0
        lines.append(
            f"| {name} | {len(trades)} | {delisted_pnl:.2f} | {sum(1 for item in trades if item.pnl < 0)} | {all_pnl:.2f} | {_fmt_pct(ratio)} |"
        )
    return "\n".join(lines)


def _sensitivity_table(
    panel: pd.DataFrame,
    data: dict[str, pd.DataFrame],
    calendar_dates: list[date],
    regimes: dict[str, Any],
    cost_config: CostConfig,
) -> str:
    lines = [
        "| prefilter_min_amount | in_sample_trades | avg_return | worst_DD | expectancy | PF | win_rate | limit_up_reject_ratio | forced_hold/sell_orders |",
        "|---:|---:|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for amount in AMOUNT_SENSITIVITY:
        cfg = _strategy_cfg(amount)
        candidates = _build_limit_up_candidates(panel, cfg)
        selected = _selected_table(candidates, int(cfg["max_positions"]))
        lookup = _candidate_lookup(selected)
        strategy = S7LimitUpFollowupStrategy(cfg)
        run_items: list[BacktestRun] = []
        total_stats = ExecutionStats()
        for regime in ("bull", "bear", "range"):
            span = regimes[regime]
            run, stats = _run_daily_followup_backtest(
                f"s7_amount_{amount:g}",
                regime,
                _parse_date(span["start"]),
                _parse_date(span["end"]),
                data,
                lookup,
                calendar_dates,
                strategy.generate_signals,
                cost_config,
            )
            run_items.append(run)
            for key, value in asdict(stats).items():
                setattr(total_stats, key, getattr(total_stats, key) + int(value))
        metrics = _trade_metrics(tuple(trade for run in run_items for trade in run.trades))
        avg_return = float(np.mean([run.total_return for run in run_items]))
        worst_dd = max(run.max_drawdown for run in run_items)
        lines.append(
            f"| {amount:.0f} | {int(metrics['trades'])} | {_fmt_pct(avg_return)} | {_fmt_pct(worst_dd)} | "
            f"{metrics['expectancy']:.2f} | {_fmt_float(metrics['profit_factor'])} | {_fmt_pct(metrics['win_rate'])} | "
            f"{_fmt_pct(total_stats.limit_up_reject_ratio)} | {_fmt_pct(total_stats.forced_hold_sell_order_ratio)} |"
        )
    return "\n".join(lines)


def _data_info(panel: pd.DataFrame, base: pd.DataFrame, candidates: pd.DataFrame, selected: pd.DataFrame, calendar_dates: list[date]) -> S7DataInfo:
    amount = pd.to_numeric(selected.get("amount", pd.Series(dtype="float64")), errors="coerce")
    return S7DataInfo(
        panel_rows=len(panel),
        panel_symbols=int(panel["symbol"].nunique()),
        active_symbols=int((~panel.groupby("symbol")["is_delisted"].max().astype(bool)).sum()),
        delisted_symbols=int(panel.groupby("symbol")["is_delisted"].max().astype(bool).sum()),
        query_start=str(min(panel["date"])) if not panel.empty else "NA",
        query_end=str(max(panel["date"])) if not panel.empty else "NA",
        calendar_dates=len(calendar_dates),
        base_rows=len(base),
        base_dates=int(base["as_of_date"].nunique()) if not base.empty else 0,
        limit_up_rows=len(candidates),
        limit_up_dates=int(candidates["as_of_date"].nunique()) if not candidates.empty else 0,
        selected_rows=len(selected),
        selected_unique_symbols=int(selected["symbol"].nunique()) if not selected.empty else 0,
        selected_delisted_symbols=int(selected[selected["is_delisted"].astype(bool)]["symbol"].nunique()) if not selected.empty else 0,
        selected_st_rows=int(selected["is_st"].astype(bool).sum()) if not selected.empty else 0,
        amount_min=float(amount.min()) if not amount.empty else math.nan,
        amount_median=float(amount.median()) if not amount.empty else math.nan,
        amount_max=float(amount.max()) if not amount.empty else math.nan,
    )


def _data_info_lines(info: S7DataInfo) -> list[str]:
    return [
        f"- panel={info.panel_rows} rows / {info.panel_symbols} symbols；active={info.active_symbols}, delisted={info.delisted_symbols}。",
        f"- panel span={info.query_start}..{info.query_end}；calendar_dates={info.calendar_dates}。",
        f"- base universe rows={info.base_rows}, dates={info.base_dates}；limit_up rows={info.limit_up_rows}, dates={info.limit_up_dates}。",
        f"- selected rows={info.selected_rows}, unique_symbols={info.selected_unique_symbols}, selected_delisted_symbols={info.selected_delisted_symbols}, selected_ST_rows={info.selected_st_rows}。",
        f"- selected D-day amount min/median/max={info.amount_min:.0f}/{info.amount_median:.0f}/{info.amount_max:.0f}。",
        "- PIT assertion: candidates satisfy list_date<=D, delist_date>D or null, non-ST, age_days>=exclude_new_days, D-day amount threshold, and close==limit_up_price.",
    ]


def _s1_comparison_text(s7_runs: dict[str, BacktestRun]) -> str:
    path = REPORT_DIR / "s1_gate1.md"
    if not path.exists():
        return "S1 报告不存在，无法做同口径数字对比。"
    rows = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.startswith("| "):
            continue
        parts = [item.strip() for item in line.strip().strip("|").split("|")]
        if len(parts) >= 9 and parts[0] in {"bull", "bear", "range", "oos"}:
            rows[parts[0]] = parts
    s7_oos = summarize_run(s7_runs["oos"])
    s7_in = _merged_metrics(s7_runs, ("bull", "bear", "range"))
    if "oos" not in rows:
        return "S1 报告未解析到 OOS 表行，无法做完整数字对比。"
    s1_oos = rows["oos"]
    return (
        "同属追涨打板流派：S1 是 14:50 尾盘追强，S7 是涨停后次日开盘接力。"
        f"S1 OOS return={s1_oos[1]}, trades={s1_oos[3]}, expectancy={s1_oos[4]}, PF={s1_oos[5]}, win_rate={s1_oos[6]}；"
        f"S7 OOS return={_fmt_pct(s7_oos['return'])}, trades={int(s7_oos['trades'])}, expectancy={s7_oos['expectancy']:.2f}, "
        f"PF={_fmt_float(s7_oos['profit_factor'])}, win_rate={_fmt_pct(s7_oos['win_rate'])}。"
        f"S7 in-sample merged trades={int(s7_in['trades'])}, expectancy={s7_in['expectancy']:.2f}, PF={_fmt_float(s7_in['profit_factor'])}。"
    )


def render_report(
    all_runs: dict[str, dict[str, Any]],
    data_info: S7DataInfo,
    panel: pd.DataFrame,
    sensitivity: str,
) -> str:
    s7_runs = {name: runs["s7"] for name, runs in all_runs.items()}
    s7_stats = {name: runs["s7_stats"] for name, runs in all_runs.items()}
    checks = _gate_checks(s7_runs, _load_yaml("backtest.yaml")["gate1"])
    final = "PASS" if checks["overall_pass"] else "FAIL"
    cfg = _strategy_cfg()
    amount_threshold = float(cfg["prefilter_min_amount"])
    delisted_symbols = set(panel[panel["is_delisted"].astype(bool)]["symbol"].astype(str).unique().tolist())
    lines = [
        "# S7 Limit-Up Follow-Up Gate1 Report",
        "",
        f"规则：每日 D 收盘后在 PIT 全市场 panel 中筛 D 日 close==limit_up_price、amount>={amount_threshold:.0f}、非 ST、上市>={cfg['exclude_new_days']} 日且未退市股票；按成交额降序取 max_positions={cfg['max_positions']}，D+1 开盘买入，D+2 开盘无条件卖出。",
        "撮合完全复用 constraints.py：一字涨停买单拒绝，一字跌停/停牌卖单拒绝并记录 forced_hold；成本含佣金地板、印花税、过户费、滑点。",
        "",
        "## 数据与 universe",
        *_data_info_lines(data_info),
        "",
        "## S7 分段关键指标",
        _summary_table(s7_runs, s7_stats),
        "",
        "## 一字涨停/forced_hold 真实成交约束",
        _execution_reality_table(s7_stats),
        "",
        "## in-sample vs OOS 差异",
        _in_oos_table(s7_runs),
        "",
        "## 对照组 ratio 表",
        _comparison_table(all_runs),
        "",
        "## 反假设列表",
        "- 一字涨停买不进：上表 buy_rejected_limit_up 明确把未成交买单计入分母；如果只统计成功买入后的收益，会系统性高估打板成功率。",
        "- 退市股亏损贡献：",
        _delisted_loss_table(s7_runs, delisted_symbols),
        "- 成交额 5亿过滤是否过松：以下敏感性仅使用 bull/bear/range in-sample，未触碰 OOS，不用于改参数。",
        sensitivity,
        "- 牛市 beta/追涨共振：对照组加入同 universe 随机 3 只和 510300 买入持有；若 S7 不优于随机或只在 bull 好，不能声称有独立 alpha。",
        "",
        "## 与 S1 杨永兴法对比",
        _s1_comparison_text(s7_runs),
        "",
        "## flag/参数调查记录",
        "- 未调参、未碰 OOS。",
        "- 默认 prefilter_min_amount 固定为 strategy_addon.yaml 的 5e8；敏感性只展示 in-sample。",
        "- 未修改成本、滑点、regime 或 Gate1 阈值。",
        "- 未静默忽略一字涨停买不进或跌停/停牌卖不掉；拒单与 forced_hold 均在报告披露。",
        "",
        "## Gate1 判定表",
        _gate1_table(checks, _load_yaml("backtest.yaml")["gate1"]),
        "",
        f"最终判定：{final}",
    ]
    return "\n".join(lines) + "\n"


def run(refresh: bool = False, workers: int = DEFAULT_WORKERS) -> dict[str, Any]:
    cfg = _strategy_cfg()
    regimes = _load_yaml("backtest.yaml")["regimes"]
    global_start = min(_parse_date(span["start"]) for span in regimes.values())
    global_end = max(_parse_date(span["end"]) for span in regimes.values())
    raw_panel, _s2_info = _load_or_build_panel(global_start, global_end, DEFAULT_TOP_N, workers, refresh)
    panel = _prepare_panel(raw_panel)
    calendar_dates = sorted(panel["date"].dropna().unique().tolist())
    base = _base_universe(panel, cfg)
    candidates = _build_limit_up_candidates(panel, cfg)
    selected = _selected_table(candidates, int(cfg["max_positions"]))
    candidates_by_date = _candidate_lookup(selected)
    random_by_date = _candidate_lookup(base)
    data = _data_by_symbol(panel)
    cost_config = CostConfig.from_mapping(_load_yaml("cost.yaml"))
    strategy = S7LimitUpFollowupStrategy(cfg)
    etf = _load_etf_data(global_start, global_end)

    all_runs: dict[str, dict[str, Any]] = {}
    for regime, span in regimes.items():
        start = _parse_date(span["start"])
        end = _parse_date(span["end"])
        s7_run, s7_stats = _run_daily_followup_backtest(
            "s7",
            regime,
            start,
            end,
            data,
            candidates_by_date,
            calendar_dates,
            strategy.generate_signals,
            cost_config,
        )
        random_run, random_stats = _run_daily_followup_backtest(
            "s7_random3_same_universe",
            regime,
            start,
            end,
            data,
            random_by_date,
            calendar_dates,
            _random_followup_signal(int(cfg["max_positions"])),
            cost_config,
        )
        all_runs[regime] = {
            "s7": s7_run,
            "s7_stats": s7_stats,
            "random": random_run,
            "random_stats": random_stats,
            "hs300": _run_etf_buy_hold("510300", regime, start, end, etf, cost_config),
        }

    sensitivity = _sensitivity_table(panel, data, calendar_dates, regimes, cost_config)
    data_info = _data_info(panel, base, candidates, selected, calendar_dates)
    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    path = REPORT_DIR / "s7_limit_up_gate1.md"
    path.write_text(render_report(all_runs, data_info, panel, sensitivity), encoding="utf-8")
    return {"path": path, "runs": all_runs, "data_info": data_info, "sensitivity": sensitivity}


def main() -> None:
    parser = argparse.ArgumentParser(description="Run S7 limit-up follow-up Gate1")
    parser.add_argument("--refresh", action="store_true")
    parser.add_argument("--workers", type=int, default=DEFAULT_WORKERS)
    args = parser.parse_args()
    result = run(refresh=args.refresh, workers=args.workers)
    s7_runs = {regime: runs["s7"] for regime, runs in result["runs"].items()}
    s7_stats = {regime: runs["s7_stats"] for regime, runs in result["runs"].items()}
    checks = _gate_checks(s7_runs, _load_yaml("backtest.yaml")["gate1"])
    trades = int(sum(len(run.trades) for run in s7_runs.values()))
    total_stats = ExecutionStats()
    for item in s7_stats.values():
        for key, value in asdict(item).items():
            setattr(total_stats, key, getattr(total_stats, key) + int(value))
    print(f"wrote {result['path']}")
    print(f"S7 trades={trades} final={'PASS' if checks['overall_pass'] else 'FAIL'}")
    print(_summary_table(s7_runs, s7_stats))
    print(_execution_reality_table(s7_stats))
    print(_in_oos_table(s7_runs))
    print(_gate1_table(checks, _load_yaml("backtest.yaml")["gate1"]))
    print("data_info=" + str(asdict(result["data_info"])))
    print(
        f"total_limit_up_reject_ratio={total_stats.limit_up_reject_ratio:.4%} "
        f"total_forced_hold_ratio={total_stats.forced_hold_sell_order_ratio:.4%}"
    )


if __name__ == "__main__":
    main()
