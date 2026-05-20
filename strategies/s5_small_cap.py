"""S5 classic small-cap monthly rebalance strategy."""

from __future__ import annotations

from datetime import date
from math import floor
from typing import Any

import numpy as np
import pandas as pd

from backtest.constraints import Order, Position
from strategies.base import Strategy


class S5SmallCapStrategy(Strategy):
    """Select the smallest PIT float-market-cap stocks with D-1 market cap."""

    def __init__(self, config: dict[str, Any]):
        self.rebalance = str(config["rebalance"])
        self.hold_n = int(config["hold_n"])
        self.exclude_st = bool(config["exclude_st"])
        self.exclude_new_days = int(config["exclude_new_days"])
        self.min_price = float(config["min_price"])
        self.max_market_cap_rank_pct = config.get("max_market_cap_rank_pct")
        self.weight = str(config["weight"])

        if self.rebalance != "monthly":
            raise ValueError(f"Unsupported S5 rebalance: {self.rebalance}")
        if self.hold_n <= 0:
            raise ValueError("S5 hold_n must be positive")
        if self.weight != "equal":
            raise ValueError(f"Unsupported S5 weight: {self.weight}")

    def generate_signals(self, as_of_date: date, ctx: dict[str, Any]) -> list[Order]:
        self.assert_context_as_of(as_of_date, ctx)
        if as_of_date not in set(ctx.get("rebalance_dates", ())):
            return []

        candidates = _candidate_rows(ctx.get("candidates"), as_of_date)
        if candidates.empty:
            return []
        _assert_pit_candidates(candidates, as_of_date, self.exclude_st, self.exclude_new_days, self.min_price)

        selected = candidates.sort_values(["prev_float_mv", "symbol"], ascending=[True, True]).head(self.hold_n)
        selected_symbols = set(selected["symbol"].astype(str))

        positions: tuple[Position, ...] = tuple(ctx.get("positions", ()))
        current_qty = {item.symbol: item.quantity for item in positions if item.quantity > 0}
        orders: list[Order] = []
        for symbol, quantity in sorted(current_qty.items()):
            if symbol not in selected_symbols:
                orders.append(Order(symbol=symbol, side="sell", quantity=quantity, submitted_date=as_of_date))

        nav = float(ctx["nav"])
        lot_size = int(ctx.get("lot_size", 100))
        target_value = nav / len(selected)
        for row in selected.sort_values("symbol").itertuples(index=False):
            symbol = str(row.symbol)
            close = float(row.close)
            if close <= 0 or not np.isfinite(close):
                continue
            target_quantity = _floor_to_lot(target_value / close, lot_size)
            diff = target_quantity - current_qty.get(symbol, 0)
            if diff > 0:
                orders.append(Order(symbol=symbol, side="buy", quantity=diff, submitted_date=as_of_date))
            elif diff < 0:
                orders.append(Order(symbol=symbol, side="sell", quantity=abs(diff), submitted_date=as_of_date))
        return sorted(orders, key=lambda item: 0 if item.side == "sell" else 1)


def _candidate_rows(value: Any, as_of_date: date) -> pd.DataFrame:
    if not isinstance(value, pd.DataFrame) or value.empty:
        return pd.DataFrame()
    rows = value.copy()
    rows["as_of_date"] = pd.to_datetime(rows["as_of_date"], errors="coerce").dt.date
    assert rows["as_of_date"].max() <= as_of_date, f"future candidate date detected: {rows['as_of_date'].max()} > {as_of_date}"
    return rows[rows["as_of_date"] == as_of_date].copy()


def _assert_pit_candidates(
    rows: pd.DataFrame,
    as_of_date: date,
    exclude_st: bool,
    exclude_new_days: int,
    min_price: float,
) -> None:
    if rows.empty:
        return
    as_of = pd.Timestamp(as_of_date)
    list_dates = pd.to_datetime(rows["list_date"], errors="coerce")
    delist_dates = pd.to_datetime(rows["delist_date"], errors="coerce")
    mv_dates = pd.to_datetime(rows["mv_date"], errors="coerce")
    row_dates = pd.to_datetime(rows["as_of_date"], errors="coerce")
    assert list_dates.notna().all(), "S5 candidate missing list_date"
    assert (list_dates <= as_of).all(), "S5 candidate listed after as_of_date"
    assert (delist_dates.isna() | (delist_dates > as_of)).all(), "S5 candidate delisted by as_of_date"
    assert mv_dates.notna().all(), "S5 candidate missing D-1 market-cap date"
    assert (mv_dates < row_dates).all(), "S5 candidate uses D-day market cap; expected mv_date < as_of_date"
    assert (pd.to_numeric(rows["age_days"], errors="coerce") >= exclude_new_days).all(), "S5 candidate violates new-listing filter"
    assert (pd.to_numeric(rows["close"], errors="coerce") >= min_price).all(), "S5 candidate violates min_price filter"
    assert np.isfinite(pd.to_numeric(rows["prev_float_mv"], errors="coerce")).all(), "S5 candidate has non-finite prev_float_mv"
    assert (pd.to_numeric(rows["prev_float_mv"], errors="coerce") > 0).all(), "S5 candidate has non-positive prev_float_mv"
    if exclude_st:
        assert (~rows["is_st"].astype(bool)).all(), "S5 candidate violates ST exclusion"


def _floor_to_lot(quantity: float, lot_size: int) -> int:
    if quantity <= 0:
        return 0
    if lot_size <= 1:
        return int(floor(quantity))
    return int(floor(quantity / lot_size) * lot_size)
