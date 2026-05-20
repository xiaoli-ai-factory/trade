"""Paper trading runner for forward and held-out walkforward modes."""

from __future__ import annotations

import argparse
import csv
import importlib
import json
import math
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date, timedelta
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
import yaml

from backtest.constraints import CostConfig, Order, Position
from data.akshare_source import (
    CACHE_DIR,
    _elapsed_trading_fraction,
    _fetch_intraday_bars,
    _trade_dates,
    _universe,
    get_daily,
    get_index_daily,
)
from strategies.s3b_trend import S3BTrendStrategy


PROJECT_ROOT = Path(__file__).resolve().parents[1]
CONFIG_DIR = PROJECT_ROOT / "configs"
PAPER_DIR = PROJECT_ROOT / "paper"
TREND_ACCOUNT = "oos_walkforward_trend"
FORWARD_ACCOUNT = "forward"


def _paper_broker_cls():
    return importlib.import_module("exec.paper_broker").PaperBroker


def _load_yaml(name: str) -> dict[str, Any]:
    with (CONFIG_DIR / name).open("r", encoding="utf-8") as fh:
        return yaml.safe_load(fh) or {}


def _parse_date(value: str | date | pd.Timestamp) -> date:
    if isinstance(value, pd.Timestamp):
        return value.date()
    if isinstance(value, date):
        return value
    return pd.Timestamp(str(value)).date()


def _fmt_pct(value: float) -> str:
    return f"{value:.2%}"


def _fmt_float(value: float) -> str:
    if math.isinf(value):
        return "inf"
    if pd.isna(value):
        return "NA"
    return f"{value:.4f}"


def _normalize_symbol(symbol: str) -> str:
    symbol = str(symbol)
    if symbol.startswith(("sh", "sz")):
        return symbol
    return symbol.zfill(6)


def _daily_getter(symbol: str):
    return get_index_daily if str(symbol).startswith(("sh", "sz")) else get_daily


def _load_market_data(symbols: set[str], start: date, end: date, *, refresh: bool = False) -> dict[str, pd.DataFrame]:
    data: dict[str, pd.DataFrame] = {}
    for symbol in sorted(_normalize_symbol(item) for item in symbols if item):
        getter = _daily_getter(symbol)
        try:
            frame = getter(symbol, start=start, end=end, refresh=refresh)
        except Exception:
            continue
        if frame.empty:
            continue
        frame = frame.copy()
        frame["date"] = pd.to_datetime(frame["date"], errors="coerce").dt.date
        frame = frame.dropna(subset=["date"]).sort_values("date").reset_index(drop=True)
        data[symbol] = frame
    return data


def _state_symbols(state_dir: Path, account: str) -> set[str]:
    path = state_dir / f"{account}.json"
    if not path.exists():
        return set()
    state = json.loads(path.read_text(encoding="utf-8"))
    symbols = {str(item["symbol"]) for item in state.get("positions", [])}
    for item in state.get("pending_orders", []):
        symbols.add(str(item.get("order", {}).get("symbol", "")))
    return {item for item in symbols if item}


def _next_business_day(value: date) -> date:
    return pd.bdate_range(start=value + timedelta(days=1), periods=1).date[0]


def _next_trading_date(frame: pd.DataFrame, value: date) -> date:
    future = sorted(item for item in frame["date"].tolist() if item > value)
    return future[0] if future else _next_business_day(value)


def _position_for_symbol(positions: tuple[Position, ...], symbol: str) -> Position | None:
    for item in positions:
        if item.symbol == symbol:
            return item
    return None


def _floor_to_lot(quantity: float, lot_size: int) -> int:
    if quantity <= 0:
        return 0
    step = max(1, int(lot_size))
    return int(math.floor(quantity / step) * step)


def _safe_float(value: Any) -> float:
    if value is None or pd.isna(value):
        return float("nan")
    return float(value)


