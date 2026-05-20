"""Shared cost, slippage, T+1, and matching constraints.

This module is intentionally pure: no file IO, no clock reads, no randomness.
Callers must load ``configs/cost.yaml`` and pass the parsed mapping in.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import date, datetime
from typing import Any, Literal, Mapping, Sequence

import pandas as pd


Side = Literal["buy", "sell"]
Status = Literal["filled", "rejected"]


@dataclass(frozen=True)
class CostConfig:
    commission_rate: float
    min_per_order: float
    stamp_sell_rate: float
    transfer_rate: float
    slippage_rate: float

    @classmethod
    def from_mapping(cls, config: Mapping[str, Any]) -> "CostConfig":
        return cls(
            commission_rate=float(config["commission"]["rate"]),
            min_per_order=float(config["commission"]["min_per_order"]),
            stamp_sell_rate=float(config["stamp_duty"]["sell_rate"]),
            transfer_rate=float(config["transfer_fee"]["rate"]),
            slippage_rate=float(config["slippage"]["rate"]),
        )


@dataclass(frozen=True)
class Order:
    symbol: str
    side: Side
    quantity: int
    submitted_date: date | None = None


@dataclass(frozen=True)
class Position:
    symbol: str
    quantity: int
    avg_price: float
    buy_date: date
    sellable: bool = False


@dataclass(frozen=True)
class MarketBar:
    symbol: str
    date: date
    open: float
    limit_up_price: float | None = None
    limit_down_price: float | None = None
    is_suspended: bool = False


@dataclass(frozen=True)
class ExecutionResult:
    order: Order
    status: Status
    reason: str | None
    quantity: int
    base_price: float | None
    fill_price: float | None
    amount: float
    cost: float
    cash_delta: float
    events: tuple[dict[str, Any], ...] = ()


def parse_date(value: date | datetime | str | pd.Timestamp) -> date:
    if isinstance(value, pd.Timestamp):
        return value.date()
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    return pd.Timestamp(str(value)).date()


def cost_config_from_mapping(config: Mapping[str, Any]) -> CostConfig:
    return CostConfig.from_mapping(config)


def trade_cost(amount: float, side: Side, cost_config: CostConfig) -> float:
    commission = max(amount * cost_config.commission_rate, cost_config.min_per_order)
    transfer = amount * cost_config.transfer_rate
    stamp = amount * cost_config.stamp_sell_rate if side == "sell" else 0.0
    return commission + transfer + stamp


def slippage_price(base_price: float, side: Side, cost_config: CostConfig) -> float:
    if side == "buy":
        return base_price * (1.0 + cost_config.slippage_rate)
    return base_price * (1.0 - cost_config.slippage_rate)


def mark_sellable(positions: Sequence[Position], as_of_date: date | datetime | str | pd.Timestamp) -> tuple[Position, ...]:
    current_date = parse_date(as_of_date)
    return tuple(replace(item, sellable=item.sellable or item.buy_date < current_date) for item in positions)


def _is_finite_price(value: float | None) -> bool:
    return value is not None and pd.notna(value)


def is_limit_up_open(bar: MarketBar) -> bool:
    return _is_finite_price(bar.limit_up_price) and bar.open >= float(bar.limit_up_price)


def is_limit_down_open(bar: MarketBar) -> bool:
    return _is_finite_price(bar.limit_down_price) and bar.open <= float(bar.limit_down_price)


def _forced_hold_event(order: Order, bar: MarketBar, reason: str, position: Position | None) -> tuple[dict[str, Any], ...]:
    if order.side != "sell" or position is None:
        return ()
    return (
        {
            "type": "forced_hold",
            "symbol": order.symbol,
            "date": bar.date.isoformat(),
            "reason": reason,
            "quantity": min(order.quantity, position.quantity),
        },
    )


def _reject(order: Order, bar: MarketBar, reason: str, position: Position | None = None) -> ExecutionResult:
    return ExecutionResult(
        order=order,
        status="rejected",
        reason=reason,
        quantity=0,
        base_price=None,
        fill_price=None,
        amount=0.0,
        cost=0.0,
        cash_delta=0.0,
        events=_forced_hold_event(order, bar, reason, position),
    )


def match_order(
    order: Order,
    bar: MarketBar,
    cost_config: CostConfig,
    position: Position | None = None,
) -> ExecutionResult:
    if order.quantity <= 0:
        return _reject(order, bar, "non_positive_quantity", position)
    if order.symbol != bar.symbol:
        return _reject(order, bar, "symbol_mismatch", position)
    if bar.is_suspended:
        return _reject(order, bar, "suspended", position)
    if order.side == "buy" and is_limit_up_open(bar):
        return _reject(order, bar, "limit_up_open", position)
    if order.side == "sell":
        if position is None or position.quantity <= 0:
            return _reject(order, bar, "no_position", position)
        if not position.sellable:
            return _reject(order, bar, "t_plus_1", position)
        if is_limit_down_open(bar):
            return _reject(order, bar, "limit_down_open", position)

    quantity = order.quantity if order.side == "buy" else min(order.quantity, position.quantity if position else order.quantity)
    fill_price = slippage_price(float(bar.open), order.side, cost_config)
    amount = fill_price * quantity
    cost = trade_cost(amount, order.side, cost_config)
    cash_delta = -(amount + cost) if order.side == "buy" else amount - cost
    return ExecutionResult(
        order=order,
        status="filled",
        reason=None,
        quantity=quantity,
        base_price=float(bar.open),
        fill_price=fill_price,
        amount=amount,
        cost=cost,
        cash_delta=cash_delta,
    )


def apply_execution(
    positions: Sequence[Position],
    result: ExecutionResult,
    trade_date: date | datetime | str | pd.Timestamp,
) -> tuple[Position, ...]:
    if result.status != "filled":
        return tuple(positions)

    current_date = parse_date(trade_date)
    order = result.order
    updated = list(positions)
    for index, position in enumerate(updated):
        if position.symbol != order.symbol:
            continue
        if order.side == "buy":
            total_qty = position.quantity + result.quantity
            total_value = position.avg_price * position.quantity + (result.fill_price or 0.0) * result.quantity
            updated[index] = Position(
                symbol=position.symbol,
                quantity=total_qty,
                avg_price=total_value / total_qty,
                buy_date=current_date,
                sellable=False,
            )
        else:
            remaining = position.quantity - result.quantity
            if remaining > 0:
                updated[index] = replace(position, quantity=remaining)
            else:
                updated.pop(index)
        return tuple(updated)

    if order.side == "buy":
        updated.append(
            Position(
                symbol=order.symbol,
                quantity=result.quantity,
                avg_price=float(result.fill_price or 0.0),
                buy_date=current_date,
                sellable=False,
            )
        )
    return tuple(updated)
