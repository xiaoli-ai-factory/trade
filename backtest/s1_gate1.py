"""S1 tail-chasing Gate1 runner using BaoStock 5min PIT snapshots."""

from __future__ import annotations

import argparse
import contextlib
import io
import json
import math
import time
from dataclasses import asdict, dataclass
from datetime import date, timedelta
from pathlib import Path
from typing import Any, Iterable

import baostock as bs
import baostock.common.context as bs_context
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
    _max_drawdown,
    _parse_date,
    _record_execution,
    _trade_metrics,
    summarize_run,
)
from data.akshare_source import (
    BAOSTOCK_5MIN_FIELDS,
    CACHE_DIR,
    VOL_RATIO_LOOKBACK_DAYS,
    _baostock_symbol,
    _elapsed_trading_fraction,
    _is_bj_symbol,
    _load_yaml,
    _normalize_symbol,
)


S1_CACHE_VERSION = "v1"
S2_PIT_PANEL = CACHE_DIR / "s2_panel_v2pit_2019-10-01_2026-05-15.parquet"
SNAPSHOT_CACHE = CACHE_DIR / f"s1_baostock_snapshots_{S1_CACHE_VERSION}.parquet"
PREFILTER_CACHE = CACHE_DIR / f"s1_prefilter_{S1_CACHE_VERSION}.parquet"
MAX_CLUSTER_TRADING_DAYS = 20
SNAPSHOT_BATCH_CLUSTERS = 100
BAOSTOCK_ATTEMPTS = 3
BAOSTOCK_SOCKET_TIMEOUT_SECONDS = 20

SNAPSHOT_COLUMNS = [
    "symbol",
    "date",
    "fetch_ok",
    "error",
    "source_max_ts",
    "price_at_1450",
    "pct_chg_at_1450",
    "cum_vol_at_1450",
    "vol_ratio_at_1450",
    "turnover_at_1450",
    "vwap_at_1450",
    "is_above_vwap",
    "high_after_1430",
    "high_after_1430_price",
]


@dataclass(frozen=True)
class S1DataInfo:
    daily_panel_source: str
    daily_panel_rows: int
    daily_panel_symbols: int
    active_symbols: int
    delisted_symbols: int
    global_start: str
    global_end: str
    prefilter_rows: int
    prefilter_dates: int
    prefilter_symbols: int
    prefilter_min_per_day: int
    prefilter_median_per_day: float
    prefilter_max_per_day: int
    delisted_prefilter_rows: int
    delisted_prefilter_symbols: int
    delisted_prefilter_sample: tuple[str, ...]
    snapshot_rows: int
    snapshot_ok_rows: int
    snapshot_failed_rows: int
    snapshot_failures_sample: tuple[str, ...]
    snapshot_clusters_total: int
    snapshot_clusters_fetched: int
    snapshot_fetch_seconds: float
    skipped_signal_rows_no_next_open: int


def _baostock_version() -> str:
    return str(getattr(bs, "__version__", "unknown"))


def _set_baostock_socket_timeout() -> None:
    sock = getattr(bs_context, "default_socket", None)
    if sock is not None:
        sock.settimeout(BAOSTOCK_SOCKET_TIMEOUT_SECONDS)


def _is_b_share(symbol: str) -> bool:
    code = _normalize_symbol(symbol)
    return code.startswith(("200", "900"))


def _trade_date_set(backtest_cfg: dict[str, Any]) -> tuple[date, date]:
    regimes = backtest_cfg["regimes"]
    global_start = min(_parse_date(span["start"]) for span in regimes.values())
    global_end = max(_parse_date(span["end"]) for span in regimes.values())
    return global_start, global_end


def _load_daily_panel(global_start: date, global_end: date) -> pd.DataFrame:
    if not S2_PIT_PANEL.exists():
        raise RuntimeError(f"Missing PIT daily panel: {S2_PIT_PANEL}")
    columns = [
        "symbol",
        "date",
        "open",
        "high",
        "low",
        "close",
        "vol",
        "amount",
        "pct_chg",
        "turnover",
        "float_mv",
        "is_suspended",
        "limit_up_price",
        "limit_down_price",
        "is_delisted",
        "list_date",
        "delist_date",
    ]
    panel = pd.read_parquet(S2_PIT_PANEL, columns=columns)
    panel["symbol"] = panel["symbol"].astype(str).str.zfill(6)
    panel = panel[~panel["symbol"].map(_is_bj_symbol) & ~panel["symbol"].map(_is_b_share)].copy()
    panel["date"] = pd.to_datetime(panel["date"], errors="coerce").dt.date
    panel["list_date"] = pd.to_datetime(panel["list_date"], errors="coerce").dt.date
    panel["delist_date"] = pd.to_datetime(panel["delist_date"], errors="coerce").dt.date
    panel = panel.dropna(subset=["date"])
    warmup_start = global_start - timedelta(days=80)
    panel = panel[(panel["date"] >= warmup_start) & (panel["date"] <= global_end)].copy()
    for col in ("open", "high", "low", "close", "vol", "amount", "turnover", "float_mv", "limit_up_price", "limit_down_price"):
        panel[col] = pd.to_numeric(panel[col], errors="coerce")
    for col in ("is_suspended", "is_delisted"):
        panel[col] = panel[col].astype(bool)
    return panel.sort_values(["symbol", "date"]).reset_index(drop=True)


