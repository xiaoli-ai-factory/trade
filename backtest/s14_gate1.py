"""S14 convertible bond double-low Gate1 runner."""

from __future__ import annotations

import argparse
import math
import signal
import time
from bisect import bisect_right
from dataclasses import dataclass, replace
from datetime import date, timedelta
from pathlib import Path
from typing import Any, Callable

import akshare as ak
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
    slippage_price,
    trade_cost,
)
from backtest.engine import (
    CONFIG_DIR,
    INITIAL_CASH,
    RANDOM_SEED,
    REPORT_DIR,
    BacktestRun,
    TradeRecord,
    _apply_basis_on_fill,
    _fmt_float,
    _fmt_pct,
    _load_yaml,
    _max_drawdown,
    _record_execution,
    _trade_metrics,
    summarize_run,
)
from backtest.s9_gate1 import (
    EffectiveSpan,
    _common_dates,
    _effective_span_table,
    _insample_oos_table,
    _month_end_dates,
    _single_etf_buy_hold_signal,
    run_monthly_backtest,
)
from data.akshare_source import CACHE_DIR, get_etf_daily
from strategies.s14_double_low_bond import S14DoubleLowBondStrategy, orders_for_target_weights


PANEL_START = date(2020, 1, 1)
PANEL_END = date(2026, 5, 21)
PANEL_CACHE = CACHE_DIR / "cb_panel_pit_2020_2026.parquet"
PANEL_META_CACHE = CACHE_DIR / "cb_panel_pit_2020_2026_meta.parquet"
CB_LOT_SIZE = 10
REGIMES = ("bull", "bear", "range", "oos")
S14_NAMES = {
    "s14": "S14_top10_double_low",
    "all_candidates": "all_filtered_equal_weight",
    "random10": "random10_filtered",
    "hs300_buy_hold": "HS300ETF_510300_BH",
    "s12": "S12_global_risk_parity",
}

SignalFunc = Callable[[date, dict[str, Any]], list[Order]]
_PRICE_LOOKUP: dict[str, tuple[list[date], list[float]]] = {}


@dataclass(frozen=True)
class PanelBuildStats:
    universe_raw: int
    attempted: int
    included: int
    failed: int
    failed_symbols: tuple[str, ...]
    rows: int
    start: date
    end: date
    elapsed_seconds: float
    cached: bool

    @property
    def fail_rate(self) -> float:
        return self.failed / self.attempted if self.attempted else 0.0


def _strategy_cfg() -> dict[str, Any]:
    return _load_yaml("strategy_addon.yaml")["s14_convertible_bond_double_low"].copy()


def _gate_cfg() -> dict[str, Any]:
    return _load_yaml("backtest.yaml")["gate1"]


def _regime_cfg() -> dict[str, Any]:
    return _load_yaml("backtest.yaml")["regimes"]


def _parse_date(value: str | date | pd.Timestamp | None) -> date | None:
    if value is None or pd.isna(value):
        return None
    if isinstance(value, pd.Timestamp):
        return value.date()
    if isinstance(value, date):
        return value
    return pd.Timestamp(str(value)).date()


def _s14_cost_config(cfg: dict[str, Any]) -> CostConfig:
    cost = cfg["cost_model"]
    return CostConfig(
        commission_rate=float(cost["commission_rate"]),
        min_per_order=float(cost["min_per_order"]),
        stamp_sell_rate=float(cost["stamp_duty_sell"]),
        transfer_rate=float(cost["transfer_fee_rate"]),
        slippage_rate=float(cost["slippage_rate"]),
    )


def _normalize_code(value: Any) -> str:
    text = str(value).strip().lower()
    if text.startswith(("sh", "sz", "bj")):
        text = text[2:]
    return text.zfill(6) if text.isdigit() else text


def _market_symbol(code: str) -> str:
    code = _normalize_code(code)
    if code.startswith(("110", "111", "113", "118")):
        return f"sh{code}"
    if code.startswith(("123", "127", "128")):
        return f"sz{code}"
    if code.startswith(("810", "830", "831")):
        return f"bj{code}"
    return f"sh{code}" if code.startswith("11") else f"sz{code}"


def _is_hs_convertible(code: str) -> bool:
    code = _normalize_code(code)
    return len(code) == 6 and code.startswith(("110", "111", "113", "118", "123", "127", "128"))


class _CallTimeout(TimeoutError):
    pass


def _timeout_handler(_signum: int, _frame: Any) -> None:
    raise _CallTimeout("akshare call timeout")


def _call_with_retry(
    func: Callable[..., pd.DataFrame],
    *args: Any,
    attempts: int = 3,
    sleep_seconds: float = 0.5,
    timeout_seconds: int = 20,
    **kwargs: Any,
) -> pd.DataFrame:
    last_exc: Exception | None = None
    for attempt in range(1, attempts + 1):
        try:
            previous_handler = signal.signal(signal.SIGALRM, _timeout_handler)
            signal.alarm(timeout_seconds)
            result = func(*args, **kwargs)
            signal.alarm(0)
            signal.signal(signal.SIGALRM, previous_handler)
            time.sleep(sleep_seconds)
            return result
        except Exception as exc:
            signal.alarm(0)
            signal.signal(signal.SIGALRM, signal.SIG_DFL)
            last_exc = exc
            if attempt < attempts:
                time.sleep(sleep_seconds * attempt * 2.0)
    raise RuntimeError(f"{func.__name__} failed") from last_exc


def _cached_raw_path(kind: str, code: str) -> Path:
    return CACHE_DIR / f"cb_{kind}__{_normalize_code(code)}__2020_2026.parquet"


