"""S7 limit-up next-day follow-up strategy."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from math import floor
from typing import Any

import numpy as np
import pandas as pd

from backtest.constraints import Order, Position
from strategies.base import Strategy


@dataclass(frozen=True)
class S7LimitUpFollowupConfig:
    rebalance: str
    signal: str
    buy_time: str
    hold_period_days: int
    max_positions: int
    exclude_st: bool
    exclude_new_days: int
    prefilter_min_amount: float


class S7LimitUpFollowupStrategy(Strategy):
    """Buy the highest-amount limit-up stocks next open, sell next open."""

    def __init__(self, config: dict[str, Any]):
        self.config = S7LimitUpFollowupConfig(
            rebalance=str(config["rebalance"]),
            signal=str(config["signal"]),
            buy_time=str(config["buy_time"]),
            hold_period_days=int(config["hold_period_days"]),
            max_positions=int(config["max_positions"]),
            exclude_st=bool(config["exclude_st"]),
            exclude_new_days=int(config["exclude_new_days"]),
            prefilter_min_amount=float(config["prefilter_min_amount"]),
        )
        if self.config.rebalance != "daily_signal":
            raise ValueError(f"Unsupported S7 rebalance: {self.config.rebalance}")
        if self.config.signal != "yesterday_limit_up":
            raise ValueError(f"Unsupported S7 signal: {self.config.signal}")
        if self.config.buy_time != "D_open":
            raise ValueError(f"Unsupported S7 buy_time: {self.config.buy_time}")
        if self.config.hold_period_days != 1:
            raise ValueError("S7 Gate1 requires hold_period_days=1")
        if self.config.max_positions <= 0:
            raise ValueError("S7 max_positions must be positive")

    def generate_signals(self, as_of_date: date, ctx: dict[str, Any]) -> list[Order]:
        self.assert_context_as_of(as_of_date, ctx)
        candidates = _candidate_rows(ctx.get("candidates"), as_of_date)
        if not candidates.empty:
            _assert_pit_candidates(candidates, as_of_date, self.config)

        positions: tuple[Position, ...] = tuple(ctx.get("positions", ()))
        current_qty = {item.symbol: item.quantity for item in positions if item.quantity > 0}
        orders: list[Order] = []
        for position in sorted(positions, key=lambda item: item.symbol):
            if position.quantity > 0 and position.sellable and position.buy_date <= as_of_date:
                orders.append(Order(symbol=position.symbol, side="sell", quantity=position.quantity, submitted_date=as_of_date))

        if candidates.empty:
            return orders

        selected = candidates.sort_values(["amount", "symbol"], ascending=[False, True]).head(self.config.max_positions)
        nav = float(ctx["nav"])
        target_value = nav / self.config.max_positions
        lot_size = int(ctx.get("lot_size", 100))
        for row in selected.itertuples(index=False):
            symbol = str(row.symbol)
            if current_qty.get(symbol, 0) > 0:
                continue
            close = float(row.close)
            if close <= 0 or not np.isfinite(close):
                continue
            quantity = _floor_to_lot(target_value / close, lot_size)
            if quantity > 0:
                orders.append(Order(symbol=symbol, side="buy", quantity=quantity, submitted_date=as_of_date))
        return sorted(orders, key=lambda item: 0 if item.side == "sell" else 1)


def _candidate_rows(value: Any, as_of_date: date) -> pd.DataFrame:
    if not isinstance(value, pd.DataFrame) or value.empty:
        return pd.DataFrame()
    rows = value.copy()
    rows["as_of_date"] = pd.to_datetime(rows["as_of_date"], errors="coerce").dt.date
    assert rows["as_of_date"].max() <= as_of_date, f"future candidate date detected: {rows['as_of_date'].max()} > {as_of_date}"
    return rows[rows["as_of_date"] == as_of_date].copy()


def _assert_pit_candidates(rows: pd.DataFrame, as_of_date: date, cfg: S7LimitUpFollowupConfig) -> None:
    if rows.empty:
        return
    as_of = pd.Timestamp(as_of_date)
    list_dates = pd.to_datetime(rows["list_date"], errors="coerce")
    delist_dates = pd.to_datetime(rows["delist_date"], errors="coerce")
    as_of_dates = pd.to_datetime(rows["as_of_date"], errors="coerce")
    assert list_dates.notna().all(), "S7 candidate missing list_date"
    assert (list_dates <= as_of).all(), "S7 candidate listed after as_of_date"
    assert (delist_dates.isna() | (delist_dates > as_of)).all(), "S7 candidate delisted by as_of_date"
    assert (as_of_dates <= as_of).all(), "S7 candidate is future dated"
    assert (pd.to_numeric(rows["age_days"], errors="coerce") >= cfg.exclude_new_days).all(), "S7 candidate violates new-listing filter"
    assert (pd.to_numeric(rows["amount"], errors="coerce") >= cfg.prefilter_min_amount).all(), "S7 candidate violates amount filter"
    assert _is_limit_up(rows).all(), "S7 candidate is not D-day limit-up by close==limit_up_price"
    if cfg.exclude_st:
        assert (~rows["is_st"].astype(bool)).all(), "S7 candidate violates ST exclusion"


def _is_limit_up(rows: pd.DataFrame) -> pd.Series:
    close = pd.to_numeric(rows["close"], errors="coerce").round(2)
    limit_up = pd.to_numeric(rows["limit_up_price"], errors="coerce").round(2)
    return close.notna() & limit_up.notna() & (close == limit_up)


def _floor_to_lot(quantity: float, lot_size: int) -> int:
    if quantity <= 0:
        return 0
    if lot_size <= 1:
        return int(floor(quantity))
    return int(floor(quantity / lot_size) * lot_size)