def _add_prior_volume_context(panel: pd.DataFrame, candidates: pd.DataFrame) -> pd.DataFrame:
    if candidates.empty:
        return candidates
    parts: list[pd.DataFrame] = []
    candidate_keys = candidates[["symbol", "date"]].copy()
    panel_by_symbol = {
        str(symbol): frame[["date", "vol", "is_suspended"]].copy()
        for symbol, frame in panel.groupby("symbol", sort=False)
    }
    for symbol, sym_candidates in candidate_keys.groupby("symbol", sort=False):
        hist = panel_by_symbol.get(str(symbol), pd.DataFrame(columns=["date", "vol", "is_suspended"]))
        valid = hist[(~hist["is_suspended"].astype(bool)) & pd.to_numeric(hist["vol"], errors="coerce").notna()].copy()
        if valid.empty:
            item = sym_candidates.copy()
            item["avg_vol5"] = np.nan
            parts.append(item)
            continue
        valid = valid.sort_values("date")
        valid["avg_vol5"] = valid["vol"].rolling(VOL_RATIO_LOOKBACK_DAYS, min_periods=1).mean()
        left = sym_candidates.sort_values("date")
        merged = pd.merge_asof(
            pd.DataFrame({"date": pd.to_datetime(left["date"])}),
            pd.DataFrame({"date": pd.to_datetime(valid["date"]), "avg_vol5": valid["avg_vol5"].to_numpy()}),
            on="date",
            direction="backward",
            allow_exact_matches=False,
        )
        item = left.copy()
        item["avg_vol5"] = merged["avg_vol5"].to_numpy()
        parts.append(item)
    avg = pd.concat(parts, ignore_index=True) if parts else pd.DataFrame(columns=["symbol", "date", "avg_vol5"])
    out = candidates.merge(avg, on=["symbol", "date"], how="left")
    return out


def _build_prefilter(panel: pd.DataFrame, global_start: date, global_end: date, refresh: bool = False) -> pd.DataFrame:
    strategy_cfg = _load_yaml("strategy.yaml")["s1_tail"]
    cache_key = f"{global_start.isoformat()}_{global_end.isoformat()}_{S1_CACHE_VERSION}"
    cache_path = PREFILTER_CACHE.with_name(f"{PREFILTER_CACHE.stem}_{cache_key}.parquet")
    if cache_path.exists() and not refresh:
        out = pd.read_parquet(cache_path)
        out["date"] = pd.to_datetime(out["date"], errors="coerce").dt.date
        out["list_date"] = pd.to_datetime(out["list_date"], errors="coerce").dt.date
        out["delist_date"] = pd.to_datetime(out["delist_date"], errors="coerce").dt.date
        return out

    df = panel.copy().sort_values(["symbol", "date"]).reset_index(drop=True)
    limit_hit = (
        pd.to_numeric(df["close"], errors="coerce").notna()
        & pd.to_numeric(df["limit_up_price"], errors="coerce").notna()
        & (pd.to_numeric(df["close"], errors="coerce") >= pd.to_numeric(df["limit_up_price"], errors="coerce") * 0.999)
        & (~df["is_suspended"].astype(bool))
    )
    lookback = int(strategy_cfg["limit_up_lookback_days"])
    grouped = df.groupby("symbol", group_keys=False)
    df["prior_limit_hit"] = limit_hit.groupby(df["symbol"]).transform(
        lambda item: item.shift(1).rolling(lookback, min_periods=1).max().fillna(False).astype(bool)
    )
    df["prev_close"] = grouped["close"].shift(1)
    df["prev_float_mv"] = grouped["float_mv"].shift(1).ffill()
    df["prev_float_shares"] = np.where(df["prev_close"] > 0, df["prev_float_mv"] / df["prev_close"], np.nan)

    listed = df["list_date"].notna() & (df["list_date"] <= df["date"])
    not_delisted = df["delist_date"].isna() | (df["delist_date"] > df["date"])
    mask = (
        (df["date"] >= global_start)
        & (df["date"] <= global_end)
        & listed
        & not_delisted
        & df["prior_limit_hit"]
        & (pd.to_numeric(df["prev_float_mv"], errors="coerce") < float(strategy_cfg["float_mv_max"]))
        & (pd.to_numeric(df["prev_close"], errors="coerce") > 0)
        & (pd.to_numeric(df["prev_float_shares"], errors="coerce") > 0)
    )
    candidates = df.loc[
        mask,
        [
            "symbol",
            "date",
            "list_date",
            "delist_date",
            "is_delisted",
            "prev_close",
            "prev_float_mv",
            "prev_float_shares",
        ],
    ].copy()
    candidates = _add_prior_volume_context(df, candidates)
    candidates = candidates[pd.to_numeric(candidates["avg_vol5"], errors="coerce") > 0].copy()
    candidates = candidates.sort_values(["date", "symbol"]).reset_index(drop=True)
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    candidates.to_parquet(cache_path, index=False)
    return candidates


def _trade_dates(panel: pd.DataFrame, start: date, end: date) -> list[date]:
    return sorted(item for item in panel["date"].dropna().unique().tolist() if start <= item <= end)


def _candidate_clusters(candidates: pd.DataFrame, trade_dates: list[date]) -> list[dict[str, Any]]:
    index_by_date = {item: idx for idx, item in enumerate(trade_dates)}
    clusters: list[dict[str, Any]] = []
    for symbol, frame in candidates.groupby("symbol", sort=False):
        rows = frame.sort_values("date").to_dict("records")
        current: list[dict[str, Any]] = []
        previous_index: int | None = None
        for row in rows:
            idx = index_by_date.get(row["date"])
            if idx is None:
                continue
            starts_new = (
                not current
                or previous_index is None
                or idx != previous_index + 1
                or len(current) >= MAX_CLUSTER_TRADING_DAYS
            )
            if starts_new and current:
                clusters.append({"symbol": symbol, "rows": current})
                current = []
            current.append(row)
            previous_index = idx
        if current:
            clusters.append({"symbol": symbol, "rows": current})
    return clusters


def _cutoff_key(on_date: date, cutoff: str) -> str:
    return f"{on_date.strftime('%Y%m%d')}{cutoff.replace(':', '')}00000"


