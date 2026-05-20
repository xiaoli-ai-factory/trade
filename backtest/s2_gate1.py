"""S2 multi-factor Gate1 runner with point-in-time universe construction."""

from __future__ import annotations

import argparse
import json
import math
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import asdict, dataclass
from datetime import date, timedelta
from pathlib import Path
from typing import Any

import lightgbm as lgb
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
    _parse_date,
    _record_execution,
    _trade_metrics,
    summarize_run,
)
from data.akshare_source import CACHE_DIR, _is_bj_symbol, _normalize_symbol, _universe, get_daily
from strategies.s2_factor import S2FactorStrategy


FEATURE_COLUMNS = ("mom_20", "mom_60", "vol_20", "turnover_20", "amount_20")
REQUESTED_TOP_N = 300
DEFAULT_TOP_N = REQUESTED_TOP_N
DEFAULT_WORKERS = 8
WARMUP_DAYS = 120
PIT_QUERY_START = date(2019, 10, 1)
S2_CACHE_VERSION = "v2pit"

OLD_CONTAMINATED_RESULTS = {
    "bull": {"return": 2.0140, "max_drawdown": 0.0918, "trades": 356, "expectancy": 5657.36, "profit_factor": 3.3776},
    "bear": {"return": 1.0603, "max_drawdown": 0.2540, "trades": 437, "expectancy": 2426.42, "profit_factor": 2.0548},
    "range": {"return": 2.9347, "max_drawdown": 0.2423, "trades": 699, "expectancy": 4198.42, "profit_factor": 2.2368},
    "oos": {"return": 1.7026, "max_drawdown": 0.2590, "trades": 868, "expectancy": 1961.54, "profit_factor": 1.6454},
}


@dataclass(frozen=True)
class S2DataInfo:
    query_start: str
    query_end: str
    requested_top_n: int
    used_top_n: int
    registry_source: str
    active_registry_symbols: int
    delisted_registry_symbols: int
    symbols_requested: int
    symbols_ok: int
    symbols_failed: int
    panel_rows: int
    panel_symbols: int
    inferred_list_date_symbols: int
    pit_assertion_rows: int
    delisted_symbols_with_rows: int
    delisted_feature_rows: int
    min_pool_size: int
    median_pool_size: float
    fetch_seconds: float
    failures_sample: tuple[str, ...]
    delisted_sample: tuple[str, ...]
    scope_note: str


@dataclass(frozen=True)
class S2ModelInfo:
    train_rows: int
    train_dates: int
    train_min_date: str
    train_max_date: str
    oos_min_date: str
    prediction_rows: int
    feature_importance: tuple[tuple[str, float], ...]
    lightgbm_version: str


def _cache_file(name: str, start: date, end: date, suffix: str) -> Path:
    return CACHE_DIR / f"s2_{name}_{S2_CACHE_VERSION}_{start.isoformat()}_{end.isoformat()}.{suffix}"


def _is_b_share(symbol: str) -> bool:
    code = _normalize_symbol(symbol)
    return code.startswith(("200", "900"))


def _registry_metadata(query_start: date, global_end: date) -> pd.DataFrame:
    universe = _universe(refresh=False).copy()
    universe["symbol"] = universe["symbol"].astype(str).str.zfill(6)
    universe["list_date"] = pd.to_datetime(universe["list_date"], errors="coerce").dt.date
    universe["delist_date"] = pd.to_datetime(universe["delist_date"], errors="coerce").dt.date
    universe = universe[~universe["symbol"].map(_is_bj_symbol) & ~universe["symbol"].map(_is_b_share)].copy()
    existed = (~universe["is_delisted"].astype(bool)) | (
        universe["delist_date"].notna() & (universe["delist_date"] >= query_start)
    )
    out = universe[existed].copy()
    out = out.drop_duplicates("symbol", keep="last").sort_values("symbol").reset_index(drop=True)
    return out[["symbol", "name", "list_date", "delist_date", "is_delisted"]]


def _add_pit_metadata(panel: pd.DataFrame, registry: pd.DataFrame) -> tuple[pd.DataFrame, int]:
    if panel.empty:
        return panel, 0
    meta = registry.rename(columns={"list_date": "meta_list_date"}).copy()
    out = panel.merge(meta[["symbol", "meta_list_date", "delist_date"]], on="symbol", how="left")
    out["date"] = pd.to_datetime(out["date"], errors="coerce").dt.date
    out["meta_list_date"] = pd.to_datetime(out["meta_list_date"], errors="coerce").dt.date
    out["delist_date"] = pd.to_datetime(out["delist_date"], errors="coerce").dt.date
    first_trade = out.groupby("symbol")["date"].transform("min")
    inferred_mask = out["meta_list_date"].isna()
    out["list_date"] = out["meta_list_date"].where(~inferred_mask, first_trade)
    out = out.drop(columns=["meta_list_date"])
    inferred_symbols = int(out.loc[inferred_mask, "symbol"].nunique())
    return out, inferred_symbols


