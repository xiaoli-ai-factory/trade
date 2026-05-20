"""S8 RSI reversal strategy."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from math import floor
from typing import Any

import pandas as pd

from backtest.constraints import Order, Position
from strategies.base import Strategy


@dataclass(frozen=True)
class S8RSIReversalConfig:
    asset: str
    signal_basis: str
    rsi_period: int
    buy_threshold: float
    sell_threshold: float
    rebalance_when: str


class S8RSIReversalStrategy(Strategy):
    """Long-only RSI reversal: buy oversold recovery, sell overbought breakdown."""

    def __init__(self, config: dict[str, Any]):
        self.config = S8RSIReversalConfig(
            asset=str(config["asset"]),
            signal_basis=str(config["signal_basis"]),
            rsi_period=int(config["rsi_period"]),
            buy_threshold=float(config["buy_threshold"]),
            sell_threshold=float(config["sell_threshold"]),
            rebalance_when=str(config["rebalance_when"]),
        )
        if self.config.signal_basis != "daily_close":
            raise ValueError(f"Unsupported S8 signal_basis: {self.config.signal_basis}")
        if self.config.rebalance_when != "crossover_only":
            raise ValueError(f"Unsupported S8 rebalance_when: {self.config.rebalance_when}")
        if self.config.rsi_period <= 0:
            raise ValueError("S8 rsi_period must be positive")
        if not 0.0 < self.config.buy_threshold < self.config.sell_threshold < 100.0:
            raise ValueError("S8 requires 0 < buy_threshold < sell_threshold < 100")

    def generate_signals(self, as_of_date: date, ctx: dict[str, Any]) -> list[Order]:
        self.assert_context_as_of(as_of_date, ctx)
        frame = ctx["data"].get(self.config.asset)
        if frame is None or frame.empty:
            return []
        frame = frame.sort_values("date").reset_index(drop=True)
        max_date = pd.to_datetime(frame["date"], errors="coerce").max()
        assert pd.notna(max_date) and max_date.date() <= as_of_date
        if len(frame) < self.config.rsi_period + 2:
            return []

        close = pd.to_numeric(frame["close"], errors="coerce")
        rsi = wilder_rsi(close, self.config.rsi_period)
        prev_rsi = rsi.iloc[-2]
        curr_rsi = rsi.iloc[-1]
        current_close = close.iloc[-1]
        if pd.isna(prev_rsi) or pd.isna(curr_rsi) or pd.isna(current_close):
            return []

        buy_cross = float(prev_rsi) <= self.config.buy_threshold and float(curr_rsi) > self.config.buy_threshold
        sell_cross = float(prev_rsi) >= self.config.sell_threshold and float(curr_rsi) < self.config.sell_threshold
        positions: tuple[Position, ...] = tuple(ctx.get("positions", ()))
        current_quantity = sum(
            item.quantity for item in positions if item.symbol == self.config.asset and item.quantity > 0
        )

        if buy_cross and current_quantity <= 0:
            nav = float(ctx["nav"])
            lot_size = int(ctx.get("lot_size", 100))
            quantity = _floor_to_lot(nav / float(current_close), lot_size)
            if quantity > 0:
                return [Order(symbol=self.config.asset, side="buy", quantity=quantity, submitted_date=as_of_date)]
        if sell_cross and current_quantity > 0:
            return [Order(symbol=self.config.asset, side="sell", quantity=current_quantity, submitted_date=as_of_date)]
        return []


def wilder_rsi(closes: pd.Series, period: int) -> pd.Series:
    """Return classic Wilder RSI, seeded by the first period of price changes."""
    if period <= 0:
        raise ValueError("period must be positive")
    close = pd.to_numeric(closes, errors="coerce")
    rsi = pd.Series(float("nan"), index=close.index, dtype=float)
    if len(close) < period + 1:
        return rsi

    delta = close.diff()
    gains = delta.clip(lower=0.0)
    losses = -delta.clip(upper=0.0)
    seed_gains = gains.iloc[1 : period + 1]
    seed_losses = losses.iloc[1 : period + 1]
    if seed_gains.isna().any() or seed_losses.isna().any():
        return rsi

    avg_gain = float(seed_gains.mean())
    avg_loss = float(seed_losses.mean())
    rsi.iloc[period] = _rsi_value(avg_gain, avg_loss)

    for idx in range(period + 1, len(close)):
        gain = gains.iloc[idx]
        loss = losses.iloc[idx]
        if pd.isna(gain) or pd.isna(loss):
            avg_gain = float("nan")
            avg_loss = float("nan")
            continue
        avg_gain = (avg_gain * (period - 1) + float(gain)) / period
        avg_loss = (avg_loss * (period - 1) + float(loss)) / period
        rsi.iloc[idx] = _rsi_value(avg_gain, avg_loss)
    return rsi


def _rsi_value(avg_gain: float, avg_loss: float) -> float:
    if pd.isna(avg_gain) or pd.isna(avg_loss):
        return float("nan")
    if avg_loss == 0.0:
        return 50.0 if avg_gain == 0.0 else 100.0
    rs = avg_gain / avg_loss
    return 100.0 - (100.0 / (1.0 + rs))


def _floor_to_lot(quantity: float, lot_size: int) -> int:
    if quantity <= 0:
        return 0
    if lot_size <= 1:
        return int(floor(quantity))
    return int(floor(quantity / lot_size) * lot_size)