def _source_ts(time_text: str) -> str:
    ts = pd.to_datetime(str(time_text)[:14], format="%Y%m%d%H%M%S", errors="coerce")
    return "" if pd.isna(ts) else ts.isoformat(sep=" ")


def _snapshot_from_rows(row: dict[str, Any], raw_rows: list[list[str]], cutoff: str, elapsed_fraction: float) -> dict[str, Any]:
    symbol = str(row["symbol"])
    on_date = _parse_date(row["date"])
    key = _cutoff_key(on_date, cutoff)
    kept = [item for item in raw_rows if len(item) >= 9 and str(item[1]) <= key and str(item[0]) == on_date.isoformat()]
    if not kept:
        return _empty_snapshot(row, "no_bars_before_cutoff")
    assert max(str(item[1]) for item in kept) <= key

    close = np.array([float(item[6]) for item in kept], dtype="float64")
    high = np.array([float(item[5]) for item in kept], dtype="float64")
    volume = np.array([float(item[7]) for item in kept], dtype="float64")
    amount = np.array([float(item[8]) for item in kept], dtype="float64")
    cum_vol = np.cumsum(np.nan_to_num(volume, nan=0.0))
    cum_amount = np.cumsum(np.nan_to_num(amount, nan=0.0))
    with np.errstate(divide="ignore", invalid="ignore"):
        vwap = cum_amount / cum_vol
    valid = np.isfinite(vwap)
    if valid.any():
        last_valid = np.maximum.accumulate(np.where(valid, np.arange(len(vwap)), 0))
        vwap = vwap[last_valid]
    else:
        vwap = np.full_like(close, np.nan)

    before_highs = [float(item[5]) for item in kept if str(item[1]) <= f"{on_date.strftime('%Y%m%d')}143000000"]
    after_highs = [float(item[5]) for item in kept if f"{on_date.strftime('%Y%m%d')}143000000" < str(item[1]) <= key]
    before_high = max(before_highs) if before_highs else np.nan
    after_high = max(after_highs) if after_highs else np.nan

    prev_close = float(row["prev_close"])
    avg_vol5 = float(row["avg_vol5"])
    float_shares = float(row["prev_float_shares"])
    price = float(close[-1])
    if not math.isfinite(price) or price <= 0:
        return _empty_snapshot(row, "non_positive_price_at_1450")
    cum_vol_last = float(cum_vol[-1])
    pct_chg = price / prev_close - 1.0 if prev_close > 0 else np.nan
    vol_ratio = cum_vol_last / (avg_vol5 * elapsed_fraction) if avg_vol5 > 0 and elapsed_fraction > 0 else np.nan
    turnover = cum_vol_last / float_shares if float_shares > 0 else np.nan
    above_vwap = bool(np.isfinite(vwap).any() and np.all(close[np.isfinite(vwap)] >= vwap[np.isfinite(vwap)]))

    return {
        "symbol": symbol,
        "date": on_date.isoformat(),
        "fetch_ok": True,
        "error": "",
        "source_max_ts": _source_ts(str(kept[-1][1])),
        "price_at_1450": price,
        "pct_chg_at_1450": float(pct_chg),
        "cum_vol_at_1450": cum_vol_last,
        "vol_ratio_at_1450": float(vol_ratio),
        "turnover_at_1450": float(turnover),
        "vwap_at_1450": float(vwap[-1]) if np.isfinite(vwap[-1]) else np.nan,
        "is_above_vwap": above_vwap,
        "high_after_1430": bool(pd.notna(after_high) and pd.notna(before_high) and after_high > before_high),
        "high_after_1430_price": float(after_high) if pd.notna(after_high) else np.nan,
    }


def _empty_snapshot(row: dict[str, Any], error: str) -> dict[str, Any]:
    return {
        "symbol": str(row["symbol"]),
        "date": _parse_date(row["date"]).isoformat(),
        "fetch_ok": False,
        "error": error,
        "source_max_ts": "",
        "price_at_1450": np.nan,
        "pct_chg_at_1450": np.nan,
        "cum_vol_at_1450": np.nan,
        "vol_ratio_at_1450": np.nan,
        "turnover_at_1450": np.nan,
        "vwap_at_1450": np.nan,
        "is_above_vwap": False,
        "high_after_1430": False,
        "high_after_1430_price": np.nan,
    }


def _query_cluster(cluster: dict[str, Any], cutoff: str, elapsed_fraction: float) -> list[dict[str, Any]]:
    rows = cluster["rows"]
    symbol = str(cluster["symbol"])
    start = min(_parse_date(item["date"]) for item in rows)
    end = max(_parse_date(item["date"]) for item in rows)
    last_error = ""
    result = None
    for attempt in range(1, BAOSTOCK_ATTEMPTS + 1):
        result = bs.query_history_k_data_plus(
            _baostock_symbol(symbol),
            BAOSTOCK_5MIN_FIELDS,
            start_date=start.isoformat(),
            end_date=end.isoformat(),
            frequency="5",
            adjustflag="3",
        )
        if result.error_code == "0":
            break
        last_error = f"{result.error_code}:{result.error_msg}"
        if result.error_code == "10001001":
            with contextlib.redirect_stdout(io.StringIO()):
                bs.login()
            _set_baostock_socket_timeout()
        time.sleep(0.5 * attempt)
    if result is None or result.error_code != "0":
        return [_empty_snapshot(item, last_error or "baostock_error") for item in rows]

    raw_by_date: dict[str, list[list[str]]] = {}
    for raw in result.data:
        if len(raw) >= 2:
            raw_by_date.setdefault(str(raw[0]), []).append(raw)
    out = []
    for item in rows:
        on_date = _parse_date(item["date"]).isoformat()
        out.append(_snapshot_from_rows(item, raw_by_date.get(on_date, []), cutoff, elapsed_fraction))
    return out


