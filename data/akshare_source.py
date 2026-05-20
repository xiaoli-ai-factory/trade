"""AkShare data access with cache, retries, and explicit PIT limits.

The S1 intraday snapshot is exact only when AkShare minute bars are
available for the requested date. For older dates AkShare does not expose
the needed 14:50 minute state, so this module returns a daily-bar proxy and
marks it with ``is_proxy=True`` and ``proxy_uses_future=True``. That proxy is
not Gate-valid for a no-future-function S1 backtest; it exists only so the
data limitation is explicit and reproducible.
"""

from __future__ import annotations

import argparse
import hashlib
import inspect
import json
import math
import time
from datetime import date, datetime, time as dt_time, timedelta
from pathlib import Path
from typing import Any, Callable, Iterable

import akshare as ak
import baostock as bs
import numpy as np
import pandas as pd
import yaml


PROJECT_ROOT = Path(__file__).resolve().parents[1]
CONFIG_DIR = PROJECT_ROOT / "configs"
CACHE_DIR = PROJECT_ROOT / "data" / "cache"

RETRY_ATTEMPTS = 5
RETRY_BASE_SLEEP_SECONDS = 1.2
RATE_LIMIT_SLEEP_SECONDS = 0.3
VOL_RATIO_LOOKBACK_DAYS = 5
A_SHARE_TRADING_MINUTES = 240
DAILY_PROXY_CLOSE_NEAR_HIGH_RATIO = 0.995

DAILY_COLUMNS = [
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
    "is_st",
    "is_suspended",
    "limit_up_price",
    "limit_down_price",
    "is_delisted",
]

S3_DAILY_COLUMNS = DAILY_COLUMNS + ["name", "source"]

SNAPSHOT_COLUMNS = [
    "symbol",
    "date",
    "cutoff",
    "price_at_cutoff",
    "pct_chg_at_cutoff",
    "vwap_curve",
    "vwap_at_cutoff",
    "is_above_vwap",
    "high_after_1430",
    "high_after_1430_price",
    "vol_ratio_at_cutoff",
    "turnover_at_cutoff",
    "source",
    "source_period",
    "source_max_ts",
    "is_proxy",
    "proxy_uses_future",
]

BAOSTOCK_5MIN_FIELDS = "date,time,code,open,high,low,close,volume,amount,adjustflag"


def _load_yaml(name: str) -> dict[str, Any]:
    with (CONFIG_DIR / name).open("r", encoding="utf-8") as fh:
        loaded = yaml.safe_load(fh)
    return loaded or {}


def _parse_date(value: str | date | pd.Timestamp) -> date:
    if isinstance(value, pd.Timestamp):
        return value.date()
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    return pd.Timestamp(str(value)).date()


def _yyyymmdd(value: str | date | pd.Timestamp) -> str:
    return _parse_date(value).strftime("%Y%m%d")


def _date_str(value: str | date | pd.Timestamp) -> str:
    return _parse_date(value).isoformat()


def _normalize_symbol(symbol: str | int) -> str:
    text = str(symbol).strip().lower()
    if text.startswith(("sh", "sz", "bj")):
        text = text[2:]
    if not text.isdigit():
        raise ValueError(f"Unsupported symbol: {symbol!r}")
    return text.zfill(6)


def _market_symbol(symbol: str | int) -> str:
    code = _normalize_symbol(symbol)
    if code.startswith(("6", "9")):
        return f"sh{code}"
    if _is_bj_symbol(code):
        return f"bj{code}"
    return f"sz{code}"


def _baostock_symbol(symbol: str | int) -> str:
    code = _normalize_symbol(symbol)
    if code.startswith(("6", "9")):
        return f"sh.{code}"
    if _is_bj_symbol(code):
        return f"bj.{code}"
    return f"sz.{code}"


def _is_bj_symbol(symbol: str) -> bool:
    code = _normalize_symbol(symbol)
    return code.startswith(("4", "8", "920"))


def _is_st_name(name: Any) -> bool:
    if not isinstance(name, str):
        return False
    upper_name = name.upper()
    return "ST" in upper_name or upper_name.startswith("PT")


def _cache_path(kind: str, *parts: Any) -> Path:
    safe_parts = [kind]
    for part in parts:
        text = str(part)
        text = "".join(ch if ch.isalnum() or ch in ("-", "_") else "_" for ch in text)
        safe_parts.append(text[:80])
    raw = "|".join(map(str, parts))
    digest = hashlib.sha1(raw.encode("utf-8")).hexdigest()[:12]
    filename = "__".join(safe_parts + [digest]) + ".parquet"
    return CACHE_DIR / filename


def _read_cache(path: Path, refresh: bool) -> pd.DataFrame | None:
    if refresh or not path.exists():
        return None
    return pd.read_parquet(path)


def _write_cache(path: Path, df: pd.DataFrame) -> None:
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    df.to_parquet(path, index=False)


def _call_akshare(
    func: Callable[..., pd.DataFrame],
    *args: Any,
    attempts: int = RETRY_ATTEMPTS,
    **kwargs: Any,
) -> pd.DataFrame:
    last_exc: Exception | None = None
    for attempt in range(1, attempts + 1):
        try:
            result = func(*args, **kwargs)
            time.sleep(RATE_LIMIT_SLEEP_SECONDS)
            return result
        except Exception as exc:  # AkShare raises requests/json exceptions directly.
            last_exc = exc
            if attempt >= attempts:
                break
            sleep_for = RETRY_BASE_SLEEP_SECONDS * attempt
            time.sleep(sleep_for)
    raise RuntimeError(f"AkShare call failed: {func.__name__}") from last_exc


def _baostock_login() -> None:
    result = bs.login()
    if result.error_code != "0":
        raise RuntimeError(f"BaoStock login failed: {result.error_code} {result.error_msg}")


def _call_baostock_history_5min(
    symbol: str,
    on_date: date,
    attempts: int = RETRY_ATTEMPTS,
) -> pd.DataFrame:
    last_error: str | None = None
    bs_code = _baostock_symbol(symbol)
    for attempt in range(1, attempts + 1):
        result = bs.query_history_k_data_plus(
            bs_code,
            BAOSTOCK_5MIN_FIELDS,
            start_date=on_date.isoformat(),
            end_date=on_date.isoformat(),
            frequency="5",
            adjustflag="3",
        )
        if result.error_code == "0":
            rows = list(result.data)
            if not rows:
                return pd.DataFrame(columns=result.fields)
            return pd.DataFrame(rows, columns=result.fields)
        last_error = f"{result.error_code} {result.error_msg}"
        time.sleep(RETRY_BASE_SLEEP_SECONDS * attempt)
    raise RuntimeError(f"BaoStock 5min call failed for {bs_code} {on_date}: {last_error}")


def _active_code_name(refresh: bool = False) -> pd.DataFrame:
    path = _cache_path("active_code_name", "akshare")
    cached = _read_cache(path, refresh)
    if cached is not None:
        return cached
    df = _call_akshare(ak.stock_info_a_code_name)
    df = df.rename(columns={"code": "symbol", "name": "name"})
    df["symbol"] = df["symbol"].astype(str).str.zfill(6)
    df["name"] = df["name"].astype(str)
    df = df[["symbol", "name"]].drop_duplicates("symbol")
    _write_cache(path, df)
    return df


