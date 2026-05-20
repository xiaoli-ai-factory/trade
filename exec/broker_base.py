"""Deterministic broker interface for paper/live execution adapters."""

from __future__ import annotations

from abc import ABC, abstractmethod

from backtest.constraints import Order, Position


class BrokerBase(ABC):
    """Minimal broker contract.

    Implementations must be deterministic: no LLM calls, no random choices,
    and no hidden mutable state outside their explicit persisted state.
    """

    @abstractmethod
    def submit(self, order: Order):
        """Submit an order and return the deterministic execution result."""

    @abstractmethod
    def positions(self) -> tuple[Position, ...]:
        """Return current positions."""

    @abstractmethod
    def cash(self) -> float:
        """Return available cash."""

    @abstractmethod
    def nav(self) -> float:
        """Return current marked NAV."""