def _load_universe(refresh: bool = False) -> pd.DataFrame:
    frames: list[pd.DataFrame] = []
    if refresh or not (CACHE_DIR / "cb_universe_s14.parquet").exists():
        zh = _call_with_retry(ak.bond_zh_cov)
        zh = zh.rename(
            columns={
                "债券代码": "symbol",
                "债券简称": "name",
                "上市时间": "listing_date",
                "发行规模": "issue_size",
                "到期时间": "maturity_date",
            }
        )
        cols = [item for item in ["symbol", "name", "listing_date", "issue_size", "maturity_date"] if item in zh.columns]
        frames.append(zh[cols].copy())

        try:
            ths = _call_with_retry(ak.bond_zh_cov_info_ths)
            ths = ths.rename(
                columns={
                    "债券代码": "symbol",
                    "债券简称": "name",
                    "上市日期": "listing_date",
                    "实际发行量": "issue_size",
                    "到期时间": "maturity_date",
                }
            )
            cols = [item for item in ["symbol", "name", "listing_date", "issue_size", "maturity_date"] if item in ths.columns]
            frames.append(ths[cols].copy())
        except Exception:
            pass

        try:
            redeem = _call_with_retry(ak.bond_cb_redeem_jsl)
            redeem = redeem.rename(
                columns={
                    "代码": "symbol",
                    "名称": "name",
                    "到期日": "maturity_date",
                    "剩余规模": "remaining_size_current",
                    "最后交易日": "jsl_last_trade_date",
                }
            )
            cols = [
                item
                for item in ["symbol", "name", "maturity_date", "remaining_size_current", "jsl_last_trade_date"]
                if item in redeem.columns
            ]
            frames.append(redeem[cols].copy())
        except Exception:
            pass

        universe = pd.concat(frames, ignore_index=True, sort=False)
        universe["symbol"] = universe["symbol"].map(_normalize_code)
        universe = universe[universe["symbol"].map(_is_hs_convertible)].copy()
        universe["listing_date"] = pd.to_datetime(universe.get("listing_date"), errors="coerce").dt.date
        universe["maturity_date"] = pd.to_datetime(universe.get("maturity_date"), errors="coerce").dt.date
        if "jsl_last_trade_date" in universe.columns:
            universe["jsl_last_trade_date"] = pd.to_datetime(universe["jsl_last_trade_date"], errors="coerce").dt.date
        for col in ("name", "listing_date", "issue_size", "maturity_date", "remaining_size_current", "jsl_last_trade_date"):
            if col not in universe.columns:
                universe[col] = pd.NA
        universe = (
            universe.sort_values(["symbol", "listing_date"], na_position="last")
            .groupby("symbol", as_index=False)
            .agg(
                {
                    "name": "first",
                    "listing_date": "first",
                    "issue_size": "first",
                    "maturity_date": "first",
                    "remaining_size_current": "first",
                    "jsl_last_trade_date": "first",
                }
            )
        )
        CACHE_DIR.mkdir(parents=True, exist_ok=True)
        universe.to_parquet(CACHE_DIR / "cb_universe_s14.parquet", index=False)
        return universe
    return pd.read_parquet(CACHE_DIR / "cb_universe_s14.parquet")


def _load_daily_raw(code: str, refresh: bool = False) -> pd.DataFrame:
    path = _cached_raw_path("daily", code)
    if path.exists() and not refresh:
        return pd.read_parquet(path)
    frame = _call_with_retry(ak.bond_zh_hs_cov_daily, _market_symbol(code), sleep_seconds=0.25)
    frame.to_parquet(path, index=False)
    return frame


def _load_value_raw(code: str, refresh: bool = False) -> pd.DataFrame:
    path = _cached_raw_path("value", code)
    if path.exists() and not refresh:
        return pd.read_parquet(path)
    frame = _call_with_retry(ak.bond_zh_cov_value_analysis, _normalize_code(code), sleep_seconds=0.25)
    frame.to_parquet(path, index=False)
    return frame


def _normalize_daily(code: str, frame: pd.DataFrame) -> pd.DataFrame:
    if frame.empty:
        return pd.DataFrame(columns=["symbol", "date", "open", "high", "low", "close", "volume"])
    out = frame.copy()
    out["symbol"] = _normalize_code(code)
    out["date"] = pd.to_datetime(out["date"], errors="coerce").dt.date
    for col in ("open", "high", "low", "close", "volume"):
        out[col] = pd.to_numeric(out[col], errors="coerce")
    out = out.dropna(subset=["date", "close"]).sort_values("date")
    return out[["symbol", "date", "open", "high", "low", "close", "volume"]].reset_index(drop=True)


def _normalize_value(frame: pd.DataFrame) -> pd.DataFrame:
    if frame.empty:
        return pd.DataFrame(columns=["date", "premium_rate", "pure_bond_value", "convert_value"])
    out = frame.rename(
        columns={
            "日期": "date",
            "纯债价值": "pure_bond_value",
            "转股价值": "convert_value",
            "转股溢价率": "premium_rate_percent",
        }
    ).copy()
    out["date"] = pd.to_datetime(out["date"], errors="coerce")
    out["premium_rate"] = pd.to_numeric(out["premium_rate_percent"], errors="coerce") / 100.0
    out["pure_bond_value"] = pd.to_numeric(out["pure_bond_value"], errors="coerce")
    out["convert_value"] = pd.to_numeric(out["convert_value"], errors="coerce")
    out = out.dropna(subset=["date"]).sort_values("date")
    return out[["date", "premium_rate", "pure_bond_value", "convert_value"]].reset_index(drop=True)


def _combine_symbol(symbol_meta: pd.Series, refresh: bool = False) -> tuple[pd.DataFrame, dict[str, Any]]:
    code = _normalize_code(symbol_meta["symbol"])
    daily = _normalize_daily(code, _load_daily_raw(code, refresh=refresh))
    if daily.empty:
        return pd.DataFrame(), {"symbol": code, "status": "empty_daily", "rows": 0}

    daily_start = min(daily["date"].tolist())
    daily_end = max(daily["date"].tolist())
    listing_date = _parse_date(symbol_meta.get("listing_date")) or daily_start
    delist_date = daily_end if daily_end < PANEL_END else None
    overlaps_panel = listing_date <= PANEL_END and (delist_date is None or delist_date >= PANEL_START) and daily_end >= PANEL_START
    if not overlaps_panel:
        return pd.DataFrame(), {"symbol": code, "status": "outside_panel", "rows": len(daily), "daily_start": daily_start, "daily_end": daily_end}

    value = _normalize_value(_load_value_raw(code, refresh=refresh))
    daily_ts = daily.copy()
    daily_ts["date_ts"] = pd.to_datetime(daily_ts["date"], errors="coerce")
    if value.empty:
        merged = daily_ts.copy()
        merged["premium_rate"] = np.nan
        merged["pure_bond_value"] = np.nan
        merged["convert_value"] = np.nan
    else:
        merged = pd.merge_asof(
            daily_ts.sort_values("date_ts"),
            value.sort_values("date"),
            left_on="date_ts",
            right_on="date",
            direction="backward",
            suffixes=("", "_factor"),
        )
        merged["date"] = merged["date_ts"].dt.date

    merged = merged[(merged["date"] >= PANEL_START) & (merged["date"] <= PANEL_END)].copy()
    if merged.empty:
        return pd.DataFrame(), {"symbol": code, "status": "no_rows_in_panel", "rows": 0, "daily_start": daily_start, "daily_end": daily_end}

    merged["name"] = str(symbol_meta.get("name") or "")
    merged["listing_date"] = listing_date
    merged["delist_date"] = delist_date
    merged["maturity_date"] = _parse_date(symbol_meta.get("maturity_date"))
    merged["jsl_last_trade_date"] = _parse_date(symbol_meta.get("jsl_last_trade_date"))
    merged["issue_size"] = pd.to_numeric(pd.Series([symbol_meta.get("issue_size")]), errors="coerce").iloc[0]
    merged["remaining_size_current"] = pd.to_numeric(pd.Series([symbol_meta.get("remaining_size_current")]), errors="coerce").iloc[0]
    merged["amount_proxy"] = pd.to_numeric(merged["close"], errors="coerce") * pd.to_numeric(merged["volume"], errors="coerce")
    merged["redeem_trigger_count_30"] = (
        (pd.to_numeric(merged["convert_value"], errors="coerce") >= 130.0)
        .astype(int)
        .rolling(30, min_periods=1)
        .sum()
        .astype(float)
    )
    merged["in_universe"] = True
    cols = [
        "symbol",
        "name",
        "date",
        "open",
        "high",
        "low",
        "close",
        "volume",
        "amount_proxy",
        "premium_rate",
        "pure_bond_value",
        "convert_value",
        "redeem_trigger_count_30",
        "listing_date",
        "delist_date",
        "maturity_date",
        "jsl_last_trade_date",
        "issue_size",
        "remaining_size_current",
        "in_universe",
    ]
    return merged[cols].reset_index(drop=True), {
        "symbol": code,
        "status": "included",
        "rows": len(merged),
        "daily_start": daily_start,
        "daily_end": daily_end,
        "listing_date": listing_date,
        "delist_date": delist_date,
    }


