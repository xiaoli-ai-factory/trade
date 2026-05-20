"""S11 defensive composite: S3b MA200 trend gate + S9 inverse-vol weights."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Any, Mapping

import pandas as pd

from backtest.constraints import Order
from strategies.base import Strategy
from strategies.s9_risk_parity import orders_for_target_weights


@dataclass(frozen=True)
class S11Asset:
    symbol: str
    name: str
    weight: float | None = None


@dataclass(frozen=True)
class S11TrendConfig:
    asset: str
    ma_len: int


class S11DefensiveCompositeStrategy(Strategy):
    """Monthly/flip defensive composite with no fitted parameters."""

    def __init__(self, config: dict[str, Any]):
        trend_cfg = config["trend_signal"]
        self.trend = S11TrendConfig(asset=str(trend_cfg["asset"]), ma_len=int(trend_cfg["ma_len"]))
        self.pool_on = tuple(S11Asset(symbol=str(item["code"]), name=str(item["name"])) for item in config["pool_when_trend_on"])
        self.pool_off = tuple(
            S11Asset(symbol=str(item["code"]), name=str(item["name"]), weight=float(item["weight"]))
            for item in config["pool_when_trend_off"]
        )
        self.lookback_vol_days = int(config["lookback_vol_days"])
        self.rebalance = str(config["rebalance"])
        self.rebalance_trigger = tuple(str(item) for item in config.get("rebalance_trigger", ()))

        if self.trend.ma_len <= 1:
            raise ValueError("S11 trend ma_len must be greater than 1")
        if len(self.pool_on) < 2:
            raise ValueError("S11 trend-on pool requires at least two ETF assets")
        if self.lookback_vol_days <= 1:
            raise ValueError("S11 lookback_vol_days must be greater than 1")
        if self.rebalance != "monthly":
            raise ValueError(f"Unsupported S11 rebalance: {self.rebalance}")
        if set(self.rebalance_trigger) != {"monthly", "trend_flip"}:
            raise ValueError("S11 requires monthly + trend_flip triggers")
        off_total = sum(float(item.weight or 0.0) for item in self.pool_off)
        if len(self.pool_off) != 1 or abs(off_total - 1.0) > 1e-9:
            raise ValueError("S11 trend-off pool must be exactly one 100% defensive asset")

    @property
    def symbols(self) -> tuple[str, ...]:
        ordered: list[str] = []
        for symbol in [*(item.symbol for item in self.pool_on), *(item.symbol for item in self.pool_off)]:
            if symbol not in ordered:
                ordered.append(symbol)
        return tuple(ordered)

    @property
    def bond_symbol(self) -> str:
        return self.pool_off[0].symbol

    def trend_on(self, as_of_date: date, ctx: dict[str, Any]) -> bool | None:
        self.assert_context_as_of(as_of_date, ctx)
        return ma_trend_on(ctx["data"].get(self.trend.asset), self.trend.ma_len, as_of_date)

    def trend_flipped(self, as_of_date: date, ctx: dict[str, Any]) -> bool:
        self.assert_context_as_of(as_of_date, ctx)
        return ma_trend_flipped(ctx["data"].get(self.trend.asset), self.trend.ma_len, as_of_date)

    def should_rebalance(self, as_of_date: date, ctx: dict[str, Any]) -> bool:
        month_end_dates = set(ctx.get("month_end_dates", ()))
        monthly = as_of_date in month_end_dates
        flipped = self.trend_flipped(as_of_date, ctx)
        return bool(monthly or flipped)

    def target_weights(self, as_of_date: date, ctx: dict[str, Any]) -> dict[str, float]:
        self.assert_context_as_of(as_of_date, ctx)
        if not self.should_rebalance(as_of_date, ctx):
            return {}

        trend_on = self.trend_on(as_of_date, ctx)
        if trend_on is None:
            return {}
        if not trend_on:
            return {item.symbol: float(item.weight or 0.0) for item in self.pool_off}
        return inverse_vol_weights(
            tuple(item.symbol for item in self.pool_on),
            ctx["data"],
            self.lookback_vol_days,
            as_of_date,
        )

    def generate_signals(self, as_of_date: date, ctx: dict[str, Any]) -> list[Order]:
        weights = self.target_weights(as_of_date, ctx)
        if not weights:
            return []
        return orders_for_target_weights(self.symbols, weights, as_of_date, ctx)


def ma_trend_on(frame: pd.DataFrame | None, ma_len: int, as_of_date: date) -> bool | None:
    rows = _sorted_frame(frame)
    if rows.empty:
        return None
    max_date = pd.to_datetime(rows["date"], errors="coerce").max()
    assert pd.notna(max_date) and max_date.date() <= as_of_date
    if max_date.date() != as_of_date:
        return None
    return _ma_state(rows, ma_len)


def ma_trend_flipped(frame: pd.DataFrame | None, ma_len: int, as_of_date: date) -> bool:
    rows = _sorted_frame(frame)
    if rows.empty:
        return False
    max_date = pd.to_datetime(rows["date"], errors="coerce").max()
    assert pd.notna(max_date) and max_date.date() <= as_of_date
    if max_date.date() != as_of_date or len(rows) <= ma_len:
        return False
    current = _ma_state(rows, ma_len)
    previous = _ma_state(rows.iloc[:-1].copy(), ma_len)
    if current is None or previous is None:
        return False
    return bool(current != previous)


def inverse_vol_weights(
    symbols: tuple[str, ...],
    data: Mapping[str, pd.DataFrame],
    lookback_vol_days: int,
    as_of_date: date,
) -> dict[str, float]:
    inverse_vols: list[tuple[str, float]] = []
    for symbol in symbols:
        frame = _sorted_frame(data.get(symbol))
        if frame.empty:
            return {}
        max_date = pd.to_datetime(frame["date"], errors="coerce").max()
        assert pd.notna(max_date) and max_date.date() <= as_of_date
        if max_date.date() != as_of_date:
            return {}

        close = pd.to_numeric(frame["close"], errors="coerce")
        returns = close.pct_change().dropna()
        if len(returns) < lookback_vol_days:
            return {}
        sigma = returns.tail(lookback_vol_days).std(ddof=1)
        if pd.isna(sigma) or float(sigma) <= 0.0:
            return {}
        inverse_vols.append((symbol, 1.0 / float(sigma)))

    total = sum(value for _symbol, value in inverse_vols)
    if total <= 0.0:
        return {}
    return {symbol: value / total for symbol, value in inverse_vols}


def _ma_state(rows: pd.DataFrame, ma_len: int) -> bool | None:
    if len(rows) < ma_len:
        return None
    close = pd.to_numeric(rows["close"], errors="coerce")
    current_close = close.iloc[-1]
    ma = close.tail(ma_len).mean()
    if pd.isna(current_close) or pd.isna(ma):
        return None
    return bool(float(current_close) > float(ma))


def _sorted_frame(frame: pd.DataFrame | None) -> pd.DataFrame:
    if not isinstance(frame, pd.DataFrame) or frame.empty:
        return pd.DataFrame()
    return frame.sort_values("date").reset_index(drop=True)
