"""Persistent deterministic paper broker using shared backtest constraints."""

from __future__ import annotations

import json
import math
from dataclasses import asdict
from datetime import date, datetime
from pathlib import Path
from typing import Any

import pandas as pd

from backtest.constraints import (
    CostConfig,
    ExecutionResult,
    MarketBar,
    Order,
    Position,
    apply_execution,
    mark_sellable,
    match_order,
    slippage_price,
    trade_cost,
)
from .broker_base import BrokerBase


class PaperBroker(BrokerBase):
    """Stateful paper broker.

    State is persisted after every state transition. Re-running the same day is
    idempotent when callers reuse stable order ids.
    """

    def __init__(
        self,
        *,
        state_dir: str | Path,
        account: str,
        trade_date: date,
        market_data: dict[str, pd.DataFrame],
        cost_config: CostConfig,
        initial_cash: float,
        reset: bool = False,
    ):
        self.state_dir = Path(state_dir)
        self.state_dir.mkdir(parents=True, exist_ok=True)
        self.account = account
        self.trade_date = _parse_date(trade_date)
        self.market_data = {symbol: _normalize_frame(frame) for symbol, frame in market_data.items()}
        self.cost_config = cost_config
        self.initial_cash = float(initial_cash)
        self.state_path = self.state_dir / f"{account}.json"
        if reset and self.state_path.exists():
            self.state_path.unlink()
        self.state = self._load_state()
        self._set_positions(mark_sellable(self.positions(), self.trade_date))
        self._save()

    def submit(
        self,
        order: Order,
        *,
        strategy: str = "manual",
        order_id: str | None = None,
        lot_size: int = 1,
    ) -> dict[str, Any]:
        """Submit and immediately match an order at this broker's trade date."""

        stable_id = order_id or _order_id(strategy, self.trade_date, order)
        existing = self._find_execution(stable_id)
        if existing is not None:
            return existing

        positions = self.positions()
        position_before = _position_for_symbol(positions, order.symbol)
        bar = self._market_bar(order.symbol, self.trade_date)
        order_to_match = order
        if order.side == "buy":
            affordable = _affordable_quantity(order.quantity, bar.open, self.cash(), self.cost_config, max(1, int(lot_size)))
            if affordable <= 0:
                record = self._rejected_without_match(order, stable_id, strategy, "insufficient_cash")
                self._save()
                return record
            order_to_match = Order(
                symbol=order.symbol,
                side=order.side,
                quantity=affordable,
                submitted_date=order.submitted_date,
            )
            position_before = _position_for_symbol(positions, order_to_match.symbol)

        result = match_order(
            order_to_match,
            bar,
            self.cost_config,
            position=position_before if order_to_match.side == "sell" else None,
        )
        record = self._record_execution(result, stable_id, strategy)
        if result.status == "filled":
            self._apply_basis(result, position_before)
            self.state["cash"] = float(self.state["cash"]) + result.cash_delta
            self._set_positions(apply_execution(positions, result, self.trade_date))
        self.state["executions"].append(record)
        self.state["events"].extend(result.events)
        self._save()
        return record

    def queue(
        self,
        order: Order,
        *,
        execute_date: date,
        strategy: str,
        order_id: str,
        lot_size: int = 1,
    ) -> dict[str, Any]:
        """Persist a future order without duplicating it on reruns."""

        existing = self._find_pending(order_id) or self._find_execution(order_id)
        if existing is not None:
            return existing
        item = {
            "id": order_id,
            "strategy": strategy,
            "execute_date": _parse_date(execute_date).isoformat(),
            "lot_size": int(lot_size),
            "order": _order_to_json(order),
            "status": "pending",
        }
        self.state["pending_orders"].append(item)
        self._save()
        return item

    def process_pending(self) -> list[dict[str, Any]]:
        """Execute pending orders due on or before ``trade_date`` once."""

        due = []
        remaining = []
        for item in self.state["pending_orders"]:
            if _parse_date(item["execute_date"]) <= self.trade_date:
                due.append(item)
            else:
                remaining.append(item)
        self.state["pending_orders"] = remaining
        self._save()

        executions = []
        for item in sorted(due, key=lambda row: (row["execute_date"], 0 if row["order"]["side"] == "sell" else 1, row["id"])):
            order = _order_from_json(item["order"])
            executions.append(
                self.submit(
                    order,
                    strategy=str(item["strategy"]),
                    order_id=str(item["id"]),
                    lot_size=int(item.get("lot_size", 1)),
                )
            )
        return executions

    def mark_nav(self, on_date: date | None = None) -> float:
        mark_date = self.trade_date if on_date is None else _parse_date(on_date)
        nav = self.cash()
        for position in self.positions():
            close = self._last_close(position.symbol, mark_date)
            if close is not None:
                nav += position.quantity * close
        row = {"date": mark_date.isoformat(), "nav": float(nav), "cash": self.cash()}
        self.state["nav_history"] = [item for item in self.state["nav_history"] if item.get("date") != row["date"]]
        self.state["nav_history"].append(row)
        self.state["nav_history"].sort(key=lambda item: item["date"])
        self._save()
        return float(nav)

    def positions(self) -> tuple[Position, ...]:
        return tuple(_position_from_json(item) for item in self.state.get("positions", []))

    def cash(self) -> float:
        return float(self.state["cash"])

    def nav(self) -> float:
        return self.mark_nav(self.trade_date)

    def trades(self) -> tuple[dict[str, Any], ...]:
        return tuple(self.state.get("trades", ()))

    def executions(self) -> tuple[dict[str, Any], ...]:
        return tuple(self.state.get("executions", ()))

    def pending_orders(self) -> tuple[dict[str, Any], ...]:
        return tuple(self.state.get("pending_orders", ()))

    def _load_state(self) -> dict[str, Any]:
        if not self.state_path.exists():
            return {
                "account": self.account,
                "cash": self.initial_cash,
                "positions": [],
                "basis": {},
                "pending_orders": [],
                "executions": [],
                "events": [],
                "trades": [],
                "nav_history": [],
            }
        with self.state_path.open("r", encoding="utf-8") as fh:
            state = json.load(fh)
        state.setdefault("basis", {})
        state.setdefault("pending_orders", [])
        state.setdefault("executions", [])
        state.setdefault("events", [])
        state.setdefault("trades", [])
        state.setdefault("nav_history", [])
        return state

    def _save(self) -> None:
        tmp = self.state_path.with_suffix(".tmp")
        with tmp.open("w", encoding="utf-8") as fh:
            json.dump(self.state, fh, ensure_ascii=False, indent=2, sort_keys=True)
        tmp.replace(self.state_path)

    def _set_positions(self, positions: tuple[Position, ...]) -> None:
        self.state["positions"] = [_position_to_json(item) for item in positions]

    def _find_pending(self, order_id: str) -> dict[str, Any] | None:
        for item in self.state.get("pending_orders", []):
            if item.get("id") == order_id:
                return item
        return None

    def _find_execution(self, order_id: str) -> dict[str, Any] | None:
        for item in self.state.get("executions", []):
            if item.get("id") == order_id:
                return item
        return None

    def _market_bar(self, symbol: str, trade_date: date) -> MarketBar:
        frame = self.market_data.get(symbol)
        if frame is None or frame.empty:
            return MarketBar(symbol=symbol, date=trade_date, open=0.0, is_suspended=True)
        row = frame[frame["date"] == trade_date]
        if row.empty:
            last = self._last_close(symbol, trade_date)
            return MarketBar(symbol=symbol, date=trade_date, open=float(last or 0.0), is_suspended=True)
        item = row.iloc[-1]
        return MarketBar(
            symbol=symbol,
            date=trade_date,
            open=float(item["open"]),
            limit_up_price=_none_if_nan(item.get("limit_up_price")),
            limit_down_price=_none_if_nan(item.get("limit_down_price")),
            is_suspended=bool(item.get("is_suspended", False)),
        )

    def _last_close(self, symbol: str, as_of_date: date) -> float | None:
        frame = self.market_data.get(symbol)
        if frame is None or frame.empty:
            return None
        rows = frame[frame["date"] <= as_of_date]
        if rows.empty:
            return None
        close = rows.iloc[-1].get("close")
        if pd.isna(close):
            return None
        return float(close)

    def _record_execution(self, result: ExecutionResult, order_id: str, strategy: str) -> dict[str, Any]:
        return {
            "id": order_id,
            "strategy": strategy,
            "date": self.trade_date.isoformat(),
            "symbol": result.order.symbol,
            "side": result.order.side,
            "status": result.status,
            "reason": result.reason,
            "quantity": int(result.quantity),
            "base_price": result.base_price,
            "fill_price": result.fill_price,
            "amount": float(result.amount),
            "cost": float(result.cost),
            "cash_delta": float(result.cash_delta),
            "submitted_date": result.order.submitted_date.isoformat() if result.order.submitted_date else None,
        }

    def _rejected_without_match(self, order: Order, order_id: str, strategy: str, reason: str) -> dict[str, Any]:
        record = {
            "id": order_id,
            "strategy": strategy,
            "date": self.trade_date.isoformat(),
            "symbol": order.symbol,
            "side": order.side,
            "status": "rejected",
            "reason": reason,
            "quantity": 0,
            "base_price": None,
            "fill_price": None,
            "amount": 0.0,
            "cost": 0.0,
            "cash_delta": 0.0,
            "submitted_date": order.submitted_date.isoformat() if order.submitted_date else None,
        }
        self.state["executions"].append(record)
        return record

    def _apply_basis(self, result: ExecutionResult, position_before: Position | None) -> None:
        symbol = result.order.symbol
        basis = self.state["basis"]
        if result.order.side == "buy":
            basis[symbol] = float(basis.get(symbol, 0.0)) + result.amount + result.cost
            return
        if position_before is None or position_before.quantity <= 0:
            return
        existing_basis = float(basis.get(symbol, 0.0))
        sold_quantity = min(result.quantity, position_before.quantity)
        basis_used = existing_basis * (sold_quantity / position_before.quantity) if position_before.quantity else 0.0
        exit_cash = result.amount - result.cost
        self.state["trades"].append(
            {
                "symbol": symbol,
                "entry_date": position_before.buy_date.isoformat(),
                "exit_date": self.trade_date.isoformat(),
                "quantity": int(sold_quantity),
                "pnl": float(exit_cash - basis_used),
                "entry_basis": float(basis_used),
                "exit_cash": float(exit_cash),
            }
        )
        remaining_basis = existing_basis - basis_used
        if position_before.quantity == sold_quantity:
            basis.pop(symbol, None)
        else:
            basis[symbol] = float(remaining_basis)