def _expand_panel(sparse: pd.DataFrame, meta: pd.DataFrame) -> pd.DataFrame:
    calendar = pd.DataFrame({"date": sorted(sparse["date"].dropna().unique().tolist())})
    frames: list[pd.DataFrame] = []
    sparse_by_symbol = {symbol: group.sort_values("date") for symbol, group in sparse.groupby("symbol", sort=False)}
    for row in meta.itertuples(index=False):
        symbol = str(row.symbol)
        base = calendar.copy()
        base["symbol"] = symbol
        merged = base.merge(sparse_by_symbol.get(symbol, pd.DataFrame()), on=["symbol", "date"], how="left", suffixes=("", "_sparse"))
        for col in ("name", "listing_date", "delist_date", "maturity_date", "jsl_last_trade_date", "issue_size", "remaining_size_current"):
            if col not in merged.columns:
                merged[col] = getattr(row, col, None)
            merged[col] = merged[col].where(merged[col].notna(), getattr(row, col, None))
        listing = _parse_date(getattr(row, "listing_date", None))
        delist = _parse_date(getattr(row, "delist_date", None))
        merged["in_universe"] = merged["date"].map(
            lambda item: bool(listing is not None and item >= listing and (delist is None or item <= delist))
        )
        frames.append(merged)
    panel = pd.concat(frames, ignore_index=True, sort=False)
    panel = panel.sort_values(["symbol", "date"]).reset_index(drop=True)
    return panel


def build_or_load_panel(refresh: bool = False) -> tuple[pd.DataFrame, PanelBuildStats]:
    start_time = time.monotonic()
    if PANEL_CACHE.exists() and not refresh:
        panel = pd.read_parquet(PANEL_CACHE)
        panel = _normalize_panel_dates(panel)
        meta = pd.read_parquet(PANEL_META_CACHE) if PANEL_META_CACHE.exists() else pd.DataFrame({"symbol": panel["symbol"].drop_duplicates()})
        failed_mask = meta["status"].astype(str).str.startswith("failed") if "status" in meta.columns else pd.Series(False, index=meta.index)
        failed_symbols = tuple(meta.loc[failed_mask, "symbol"].astype(str).map(_normalize_code).tolist()) if "symbol" in meta.columns else ()
        stats = PanelBuildStats(
            universe_raw=len(meta),
            attempted=len(meta),
            included=panel["symbol"].nunique(),
            failed=len(failed_symbols),
            failed_symbols=failed_symbols,
            rows=len(panel),
            start=min(panel["date"].tolist()),
            end=max(panel["date"].tolist()),
            elapsed_seconds=time.monotonic() - start_time,
            cached=True,
        )
        return panel, stats

    universe = _load_universe(refresh=refresh)
    sparse_frames: list[pd.DataFrame] = []
    status_rows: list[dict[str, Any]] = []
    failed: list[str] = []
    attempted = 0
    for index, row in universe.iterrows():
        code = _normalize_code(row["symbol"])
        attempted += 1
        try:
            frame, status = _combine_symbol(row, refresh=refresh)
        except Exception as exc:
            failed.append(code)
            status = {"symbol": code, "status": f"failed:{type(exc).__name__}", "rows": 0, "error": str(exc)}
            frame = pd.DataFrame()
        status_rows.append(status)
        if not frame.empty:
            sparse_frames.append(frame)
        if attempted % 50 == 0:
            print(f"S14 panel progress attempted={attempted}/{len(universe)} included={len(sparse_frames)} failed={len(failed)}")

    if not sparse_frames:
        raise RuntimeError("S14 convertible bond panel build produced no data")

    sparse = pd.concat(sparse_frames, ignore_index=True, sort=False)
    included_meta = (
        sparse[["symbol", "name", "listing_date", "delist_date", "maturity_date", "jsl_last_trade_date", "issue_size", "remaining_size_current"]]
        .drop_duplicates("symbol", keep="last")
        .sort_values("symbol")
        .reset_index(drop=True)
    )
    panel = _expand_panel(sparse, included_meta)
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    panel.to_parquet(PANEL_CACHE, index=False)
    pd.DataFrame(status_rows).to_parquet(PANEL_META_CACHE, index=False)
    stats = PanelBuildStats(
        universe_raw=len(universe),
        attempted=attempted,
        included=panel["symbol"].nunique(),
        failed=len(failed),
        failed_symbols=tuple(failed),
        rows=len(panel),
        start=min(panel["date"].tolist()),
        end=max(panel["date"].tolist()),
        elapsed_seconds=time.monotonic() - start_time,
        cached=False,
    )
    return panel, stats


def _normalize_panel_dates(panel: pd.DataFrame) -> pd.DataFrame:
    out = panel.copy()
    for col in ("date", "listing_date", "delist_date", "maturity_date", "jsl_last_trade_date"):
        if col in out.columns:
            out[col] = pd.to_datetime(out[col], errors="coerce").dt.date
    out["symbol"] = out["symbol"].map(_normalize_code)
    return out.sort_values(["symbol", "date"]).reset_index(drop=True)


