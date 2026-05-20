"""S13 vol-targeted cross-asset ETF risk parity strategy."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from math import sqrt
from typing import Any

import numpy as np
import pandas as pd

from backtest.constraints import Order
from strategies.base import Strategy
from strategies.s9_risk_parity import orders_for_target_weights


TRADING_DAYS_PER_YEAR = 252


@dataclass(frozen=True)
class S13Asset:
    symbol: str
    name: str
    asset_class: str = ""


@dataclass(frozen=True)
class S13TargetProfile:
    as_of_date: date
    base_weights: dict[str, float]
    final_weights: dict[str, float]
    portfolio_vol_annual: float
    leverage_target: float
    leverage: float
    cash_weight: float


class S13VolTargetedGlobalRPStrategy(Strategy):
    """Monthly S12 inverse-vol risk parity scaled to a portfolio volatility target."""

    def __init__(self, config: dict[str, Any]):
        pool = config.get("pool", ())
        self.assets = tuple(
            S13Asset(
                symbol=str(item["code"]),
                name=str(item["name"]),
                asset_class=str(item.get("class", "")),
            )
            for item in pool
        )
        self.base_pool = str(config["base_pool"])
        self.lookback_vol_days = int(config["lookback_vol_days"])
        self.rebalance = str(config["rebalance"])
        self.weight_method = str(config["weight_method"])
        self.target_portfolio_vol_annual = float(config["target_portfolio_vol_annual"])
        self.leverage_max = float(config["leverage_max"])
        self.leverage_min = float(config["leverage_min"])
        self.rebal_when_vol_drift_pct = float(config["rebal_when_vol_drift_pct"])

        if self.base_pool != "s12_global_risk_parity":
            raise ValueError(f"S13 requires base_pool=s12_global_risk_parity, got {self.base_pool}")
        if len(self.assets) != 7:
            raise ValueError("S13 requires the full 7 ETF S12 pool")
        if self.lookback_vol_days <= 1:
            raise ValueError("S13 lookback_vol_days must be greater than 1")
        if self.rebalance != "monthly":
            raise ValueError(f"Unsupported S13 rebalance: {self.rebalance}")
        if self.weight_method != "inverse_volatility":
            raise ValueError(f"Unsupported S13 weight_method: {self.weight_method}")
        if self.target_portfolio_vol_annual <= 0.0:
            raise ValueError("S13 target_portfolio_vol_annual must be positive")
        if self.leverage_min <= 0.0 or self.leverage_max < self.leverage_min:
            raise ValueError("S13 leverage bounds are invalid")
        if self.rebal_when_vol_drift_pct <= 0.0:
            raise ValueError("S13 rebal_when_vol_drift_pct must be positive")

    @property
    def symbols(self) -> tuple[str, ...]:
        return tuple(item.symbol for item in self.assets)

    def target_profile(self, as_of_date: date, ctx: dict[str, Any]) -> S13TargetProfile | None:
        self.assert_context_as_of(as_of_date, ctx)
        if as_of_date not in set(ctx.get("month_end_dates", ())):
            return None

        returns = _aligned_lookback_returns(ctx["data"], self.symbols, self.lookback_vol_days, as_of_date)
        if returns is None:
            return None

        sigma = returns.std(ddof=1)
        if sigma.isna().any() or (sigma <= 0.0).any():
            return None

        inverse_vol = 1.0 / sigma.astype(float)
        total_inverse_vol = float(inverse_vol.sum())
        if total_inverse_vol <= 0.0:
            return None

        base_weights_series = inverse_vol / total_inverse_vol
        covariance = returns.cov()
        raw_portfolio_var = float(base_weights_series.to_numpy().T @ covariance.to_numpy() @ base_weights_series.to_numpy())
        if not np.isfinite(raw_portfolio_var) or raw_portfolio_var <= 0.0:
            return None

        portfolio_vol_annual = sqrt(raw_portfolio_var) * sqrt(TRADING_DAYS_PER_YEAR)
        if not np.isfinite(portfolio_vol_annual) or portfolio_vol_annual <= 0.0:
            return None

        leverage_target = self.target_portfolio_vol_annual / portfolio_vol_annual
        leverage = float(np.clip(leverage_target, self.leverage_min, self.leverage_max))
        base_weights = {symbol: float(base_weights_series.loc[symbol]) for symbol in self.symbols}
        final_weights = {symbol: weight * leverage for symbol, weight in base_weights.items()}
        return S13TargetProfile(
            as_of_date=as_of_date,
            base_weights=base_weights,
            final_weights=final_weights,
            portfolio_vol_annual=float(portfolio_vol_annual),
            leverage_target=float(leverage_target),
            leverage=leverage,
            cash_weight=1.0 - leverage,
        )

    def target_weights(self, as_of_date: date, ctx: dict[str, Any]) -> dict[str, float]:
        profile = self.target_profile(as_of_date, ctx)
        return {} if profile is None else profile.final_weights

    def generate_signals(self, as_of_date: date, ctx: dict[str, Any]) -> list[Order]:
        weights = self.target_weights(as_of_date, ctx)
        if not weights:
            return []
        return orders_for_target_weights(self.symbols, weights, as_of_date, ctx)


def _aligned_lookback_returns(
    data: dict[str, pd.DataFrame],
    symbols: tuple[str, ...],
    lookback_days: int,
    as_of_date: date,
) -> pd.DataFrame | None:
    series: list[pd.Series] = []
    for symbol in symbols:
        frame = _sorted_frame(data.get(symbol))
        if frame.empty:
            return None
        max_date = pd.to_datetime(frame["date"], errors="coerce").max()
        assert pd.notna(max_date) and max_date.date() <= as_of_date
        if max_date.date() != as_of_date:
            return None

        close = pd.to_numeric(frame["close"], errors="coerce")
        close.index = pd.to_datetime(frame["date"], errors="coerce")
        returns = close.pct_change().dropna()
        returns.name = symbol
        series.append(returns)

    aligned = pd.concat(series, axis=1, join="inner").dropna()
    if aligned.empty:
        return None
    max_return_date = pd.to_datetime(aligned.index, errors="coerce").max()
    assert pd.notna(max_return_date) and max_return_date.date() <= as_of_date
    if len(aligned) < lookback_days:
        return None
    return aligned.tail(lookback_days)


def _sorted_frame(frame: pd.DataFrame | None) -> pd.DataFrame:
    if not isinstance(frame, pd.DataFrame) or frame.empty:
        return pd.DataFrame()
    return frame.sort_values("date").reset_index(drop=True)
