"""S3b single-asset time-series trend strategy."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from math import floor
from typing import Any

import pandas as pd

from backtest.constraints import Order, Position
from strategies.base import Strategy


@dataclass(frozen=True)
class S3BTrendConfig:
    asset: str
    ma_len: int
    rebalance: str


class S3BTrendStrategy(Strategy):
    def __init__(self, config: dict[str, Any]):
        self.config = S3BTrendConfig(
            asset=str(config["asset"]),
            ma_len=int(config["ma_len"]),
            rebalance=str(config["rebalance"]),
        )
        if self.config.rebalance != "daily_signal":
            raise ValueError(f"Unsupported S3b rebalance: {self.config.rebalance}")

    def generate_signals(self, as_of_date: date, ctx: dict[str, Any]) -> list[Order]:
        self.assert_context_as_of(as_of_date, ctx)
        frame = ctx["data"].get(self.config.asset)
        if frame is None or frame.empty:
            return []
        frame = frame.sort_values("date")
        assert pd.to_datetime(frame["date"], errors="coerce").max().date() <= as_of_date
        if len(frame) < self.config.ma_len:
            return []

        close = pd.to_numeric(frame["close"], errors="coerce")
        current_close = close.iloc[-1]
        ma = close.tail(self.config.ma_len).mean()
        if pd.isna(current_close) or pd.isna(ma):
            return []

        positions: tuple[Position, ...] = tuple(ctx.get("positions", ()))
        current_quantity = sum(item.quantity for item in positions if item.symbol == self.config.asset and item.quantity > 0)
        should_hold = current_close > ma
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