def _fetch_one(symbol: str, start: date, end: date, refresh: bool) -> tuple[str, pd.DataFrame | None, str | None]:
    try:
        frame = get_daily(symbol, start=start, end=end, refresh=refresh)
        if frame.empty:
            return symbol, None, "empty"
        frame = frame.copy()
        frame["date"] = pd.to_datetime(frame["date"], errors="coerce").dt.date
        frame = frame.dropna(subset=["date"])
        if frame.empty:
            return symbol, None, "no_valid_date"
        return symbol, frame, None
    except Exception as exc:
        return symbol, None, f"{type(exc).__name__}: {exc}"


def _load_or_build_panel(
    global_start: date,
    global_end: date,
    top_n: int,
    workers: int,
    refresh: bool,
) -> tuple[pd.DataFrame, S2DataInfo]:
    query_start = min(PIT_QUERY_START, global_start - timedelta(days=WARMUP_DAYS))
    panel_path = _cache_file("panel", query_start, global_end, "parquet")
    info_path = _cache_file("panel_info", query_start, global_end, "json")
    if panel_path.exists() and info_path.exists() and not refresh:
        panel = pd.read_parquet(panel_path)
        raw_info = json.loads(info_path.read_text(encoding="utf-8"))
        return panel, S2DataInfo(**raw_info)

    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    registry = _registry_metadata(query_start, global_end)
    symbols = registry["symbol"].tolist()
    started = time.monotonic()
    frames: list[pd.DataFrame] = []
    failures: list[str] = []
    with ThreadPoolExecutor(max_workers=workers) as executor:
        futures = {executor.submit(_fetch_one, symbol, query_start, global_end, refresh): symbol for symbol in symbols}
        for index, future in enumerate(as_completed(futures), start=1):
            symbol, frame, error = future.result()
            if frame is None:
                failures.append(f"{symbol}:{error}")
            else:
                frames.append(frame)
            if index % 25 == 0 or index == len(symbols):
                print(f"s2_fetch_progress {index}/{len(symbols)} ok={len(frames)} fail={len(failures)}", flush=True)
    fetch_seconds = time.monotonic() - started
    panel = pd.concat(frames, ignore_index=True) if frames else pd.DataFrame()
    if not panel.empty:
        panel["symbol"] = panel["symbol"].astype(str).str.zfill(6)
        panel["date"] = pd.to_datetime(panel["date"], errors="coerce").dt.date
        panel = panel.dropna(subset=["date"]).sort_values(["symbol", "date"]).reset_index(drop=True)
        panel, inferred_list_dates = _add_pit_metadata(panel, registry)
        panel.to_parquet(panel_path, index=False)
    else:
        inferred_list_dates = 0

    features = _feature_frame(panel, global_start) if not panel.empty else pd.DataFrame()
    pool_sizes = _pool_sizes(features, top_n)
    pit_assertion_rows = _assert_feature_pit(features) if not features.empty else 0
    delisted_rows = panel[panel.get("is_delisted", False).astype(bool)] if not panel.empty else pd.DataFrame()
    delisted_features = features[features.get("is_delisted", False).astype(bool)] if not features.empty else pd.DataFrame()
    delisted_sample = tuple(
        delisted_rows[["symbol", "date"]]
        .drop_duplicates("symbol")
        .sort_values(["symbol", "date"])
        .head(8)
        .assign(text=lambda x: x["symbol"].astype(str) + "@" + x["date"].astype(str))["text"]
        .tolist()
    )
    info = S2DataInfo(
        query_start=query_start.isoformat(),
        query_end=global_end.isoformat(),
        requested_top_n=REQUESTED_TOP_N,
        used_top_n=top_n,
        registry_source="data._universe active+delisted registry; no spot/current-liquidity prefilter",
        active_registry_symbols=int((~registry["is_delisted"].astype(bool)).sum()),
        delisted_registry_symbols=int(registry["is_delisted"].astype(bool).sum()),
        symbols_requested=int(len(symbols)),
        symbols_ok=int(panel["symbol"].nunique()) if not panel.empty else 0,
        symbols_failed=len(failures),
        panel_rows=len(panel),
        panel_symbols=int(panel["symbol"].nunique()) if not panel.empty else 0,
        inferred_list_date_symbols=inferred_list_dates,
        pit_assertion_rows=pit_assertion_rows,
        delisted_symbols_with_rows=int(delisted_rows["symbol"].nunique()) if not delisted_rows.empty else 0,
        delisted_feature_rows=len(delisted_features),
        min_pool_size=int(pool_sizes.min()) if not pool_sizes.empty else 0,
        median_pool_size=float(pool_sizes.median()) if not pool_sizes.empty else 0.0,
        fetch_seconds=float(fetch_seconds),
        failures_sample=tuple(failures[:12]),
        delisted_sample=delisted_sample,
        scope_note="strict_pit_registry_all_symbols_with_history; liquidity ranking uses only rolling amount_20 at each as_of_date",
    )
    info_path.write_text(json.dumps(asdict(info), ensure_ascii=False, indent=2), encoding="utf-8")
    return panel, info