def _delisted_code_name(refresh: bool = False) -> pd.DataFrame:
    path = _cache_path("delisted_code_name", "akshare")
    cached = _read_cache(path, refresh)
    if cached is not None:
        return cached

    frames: list[pd.DataFrame] = []
    sh = _call_akshare(ak.stock_info_sh_delist)
    if not sh.empty:
        tmp = pd.DataFrame(
            {
                "symbol": sh["公司代码"].astype(str).str.zfill(6),
                "name": sh["公司简称"].astype(str),
                "list_date": pd.to_datetime(sh["上市日期"], errors="coerce").dt.date,
                "delist_date": pd.to_datetime(sh["暂停上市日期"], errors="coerce").dt.date,
                "exchange": "SH",
            }
        )
        frames.append(tmp)

    sz = _call_akshare(ak.stock_info_sz_delist)
    if not sz.empty:
        tmp = pd.DataFrame(
            {
                "symbol": sz["证券代码"].astype(str).str.zfill(6),
                "name": sz["证券简称"].astype(str),
                "list_date": pd.to_datetime(sz["上市日期"], errors="coerce").dt.date,
                "delist_date": pd.to_datetime(sz["终止上市日期"], errors="coerce").dt.date,
                "exchange": "SZ",
            }
        )
        frames.append(tmp)

    if frames:
        out = pd.concat(frames, ignore_index=True).drop_duplicates("symbol")
    else:
        out = pd.DataFrame(columns=["symbol", "name", "list_date", "delist_date", "exchange"])
    _write_cache(path, out)
    return out


def _universe(refresh: bool = False) -> pd.DataFrame:
    config = _load_yaml("backtest.yaml")
    universe_cfg = config.get("universe", {})
    include_delisted = bool(universe_cfg.get("include_delisted", True))
    include_st = bool(universe_cfg.get("include_st", True))
    exclude_bj = bool(universe_cfg.get("exclude_bj", True))

    active = _active_code_name(refresh=refresh).copy()
    active["is_delisted"] = False
    active["list_date"] = pd.NaT
    active["delist_date"] = pd.NaT
    active["exchange"] = np.where(active["symbol"].str.startswith("6"), "SH", "SZ")

    frames = [active]
    if include_delisted:
        delisted = _delisted_code_name(refresh=refresh).copy()
        delisted["is_delisted"] = True
        frames.append(delisted)

    out = pd.concat(frames, ignore_index=True, sort=False)
    out = out.drop_duplicates("symbol", keep="last")
    if exclude_bj:
        out = out[~out["symbol"].map(_is_bj_symbol)]
    if not include_st:
        out = out[~out["name"].map(_is_st_name)]
    out = out.reset_index(drop=True)
    return out


def _symbol_metadata(symbol: str, refresh: bool = False) -> dict[str, Any]:
    code = _normalize_symbol(symbol)
    uni = _universe(refresh=refresh)
    row = uni[uni["symbol"] == code]
    if row.empty:
        return {
            "symbol": code,
            "name": "",
            "is_delisted": False,
            "is_st": False,
            "delist_date": pd.NaT,
        }
    item = row.iloc[0].to_dict()
    item["is_st"] = _is_st_name(item.get("name"))
    return item


def _trade_dates(start: date, end: date, refresh: bool = False) -> pd.Series:
    path = _cache_path("trade_dates", "sina")
    cached = _read_cache(path, refresh)
    if cached is None:
        df = _call_akshare(ak.tool_trade_date_hist_sina)
        df["trade_date"] = pd.to_datetime(df["trade_date"], errors="coerce").dt.date
        df = df.dropna().drop_duplicates().sort_values("trade_date")
        _write_cache(path, df)
    else:
        df = cached
        df["trade_date"] = pd.to_datetime(df["trade_date"], errors="coerce").dt.date
    dates = df["trade_date"]
    return dates[(dates >= start) & (dates <= end)].reset_index(drop=True)


def _fetch_daily_eastmoney(symbol: str, start: date, end: date) -> pd.DataFrame:
    return _call_akshare(
        ak.stock_zh_a_hist,
        symbol=symbol,
        period="daily",
        start_date=start.strftime("%Y%m%d"),
        end_date=end.strftime("%Y%m%d"),
        adjust="",
        timeout=15,
    )


def _fetch_daily_tencent(symbol: str, start: date, end: date) -> pd.DataFrame:
    return _call_akshare(
        ak.stock_zh_a_hist_tx,
        symbol=_market_symbol(symbol),
        start_date=start.strftime("%Y%m%d"),
        end_date=end.strftime("%Y%m%d"),
        adjust="",
        timeout=15,
    )


def _fetch_daily_sina(symbol: str, start: date, end: date) -> pd.DataFrame:
    return _call_akshare(
        ak.stock_zh_a_daily,
        symbol=_market_symbol(symbol),
        start_date=start.strftime("%Y%m%d"),
        end_date=end.strftime("%Y%m%d"),
        adjust="",
    )


def _normalize_daily_eastmoney(raw: pd.DataFrame, symbol: str) -> pd.DataFrame:
    if raw.empty:
        return pd.DataFrame()
    out = pd.DataFrame(
        {
            "symbol": raw["股票代码"].astype(str).str.zfill(6),
            "date": pd.to_datetime(raw["日期"], errors="coerce").dt.date,
            "open": pd.to_numeric(raw["开盘"], errors="coerce"),
            "high": pd.to_numeric(raw["最高"], errors="coerce"),
            "low": pd.to_numeric(raw["最低"], errors="coerce"),
            "close": pd.to_numeric(raw["收盘"], errors="coerce"),
            "vol": pd.to_numeric(raw["成交量"], errors="coerce") * 100.0,
            "amount": pd.to_numeric(raw["成交额"], errors="coerce"),
            "pct_chg": pd.to_numeric(raw["涨跌幅"], errors="coerce") / 100.0,
            "turnover": pd.to_numeric(raw["换手率"], errors="coerce") / 100.0,
            "source": "eastmoney_stock_zh_a_hist",
        }
    )
    out["symbol"] = out["symbol"].fillna(_normalize_symbol(symbol))
    return out.dropna(subset=["date"]).sort_values("date").reset_index(drop=True)


def _normalize_daily_sina(raw: pd.DataFrame, symbol: str) -> pd.DataFrame:
    if raw.empty:
        return pd.DataFrame()
    close = pd.to_numeric(raw["close"], errors="coerce")
    out = pd.DataFrame(
        {
            "symbol": _normalize_symbol(symbol),
            "date": pd.to_datetime(raw["date"], errors="coerce").dt.date,
            "open": pd.to_numeric(raw["open"], errors="coerce"),
            "high": pd.to_numeric(raw["high"], errors="coerce"),
            "low": pd.to_numeric(raw["low"], errors="coerce"),
            "close": close,
            "vol": pd.to_numeric(raw["volume"], errors="coerce"),
            "amount": pd.to_numeric(raw["amount"], errors="coerce"),
            "pct_chg": close.pct_change(),
            "turnover": pd.to_numeric(raw["turnover"], errors="coerce"),
            "source": "sina_stock_zh_a_daily",
        }
    )
    return out.dropna(subset=["date"]).sort_values("date").reset_index(drop=True)


