from __future__ import annotations

import json
from datetime import date
from pathlib import Path
from typing import Any

import pandas as pd
import pytest

from paper import runner
from strategies.s12_global_risk_parity import S12GlobalRiskParityStrategy


def _paper_cfg(tmp_path: Path) -> dict[str, Any]:
    return {
        "initial_cash": 10000.0,
        "state_dir": str(tmp_path / "state"),
        "forward_log_path": str(tmp_path / "forward_log.csv"),
        "forward_status_path": str(tmp_path / "forward_status.md"),
        "dashboard_path": str(tmp_path / "dashboard.md"),
        "gate2_min_trading_days": 42,
        "strategies": {
            "s1_tail": {"enabled": False},
            "s3b_trend": {"enabled": False},
            "s12_global_rp": {
                "enabled": True,
                "rebalance": "month_end_or_first_trading_day_of_month",
                "initial_cash": 10000,
                "allow_fractional_lot": False,
            },
        },
        "lot_sizes": {"s1_tail": 100, "s3b_trend": 1, "s12_global_rp": 100},
    }


def _s12_cfg() -> dict[str, Any]:
    return {
        "enabled": True,
        "rebalance": "monthly",
        "pool": [
            {"code": "510300", "name": "A", "class": "a"},
            {"code": "518880", "name": "B", "class": "b"},
        ],
        "lookback_vol_days": 3,
        "weight_method": "inverse_volatility",
        "weight_normalize": True,
        "trend_filter": False,
    }


def _cost_cfg() -> dict[str, Any]:
    return {
        "commission": {"rate": 0.0, "min_per_order": 0.0},
        "stamp_duty": {"sell_rate": 0.0},
        "transfer_fee": {"rate": 0.0},
        "slippage": {"rate": 0.0},
    }


def _frame(symbol: str) -> pd.DataFrame:
    dates = [
        date(2024, 1, 24),
        date(2024, 1, 25),
        date(2024, 1, 26),
        date(2024, 1, 29),
        date(2024, 1, 30),
        date(2024, 1, 31),
    ]
    closes = [10.0, 10.2, 10.1, 10.5, 10.4, 10.8] if symbol == "510300" else [20.0, 19.9, 20.4, 20.2, 20.6, 20.7]
    return pd.DataFrame(
        {
            "symbol": symbol,
            "date": dates,
            "open": closes,
            "high": [value * 1.01 for value in closes],
            "low": [value * 0.99 for value in closes],
            "close": closes,
            "vol": [1000000.0] * len(dates),
            "amount": [10000000.0] * len(dates),
            "limit_up_price": [value * 1.1 for value in closes],
            "limit_down_price": [value * 0.9 for value in closes],
            "is_suspended": [False] * len(dates),
            "source": ["sina_fund_etf_hist_sina"] * len(dates),
        }
    )


def _install_fakes(monkeypatch: pytest.MonkeyPatch, tmp_path: Path, *, data_available: bool = True) -> dict[str, Any]:
    s12_cfg = _s12_cfg()
    paper_cfg = _paper_cfg(tmp_path)

    def fake_load_yaml(name: str) -> dict[str, Any]:
        if name == "paper.yaml":
            return paper_cfg
        if name == "cost.yaml":
            return _cost_cfg()
        if name == "strategy_addon.yaml":
            return {"s12_global_risk_parity": s12_cfg}
        if name == "strategy.yaml":
            return {"s1_tail": {}, "s3b_trend": {"asset": "sh000300"}}
        return {}

    trade_dates = [
        date(2024, 1, 24),
        date(2024, 1, 25),
        date(2024, 1, 26),
        date(2024, 1, 29),
        date(2024, 1, 30),
        date(2024, 1, 31),
        date(2024, 2, 1),
    ]

    def fake_trade_dates(start: date, end: date, refresh: bool = False) -> pd.Series:
        return pd.Series([item for item in trade_dates if start <= item <= end])

    def fake_get_etf_daily_sina(symbol: str, start: date, end: date, refresh: bool = False) -> pd.DataFrame:
        if not data_available:
            raise RuntimeError("sina unavailable")
        frame = _frame(symbol)
        return frame[(frame["date"] >= start) & (frame["date"] <= end)].copy()

    monkeypatch.setattr(runner, "_load_yaml", fake_load_yaml)
    monkeypatch.setattr(runner, "_trade_dates", fake_trade_dates)
    monkeypatch.setattr(runner, "get_etf_daily_sina", fake_get_etf_daily_sina)
    return {"paper_cfg": paper_cfg, "s12_cfg": s12_cfg}


def test_s12_forward_non_month_end_does_not_rebalance(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    cfg = _install_fakes(monkeypatch, tmp_path)

    summary = runner.run_forward_s12(date(2024, 1, 30))

    assert summary["s12_note"] == "not_month_end"
    assert summary["target_weights"] == {}
    assert summary["s12_orders"] == []
    state = json.loads((Path(cfg["paper_cfg"]["state_dir"]) / "s12_forward.json").read_text(encoding="utf-8"))
    assert state["pending_orders"] == []
    assert state["nav_history"] == [{"cash": 10000.0, "date": "2024-01-30", "nav": 10000.0}]


def test_s12_forward_month_end_weights_match_strategy(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    cfg = _install_fakes(monkeypatch, tmp_path)
    as_of = date(2024, 1, 31)

    summary = runner.run_forward_s12(as_of)

    strategy = S12GlobalRiskParityStrategy(cfg["s12_cfg"])
    data = {symbol: _frame(symbol) for symbol in ("510300", "518880")}
    expected = strategy.target_weights(
        as_of,
        {"data": data, "positions": (), "cash": 10000.0, "nav": 10000.0, "lot_size": 100, "month_end_dates": {as_of}},
    )
    assert summary["s12_note"] == "ok"
    assert summary["target_weights"] == pytest.approx(expected)
    state = json.loads((Path(cfg["paper_cfg"]["state_dir"]) / "s12_forward.json").read_text(encoding="utf-8"))
    assert state["pending_orders"]
    assert {item["execute_date"] for item in state["pending_orders"]} == {"2024-02-01"}


def test_s12_forward_skips_when_etf_fallback_unavailable(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    cfg = _install_fakes(monkeypatch, tmp_path, data_available=False)

    summary = runner.run_forward_s12(date(2024, 1, 31))

    assert summary["s12_note"].startswith("s12_skipped_missing_as_of_bar")
    assert summary["target_weights"] == {}
    assert summary["s12_orders"] == []
    state = json.loads((Path(cfg["paper_cfg"]["state_dir"]) / "s12_forward.json").read_text(encoding="utf-8"))
    assert state["pending_orders"] == []
    assert all(item["error"] == "RuntimeError: sina unavailable" for item in summary["s12_data_status"].values())