def _load_snapshot_cache() -> pd.DataFrame:
    if not SNAPSHOT_CACHE.exists():
        return pd.DataFrame(columns=SNAPSHOT_COLUMNS)
    out = pd.read_parquet(SNAPSHOT_CACHE)
    out["date"] = pd.to_datetime(out["date"], errors="coerce").dt.date
    out["symbol"] = out["symbol"].astype(str).str.zfill(6)
    return out


def _write_snapshot_cache(frame: pd.DataFrame) -> None:
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    out = frame.copy()
    out["date"] = pd.to_datetime(out["date"], errors="coerce").dt.date
    out = out.drop_duplicates(["symbol", "date"], keep="last").sort_values(["date", "symbol"])
    out.to_parquet(SNAPSHOT_CACHE, index=False)


def _snapshot_key(frame: pd.DataFrame) -> pd.Series:
    return frame["symbol"].astype(str).str.zfill(6) + "|" + pd.to_datetime(frame["date"], errors="coerce").dt.date.astype(str)


def _fetch_snapshots(
    candidates: pd.DataFrame,
    trade_dates: list[date],
    cutoff: str,
    refresh: bool,
    max_clusters: int | None = None,
) -> tuple[pd.DataFrame, int, int, float]:
    cache = pd.DataFrame(columns=SNAPSHOT_COLUMNS) if refresh else _load_snapshot_cache()
    cached_keys = set(_snapshot_key(cache).tolist()) if not cache.empty else set()
    missing = candidates[~_snapshot_key(candidates).isin(cached_keys)].copy()
    if missing.empty:
        return cache, len(_candidate_clusters(candidates, trade_dates)), 0, 0.0

    clusters = _candidate_clusters(missing, trade_dates)
    total_clusters = len(clusters)
    if max_clusters is not None:
        clusters = clusters[:max_clusters]
    elapsed_fraction = _elapsed_trading_fraction(cutoff)
    fetched: list[pd.DataFrame] = []
    started = time.monotonic()
    with contextlib.redirect_stdout(io.StringIO()):
        login = bs.login()
    if login.error_code != "0":
        raise RuntimeError(f"BaoStock login failed: {login.error_code} {login.error_msg}")
    _set_baostock_socket_timeout()
    try:
        for index in range(0, len(clusters), SNAPSHOT_BATCH_CLUSTERS):
            batch = clusters[index : index + SNAPSHOT_BATCH_CLUSTERS]
            rows: list[dict[str, Any]] = []
            for cluster in batch:
                rows.extend(_query_cluster(cluster, cutoff, elapsed_fraction))
            if rows:
                part = pd.DataFrame(rows, columns=SNAPSHOT_COLUMNS)
                fetched.append(part)
                combined = pd.concat([cache, *fetched], ignore_index=True)
                _write_snapshot_cache(combined)
            done = min(index + SNAPSHOT_BATCH_CLUSTERS, len(clusters))
            print(
                f"s1_baostock_snapshot_progress clusters={done}/{len(clusters)} "
                f"new_rows={sum(len(item) for item in fetched)} cache_rows={len(cache) + sum(len(item) for item in fetched)}",
                flush=True,
            )
    finally:
        with contextlib.redirect_stdout(io.StringIO()):
            bs.logout()
    seconds = time.monotonic() - started
    final = _load_snapshot_cache()
    return final, total_clusters, len(clusters), seconds


def _merge_signals(candidates: pd.DataFrame, snapshots: pd.DataFrame) -> pd.DataFrame:
    left = candidates.copy()
    right = snapshots.copy()
    left["date"] = pd.to_datetime(left["date"], errors="coerce").dt.date
    right["date"] = pd.to_datetime(right["date"], errors="coerce").dt.date
    out = left.merge(right, on=["symbol", "date"], how="left")
    out["fetch_ok"] = out["fetch_ok"].fillna(False).astype(bool)
    for col in ("is_above_vwap", "high_after_1430"):
        out[col] = out[col].fillna(False).astype(bool)
    return out


def _eligible_s1_rows(signal_base: pd.DataFrame) -> pd.DataFrame:
    cfg = _load_yaml("strategy.yaml")["s1_tail"]
    rows = signal_base[signal_base["fetch_ok"]].copy()
    mask = (
        (pd.to_numeric(rows["pct_chg_at_1450"], errors="coerce") >= float(cfg["pct_change_min"]))
        & (pd.to_numeric(rows["pct_chg_at_1450"], errors="coerce") <= float(cfg["pct_change_max"]))
        & (pd.to_numeric(rows["vol_ratio_at_1450"], errors="coerce") > float(cfg["volume_ratio_min"]))
        & (pd.to_numeric(rows["turnover_at_1450"], errors="coerce") >= float(cfg["turnover_min"]))
        & (pd.to_numeric(rows["turnover_at_1450"], errors="coerce") <= float(cfg["turnover_max"]))
    )
    if bool(cfg.get("require_above_vwap", True)):
        mask &= rows["is_above_vwap"].astype(bool)
    if bool(cfg.get("require_new_high_after_1430", True)):
        mask &= rows["high_after_1430"].astype(bool)
    return rows[mask].copy()


def _select_s1(signal_base: pd.DataFrame) -> pd.DataFrame:
    max_positions = int(_load_yaml("strategy.yaml")["s1_tail"]["max_positions"])
    eligible = _eligible_s1_rows(signal_base)
    if eligible.empty:
        return eligible
    return (
        eligible.sort_values(["date", "pct_chg_at_1450", "vol_ratio_at_1450", "symbol"], ascending=[True, False, False, True])
        .groupby("date", group_keys=False)
        .head(max_positions)
        .reset_index(drop=True)
    )


