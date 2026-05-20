"""S10 pairs trading Gate1 runner using the cached S2 PIT panel."""

from __future__ import annotations

import argparse
import math
from bisect import bisect_right
from dataclasses import dataclass
from datetime import date
from itertools import combinations
from pathlib import Path
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
    _position_for_symbol,
    _record_execution,
    _ratio_note,
    _trade_metrics,
    summarize_run,
)
from strategies.s10_pairs_trading import S10PairsTradingStrategy


PROJECT_ROOT = Path(__file__).resolve().parents[1]
PANEL_CACHE = PROJECT_ROOT / "data/cache/s2_panel_v2pit_2019-10-01_2026-05-15.parquet"
PAIR_SAMPLE_SIZE = 1000
UNIVERSE_TOP_N = 300
MC_PVALUE_SIMS = 5000
MIN_SPREAD_STD = 1.0e-8

SignalFunc = Callable[[date, dict[str, Any]], list[Order]]
_NULL_STATS_CACHE: dict[tuple[int, int], np.ndarray] = {}


@dataclass(frozen=True)
class S10DataInfo:
    panel_path: str
    panel_rows: int
    panel_symbols: int
    delisted_symbols: int
    panel_start: str
    panel_end: str
    universe_dates: int
    universe_min: int
    universe_median: float
    universe_max: int
    formation_count: int
    selected_pairs: int
    selected_unique_pairs: int
    selected_unique_symbols: int
    pair_sample_size: int
    monte_carlo_sims: int
    eg_critical_5pct: float
    scope_note: str


@dataclass(frozen=True)
class FormationInfo:
    formation_date: date
    trading_end_date: date
    universe_size: int
    valid_price_symbols: int
    sampled_pairs: int
    coint_pairs: int
    selected_pairs: int
    min_pvalue: float
    max_abs_tstat: float


def _strategy_cfg() -> dict[str, Any]:
    return _load_yaml("strategy_addon.yaml")["s10_pairs_trading"]


def _regimes() -> dict[str, Any]:
    return _load_yaml("backtest.yaml")["regimes"]


def _load_panel_cache() -> pd.DataFrame:
    if not PANEL_CACHE.exists():
        raise FileNotFoundError(f"S10 requires existing S2 PIT panel cache: {PANEL_CACHE}")
    columns = [
        "symbol",
        "date",
        "open",
        "high",
        "low",
        "close",
        "amount",
        "turnover",
        "float_mv",
        "is_st",
        "is_suspended",
        "is_delisted",
        "limit_up_price",
        "limit_down_price",
        "list_date",
        "delist_date",
    ]
    return pd.read_parquet(PANEL_CACHE, columns=columns)


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
    df["amount_20"] = grouped["amount"].transform(lambda item: item.rolling(20, min_periods=20).mean())

    min_list_days = int(_load_yaml("backtest.yaml")["universe"].get("min_list_days", 60))
    row_dates = pd.to_datetime(df["date"], errors="coerce")
    list_dates = pd.to_datetime(df["list_date"], errors="coerce")
    delist_dates = pd.to_datetime(df["delist_date"], errors="coerce")
    eligible = (
        list_dates.notna()
        & (list_dates <= row_dates)
        & (delist_dates.isna() | (delist_dates > row_dates))
        & (pd.to_numeric(df["age_days"], errors="coerce") >= min_list_days)
        & (~df["is_suspended"].astype(bool))
        & (pd.to_numeric(df["close"], errors="coerce") > 0)
        & np.isfinite(pd.to_numeric(df["amount_20"], errors="coerce"))
        & (pd.to_numeric(df["amount_20"], errors="coerce") > 0)
    )
    df["s10_eligible"] = eligible
    _assert_panel_pit(df[df["s10_eligible"]])
    return df


def _assert_panel_pit(rows: pd.DataFrame) -> None:
    if rows.empty:
        return
    row_dates = pd.to_datetime(rows["date"], errors="coerce")
    list_dates = pd.to_datetime(rows["list_date"], errors="coerce")
    delist_dates = pd.to_datetime(rows["delist_date"], errors="coerce")
    assert list_dates.notna().all(), "S10 eligible row missing list_date"
    assert (list_dates <= row_dates).all(), "S10 eligible universe includes pre-listing rows"
    assert (delist_dates.isna() | (delist_dates > row_dates)).all(), "S10 eligible universe includes delisted rows"


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


def _build_monthly_universe(panel: pd.DataFrame, month_ends: set[date]) -> pd.DataFrame:
    rows = panel[panel["date"].isin(month_ends) & panel["s10_eligible"]].copy()
    if rows.empty:
        return rows
    rows["liquidity_rank"] = rows.groupby("date")["amount_20"].rank(method="first", ascending=False)
    out = rows[rows["liquidity_rank"] <= UNIVERSE_TOP_N].copy()
    out["as_of_date"] = out["date"]
    return out.sort_values(["as_of_date", "liquidity_rank", "symbol"]).reset_index(drop=True)


def _universe_lookup(universe: pd.DataFrame) -> dict[date, pd.DataFrame]:
    out: dict[date, pd.DataFrame] = {}
    if universe.empty:
        return out
    rows = universe.copy()
    rows["as_of_date"] = pd.to_datetime(rows["as_of_date"], errors="coerce").dt.date
    for as_of_date, frame in rows.groupby("as_of_date", sort=True):
        out[as_of_date] = frame.copy()
    return out


def _close_wide(panel: pd.DataFrame) -> pd.DataFrame:
    wide = panel.pivot(index="date", columns="symbol", values="close").sort_index()
    wide.index = pd.to_datetime(wide.index, errors="coerce").date
    return wide


def _window_dates(calendar_dates: list[date], as_of_date: date, window: int) -> list[date]:
    index = bisect_right(calendar_dates, as_of_date) - 1
    if index + 1 < window:
        return []
    return calendar_dates[index - window + 1 : index + 1]


