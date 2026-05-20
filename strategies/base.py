"""Strategy interfaces shared by historical and paper engines."""

from __future__ import annotations

from abc import ABC, abstractmethod
from datetime import date, datetime
from typing import Any

import pandas as pd

from backtest.constraints import Order


class Strategy(ABC):
    """Point-in-time strategy interface."""

    @abstractmethod
    def generate_signals(self, as_of_date: date, ctx: dict[str, Any]) -> list[Order]:
        """Return orders generated with data available at ``as_of_date``."""

    @staticmethod
    def assert_context_as_of(as_of_date: date | datetime | str | pd.Timestamp, ctx: dict[str, Any]) -> None:
        cutoff = _parse_date(as_of_date)
        data = ctx.get("data", {})
        frames = data.values() if isinstance(data, dict) else [data]
        for frame in frames:
            if not isinstance(frame, pd.DataFrame) or frame.empty:
                continue
            if "date" in frame.columns:
                max_date = pd.to_datetime(frame["date"], errors="coerce").max()
            else:
                max_date = pd.to_datetime(frame.index, errors="coerce").max()
            if pd.isna(max_date):
                continue
            assert max_date.date() <= cutoff, f"future data detected: {max_date.date()} > {cutoff}"


def _parse_date(value: date | datetime | str | pd.Timestamp) -> date:
    if isinstance(value, pd.Timestamp):
        return value.date()
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    return pd.Timestamp(str(value)).date()
