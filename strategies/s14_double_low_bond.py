"""S14 convertible bond double-low monthly strategy."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta
from math import floor
from typing import Any

import pandas as pd

from backtest.constraints import Order, Position
from strategies.base import Strategy


@dataclass(frozen=True)
class S14CandidateStats:
    total_asof: int
    in_universe: int
    after_listing_age: int
    after_price: int
    after_premium: int
    after_liquidity: int
    after_redeem: int
    selected: int


class S14DoubleLowBondStrategy(Strategy):
    """Monthly Top-N low price + low premium convertible bond selector."""

    def __init__(self, config: dict[str, Any]):
        self.hold_n = int(config["hold_n"])
        filters = config["filters"]
        self.price_max = float(filters["price_max"])
        self.premium_max = float(filters["premium_max"])
        self.min_volume_yuan = float(filters["min_volume_yuan"])
        self.exclude_redeem_pending = bool(filters["exclude_redeem_pending"])
        self.exclude_new_days = int(filters["exclude_new_days"])
        self.weight = str(config["weight"])

        if self.hold_n <= 0:
            raise ValueError("S14 hold_n must be positive")
        if self.weight != "equal":
            raise ValueError(f"Unsupported S14 weight: {self.weight}")

    def filtered_candidates(self, as_of_date: date, panel_asof: pd.DataFrame) -> tuple[pd.DataFrame, S14CandidateStats]:
        if panel_asof.empty:
            return panel_asof.copy(), S14CandidateStats(0, 0, 0, 0, 0, 0, 0, 0)

        frame = panel_asof.copy()
        frame["date"] = pd.to_datetime(frame["date"], errors="coerce").dt.date
        max_date = max(frame["date"].dropna().tolist())
        assert max_date <= as_of_date, f"future data detected: {max_date} > {as_of_date}"

        total = len(frame)
        frame = frame[frame["in_universe"].fillna(False)].copy()
        in_universe = len(frame)

        frame["listing_date"] = pd.to_datetime(frame["listing_date"], errors="coerce").dt.date
        min_listing_date = as_of_date - timedelta(days=self.exclude_new_days)
        frame = frame[frame["listing_date"].notna() & (frame["listing_date"] <= min_listing_date)].copy()
        after_listing_age = len(frame)

        frame["close"] = pd.to_numeric(frame["close"], errors="coerce")
        frame = frame[frame["close"].notna() & (frame["close"] > 0.0) & (frame["close"] < self.price_max)].copy()
        after_price = len(frame)

        frame["premium_rate"] = pd.to_numeric(frame["premium_rate"], errors="coerce")
        frame = frame[frame["premium_rate"].notna() & (frame["premium_rate"] < self.premium_max)].copy()
        after_premium = len(frame)

        frame["amount_proxy"] = pd.to_numeric(frame["amount_proxy"], errors="coerce")
        frame = frame[frame["amount_proxy"].notna() & (frame["amount_proxy"] > self.min_volume_yuan)].copy()
        after_liquidity = len(frame)

        if self.exclude_redeem_pending:
            trigger_count = pd.to_numeric(frame.get("redeem_trigger_count_30"), errors="coerce").fillna(0)
            delist_dates = pd.to_datetime(frame.get("delist_date"), errors="coerce").dt.date
            days_to_delist = delist_dates.map(lambda item: (item - as_of_date).days if pd.notna(item) else pd.NA)
            near_delist = days_to_delist.map(lambda item: pd.notna(item) and 0 <= int(item) < 30)
            frame = frame[(trigger_count < 15) & (~near_delist.fillna(False))].copy()
        after_redeem = len(frame)

        frame["score"] = frame["close"] + frame["premium_rate"] * 100.0
        candidates = frame.sort_values(["score", "close", "premium_rate", "symbol"], ascending=True).copy()
        stats = S14CandidateStats(
            total_asof=total,
            in_universe=in_universe,
            after_listing_age=after_listing_age,
            after_price=after_price,
            after_premium=after_premium,
            after_liquidity=after_liquidity,
            after_redeem=after_redeem,
            selected=min(len(candidates), self.hold_n),
        )
        return candidates, stats

    def select_candidates(self, as_of_date: date, panel_asof: pd.DataFrame) -> tuple[pd.DataFrame, S14CandidateStats]:
        candidates, stats = self.filtered_candidates(as_of_date, panel_asof)
        selected = candidates.head(self.hold_n).copy()
        return selected, stats

    def target_weights(self, as_of_date: date, ctx: dict[str, Any]) -> dict[str, float]:
        panel_asof = ctx.get("panel_asof")
        if not isinstance(panel_asof, pd.DataFrame):
            return {}
        selected, _stats = self.select_candidates(as_of_date, panel_asof)
        if selected.empty:
            return {}
        weight = 1.0 / len(selected)
        return {str(symbol): weight for symbol in selected["symbol"].tolist()}

    def generate_signals(self, as_of_date: date, ctx: dict[str, Any]) -> list[Order]:
        panel_asof = ctx.get("panel_asof")
        if not isinstance(panel_asof, pd.DataFrame) or panel_asof.empty:
            return []
        self.assert_context_as_of(as_of_date, {"data": {"panel_asof": panel_asof}})
        weights = self.target_weights(as_of_date, ctx)
        return orders_for_target_weights(weights, as_of_date, ctx)


def orders_for_target_weights(target_weights: dict[str, float], as_of_date: date, ctx: dict[str, Any]) -> list[Order]:
    panel_asof = ctx["panel_asof"]
    positions: tuple[Position, ...] = tuple(ctx.get("positions", ()))
    nav = float(ctx["nav"])
    lot_size = int(ctx.get("lot_size", 10))

    close_by_symbol = {
        str(row["symbol"]): float(row["close"])
        for _idx, row in panel_asof.iterrows()
        if pd.notna(row.get("close")) and float(row.get("close")) > 0.0
    }
    current_qty = {item.symbol: item.quantity for item in positions if item.quantity > 0}
    orders: list[Order] = []

    for symbol, quantity in sorted(current_qty.items()):
        if float(target_weights.get(symbol, 0.0)) <= 0.0:
            orders.append(Order(symbol=symbol, side="sell", quantity=quantity, submitted_date=as_of_date))

    for symbol, weight in sorted(target_weights.items()):
        close = close_by_symbol.get(symbol)
        if close is None:
            continue
        target_quantity = _floor_to_lot(nav * float(weight) / close, lot_size)
        diff = target_quantity - current_qty.get(symbol, 0)
        if diff > 0:
            orders.append(Order(symbol=symbol, side="buy", quantity=diff, submitted_date=as_of_date))
        elif diff < 0:
            orders.append(Order(symbol=symbol, side="sell", quantity=abs(diff), submitted_date=as_of_date))
    return sorted(orders, key=lambda item: 0 if item.side == "sell" else 1)


def _floor_to_lot(quantity: float, lot_size: int) -> int:
    if quantity <= 0.0:
        return 0
    if lot_size <= 1:
        return int(floor(quantity))
    return int(floor(quantity / lot_size) * lot_size)