def _feature_frame(panel: pd.DataFrame, global_start: date) -> pd.DataFrame:
    if panel.empty:
        return pd.DataFrame()
    df = panel.copy()
    df["date"] = pd.to_datetime(df["date"], errors="coerce").dt.date
    df = df.dropna(subset=["date"]).sort_values(["symbol", "date"]).reset_index(drop=True)
    df["list_date"] = pd.to_datetime(df["list_date"], errors="coerce").dt.date
    df["delist_date"] = pd.to_datetime(df["delist_date"], errors="coerce").dt.date
    for col in ("open", "high", "low", "close", "vol", "amount", "turnover", "float_mv"):
        df[col] = pd.to_numeric(df[col], errors="coerce")
    df["ret_1"] = df.groupby("symbol")["close"].pct_change()
    grouped = df.groupby("symbol", group_keys=False)
    df["mom_20"] = grouped["close"].transform(lambda item: item / item.shift(20) - 1.0)
    df["mom_60"] = grouped["close"].transform(lambda item: item / item.shift(60) - 1.0)
    df["vol_20"] = grouped["ret_1"].transform(lambda item: item.rolling(20, min_periods=20).std())
    df["turnover_20"] = grouped["turnover"].transform(lambda item: item.rolling(20, min_periods=20).mean())
    df["amount_20"] = grouped["amount"].transform(lambda item: item.rolling(20, min_periods=20).mean())
    df["age_days"] = grouped.cumcount() + 1
    min_list_days = int(_load_yaml("backtest.yaml")["universe"].get("min_list_days", 60))
    listed = df["list_date"].notna() & (df["list_date"] <= df["date"])
    not_delisted = df["delist_date"].isna() | (df["delist_date"] > df["date"])
    eligible = (
        (df["date"] >= global_start)
        & listed
        & not_delisted
        & (df["age_days"] >= min_list_days)
        & (~df["is_suspended"].astype(bool))
        & (df["close"] > 0)
        & (df["amount_20"] > 0)
    )
    for col in FEATURE_COLUMNS:
        eligible &= np.isfinite(pd.to_numeric(df[col], errors="coerce"))
    df["feature_eligible"] = eligible
    return df


def _assert_feature_pit(features: pd.DataFrame) -> int:
    rows = features[features["feature_eligible"]].copy()
    if rows.empty:
        return 0
    list_dates = pd.to_datetime(rows["list_date"], errors="coerce")
    delist_dates = pd.to_datetime(rows["delist_date"], errors="coerce")
    dates = pd.to_datetime(rows["date"], errors="coerce")
    assert list_dates.notna().all(), "S2 PIT universe has missing list_date"
    assert (list_dates <= dates).all(), "S2 PIT universe includes pre-listing rows"
    assert (delist_dates.isna() | (delist_dates > dates)).all(), "S2 PIT universe includes delisted rows"
    return len(rows)


def _weekly_dates(dates: list[date]) -> list[date]:
    if not dates:
        return []
    frame = pd.DataFrame({"date": pd.to_datetime(pd.Series(dates))})
    frame["week"] = frame["date"].dt.strftime("%G-%V")
    return frame.groupby("week", sort=True)["date"].min().dt.date.tolist()


def _pool_sizes(features: pd.DataFrame, top_n: int) -> pd.Series:
    if features.empty:
        return pd.Series(dtype="float64")
    rows = features[features["feature_eligible"]].copy()
    if rows.empty:
        return pd.Series(dtype="float64")
    ranks = rows.groupby("date")["amount_20"].rank(method="first", ascending=False)
    return rows[ranks <= top_n].groupby("date")["symbol"].nunique()