def _empty_s3_daily() -> pd.DataFrame:
    return pd.DataFrame(columns=S3_DAILY_COLUMNS)


def _finalize_s3_daily(
    df: pd.DataFrame,
    symbol: str,
    start: date,
    end: date,
    name: str,
    source: str,
) -> pd.DataFrame:
    if df.empty:
        return _empty_s3_daily()

    out = df[(df["date"] >= start) & (df["date"] <= end)].copy()
    out["symbol"] = out["symbol"].astype(str)
    out["name"] = name
    out["source"] = source
    out["pct_chg"] = pd.to_numeric(out["close"], errors="coerce").pct_change()
    if "pct_chg_raw" in out.columns:
        raw_pct = pd.to_numeric(out["pct_chg_raw"], errors="coerce")
        out["pct_chg"] = raw_pct.fillna(out["pct_chg"])
    for col in ["turnover", "float_mv", "limit_up_price", "limit_down_price"]:
        if col not in out.columns:
            out[col] = np.nan
    out["is_st"] = False
    out["is_suspended"] = False
    out["is_delisted"] = False
    for col in ["open", "high", "low", "close", "vol", "amount"]:
        out[col] = pd.to_numeric(out[col], errors="coerce")
    return out[S3_DAILY_COLUMNS].dropna(subset=["date"]).sort_values("date").reset_index(drop=True)


def _normalize_index_symbol(symbol: str | int) -> str:
    text = str(symbol).strip().lower()
    if text.startswith(("sh", "sz", "csi", "bj")):
        return text
    code = _normalize_symbol(text)
    if code in {"000300", "000905", "000852"}:
        return f"sh{code}"
    if code.startswith(("399", "159")):
        return f"sz{code}"
    return f"sh{code}"


def _index_symbol_candidates(symbol: str | int) -> list[str]:
    first = _normalize_index_symbol(symbol)
    code = first[2:] if first[:2] in {"sh", "sz", "bj"} else first[3:] if first.startswith("csi") else first
    candidates = [first]
    if code in {"000300", "000905", "000852"}:
        candidates.extend([f"sh{code}", f"csi{code}"])
    return list(dict.fromkeys(candidates))


def _fetch_index_daily(symbol: str, start: date, end: date) -> pd.DataFrame:
    last = pd.DataFrame()
    errors: list[Exception] = []
    for candidate in _index_symbol_candidates(symbol):
        try:
            last = _call_akshare(
                ak.stock_zh_index_daily_em,
                symbol=candidate,
                start_date=start.strftime("%Y%m%d"),
                end_date=end.strftime("%Y%m%d"),
            )
        except Exception as exc:
            errors.append(exc)
            continue
        if not last.empty:
            last.attrs["source"] = "eastmoney_stock_zh_index_daily_em"
            return last
    try:
        last = _call_akshare(ak.stock_zh_index_daily, symbol=_normalize_index_symbol(symbol))
    except Exception as exc:
        errors.append(exc)
    else:
        if not last.empty:
            last["date"] = pd.to_datetime(last["date"], errors="coerce").dt.date
            last = last[(last["date"] >= start) & (last["date"] <= end)].copy()
            last.attrs["source"] = "sina_stock_zh_index_daily"
            return last
    if errors:
        raise RuntimeError(f"Index daily fetch failed for {symbol}") from errors[-1]
    return last


def _normalize_index_daily(raw: pd.DataFrame, symbol: str) -> pd.DataFrame:
    if raw.empty:
        return pd.DataFrame()
    amount = pd.to_numeric(raw["amount"], errors="coerce") if "amount" in raw.columns else np.nan
    out = pd.DataFrame(
        {
            "symbol": _normalize_index_symbol(symbol),
            "date": pd.to_datetime(raw["date"], errors="coerce").dt.date,
            "open": pd.to_numeric(raw["open"], errors="coerce"),
            "high": pd.to_numeric(raw["high"], errors="coerce"),
            "low": pd.to_numeric(raw["low"], errors="coerce"),
            "close": pd.to_numeric(raw["close"], errors="coerce"),
            "vol": pd.to_numeric(raw["volume"], errors="coerce") * 100.0,
            "amount": amount,
        }
    )
    return out.dropna(subset=["date"]).sort_values("date").reset_index(drop=True)


def get_index_daily(
    symbol: str | int,
    start: str | date = "1990-01-01",
    end: str | date | None = None,
    refresh: bool = False,
) -> pd.DataFrame:
    """Return index daily bars aligned to the stock daily field contract."""
    start_date = _parse_date(start)
    end_date = _parse_date(end or date.today())
    code = _normalize_index_symbol(symbol)
    path = _cache_path("index_daily", code, _date_str(start_date), _date_str(end_date))
    cached = _read_cache(path, refresh)
    if cached is not None:
        return cached

    raw = _fetch_index_daily(code, start_date, end_date)
    normalized = _normalize_index_daily(raw, code)
    source = str(raw.attrs.get("source", "eastmoney_stock_zh_index_daily_em"))
    out = _finalize_s3_daily(normalized, code, start_date, end_date, name=code, source=source)
    _write_cache(path, out)
    return out


def _fetch_etf_daily(symbol: str, start: date, end: date) -> pd.DataFrame:
    return _call_akshare(
        ak.fund_etf_hist_em,
        symbol=_normalize_symbol(symbol),
        period="daily",
        start_date=start.strftime("%Y%m%d"),
        end_date=end.strftime("%Y%m%d"),
        adjust="",
    )


def _etf_market_symbol(symbol: str | int) -> str:
    code = _normalize_symbol(symbol)
    if code.startswith(("5", "6", "9")):
        return f"sh{code}"
    return f"sz{code}"


def _fetch_etf_daily_sina(symbol: str) -> pd.DataFrame:
    return _call_akshare(ak.fund_etf_hist_sina, symbol=_etf_market_symbol(symbol))


def _normalize_etf_daily(raw: pd.DataFrame, symbol: str) -> pd.DataFrame:
    if raw.empty:
        return pd.DataFrame()
    out = pd.DataFrame(
        {
            "symbol": _normalize_symbol(symbol),
            "date": pd.to_datetime(raw["日期"], errors="coerce").dt.date,
            "open": pd.to_numeric(raw["开盘"], errors="coerce"),
            "high": pd.to_numeric(raw["最高"], errors="coerce"),
            "low": pd.to_numeric(raw["最低"], errors="coerce"),
            "close": pd.to_numeric(raw["收盘"], errors="coerce"),
            "vol": pd.to_numeric(raw["成交量"], errors="coerce") * 100.0,
            "amount": pd.to_numeric(raw["成交额"], errors="coerce"),
            "pct_chg_raw": pd.to_numeric(raw["涨跌幅"], errors="coerce") / 100.0,
            "turnover": pd.to_numeric(raw["换手率"], errors="coerce") / 100.0,
        }
    )
    return out.dropna(subset=["date"]).sort_values("date").reset_index(drop=True)


