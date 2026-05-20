from __future__ import annotations

import importlib
from datetime import date
from pathlib import Path

import pandas as pd
import pytest
import yaml

from backtest.constraints import CostConfig, Order


def _cost_config() -> CostConfig:
    path = Path(__file__).resolve().parents[1] / "configs" / "cost.yaml"
    with path.open("r", encoding="utf-8") as fh:
        return CostConfig.from_mapping(yaml.safe_load(fh))


def test_paper_broker_submit_is_idempotent_by_order_id(tmp_path: Path) -> None:
    paper_broker = importlib.import_module("exec.paper_broker")
    frame = pd.DataFrame(
        [
            {
                "date": date(2024, 1, 3),
                "open": 10.0,
                "close": 10.1,
                "limit_up_price": 11.0,
                "limit_down_price": 9.0,
                "is_suspended": False,
            }
        ]
    )
    broker = paper_broker.PaperBroker(
        state_dir=tmp_path,
        account="test",
        trade_date=date(2024, 1, 3),
        market_data={"000001": frame},
        cost_config=_cost_config(),
        initial_cash=10000.0,
    )
    order = Order(symbol="000001", side="buy", quantity=100, submitted_date=date(2024, 1, 2))

    first = broker.submit(order, strategy="test", order_id="stable-order", lot_size=100)
    cash_after_first = broker.cash()
    second = broker.submit(order, strategy="test", order_id="stable-order", lot_size=100)

    assert first == second
    assert broker.cash() == pytest.approx(cash_after_first)
    assert len(broker.executions()) == 1
    assert broker.positions()[0].quantity == 100
