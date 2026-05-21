"""Convert the project S2 PIT A-share panel into Qlib day-bin format.

The converter writes Qlib's local file layout directly:

    calendars/day.txt
    instruments/all.txt
    features/<instrument>/<field>.day.bin

Qlib feature bin files are float32 arrays.  The first value is the start
calendar index, followed by one value per trading day.
"""

from __future__ import annotations

import argparse
import json
import shutil
import time
from pathlib import Path

import numpy as np
import pandas as pd


PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_PANEL = PROJECT_ROOT / "data" / "cache" / "s2_panel_v2pit_2019-10-01_2026-05-15.parquet"
DEFAULT_OUTPUT = PROJECT_ROOT / "data" / "qlib_data" / "cn_data"

PANEL_COLUMNS = [
    "symbol",
    "date",
    "open",
    "high",
    "low",
    "close",
    "vol",
    "amount",
    "is_delisted",
    "delist_date",
    "list_date",
    "name",
    "source",
]

FIELD_MAP = {
    "open": "open",
    "high": "high",
    "low": "low",
    "close": "close",
    "volume": "vol",
    "money": "amount",
    "vwap": "vwap",
    "factor": "factor",
}


def normalize_symbol(symbol: str | int) -> str:
    text = str(symbol).strip().lower()
    if text.startswith(("sh", "sz", "bj")):
        text = text[2:]
    if not text.isdigit():
        raise ValueError(f"unsupported symbol: {symbol!r}")
    return text.zfill(6)


def qlib_instrument(symbol: str | int) -> str:
    code = normalize_symbol(symbol)
    if code.startswith(("6", "9")):
        return f"SH{code}"
    if code.startswith(("4", "8", "920")):
        return f"BJ{code}"
    return f"SZ{code}"


def write_bin(path: Path, values: np.ndarray, start_index: int) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    out = np.empty(len(values) + 1, dtype="<f4")
    out[0] = np.float32(start_index)
    out[1:] = values.astype("<f4", copy=False)
    out.tofile(path)


def clean_output(path: Path, force: bool) -> None:
    if path.exists():
        if not force:
            raise FileExistsError(f"{path} already exists; pass --force to replace it")
        shutil.rmtree(path)
    (path / "calendars").mkdir(parents=True, exist_ok=True)
    (path / "instruments").mkdir(parents=True, exist_ok=True)
    (path / "features").mkdir(parents=True, exist_ok=True)


def load_panel(panel_path: Path) -> pd.DataFrame:
    df = pd.read_parquet(panel_path, columns=PANEL_COLUMNS)
    df["symbol"] = df["symbol"].map(normalize_symbol)
    df["instrument"] = df["symbol"].map(qlib_instrument)
    df["date"] = pd.to_datetime(df["date"], errors="coerce")
    df["delist_date"] = pd.to_datetime(df["delist_date"], errors="coerce")
    df["list_date"] = pd.to_datetime(df["list_date"], errors="coerce")
    df = df.dropna(subset=["symbol", "instrument", "date"])
    df = df.drop_duplicates(["symbol", "date"], keep="last")
    return df.sort_values(["instrument", "date"]).reset_index(drop=True)


def write_calendar(df: pd.DataFrame, output: Path) -> tuple[list[pd.Timestamp], dict[pd.Timestamp, int]]:
    calendar = sorted(pd.Timestamp(d) for d in df["date"].dropna().unique())
    with (output / "calendars" / "day.txt").open("w", encoding="utf-8") as fh:
        for dt in calendar:
            fh.write(dt.strftime("%Y-%m-%d") + "\n")
    return calendar, {dt: i for i, dt in enumerate(calendar)}


def symbol_end_date(group: pd.DataFrame) -> pd.Timestamp:
    last_data_date = group["date"].max()
    is_delisted = bool(group["is_delisted"].fillna(False).any())
    delist_dates = group["delist_date"].dropna()
    if is_delisted and not delist_dates.empty:
        return min(last_data_date, delist_dates.max())
    return last_data_date


