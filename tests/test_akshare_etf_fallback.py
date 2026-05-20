from datetime import date

import pandas as pd

from data.akshare_source import _etf_market_symbol, _normalize_etf_daily_sina


def test_etf_market_symbol_maps_shanghai_fund_codes() -> None:
    assert _etf_market_symbol("510300") == "sh510300"
    assert _etf_market_symbol("511010") == "sh511010"
    assert _etf_market_symbol("518880") == "sh518880"
    assert _etf_market_symbol("159920") == "sz159920"


def test_normalize_etf_daily_sina_matches_daily_contract() -> None:
    raw = pd.DataFrame(
        {
            "date": [date(2026, 5, 15)],
            "open": ["1.00"],
            "high": ["1.10"],
            "low": ["0.90"],
            "close": ["1.05"],
            "volume": ["123400"],
            "amount": ["129570"],
        }
    )

    out = _normalize_etf_daily_sina(raw, "159920")

    assert out.loc[0, "symbol"] == "159920"
    assert out.loc[0, "date"] == date(2026, 5, 15)
    assert out.loc[0, "close"] == 1.05
    assert out.loc[0, "vol"] == 123400
    assert out.loc[0, "amount"] == 129570
