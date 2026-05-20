"""S10 long-only pairs trading strategy.

The Gate1 runner builds point-in-time pair signals from the cached daily panel.
This strategy only converts those pair-level states into executable long-only
stock orders, while keeping all entry/exit thresholds in configuration.
"""

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
class S10PairsTradingConfig:
    rebalance: str
    universe: str
    formation_window_days: int
    trading_window_days: int
    selection_method: str
    num_pairs: int
    entry_z: float
    exit_z: float
    stop_z: float
    pair_capital_pct: float
    allow_short: bool


class S10PairsTradingStrategy(Strategy):
    """Convert pair z-scores into long-only target holdings."""

    def __init__(self, config: dict[str, Any]):
        self.config = S10PairsTradingConfig(
            rebalance=str(config["rebalance"]),
            universe=str(config["universe"]),
            formation_window_days=int(config["formation_window_days"]),
            trading_window_days=int(config["trading_window_days"]),
            selection_method=str(config["selection_method"]),
            num_pairs=int(config["num_pairs"]),
            entry_z=float(config["entry_z"]),
            exit_z=float(config["exit_z"]),
            stop_z=float(config["stop_z"]),
            pair_capital_pct=float(config["pair_capital_pct"]),
            allow_short=bool(config["allow_short"]),
        )
        if self.config.rebalance != "daily_signal":
            raise ValueError(f"Unsupported S10 rebalance: {self.config.rebalance}")
        if self.config.universe != "csi300_constituents":
            raise ValueError(f"Unsupported S10 universe: {self.config.universe}")
        if self.config.selection_method != "engle_granger":
            raise ValueError(f"Unsupported S10 selection_method: {self.config.selection_method}")
        if self.config.allow_short:
            raise ValueError("S10 Gate1 implements the configured allow_short=false long-only approximation")
        if self.config.num_pairs <= 0:
            raise ValueError("S10 num_pairs must be positive")
        if self.config.pair_capital_pct <= 0:
            raise ValueError("S10 pair_capital_pct must be positive")
        if not (0 <= self.config.exit_z < self.config.entry_z < self.config.stop_z):
            raise ValueError("S10 z thresholds must satisfy 0 <= exit_z < entry_z < stop_z")

    def generate_signals(self, as_of_date: date, ctx: dict[str, Any]) -> list[Order]:
        self.assert_context_as_of(as_of_date, ctx)
        pair_signals = _pair_signal_rows(ctx.get("pair_signals"), as_of_date)
        pair_states: dict[str, str | None] = ctx.setdefault("pair_states", {})
        pair_events: list[dict[str, Any]] = ctx.setdefault("pair_events", [])

        for row in pair_signals.sort_values(["formation_date", "pair_rank", "pair_id"]).itertuples(index=False):
            pair_id = str(row.pair_id)
            previous = pair_states.get(pair_id)
            expired = bool(row.expired)
            zscore = float(row.zscore) if pd.notna(row.zscore) else np.nan
            symbol_a = str(row.symbol_a)
            symbol_b = str(row.symbol_b)

            if expired:
                if previous is not None:
                    pair_events.append(_event(as_of_date, pair_id, "window_expired", zscore, previous, None))
                pair_states[pair_id] = None
                continue

            if not np.isfinite(zscore):
                continue

            if previous is not None and abs(zscore) >= self.config.stop_z:
                pair_events.append(_event(as_of_date, pair_id, "stop_z", zscore, previous, None))
                pair_states[pair_id] = None
                continue
            if previous is not None and abs(zscore) <= self.config.exit_z:
                pair_events.append(_event(as_of_date, pair_id, "mean_revert_exit", zscore, previous, None))
                pair_states[pair_id] = None
                continue

            if previous is None and abs(zscore) >= self.config.stop_z:
                pair_events.append(_event(as_of_date, pair_id, "skip_stop_zone", zscore, None, None))
                pair_states[pair_id] = None
                continue

            if previous is None:
                if zscore >= self.config.entry_z:
                    pair_states[pair_id] = symbol_b
                    pair_events.append(_event(as_of_date, pair_id, "entry_long_b", zscore, None, symbol_b))
                elif zscore <= -self.config.entry_z:
                    pair_states[pair_id] = symbol_a
                    pair_events.append(_event(as_of_date, pair_id, "entry_long_a", zscore, None, symbol_a))

        return _orders_to_pair_targets(as_of_date, ctx, pair_states, self.config.pair_capital_pct)


def _event(
    as_of_date: date,
    pair_id: str,
    event_type: str,
    zscore: float,
    previous_symbol: str | None,
    target_symbol: str | None,
) -> dict[str, Any]:
    return {
        "type": event_type,
        "date": as_of_date.isoformat(),
        "pair_id": pair_id,
        "zscore": None if not np.isfinite(zscore) else float(zscore),
        "previous_symbol": previous_symbol,
        "target_symbol": target_symbol,
    }


def _pair_signal_rows(value: Any, as_of_date: date) -> pd.DataFrame:
    if not isinstance(value, pd.DataFrame) or value.empty:
        return pd.DataFrame(
            columns=[
                "date",
                "pair_id",
                "formation_date",
                "pair_rank",
                "symbol_a",
                "symbol_b",
                "zscore",
                "expired",
            ]
        )
    rows = value.copy()
    rows["date"] = pd.to_datetime(rows["date"], errors="coerce").dt.date
    assert rows["date"].max() <= as_of_date, f"S10 pair signal uses future date: {rows['date'].max()} > {as_of_date}"
    return rows[rows["date"] == as_of_date].copy()


def _orders_to_pair_targets(
    as_of_date: date,
    ctx: dict[str, Any],
    pair_states: dict[str, str | None],
    pair_capital_pct: float,
) -> list[Order]:
    prices = ctx.get("prices", {})
    if not isinstance(prices, dict):
        prices = {}
    positions: tuple[Position, ...] = tuple(ctx.get("positions", ()))
    current_qty = {item.symbol: int(item.quantity) for item in positions if item.quantity > 0}
    nav = float(ctx.get("nav", 0.0))
    lot_size = int(ctx.get("lot_size", 100))

    target_values: dict[str, float] = {}
    for target_symbol in pair_states.values():
        if target_symbol is None:
            continue
        target_values[target_symbol] = target_values.get(target_symbol, 0.0) + nav * pair_capital_pct

    orders: list[Order] = []
    for symbol, quantity in sorted(current_qty.items()):
        if symbol not in target_values:
            orders.append(Order(symbol=symbol, side="sell", quantity=quantity, submitted_date=as_of_date))

    for symbol, target_value in sorted(target_values.items()):
        price = _finite_positive_price(prices.get(symbol))
        if price is None:
            continue
        target_qty = _floor_to_lot(target_value / price, lot_size)
        diff = target_qty - current_qty.get(symbol, 0)
        if diff > 0:
            orders.append(Order(symbol=symbol, side="buy", quantity=diff, submitted_date=as_of_date))
        elif diff < 0:
            orders.append(Order(symbol=symbol, side="sell", quantity=abs(diff), submitted_date=as_of_date))

    return sorted(orders, key=lambda item: 0 if item.side == "sell" else 1)


def _finite_positive_price(value: Any) -> float | None:
    try:
        price = float(value)
    except (TypeError, ValueError):
        return None
    if not np.isfinite(price) or price <= 0:
        return None
    return price


def _floor_to_lot(quantity: float, lot_size: int) -> int:
    if quantity <= 0:
        return 0
    if lot_size <= 1:
        return int(floor(quantity))
    return int(floor(quantity / lot_size) * lot_size)