def _normalize_etf_daily_sina(raw: pd.DataFrame, symbol: str) -> pd.DataFrame:
    if raw.empty:
        return pd.DataFrame()
    out = pd.DataFrame(
        {
            "symbol": _normalize_symbol(symbol),
            "date": pd.to_datetime(raw["date"], errors="coerce").dt.date,
            "open": pd.to_numeric(raw["open"], errors="coerce"),
            "high": pd.to_numeric(raw["high"], errors="coerce"),
            "low": pd.to_numeric(raw["low"], errors="coerce"),
            "close": pd.to_numeric(raw["close"], errors="coerce"),
            "vol": pd.to_numeric(raw["volume"], errors="coerce"),
            "amount": pd.to_numeric(raw["amount"], errors="coerce"),
        }
    )
    return out.dropna(subset=["date"]).sort_values("date").reset_index(drop=True)


def get_etf_daily(
    symbol: str | int,
    start: str | date = "1990-01-01",
    end: str | date | None = None,
    refresh: bool = False,
) -> pd.DataFrame:
    """Return ETF daily bars aligned to the stock daily field contract."""
    start_date = _parse_date(start)
    end_date = _parse_date(end or date.today())
    code = _normalize_symbol(symbol)
    path = _cache_path("etf_daily", code, _date_str(start_date), _date_str(end_date))
    cached = _read_cache(path, refresh)
    if cached is not None:
        return cached

    errors: list[Exception] = []
    normalized = pd.DataFrame()
    source = "eastmoney_fund_etf_hist_em"
    try:
        raw = _fetch_etf_daily(code, start_date, end_date)
        normalized = _normalize_etf_daily(raw, code)
    except Exception as exc:
        errors.append(exc)

    if normalized.empty:
        source = "sina_fund_etf_hist_sina"
        try:
            raw = _fetch_etf_daily_sina(code)
            normalized = _normalize_etf_daily_sina(raw, code)
        except Exception as exc:
            errors.append(exc)

    if normalized.empty and errors:
        raise RuntimeError(f"ETF daily fetch failed for {code}") from errors[-1]

    out = _finalize_s3_daily(normalized, code, start_date, end_date, name=code, source=source)
    _write_cache(path, out)
    return out


def _normalize_daily_tencent(raw: pd.DataFrame, symbol: str) -> pd.DataFrame:
    if raw.empty:
        return pd.DataFrame()
    # AkShare names Tencent's 6th kline field "amount", but it is volume
    # in hands for A-share stocks in the observed payload.
    close = pd.to_numeric(raw["close"], errors="coerce")
    hands = pd.to_numeric(raw["amount"], errors="coerce")
    out = pd.DataFrame(
        {
            "symbol": _normalize_symbol(symbol),
            "date": pd.to_datetime(raw["date"], errors="coerce").dt.date,
            "open": pd.to_numeric(raw["open"], errors="coerce"),
            "high": pd.to_numeric(raw["high"], errors="coerce"),
            "low": pd.to_numeric(raw["low"], errors="coerce"),
            "close": close,
            "vol": hands * 100.0,
            "amount": hands * 100.0 * close,
            "pct_chg": close.pct_change(),
            "turnover": np.nan,
            "source": "tencent_stock_zh_a_hist_tx",
        }
    )
    return out.dropna(subset=["date"]).sort_values("date").reset_index(drop=True)


def _estimate_float_mv(df: pd.DataFrame) -> pd.Series:
    turnover = pd.to_numeric(df["turnover"], errors="coerce")
    vol = pd.to_numeric(df["vol"], errors="coerce")
    close = pd.to_numeric(df["close"], errors="coerce")
    float_shares = np.where(turnover > 0, vol / turnover, np.nan)
    return pd.Series(float_shares * close, index=df.index, dtype="float64")


def _daily_limit_rate(symbol: str, on_date: date, is_st: bool) -> float:
    if is_st:
        return 0.05
    code = _normalize_symbol(symbol)
    if code.startswith("688"):
        return 0.20
    if code.startswith("300") and on_date >= date(2020, 8, 24):
        return 0.20
    if _is_bj_symbol(code):
        return 0.30
    return 0.10


def _add_calendar_rows(
    df: pd.DataFrame,
    symbol: str,
    start: date,
    end: date,
    meta: dict[str, Any],
    refresh: bool = False,
) -> pd.DataFrame:
    if df.empty:
        return df

    last_calendar_date = end
    delist_date = meta.get("delist_date")
    if pd.notna(delist_date):
        last_calendar_date = min(last_calendar_date, _parse_date(delist_date))

    first_calendar_date = max(start, min(df["date"]))
    calendar = _trade_dates(first_calendar_date, last_calendar_date, refresh=refresh)
    if calendar.empty:
        return df

    base = pd.DataFrame({"date": calendar})
    merged = base.merge(df, on="date", how="left", suffixes=("", "_raw"))
    merged["symbol"] = merged["symbol"].fillna(_normalize_symbol(symbol))
    merged["is_suspended"] = merged["open"].isna()

    prev_close = merged["close"].ffill()
    for col in ["open", "high", "low", "close"]:
        merged[col] = merged[col].fillna(prev_close)
    merged["vol"] = merged["vol"].fillna(0.0)
    merged["amount"] = merged["amount"].fillna(0.0)
    merged["pct_chg"] = merged["pct_chg"].fillna(0.0)
    merged["turnover"] = merged["turnover"].fillna(0.0)
    merged["source"] = merged["source"].fillna("calendar_suspension")
    return merged


def _finalize_daily(
    df: pd.DataFrame,
    symbol: str,
    start: date,
    end: date,
    refresh: bool = False,
) -> pd.DataFrame:
    meta = _symbol_metadata(symbol, refresh=False)
    if df.empty:
        return pd.DataFrame(columns=DAILY_COLUMNS + ["name", "source"])

    df = _add_calendar_rows(df, symbol, start, end, meta, refresh=False)
    df = df[(df["date"] >= start) & (df["date"] <= end)].copy()
    df["symbol"] = df["symbol"].astype(str).str.zfill(6)
    df["name"] = str(meta.get("name") or "")
    df["is_st"] = bool(meta.get("is_st", False))
    df["is_delisted"] = bool(meta.get("is_delisted", False))
    df["float_mv"] = _estimate_float_mv(df)
    df["float_mv"] = df["float_mv"].ffill()

    prev_close = df["close"].ffill().shift(1)
    rates = [
        _daily_limit_rate(symbol, row_date, bool(is_st))
        for row_date, is_st in zip(df["date"], df["is_st"], strict=False)
    ]
    df["limit_up_price"] = (prev_close * (1.0 + pd.Series(rates, index=df.index))).round(2)
    df["limit_down_price"] = (prev_close * (1.0 - pd.Series(rates, index=df.index))).round(2)

    for col in ["is_st", "is_suspended", "is_delisted"]:
        df[col] = df[col].astype(bool)
    out = df[DAILY_COLUMNS + ["name", "source"]].sort_values(["symbol", "date"])
    return out.reset_index(drop=True)


