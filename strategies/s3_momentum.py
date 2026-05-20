"""S3 daily momentum rotation strategy."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from math import floor
from typing import Any

import pandas as pd

from backtest.constraints import Order, Position
from strategies.base import Strategy


@dataclass(frozen=True)
class S3Asset:
    symbol: str
    name: str
    kind: str


S3_ASSET_POOL: tuple[S3Asset, ...] = (
    S3Asset("sh000300", "沪深300", "index"),
    S3Asset("sh000905", "中证500", "index"),
    S3Asset("512880", "证券ETF", "etf"),
    S3Asset("512800", "银行ETF", "etf"),
    S3Asset("159995", "芯片ETF", "etf"),
    S3Asset("512010", "医药ETF", "etf"),
)


class S3MomentumStrategy(Strategy):
    def __init__(self, config: dict[str, Any]):
        self.lookback_days = int(config["lookback_days"])
        self.top_k = int(config["top_k"])
        self.trend_filter_ma = int(config["trend_filter_ma"])
        self.rebalance = str(config["rebalance"])
        if self.rebalance != "daily":
            raise ValueError(f"Unsupported S3 rebalance: {self.rebalance}")

    def generate_signals(self, as_of_date: date, ctx: dict[str, Any]) -> list[Order]:
        self.assert_context_as_of(as_of_date, ctx)
        data: dict[str, pd.DataFrame] = ctx["data"]
        positions: tuple[Position, ...] = tuple(ctx.get("positions", ()))
        nav = float(ctx["nav"])
        lot_size = int(ctx.get("lot_size", 100))

        candidates = []
        min_rows = max(self.lookback_days + 1, self.trend_filter_ma)
        for asset in S3_ASSET_POOL:
            frame = data.get(asset.symbol)
            if frame is None or frame.empty:
                continue
            frame = frame.sort_values("date")
            assert pd.to_datetime(frame["date"], errors="coerce").max().date() <= as_of_date
            if len(frame) < min_rows:
                continue
            close = pd.to_numeric(frame["close"], errors="coerce")
            current_close = close.iloc[-1]
            lookback_close = close.iloc[-self.lookback_days - 1]
            ma = close.tail(self.trend_filter_ma).mean()
            if pd.isna(current_close) or pd.isna(lookback_close) or pd.isna(ma):
                continue
            if current_close <= ma:
                continue
            momentum = current_close / lookback_close - 1.0
            candidates.append((asset.symbol, float(momentum), float(current_close)))

        selected = {
            symbol: close
            for symbol, _momentum, close in sorted(candidates, key=lambda item: item[1], reverse=True)[: self.top_k]
        }
        current_qty = {item.symbol: item.quantity for item in positions if item.quantity > 0}
        orders: list[Order] = []

        for symbol, quantity in sorted(current_qty.items()):
            if symbol not in selected:
                orders.append(Order(symbol=symbol, side="sell", quantity=quantity, submitted_date=as_of_date))

        if not selected:
            return orders

        target_value = nav / len(selected)
        for symbol, close in sorted(selected.items()):
            target_quantity = _floor_to_lot(target_value / close, lot_size)
            diff = target_quantity - current_qty.get(symbol, 0)
            if diff > 0:
                orders.append(Order(symbol=symbol, side="buy", quantity=diff, submitted_date=as_of_date))
            elif diff < 0:
                orders.append(Order(symbol=symbol, side="sell", quantity=abs(diff), submitted_date=as_of_date))
        return sorted(orders, key=lambda item: 0 if item.side == "sell" else 1)


def _floor_to_lot(quantity: float, lot_size: int) -> int:
    if quantity <= 0:
        return 0
    if lot_size <= 1:
        return int(floor(quantity))
    return int(floor(quantity / lot_size) * lot_size)
