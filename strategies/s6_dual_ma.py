"""S6 dual moving-average crossover strategy."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from math import floor
from typing import Any

import pandas as pd

from backtest.constraints import Order, Position
from strategies.base import Strategy


@dataclass(frozen=True)
class S6DualMAConfig:
    asset: str
    signal_basis: str
    short_ma: int
    long_ma: int
    rebalance_when: str
    allow_short: bool


class S6DualMAStrategy(Strategy):
    """Golden/death cross: MA short crosses MA long, long-only."""

    def __init__(self, config: dict[str, Any]):
        self.config = S6DualMAConfig(
            asset=str(config["asset"]),
            signal_basis=str(config["signal_basis"]),
            short_ma=int(config["short_ma"]),
            long_ma=int(config["long_ma"]),
            rebalance_when=str(config["rebalance_when"]),
            allow_short=bool(config["allow_short"]),
        )
        if self.config.signal_basis != "daily_close":
            raise ValueError(f"Unsupported S6 signal_basis: {self.config.signal_basis}")
        if self.config.rebalance_when != "crossover_only":
            raise ValueError(f"Unsupported S6 rebalance_when: {self.config.rebalance_when}")
        if self.config.allow_short:
            raise ValueError("S6 Gate1 is long-only; allow_short must be false")
        if self.config.short_ma <= 0 or self.config.long_ma <= 0:
            raise ValueError("S6 MA lengths must be positive")
        if self.config.short_ma >= self.config.long_ma:
            raise ValueError("S6 requires short_ma < long_ma")

    def generate_signals(self, as_of_date: date, ctx: dict[str, Any]) -> list[Order]:
        self.assert_context_as_of(as_of_date, ctx)
        frame = ctx["data"].get(self.config.asset)
        if frame is None or frame.empty:
            return []
        frame = frame.sort_values("date").reset_index(drop=True)
        max_date = pd.to_datetime(frame["date"], errors="coerce").max()
        assert pd.notna(max_date) and max_date.date() <= as_of_date
        if len(frame) < self.config.long_ma + 1:
            return []

        close = pd.to_numeric(frame["close"], errors="coerce")
        if close.tail(self.config.long_ma + 1).isna().any():
            return []
        prev_short = close.iloc[-self.config.short_ma - 1 : -1].mean()
        prev_long = close.iloc[-self.config.long_ma - 1 : -1].mean()
        curr_short = close.iloc[-self.config.short_ma :].mean()
        curr_long = close.iloc[-self.config.long_ma :].mean()
        if any(pd.isna(item) for item in (prev_short, prev_long, curr_short, curr_long)):
            return []

        golden_cross = float(prev_short) < float(prev_long) and float(curr_short) >= float(curr_long)
        death_cross = float(prev_short) >= float(prev_long) and float(curr_short) < float(curr_long)
        positions: tuple[Position, ...] = tuple(ctx.get("positions", ()))
        current_quantity = sum(
            item.quantity for item in positions if item.symbol == self.config.asset and item.quantity > 0
        )

        if golden_cross and current_quantity <= 0:
            nav = float(ctx["nav"])
            lot_size = int(ctx.get("lot_size", 100))
            current_close = float(close.iloc[-1])
            quantity = _floor_to_lot(nav / current_close, lot_size)
            if quantity > 0:
                return [Order(symbol=self.config.asset, side="buy", quantity=quantity, submitted_date=as_of_date)]
        if death_cross and current_quantity > 0:
            return [Order(symbol=self.config.asset, side="sell", quantity=current_quantity, submitted_date=as_of_date)]
        return []


def _floor_to_lot(quantity: float, lot_size: int) -> int:
    if quantity <= 0:
        return 0
    if lot_size <= 1:
        return int(floor(quantity))
    return int(floor(quantity / lot_size) * lot_size)