def _has_recent_limit_up(symbol: str, as_of_date: date, lookback_days: int) -> bool:
    start = as_of_date - timedelta(days=max(lookback_days * 3, 12))
    end = as_of_date - timedelta(days=1)
    if end < start:
        return False
    try:
        daily = get_daily(symbol, start=start, end=end, refresh=False)
    except Exception:
        return False
    if daily.empty:
        return False
    daily = daily.copy()
    daily["date"] = pd.to_datetime(daily["date"], errors="coerce").dt.date
    recent = daily[daily["date"] < as_of_date].sort_values("date").tail(lookback_days)
    if recent.empty or "limit_up_price" not in recent.columns:
        return False
    close = pd.to_numeric(recent["close"], errors="coerce")
    limit_up = pd.to_numeric(recent["limit_up_price"], errors="coerce")
    high = pd.to_numeric(recent.get("high"), errors="coerce") if "high" in recent.columns else close
    return bool(((close >= limit_up) | (high >= limit_up)).fillna(False).any())


def _latest_float_mv(symbol: str, as_of_date: date) -> float:
    try:
        daily = get_daily(symbol, start=as_of_date - timedelta(days=45), end=as_of_date - timedelta(days=1), refresh=False)
    except Exception:
        return float("nan")
    if daily.empty or "float_mv" not in daily.columns:
        return float("nan")
    daily = daily.copy()
    daily["date"] = pd.to_datetime(daily["date"], errors="coerce").dt.date
    prior = daily[daily["date"] < as_of_date].sort_values("date")
    if prior.empty:
        return float("nan")
    return _safe_float(prior.iloc[-1].get("float_mv"))


def _s1_universe_symbols(paper_cfg: dict[str, Any]) -> tuple[list[str], str]:
    configured = paper_cfg.get("s1_universe")
    if configured is not None:
        return sorted(_normalize_symbol(item) for item in configured), "configured_list"
    universe = _universe(refresh=False).copy()
    if "is_delisted" in universe.columns:
        universe = universe[~universe["is_delisted"].astype(bool)]
    return sorted(universe["symbol"].astype(str).map(_normalize_symbol).unique().tolist()), "full_active_a_share"


def _s1_minute_cache_path(symbol: str, on_date: date, cutoff: str) -> Path:
    safe_cutoff = cutoff.replace(":", "_")
    return CACHE_DIR / f"s1_forward_exact__{on_date.isoformat()}__{safe_cutoff}__{symbol}.parquet"


def _json_float_list(values: pd.Series) -> str:
    clean = []
    for value in values.tolist():
        clean.append(None if value is None or pd.isna(value) else round(float(value), 6))
    return json.dumps(clean, separators=(",", ":"))


def _fetch_s1_minute_candidate(symbol: str, on_date: date, cutoff: str, refresh: bool) -> tuple[dict[str, Any] | None, str | None]:
    path = _s1_minute_cache_path(symbol, on_date, cutoff)
    if path.exists() and not refresh:
        try:
            cached = pd.read_parquet(path)
            if not cached.empty:
                return cached.iloc[-1].to_dict(), None
        except Exception:
            pass
    try:
        bars, period = _fetch_intraday_bars(symbol, on_date, cutoff)
    except Exception as exc:
        return None, f"{symbol}:{type(exc).__name__}"
    if bars.empty or period is None:
        return None, f"{symbol}:no_exact_minute"

    cutoff_ts = pd.Timestamp(f"{on_date.isoformat()} {cutoff}:00")
    assert bars.index.max() <= cutoff_ts
    close = pd.to_numeric(bars["收盘"], errors="coerce")
    high = pd.to_numeric(bars["最高"], errors="coerce")
    vol = pd.to_numeric(bars["成交量"], errors="coerce").fillna(0.0) * 100.0
    amount = pd.to_numeric(bars["成交额"], errors="coerce").fillna(0.0)
    cum_vol = vol.cumsum()
    cum_amount = amount.cumsum()
    vwap = (cum_amount / cum_vol.replace(0, np.nan)).ffill()
    before_1430 = bars[bars.index <= pd.Timestamp(f"{on_date.isoformat()} 14:30:00")]
    after_1430 = bars[bars.index > pd.Timestamp(f"{on_date.isoformat()} 14:30:00")]
    before_high = pd.to_numeric(before_1430["最高"], errors="coerce").max() if not before_1430.empty else np.nan
    after_high = pd.to_numeric(after_1430["最高"], errors="coerce").max() if not after_1430.empty else np.nan
    row = {
        "symbol": _normalize_symbol(symbol),
        "date": on_date.isoformat(),
        "cutoff": cutoff,
        "price_at_cutoff": float(close.iloc[-1]) if pd.notna(close.iloc[-1]) else np.nan,
        "intraday_cum_vol": float(cum_vol.iloc[-1]) if pd.notna(cum_vol.iloc[-1]) else np.nan,
        "vwap_curve": _json_float_list(vwap),
        "vwap_at_cutoff": float(vwap.iloc[-1]) if pd.notna(vwap.iloc[-1]) else np.nan,
        "is_above_vwap": bool((close >= vwap).dropna().all()) if not vwap.dropna().empty else False,
        "high_after_1430": bool(pd.notna(after_high) and pd.notna(before_high) and after_high > before_high),
        "high_after_1430_price": float(after_high) if pd.notna(after_high) else np.nan,
        "source": "ak.stock_zh_a_hist_min_em",
        "source_period": period,
        "source_max_ts": bars.index.max().isoformat(sep=" "),
        "is_proxy": False,
        "proxy_uses_future": False,
    }
    pd.DataFrame([row]).to_parquet(path, index=False)
    return row, None