def _first_month_end_on_or_after(value: date, month_ends: set[date]) -> date | None:
    for item in sorted(month_ends):
        if item >= value:
            return item
    return None


def _formation_dates(
    calendar_dates: list[date],
    month_ends: set[date],
    global_start: date,
    global_end: date,
    cfg: dict[str, Any],
) -> list[date]:
    formation_window = int(cfg["formation_window_days"])
    trading_window = int(cfg["trading_window_days"])
    out: list[date] = []
    next_allowed = global_start
    for month_end in sorted(month_ends):
        if month_end < next_allowed or month_end > global_end:
            continue
        if len(_window_dates(calendar_dates, month_end, formation_window)) < formation_window:
            continue
        out.append(month_end)
        index = bisect_right(calendar_dates, month_end) - 1
        if index < 0:
            continue
        next_index = min(index + trading_window, len(calendar_dates) - 1)
        next_month_end = _first_month_end_on_or_after(calendar_dates[next_index], month_ends)
        if next_month_end is None:
            break
        next_allowed = next_month_end
    return out


def _ols_residual(log_a: np.ndarray, log_b: np.ndarray) -> tuple[float, float, np.ndarray] | None:
    x = np.asarray(log_b, dtype="float64")
    y = np.asarray(log_a, dtype="float64")
    if len(x) != len(y) or len(x) < 30 or not np.isfinite(x).all() or not np.isfinite(y).all():
        return None
    x_centered = x - x.mean()
    y_centered = y - y.mean()
    denom = float(np.dot(x_centered, x_centered))
    if denom <= 0 or not np.isfinite(denom):
        return None
    beta = float(np.dot(x_centered, y_centered) / denom)
    alpha = float(y.mean() - beta * x.mean())
    residual = y - alpha - beta * x
    return alpha, beta, residual


def _adf_tstat_no_lag(residual: np.ndarray) -> float:
    lagged = residual[:-1]
    delta = np.diff(residual)
    denom = float(np.dot(lagged, lagged))
    if denom <= 0 or not np.isfinite(denom):
        return math.nan
    gamma = float(np.dot(lagged, delta) / denom)
    errors = delta - gamma * lagged
    dof = max(len(delta) - 1, 1)
    sigma2 = float(np.dot(errors, errors) / dof)
    if sigma2 <= 0 or not np.isfinite(sigma2):
        return math.nan
    se = math.sqrt(sigma2 / denom)
    if se <= 0 or not np.isfinite(se):
        return math.nan
    return gamma / se


def _null_stats(nobs: int, sims: int = MC_PVALUE_SIMS) -> np.ndarray:
    key = (nobs, sims)
    cached = _NULL_STATS_CACHE.get(key)
    if cached is not None:
        return cached
    rng = np.random.default_rng(RANDOM_SEED + 1009 + nobs + sims)
    x = rng.standard_normal((sims, nobs)).cumsum(axis=1)
    y = rng.standard_normal((sims, nobs)).cumsum(axis=1)
    x_centered = x - x.mean(axis=1, keepdims=True)
    y_centered = y - y.mean(axis=1, keepdims=True)
    denom = np.sum(x_centered * x_centered, axis=1)
    beta = np.sum(x_centered * y_centered, axis=1) / denom
    alpha = y.mean(axis=1) - beta * x.mean(axis=1)
    residual = y - alpha[:, None] - beta[:, None] * x
    lagged = residual[:, :-1]
    delta = np.diff(residual, axis=1)
    den = np.sum(lagged * lagged, axis=1)
    gamma = np.sum(lagged * delta, axis=1) / den
    errors = delta - gamma[:, None] * lagged
    dof = max(delta.shape[1] - 1, 1)
    sigma2 = np.sum(errors * errors, axis=1) / dof
    se = np.sqrt(sigma2 / den)
    stats = gamma / se
    stats = stats[np.isfinite(stats)]
    _NULL_STATS_CACHE[key] = np.sort(stats)
    return _NULL_STATS_CACHE[key]


def _eg_test(log_a: np.ndarray, log_b: np.ndarray, null_distribution: np.ndarray) -> dict[str, float] | None:
    fit = _ols_residual(log_a, log_b)
    if fit is None:
        return None
    alpha, beta, residual = fit
    spread_std = float(np.std(residual, ddof=1))
    if not np.isfinite(spread_std) or spread_std <= MIN_SPREAD_STD:
        return None
    t_stat = _adf_tstat_no_lag(residual)
    if not np.isfinite(t_stat):
        return None
    p_value = float((np.searchsorted(null_distribution, t_stat, side="right") + 1) / (len(null_distribution) + 1))
    return {
        "alpha": alpha,
        "beta": beta,
        "spread_mean": float(np.mean(residual)),
        "spread_std": spread_std,
        "t_stat": float(t_stat),
        "p_value": p_value,
    }