def convert_panel(panel_path: Path, output: Path, force: bool, log_every: int) -> dict[str, object]:
    started = time.time()
    clean_output(output, force=force)

    print(f"[load] reading {panel_path}", flush=True)
    df = load_panel(panel_path)
    calendar, calendar_index = write_calendar(df, output)
    print(
        f"[load] rows={len(df):,} symbols={df['instrument'].nunique():,} "
        f"trade_days={len(calendar):,} range={calendar[0].date()}..{calendar[-1].date()}",
        flush=True,
    )

    instrument_lines: list[str] = []
    converted_symbols = 0
    skipped_symbols = 0
    delisted_symbols = 0
    vwap_zero_volume_fallback_rows = 0
    source_values = sorted(str(x) for x in df["source"].dropna().unique())
    fields_written = sorted(FIELD_MAP)

    grouped = df.groupby("instrument", sort=True)
    total_symbols = grouped.ngroups

    for idx, (instrument, group) in enumerate(grouped, start=1):
        group = group.sort_values("date").copy()
        end_date = symbol_end_date(group)
        group = group[group["date"] <= end_date]
        if group.empty:
            skipped_symbols += 1
            continue

        start_date = group["date"].min()
        end_date = group["date"].max()
        start_idx = calendar_index[pd.Timestamp(start_date)]
        end_idx = calendar_index[pd.Timestamp(end_date)]
        full_dates = pd.DatetimeIndex(calendar[start_idx : end_idx + 1])
        aligned = group.set_index("date").reindex(full_dates)

        volume = pd.to_numeric(aligned["vol"], errors="coerce")
        money = pd.to_numeric(aligned["amount"], errors="coerce")
        close = pd.to_numeric(aligned["close"], errors="coerce")
        zero_volume = volume.notna() & (volume <= 0)
        vwap = money / volume.where(volume > 0)
        vwap = vwap.where(~zero_volume, close)
        aligned["vwap"] = vwap
        aligned["factor"] = 1.0
        vwap_zero_volume_fallback_rows += int(zero_volume.sum())

        feature_dir = output / "features" / instrument.lower()
        for qlib_field, source_col in FIELD_MAP.items():
            values = pd.to_numeric(aligned[source_col], errors="coerce").to_numpy(dtype=np.float32)
            write_bin(feature_dir / f"{qlib_field}.day.bin", values, start_idx)

        instrument_lines.append(
            f"{instrument}\t{pd.Timestamp(start_date).strftime('%Y-%m-%d')}\t"
            f"{pd.Timestamp(end_date).strftime('%Y-%m-%d')}\n"
        )
        converted_symbols += 1
        if bool(group["is_delisted"].fillna(False).any()):
            delisted_symbols += 1

        if idx == 1 or idx % log_every == 0 or idx == total_symbols:
            elapsed = time.time() - started
            print(
                f"[convert] {idx:,}/{total_symbols:,} symbols processed; "
                f"converted={converted_symbols:,} elapsed={elapsed:.1f}s",
                flush=True,
            )

    with (output / "instruments" / "all.txt").open("w", encoding="utf-8") as fh:
        fh.writelines(instrument_lines)

    metadata = {
        "panel_path": str(panel_path),
        "output": str(output),
        "rows": int(len(df)),
        "symbols": int(converted_symbols),
        "skipped_symbols": int(skipped_symbols),
        "trade_days": int(len(calendar)),
        "start_date": calendar[0].strftime("%Y-%m-%d"),
        "end_date": calendar[-1].strftime("%Y-%m-%d"),
        "delisted_symbols": int(delisted_symbols),
        "fields_written": fields_written,
        "vwap_rule": "money / volume when volume > 0 else close",
        "vwap_zero_volume_fallback_rows": int(vwap_zero_volume_fallback_rows),
        "factor_rule": "constant 1.0; upstream PIT panel is treated as already adjusted for this probe",
        "source_values": source_values,
        "elapsed_seconds": round(time.time() - started, 3),
    }
    with (output / "metadata.json").open("w", encoding="utf-8") as fh:
        json.dump(metadata, fh, ensure_ascii=False, indent=2, sort_keys=True)
        fh.write("\n")
    return metadata


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--panel", type=Path, default=DEFAULT_PANEL, help="Input S2 PIT panel parquet")
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT, help="Output Qlib cn_data directory")
    parser.add_argument("--force", action="store_true", help="Replace output directory if it exists")
    parser.add_argument("--log-every", type=int, default=100, help="Progress interval in symbols")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    metadata = convert_panel(args.panel, args.output, force=args.force, log_every=max(args.log_every, 1))
    print(
        "EXECUTED: "
        f"bin_path={metadata['output']} "
        f"symbols={metadata['symbols']} "
        f"trade_days={metadata['trade_days']} "
        "vwap_filled=yes "
        f"delisted_included={'yes' if metadata['delisted_symbols'] else 'no'}",
        flush=True,
    )


if __name__ == "__main__":
    main()