def _select_random(signal_base: pd.DataFrame) -> pd.DataFrame:
    max_positions = int(_load_yaml("strategy.yaml")["s1_tail"]["max_positions"])
    eligible = _eligible_s1_rows(signal_base)
    if eligible.empty:
        return eligible
    parts = []
    for on_date, frame in eligible.groupby("date", sort=True):
        rng = np.random.default_rng(RANDOM_SEED + _parse_date(on_date).toordinal())
        rows = frame.sort_values("symbol").reset_index(drop=True)
        take = min(max_positions, len(rows))
        picked = sorted(rng.choice(np.arange(len(rows)), size=take, replace=False).tolist())
        parts.append(rows.iloc[picked])
    return pd.concat(parts, ignore_index=True) if parts else eligible.head(0)


def _select_prefilter_equal(signal_base: pd.DataFrame) -> pd.DataFrame:
    return signal_base[signal_base["fetch_ok"]].copy()


def _position_for_symbol(positions: tuple[Position, ...], symbol: str) -> Position | None:
    for item in positions:
        if item.symbol == symbol:
            return item
    return None


def _data_by_symbol(panel: pd.DataFrame) -> dict[str, pd.DataFrame]:
    out = {}
    for symbol, frame in panel.groupby("symbol", sort=False):
        item = frame.copy()
        item["date"] = pd.to_datetime(item["date"], errors="coerce").dt.date
        out[str(symbol)] = item.sort_values("date").reset_index(drop=True)
    return out


def _row_on(data: dict[str, pd.DataFrame], symbol: str, trade_date: date) -> pd.Series | None:
    frame = data.get(symbol)
    if frame is None or frame.empty:
        return None
    rows = frame[frame["date"] == trade_date]
    if rows.empty:
        return None
    return rows.iloc[-1]


def _last_close(data: dict[str, pd.DataFrame], symbol: str, as_of_date: date) -> float | None:
    frame = data.get(symbol)
    if frame is None or frame.empty:
        return None
    rows = frame[frame["date"] <= as_of_date]
    if rows.empty:
        return None
    value = pd.to_numeric(rows.iloc[-1]["close"], errors="coerce")
    return None if pd.isna(value) else float(value)


def _market_bar(data: dict[str, pd.DataFrame], symbol: str, trade_date: date) -> MarketBar:
    row = _row_on(data, symbol, trade_date)
    if row is None:
        last = _last_close(data, symbol, trade_date)
        return MarketBar(symbol=symbol, date=trade_date, open=float(last or 0.0), is_suspended=True)
    return MarketBar(
        symbol=symbol,
        date=trade_date,
        open=float(row["open"]),
        limit_up_price=None if pd.isna(row.get("limit_up_price")) else float(row.get("limit_up_price")),
        limit_down_price=None if pd.isna(row.get("limit_down_price")) else float(row.get("limit_down_price")),
        is_suspended=bool(row.get("is_suspended", False)),
    )


def _mark_nav(cash: float, positions: tuple[Position, ...], data: dict[str, pd.DataFrame], as_of_date: date) -> float:
    nav = cash
    for item in positions:
        close = _last_close(data, item.symbol, as_of_date)
        if close is not None:
            nav += item.quantity * close
    return nav


def _selected_by_date(selected: pd.DataFrame, start: date, end: date) -> dict[date, pd.DataFrame]:
    if selected.empty:
        return {}
    rows = selected.copy()
    rows["date"] = pd.to_datetime(rows["date"], errors="coerce").dt.date
    rows = rows[(rows["date"] >= start) & (rows["date"] <= end)].copy()
    return {on_date: frame.copy() for on_date, frame in rows.groupby("date", sort=True)}