def get_daily(
    symbol: str | int | None = "all",
    start: str | date = "1990-01-01",
    end: str | date | None = None,
    refresh: bool = False,
) -> pd.DataFrame:
    """Return daily A-share bars with required contract fields.

    A-share rows are fetched from Sina daily first because it provides
    OHLCV, amount, and turnover and has been more stable in this environment.
    Eastmoney and then Tencent are used as fallbacks. Tencent lacks turnover,
    so it is only an OHLCV last resort.

    Limitations:
    - Historical ST state is approximated from current/latest known names or
      delisting names; AkShare does not expose a complete point-in-time ST
      flag in these interfaces.
    - Tencent delisted fallback lacks traded amount and turnover, so
      ``amount``, ``turnover`` and ``float_mv`` can be NaN/0 for those rows.
    - Suspension rows are inferred from missing trading-calendar bars between
      listing/trading availability and the requested/delisting end date.
    """
    start_date = _parse_date(start)
    end_date = _parse_date(end or date.today())

    if symbol is None or str(symbol).lower() == "all":
        path = _cache_path("daily_all", _date_str(start_date), _date_str(end_date))
        cached = _read_cache(path, refresh)
        if cached is not None:
            return cached
        frames: list[pd.DataFrame] = []
        for code in _universe(refresh=refresh)["symbol"].tolist():
            try:
                item = get_daily(code, start=start_date, end=end_date, refresh=refresh)
            except Exception as exc:
                item = pd.DataFrame(columns=DAILY_COLUMNS + ["name", "source"])
                item.loc[0, "symbol"] = code
                item.loc[0, "source"] = f"fetch_error:{type(exc).__name__}"
            if not item.empty:
                frames.append(item)
        out = pd.concat(frames, ignore_index=True) if frames else pd.DataFrame(columns=DAILY_COLUMNS)
        _write_cache(path, out)
        return out

    code = _normalize_symbol(symbol)
    path = _cache_path("daily", code, _date_str(start_date), _date_str(end_date))
    cached = _read_cache(path, refresh)
    if cached is not None:
        return cached

    meta = _symbol_metadata(code, refresh=refresh)
    errors: list[Exception] = []
    daily = pd.DataFrame()
    try:
        raw_sina = _fetch_daily_sina(code, start_date, end_date)
        daily = _normalize_daily_sina(raw_sina, code)
    except Exception as exc:
        errors.append(exc)
        daily = pd.DataFrame()
    if daily.empty:
        try:
            raw_em = _fetch_daily_eastmoney(code, start_date, end_date)
            em_daily = _normalize_daily_eastmoney(raw_em, code)
            if not em_daily.empty:
                daily = em_daily
        except Exception as exc:
            errors.append(exc)
    if daily.empty:
        try:
            raw_tx = _fetch_daily_tencent(code, start_date, end_date)
            tx_daily = _normalize_daily_tencent(raw_tx, code)
            if not tx_daily.empty:
                daily = tx_daily
        except Exception as exc:
            errors.append(exc)
            if errors:
                raise errors[0]
            raise
    out = _finalize_daily(daily, code, start_date, end_date, refresh=refresh)
    _write_cache(path, out)
    return out


def _fetch_intraday_bars(
    symbol: str,
    on_date: date,
    cutoff: str,
    periods: Iterable[str] = ("1", "5"),
) -> tuple[pd.DataFrame, str] | tuple[pd.DataFrame, None]:
    start_ts = f"{on_date.isoformat()} 09:30:00"
    end_ts = f"{on_date.isoformat()} {cutoff}:00"
    cutoff_ts = pd.Timestamp(end_ts)

    for period in periods:
        try:
            raw = _call_akshare(
                ak.stock_zh_a_hist_min_em,
                symbol=_normalize_symbol(symbol),
                start_date=start_ts,
                end_date=end_ts,
                period=period,
                adjust="",
            )
        except Exception:
            continue
        if raw.empty or "时间" not in raw.columns:
            continue
        raw = raw.copy()
        raw["timestamp"] = pd.to_datetime(raw["时间"], errors="coerce")
        raw = raw.dropna(subset=["timestamp"])
        raw = raw[(raw["timestamp"].dt.date == on_date) & (raw["timestamp"] <= cutoff_ts)]
        if raw.empty:
            continue
        raw = raw.sort_values("timestamp").set_index("timestamp", drop=False)
        assert raw.index.max() <= cutoff_ts
        return raw, period
    return pd.DataFrame(), None


