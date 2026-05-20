"""S3c Faber monthly time-series trend strategy."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from math import floor
from typing import Any

import pandas as pd

from backtest.constraints import Order, Position
from strategies.base import Strategy


@dataclass(frozen=True)
class S3CTrendMonthlyConfig:
    asset: str
    ma_len_months: int
    rebalance: str
    signal_basis: str


class S3CTrendMonthlyStrategy(Strategy):
    """Faber-style monthly trend: hold when month-end close is above 10-month SMA."""

    def __init__(self, config: dict[str, Any]):
        self.config = S3CTrendMonthlyConfig(
            asset=str(config["asset"]),
            ma_len_months=int(config["ma_len_months"]),
            rebalance=str(config["rebalance"]),
            signal_basis=str(config["signal_basis"]),
        )
        if self.config.rebalance != "monthly":
            raise ValueError(f"Unsupported S3c rebalance: {self.config.rebalance}")
        if self.config.signal_basis != "month_end_close":
            raise ValueError(f"Unsupported S3c signal_basis: {self.config.signal_basis}")

    def generate_signals(self, as_of_date: date, ctx: dict[str, Any]) -> list[Order]:
        self.assert_context_as_of(as_of_date, ctx)
        monthly = ctx.get("monthly_data", {}).get(self.config.asset)
        if not isinstance(monthly, pd.DataFrame) or monthly.empty:
            return []
        monthly = monthly.sort_values("date").copy()
        max_month = pd.to_datetime(monthly["date"], errors="coerce").max()
        assert pd.notna(max_month) and max_month.date() <= as_of_date
        if max_month.date() != as_of_date:
            return []
        if len(monthly) < self.config.ma_len_months:
            return []

        close = pd.to_numeric(monthly["close"], errors="coerce")
        current_close = close.iloc[-1]
        sma = close.tail(self.config.ma_len_months).mean()
        if pd.isna(current_close) or pd.isna(sma):
            return []

        positions: tuple[Position, ...] = tuple(ctx.get("positions", ()))
        current_quantity = sum(
            item.quantity for item in positions if item.symbol == self.config.asset and item.quantity > 0
        )
        should_hold = float(current_close) > float(sma)
        if should_hold and current_quantity <= 0:
            nav = float(ctx["nav"])
            lot_size = int(ctx.get("lot_size", 100))
            quantity = _floor_to_lot(nav / float(current_close), lot_size)
            if quantity > 0:
                return [Order(symbol=self.config.asset, side="buy", quantity=quantity, submitted_date=as_of_date)]
        if not should_hold and current_quantity > 0:
            return [Order(symbol=self.config.asset, side="sell", quantity=current_quantity, submitted_date=as_of_date)]
        return []


def _floor_to_lot(quantity: float, lot_size: int) -> int:
    if quantity <= 0:
        return 0
    if lot_size <= 1:
        return int(floor(quantity))
    return int(floor(quantity / lot_size) * lot_size)