def _build_model_tables(panel: pd.DataFrame, top_n: int) -> tuple[pd.DataFrame, pd.DataFrame, S2ModelInfo]:
    backtest_cfg = _load_yaml("backtest.yaml")
    regimes = backtest_cfg["regimes"]
    global_start = min(_parse_date(item["start"]) for item in regimes.values())
    oos_start = _parse_date(regimes["oos"]["start"])
    features = _feature_frame(panel, global_start)
    if features.empty:
        raise RuntimeError("S2 feature frame is empty")
    all_dates = sorted(features["date"].dropna().unique().tolist())
    rebalance_dates = [item for item in _weekly_dates(all_dates) if global_start <= item <= max(all_dates)]
    next_rebalance = {left: right for left, right in zip(rebalance_dates[:-1], rebalance_dates[1:], strict=False)}
    rows = features[features["feature_eligible"] & features["date"].isin(rebalance_dates)].copy()
    rows["as_of_date"] = rows["date"]
    rows["label_end_date"] = rows["as_of_date"].map(next_rebalance)
    _assert_feature_pit(rows)
    rows["liquidity_rank"] = rows.groupby("as_of_date")["amount_20"].rank(method="first", ascending=False)
    rows = rows[rows["liquidity_rank"] <= top_n].copy()
    _assert_candidate_pit(rows)

    prediction_rows = rows.dropna(subset=list(FEATURE_COLUMNS)).copy()
    closes = features[["symbol", "date", "close"]].rename(columns={"date": "label_end_date", "close": "future_close"})
    trainable = prediction_rows.dropna(subset=["label_end_date"]).merge(closes, on=["symbol", "label_end_date"], how="left")
    trainable = trainable.dropna(subset=["future_close"])
    trainable["label"] = pd.to_numeric(trainable["future_close"], errors="coerce") / pd.to_numeric(trainable["close"], errors="coerce") - 1.0
    trainable = trainable[np.isfinite(trainable["label"])].copy()
    assert (pd.to_datetime(trainable["label_end_date"]).dt.date > pd.to_datetime(trainable["as_of_date"]).dt.date).all()

    train_mask = _in_sample_mask(trainable["as_of_date"], regimes)
    train_mask &= pd.to_datetime(trainable["label_end_date"]).dt.date < oos_start
    train = trainable[train_mask].copy()
    if train.empty:
        raise RuntimeError("S2 train set is empty")
    train_dates = pd.to_datetime(train["as_of_date"]).dt.date
    assert train_dates.max() < oos_start, f"training leaks into OOS: {train_dates.max()} >= {oos_start}"

    train_set = lgb.Dataset(train[list(FEATURE_COLUMNS)], label=train["label"], free_raw_data=False)
    params = {
        "objective": "regression",
        "metric": "l2",
        "learning_rate": 0.05,
        "num_leaves": 31,
        "min_data_in_leaf": 20,
        "bagging_fraction": 1.0,
        "feature_fraction": 1.0,
        "seed": RANDOM_SEED,
        "deterministic": True,
        "force_col_wise": True,
        "verbosity": -1,
        "num_threads": 1,
    }
    model = lgb.train(params, train_set, num_boost_round=100)
    prediction_rows = prediction_rows.copy()
    prediction_rows["score"] = model.predict(prediction_rows[list(FEATURE_COLUMNS)])
    keep_cols = [
        "as_of_date",
        "symbol",
        "close",
        "amount_20",
        "turnover_20",
        "float_mv",
        "liquidity_rank",
        "score",
        "is_delisted",
        "list_date",
        "delist_date",
    ]
    predictions = prediction_rows[keep_cols].copy()
    predictions["as_of_date"] = pd.to_datetime(predictions["as_of_date"]).dt.date
    info = S2ModelInfo(
        train_rows=len(train),
        train_dates=int(train["as_of_date"].nunique()),
        train_min_date=str(train_dates.min()),
        train_max_date=str(train_dates.max()),
        oos_min_date=oos_start.isoformat(),
        prediction_rows=len(predictions),
        feature_importance=tuple(
            sorted(
                zip(FEATURE_COLUMNS, [float(item) for item in model.feature_importance(importance_type="split")], strict=False),
                key=lambda item: item[1],
                reverse=True,
            )
        ),
        lightgbm_version=str(lgb.__version__),
    )
    return features, predictions, info


def _assert_candidate_pit(rows: pd.DataFrame) -> None:
    if rows.empty:
        return
    list_dates = pd.to_datetime(rows["list_date"], errors="coerce")
    delist_dates = pd.to_datetime(rows["delist_date"], errors="coerce")
    as_of_dates = pd.to_datetime(rows["as_of_date"], errors="coerce")
    assert list_dates.notna().all(), "S2 candidate missing list_date"
    assert (list_dates <= as_of_dates).all(), "S2 candidate listed after as_of_date"
    assert (delist_dates.isna() | (delist_dates > as_of_dates)).all(), "S2 candidate delisted by as_of_date"


def _in_sample_mask(values: pd.Series, regimes: dict[str, Any]) -> pd.Series:
    dates = pd.to_datetime(values, errors="coerce").dt.date
    mask = pd.Series(False, index=values.index)
    for name in ("bull", "bear", "range"):
        start = _parse_date(regimes[name]["start"])
        end = _parse_date(regimes[name]["end"])
        mask |= (dates >= start) & (dates <= end)
    return mask