def _elapsed_trading_fraction(cutoff: str) -> float:
    cutoff_time = datetime.strptime(cutoff, "%H:%M").time()
    morning_start = dt_time(9, 30)
    morning_end = dt_time(11, 30)
    afternoon_start = dt_time(13, 0)
    afternoon_end = dt_time(15, 0)

    def minutes_between(left: dt_time, right: dt_time) -> int:
        left_dt = datetime.combine(date(2000, 1, 1), left)
        right_dt = datetime.combine(date(2000, 1, 1), right)
        return max(0, int((right_dt - left_dt).total_seconds() // 60))

    elapsed = 0
    if cutoff_time > morning_start:
        elapsed += minutes_between(morning_start, min(cutoff_time, morning_end))
    if cutoff_time > afternoon_start:
        elapsed += minutes_between(afternoon_start, min(cutoff_time, afternoon_end))
    return max(min(elapsed / A_SHARE_TRADING_MINUTES, 1.0), 0.0)


def _prior_daily_context(symbol: str, on_date: date) -> pd.DataFrame:
    start = on_date - timedelta(days=45)
    end = on_date - timedelta(days=1)
    if end < start:
        return pd.DataFrame()
    daily = get_daily(symbol, start=start, end=end)
    if daily.empty:
        return daily
    daily = daily[~daily["is_suspended"]].copy()
    return daily.sort_values("date")


def _safe_json_float_list(values: pd.Series) -> str:
    clean = []
    for value in values.tolist():
        if value is None or pd.isna(value):
            clean.append(None)
        else:
            clean.append(round(float(value), 6))
    return json.dumps(clean, separators=(",", ":"))


def _snapshot_from_intraday(symbol: str, on_date: date, cutoff: str, bars: pd.DataFrame, period: str) -> dict[str, Any]:
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
    high_after_1430 = bool(pd.notna(after_high) and pd.notna(before_high) and after_high > before_high)

    prior = _prior_daily_context(symbol, on_date)
    prev_close = float(prior.iloc[-1]["close"]) if not prior.empty and pd.notna(prior.iloc[-1]["close"]) else np.nan
    avg_vol = prior.tail(VOL_RATIO_LOOKBACK_DAYS)["vol"].mean() if not prior.empty else np.nan
    elapsed_fraction = _elapsed_trading_fraction(cutoff)
    vol_ratio = np.nan
    if pd.notna(avg_vol) and avg_vol > 0 and elapsed_fraction > 0:
        vol_ratio = float(cum_vol.iloc[-1] / (avg_vol * elapsed_fraction))

    float_shares = np.nan
    if not prior.empty:
        last_float_mv = prior.iloc[-1].get("float_mv", np.nan)
        last_close = prior.iloc[-1].get("close", np.nan)
        if pd.notna(last_float_mv) and pd.notna(last_close) and last_close > 0:
            float_shares = float(last_float_mv / last_close)
    turnover_at_cutoff = float(cum_vol.iloc[-1] / float_shares) if pd.notna(float_shares) and float_shares > 0 else np.nan

    price = float(close.iloc[-1])
    pct_chg = float(price / prev_close - 1.0) if pd.notna(prev_close) and prev_close > 0 else np.nan
    above_vwap = bool((close >= vwap).dropna().all()) if not vwap.dropna().empty else False

    return {
        "symbol": _normalize_symbol(symbol),
        "date": on_date.isoformat(),
        "cutoff": cutoff,
        "price_at_cutoff": price,
        "pct_chg_at_cutoff": pct_chg,
        "vwap_curve": _safe_json_float_list(vwap),
        "vwap_at_cutoff": float(vwap.iloc[-1]) if pd.notna(vwap.iloc[-1]) else np.nan,
        "is_above_vwap": above_vwap,
        "high_after_1430": high_after_1430,
        "high_after_1430_price": float(after_high) if pd.notna(after_high) else np.nan,
        "vol_ratio_at_cutoff": vol_ratio,
        "turnover_at_cutoff": turnover_at_cutoff,
        "source": "ak.stock_zh_a_hist_min_em",
        "source_period": period,
        "source_max_ts": bars.index.max().isoformat(sep=" "),
        "is_proxy": False,
        "proxy_uses_future": False,
    }


def _normalize_baostock_5min_bars(raw: pd.DataFrame, on_date: date, cutoff: str) -> pd.DataFrame:
    if raw.empty:
        return pd.DataFrame()
    out = raw.copy()
    out["timestamp"] = pd.to_datetime(out["time"].astype(str).str.slice(0, 14), format="%Y%m%d%H%M%S", errors="coerce")
    out = out.dropna(subset=["timestamp"])
    cutoff_ts = pd.Timestamp(f"{on_date.isoformat()} {cutoff}:00")
    out = out[(out["timestamp"].dt.date == on_date) & (out["timestamp"] <= cutoff_ts)].copy()
    if out.empty:
        return out
    out = out.sort_values("timestamp").set_index("timestamp", drop=False)
    assert out.index.max() <= cutoff_ts, f"BaoStock 5min contains future bar: {out.index.max()} > {cutoff_ts}"
    return out


def _snapshot_from_baostock_intraday(symbol: str, on_date: date, cutoff: str, bars: pd.DataFrame) -> dict[str, Any]:
    cutoff_ts = pd.Timestamp(f"{on_date.isoformat()} {cutoff}:00")
    assert bars.index.max() <= cutoff_ts

    close = pd.to_numeric(bars["close"], errors="coerce")
    high = pd.to_numeric(bars["high"], errors="coerce")
    vol = pd.to_numeric(bars["volume"], errors="coerce").fillna(0.0)
    amount = pd.to_numeric(bars["amount"], errors="coerce").fillna(0.0)
    cum_vol = vol.cumsum()
    cum_amount = amount.cumsum()
    vwap = (cum_amount / cum_vol.replace(0, np.nan)).ffill()

    before_1430 = bars[bars.index <= pd.Timestamp(f"{on_date.isoformat()} 14:30:00")]
    after_1430 = bars[bars.index > pd.Timestamp(f"{on_date.isoformat()} 14:30:00")]
    before_high = pd.to_numeric(before_1430["high"], errors="coerce").max() if not before_1430.empty else np.nan
    after_high = pd.to_numeric(after_1430["high"], errors="coerce").max() if not after_1430.empty else np.nan
    high_after_1430 = bool(pd.notna(after_high) and pd.notna(before_high) and after_high > before_high)

    prior = _prior_daily_context(symbol, on_date)
    prev_close = float(prior.iloc[-1]["close"]) if not prior.empty and pd.notna(prior.iloc[-1]["close"]) else np.nan
    avg_vol = prior.tail(VOL_RATIO_LOOKBACK_DAYS)["vol"].mean() if not prior.empty else np.nan
    elapsed_fraction = _elapsed_trading_fraction(cutoff)
    vol_ratio = np.nan
    if pd.notna(avg_vol) and avg_vol > 0 and elapsed_fraction > 0:
        vol_ratio = float(cum_vol.iloc[-1] / (avg_vol * elapsed_fraction))

    float_shares = np.nan
    if not prior.empty:
        last_float_mv = prior.iloc[-1].get("float_mv", np.nan)
        last_close = prior.iloc[-1].get("close", np.nan)
        if pd.notna(last_float_mv) and pd.notna(last_close) and last_close > 0:
            float_shares = float(last_float_mv / last_close)
    turnover_at_cutoff = float(cum_vol.iloc[-1] / float_shares) if pd.notna(float_shares) and float_shares > 0 else np.nan

    price = float(close.iloc[-1])
    pct_chg = float(price / prev_close - 1.0) if pd.notna(prev_close) and prev_close > 0 else np.nan
    above_vwap = bool((close >= vwap).dropna().all()) if not vwap.dropna().empty else False

    return {
        "symbol": _normalize_symbol(symbol),
        "date": on_date.isoformat(),
        "cutoff": cutoff,
        "price_at_cutoff": price,
        "pct_chg_at_cutoff": pct_chg,
        "vwap_curve": _safe_json_float_list(vwap),
        "vwap_at_cutoff": float(vwap.iloc[-1]) if pd.notna(vwap.iloc[-1]) else np.nan,
        "is_above_vwap": above_vwap,
        "high_after_1430": high_after_1430,
        "high_after_1430_price": float(after_high) if pd.notna(after_high) else np.nan,
        "vol_ratio_at_cutoff": vol_ratio,
        "turnover_at_cutoff": turnover_at_cutoff,
        "source": "baostock.query_history_k_data_plus",
        "source_period": "5",
        "source_max_ts": bars.index.max().isoformat(sep=" "),
        "is_proxy": False,
        "proxy_uses_future": False,
    }


def _snapshot_from_daily_proxy(symbol: str, on_date: date, cutoff: str) -> dict[str, Any]:
    daily = get_daily(symbol, start=on_date - timedelta(days=45), end=on_date)
    current = daily[daily["date"].map(_parse_date) == on_date] if not daily.empty else pd.DataFrame()
    prior = daily[daily["date"].map(_parse_date) < on_date] if not daily.empty else pd.DataFrame()
    if current.empty:
        row = {}
    else:
        row = current.iloc[-1].to_dict()
    prev_close = prior.iloc[-1]["close"] if not prior.empty else np.nan
    price = row.get("close", np.nan)
    vol = row.get("vol", np.nan)
    amount = row.get("amount", np.nan)
    high = row.get("high", np.nan)
    close = row.get("close", np.nan)
    turnover = row.get("turnover", np.nan)
    avg_vol = prior.tail(VOL_RATIO_LOOKBACK_DAYS)["vol"].mean() if not prior.empty else np.nan

    vwap = np.nan
    if pd.notna(amount) and pd.notna(vol) and vol > 0:
        vwap = float(amount / vol)
    pct_chg = float(price / prev_close - 1.0) if pd.notna(price) and pd.notna(prev_close) and prev_close > 0 else np.nan
    vol_ratio = float(vol / avg_vol) if pd.notna(vol) and pd.notna(avg_vol) and avg_vol > 0 else np.nan
    high_after_1430 = bool(
        pd.notna(close)
        and pd.notna(high)
        and high > 0
        and close >= high * DAILY_PROXY_CLOSE_NEAR_HIGH_RATIO
    )

    return {
        "symbol": _normalize_symbol(symbol),
        "date": on_date.isoformat(),
        "cutoff": cutoff,
        "price_at_cutoff": float(price) if pd.notna(price) else np.nan,
        "pct_chg_at_cutoff": pct_chg,
        "vwap_curve": _safe_json_float_list(pd.Series([vwap])),
        "vwap_at_cutoff": vwap,
        "is_above_vwap": bool(pd.notna(price) and pd.notna(vwap) and price >= vwap),
        "high_after_1430": high_after_1430,
        "high_after_1430_price": float(high) if pd.notna(high) else np.nan,
        "vol_ratio_at_cutoff": vol_ratio,
        "turnover_at_cutoff": float(turnover) if pd.notna(turnover) else np.nan,
        "source": "daily_bar_proxy",
        "source_period": "daily",
        "source_max_ts": f"{on_date.isoformat()} 15:00:00",
        "is_proxy": True,
        "proxy_uses_future": True,
    }


def get_intraday_snapshot(
    date: str | date,
    cutoff: str = "14:50",
    symbols: Iterable[str | int] | None = None,
    refresh: bool = False,
    allow_daily_proxy: bool = True,
    strict_exact: bool = False,
) -> pd.DataFrame:
    """Return S1 intraday fields as of ``cutoff``.

    Exact mode uses AkShare ``stock_zh_a_hist_min_em`` minute bars and asserts
    the latest bar timestamp is not later than ``date cutoff``. When exact
    minute bars are unavailable, the optional daily proxy uses full-day daily
    OHLCV and is marked with ``is_proxy=True`` and ``proxy_uses_future=True``.

    The proxy has a known direction of bias: it can include stocks that only
    crossed S1 thresholds after 14:50 and can miss stocks that faded before
    the close. Its error size is unknowable for old dates without a paid or
    archived minute source; it should be calibrated only on dates where exact
    minute bars are available.
    """
    on_date = _parse_date(date)
    if symbols is None:
        symbols = _universe(refresh=refresh)["symbol"].tolist()
    symbols_list = [_normalize_symbol(item) for item in symbols]
    symbol_key = hashlib.sha1(",".join(symbols_list).encode("utf-8")).hexdigest()[:12]
    path = _cache_path("snapshot", on_date.isoformat(), cutoff, symbol_key, int(allow_daily_proxy), int(strict_exact))
    cached = _read_cache(path, refresh)
    if cached is not None:
        return cached

    rows: list[dict[str, Any]] = []
    errors: list[str] = []
    for code in symbols_list:
        bars, period = _fetch_intraday_bars(code, on_date, cutoff)
        if not bars.empty and period is not None:
            rows.append(_snapshot_from_intraday(code, on_date, cutoff, bars, period))
            continue
        if strict_exact or not allow_daily_proxy:
            errors.append(code)
            continue
        rows.append(_snapshot_from_daily_proxy(code, on_date, cutoff))

    if errors:
        raise RuntimeError(f"Exact intraday bars unavailable for: {', '.join(errors[:10])}")
    out = pd.DataFrame(rows, columns=SNAPSHOT_COLUMNS)
    _write_cache(path, out)
    return out


def get_intraday_snapshot_baostock(
    date: str | date,
    cutoff: str = "14:50",
    symbols: Iterable[str | int] | None = None,
    refresh: bool = False,
) -> pd.DataFrame:
    """Return S1 5min PIT fields from BaoStock, never using bars after cutoff.

    Field contract and known measurement status:
    - ``price_at_cutoff``: approximate. Last BaoStock 5min close with
      ``bar_time <= cutoff``; it is not tick-exact inside the 5min bucket.
    - ``pct_chg_at_cutoff``: approximate. ``price_at_cutoff / prev_close - 1``;
      ``prev_close`` comes only from daily rows with ``date <= D-1``.
    - ``vol_ratio_at_cutoff``: approximate. Cumulative BaoStock 5min volume
      through cutoff divided by the elapsed-trading-fraction share of the
      prior five non-suspended daily volumes.
    - ``turnover_at_cutoff``: approximate. BaoStock 5min has no ``turn`` field,
      so this uses cumulative volume divided by ``float_shares`` estimated from
      ``float_mv / close`` on the latest ``date <= D-1`` daily row.
    - ``vwap_curve`` / ``is_above_vwap``: 5min approximation. Running VWAP is
      ``sum(amount) / sum(volume)`` through each 5min endpoint; intrabar
      crossings are not observable.
    - ``high_after_1430``: 5min approximation. Compares max 5min high for
      ``14:30 < bar_time <= cutoff`` against max high up to ``14:30``; intrabar
      ordering is not observable.

    The implementation asserts the maximum retained bar timestamp is not later
    than ``D cutoff``. Rows with no BaoStock 5min bars are omitted rather than
    filled from daily data, because a daily fallback would be a future leak for
    S1 historical Gate1.
    """
    on_date = _parse_date(date)
    if symbols is None:
        symbols = _universe(refresh=refresh)["symbol"].tolist()
    symbols_list = [_normalize_symbol(item) for item in symbols]
    symbol_key = hashlib.sha1(",".join(symbols_list).encode("utf-8")).hexdigest()[:12]
    path = _cache_path("baostock_snapshot", on_date.isoformat(), cutoff, symbol_key)
    cached = _read_cache(path, refresh)
    if cached is not None:
        return cached

    rows: list[dict[str, Any]] = []
    _baostock_login()
    try:
        for code in symbols_list:
            raw = _call_baostock_history_5min(code, on_date)
            bars = _normalize_baostock_5min_bars(raw, on_date, cutoff)
            if bars.empty:
                continue
            rows.append(_snapshot_from_baostock_intraday(code, on_date, cutoff, bars))
    finally:
        bs.logout()

    out = pd.DataFrame(rows, columns=SNAPSHOT_COLUMNS)
    _write_cache(path, out)
    return out


def _minute_depth_probe(symbol: str = "000001") -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    for period in ("1", "5"):
        item: dict[str, Any] = {"interface": "ak.stock_zh_a_hist_min_em", "period": period}
        try:
            raw = _call_akshare(
                ak.stock_zh_a_hist_min_em,
                symbol=symbol,
                start_date="1979-01-01 09:30:00",
                end_date="2222-01-01 15:00:00",
                period=period,
                adjust="",
                attempts=3,
            )
            if raw.empty:
                item.update({"rows": 0, "earliest": None, "latest": None, "error": None})
            else:
                ts = pd.to_datetime(raw["时间"], errors="coerce")
                item.update(
                    {
                        "rows": int(len(raw)),
                        "earliest": str(ts.min()),
                        "latest": str(ts.max()),
                        "error": None,
                    }
                )
        except Exception as exc:
            item.update({"rows": 0, "earliest": None, "latest": None, "error": f"{type(exc).__name__}: {exc}"})
        result.append(item)
    return result


def _minute_source_ndays_note() -> str:
    source = inspect.getsource(ak.stock_zh_a_hist_min_em)
    if '"ndays": "5"' in source:
        return "period=1 source hardcodes ndays=5"
    return "period=1 source ndays not detected"


def _coverage_note(earliest: str | None, config: dict[str, Any]) -> str:
    if not earliest:
        return "unknown/no rows"
    earliest_date = _parse_date(earliest)
    notes = []
    for name, span in config.get("regimes", {}).items():
        start = _parse_date(span["start"])
        end = _parse_date(span["end"])
        covered = earliest_date <= start
        notes.append(f"{name}({start}..{end})={'covered' if covered else 'NOT covered'}")
    return "; ".join(notes)


def _span_coverage_note(earliest: Any, latest: Any, config: dict[str, Any]) -> str:
    if not earliest or not latest or pd.isna(earliest) or pd.isna(latest):
        return "unknown/no rows"
    earliest_date = _parse_date(earliest)
    latest_date = _parse_date(latest)
    notes = []
    for name, span in config.get("regimes", {}).items():
        start = _parse_date(span["start"])
        end = _parse_date(span["end"])
        covered = earliest_date <= start and latest_date >= end
        notes.append(f"{name}({start}..{end})={'covered' if covered else 'NOT covered'}")
    return "; ".join(notes)


def _s3_daily_probe(config: dict[str, Any]) -> list[dict[str, Any]]:
    first_start = min(_parse_date(span["start"]) for span in config.get("regimes", {}).values())
    last_end = max(_parse_date(span["end"]) for span in config.get("regimes", {}).values())
    probes = [
        ("index", "沪深300", "sh000300", get_index_daily),
        ("index", "中证500", "sh000905", get_index_daily),
        ("etf", "证券ETF", "512880", get_etf_daily),
        ("etf", "银行ETF", "512800", get_etf_daily),
        ("etf", "芯片ETF", "159995", get_etf_daily),
        ("etf", "医药ETF", "512010", get_etf_daily),
    ]
    rows: list[dict[str, Any]] = []
    for asset_type, name, symbol, getter in probes:
        item: dict[str, Any] = {
            "type": asset_type,
            "name": name,
            "symbol": symbol,
            "rows": 0,
            "earliest": None,
            "latest": None,
            "error": None,
        }
        try:
            df = getter(symbol, start=first_start, end=last_end, refresh=True)
            if not df.empty:
                item.update(
                    {
                        "rows": int(len(df)),
                        "earliest": str(min(df["date"])),
                        "latest": str(max(df["date"])),
                    }
                )
        except Exception as exc:
            item["error"] = f"{type(exc).__name__}: {exc}"
        item["coverage"] = _span_coverage_note(item["earliest"], item["latest"], config)
        rows.append(item)
    return rows


def _print_df(label: str, df: pd.DataFrame, columns: list[str] | None = None, max_rows: int = 5) -> None:
    print(f"\n[{label}]")
    if df.empty:
        print("<empty>")
        return
    shown = df if columns is None else df[[col for col in columns if col in df.columns]]
    print(shown.head(max_rows).to_string(index=False))


def run_check() -> None:
    backtest_cfg = _load_yaml("backtest.yaml")
    strategy_cfg = _load_yaml("strategy.yaml")
    decision_time = strategy_cfg["s1_tail"]["decision_time"]

    print("AkShare data availability check")
    print(f"akshare_version={getattr(ak, '__version__', 'unknown')}")
    print(f"s1_decision_time={decision_time}")

    active = get_daily("000001", start="2020-07-01", end="2020-07-10")
    _print_df(
        "daily active sample via ak.stock_zh_a_hist",
        active,
        ["symbol", "date", "open", "high", "low", "close", "vol", "amount", "pct_chg", "turnover", "float_mv", "is_st", "is_suspended"],
        3,
    )

    delisted = get_daily("000003", start="2002-04-15", end="2002-06-14")
    _print_df(
        "delisted daily sample via ak.stock_zh_a_hist_tx fallback",
        delisted,
        ["symbol", "date", "open", "high", "low", "close", "vol", "amount", "is_delisted", "is_suspended", "source"],
        5,
    )

    print("\n[minute depth probe]")
    print(_minute_source_ndays_note())
    depth = _minute_depth_probe("000001")
    for item in depth:
        print(json.dumps(item, ensure_ascii=False))
        print("coverage:", _coverage_note(item.get("earliest"), backtest_cfg))

    print("\n[S3 daily source probe]")
    s3_probe = _s3_daily_probe(backtest_cfg)
    any_s3_usable = False
    for item in s3_probe:
        print(json.dumps(item, ensure_ascii=False))
        if "NOT covered" not in item["coverage"] and item["coverage"] != "unknown/no rows":
            any_s3_usable = True
    if not any_s3_usable:
        print("S3数据不可得")

    exact_date = None
    for item in depth:
        if item.get("latest"):
            exact_date = _parse_date(item["latest"])
            break
    if exact_date is None:
        exact_date = date.today()

    snapshot = get_intraday_snapshot(
        exact_date,
        cutoff=decision_time,
        symbols=["000001"],
        refresh=True,
        allow_daily_proxy=False,
        strict_exact=True,
    )
    source_max_ts = pd.Timestamp(snapshot.iloc[0]["source_max_ts"])
    as_of_ts = pd.Timestamp(f"{exact_date.isoformat()} {decision_time}:00")
    assert source_max_ts <= as_of_ts
    _print_df(
        "Gate0(i) S1 fields using <= cutoff intraday bars",
        snapshot,
        [
            "symbol",
            "date",
            "cutoff",
            "price_at_cutoff",
            "pct_chg_at_cutoff",
            "vwap_at_cutoff",
            "is_above_vwap",
            "high_after_1430",
            "vol_ratio_at_cutoff",
            "turnover_at_cutoff",
            "source_period",
            "source_max_ts",
            "is_proxy",
        ],
        3,
    )
    print(f"Gate0(i) assertion passed: source_max_ts={source_max_ts} <= as_of_ts={as_of_ts}")

    uni = _universe()
    delisted_in_pool = uni[uni["is_delisted"]].head(3)
    _print_df("Gate0(ii) universe contains delisted stocks", delisted_in_pool, ["symbol", "name", "is_delisted", "delist_date"], 3)

    st_candidates = uni[uni["name"].map(_is_st_name)]
    st_code = st_candidates.iloc[0]["symbol"] if not st_candidates.empty else "000004"
    st_sample = get_daily(st_code, start=exact_date - timedelta(days=7), end=exact_date)
    _print_df(
        "Gate0(iii) ST/limit sample",
        st_sample.tail(3),
        ["symbol", "date", "name", "close", "is_st", "limit_up_price", "limit_down_price"],
        3,
    )

    suspension_sample = get_daily("000005", start="2024-03-04", end="2024-04-26")
    suspension_rows = suspension_sample[suspension_sample["is_suspended"]]
    _print_df(
        "Gate0(iii) suspension sample inferred from missing trading bars",
        suspension_rows,
        ["symbol", "date", "name", "close", "vol", "is_suspended", "is_delisted", "source"],
        5,
    )

    print("\n[proxy limitation]")
    proxy = get_intraday_snapshot("2020-07-01", cutoff=decision_time, symbols=["000001"], refresh=True)
    _print_df(
        "historical 14:50 unavailable: daily proxy is explicitly flagged",
        proxy,
        ["symbol", "date", "cutoff", "source", "source_max_ts", "is_proxy", "proxy_uses_future", "price_at_cutoff"],
        1,
    )


def main() -> None:
    parser = argparse.ArgumentParser(description="AkShare data source utilities")
    parser.add_argument("--check", action="store_true", help="run Gate0 data availability checks")
    args = parser.parse_args()
    if args.check:
        run_check()
    else:
        parser.print_help()


if __name__ == "__main__":
    main()
