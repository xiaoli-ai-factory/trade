from __future__ import annotations

from datetime import date

import pandas as pd
import pytest

from strategies.s8_rsi_reversal import S8RSIReversalStrategy, wilder_rsi


def test_wilder_rsi_matches_classic_example() -> None:
    prices = pd.Series(
        [
            44.34,
            44.09,
            44.15,
            43.61,
            44.33,
            44.83,
            45.10,
            45.42,
            45.84,
            46.08,
            45.89,
            46.03,
            45.61,
            46.28,
            46.28,
            46.00,
            46.03,
            46.41,
            46.22,
            45.64,
            46.21,
        ]
    )

    rsi = wilder_rsi(prices, 14)

    assert rsi.iloc[14] == pytest.approx(70.4641, abs=1e-4)
    assert rsi.iloc[15] == pytest.approx(66.2496, abs=1e-4)
    assert rsi.iloc[20] == pytest.approx(62.8807, abs=1e-4)


def test_s8_strategy_rejects_future_daily_data() -> None:
    strategy = S8RSIReversalStrategy(
        {
            "asset": "sh000300",
            "signal_basis": "daily_close",
            "rsi_period": 14,
            "buy_threshold": 30,
            "sell_threshold": 70,
            "rebalance_when": "crossover_only",
        }
    )
    frame = pd.DataFrame(
        {
            "date": pd.date_range("2024-01-01", periods=20, freq="B").date,
            "close": [100 + idx for idx in range(20)],
        }
    )

    with pytest.raises(AssertionError, match="future data detected"):
        strategy.generate_signals(
            date(2024, 1, 10),
            {"data": {"sh000300": frame}, "positions": (), "nav": 1_000_000.0, "lot_size": 100},
        )