def _daily_enrich_s1_candidate(row: dict[str, Any], on_date: date, strategy_cfg: dict[str, Any]) -> dict[str, Any] | None:
    symbol = str(row["symbol"])
    try:
        daily = get_daily(symbol, start=on_date - timedelta(days=45), end=on_date - timedelta(days=1), refresh=False)
    except Exception:
        return None
    if daily.empty:
        return None
    daily = daily.copy()
    daily["date"] = pd.to_datetime(daily["date"], errors="coerce").dt.date
    prior = daily[(daily["date"] < on_date) & (~daily["is_suspended"].astype(bool))].sort_values("date")
    if prior.empty:
        return None
    prev_close = _safe_float(prior.iloc[-1].get("close"))
    price = _safe_float(row.get("price_at_cutoff"))
    if pd.isna(prev_close) or prev_close <= 0 or pd.isna(price):
        return None
    avg_vol = pd.to_numeric(prior.tail(5)["vol"], errors="coerce").mean()
    elapsed_fraction = _elapsed_trading_fraction(str(strategy_cfg["decision_time"]))
    cum_vol = _safe_float(row.get("intraday_cum_vol"))
    vol_ratio = float(cum_vol / (avg_vol * elapsed_fraction)) if pd.notna(avg_vol) and avg_vol > 0 and elapsed_fraction > 0 else np.nan
    last_float_mv = _safe_float(prior.iloc[-1].get("float_mv"))
    float_shares = last_float_mv / prev_close if pd.notna(last_float_mv) and last_float_mv > 0 else np.nan
    turnover = float(cum_vol / float_shares) if pd.notna(float_shares) and float_shares > 0 else np.nan
    recent = prior.tail(int(strategy_cfg["limit_up_lookback_days"]))
    close = pd.to_numeric(recent["close"], errors="coerce")
    limit_up = pd.to_numeric(recent["limit_up_price"], errors="coerce")
    high = pd.to_numeric(recent["high"], errors="coerce") if "high" in recent.columns else close
    recent_limit_up = bool(((close >= limit_up) | (high >= limit_up)).fillna(False).any())
    out = dict(row)
    out.update(
        {
            "pct_chg_at_cutoff": float(price / prev_close - 1.0),
            "vol_ratio_at_cutoff": vol_ratio,
            "turnover_at_cutoff": turnover,
            "float_mv": last_float_mv,
            "recent_limit_up": recent_limit_up,
        }
    )
    return out