def _select_pairs_for_date(
    formation_date: date,
    calendar_dates: list[date],
    universe_by_date: dict[date, pd.DataFrame],
    close_wide: pd.DataFrame,
    cfg: dict[str, Any],
    null_distribution: np.ndarray,
) -> tuple[list[dict[str, Any]], FormationInfo]:
    universe_rows = universe_by_date.get(formation_date, pd.DataFrame())
    universe_symbols = universe_rows["symbol"].astype(str).tolist() if not universe_rows.empty else []
    window = _window_dates(calendar_dates, formation_date, int(cfg["formation_window_days"]))
    trading_end_index = min((bisect_right(calendar_dates, formation_date) - 1) + int(cfg["trading_window_days"]), len(calendar_dates) - 1)
    trading_end_date = calendar_dates[trading_end_index]
    if not universe_symbols or len(window) < int(cfg["formation_window_days"]):
        return [], FormationInfo(formation_date, trading_end_date, len(universe_symbols), 0, 0, 0, 0, math.nan, math.nan)
    assert max(window) <= formation_date, f"S10 formation window leaks future data: {max(window)} > {formation_date}"

    prices = close_wide.loc[window, [symbol for symbol in universe_symbols if symbol in close_wide.columns]]
    prices = prices.replace([np.inf, -np.inf], np.nan)
    prices = prices.dropna(axis=1, how="any")
    prices = prices.loc[:, (prices > 0).all(axis=0)]
    symbols = prices.columns.astype(str).tolist()
    if len(symbols) < 2:
        return [], FormationInfo(formation_date, trading_end_date, len(universe_symbols), len(symbols), 0, 0, 0, math.nan, math.nan)

    combos = list(combinations(symbols, 2))
    rng = np.random.default_rng(RANDOM_SEED + formation_date.toordinal())
    sample_count = min(PAIR_SAMPLE_SIZE, len(combos))
    sampled_idx = rng.choice(len(combos), size=sample_count, replace=False)
    log_prices = np.log(prices)
    candidates: list[dict[str, Any]] = []
    for idx in sampled_idx:
        symbol_a, symbol_b = combos[int(idx)]
        result = _eg_test(log_prices[symbol_a].to_numpy(), log_prices[symbol_b].to_numpy(), null_distribution)
        if result is None or result["p_value"] >= 0.05:
            continue
        candidates.append(
            {
                "formation_date": formation_date,
                "start_signal_date": formation_date,
                "end_signal_date": trading_end_date,
                "symbol_a": symbol_a,
                "symbol_b": symbol_b,
                **result,
            }
        )

    selected = sorted(candidates, key=lambda item: abs(float(item["t_stat"])), reverse=True)[: int(cfg["num_pairs"])]
    for rank, item in enumerate(selected, start=1):
        item["pair_rank"] = rank
        item["pair_id"] = f"{formation_date:%Y%m%d}_{rank}_{item['symbol_a']}_{item['symbol_b']}"

    min_p = min((float(item["p_value"]) for item in candidates), default=math.nan)
    max_abs_t = max((abs(float(item["t_stat"])) for item in candidates), default=math.nan)
    info = FormationInfo(
        formation_date=formation_date,
        trading_end_date=trading_end_date,
        universe_size=len(universe_symbols),
        valid_price_symbols=len(symbols),
        sampled_pairs=sample_count,
        coint_pairs=len(candidates),
        selected_pairs=len(selected),
        min_pvalue=min_p,
        max_abs_tstat=max_abs_t,
    )
    return selected, info


def _build_pair_table(
    calendar_dates: list[date],
    month_ends: set[date],
    universe_by_date: dict[date, pd.DataFrame],
    close_wide: pd.DataFrame,
    cfg: dict[str, Any],
) -> tuple[pd.DataFrame, list[FormationInfo], float]:
    regimes = _regimes()
    global_start = min(_parse_date(span["start"]) for span in regimes.values())
    global_end = max(_parse_date(span["end"]) for span in regimes.values())
    formation_dates = _formation_dates(calendar_dates, month_ends, global_start, global_end, cfg)
    null_distribution = _null_stats(int(cfg["formation_window_days"]), MC_PVALUE_SIMS)
    rows: list[dict[str, Any]] = []
    infos: list[FormationInfo] = []
    for formation_date in formation_dates:
        selected, info = _select_pairs_for_date(
            formation_date,
            calendar_dates,
            universe_by_date,
            close_wide,
            cfg,
            null_distribution,
        )
        rows.extend(selected)
        infos.append(info)
        print(
            f"s10_formation {formation_date} universe={info.universe_size} valid={info.valid_price_symbols} "
            f"sampled={info.sampled_pairs} coint={info.coint_pairs} selected={info.selected_pairs}",
            flush=True,
        )
    pair_table = pd.DataFrame(rows)
    if not pair_table.empty:
        for col in ("formation_date", "start_signal_date", "end_signal_date"):
            pair_table[col] = pd.to_datetime(pair_table[col], errors="coerce").dt.date
        pair_table = pair_table.sort_values(["formation_date", "pair_rank", "pair_id"]).reset_index(drop=True)
    return pair_table, infos, float(np.quantile(null_distribution, 0.05))


def _pair_signals_for_date(
    signal_date: date,
    pair_table: pd.DataFrame,
    close_wide: pd.DataFrame,
    pair_states: dict[str, str | None],
) -> tuple[pd.DataFrame, dict[str, float]]:
    if pair_table.empty:
        return pd.DataFrame(columns=["date"]), {}
    rows: list[dict[str, Any]] = []
    prices: dict[str, float] = {}
    day_prices = close_wide.loc[signal_date] if signal_date in close_wide.index else pd.Series(dtype="float64")

    for pair in pair_table.itertuples(index=False):
        pair_id = str(pair.pair_id)
        start_signal_date = pair.start_signal_date
        end_signal_date = pair.end_signal_date
        active = start_signal_date <= signal_date <= end_signal_date
        expired_holding = signal_date > end_signal_date and pair_states.get(pair_id) is not None
        if not active and not expired_holding:
            continue

        symbol_a = str(pair.symbol_a)
        symbol_b = str(pair.symbol_b)
        price_a = _series_price(day_prices, symbol_a)
        price_b = _series_price(day_prices, symbol_b)
        zscore = math.nan
        if active and price_a is not None and price_b is not None:
            spread = math.log(price_a) - (float(pair.alpha) + float(pair.beta) * math.log(price_b))
            zscore = (spread - float(pair.spread_mean)) / float(pair.spread_std)
            prices[symbol_a] = price_a
            prices[symbol_b] = price_b

        rows.append(
            {
                "date": signal_date,
                "pair_id": pair_id,
                "formation_date": pair.formation_date,
                "pair_rank": int(pair.pair_rank),
                "symbol_a": symbol_a,
                "symbol_b": symbol_b,
                "zscore": zscore,
                "expired": expired_holding,
            }
        )
    return pd.DataFrame(rows), prices