def _run_overnight_backtest(
    name: str,
    regime: str,
    start: date,
    end: date,
    data: dict[str, pd.DataFrame],
    selected: pd.DataFrame,
    max_positions: int | None,
    skip_buys_without_next_open: bool = True,
) -> tuple[BacktestRun, int]:
    dates = sorted({item for frame in data.values() for item in frame["date"].tolist() if start <= item <= end})
    if len(dates) < 2:
        raise RuntimeError(f"Not enough S1 dates for {regime}")
    selected_map = _selected_by_date(selected, start, end)
    cost_config = CostConfig.from_mapping(_load_yaml("cost.yaml"))
    cash = INITIAL_CASH
    positions: tuple[Position, ...] = ()
    basis: dict[str, float] = {}
    trades: list[TradeRecord] = []
    filled_orders: list[dict[str, Any]] = []
    rejected_orders: list[dict[str, Any]] = []
    events: list[dict[str, Any]] = []
    nav_rows = [{"date": dates[0].isoformat(), "nav": cash}]
    skipped_no_next = 0

    for idx, trade_date in enumerate(dates):
        positions = mark_sellable(positions, trade_date)
        for position in sorted(positions, key=lambda item: item.symbol):
            if not position.sellable:
                continue
            result = match_order(
                Order(symbol=position.symbol, side="sell", quantity=position.quantity, submitted_date=trade_date),
                _market_bar(data, position.symbol, trade_date),
                cost_config,
                position=position,
            )
            if result.status == "filled":
                _apply_basis_on_fill(basis, result, position, trade_date, trades)
                cash += result.cash_delta
                positions = apply_execution(positions, result, trade_date)
                filled_orders.append(_record_execution(result, trade_date))
            else:
                rejected_orders.append(_record_execution(result, trade_date))
            events.extend(result.events)

        rows = selected_map.get(trade_date)
        if rows is not None and not rows.empty:
            if skip_buys_without_next_open and idx >= len(dates) - 1:
                skipped_no_next += len(rows)
            else:
                current_symbols = {item.symbol for item in positions if item.quantity > 0}
                rows = rows[~rows["symbol"].astype(str).isin(current_symbols)].copy()
                if max_positions is not None:
                    slots = max(0, max_positions - len(current_symbols))
                    rows = rows.head(slots)
                if not rows.empty:
                    nav = _mark_nav(cash, positions, data, trade_date)
                    denominator = max_positions if max_positions is not None else max(1, len(rows))
                    target_value = nav / denominator
                    for row in rows.sort_values("symbol").itertuples(index=False):
                        base_price = float(row.price_at_1450)
                        if not math.isfinite(base_price) or base_price <= 0:
                            continue
                        desired = int(math.floor((target_value / base_price) / LOT_SIZE) * LOT_SIZE)
                        quantity = _affordable_quantity(desired, base_price, cash, cost_config)
                        if quantity <= 0:
                            continue
                        result = match_order(
                            Order(symbol=str(row.symbol), side="buy", quantity=quantity, submitted_date=trade_date),
                            MarketBar(symbol=str(row.symbol), date=trade_date, open=base_price, is_suspended=False),
                            cost_config,
                            position=None,
                        )
                        if result.status == "filled":
                            _apply_basis_on_fill(basis, result, None, trade_date, trades)
                            cash += result.cash_delta
                            positions = apply_execution(positions, result, trade_date)
                            filled_orders.append(_record_execution(result, trade_date))
                        else:
                            rejected_orders.append(_record_execution(result, trade_date))
                        events.extend(result.events)
        nav_rows.append({"date": trade_date.isoformat(), "nav": _mark_nav(cash, positions, data, trade_date)})

    final_date = dates[-1]
    final_nav = _mark_nav(cash, positions, data, final_date)
    nav_rows[-1] = {"date": final_date.isoformat(), "nav": final_nav}
    nav_curve = pd.DataFrame(nav_rows).drop_duplicates("date", keep="last")
    run = BacktestRun(
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
    return run, skipped_no_next


def _comparison_table(all_runs: dict[str, dict[str, BacktestRun]]) -> str:
    lines = [
        "| regime | metric | S1 | random_2_same_eligible | prefilter_equal | S1/random | S1/prefilter | note |",
        "|---|---|---:|---:|---:|---:|---:|---|",
    ]
    for regime in ("bull", "bear", "range", "oos"):
        s1 = summarize_run(all_runs[regime]["s1"])
        rnd = summarize_run(all_runs[regime]["random"])
        pre = summarize_run(all_runs[regime]["prefilter_equal"])
        for metric, pct in (("return", True), ("max_drawdown", True), ("trades", False), ("fee_ratio", True)):
            left = s1[metric]
            right_random = rnd[metric]
            right_pre = pre[metric]
            r1 = left / right_random if abs(right_random) > 1e-12 else math.nan
            r2 = left / right_pre if abs(right_pre) > 1e-12 else math.nan
            fmt = _fmt_pct if pct else _fmt_float
            note = "ratio>2x需调查" if any(abs(x) > 2 for x in (r1, r2) if not pd.isna(x) and not math.isinf(x)) else ""
            lines.append(f"| {regime} | {metric} | {fmt(left)} | {fmt(right_random)} | {fmt(right_pre)} | {_fmt_float(r1)} | {_fmt_float(r2)} | {note} |")
    return "\n".join(lines)


def _summary_table(runs: dict[str, BacktestRun]) -> str:
    lines = [
        "| regime | return | max_drawdown | trades | expectancy_after_cost | profit_factor | win_rate | fee_ratio | forced_hold_events |",
        "|---|---:|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for name in ("bull", "bear", "range", "oos"):
        metrics = summarize_run(runs[name])
        forced = sum(1 for item in runs[name].events if item.get("type") == "forced_hold")
        lines.append(
            f"| {name} | {_fmt_pct(metrics['return'])} | {_fmt_pct(metrics['max_drawdown'])} | {int(metrics['trades'])} | "
            f"{metrics['expectancy']:.2f} | {_fmt_float(metrics['profit_factor'])} | {_fmt_pct(metrics['win_rate'])} | "
            f"{_fmt_pct(metrics['fee_ratio'])} | {forced} |"
        )
    return "\n".join(lines)


def _forced_hold_table(runs: dict[str, BacktestRun]) -> str:
    lines = ["| regime | sell_rejections | forced_hold_events | forced_hold占卖单拒单 |", "|---|---:|---:|---:|"]
    for name in ("bull", "bear", "range", "oos"):
        rejected_sells = [item for item in runs[name].rejected_orders if item["side"] == "sell"]
        forced = [item for item in runs[name].events if item.get("type") == "forced_hold"]
        ratio = len(forced) / len(rejected_sells) if rejected_sells else 0.0
        lines.append(f"| {name} | {len(rejected_sells)} | {len(forced)} | {_fmt_pct(ratio)} |")
    return "\n".join(lines)


def _data_info_lines(info: S1DataInfo) -> list[str]:
    return [
        f"- BaoStock version={_baostock_version()}。",
        f"- daily_panel_source={info.daily_panel_source}, rows={info.daily_panel_rows}, symbols={info.daily_panel_symbols}, active_symbols={info.active_symbols}, delisted_symbols={info.delisted_symbols}。",
        f"- Gate1 signal span={info.global_start}..{info.global_end}；日线预筛只用 D-1 及以前派生字段。",
        f"- prefilter rows={info.prefilter_rows}, dates={info.prefilter_dates}, symbols={info.prefilter_symbols}, per_day min/median/max={info.prefilter_min_per_day}/{info.prefilter_median_per_day:.1f}/{info.prefilter_max_per_day}。",
        f"- delisted evidence: prefilter_rows={info.delisted_prefilter_rows}, symbols={info.delisted_prefilter_symbols}, sample={', '.join(info.delisted_prefilter_sample) if info.delisted_prefilter_sample else 'none'}。",
        f"- BaoStock snapshot cache rows={info.snapshot_rows}, ok={info.snapshot_ok_rows}, failed={info.snapshot_failed_rows}, failures_sample={', '.join(info.snapshot_failures_sample) if info.snapshot_failures_sample else 'none'}。",
        f"- snapshot clusters total={info.snapshot_clusters_total}, fetched_this_run={info.snapshot_clusters_fetched}, fetch_seconds={info.snapshot_fetch_seconds:.1f}。",
        f"- skipped_signal_rows_no_next_open={info.skipped_signal_rows_no_next_open}；无下一交易日开盘数据时不新开隔夜仓，避免用收盘强平替代 S1 卖出模型。",
    ]


def _overfit_table(s1_runs: dict[str, BacktestRun]) -> str:
    in_trades = tuple(item for name in ("bull", "bear", "range") for item in s1_runs[name].trades)
    in_metrics = _trade_metrics(in_trades)
    oos_metrics = summarize_run(s1_runs["oos"])
    avg_in_return = float(np.mean([s1_runs[name].total_return for name in ("bull", "bear", "range")]))
    lines = [
        "| span | avg/period_return | trades | expectancy | profit_factor | win_rate | max_drawdown_worst |",
        "|---|---:|---:|---:|---:|---:|---:|",
        f"| in_sample(bull+bear+range) | {_fmt_pct(avg_in_return)} | {int(in_metrics['trades'])} | {in_metrics['expectancy']:.2f} | {_fmt_float(in_metrics['profit_factor'])} | {_fmt_pct(in_metrics['win_rate'])} | {_fmt_pct(max(s1_runs[n].max_drawdown for n in ('bull','bear','range')))} |",
        f"| oos | {_fmt_pct(oos_metrics['return'])} | {int(oos_metrics['trades'])} | {oos_metrics['expectancy']:.2f} | {_fmt_float(oos_metrics['profit_factor'])} | {_fmt_pct(oos_metrics['win_rate'])} | {_fmt_pct(oos_metrics['max_drawdown'])} |",
    ]
    return "\n".join(lines)


def render_report(all_runs: dict[str, dict[str, BacktestRun]], info: S1DataInfo) -> str:
    s1_runs = {regime: runs["s1"] for regime, runs in all_runs.items()}
    checks = _gate_checks(s1_runs, _load_yaml("backtest.yaml")["gate1"])
    overall = "PASS" if checks["overall_pass"] else "FAIL"
    lines = [
        "# S1 Tail 5min PIT Gate1 Report",
        "",
        "参数：严格使用 `configs/strategy.yaml` 的 `s1_tail`，未改 pct/量比/换手/VWAP/14:30后新高/max_positions。",
        "信号在 D 日 14:50 生成，只读取当日 BaoStock 5min `time<=14:50` bar 和 D-1 及更早日线字段；OOS 未用于调参。",
        "",
        "## 数据与 PIT 证据",
        *_data_info_lines(info),
        "",
        "## S1 分段关键指标",
        _summary_table(s1_runs),
        "",
        "## forced_hold 占比",
        _forced_hold_table(s1_runs),
        "",
        "## in-sample vs OOS 差异",
        _overfit_table(s1_runs),
        "",
        "## 对照组 ratio 表",
        _comparison_table(all_runs),
        "",
        "## 反假设列表",
        "- 5min 近似偏差：price_at_1450 用 14:50 endpoint bar close，不是 tick；VWAP/全程在均价线上方看不到 5min 内跌破，14:30 后新高也不知道 bar 内先后。偏差方向偏乐观，可能高估可执行性。",
        "- 次日跌停/停牌卖不掉：卖出完全复用 constraints.py；一字跌停或停牌产生 forced_hold 并顺延下一可成交开盘，上表披露 forced_hold 占比。",
        "- 幸存者偏差：日线面板含 delisted 标记，预筛中实际出现退市股；上方列出退市候选行数和样例。仍承认免费数据的退市日线质量弱于在市股。",
        "- edge 是否只是小盘/低价 beta：预筛本身限定流通市值 <200亿且近4日涨停，报告加入同 S1 eligible 随机2只与预筛者等权隔夜对照；若 S1 不优于对照，不能声称有独立 alpha。",
        "",
        "## flag/参数调查记录",
        "- 本轮没有修改 `configs/strategy.yaml`、`configs/backtest.yaml` 或 `configs/cost.yaml`。",
        "- 未触碰 OOS 调参；OOS 只在本次固定规则跑完后用于 C 组最终裁决。",
        "- 没有使用 D 日收盘涨跌幅、收盘换手或 14:50 之后的任何分钟 bar 做预筛或信号。",
        "",
        "## Gate1 判定表",
        _gate1_table(checks, _load_yaml("backtest.yaml")["gate1"]),
        "",
        f"最终判定：{overall}",
    ]
    return "\n".join(lines) + "\n"


def write_report(all_runs: dict[str, dict[str, BacktestRun]], info: S1DataInfo) -> Path:
    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    path = REPORT_DIR / "s1_gate1.md"
    path.write_text(render_report(all_runs, info), encoding="utf-8")
    return path


def _build_data_info(
    panel: pd.DataFrame,
    candidates: pd.DataFrame,
    snapshots: pd.DataFrame,
    global_start: date,
    global_end: date,
    total_clusters: int,
    fetched_clusters: int,
    fetch_seconds: float,
    skipped_signal_rows_no_next_open: int,
) -> S1DataInfo:
    counts = candidates.groupby("date")["symbol"].nunique() if not candidates.empty else pd.Series(dtype="int64")
    delisted_prefilter = candidates[candidates["is_delisted"].astype(bool)] if not candidates.empty else pd.DataFrame()
    sample = tuple(
        delisted_prefilter[["symbol", "date"]]
        .drop_duplicates("symbol")
        .sort_values(["symbol", "date"])
        .head(10)
        .assign(text=lambda x: x["symbol"].astype(str) + "@" + x["date"].astype(str))["text"]
        .tolist()
    )
    failures = snapshots[~snapshots["fetch_ok"].astype(bool)] if not snapshots.empty else pd.DataFrame()
    failure_sample = tuple(
        failures[["symbol", "date", "error"]]
        .head(10)
        .assign(text=lambda x: x["symbol"].astype(str) + "@" + x["date"].astype(str) + ":" + x["error"].astype(str))["text"]
        .tolist()
    )
    active_symbols = int(panel.loc[~panel["is_delisted"].astype(bool), "symbol"].nunique())
    delisted_symbols = int(panel.loc[panel["is_delisted"].astype(bool), "symbol"].nunique())
    return S1DataInfo(
        daily_panel_source=str(S2_PIT_PANEL),
        daily_panel_rows=len(panel),
        daily_panel_symbols=int(panel["symbol"].nunique()),
        active_symbols=active_symbols,
        delisted_symbols=delisted_symbols,
        global_start=global_start.isoformat(),
        global_end=global_end.isoformat(),
        prefilter_rows=len(candidates),
        prefilter_dates=int(candidates["date"].nunique()) if not candidates.empty else 0,
        prefilter_symbols=int(candidates["symbol"].nunique()) if not candidates.empty else 0,
        prefilter_min_per_day=int(counts.min()) if not counts.empty else 0,
        prefilter_median_per_day=float(counts.median()) if not counts.empty else 0.0,
        prefilter_max_per_day=int(counts.max()) if not counts.empty else 0,
        delisted_prefilter_rows=len(delisted_prefilter),
        delisted_prefilter_symbols=int(delisted_prefilter["symbol"].nunique()) if not delisted_prefilter.empty else 0,
        delisted_prefilter_sample=sample,
        snapshot_rows=len(snapshots),
        snapshot_ok_rows=int(snapshots["fetch_ok"].astype(bool).sum()) if not snapshots.empty else 0,
        snapshot_failed_rows=int((~snapshots["fetch_ok"].astype(bool)).sum()) if not snapshots.empty else 0,
        snapshot_failures_sample=failure_sample,
        snapshot_clusters_total=total_clusters,
        snapshot_clusters_fetched=fetched_clusters,
        snapshot_fetch_seconds=float(fetch_seconds),
        skipped_signal_rows_no_next_open=skipped_signal_rows_no_next_open,
    )


def run_s1_gate1(
    refresh_prefilter: bool = False,
    refresh_snapshots: bool = False,
    max_clusters: int | None = None,
) -> tuple[dict[str, dict[str, BacktestRun]], S1DataInfo]:
    backtest_cfg = _load_yaml("backtest.yaml")
    global_start, global_end = _trade_date_set(backtest_cfg)
    panel = _load_daily_panel(global_start, global_end)
    candidates = _build_prefilter(panel, global_start, global_end, refresh=refresh_prefilter)
    trade_dates = _trade_dates(panel, global_start, global_end)
    snapshots, total_clusters, fetched_clusters, fetch_seconds = _fetch_snapshots(
        candidates,
        trade_dates,
        _load_yaml("strategy.yaml")["s1_tail"]["decision_time"],
        refresh=refresh_snapshots,
        max_clusters=max_clusters,
    )
    signal_base = _merge_signals(candidates, snapshots)
    s1_selected = _select_s1(signal_base)
    random_selected = _select_random(signal_base)
    prefilter_selected = _select_prefilter_equal(signal_base)
    data = _data_by_symbol(panel)

    all_runs: dict[str, dict[str, BacktestRun]] = {}
    skipped_total = 0
    max_positions = int(_load_yaml("strategy.yaml")["s1_tail"]["max_positions"])
    for regime, span in backtest_cfg["regimes"].items():
        start = _parse_date(span["start"])
        end = _parse_date(span["end"])
        s1_run, skipped = _run_overnight_backtest("s1", regime, start, end, data, s1_selected, max_positions)
        skipped_total += skipped
        random_run, skipped = _run_overnight_backtest("s1_random_2", regime, start, end, data, random_selected, max_positions)
        skipped_total += skipped
        prefilter_run, skipped = _run_overnight_backtest(
            "s1_prefilter_equal",
            regime,
            start,
            end,
            data,
            prefilter_selected,
            max_positions=None,
        )
        skipped_total += skipped
        all_runs[regime] = {"s1": s1_run, "random": random_run, "prefilter_equal": prefilter_run}

    info = _build_data_info(
        panel,
        candidates,
        snapshots,
        global_start,
        global_end,
        total_clusters,
        fetched_clusters,
        fetch_seconds,
        skipped_total,
    )
    write_report(all_runs, info)
    return all_runs, info


def main() -> None:
    parser = argparse.ArgumentParser(description="Run S1 BaoStock 5min PIT Gate1")
    parser.add_argument("--refresh-prefilter", action="store_true")
    parser.add_argument("--refresh-snapshots", action="store_true")
    parser.add_argument("--max-clusters", type=int, default=None, help="debug only; do not use for final Gate1")
    args = parser.parse_args()
    all_runs, info = run_s1_gate1(
        refresh_prefilter=args.refresh_prefilter,
        refresh_snapshots=args.refresh_snapshots,
        max_clusters=args.max_clusters,
    )
    checks = _gate_checks({regime: runs["s1"] for regime, runs in all_runs.items()}, _load_yaml("backtest.yaml")["gate1"])
    s1_trades = sum(len(runs["s1"].trades) for runs in all_runs.values())
    print(
        json.dumps(
            {
                "baostock": _baostock_version(),
                "s1_trades": s1_trades,
                "final": "PASS" if checks["overall_pass"] else "FAIL",
                "data_info": asdict(info),
            },
            ensure_ascii=False,
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