def _select_s1(on_date: date, paper_cfg: dict[str, Any], strategy_cfg: dict[str, Any]) -> tuple[pd.DataFrame, str, dict[str, Any]]:
    cutoff = str(strategy_cfg["decision_time"])
    symbols, universe_mode = _s1_universe_symbols(paper_cfg)
    workers = max(1, int(paper_cfg.get("s1_snapshot_workers", 16)))
    progress_every = max(1, int(paper_cfg.get("s1_snapshot_progress_every", 250)))
    refresh = bool(paper_cfg.get("s1_snapshot_refresh", False))
    started = time.monotonic()
    rows: list[dict[str, Any]] = []
    failures: list[str] = []
    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = {pool.submit(_fetch_s1_minute_candidate, symbol, on_date, cutoff, refresh): symbol for symbol in symbols}
        for index, future in enumerate(as_completed(futures), start=1):
            row, error = future.result()
            if row is not None:
                rows.append(row)
            elif error:
                failures.append(error)
            if index % progress_every == 0:
                print(f"s1_snapshot_progress {index}/{len(symbols)} ok={len(rows)} fail={len(failures)}")

    stats = {
        "universe_mode": universe_mode,
        "universe_size": len(symbols),
        "scanned": len(symbols),
        "snapshot_ok": len(rows),
        "fetch_failed": len(failures),
        "failures_sample": failures[:10],
        "workers": workers,
        "preliminary_candidates": 0,
        "daily_enriched": 0,
        "daily_dropped": 0,
        "rule_candidates": 0,
        "elapsed_seconds": 0.0,
    }
    if not rows:
        stats["elapsed_seconds"] = time.monotonic() - started
        return pd.DataFrame(), "snapshot_empty", stats
    snapshot = pd.DataFrame(rows)

    as_of_ts = pd.Timestamp(f"{on_date.isoformat()} {cutoff}:00")
    max_ts = pd.to_datetime(snapshot["source_max_ts"], errors="coerce").max()
    if pd.notna(max_ts):
        assert max_ts <= as_of_ts, f"S1 snapshot future timestamp: {max_ts} > {as_of_ts}"
    if bool(snapshot.get("proxy_uses_future", pd.Series([False])).fillna(False).any()):
        raise RuntimeError("S1 forward snapshot unexpectedly used daily proxy")

    rows = snapshot.copy()
    rows["price_at_cutoff"] = pd.to_numeric(rows["price_at_cutoff"], errors="coerce")
    mask = rows["price_at_cutoff"] > 0
    if bool(strategy_cfg.get("require_above_vwap", False)):
        mask &= rows["is_above_vwap"].astype(bool)
    if bool(strategy_cfg.get("require_new_high_after_1430", False)):
        mask &= rows["high_after_1430"].astype(bool)
    prelim = rows[mask].copy()
    stats["preliminary_candidates"] = int(len(prelim))
    if prelim.empty:
        stats["elapsed_seconds"] = time.monotonic() - started
        return prelim, "ok", stats

    enriched: list[dict[str, Any]] = []
    for item in prelim.sort_values("symbol").to_dict("records"):
        enriched_item = _daily_enrich_s1_candidate(item, on_date, strategy_cfg)
        if enriched_item is None:
            stats["daily_dropped"] += 1
            continue
        enriched.append(enriched_item)
    stats["daily_enriched"] = int(len(enriched))
    if not enriched:
        stats["elapsed_seconds"] = time.monotonic() - started
        return pd.DataFrame(), "ok", stats

    candidates = pd.DataFrame(enriched)
    for col in ("pct_chg_at_cutoff", "vol_ratio_at_cutoff", "turnover_at_cutoff", "float_mv"):
        candidates[col] = pd.to_numeric(candidates[col], errors="coerce")
    rule_mask = (
        (candidates["pct_chg_at_cutoff"] >= float(strategy_cfg["pct_change_min"]))
        & (candidates["pct_chg_at_cutoff"] <= float(strategy_cfg["pct_change_max"]))
        & (candidates["vol_ratio_at_cutoff"] >= float(strategy_cfg["volume_ratio_min"]))
        & (candidates["turnover_at_cutoff"] >= float(strategy_cfg["turnover_min"]))
        & (candidates["turnover_at_cutoff"] <= float(strategy_cfg["turnover_max"]))
        & (candidates["float_mv"] < float(strategy_cfg["float_mv_max"]))
        & (candidates["recent_limit_up"].astype(bool))
    )
    selected = candidates[rule_mask].copy()
    stats["rule_candidates"] = int(len(selected))
    if selected.empty:
        stats["elapsed_seconds"] = time.monotonic() - started
        return selected, "ok", stats
    selected = selected.sort_values(["pct_chg_at_cutoff", "vol_ratio_at_cutoff", "symbol"], ascending=[False, False, True])
    stats["elapsed_seconds"] = time.monotonic() - started
    return selected.head(int(strategy_cfg["max_positions"])).reset_index(drop=True), "ok", stats


