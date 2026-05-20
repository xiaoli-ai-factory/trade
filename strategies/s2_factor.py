"""S2 weekly multi-factor stock selection strategy."""

from __future__ import annotations

from datetime import date
from math import floor
from typing import Any

import numpy as np
import pandas as pd

from backtest.constraints import Order, Position
from strategies.base import Strategy


class S2FactorStrategy(Strategy):
    """Generate weekly rebalance orders from point-in-time model scores."""

    def __init__(self, config: dict[str, Any]):
        self.rebalance = str(config["rebalance"])
        self.hold_n = int(config["hold_n"])
        self.factors = tuple(str(item) for item in config["factors"])
        self.model = str(config["model"])
        self.selection_mode = str(config.get("selection_mode", "model"))
        self.universe_top_n = int(config.get("universe_top_n", 300))
        self.benchmark_hold_n = config.get("benchmark_hold_n", self.hold_n)
        self.exclude_small_mv = bool(config.get("exclude_small_mv", False))
        self.random_seed = int(config.get("random_seed", 20260519))
        if self.rebalance != "weekly":
            raise ValueError(f"Unsupported S2 rebalance: {self.rebalance}")
        if self.model != "lightgbm":
            raise ValueError(f"Unsupported S2 model: {self.model}")
        if self.selection_mode not in {"model", "equal_weight", "random"}:
            raise ValueError(f"Unsupported S2 selection mode: {self.selection_mode}")

    def generate_signals(self, as_of_date: date, ctx: dict[str, Any]) -> list[Order]:
        self.assert_context_as_of(as_of_date, ctx)
        rebalance_dates = set(ctx.get("rebalance_dates", ()))
        if as_of_date not in rebalance_dates:
            return []

        rows = _current_predictions(as_of_date, ctx.get("predictions"))
        if rows.empty:
            return []
        rows = rows[pd.to_numeric(rows["liquidity_rank"], errors="coerce") <= self.universe_top_n].copy()
        rows = rows.dropna(subset=["close"])
        rows = rows[pd.to_numeric(rows["close"], errors="coerce") > 0]
        if rows.empty:
            return []
        _assert_pit_listing(rows, as_of_date)

        if self.exclude_small_mv and len(rows) > self.hold_n:
            mv = pd.to_numeric(rows["float_mv"], errors="coerce")
            if mv.notna().sum() >= self.hold_n:
                cutoff = mv.quantile(0.2)
                rows = rows[mv.isna() | (mv > cutoff)].copy()
        if rows.empty:
            return []

        selected = self._select(rows, as_of_date)
        if selected.empty:
            return []

        positions: tuple[Position, ...] = tuple(ctx.get("positions", ()))
        current_qty = {item.symbol: item.quantity for item in positions if item.quantity > 0}
        selected_symbols = set(selected["symbol"].astype(str))
        orders: list[Order] = []

        for symbol, quantity in sorted(current_qty.items()):
            if symbol not in selected_symbols:
                orders.append(Order(symbol=symbol, side="sell", quantity=quantity, submitted_date=as_of_date))

        nav = float(ctx["nav"])
        lot_size = int(ctx.get("lot_size", 100))
        target_value = nav / len(selected)
        for row in selected.sort_values("symbol").itertuples(index=False):
            symbol = str(row.symbol)
            close = _latest_close(symbol, ctx["data"], float(row.close))
            if close <= 0:
                continue
            target_quantity = _floor_to_lot(target_value / close, lot_size)
            diff = target_quantity - current_qty.get(symbol, 0)
            if diff > 0:
                orders.append(Order(symbol=symbol, side="buy", quantity=diff, submitted_date=as_of_date))
            elif diff < 0:
                orders.append(Order(symbol=symbol, side="sell", quantity=abs(diff), submitted_date=as_of_date))
        return sorted(orders, key=lambda item: 0 if item.side == "sell" else 1)

    def _select(self, rows: pd.DataFrame, as_of_date: date) -> pd.DataFrame:
        if self.selection_mode == "model":
            return rows.sort_values(["score", "amount_20"], ascending=[False, False]).head(self.hold_n)
        if self.selection_mode == "equal_weight":
            hold_n = self._benchmark_count(rows)
            return rows.sort_values(["liquidity_rank", "amount_20"], ascending=[True, False]).head(hold_n)
        rng = np.random.default_rng(self.random_seed + as_of_date.toordinal())
        symbols = rows["symbol"].astype(str).to_numpy()
        size = min(self.hold_n, len(symbols))
        chosen = set(rng.choice(symbols, size=size, replace=False).tolist())
        return rows[rows["symbol"].astype(str).isin(chosen)].copy()

    def _benchmark_count(self, rows: pd.DataFrame) -> int:
        if isinstance(self.benchmark_hold_n, str) and self.benchmark_hold_n.lower() == "all":
            return len(rows)
        return max(1, min(int(self.benchmark_hold_n), len(rows)))


def _current_predictions(as_of_date: date, predictions: Any) -> pd.DataFrame:
    if not isinstance(predictions, pd.DataFrame) or predictions.empty:
        return pd.DataFrame()
    rows = predictions.copy()
    dates = pd.to_datetime(rows["as_of_date"], errors="coerce").dt.date
    assert dates.max() <= as_of_date, f"future prediction date detected: {dates.max()} > {as_of_date}"
    return rows[dates == as_of_date].copy()


def _assert_pit_listing(rows: pd.DataFrame, as_of_date: date) -> None:
    if rows.empty or "list_date" not in rows.columns or "delist_date" not in rows.columns:
        return
    as_of_ts = pd.Timestamp(as_of_date)
    list_dates = pd.to_datetime(rows["list_date"], errors="coerce")
    delist_dates = pd.to_datetime(rows["delist_date"], errors="coerce")
    assert list_dates.notna().all(), "S2 candidate missing list_date"
    assert (list_dates <= as_of_ts).all(), f"S2 candidate listed after as_of_date={as_of_date}"
    valid_delist = delist_dates.isna() | (delist_dates > as_of_ts)
    assert valid_delist.all(), f"S2 candidate delisted by as_of_date={as_of_date}"


def _latest_close(symbol: str, data: dict[str, pd.DataFrame], fallback: float) -> float:
    frame = data.get(symbol)
    if isinstance(frame, pd.DataFrame) and not frame.empty:
        close = pd.to_numeric(frame["close"], errors="coerce").iloc[-1]
        if pd.notna(close):
            return float(close)
    return fallback


def _floor_to_lot(quantity: float, lot_size: int) -> int:
    if quantity <= 0:
        return 0
    if lot_size <= 1:
        return int(floor(quantity))
    return int(floor(quantity / lot_size) * lot_size)