def _data_dict_from_panel(panel: pd.DataFrame) -> dict[str, pd.DataFrame]:
    data: dict[str, pd.DataFrame] = {}
    tradable = panel[panel["open"].notna() & panel["close"].notna()].copy()
    for symbol, frame in tradable.groupby("symbol", sort=False):
        data[str(symbol)] = frame.sort_values("date").reset_index(drop=True)
    return data


def _prepare_price_lookup(data: dict[str, pd.DataFrame]) -> None:
    _PRICE_LOOKUP.clear()
    for symbol, frame in data.items():
        ordered = frame.sort_values("date")
        dates = ordered["date"].tolist()
        closes = pd.to_numeric(ordered["close"], errors="coerce").astype(float).tolist()
        _PRICE_LOOKUP[str(symbol)] = (dates, closes)


def _calendar_dates(panel: pd.DataFrame) -> list[date]:
    tradable = panel[panel["close"].notna()]
    return sorted(tradable["date"].dropna().unique().tolist())


def _effective_span(regime: str, calendar_dates: list[date]) -> EffectiveSpan:
    span = _regime_cfg()[regime]
    configured_start = _parse_date(span["start"])
    configured_end = _parse_date(span["end"])
    assert configured_start is not None and configured_end is not None
    dates = [item for item in calendar_dates if configured_start <= item <= configured_end]
    if not dates:
        raise RuntimeError(f"No S14 bond dates for {regime}")
    return EffectiveSpan(regime, configured_start, configured_end, dates[0], dates[-1], dates[0] != configured_start or dates[-1] != configured_end)


def _panel_asof_rows(panel: pd.DataFrame, as_of_date: date) -> pd.DataFrame:
    rows = panel[(panel["date"] <= as_of_date) & panel["close"].notna()].sort_values(["symbol", "date"])
    if rows.empty:
        return rows.copy()
    latest = rows.groupby("symbol", as_index=False).tail(1).copy()
    return _mark_panel_asof_membership(latest, as_of_date)


def _mark_panel_asof_membership(latest: pd.DataFrame, as_of_date: date) -> pd.DataFrame:
    latest["asof_date"] = as_of_date
    listing = pd.to_datetime(latest["listing_date"], errors="coerce").dt.date
    delist = pd.to_datetime(latest["delist_date"], errors="coerce").dt.date
    latest["in_universe"] = [
        bool(pd.notna(listed) and listed <= as_of_date and (pd.isna(ended) or as_of_date <= ended))
        for listed, ended in zip(listing, delist, strict=True)
    ]
    return latest.reset_index(drop=True)


def _precompute_asof_cache(panel: pd.DataFrame, signal_dates: list[date]) -> dict[date, pd.DataFrame]:
    tradable = panel[panel["close"].notna()].sort_values(["symbol", "date"])
    records_by_date: dict[date, list[dict[str, Any]]] = {signal_date: [] for signal_date in signal_dates}
    for _symbol, group in tradable.groupby("symbol", sort=False):
        dates = group["date"].tolist()
        records = group.to_dict("records")
        for signal_date in signal_dates:
            idx = bisect_right(dates, signal_date) - 1
            if idx >= 0:
                records_by_date[signal_date].append(records[idx])
    return {
        signal_date: _mark_panel_asof_membership(pd.DataFrame(records), signal_date)
        for signal_date, records in records_by_date.items()
        if records
    }


def _position_for_symbol(positions: tuple[Position, ...], symbol: str) -> Position | None:
    for item in positions:
        if item.symbol == symbol:
            return item
    return None


def _last_close(data: dict[str, pd.DataFrame], symbol: str, as_of_date: date) -> float | None:
    lookup = _PRICE_LOOKUP.get(symbol)
    if lookup is not None:
        dates, closes = lookup
        idx = bisect_right(dates, as_of_date) - 1
        if idx < 0:
            return None
        value = closes[idx]
        if pd.isna(value):
            return None
        return float(value)
    frame = data.get(symbol)
    if frame is None or frame.empty:
        return None
    rows = frame[frame["date"] <= as_of_date]
    if rows.empty:
        return None
    close = pd.to_numeric(rows.iloc[-1]["close"], errors="coerce")
    if pd.isna(close):
        return None
    return float(close)


def _row_on(data: dict[str, pd.DataFrame], symbol: str, trade_date: date) -> pd.Series | None:
    frame = data.get(symbol)
    if frame is None or frame.empty:
        return None
    row = frame[frame["date"] == trade_date]
    if row.empty:
        return None
    return row.iloc[-1]


def _market_bar(data: dict[str, pd.DataFrame], symbol: str, trade_date: date) -> MarketBar:
    row = _row_on(data, symbol, trade_date)
    if row is None or pd.isna(row.get("open")):
        return MarketBar(symbol=symbol, date=trade_date, open=float(_last_close(data, symbol, trade_date) or 0.0), is_suspended=True)
    return MarketBar(symbol=symbol, date=trade_date, open=float(row["open"]), is_suspended=False)


def _mark_nav(cash: float, positions: tuple[Position, ...], data: dict[str, pd.DataFrame], as_of_date: date) -> float:
    nav = cash
    for position in positions:
        close = _last_close(data, position.symbol, as_of_date)
        if close is not None:
            nav += position.quantity * close
    return nav


def _affordable_quantity(desired_quantity: int, base_price: float, cash: float, cost_config: CostConfig, lot_size: int = CB_LOT_SIZE) -> int:
    quantity = desired_quantity - desired_quantity % lot_size
    while quantity > 0:
        fill_price = slippage_price(base_price, "buy", cost_config)
        amount = fill_price * quantity
        if amount + trade_cost(amount, "buy", cost_config) <= cash + 1e-9:
            return quantity
        quantity -= lot_size
    return 0


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


def _force_exit_delisted_positions(
    trade_date: date,
    cash: float,
    positions: tuple[Position, ...],
    basis: dict[str, float],
    trades: list[TradeRecord],
    filled_orders: list[dict[str, Any]],
    rejected_orders: list[dict[str, Any]],
    events: list[dict[str, Any]],
    data: dict[str, pd.DataFrame],
    delist_dates: dict[str, date | None],
    cost_config: CostConfig,
) -> tuple[float, tuple[Position, ...]]:
    forced_orders: list[Order] = []
    for position in positions:
        delist_date = delist_dates.get(position.symbol)
        if delist_date is not None and trade_date >= delist_date:
            forced_orders.append(Order(symbol=position.symbol, side="sell", quantity=position.quantity, submitted_date=trade_date))
            events.append({"type": "forced_redeem_exit", "symbol": position.symbol, "date": trade_date.isoformat(), "delist_date": delist_date.isoformat()})
    return _execute_orders(forced_orders, trade_date, cash, positions, basis, trades, filled_orders, rejected_orders, events, data, cost_config)