def _trend_signal_orders(on_date: date, broker, trend_data: dict[str, pd.DataFrame], trend_cfg: dict[str, Any], lot_size: int) -> tuple[list[Order], str]:
    asset = str(trend_cfg["asset"])
    frame = trend_data.get(asset)
    if frame is None or frame.empty or on_date not in set(frame["date"].tolist()):
        return [], "trend_skipped_no_bar_for_date"
    strategy = S3BTrendStrategy(trend_cfg)
    ctx = {
        "data": {asset: frame[frame["date"] <= on_date].copy()},
        "positions": broker.positions(),
        "cash": broker.cash(),
        "nav": broker.nav(),
        "lot_size": lot_size,
    }
    return strategy.generate_signals(on_date, ctx), "ok"


def run_forward(on_date: date, phase: str = "all") -> dict[str, Any]:
    if phase not in {"open", "select", "all"}:
        raise ValueError(f"Unsupported forward phase: {phase}")
    paper_cfg = _load_yaml("paper.yaml")
    strategy_cfg = _load_yaml("strategy.yaml")
    cost_cfg = CostConfig.from_mapping(_load_yaml("cost.yaml"))
    trend_cfg = strategy_cfg["s3b_trend"].copy()
    trend_asset = str(trend_cfg["asset"])
    state_dir = PROJECT_ROOT / str(paper_cfg["state_dir"])
    s1_lot = int(paper_cfg["lot_sizes"]["s1_tail"])
    trend_lot = int(paper_cfg["lot_sizes"]["s3b_trend"])

    symbols = _state_symbols(state_dir, FORWARD_ACCOUNT)
    symbols.add(trend_asset)
    configured_s1 = paper_cfg.get("s1_universe")
    if configured_s1 is not None:
        symbols.update(_normalize_symbol(item) for item in configured_s1)
    market_data = _load_market_data(symbols, on_date - timedelta(days=800), on_date + timedelta(days=10), refresh=True)

    PaperBroker = _paper_broker_cls()
    broker = PaperBroker(
        state_dir=state_dir,
        account=FORWARD_ACCOUNT,
        trade_date=on_date,
        market_data=market_data,
        cost_config=cost_cfg,
        initial_cash=float(paper_cfg["initial_cash"]),
    )
    due_executions = broker.process_pending()

    s1_sell_orders = []
    if phase in {"open", "all"}:
        for position in broker.positions():
            if position.symbol == trend_asset or position.quantity <= 0 or not position.sellable:
                continue
            order = Order(symbol=position.symbol, side="sell", quantity=position.quantity, submitted_date=on_date)
            s1_sell_orders.append(
                broker.submit(
                    order,
                    strategy="s1_tail",
                    order_id=f"s1_sell:{on_date.isoformat()}:{position.symbol}:{position.quantity}",
                    lot_size=s1_lot,
                )
            )

    selected = pd.DataFrame()
    s1_note = "phase_skipped"
    s1_stats = {
        "universe_mode": "not_scanned",
        "universe_size": 0,
        "scanned": 0,
        "snapshot_ok": 0,
        "fetch_failed": 0,
        "failures_sample": [],
        "workers": 0,
        "preliminary_candidates": 0,
        "daily_enriched": 0,
        "daily_dropped": 0,
        "rule_candidates": 0,
        "elapsed_seconds": 0.0,
    }
    if phase in {"select", "all"}:
        selected, s1_note, s1_stats = _select_s1(on_date, paper_cfg, strategy_cfg["s1_tail"])
    trend_frame = market_data.get(trend_asset, pd.DataFrame())
    next_date = _next_trading_date(trend_frame, on_date) if not trend_frame.empty else _next_business_day(on_date)
    s1_buy_orders = []
    if phase in {"select", "all"} and not selected.empty:
        target_value = broker.nav() / max(int(strategy_cfg["s1_tail"]["max_positions"]), 1)
        for row in selected.itertuples(index=False):
            symbol = str(row.symbol)
            price = float(row.price_at_cutoff)
            quantity = _floor_to_lot(target_value / price, s1_lot)
            if quantity <= 0:
                continue
            order = Order(symbol=symbol, side="buy", quantity=quantity, submitted_date=on_date)
            s1_buy_orders.append(
                broker.queue(
                    order,
                    execute_date=next_date,
                    strategy="s1_tail",
                    order_id=f"s1_buy:{on_date.isoformat()}:{next_date.isoformat()}:{symbol}",
                    lot_size=s1_lot,
                )
            )

    if phase in {"select", "all"}:
        trend_orders, trend_note = _trend_signal_orders(on_date, broker, {trend_asset: trend_frame}, trend_cfg, trend_lot)
    else:
        trend_orders, trend_note = [], "open_phase_pending_only"
    queued_trend = []
    for order in trend_orders:
        queued_trend.append(
            broker.queue(
                order,
                execute_date=next_date,
                strategy="s3b_trend",
                order_id=f"s3b_trend:{on_date.isoformat()}:{next_date.isoformat()}:{order.symbol}:{order.side}:{order.quantity}",
                lot_size=trend_lot,
            )
        )

    nav = broker.mark_nav(on_date)
    summary = {
        "date": on_date.isoformat(),
        "phase": phase,
        "nav": nav,
        "cash": broker.cash(),
        "positions": [position.__dict__ | {"buy_date": position.buy_date.isoformat()} for position in broker.positions()],
        "pending_orders": list(broker.pending_orders()),
        "due_executions": due_executions,
        "s1_selected": selected.to_dict("records") if not selected.empty else [],
        "s1_note": s1_note,
        "s1_stats": s1_stats,
        "s1_sell_orders": s1_sell_orders,
        "s1_buy_orders": s1_buy_orders,
        "trend_orders": queued_trend,
        "trend_note": trend_note,
        "state_path": str(broker.state_path),
        "next_trade_date": next_date.isoformat(),
    }
    _append_forward_log(summary, paper_cfg)
    _write_forward_status(summary, paper_cfg)
    return summary


