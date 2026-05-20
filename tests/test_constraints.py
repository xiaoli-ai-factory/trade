from __future__ import annotations

from datetime import date
from pathlib import Path

import pytest
import yaml

from backtest.constraints import (
    CostConfig,
    MarketBar,
    Order,
    Position,
    match_order,
    slippage_price,
    trade_cost,
)


def _cost_config() -> CostConfig:
    path = Path(__file__).resolve().parents[1] / "configs" / "cost.yaml"
    with path.open("r", encoding="utf-8") as fh:
        return CostConfig.from_mapping(yaml.safe_load(fh))


def test_next_day_limit_down_sell_rejected_and_forced_hold() -> None:
    cfg = _cost_config()
    order = Order(symbol="000001", side="sell", quantity=100, submitted_date=date(2024, 1, 3))
    position = Position(symbol="000001", quantity=100, avg_price=10.0, buy_date=date(2024, 1, 2), sellable=True)
    bar = MarketBar(
        symbol="000001",
        date=date(2024, 1, 3),
        open=9.0,
        limit_up_price=11.0,
        limit_down_price=9.0,
        is_suspended=False,
    )

    result = match_order(order, bar, cfg, position=position)

    assert result.status == "rejected"
    assert result.reason == "limit_down_open"
    assert result.events == (
        {
            "type": "forced_hold",
            "symbol": "000001",
            "date": "2024-01-03",
            "reason": "limit_down_open",
            "quantity": 100,
        },
    )


def test_suspended_day_rejects_buy_and_sell() -> None:
    cfg = _cost_config()
    bar = MarketBar(
        symbol="000001",
        date=date(2024, 1, 3),
        open=10.0,
        limit_up_price=11.0,
        limit_down_price=9.0,
        is_suspended=True,
    )
    buy = Order(symbol="000001", side="buy", quantity=100, submitted_date=date(2024, 1, 3))
    sell = Order(symbol="000001", side="sell", quantity=100, submitted_date=date(2024, 1, 3))
    position = Position(symbol="000001", quantity=100, avg_price=10.0, buy_date=date(2024, 1, 2), sellable=True)

    buy_result = match_order(buy, bar, cfg)
    sell_result = match_order(sell, bar, cfg, position=position)

    assert buy_result.status == "rejected"
    assert buy_result.reason == "suspended"
    assert sell_result.status == "rejected"
    assert sell_result.reason == "suspended"
    assert sell_result.events[0]["type"] == "forced_hold"


def test_next_day_limit_up_buy_rejected() -> None:
    cfg = _cost_config()
    order = Order(symbol="000001", side="buy", quantity=100, submitted_date=date(2024, 1, 3))
    bar = MarketBar(
        symbol="000001",
        date=date(2024, 1, 3),
        open=11.0,
        limit_up_price=11.0,
        limit_down_price=9.0,
        is_suspended=False,
    )

    result = match_order(order, bar, cfg)

    assert result.status == "rejected"
    assert result.reason == "limit_up_open"


def test_cost_formula_min_commission_stamp_tax_and_roundtrip_to_cent() -> None:
    cfg = _cost_config()
    amount = 3000.0

    buy_cost = trade_cost(amount, "buy", cfg)
    sell_cost = trade_cost(amount, "sell", cfg)

    assert buy_cost == pytest.approx(5.03, abs=0.005)
    assert sell_cost == pytest.approx(8.03, abs=0.005)
    assert buy_cost + sell_cost == pytest.approx(13.06, abs=0.005)
    assert slippage_price(10.0, "buy", cfg) == pytest.approx(10.02)
    assert slippage_price(10.0, "sell", cfg) == pytest.approx(9.98)
