"""S9 monthly inverse-volatility ETF risk parity strategy."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from math import floor
from typing import Any, Mapping

import pandas as pd

from backtest.constraints import Order, Position
from strategies.base import Strategy


@dataclass(frozen=True)
class S9RiskParityAsset:
    symbol: str
    name: str


class S9RiskParityStrategy(Strategy):
    """Monthly ETF risk parity with weights proportional to inverse volatility."""

    def __init__(self, config: dict[str, Any]):
        pool = config.get("pool", ())
        self.assets = tuple(S9RiskParityAsset(symbol=str(item["code"]), name=str(item["name"])) for item in pool)
        self.lookback_vol_days = int(config["lookback_vol_days"])
        self.rebalance = str(config["rebalance"])
        self.weight_method = str(config["weight_method"])
        self.weight_normalize = bool(config["weight_normalize"])
        self.allow_short = bool(config.get("allow_short", False))

        if len(self.assets) < 2:
            raise ValueError("S9 requires at least two ETF assets")
        if self.lookback_vol_days <= 1:
            raise ValueError("S9 lookback_vol_days must be greater than 1")
        if self.rebalance != "monthly":
            raise ValueError(f"Unsupported S9 rebalance: {self.rebalance}")
        if self.weight_method != "inverse_volatility":
            raise ValueError(f"Unsupported S9 weight_method: {self.weight_method}")
        if not self.weight_normalize:
            raise ValueError("S9 Gate1 requires weight_normalize=true")
        if self.allow_short:
            raise ValueError("S9 Gate1 requires allow_short=false")

    @property
    def symbols(self) -> tuple[str, ...]:
        return tuple(item.symbol for item in self.assets)

    def target_weights(self, as_of_date: date, ctx: dict[str, Any]) -> dict[str, float]:
        self.assert_context_as_of(as_of_date, ctx)
        if as_of_date not in set(ctx.get("month_end_dates", ())):
            return {}

        data: dict[str, pd.DataFrame] = ctx["data"]
        inverse_vols: list[tuple[str, float]] = []
        for asset in self.assets:
            frame = _sorted_frame(data.get(asset.symbol))
            if frame.empty:
                return {}
            max_date = pd.to_datetime(frame["date"], errors="coerce").max()
            assert pd.notna(max_date) and max_date.date() <= as_of_date
            if max_date.date() != as_of_date:
                return {}

            close = pd.to_numeric(frame["close"], errors="coerce")
            returns = close.pct_change().dropna()
            if len(returns) < self.lookback_vol_days:
                return {}
            sigma = returns.tail(self.lookback_vol_days).std(ddof=1)
            if pd.isna(sigma) or float(sigma) <= 0.0:
                return {}
            inverse_vols.append((asset.symbol, 1.0 / float(sigma)))

        total = sum(value for _symbol, value in inverse_vols)
        if total <= 0.0:
            return {}
        return {symbol: value / total for symbol, value in inverse_vols}

    def generate_signals(self, as_of_date: date, ctx: dict[str, Any]) -> list[Order]:
        weights = self.target_weights(as_of_date, ctx)
        if not weights:
            return []
        return orders_for_target_weights(self.symbols, weights, as_of_date, ctx)


def orders_for_target_weights(
    symbols: tuple[str, ...],
    target_weights: Mapping[str, float],
    as_of_date: date,
    ctx: dict[str, Any],
) -> list[Order]:
    data: dict[str, pd.DataFrame] = ctx["data"]
    positions: tuple[Position, ...] = tuple(ctx.get("positions", ()))
    nav = float(ctx["nav"])
    lot_size = int(ctx.get("lot_size", 100))
    symbol_set = set(symbols)
    current_qty = {item.symbol: item.quantity for item in positions if item.symbol in symbol_set and item.quantity > 0}

    orders: list[Order] = []
    for symbol, quantity in sorted(current_qty.items()):
        if symbol not in target_weights or float(target_weights.get(symbol, 0.0)) <= 0.0:
            orders.append(Order(symbol=symbol, side="sell", quantity=quantity, submitted_date=as_of_date))

    for symbol in symbols:
        weight = float(target_weights.get(symbol, 0.0))
        close = _latest_close(data.get(symbol))
        if close is None:
            continue
        target_quantity = _floor_to_lot(nav * weight / close, lot_size)
        diff = target_quantity - current_qty.get(symbol, 0)
        if diff > 0:
            orders.append(Order(symbol=symbol, side="buy", quantity=diff, submitted_date=as_of_date))
        elif diff < 0:
            orders.append(Order(symbol=symbol, side="sell", quantity=abs(diff), submitted_date=as_of_date))
    return sorted(orders, key=lambda item: 0 if item.side == "sell" else 1)


def _sorted_frame(frame: pd.DataFrame | None) -> pd.DataFrame:
    if not isinstance(frame, pd.DataFrame) or frame.empty:
        return pd.DataFrame()
    return frame.sort_values("date").reset_index(drop=True)


def _latest_close(frame: pd.DataFrame | None) -> float | None:
    rows = _sorted_frame(frame)
    if rows.empty:
        return None
    close = pd.to_numeric(rows["close"], errors="coerce").iloc[-1]
    if pd.isna(close) or float(close) <= 0.0:
        return None
    return float(close)


def _floor_to_lot(quantity: float, lot_size: int) -> int:
    if quantity <= 0:
        return 0
    if lot_size <= 1:
        return int(floor(quantity))
    return int(floor(quantity / lot_size) * lot_size)
