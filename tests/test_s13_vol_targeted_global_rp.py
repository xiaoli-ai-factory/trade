from __future__ import annotations

from datetime import date
from math import sqrt

import numpy as np
import pandas as pd
import pytest

from strategies.s13_vol_targeted_global_rp import TRADING_DAYS_PER_YEAR, S13VolTargetedGlobalRPStrategy


def _config() -> dict[str, object]:
    return {
        "base_pool": "s12_global_risk_parity",
        "pool": [{"code": f"T{idx}", "name": f"Test {idx}", "class": "test"} for idx in range(7)],
        "rebalance": "monthly",
        "lookback_vol_days": 3,
        "weight_method": "inverse_volatility",
        "target_portfolio_vol_annual": 0.08,
        "leverage_max": 1.5,
        "leverage_min": 0.3,
        "rebal_when_vol_drift_pct": 0.25,
    }


def _data() -> dict[str, pd.DataFrame]:
    dates = pd.date_range("2024-01-01", periods=6, freq="B").date
    base_returns = np.array([0.002, -0.001, 0.003, -0.002, 0.001], dtype=float)
    frames: dict[str, pd.DataFrame] = {}
    for idx in range(7):
        returns = base_returns * (idx + 1)
        close = [100.0]
        for item in returns:
            close.append(close[-1] * (1.0 + item))
        frames[f"T{idx}"] = pd.DataFrame({"date": dates, "close": close})
    return frames


def test_s13_profile_uses_inverse_vol_covariance_and_clipped_leverage() -> None:
    strategy = S13VolTargetedGlobalRPStrategy(_config())
    as_of = date(2024, 1, 8)
    ctx = {"data": _data(), "positions": (), "nav": 1_000_000.0, "lot_size": 100, "month_end_dates": {as_of}}

    profile = strategy.target_profile(as_of, ctx)

    assert profile is not None
    returns = pd.concat(
        [
            pd.Series(frame["close"]).pct_change().dropna().rename(symbol)
            for symbol, frame in ctx["data"].items()
        ],
        axis=1,
    ).tail(3)
    inverse_vol = 1.0 / returns.std(ddof=1)
    expected_weights = inverse_vol / inverse_vol.sum()
    expected_vol = sqrt(float(expected_weights.to_numpy().T @ returns.cov().to_numpy() @ expected_weights.to_numpy())) * sqrt(
        TRADING_DAYS_PER_YEAR
    )
    expected_leverage = float(np.clip(0.08 / expected_vol, 0.3, 1.5))

    assert profile.portfolio_vol_annual == pytest.approx(expected_vol)
    assert profile.leverage == pytest.approx(expected_leverage)
    assert sum(profile.base_weights.values()) == pytest.approx(1.0)
    assert sum(profile.final_weights.values()) == pytest.approx(profile.leverage)
    assert profile.cash_weight == pytest.approx(1.0 - profile.leverage)


def test_s13_rejects_future_data() -> None:
    strategy = S13VolTargetedGlobalRPStrategy(_config())

    with pytest.raises(AssertionError, match="future data detected"):
        strategy.target_profile(
            date(2024, 1, 5),
            {"data": _data(), "positions": (), "nav": 1_000_000.0, "lot_size": 100, "month_end_dates": {date(2024, 1, 5)}},
        )