def _append_forward_log(summary: dict[str, Any], paper_cfg: dict[str, Any]) -> None:
    path = PROJECT_ROOT / str(paper_cfg["forward_log_path"])
    path.parent.mkdir(parents=True, exist_ok=True)
    row = {
        "date": summary["date"],
        "phase": summary["phase"],
        "nav": f"{summary['nav']:.6f}",
        "cash": f"{summary['cash']:.6f}",
        "positions": len(summary["positions"]),
        "pending_orders": len(summary["pending_orders"]),
        "s1_universe_size": summary["s1_stats"]["universe_size"],
        "s1_scanned": summary["s1_stats"]["scanned"],
        "s1_snapshot_ok": summary["s1_stats"]["snapshot_ok"],
        "s1_fetch_failed": summary["s1_stats"]["fetch_failed"],
        "s1_selected": len(summary["s1_selected"]),
        "trend_orders": len(summary["trend_orders"]),
        "s1_note": summary["s1_note"],
        "trend_note": summary["trend_note"],
        "s1_elapsed_seconds": f"{float(summary['s1_stats']['elapsed_seconds']):.3f}",
    }
    rows = []
    if path.exists():
        with path.open("r", encoding="utf-8", newline="") as fh:
            rows = [item for item in csv.DictReader(fh) if item.get("date") != row["date"]]
    rows.append(row)
    rows.sort(key=lambda item: item["date"])
    with path.open("w", encoding="utf-8", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=list(row.keys()))
        writer.writeheader()
        writer.writerows(rows)