def _series_price(series: pd.Series, symbol: str) -> float | None:
    if symbol not in series.index:
        return None
    value = series.loc[symbol]
    if pd.isna(value):
        return None
    price = float(value)
    if not np.isfinite(price) or price <= 0:
        return None
    return price


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
    return cash, positions


def _position_exposure(cash: float, positions: tuple[Position, ...], data: dict[str, pd.DataFrame], as_of_date: date) -> tuple[float, float]:
    nav = _mark_nav(cash, positions, data, as_of_date)
    if nav <= 0:
        return 0.0, 0.0
    long_value = 0.0
    for position in positions:
        close = _last_close(data, position.symbol, as_of_date)
        if close is not None:
            long_value += position.quantity * close
    return long_value / nav, nav


def _run_pairs_backtest(
    name: str,
    regime: str,
    start: date,
    end: date,
    data: dict[str, pd.DataFrame],
    close_wide: pd.DataFrame,
    pair_table: pd.DataFrame,
    calendar_dates: list[date],
    strategy: S10PairsTradingStrategy,
    cost_config: CostConfig,
) -> BacktestRun:
    dates = _effective_dates(start, end, calendar_dates)
    if len(dates) < 2:
        raise RuntimeError(f"Not enough S10 dates for {regime}")
    cash = INITIAL_CASH
    positions: tuple[Position, ...] = ()
    basis: dict[str, float] = {}
    trades: list[TradeRecord] = []
    filled_orders: list[dict[str, Any]] = []
    rejected_orders: list[dict[str, Any]] = []
    events: list[dict[str, Any]] = []
    pair_states: dict[str, str | None] = {}

    gross_exposure, first_nav = _position_exposure(cash, positions, data, dates[0])
    nav_rows = [{"date": dates[0].isoformat(), "nav": first_nav, "gross_long_exposure": gross_exposure, "active_pairs": 0}]
    for signal_date, trade_date in zip(dates[:-1], dates[1:], strict=False):
        positions = mark_sellable(positions, trade_date)
        pair_signals, prices = _pair_signals_for_date(signal_date, pair_table, close_wide, pair_states)
        for position in positions:
            close = _last_close(data, position.symbol, signal_date)
            if close is not None:
                prices.setdefault(position.symbol, close)
        pair_events: list[dict[str, Any]] = []
        ctx = {
            "data": {"pair_signals": pair_signals[["date"]].copy() if "date" in pair_signals.columns else pd.DataFrame({"date": [signal_date]})},
            "positions": positions,
            "cash": cash,
            "nav": _mark_nav(cash, positions, data, signal_date),
            "lot_size": LOT_SIZE,
            "pair_signals": pair_signals,
            "pair_states": pair_states,
            "pair_events": pair_events,
            "prices": prices,
        }
        orders = strategy.generate_signals(signal_date, ctx)
        events.extend(pair_events)
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
        exposure, nav = _position_exposure(cash, positions, data, trade_date)
        nav_rows.append(
            {
                "date": trade_date.isoformat(),
                "nav": nav,
                "gross_long_exposure": exposure,
                "active_pairs": sum(1 for value in pair_states.values() if value is not None),
            }
        )

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
    if nav_rows:
        exposure, _ = _position_exposure(cash, positions, data, final_date)
        nav_rows[-1] = {
            "date": final_date.isoformat(),
            "nav": final_nav,
            "gross_long_exposure": exposure,
            "active_pairs": sum(1 for value in pair_states.values() if value is not None),
        }
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


def _orders_for_rows(selected: pd.DataFrame, as_of_date: date, ctx: dict[str, Any]) -> list[Order]:
    positions: tuple[Position, ...] = tuple(ctx.get("positions", ()))
    current_qty = {item.symbol: item.quantity for item in positions if item.quantity > 0}
    selected_symbols = set(selected["symbol"].astype(str)) if not selected.empty else set()
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


def _random10_monthly_signal(as_of_date: date, ctx: dict[str, Any]) -> list[Order]:
    candidates = ctx.get("candidates")
    if not isinstance(candidates, pd.DataFrame) or candidates.empty:
        return _orders_for_rows(pd.DataFrame(columns=["symbol", "close"]), as_of_date, ctx)
    rows = candidates.copy()
    size = min(10, len(rows))
    rng = np.random.default_rng(RANDOM_SEED + as_of_date.toordinal() + 10)
    chosen = set(rng.choice(rows["symbol"].astype(str).to_numpy(), size=size, replace=False).tolist())
    return _orders_for_rows(rows[rows["symbol"].astype(str).isin(chosen)].copy(), as_of_date, ctx)


def _universe_equal_monthly_signal(as_of_date: date, ctx: dict[str, Any]) -> list[Order]:
    candidates = ctx.get("candidates")
    if not isinstance(candidates, pd.DataFrame) or candidates.empty:
        return _orders_for_rows(pd.DataFrame(columns=["symbol", "close"]), as_of_date, ctx)
    return _orders_for_rows(candidates.copy(), as_of_date, ctx)


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
        raise RuntimeError(f"Not enough S10 control dates for {regime}")
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
            "positions": positions,
            "cash": cash,
            "nav": _mark_nav(cash, positions, data, signal_date),
            "lot_size": LOT_SIZE,
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
            cash, positions = _execute_orders(orders, trade_date, cash, positions, basis, trades, filled_orders, rejected_orders, events, data, cost_config)
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