def _ctx_for_signal(
    as_of_date: date,
    cash: float,
    positions: tuple[Position, ...],
    data: dict[str, pd.DataFrame],
    panel: pd.DataFrame,
    month_ends: set[date],
    asof_cache: dict[date, pd.DataFrame] | None = None,
) -> dict[str, Any]:
    return {
        "panel_asof": asof_cache[as_of_date] if asof_cache is not None and as_of_date in asof_cache else _panel_asof_rows(panel, as_of_date),
        "positions": positions,
        "cash": cash,
        "nav": _mark_nav(cash, positions, data, as_of_date),
        "lot_size": CB_LOT_SIZE,
        "month_end_dates": month_ends,
    }


def run_s14_monthly_backtest(
    name: str,
    regime: str,
    start: date,
    end: date,
    panel: pd.DataFrame,
    data: dict[str, pd.DataFrame],
    calendar_dates: list[date],
    month_ends: set[date],
    signal_func: SignalFunc,
    cost_config: CostConfig,
    asof_cache: dict[date, pd.DataFrame] | None = None,
) -> BacktestRun:
    dates = [item for item in calendar_dates if start <= item <= end]
    if not dates:
        raise RuntimeError(f"Not enough S14 dates for {regime}")

    delist_dates = {
        str(row.symbol): _parse_date(row.delist_date)
        for row in panel[["symbol", "delist_date"]].drop_duplicates("symbol").itertuples(index=False)
    }
    cash = INITIAL_CASH
    positions: tuple[Position, ...] = ()
    basis: dict[str, float] = {}
    trades: list[TradeRecord] = []
    filled_orders: list[dict[str, Any]] = []
    rejected_orders: list[dict[str, Any]] = []
    events: list[dict[str, Any]] = []
    pending_orders: dict[date, list[Order]] = {}

    def next_trading_date(signal_date: date) -> date | None:
        idx = bisect_right(calendar_dates, signal_date)
        if idx >= len(calendar_dates):
            return None
        return calendar_dates[idx]

    def schedule_signal(signal_date: date) -> None:
        trade_date = next_trading_date(signal_date)
        if trade_date is None or trade_date < start or trade_date > end:
            return
        ctx = _ctx_for_signal(signal_date, cash, positions, data, panel, month_ends, asof_cache)
        orders = signal_func(signal_date, ctx)
        if orders:
            pending_orders.setdefault(trade_date, []).extend(orders)

    first_date = dates[0]
    previous_signals = [item for item in month_ends if item < first_date]
    if previous_signals:
        previous_month_end = max(previous_signals)
        if next_trading_date(previous_month_end) == first_date:
            schedule_signal(previous_month_end)

    nav_rows: list[dict[str, Any]] = [{"date": first_date.isoformat(), "nav": cash}]
    for trade_date in dates:
        positions = mark_sellable(positions, trade_date)
        cash, positions = _force_exit_delisted_positions(
            trade_date, cash, positions, basis, trades, filled_orders, rejected_orders, events, data, delist_dates, cost_config
        )
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


def _all_candidates_signal(strategy: S14DoubleLowBondStrategy) -> SignalFunc:
    def _signal(as_of_date: date, ctx: dict[str, Any]) -> list[Order]:
        if as_of_date not in set(ctx.get("month_end_dates", ())):
            return []
        candidates, _stats = strategy.filtered_candidates(as_of_date, ctx["panel_asof"])
        if candidates.empty:
            return []
        weight = 1.0 / len(candidates)
        weights = {str(symbol): weight for symbol in candidates["symbol"].tolist()}
        return orders_for_target_weights(weights, as_of_date, ctx)

    return _signal


def _random10_signal(strategy: S14DoubleLowBondStrategy) -> SignalFunc:
    def _signal(as_of_date: date, ctx: dict[str, Any]) -> list[Order]:
        if as_of_date not in set(ctx.get("month_end_dates", ())):
            return []
        candidates, _stats = strategy.filtered_candidates(as_of_date, ctx["panel_asof"])
        if candidates.empty:
            return []
        rng = np.random.default_rng(RANDOM_SEED + as_of_date.toordinal())
        selected_symbols = candidates["symbol"].to_numpy()
        if len(selected_symbols) > strategy.hold_n:
            selected_symbols = rng.choice(selected_symbols, size=strategy.hold_n, replace=False)
        weight = 1.0 / len(selected_symbols)
        weights = {str(symbol): weight for symbol in selected_symbols}
        return orders_for_target_weights(weights, as_of_date, ctx)

    return _signal


def _load_hs300_data(start: date, end: date) -> dict[str, pd.DataFrame]:
    frame = get_etf_daily("510300", start=start, end=end, refresh=False).copy()
    frame["date"] = pd.to_datetime(frame["date"], errors="coerce").dt.date
    return {"510300": frame.dropna(subset=["date"]).sort_values("date").reset_index(drop=True)}


def _run_hs300_baseline(spans: dict[str, EffectiveSpan]) -> dict[str, BacktestRun]:
    start = min(item.effective_start for item in spans.values())
    end = max(item.effective_end for item in spans.values())
    data = _load_hs300_data(start - timedelta(days=10), end)
    dates = _common_dates(data)
    month_ends = _month_end_dates(dates)
    cost_config = CostConfig.from_mapping(_load_yaml("cost.yaml"))
    return {
        regime: run_monthly_backtest(
            "hs300_buy_hold",
            regime,
            span.effective_start,
            span.effective_end,
            data,
            dates,
            month_ends,
            _single_etf_buy_hold_signal("510300"),
            cost_config,
        )
        for regime, span in spans.items()
    }


def _run_s12_baseline() -> dict[str, BacktestRun]:
    from backtest.s12_gate1 import run as run_s12

    result = run_s12(refresh=False)
    return {regime: values["s12"] for regime, values in result["runs"].items()}


def _merged_metrics(runs: list[BacktestRun] | dict[str, BacktestRun]) -> dict[str, float]:
    values = runs.values() if isinstance(runs, dict) else runs
    return _trade_metrics(tuple(item for run in values for item in run.trades))


