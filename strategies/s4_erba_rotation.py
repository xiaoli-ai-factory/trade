"""S4 classic 2/8 monthly rotation strategy.

Signals are computed from broad index closes and executed on tradable ETFs.
The runner supplies month-end trading dates from the exchange calendar; this
uses no future price data.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from math import floor
from typing import Any

import pandas as pd

from backtest.constraints import Order, Position
from strategies.base import Strategy


@dataclass(frozen=True)
class S4ErbaAsset:
    index_symbol: str
    index_name: str
    etf_symbol: str
    etf_name: str


class S4ErbaRotationStrategy(Strategy):
    """Monthly 2/8 rotation: choose the stronger CSI300/CSI500 index."""

    def __init__(self, config: dict[str, Any]):
        pool = config.get("pool", ())
        self.assets = tuple(
            S4ErbaAsset(
                index_symbol=str(item["code"]),
                index_name=str(item["name"]),
                etf_symbol=str(item["etf_code"]),
                etf_name=str(item["etf_name"]),
            )
            for item in pool
        )
        self.lookback_days = int(config["lookback_days"])
        self.signal_basis = str(config["signal_basis"])
        self.rebalance = str(config["rebalance"])
        self.trend_filter_ma = config.get("trend_filter_ma")
        self.allow_cash = bool(config.get("allow_cash", False))

        if len(self.assets) != 2:
            raise ValueError("S4 requires exactly two index/ETF pairs")
        if self.signal_basis != "month_end_close":
            raise ValueError(f"Unsupported S4 signal_basis: {self.signal_basis}")
        if self.rebalance != "monthly":
            raise ValueError(f"Unsupported S4 rebalance: {self.rebalance}")
        if self.trend_filter_ma is not None:
            raise ValueError("S4 classic Gate1 requires trend_filter_ma=null")
        if self.allow_cash:
            raise ValueError("S4 classic Gate1 requires allow_cash=false")

    @property
    def index_symbols(self) -> tuple[str, ...]:
        return tuple(item.index_symbol for item in self.assets)

    @property
    def etf_symbols(self) -> tuple[str, ...]:
        return tuple(item.etf_symbol for item in self.assets)

    def generate_signals(self, as_of_date: date, ctx: dict[str, Any]) -> list[Order]:
        combined_data: dict[str, pd.DataFrame] = {}
        combined_data.update(ctx.get("data", {}))
        combined_data.update(ctx.get("index_data", {}))
        self.assert_context_as_of(as_of_date, {"data": combined_data})

        if as_of_date not in set(ctx.get("month_end_dates", ())):
            return []

        index_data: dict[str, pd.DataFrame] = ctx.get("index_data", {})
        etf_data: dict[str, pd.DataFrame] = ctx.get("data", {})
        ranked: list[tuple[S4ErbaAsset, float]] = []
        for asset in self.assets:
            frame = _sorted_frame(index_data.get(asset.index_symbol))
            if frame.empty or len(frame) < self.lookback_days + 1:
                return []
            max_date = pd.to_datetime(frame["date"], errors="coerce").max()
            assert pd.notna(max_date) and max_date.date() <= as_of_date
            if max_date.date() != as_of_date:
                return []
            close = pd.to_numeric(frame["close"], errors="coerce")
            current_close = close.iloc[-1]
            lookback_close = close.iloc[-self.lookback_days - 1]
            if pd.isna(current_close) or pd.isna(lookback_close) or float(lookback_close) <= 0:
                return []
            ranked.append((asset, float(current_close) / float(lookback_close) - 1.0))

        selected_asset = sorted(ranked, key=lambda item: item[1], reverse=True)[0][0]
        target_symbol = selected_asset.etf_symbol
        target_frame = _sorted_frame(etf_data.get(target_symbol))
        if target_frame.empty:
            return []
        target_max_date = pd.to_datetime(target_frame["date"], errors="coerce").max()
        assert pd.notna(target_max_date) and target_max_date.date() <= as_of_date
        if target_max_date.date() != as_of_date:
            return []
        target_close = pd.to_numeric(target_frame["close"], errors="coerce").iloc[-1]
        if pd.isna(target_close) or float(target_close) <= 0:
            return []

        positions: tuple[Position, ...] = tuple(ctx.get("positions", ()))
        current_qty = {
            item.symbol: item.quantity
            for item in positions
            if item.symbol in self.etf_symbols and item.quantity > 0
        }
        orders: list[Order] = []
        for symbol, quantity in sorted(current_qty.items()):
            if symbol != target_symbol:
                orders.append(Order(symbol=symbol, side="sell", quantity=quantity, submitted_date=as_of_date))

        nav = float(ctx["nav"])
        lot_size = int(ctx.get("lot_size", 100))
        target_quantity = _floor_to_lot(nav / float(target_close), lot_size)
        diff = target_quantity - current_qty.get(target_symbol, 0)
        if diff > 0:
            orders.append(Order(symbol=target_symbol, side="buy", quantity=diff, submitted_date=as_of_date))
        elif diff < 0:
            orders.append(Order(symbol=target_symbol, side="sell", quantity=abs(diff), submitted_date=as_of_date))
        return sorted(orders, key=lambda item: 0 if item.side == "sell" else 1)


def _sorted_frame(frame: pd.DataFrame | None) -> pd.DataFrame:
    if not isinstance(frame, pd.DataFrame) or frame.empty:
        return pd.DataFrame()
    return frame.sort_values("date").reset_index(drop=True)


def _floor_to_lot(quantity: float, lot_size: int) -> int:
    if quantity <= 0:
        return 0
    if lot_size <= 1:
        return int(floor(quantity))
    return int(floor(quantity / lot_size) * lot_size)