def _normalize_frame(frame: pd.DataFrame) -> pd.DataFrame:
    out = frame.copy()
    if "date" in out.columns:
        out["date"] = pd.to_datetime(out["date"], errors="coerce").dt.date
        out = out.dropna(subset=["date"]).sort_values("date").reset_index(drop=True)
    return out


def _parse_date(value: date | datetime | str | pd.Timestamp) -> date:
    if isinstance(value, pd.Timestamp):
        return value.date()
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    return pd.Timestamp(str(value)).date()


def _position_for_symbol(positions: tuple[Position, ...], symbol: str) -> Position | None:
    for item in positions:
        if item.symbol == symbol:
            return item
    return None


def _none_if_nan(value: Any) -> float | None:
    if value is None or pd.isna(value):
        return None
    return float(value)


def _position_to_json(position: Position) -> dict[str, Any]:
    item = asdict(position)
    item["buy_date"] = position.buy_date.isoformat()
    return item


def _position_from_json(item: dict[str, Any]) -> Position:
    return Position(
        symbol=str(item["symbol"]),
        quantity=int(item["quantity"]),
        avg_price=float(item["avg_price"]),
        buy_date=_parse_date(item["buy_date"]),
        sellable=bool(item.get("sellable", False)),
    )


def _order_to_json(order: Order) -> dict[str, Any]:
    return {
        "symbol": order.symbol,
        "side": order.side,
        "quantity": int(order.quantity),
        "submitted_date": order.submitted_date.isoformat() if order.submitted_date else None,
    }


def _order_from_json(item: dict[str, Any]) -> Order:
    return Order(
        symbol=str(item["symbol"]),
        side=item["side"],
        quantity=int(item["quantity"]),
        submitted_date=_parse_date(item["submitted_date"]) if item.get("submitted_date") else None,
    )


def _order_id(strategy: str, trade_date: date, order: Order) -> str:
    submitted = order.submitted_date.isoformat() if order.submitted_date else "none"
    return f"{strategy}:{trade_date.isoformat()}:{submitted}:{order.symbol}:{order.side}:{order.quantity}"


def _affordable_quantity(quantity: int, base_price: float, cash: float, cost_config: CostConfig, lot_size: int) -> int:
    if quantity <= 0 or base_price <= 0:
        return 0
    step = max(1, int(lot_size))
    candidate = int(math.floor(quantity / step) * step)
    while candidate > 0:
        fill_price = slippage_price(base_price, "buy", cost_config)
        amount = fill_price * candidate
        if amount + trade_cost(amount, "buy", cost_config) <= cash + 1e-9:
            return candidate
        candidate -= step
    return 0