def _position_for_symbol(positions: tuple[Position, ...], symbol: str) -> Position | None:
    for item in positions:
        if item.symbol == symbol:
            return item
    return None


def _run_s2_backtest(
    name: str,
    regime: str,
    start: date,
    end: date,
    data: dict[str, pd.DataFrame],
    predictions: pd.DataFrame,
    strategy: S2FactorStrategy,
    cost_config: CostConfig,
) -> BacktestRun:
    dates = sorted({item for frame in data.values() for item in frame["date"].tolist() if start <= item <= end})
    if len(dates) < 2:
        raise RuntimeError(f"Not enough S2 dates for {regime}")
    pred = predictions.copy()
    pred["as_of_date"] = pd.to_datetime(pred["as_of_date"], errors="coerce").dt.date
    pred = pred[(pred["as_of_date"] >= start) & (pred["as_of_date"] <= end)].copy()
    rebalance_dates = set(pred["as_of_date"].dropna().unique().tolist())

    cash = INITIAL_CASH
    positions: tuple[Position, ...] = ()
    basis: dict[str, float] = {}
    trades: list[TradeRecord] = []
    filled_orders: list[dict[str, Any]] = []
    rejected_orders: list[dict[str, Any]] = []
    events: list[dict[str, Any]] = []
    nav_rows = [{"date": dates[0].isoformat(), "nav": cash}]

    for signal_date, trade_date in zip(dates[:-1], dates[1:], strict=False):
        positions = mark_sellable(positions, trade_date)
        if signal_date in rebalance_dates:
            current_pred = pred[pred["as_of_date"] == signal_date].copy()
            needed_symbols = set(current_pred["symbol"].astype(str).tolist())
            needed_symbols.update(item.symbol for item in positions if item.quantity > 0)
            ctx_data = {symbol: data[symbol][data[symbol]["date"] <= signal_date].copy() for symbol in needed_symbols if symbol in data}
            ctx = {
                "data": ctx_data,
                "positions": positions,
                "cash": cash,
                "nav": _mark_nav(cash, positions, data, signal_date),
                "lot_size": LOT_SIZE,
                "predictions": current_pred,
                "rebalance_dates": {signal_date},
            }
            orders = strategy.generate_signals(signal_date, ctx)
            orders = sorted(orders, key=lambda item: 0 if item.side == "sell" else 1)
            for order in orders:
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
    return BacktestRun(
        name=name,
        regime=regime,
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


def _data_by_symbol(panel: pd.DataFrame) -> dict[str, pd.DataFrame]:
    out = {}
    for symbol, frame in panel.groupby("symbol", sort=False):
        item = frame.copy()
        item["date"] = pd.to_datetime(item["date"], errors="coerce").dt.date
        out[str(symbol)] = item.sort_values("date").reset_index(drop=True)
    return out


def _strategy_cfg(mode: str, top_n: int, benchmark_hold_n: int | str | None = None, exclude_small_mv: bool = False) -> dict[str, Any]:
    cfg = _load_yaml("strategy.yaml")["s2_factor"].copy()
    cfg["selection_mode"] = mode
    cfg["universe_top_n"] = top_n
    cfg["random_seed"] = RANDOM_SEED
    cfg["exclude_small_mv"] = exclude_small_mv
    if benchmark_hold_n is not None:
        cfg["benchmark_hold_n"] = benchmark_hold_n
    return cfg


def run_s2_gate1(
    refresh: bool = False,
    top_n: int = DEFAULT_TOP_N,
    workers: int = DEFAULT_WORKERS,
) -> tuple[dict[str, dict[str, BacktestRun]], S2DataInfo, S2ModelInfo]:
    backtest_cfg = _load_yaml("backtest.yaml")
    regimes = backtest_cfg["regimes"]
    global_start = min(_parse_date(span["start"]) for span in regimes.values())
    global_end = max(_parse_date(span["end"]) for span in regimes.values())
    panel, data_info = _load_or_build_panel(global_start, global_end, top_n, workers, refresh)
    _features, predictions, model_info = _build_model_tables(panel, top_n)
    data = _data_by_symbol(panel)
    cost_config = CostConfig.from_mapping(_load_yaml("cost.yaml"))

    all_runs: dict[str, dict[str, BacktestRun]] = {}
    for regime, span in regimes.items():
        start = _parse_date(span["start"])
        end = _parse_date(span["end"])
        all_runs[regime] = {
            "s2": _run_s2_backtest(
                "s2",
                regime,
                start,
                end,
                data,
                predictions,
                S2FactorStrategy(_strategy_cfg("model", top_n)),
                cost_config,
            ),
            "equal_weight": _run_s2_backtest(
                "s2_equal_weight_pool",
                regime,
                start,
                end,
                data,
                predictions,
                S2FactorStrategy(_strategy_cfg("equal_weight", top_n, benchmark_hold_n="all")),
                cost_config,
            ),
            "random": _run_s2_backtest(
                "s2_random_15",
                regime,
                start,
                end,
                data,
                predictions,
                S2FactorStrategy(_strategy_cfg("random", top_n)),
                cost_config,
            ),
            "exclude_small_mv": _run_s2_backtest(
                "s2_exclude_small_mv",
                regime,
                start,
                end,
                data,
                predictions,
                S2FactorStrategy(_strategy_cfg("model", top_n, exclude_small_mv=True)),
                cost_config,
            ),
        }
    write_report(all_runs, data_info, model_info)
    return all_runs, data_info, model_info


def _summary_table(runs: dict[str, BacktestRun]) -> str:
    lines = [
        "| regime | return | max_drawdown | trades | expectancy_after_cost | profit_factor | win_rate | fee_ratio |",
        "|---|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for name in ("bull", "bear", "range", "oos"):
        metrics = summarize_run(runs[name])
        lines.append(
            f"| {name} | {_fmt_pct(metrics['return'])} | {_fmt_pct(metrics['max_drawdown'])} | {int(metrics['trades'])} | "
            f"{metrics['expectancy']:.2f} | {_fmt_float(metrics['profit_factor'])} | {_fmt_pct(metrics['win_rate'])} | {_fmt_pct(metrics['fee_ratio'])} |"
        )
    return "\n".join(lines)


def _comparison_table(all_runs: dict[str, dict[str, BacktestRun]]) -> str:
    lines = [
        "| regime | metric | S2 | equal_weight_pool | random_15 | S2/EW | S2/random | note |",
        "|---|---|---:|---:|---:|---:|---:|---|",
    ]
    for regime in ("bull", "bear", "range", "oos"):
        s2 = summarize_run(all_runs[regime]["s2"])
        ew = summarize_run(all_runs[regime]["equal_weight"])
        rnd = summarize_run(all_runs[regime]["random"])
        for metric, pct in (("return", True), ("max_drawdown", True), ("trades", False), ("fee_ratio", True)):
            left = s2[metric]
            right_ew = ew[metric]
            right_rnd = rnd[metric]
            r1 = left / right_ew if abs(right_ew) > 1e-12 else math.nan
            r2 = left / right_rnd if abs(right_rnd) > 1e-12 else math.nan
            fmt = _fmt_pct if pct else _fmt_float
            note = "ratio>2x需调查" if any(abs(x) > 2 for x in (r1, r2) if not pd.isna(x) and not math.isinf(x)) else ""
            lines.append(f"| {regime} | {metric} | {fmt(left)} | {fmt(right_ew)} | {fmt(right_rnd)} | {_fmt_float(r1)} | {_fmt_float(r2)} | {note} |")
    return "\n".join(lines)


def _overfit_table(s2_runs: dict[str, BacktestRun]) -> str:
    in_trades = tuple(item for name in ("bull", "bear", "range") for item in s2_runs[name].trades)
    in_metrics = _trade_metrics(in_trades)
    oos_metrics = summarize_run(s2_runs["oos"])
    avg_in_return = float(np.mean([s2_runs[name].total_return for name in ("bull", "bear", "range")]))
    lines = [
        "| span | avg/period_return | trades | expectancy | profit_factor | win_rate | max_drawdown_worst |",
        "|---|---:|---:|---:|---:|---:|---:|",
        f"| in_sample(bull+bear+range) | {_fmt_pct(avg_in_return)} | {int(in_metrics['trades'])} | {in_metrics['expectancy']:.2f} | {_fmt_float(in_metrics['profit_factor'])} | {_fmt_pct(in_metrics['win_rate'])} | {_fmt_pct(max(s2_runs[n].max_drawdown for n in ('bull','bear','range')))} |",
        f"| oos | {_fmt_pct(oos_metrics['return'])} | {int(oos_metrics['trades'])} | {oos_metrics['expectancy']:.2f} | {_fmt_float(oos_metrics['profit_factor'])} | {_fmt_pct(oos_metrics['win_rate'])} | {_fmt_pct(oos_metrics['max_drawdown'])} |",
    ]
    return "\n".join(lines)


def _feature_importance_table(model_info: S2ModelInfo) -> str:
    lines = ["| feature | importance |", "|---|---:|"]
    for feature, value in model_info.feature_importance:
        lines.append(f"| {feature} | {value:.0f} |")
    return "\n".join(lines)


def _old_contaminated_comparison_table(new_runs: dict[str, BacktestRun]) -> str:
    lines = [
        "| regime | old_invalid_return | new_PIT_return | old_invalid_DD | new_PIT_DD | old_invalid_PF | new_PIT_PF |",
        "|---|---:|---:|---:|---:|---:|---:|",
    ]
    for regime in ("bull", "bear", "range", "oos"):
        old = OLD_CONTAMINATED_RESULTS[regime]
        new = summarize_run(new_runs[regime])
        lines.append(
            f"| {regime} | {_fmt_pct(old['return'])} | {_fmt_pct(new['return'])} | "
            f"{_fmt_pct(old['max_drawdown'])} | {_fmt_pct(new['max_drawdown'])} | "
            f"{_fmt_float(old['profit_factor'])} | {_fmt_float(new['profit_factor'])} |"
        )
    return "\n".join(lines)


def _data_info_text(info: S2DataInfo) -> list[str]:
    return [
        f"- 取数区间：{info.query_start}..{info.query_end}，warmup={WARMUP_DAYS}日。",
        f"- requested TopN={info.requested_top_n}；本轮执行 TopN={info.used_top_n}，流动性排名只用每个调仓日 ≤D 的 amount_20。",
        f"- registry_source={info.registry_source}。",
        f"- registry: active={info.active_registry_symbols}, delisted={info.delisted_registry_symbols}, requested_symbols={info.symbols_requested}。",
        f"- fetched: ok_symbols={info.symbols_ok}, failed_symbols={info.symbols_failed}, panel_symbols={info.panel_symbols}, panel_rows={info.panel_rows}, fetch_seconds={info.fetch_seconds:.1f}。",
        f"- list_date: inferred_for_active_or_missing_meta_symbols={info.inferred_list_date_symbols}; inferred date is first historical bar in the panel, never a current liquidity attribute.",
        f"- PIT assertion: eligible feature rows checked={info.pit_assertion_rows}; assertion=list_date<=D and (delist_date is null or delist_date>D)。",
        f"- PIT liquidity pool size by rebalance date: min={info.min_pool_size}, median={info.median_pool_size:.1f}。",
        f"- delisted evidence: symbols_with_rows={info.delisted_symbols_with_rows}, eligible_feature_rows={info.delisted_feature_rows}, sample={', '.join(info.delisted_sample) if info.delisted_sample else 'none'}。",
        "- delisted data caveat: many delisted rows come from Tencent fallback; amount is approximated as volume*close and turnover is unavailable/zero-filled, so delisted factor quality is weaker than active Sina rows.",
        f"- scope_note: {info.scope_note}。",
        f"- failures_sample: {', '.join(info.failures_sample) if info.failures_sample else 'none'}。",
    ]


def render_report(all_runs: dict[str, dict[str, BacktestRun]], data_info: S2DataInfo, model_info: S2ModelInfo) -> str:
    s2_runs = {regime: runs["s2"] for regime, runs in all_runs.items()}
    checks = _gate_checks(s2_runs, _load_yaml("backtest.yaml")["gate1"])
    overall = "PASS" if checks["overall_pass"] else "FAIL"
    ex_small_oos = summarize_run(all_runs["oos"]["exclude_small_mv"])
    s2_oos = summarize_run(all_runs["oos"]["s2"])
    lines = [
        "# S2 Multi-Factor Gate1 Report",
        "",
        "旧版作废说明：上一版 S2 使用当前在市/当前成交额预筛，属于幸存者+未来属性污染，已按 Claude review 裁决作废。本报告覆盖旧报告；下表只保留旧污染数字用于审计对比。",
        _old_contaminated_comparison_table(s2_runs),
        "",
        "参数：严格使用 `configs/strategy.yaml` 的 S2 配置：weekly rebalance、hold_n=15、factors=mom_20/mom_60/vol_20/turnover_20/amount_20、model=lightgbm。",
        "信号在调仓日 D 收盘后只用 ≤D 的日线因子，D+1 开盘按 constraints.py 撮合；OOS 未参与训练、早停、选特征或调参。",
        "",
        "## 数据与 scope",
        *_data_info_text(data_info),
        "",
        "## 训练/OOS 隔离证据",
        f"- LightGBM version={model_info.lightgbm_version}，固定默认参数训练一次，无 early stopping，无 shuffle/K-fold。",
        f"- train_rows={model_info.train_rows}, train_dates={model_info.train_dates}, train_date_range={model_info.train_min_date}..{model_info.train_max_date}。",
        f"- oos_min_date={model_info.oos_min_date}；assert train_max_date < oos_min_date 已通过。",
        f"- prediction_rows={model_info.prediction_rows}。",
        _feature_importance_table(model_info),
        "",
        "## S2 分段关键指标",
        _summary_table(s2_runs),
        "",
        "## in-sample vs OOS 过拟合体检",
        _overfit_table(s2_runs),
        "",
        "## 对照组 ratio 表",
        _comparison_table(all_runs),
        "",
        "## 反假设列表",
        "- 因子收益只是小盘/低流动性 beta：候选池每个调仓日先按过去20日成交额做 PIT TopN；另跑 `exclude_small_mv`（剔除当日候选中 float_mv 最小20%）作反证。",
        f"  OOS 原 S2 return={_fmt_pct(s2_oos['return'])}/DD={_fmt_pct(s2_oos['max_drawdown'])}/PF={_fmt_float(s2_oos['profit_factor'])}；exclude_small_mv OOS return={_fmt_pct(ex_small_oos['return'])}/DD={_fmt_pct(ex_small_oos['max_drawdown'])}/PF={_fmt_float(ex_small_oos['profit_factor'])}。",
        "- ML 泄漏/过拟合：label 为调仓日之后到下个周频调仓的 forward return；训练只用 bull+bear+range 且 label_end < OOS 起点；OOS 不参与训练/早停/调参。上方 in-sample vs OOS 表用于观察性能塌缩。",
        "- 周频15只调仓成本拖累：报告展示 fee_ratio；所有成本含 5 元佣金地板、印花税、过户费和 0.2% 滑点。",
        "",
        "## flag/参数调查记录",
        "- 未修改 `configs/strategy.yaml`，未调 hold_n/factors/model/LightGBM 超参来改善 OOS。",
        "- 未触碰 OOS 训练/调参；OOS 只用于最终 C 组裁决。",
        "- 已移除当前流动性预筛，改纯PIT：候选 symbol 注册表来自 active+delisted 清单，调仓日资格由历史 list_date/delist_date/amount_20 决定。",
        "- 未调用 `_load_spot_liquidity()` 或任何当前快照成交额接口；S2 代码中该函数已删除。",
        "- scope 限制：active 清单仍来自 AkShare 当前 symbol 注册表作为代码目录，但不按当前在市状态或当前成交额做入池排序；新上市股票在其首个历史 bar/list_date 之前被 PIT 断言排除。",
        "",
        "## Gate1 判定表",
        _gate1_table(checks, _load_yaml("backtest.yaml")["gate1"]),
        "",
        f"最终判定：{overall}",
    ]
    return "\n".join(lines) + "\n"


def write_report(all_runs: dict[str, dict[str, BacktestRun]], data_info: S2DataInfo, model_info: S2ModelInfo) -> Path:
    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    path = REPORT_DIR / "s2_gate1.md"
    path.write_text(render_report(all_runs, data_info, model_info), encoding="utf-8")
    return path


def _stdout_summary(all_runs: dict[str, dict[str, BacktestRun]], data_info: S2DataInfo, model_info: S2ModelInfo) -> str:
    s2_runs = {regime: runs["s2"] for regime, runs in all_runs.items()}
    checks = _gate_checks(s2_runs, _load_yaml("backtest.yaml")["gate1"])
    return "\n".join(
        [
            "wrote <PROJECT_ROOT>/reports/s2_gate1.md",
            f"data_scope registry_active={data_info.active_registry_symbols} registry_delisted={data_info.delisted_registry_symbols} symbols_ok={data_info.symbols_ok} symbols_failed={data_info.symbols_failed} panel_rows={data_info.panel_rows} fetch_seconds={data_info.fetch_seconds:.1f} scope_note={data_info.scope_note}",
            f"pit_assertion_rows={data_info.pit_assertion_rows} inferred_list_date_symbols={data_info.inferred_list_date_symbols} pool_min={data_info.min_pool_size} pool_median={data_info.median_pool_size:.1f}",
            f"train_rows={model_info.train_rows} train_max_date={model_info.train_max_date} oos_min_date={model_info.oos_min_date} prediction_rows={model_info.prediction_rows}",
            _summary_table(s2_runs),
            _overfit_table(s2_runs),
            _gate1_table(checks, _load_yaml("backtest.yaml")["gate1"]),
            f"S2 Gate1 final: {'PASS' if checks['overall_pass'] else 'FAIL'}",
        ]
    )


def main() -> None:
    parser = argparse.ArgumentParser(description="Run S2 multi-factor Gate1")
    parser.add_argument("--refresh", action="store_true")
    parser.add_argument("--top-n", type=int, default=DEFAULT_TOP_N)
    parser.add_argument("--workers", type=int, default=DEFAULT_WORKERS)
    args = parser.parse_args()
    all_runs, data_info, model_info = run_s2_gate1(
        refresh=args.refresh,
        top_n=args.top_n,
        workers=args.workers,
    )
    print(_stdout_summary(all_runs, data_info, model_info))


if __name__ == "__main__":
    main()