def _write_forward_status(summary: dict[str, Any], paper_cfg: dict[str, Any]) -> None:
    path = PROJECT_ROOT / str(paper_cfg["forward_status_path"])
    path.parent.mkdir(parents=True, exist_ok=True)
    trend_position = next((item for item in summary["positions"] if item["symbol"] == _load_yaml("strategy.yaml")["s3b_trend"]["asset"]), None)
    lines = [
        "# Paper Forward Status",
        "",
        f"- date: {summary['date']}",
        f"- phase: {summary['phase']}",
        f"- nav: {summary['nav']:.2f}",
        f"- cash: {summary['cash']:.2f}",
        f"- state_path: {summary['state_path']}",
        f"- next_trade_date_for_new_orders: {summary['next_trade_date']}",
        f"- S1 selected: {len(summary['s1_selected'])}",
        f"- S1 note: {summary['s1_note']}",
        f"- S1 queued buys: {len(summary['s1_buy_orders'])}",
        f"- S1 same-day sells: {len(summary['s1_sell_orders'])}",
        f"- trend queued orders: {len(summary['trend_orders'])}",
        f"- trend note: {summary['trend_note']}",
        f"- trend position: {trend_position or 'none'}",
        f"- due executions processed: {len(summary['due_executions'])}",
        "",
        "## S1 Scan",
        f"- universe_mode: {summary['s1_stats']['universe_mode']}",
        f"- universe_size: {summary['s1_stats']['universe_size']}",
        f"- scanned: {summary['s1_stats']['scanned']}",
        f"- snapshot_ok: {summary['s1_stats']['snapshot_ok']}",
        f"- fetch_failed: {summary['s1_stats']['fetch_failed']}",
        f"- preliminary_candidates: {summary['s1_stats']['preliminary_candidates']}",
        f"- daily_enriched: {summary['s1_stats']['daily_enriched']}",
        f"- daily_dropped: {summary['s1_stats']['daily_dropped']}",
        f"- rule_candidates: {summary['s1_stats']['rule_candidates']}",
        f"- elapsed_seconds: {float(summary['s1_stats']['elapsed_seconds']):.3f}",
        f"- failures_sample: {summary['s1_stats']['failures_sample']}",
        "",
        "## Pending Orders",
        "```json",
        json.dumps(summary["pending_orders"], ensure_ascii=False, indent=2, sort_keys=True),
        "```",
    ]
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def run_oos_walkforward() -> dict[str, Any]:
    paper_cfg = _load_yaml("paper.yaml")
    strategy_cfg = _load_yaml("strategy.yaml")
    backtest_cfg = _load_yaml("backtest.yaml")
    cost_cfg = CostConfig.from_mapping(_load_yaml("cost.yaml"))
    trend_cfg = strategy_cfg["s3b_trend"].copy()
    asset = str(trend_cfg["asset"])
    start = _parse_date(backtest_cfg["full_history"]["oos"]["start"])
    end = _parse_date(backtest_cfg["full_history"]["oos"]["end"])
    warmup_start = start - timedelta(days=max(int(trend_cfg["ma_len"]) * 3, 700))
    data = _load_market_data({asset}, warmup_start, end, refresh=False)
    if asset not in data or data[asset].empty:
        raise RuntimeError(f"trend proxy data unavailable for {asset}")
    frame = data[asset]
    dates = [item for item in frame["date"].tolist() if start <= item <= end]
    if len(dates) < 2:
        raise RuntimeError("not enough OOS dates for paper walkforward")

    PaperBroker = _paper_broker_cls()
    state_dir = PROJECT_ROOT / str(paper_cfg["state_dir"]) / "oos_walkforward"
    lot_size = int(paper_cfg["lot_sizes"]["s3b_trend"])
    broker = None
    for idx, current in enumerate(dates):
        broker = PaperBroker(
            state_dir=state_dir,
            account=TREND_ACCOUNT,
            trade_date=current,
            market_data={asset: frame},
            cost_config=cost_cfg,
            initial_cash=float(paper_cfg["initial_cash"]),
            reset=idx == 0,
        )
        broker.process_pending()
        if idx < len(dates) - 1:
            orders, note = _trend_signal_orders(current, broker, {asset: frame}, trend_cfg, lot_size)
            if note == "ok":
                next_date = dates[idx + 1]
                for order in orders:
                    broker.queue(
                        order,
                        execute_date=next_date,
                        strategy="s3b_trend",
                        order_id=f"oos_s3b:{current.isoformat()}:{next_date.isoformat()}:{order.symbol}:{order.side}:{order.quantity}",
                        lot_size=lot_size,
                    )
        broker.mark_nav(current)

    assert broker is not None
    final_date = dates[-1]
    for position in broker.positions():
        if position.quantity <= 0:
            continue
        broker.submit(
            Order(symbol=position.symbol, side="sell", quantity=position.quantity, submitted_date=final_date),
            strategy="s3b_trend_liquidate",
            order_id=f"oos_liquidate:{final_date.isoformat()}:{position.symbol}:{position.quantity}",
            lot_size=lot_size,
        )
    final_nav = broker.mark_nav(final_date)
    metrics = _paper_metrics(float(paper_cfg["initial_cash"]), broker)
    report_path = PAPER_DIR / "oos_walkforward_trend_proxy.md"
    _write_oos_report(report_path, asset, start, end, metrics, broker)
    return {
        "asset": asset,
        "start": start.isoformat(),
        "end": end.isoformat(),
        "final_nav": final_nav,
        "state_path": str(broker.state_path),
        "report_path": str(report_path),
        **metrics,
    }