def _load_cached_510300(start: date, end: date) -> pd.DataFrame:
    candidates = sorted((PROJECT_ROOT / "data/cache").glob("etf_daily__510300__*.parquet"), key=lambda path: path.stat().st_mtime, reverse=True)
    for path in candidates:
        frame = pd.read_parquet(path)
        if frame.empty or "date" not in frame.columns:
            continue
        out = frame.copy()
        out["date"] = pd.to_datetime(out["date"], errors="coerce").dt.date
        out = out.dropna(subset=["date"]).sort_values("date").reset_index(drop=True)
        if min(out["date"]) <= start and max(out["date"]) >= end:
            for col in ("open", "high", "low", "close", "amount", "limit_up_price", "limit_down_price"):
                if col in out.columns:
                    out[col] = pd.to_numeric(out[col], errors="coerce")
            if "is_suspended" not in out.columns:
                out["is_suspended"] = False
            return out
    raise FileNotFoundError("No cached 510300 ETF parquet covers the S10 Gate1 regimes")


def _run_etf_buy_hold(regime: str, start: date, end: date, frame: pd.DataFrame, cost_config: CostConfig) -> BacktestRun:
    symbol = "510300"
    data = {symbol: frame}
    dates = [item for item in frame["date"].tolist() if start <= item <= end]
    if len(dates) < 2:
        raise RuntimeError(f"Not enough cached 510300 ETF dates for {regime}")
    first = dates[0]
    cash = INITIAL_CASH
    positions: tuple[Position, ...] = ()
    basis: dict[str, float] = {}
    trades: list[TradeRecord] = []
    filled_orders: list[dict[str, Any]] = []
    rejected_orders: list[dict[str, Any]] = []
    events: list[dict[str, Any]] = []

    bar = _market_bar(data, symbol, first)
    desired = int((cash / max(bar.open, 1.0e-9)) // LOT_SIZE * LOT_SIZE)
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
    for position in sorted(positions, key=lambda item: item.symbol):
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
        name="hs300_etf_buy_hold",
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


def _data_by_symbol(panel: pd.DataFrame) -> dict[str, pd.DataFrame]:
    out = {}
    for symbol, frame in panel.groupby("symbol", sort=False):
        item = frame.copy()
        item["date"] = pd.to_datetime(item["date"], errors="coerce").dt.date
        out[str(symbol)] = item.sort_values("date").reset_index(drop=True)
    return out


def _run_summary_table(runs: dict[str, BacktestRun]) -> str:
    lines = [
        "| regime | return | max_drawdown | trades | expectancy_after_cost | profit_factor | win_rate | fee_ratio | forced_hold | filled_orders | avg_long_exposure |",
        "|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for name in ("bull", "bear", "range", "oos"):
        metrics = summarize_run(runs[name])
        forced = sum(1 for item in runs[name].events if item.get("type") == "forced_hold")
        exposure = float(pd.to_numeric(runs[name].nav_curve.get("gross_long_exposure", pd.Series(dtype="float64")), errors="coerce").mean())
        lines.append(
            f"| {name} | {_fmt_pct(metrics['return'])} | {_fmt_pct(metrics['max_drawdown'])} | {int(metrics['trades'])} | "
            f"{metrics['expectancy']:.2f} | {_fmt_float(metrics['profit_factor'])} | {_fmt_pct(metrics['win_rate'])} | "
            f"{_fmt_pct(metrics['fee_ratio'])} | {forced} | {len(runs[name].filled_orders)} | {_fmt_pct(exposure)} |"
        )
    return "\n".join(lines)


def _comparison_table(all_runs: dict[str, dict[str, BacktestRun]]) -> str:
    lines = [
        "| regime | metric | S10 | HS300ETF_BH | random10_monthly | universe_equal | S10/HS300 | S10/random10 | S10/universe | note |",
        "|---|---|---:|---:|---:|---:|---:|---:|---:|---|",
    ]
    for regime in ("bull", "bear", "range", "oos"):
        s10 = summarize_run(all_runs[regime]["s10"])
        hs300 = summarize_run(all_runs[regime]["hs300_etf"])
        random10 = summarize_run(all_runs[regime]["random10"])
        universe = summarize_run(all_runs[regime]["universe_equal"])
        for metric, pct in (("return", True), ("max_drawdown", True), ("trades", False), ("fee_ratio", True)):
            r1 = _metric_ratio(s10[metric], hs300[metric])
            r2 = _metric_ratio(s10[metric], random10[metric])
            r3 = _metric_ratio(s10[metric], universe[metric])
            fmt = _fmt_pct if pct else _fmt_float
            lines.append(
                f"| {regime} | {metric} | {fmt(s10[metric])} | {fmt(hs300[metric])} | {fmt(random10[metric])} | "
                f"{fmt(universe[metric])} | {_fmt_float(r1)} | {_fmt_float(r2)} | {_fmt_float(r3)} | {_ratio_note(r1, r2, r3)} |"
            )
    return "\n".join(lines)


def _in_oos_table(runs: dict[str, BacktestRun]) -> str:
    in_trades = tuple(item for name in ("bull", "bear", "range") for item in runs[name].trades)
    in_metrics = _trade_metrics(in_trades)
    oos = summarize_run(runs["oos"])
    avg_in = float(np.mean([runs[name].total_return for name in ("bull", "bear", "range")]))
    worst_dd = max(runs[name].max_drawdown for name in ("bull", "bear", "range"))
    return "\n".join(
        [
            "| span | avg/period_return | trades | expectancy | profit_factor | win_rate | max_drawdown_worst |",
            "|---|---:|---:|---:|---:|---:|---:|",
            f"| in_sample(bull+bear+range) | {_fmt_pct(avg_in)} | {int(in_metrics['trades'])} | {in_metrics['expectancy']:.2f} | {_fmt_float(in_metrics['profit_factor'])} | {_fmt_pct(in_metrics['win_rate'])} | {_fmt_pct(worst_dd)} |",
            f"| oos | {_fmt_pct(oos['return'])} | {int(oos['trades'])} | {oos['expectancy']:.2f} | {_fmt_float(oos['profit_factor'])} | {_fmt_pct(oos['win_rate'])} | {_fmt_pct(oos['max_drawdown'])} |",
        ]
    )


def _formation_table(infos: list[FormationInfo]) -> str:
    lines = [
        "| formation_date | trading_end | universe | valid_price_symbols | sampled_pairs | coint_p_lt_0_05 | selected | min_p | max_abs_t |",
        "|---|---:|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for item in infos:
        lines.append(
            f"| {item.formation_date} | {item.trading_end_date} | {item.universe_size} | {item.valid_price_symbols} | "
            f"{item.sampled_pairs} | {item.coint_pairs} | {item.selected_pairs} | {_fmt_float(item.min_pvalue)} | {_fmt_float(item.max_abs_tstat)} |"
        )
    return "\n".join(lines)


def _abc_table(checks: dict[str, Any]) -> str:
    return "\n".join(
        [
            "| gate | result | note |",
            "|---|---|---|",
            f"| A | {'PASS' if checks['a_pass'] else 'FAIL'} | bull/bear/range each require expectancy>0, PF>=1.3, DD<=20% |",
            f"| B | {'PASS' if checks['b_pass'] else 'FAIL'} | merged trades>=200 plus expectancy/PF thresholds |",
            f"| C | {'PASS' if checks['c_pass'] else 'FAIL'} | OOS expectancy/PF/DD/trades>=60 |",
            f"| TOTAL | {'PASS' if checks['overall_pass'] else 'FAIL'} | A+B+C simultaneous |",
        ]
    )


def _previous_strategy_table(s10_final: str, s10_oos_pass: bool) -> str:
    rows = [
        ("S1", "tail", "FAIL"),
        ("S2", "multi_factor", "FAIL"),
        ("S3", "momentum", "FAIL"),
        ("S4", "erba_rotation", "FAIL"),
        ("S5", "small_cap", "FAIL"),
        ("S6", "dual_ma", "FAIL"),
        ("S7", "limit_up_followup", "FAIL"),
        ("S8", "rsi_reversal", "FAIL"),
        ("S9", "risk_parity", "FAIL"),
        ("S10", "pairs_trading", s10_final),
    ]
    lines = ["| strategy | name | Gate1 final |", "|---|---|---|"]
    lines.extend(f"| {sid} | {name} | {final} |" for sid, name, final in rows)
    candidate = "YES" if s10_oos_pass else "NO"
    lines.append(f"| note | S10 是第二个 OOS PASS 候选? | {candidate} |")
    return "\n".join(lines)


def _stop_stats(runs: dict[str, BacktestRun]) -> dict[str, float]:
    all_events = [event for run in runs.values() for event in run.events]
    stop_events = [event for event in all_events if event.get("type") == "stop_z"]
    entry_events = [event for event in all_events if str(event.get("type", "")).startswith("entry_")]
    trades = sum(len(run.trades) for run in runs.values())
    return {
        "stop_events": float(len(stop_events)),
        "entry_events": float(len(entry_events)),
        "trades": float(trades),
        "stop_per_trade": len(stop_events) / trades if trades else 0.0,
        "stop_per_entry": len(stop_events) / len(entry_events) if entry_events else 0.0,
    }


def _neutrality_stats(runs: dict[str, BacktestRun], hs300_runs: dict[str, BacktestRun]) -> dict[str, dict[str, float]]:
    out: dict[str, dict[str, float]] = {}
    for regime in ("bull", "bear", "range", "oos"):
        s10_curve = runs[regime].nav_curve.copy()
        hs_curve = hs300_runs[regime].nav_curve.copy()
        s10_curve["date"] = pd.to_datetime(s10_curve["date"], errors="coerce")
        hs_curve["date"] = pd.to_datetime(hs_curve["date"], errors="coerce")
        merged = s10_curve[["date", "nav", "gross_long_exposure"]].merge(hs_curve[["date", "nav"]], on="date", suffixes=("_s10", "_hs300"))
        s10_ret = pd.to_numeric(merged["nav_s10"], errors="coerce").pct_change()
        hs_ret = pd.to_numeric(merged["nav_hs300"], errors="coerce").pct_change()
        valid = pd.DataFrame({"s10": s10_ret, "hs300": hs_ret}).replace([np.inf, -np.inf], np.nan).dropna()
        if len(valid) >= 3 and float(valid["hs300"].var()) > 0:
            beta = float(valid["s10"].cov(valid["hs300"]) / valid["hs300"].var())
            corr = float(valid["s10"].corr(valid["hs300"]))
        else:
            beta = math.nan
            corr = math.nan
        exposure = pd.to_numeric(merged["gross_long_exposure"], errors="coerce")
        out[regime] = {
            "avg_long_exposure": float(exposure.mean()) if not exposure.empty else 0.0,
            "max_long_exposure": float(exposure.max()) if not exposure.empty else 0.0,
            "beta_to_hs300": beta,
            "corr_to_hs300": corr,
        }
    return out


def _neutrality_table(stats: dict[str, dict[str, float]]) -> str:
    lines = ["| regime | avg_long_exposure | max_long_exposure | daily_beta_to_HS300ETF | daily_corr_to_HS300ETF |", "|---|---:|---:|---:|---:|"]
    for regime in ("bull", "bear", "range", "oos"):
        item = stats[regime]
        lines.append(
            f"| {regime} | {_fmt_pct(item['avg_long_exposure'])} | {_fmt_pct(item['max_long_exposure'])} | "
            f"{_fmt_float(item['beta_to_hs300'])} | {_fmt_float(item['corr_to_hs300'])} |"
        )
    return "\n".join(lines)


def _data_info(panel: pd.DataFrame, universe: pd.DataFrame, pair_table: pd.DataFrame, infos: list[FormationInfo], critical_5pct: float) -> S10DataInfo:
    counts = universe.groupby("as_of_date")["symbol"].nunique() if not universe.empty else pd.Series(dtype="float64")
    unique_pairs = pair_table[["symbol_a", "symbol_b"]].drop_duplicates() if not pair_table.empty else pd.DataFrame()
    unique_symbols = set()
    if not pair_table.empty:
        unique_symbols.update(pair_table["symbol_a"].astype(str).tolist())
        unique_symbols.update(pair_table["symbol_b"].astype(str).tolist())
    by_symbol_delisted = panel.groupby("symbol")["is_delisted"].max().astype(bool)
    return S10DataInfo(
        panel_path=str(PANEL_CACHE.relative_to(PROJECT_ROOT)),
        panel_rows=len(panel),
        panel_symbols=int(panel["symbol"].nunique()),
        delisted_symbols=int(by_symbol_delisted.sum()),
        panel_start=str(min(panel["date"])) if not panel.empty else "NA",
        panel_end=str(max(panel["date"])) if not panel.empty else "NA",
        universe_dates=int(universe["as_of_date"].nunique()) if not universe.empty else 0,
        universe_min=int(counts.min()) if not counts.empty else 0,
        universe_median=float(counts.median()) if not counts.empty else 0.0,
        universe_max=int(counts.max()) if not counts.empty else 0,
        formation_count=len(infos),
        selected_pairs=len(pair_table),
        selected_unique_pairs=len(unique_pairs),
        selected_unique_symbols=len(unique_symbols),
        pair_sample_size=PAIR_SAMPLE_SIZE,
        monte_carlo_sims=MC_PVALUE_SIMS,
        eg_critical_5pct=critical_5pct,
        scope_note="reused S2 PIT panel cache only; csi300_constituents implemented as monthly PIT rolling-20d amount Top300 from that panel, no fresh constituent/data pull",
    )


def render_report(
    all_runs: dict[str, dict[str, BacktestRun]],
    data_info: S10DataInfo,
    formation_infos: list[FormationInfo],
    checks: dict[str, Any],
    neutrality: dict[str, dict[str, float]],
) -> str:
    s10_runs = {name: values["s10"] for name, values in all_runs.items()}
    hs300_runs = {name: values["hs300_etf"] for name, values in all_runs.items()}
    cfg = _strategy_cfg()
    final = "PASS" if checks["overall_pass"] else "FAIL"
    stop = _stop_stats(s10_runs)
    avg_exposure = float(np.mean([item["avg_long_exposure"] for item in neutrality.values()]))
    oos_pass = bool(checks["c_pass"])
    lines = [
        "# S10 Pairs Trading Gate1 Report",
        "",
        f"规则：每个 formation date 用过去 formation_window_days={cfg['formation_window_days']} 个交易日做 Engle-Granger 协整检验；每次从当月 PIT 流动性 universe 随机抽样 {PAIR_SAMPLE_SIZE} 对，p<0.05 后按 |t-stat| 排序取 num_pairs={cfg['num_pairs']}；交易窗口 trading_window_days={cfg['trading_window_days']}，每日用 formation mean/std 计算 z-score，不滚动重估。",
        f"执行：allow_short={cfg['allow_short']}，因此使用 long-only 近似。z>{cfg['entry_z']} 只买 B，z<-{cfg['entry_z']} 只买 A；|z|<={cfg['exit_z']} 平仓，|z|>={cfg['stop_z']} 记为 stop_z 协整失效止损。",
        "",
        "## 数据与 universe",
        f"- panel cache={data_info.panel_path}，rows={data_info.panel_rows}，symbols={data_info.panel_symbols}，delisted_symbols={data_info.delisted_symbols}，span={data_info.panel_start}..{data_info.panel_end}。",
        f"- universe monthly dates={data_info.universe_dates}，PIT rolling20 amount Top{UNIVERSE_TOP_N} size min/median/max={data_info.universe_min}/{data_info.universe_median:.1f}/{data_info.universe_max}。",
        f"- formation_count={data_info.formation_count}，selected_pairs={data_info.selected_pairs}，unique_pairs={data_info.selected_unique_pairs}，selected_unique_symbols={data_info.selected_unique_symbols}。",
        f"- Engle-Granger p 值来自固定种子 Monte Carlo 零分布，sims={data_info.monte_carlo_sims}，5% critical t={data_info.eg_critical_5pct:.4f}。",
        f"- scope_note={data_info.scope_note}。",
        "",
        "### formation 选择审计",
        _formation_table(formation_infos),
        "",
        "## S10 分段关键指标",
        _run_summary_table(s10_runs),
        "",
        "## long-only 市场中性折扣量化",
        _neutrality_table(neutrality),
        "",
        "## in-sample vs OOS 差异",
        _in_oos_table(s10_runs),
        "",
        "## 对照组 ratio 表",
        _comparison_table(all_runs),
        "",
        "## 反假设列表",
        f"- long-only 近似削弱市场中性：纯 pairs 应接近 net exposure=0；本轮 long-only 平均多头暴露={_fmt_pct(avg_exposure)}，各段 beta/corr 见上表。这个数字就是“市场中性”成立度打折，不能按美股多空配对宣传。",
        f"- 协整失效率：stop_z events={int(stop['stop_events'])}，closed trades={int(stop['trades'])}，stop_z 触发占总交易数={_fmt_pct(stop['stop_per_trade'])}，stop_z/entry={_fmt_pct(stop['stop_per_entry'])}。stop_z 是价差偏离到 {cfg['stop_z']}σ 后承认关系失效，不是无风险套利。",
        f"- 金融营销话术风险：即使名字叫市场中性，实际最大回撤最高为 {_fmt_pct(max(run.max_drawdown for run in s10_runs.values()))}，OOS return={_fmt_pct(summarize_run(s10_runs['oos'])['return'])}/PF={_fmt_float(summarize_run(s10_runs['oos'])['profit_factor'])}。若 C 组不过关，不能把 in-sample 片段包装成稳健 alpha。",
        "- A股政策冲击会破坏协整：2022 年中概股/平台经济/地产链和疫情政策预期反复冲击行业相关性；同一行业内股票可能因监管、融资、指数调仓或流动性偏好突然分化，历史 residual mean/std 不再代表未来。",
        f"- 多重检验假阳性：每次随机抽样 {PAIR_SAMPLE_SIZE} 对，在全为零假设时理论上也会有约 50 对 p<0.05；因此报告只把它当可执行反诈检验，不把“筛出协整对”本身当 alpha 证据。",
        "",
        "## flag/参数调查记录",
        "- long-only 近似：allow_short=false，short leg 未真实卖空，只买相对低估一腿；这放宽了纯市场中性假设。",
        "- 未调参：entry_z/exit_z/stop_z/num_pairs/pair_capital_pct/formation_window/trading_window 全部来自 `configs/strategy_addon.yaml`。",
        "- 未碰OOS：未用 OOS 修改参数、阈值、随机种子或 pair sample size；OOS 只按同一 walk-forward 规则生成 ≤D 信号并作 C 组最终裁决。",
        f"- random pair sampling 1000 对(种子固定)：每个 formation date 用 RANDOM_SEED={RANDOM_SEED}+date.toordinal() 抽样，未跑 N² 全对。",
        "- 成本/撮合：股票腿全部走 constraints.py，含 5 元佣金地板、印花税、过户费、滑点、T+1、涨跌停/停牌拒单。",
        "",
        "## A/B/C 判定",
        _abc_table(checks),
        "",
        "## Gate1 判定表",
        _gate1_table(checks, _load_yaml("backtest.yaml")["gate1"]),
        "",
        "## 与既有 FAIL 策略对比",
        _previous_strategy_table(final, oos_pass),
        "",
        f"最终判定：{final}",
    ]
    return "\n".join(lines) + "\n"


def run() -> dict[str, Any]:
    cfg = _strategy_cfg()
    regimes = _regimes()
    global_start = min(_parse_date(span["start"]) for span in regimes.values())
    global_end = max(_parse_date(span["end"]) for span in regimes.values())
    raw_panel = _load_panel_cache()
    panel = _prepare_panel(raw_panel)
    calendar_dates = sorted(panel["date"].dropna().unique().tolist())
    month_ends = _month_end_dates(calendar_dates)
    universe = _build_monthly_universe(panel, month_ends)
    universe_by_date = _universe_lookup(universe)
    close_wide = _close_wide(panel)
    pair_table, formation_infos, critical_5pct = _build_pair_table(calendar_dates, month_ends, universe_by_date, close_wide, cfg)
    data_info = _data_info(panel, universe, pair_table, formation_infos, critical_5pct)
    data = _data_by_symbol(panel)
    cost_config = CostConfig.from_mapping(_load_yaml("cost.yaml"))
    strategy = S10PairsTradingStrategy(cfg)
    etf_510300 = _load_cached_510300(global_start, global_end)

    all_runs: dict[str, dict[str, BacktestRun]] = {}
    for regime, span in regimes.items():
        start = _parse_date(span["start"])
        end = _parse_date(span["end"])
        all_runs[regime] = {
            "s10": _run_pairs_backtest("s10_pairs_trading", regime, start, end, data, close_wide, pair_table, calendar_dates, strategy, cost_config),
            "hs300_etf": _run_etf_buy_hold(regime, start, end, etf_510300, cost_config),
            "random10": _run_monthly_stock_backtest(
                "s10_random10_monthly",
                regime,
                start,
                end,
                data,
                universe_by_date,
                calendar_dates,
                month_ends,
                _random10_monthly_signal,
                cost_config,
            ),
            "universe_equal": _run_monthly_stock_backtest(
                "s10_universe_equal",
                regime,
                start,
                end,
                data,
                universe_by_date,
                calendar_dates,
                month_ends,
                _universe_equal_monthly_signal,
                cost_config,
            ),
        }

    s10_runs = {name: values["s10"] for name, values in all_runs.items()}
    hs300_runs = {name: values["hs300_etf"] for name, values in all_runs.items()}
    checks = _gate_checks(s10_runs, _load_yaml("backtest.yaml")["gate1"])
    neutrality = _neutrality_stats(s10_runs, hs300_runs)
    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    path = REPORT_DIR / "s10_pairs_trading_gate1.md"
    path.write_text(render_report(all_runs, data_info, formation_infos, checks, neutrality), encoding="utf-8")
    return {
        "path": path,
        "runs": all_runs,
        "checks": checks,
        "data_info": data_info,
        "formation_infos": formation_infos,
        "neutrality": neutrality,
    }


def _stdout_summary(result: dict[str, Any]) -> str:
    s10_runs = {name: values["s10"] for name, values in result["runs"].items()}
    checks = result["checks"]
    stop = _stop_stats(s10_runs)
    data_info: S10DataInfo = result["data_info"]
    final = "PASS" if checks["overall_pass"] else "FAIL"
    total_trades = sum(len(run.trades) for run in s10_runs.values())
    lines = [
        f"wrote {result['path']}",
        f"pairs_selected={data_info.selected_pairs} universe_min_median_max={data_info.universe_min}/{data_info.universe_median:.1f}/{data_info.universe_max}",
        f"stop_z_events={int(stop['stop_events'])} stop_z_per_trade={stop['stop_per_trade']:.4%}",
        _run_summary_table(s10_runs),
        _abc_table(checks),
        _gate1_table(checks, _load_yaml("backtest.yaml")["gate1"]),
        f"S10 Gate1 final: {final} trades={total_trades}",
    ]
    return "\n".join(lines)


def main() -> None:
    parser = argparse.ArgumentParser(description="Run S10 pairs trading Gate1")
    parser.parse_args()
    result = run()
    print(_stdout_summary(result))


if __name__ == "__main__":
    main()