def _gate_checks(s14_runs: dict[str, BacktestRun], gate1: dict[str, Any]) -> dict[str, Any]:
    summaries = {name: summarize_run(run) for name, run in s14_runs.items()}
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

    merged = _merged_metrics([s14_runs[name] for name in ("bull", "bear", "range")])
    b_checks = {
        "trades": merged["trades"] >= float(gate1["min_trades"]),
        "expectancy": merged["expectancy"] > float(gate1["expectancy_after_cost_gt"]),
        "profit_factor": merged["profit_factor"] >= float(gate1["profit_factor_min"]),
    }
    oos = summaries["oos"]
    c_checks = {
        "trades": oos["trades"] >= float(gate1["oos_min_trades"]),
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


def _summary_table(runs: dict[str, BacktestRun]) -> str:
    lines = [
        "| regime | start | end | return | max_drawdown | trades | expectancy_after_cost | profit_factor | win_rate | fee_ratio | filled_orders | rejected_orders |",
        "|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for regime in REGIMES:
        run = runs[regime]
        metrics = summarize_run(run)
        lines.append(
            f"| {regime} | {run.start} | {run.end} | {_fmt_pct(metrics['return'])} | {_fmt_pct(metrics['max_drawdown'])} | "
            f"{int(metrics['trades'])} | {metrics['expectancy']:.2f} | {_fmt_float(metrics['profit_factor'])} | "
            f"{_fmt_pct(metrics['win_rate'])} | {_fmt_pct(metrics['fee_ratio'])} | {len(run.filled_orders)} | {len(run.rejected_orders)} |"
        )
    return "\n".join(lines)


def _comparison_table(regime_runs: dict[str, BacktestRun]) -> str:
    s14 = summarize_run(regime_runs["s14"])
    all_c = summarize_run(regime_runs["all_candidates"])
    random10 = summarize_run(regime_runs["random10"])
    hs300 = summarize_run(regime_runs["hs300_buy_hold"])
    s12 = summarize_run(regime_runs["s12"])
    rows = [
        ("return", s14["return"], all_c["return"], random10["return"], hs300["return"], s12["return"], True),
        ("max_drawdown", s14["max_drawdown"], all_c["max_drawdown"], random10["max_drawdown"], hs300["max_drawdown"], s12["max_drawdown"], True),
        ("trades", s14["trades"], all_c["trades"], random10["trades"], hs300["trades"], s12["trades"], False),
        ("profit_factor", s14["profit_factor"], all_c["profit_factor"], random10["profit_factor"], hs300["profit_factor"], s12["profit_factor"], False),
        ("expectancy", s14["expectancy"], all_c["expectancy"], random10["expectancy"], hs300["expectancy"], s12["expectancy"], False),
    ]
    lines = [
        "| metric | S14 | all_filtered_EW | random10 | HS300ETF_BH | S12_RP | S14/all | S14/random | S14/HS300 | S14/S12 |",
        "|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for metric, s14_v, all_v, random_v, hs300_v, s12_v, pct in rows:
        fmt = _fmt_pct if pct else _fmt_float
        lines.append(
            f"| {metric} | {fmt(s14_v)} | {fmt(all_v)} | {fmt(random_v)} | {fmt(hs300_v)} | {fmt(s12_v)} | "
            f"{_fmt_float(_metric_ratio(s14_v, all_v))} | {_fmt_float(_metric_ratio(s14_v, random_v))} | "
            f"{_fmt_float(_metric_ratio(s14_v, hs300_v))} | {_fmt_float(_metric_ratio(s14_v, s12_v))} |"
        )
    return "\n".join(lines)


def _metric_ratio(left: float, right: float) -> float | None:
    if pd.isna(left) or pd.isna(right) or math.isinf(left) or math.isinf(right) or abs(right) < 1e-12:
        return None
    return left / right


def _gate_table(checks: dict[str, Any], gate1: dict[str, Any]) -> str:
    def row(label: str, metric: str, actual: float, threshold: float, passed: bool) -> str:
        return f"| {label} | {metric} | {_fmt_float(actual)} | {_fmt_float(threshold)} | {'PASS' if passed else 'FAIL'} |"

    lines = ["| group | metric | actual | threshold | result |", "|---|---:|---:|---:|---|"]
    for name, metrics, item_checks, _passed in checks["a_rows"]:
        lines.append(row(f"A/{name}", "expectancy_after_cost", metrics["expectancy"], gate1["expectancy_after_cost_gt"], item_checks["expectancy"]))
        lines.append(row(f"A/{name}", "profit_factor", metrics["profit_factor"], gate1["profit_factor_min"], item_checks["profit_factor"]))
        lines.append(row(f"A/{name}", "max_drawdown", metrics["max_drawdown"], gate1["max_drawdown_max"], item_checks["max_drawdown"]))
    merged = checks["merged"]
    b = checks["b_checks"]
    lines.append(row("B/in_sample", "trades", merged["trades"], gate1["min_trades"], b["trades"]))
    lines.append(row("B/in_sample", "expectancy_after_cost", merged["expectancy"], gate1["expectancy_after_cost_gt"], b["expectancy"]))
    lines.append(row("B/in_sample", "profit_factor", merged["profit_factor"], gate1["profit_factor_min"], b["profit_factor"]))
    oos = checks["summaries"]["oos"]
    c = checks["c_checks"]
    lines.append(row("C/oos", "trades", oos["trades"], gate1["oos_min_trades"], c["trades"]))
    lines.append(row("C/oos", "expectancy_after_cost", oos["expectancy"], gate1["expectancy_after_cost_gt"], c["expectancy"]))
    lines.append(row("C/oos", "profit_factor", oos["profit_factor"], gate1["profit_factor_min"], c["profit_factor"]))
    lines.append(row("C/oos", "max_drawdown", oos["max_drawdown"], gate1["max_drawdown_max"], c["max_drawdown"]))
    lines.append(f"| TOTAL | A+B+C | - | - | {'PASS' if checks['overall_pass'] else 'FAIL'} |")
    return "\n".join(lines)


def _candidate_diagnostics(
    strategy: S14DoubleLowBondStrategy,
    panel: pd.DataFrame,
    month_ends: set[date],
    start: date,
    end: date,
    asof_cache: dict[date, pd.DataFrame] | None = None,
) -> pd.DataFrame:
    rows = []
    for signal_date in sorted(item for item in month_ends if start <= item <= end):
        asof = asof_cache[signal_date] if asof_cache is not None and signal_date in asof_cache else _panel_asof_rows(panel, signal_date)
        selected, stats = strategy.select_candidates(signal_date, asof)
        rows.append(
            {
                "date": signal_date,
                "total_asof": stats.total_asof,
                "in_universe": stats.in_universe,
                "after_listing_age": stats.after_listing_age,
                "after_price": stats.after_price,
                "after_premium": stats.after_premium,
                "after_liquidity": stats.after_liquidity,
                "after_redeem": stats.after_redeem,
                "selected": len(selected),
                "avg_score": float(selected["score"].mean()) if not selected.empty else np.nan,
                "avg_price": float(selected["close"].mean()) if not selected.empty else np.nan,
                "avg_premium": float(selected["premium_rate"].mean()) if not selected.empty else np.nan,
            }
        )
    return pd.DataFrame(rows)


def _candidate_table(diag: pd.DataFrame) -> str:
    if diag.empty:
        return "NA"
    metrics = {
        "months": len(diag),
        "avg_in_universe": diag["in_universe"].mean(),
        "avg_after_price": diag["after_price"].mean(),
        "avg_after_premium": diag["after_premium"].mean(),
        "avg_after_liquidity": diag["after_liquidity"].mean(),
        "avg_after_redeem": diag["after_redeem"].mean(),
        "avg_selected": diag["selected"].mean(),
        "avg_price": diag["avg_price"].mean(),
        "avg_premium": diag["avg_premium"].mean(),
    }
    return "\n".join(
        [
            "| metric | value |",
            "|---|---:|",
            f"| months | {metrics['months']} |",
            f"| avg_in_universe | {metrics['avg_in_universe']:.1f} |",
            f"| avg_after_price | {metrics['avg_after_price']:.1f} |",
            f"| avg_after_premium | {metrics['avg_after_premium']:.1f} |",
            f"| avg_after_liquidity | {metrics['avg_after_liquidity']:.1f} |",
            f"| avg_after_redeem | {metrics['avg_after_redeem']:.1f} |",
            f"| avg_selected | {metrics['avg_selected']:.1f} |",
            f"| selected_avg_price | {metrics['avg_price']:.2f} |",
            f"| selected_avg_premium_rate | {_fmt_pct(metrics['avg_premium'])} |",
        ]
    )


def _turnover_metrics(runs: dict[str, BacktestRun]) -> dict[str, float]:
    month_count = 0
    traded = 0.0
    avg_navs = []
    for run in runs.values():
        traded += sum(float(item["amount"]) for item in run.filled_orders)
        curve = run.nav_curve.copy()
        if not curve.empty:
            avg_navs.append(pd.to_numeric(curve["nav"], errors="coerce").mean())
            curve["date"] = pd.to_datetime(curve["date"], errors="coerce")
            month_count += int(curve["date"].dt.to_period("M").nunique())
    avg_nav = float(np.nanmean(avg_navs)) if avg_navs else INITIAL_CASH
    return {
        "traded_amount": traded,
        "months": float(month_count),
        "monthly_turnover": traded / avg_nav / month_count if month_count else 0.0,
    }


def _panel_stats_table(stats: PanelBuildStats, panel: pd.DataFrame) -> str:
    active_symbols = panel[panel["in_universe"] & panel["close"].notna()].groupby("symbol")["date"].max()
    delisted = int(panel[panel["delist_date"].notna()]["symbol"].nunique())
    lines = [
        "| metric | value |",
        "|---|---:|",
        f"| raw_universe | {stats.universe_raw} |",
        f"| attempted | {stats.attempted} |",
        f"| panel_symbols | {stats.included} |",
        f"| panel_rows | {stats.rows} |",
        f"| panel_start | {stats.start} |",
        f"| panel_end | {stats.end} |",
        f"| delisted_or_matured_symbols_in_panel | {delisted} |",
        f"| failed_symbols | {stats.failed} |",
        f"| fail_rate | {_fmt_pct(stats.fail_rate)} |",
        f"| elapsed_minutes | {stats.elapsed_seconds / 60.0:.2f} |",
        f"| used_cached_panel | {stats.cached} |",
    ]
    if stats.fail_rate > 0.05:
        lines.append(f"| WARNING | failure_rate_gt_5pct: {', '.join(stats.failed_symbols[:10])} |")
    return "\n".join(lines)


def render_report(
    all_runs: dict[str, dict[str, BacktestRun]],
    checks: dict[str, Any],
    panel_stats: PanelBuildStats,
    panel: pd.DataFrame,
    candidate_diag: pd.DataFrame,
    spans: dict[str, EffectiveSpan],
    cfg: dict[str, Any],
) -> str:
    s14_runs = {regime: values["s14"] for regime, values in all_runs.items()}
    final = "PASS" if checks["overall_pass"] else "FAIL"
    turnover = _turnover_metrics(s14_runs)
    lines = [
        "# S14 Convertible Bond Double-Low Gate1 Report",
        "",
        "规则：每月最后交易日 D 收盘后，用截至 D 的可转债日线和估值分析表取最新可得行；过滤价格、转股溢价率、成交额近似、强赎/临近退市、新券后，按 `price + premium_rate * 100` 升序取 Top10，下月首交易日开盘等权调仓，只交易差额。",
        "PIT：价格来自 `bond_zh_hs_cov_daily` 的 D 或 D 前最近交易行；`premium_rate` 来自 `bond_zh_cov_value_analysis` 的 D 或 D 前最近行；强赎计数用 `convert_value >= 130` 的最近 30 个交易日计数近似。OOS 未用于调参。",
        "",
        "## 数据面板与反幸存者",
        _panel_stats_table(panel_stats, panel),
        "",
        "面板缓存：`data/cache/cb_panel_pit_2020_2026.parquet`。字段含 `open/high/low/close/volume/premium_rate/pure_bond_value/convert_value/listing_date/delist_date/in_universe`，并额外保留 `amount_proxy` 与 `redeem_trigger_count_30`。",
        "抓取耗时：首次 AkShare 抓取含一次中断续跑，实际从批量续跑到 panel 写出约 14 分钟；本报告最终 Gate1 为缓存复跑，所以上表 `elapsed_minutes` 是缓存加载耗时。",
        "",
        "## regime 实际可得区间",
        _effective_span_table(spans),
        "",
        "## 候选过滤诊断",
        _candidate_table(candidate_diag),
        "",
        "## S14 分段关键指标",
        _summary_table(s14_runs),
        "",
        "## in-sample vs OOS 差异",
        _insample_oos_table(s14_runs),
        "",
        f"月均换手率：{_fmt_pct(turnover['monthly_turnover'])}；全期成交额/平均 NAV/月数口径，traded_amount={turnover['traded_amount']:.2f}，months={int(turnover['months'])}。",
        "",
        "## 对照组真实数字与 ratio",
    ]
    for regime in REGIMES:
        lines.extend([f"### {regime}", _comparison_table(all_runs[regime]), ""])
    lines.extend(
        [
            "对照组定义：全等权持有所有通过同一过滤的候选、固定随机种子月度随机 10 只、510300 沪深300 ETF 买入持有、S12 跨大类风险平价同期表现。",
            "",
            "## 反假设列表",
            "1. 双低 alpha 在严格 PIT 下是否仍成立：用 S14 Top10 对比 all_filtered_equal_weight。若 S14 不能稳定优于全候选等权，Top10 排名本身没有提供增量 alpha。",
            "2. amount 缺失偏差：本轮按预注册使用 `close*volume` 近似，过滤阈值 500 万。该近似可能误判深市/沪市成交量单位，方向上会让小流动性券被错选或被误杀；报告保留候选过滤后的平均数量与 fee_ratio。",
            "3. 退市/强赎处理现实性：面板保留退市后 `in_universe=False`；策略排除最近 30 日内退市/赎回，以及最近 30 交易日 `convert_value>=130` 满 15 天的券。若仍持有到 delist_date，回测按最后可得价格强制退出，这比真实强赎兑付更简化。",
            "4. 与已 FAIL 股票策略相比：本轮不因可转债传闻放宽 Gate1；若 A/B/C 任一不达标，则说明这个公开叙事在本项目成本、PIT、反幸存者口径下未被证实。",
            "",
            "## flag/参数调查记录",
            f"- 参数全预注册不调：hold_n={cfg['hold_n']}，price_max={cfg['filters']['price_max']}，premium_max={cfg['filters']['premium_max']}，min_volume_yuan={cfg['filters']['min_volume_yuan']}。",
            "- 未碰 OOS：OOS 仅在规则、数据源、成本、过滤和对照组固定后用于最终 C 组裁决。",
            "- amount 用 `close*volume` 近似；日线源实测无 `amount` 字段。",
            "- 可转债印花税设 0；撮合不使用股票 10% 涨跌停，只有停牌/无当日开盘价拒单。",
            "",
            "## Gate1 判定表",
            _gate_table(checks, _gate_cfg()),
            "",
            "## 结论段",
            f"S14 是项目第 14 个策略。最终判定={final}。目前只有 S12 OOS PASS 但整体 FAIL；因此 " + ("S14 是本项目首次整体 Gate1 PASS。" if final == "PASS" else "S14 不是首次整体 Gate1 PASS，项目仍未出现整体 Gate1 PASS。"),
            "",
            f"最终判定：{final}。",
        ]
    )
    return "\n".join(lines) + "\n"


def run(refresh_panel: bool = False) -> dict[str, Any]:
    cfg = _strategy_cfg()
    cost_config = _s14_cost_config(cfg)
    print("S14 stage: load/build panel", flush=True)
    panel, panel_stats = build_or_load_panel(refresh=refresh_panel)
    panel = _normalize_panel_dates(panel)
    print(f"S14 stage: panel ready rows={len(panel)} symbols={panel['symbol'].nunique()}", flush=True)
    data = _data_dict_from_panel(panel)
    _prepare_price_lookup(data)
    calendar_dates = _calendar_dates(panel)
    month_ends = _month_end_dates(calendar_dates)
    spans = {regime: _effective_span(regime, calendar_dates) for regime in REGIMES}
    signal_dates = sorted(
        item
        for item in month_ends
        if min(span.effective_start for span in spans.values()) - timedelta(days=45) <= item <= max(span.effective_end for span in spans.values())
    )
    print(f"S14 stage: precompute asof slices n={len(signal_dates)}", flush=True)
    asof_cache = _precompute_asof_cache(panel, signal_dates)
    print("S14 stage: load baselines", flush=True)

    strategy = S14DoubleLowBondStrategy(cfg)
    hs300_runs = _run_hs300_baseline(spans)
    s12_runs = _run_s12_baseline()
    all_runs: dict[str, dict[str, BacktestRun]] = {}
    for regime in REGIMES:
        span = spans[regime]
        print(f"S14 stage: run regime={regime}", flush=True)
        all_runs[regime] = {}
        print(f"S14 stage: {regime}/s14", flush=True)
        all_runs[regime]["s14"] = run_s14_monthly_backtest(
                "s14",
                regime,
                span.effective_start,
                span.effective_end,
                panel,
                data,
                calendar_dates,
                month_ends,
                strategy.generate_signals,
                cost_config,
                asof_cache,
            )
        print(f"S14 stage: {regime}/all_candidates", flush=True)
        all_runs[regime]["all_candidates"] = run_s14_monthly_backtest(
                "all_candidates",
                regime,
                span.effective_start,
                span.effective_end,
                panel,
                data,
                calendar_dates,
                month_ends,
                _all_candidates_signal(strategy),
                cost_config,
                asof_cache,
            )
        print(f"S14 stage: {regime}/random10", flush=True)
        all_runs[regime]["random10"] = run_s14_monthly_backtest(
                "random10",
                regime,
                span.effective_start,
                span.effective_end,
                panel,
                data,
                calendar_dates,
                month_ends,
                _random10_signal(strategy),
                cost_config,
                asof_cache,
            )
        all_runs[regime]["hs300_buy_hold"] = hs300_runs[regime]
        all_runs[regime]["s12"] = s12_runs[regime]

    s14_runs = {regime: values["s14"] for regime, values in all_runs.items()}
    checks = _gate_checks(s14_runs, _gate_cfg())
    candidate_diag = _candidate_diagnostics(
        strategy,
        panel,
        month_ends,
        min(item.effective_start for item in spans.values()),
        max(item.effective_end for item in spans.values()),
        asof_cache,
    )
    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    path = REPORT_DIR / "s14_double_low_bond_gate1.md"
    path.write_text(render_report(all_runs, checks, panel_stats, panel, candidate_diag, spans, cfg), encoding="utf-8")
    return {
        "path": path,
        "runs": all_runs,
        "checks": checks,
        "panel_stats": panel_stats,
        "candidate_diag": candidate_diag,
        "spans": spans,
        "cfg": cfg,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Run S14 convertible bond double-low Gate1")
    parser.add_argument("--refresh-panel", action="store_true")
    args = parser.parse_args()
    result = run(refresh_panel=args.refresh_panel)
    s14_runs = {regime: values["s14"] for regime, values in result["runs"].items()}
    total_trades = int(sum(summarize_run(run)["trades"] for run in s14_runs.values()))
    final = "PASS" if result["checks"]["overall_pass"] else "FAIL"
    stats: PanelBuildStats = result["panel_stats"]
    turnover = _turnover_metrics(s14_runs)
    print(f"wrote {result['path']}")
    print(
        f"S14 trades={total_trades} final={final} universe={stats.included}/{stats.universe_raw} "
        f"panel_rows={stats.rows} elapsed_min={stats.elapsed_seconds / 60.0:.2f} cached={stats.cached} "
        f"monthly_turnover={turnover['monthly_turnover']:.4%}"
    )
    for regime in REGIMES:
        metrics = summarize_run(s14_runs[regime])
        print(
            f"{regime}: return={metrics['return']:.4%} dd={metrics['max_drawdown']:.4%} "
            f"trades={int(metrics['trades'])} expectancy={metrics['expectancy']:.2f} "
            f"pf={_fmt_float(metrics['profit_factor'])}"
        )


if __name__ == "__main__":
    main()