def _paper_metrics(initial_cash: float, broker) -> dict[str, float]:
    nav_history = pd.DataFrame(broker.state.get("nav_history", []))
    if nav_history.empty:
        max_dd = 0.0
    else:
        nav = pd.to_numeric(nav_history["nav"], errors="coerce")
        peak = nav.cummax()
        dd = nav / peak - 1.0
        max_dd = abs(float(dd.min())) if not dd.empty else 0.0
    pnls = [float(item["pnl"]) for item in broker.trades()]
    wins = [item for item in pnls if item > 0]
    losses = [item for item in pnls if item < 0]
    gross_profit = float(sum(wins))
    gross_loss = float(abs(sum(losses)))
    pf = math.inf if gross_profit > 0 and gross_loss == 0 else gross_profit / gross_loss if gross_loss > 0 else 0.0
    return {
        "return": broker.nav() / initial_cash - 1.0,
        "max_drawdown": max_dd,
        "trades": float(len(pnls)),
        "expectancy": float(np.mean(pnls)) if pnls else 0.0,
        "profit_factor": pf,
        "win_rate": float(len(wins) / len(pnls)) if pnls else 0.0,
        "filled_orders": float(len([item for item in broker.executions() if item["status"] == "filled"])),
        "rejected_orders": float(len([item for item in broker.executions() if item["status"] == "rejected"])),
    }


def _write_oos_report(path: Path, asset: str, start: date, end: date, metrics: dict[str, float], broker) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = [
        "# OOS Walkforward Trend Proxy",
        "",
        "范围：仅趋势防御分量 S3b；S1 无历史分钟数据，不能 walkforward，只能 forward 累积。",
        f"asset: {asset}",
        f"period: {start}..{end}",
        "",
        "| metric | value |",
        "|---|---:|",
        f"| return | {_fmt_pct(metrics['return'])} |",
        f"| max_drawdown | {_fmt_pct(metrics['max_drawdown'])} |",
        f"| trades | {int(metrics['trades'])} |",
        f"| expectancy | {metrics['expectancy']:.2f} |",
        f"| profit_factor | {_fmt_float(metrics['profit_factor'])} |",
        f"| win_rate | {_fmt_pct(metrics['win_rate'])} |",
        f"| filled_orders | {int(metrics['filled_orders'])} |",
        f"| rejected_orders | {int(metrics['rejected_orders'])} |",
        "",
        f"state_path: {broker.state_path}",
    ]
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--mode", required=True, choices=["forward", "oos_walkforward"])
    parser.add_argument("--date", type=str, default=None)
    parser.add_argument("--phase", choices=["open", "select", "all"], default="all")
    args = parser.parse_args()
    if args.mode == "forward":
        if not args.date:
            raise SystemExit("--date is required for forward mode")
        summary = run_forward(_parse_date(args.date), phase=args.phase)
        print(f"wrote {PROJECT_ROOT / _load_yaml('paper.yaml')['forward_status_path']}")
        print(f"state_path={summary['state_path']}")
        print(
            f"forward date={summary['date']} phase={summary['phase']} nav={summary['nav']:.2f} cash={summary['cash']:.2f} "
            f"s1_selected={len(summary['s1_selected'])} trend_orders={len(summary['trend_orders'])} "
            f"positions={len(summary['positions'])} pending={len(summary['pending_orders'])} "
            f"s1_scanned={summary['s1_stats']['scanned']} s1_failed={summary['s1_stats']['fetch_failed']} "
            f"s1_elapsed={float(summary['s1_stats']['elapsed_seconds']):.1f}s "
            f"s1_note={summary['s1_note']} trend_note={summary['trend_note']}"
        )
        return
    result = run_oos_walkforward()
    print(f"wrote {result['report_path']}")
    print(f"state_path={result['state_path']}")
    print(
        f"oos_walkforward asset={result['asset']} period={result['start']}..{result['end']} "
        f"return={result['return']:.4%} dd={result['max_drawdown']:.4%} "
        f"pf={_fmt_float(result['profit_factor'])} trades={int(result['trades'])}"
    )


if __name__ == "__main__":
    main()
