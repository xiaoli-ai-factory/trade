"""S3b full-history low-frequency significance retest."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Any

import pandas as pd

from backtest.constraints import CostConfig, Order, Position
from backtest.engine import (
    CONFIG_DIR,
    LOT_SIZE,
    REPORT_DIR,
    _fmt_float,
    _fmt_pct,
    _load_yaml,
    _parse_date,
    _run_signal_backtest,
    summarize_run,
)
from data.akshare_source import get_index_daily
from strategies.s3b_trend import S3BTrendStrategy


DEPTH_START = "1990-01-01"
OOS_SUBCYCLES = (
    ("2018_bear", "2018-01-01", "2018-12-31"),
    ("2019_2021_bull", "2019-01-01", "2021-12-31"),
    ("2022_bear", "2022-01-01", "2022-12-31"),
    ("2023_2024_range", "2023-01-01", "2024-09-30"),
    ("2024_2026_recent", "2024-10-01", "2026-05-15"),
)


@dataclass(frozen=True)
class SeriesDepth:
    symbol: str
    rows: int
    earliest: date | None
    latest: date | None
    error: str | None = None


@dataclass(frozen=True)
class CycleResult:
    name: str
    start: date
    end: date
    s3b: dict[str, float]
    buy_hold: dict[str, float]
    passed: bool | None
    criterion: str


def _single_asset_buy_hold_signal(asset: str):
    def _signal(as_of_date: date, ctx: dict[str, Any]) -> list[Order]:
        positions: tuple[Position, ...] = tuple(ctx.get("positions", ()))
        if any(item.symbol == asset and item.quantity > 0 for item in positions):
            return []
        frame = ctx["data"].get(asset)
        if frame is None or frame.empty:
            return []
        close = float(frame.iloc[-1]["close"])
        quantity = int((float(ctx["nav"]) / close) // LOT_SIZE * LOT_SIZE)
        if quantity <= 0:
            return []
        return [Order(symbol=asset, side="buy", quantity=quantity, submitted_date=as_of_date)]

    return _signal


def probe_index_depth(symbol: str) -> SeriesDepth:
    try:
        df = get_index_daily(symbol, start=DEPTH_START, end="2026-05-15", refresh=False)
        if df.empty:
            return SeriesDepth(symbol=symbol, rows=0, earliest=None, latest=None, error=None)
        dates = pd.to_datetime(df["date"], errors="coerce").dt.date
        return SeriesDepth(symbol=symbol, rows=len(df), earliest=min(dates), latest=max(dates))
    except Exception as exc:
        return SeriesDepth(symbol=symbol, rows=0, earliest=None, latest=None, error=f"{type(exc).__name__}: {exc}")


def _load_series_data(symbol: str, end: date) -> pd.DataFrame:
    df = get_index_daily(symbol, start=DEPTH_START, end=end, refresh=False)
    if df.empty:
        raise RuntimeError(f"No index data for {symbol}")
    df = df.copy()
    df["date"] = pd.to_datetime(df["date"], errors="coerce").dt.date
    return df.dropna(subset=["date"]).sort_values("date").reset_index(drop=True)


def _run_pair(symbol: str, name: str, start: date, end: date, data: dict[str, pd.DataFrame], ma_len: int):
    cfg = _load_yaml("strategy.yaml")["s3b_trend"].copy()
    cfg["asset"] = symbol
    cfg["ma_len"] = ma_len
    strategy = S3BTrendStrategy(cfg)
    cost_cfg = CostConfig.from_mapping(_load_yaml("cost.yaml"))
    s3b = _run_signal_backtest(name, "full_history", start, end, data, strategy.generate_signals, cost_cfg)
    buy_hold = _run_signal_backtest(
        f"{name}_buy_hold",
        "full_history",
        start,
        end,
        data,
        _single_asset_buy_hold_signal(symbol),
        cost_cfg,
    )
    return summarize_run(s3b), summarize_run(buy_hold)


def _cycle_result(
    symbol: str,
    name: str,
    start: date,
    end: date,
    data: dict[str, pd.DataFrame],
    ma_len: int,
    kind: str,
    max_drawdown: float,
) -> CycleResult:
    s3b, buy_hold = _run_pair(symbol, f"{symbol}_{name}", start, end, data, ma_len)
    if kind == "bull":
        passed = s3b["return"] > 0.0 and s3b["max_drawdown"] <= max_drawdown
        criterion = "return>0 and DD<=20%"
    elif kind == "bear":
        passed = s3b["max_drawdown"] < buy_hold["max_drawdown"] and s3b["max_drawdown"] <= max_drawdown
        criterion = "S3b DD < buy_hold DD and S3b DD<=20%"
    else:
        passed = None
        criterion = "listed only; OOS overall is judged separately"
    return CycleResult(name=name, start=start, end=end, s3b=s3b, buy_hold=buy_hold, passed=passed, criterion=criterion)


def _overall_result(symbol: str, label: str, start: date, end: date, data: dict[str, pd.DataFrame], ma_len: int):
    s3b, buy_hold = _run_pair(symbol, label, start, end, data, ma_len)
    return s3b, buy_hold


def _series_report(symbol: str, label: str, data: dict[str, pd.DataFrame]) -> dict[str, Any]:
    backtest_cfg = _load_yaml("backtest.yaml")
    strategy_cfg = _load_yaml("strategy.yaml")["s3b_trend"]
    full_cfg = backtest_cfg["full_history"]
    gate1 = backtest_cfg["gate1"]
    ma_len = int(strategy_cfg["ma_len"])
    max_dd = float(gate1["max_drawdown_max"])

    in_start = _parse_date(full_cfg["in_sample"]["start"])
    in_end = _parse_date(full_cfg["in_sample"]["end"])
    oos_start = _parse_date(full_cfg["oos"]["start"])
    oos_end = _parse_date(full_cfg["oos"]["end"])

    bull_cycles = [
        _cycle_result(symbol, f"bull_{idx}", _parse_date(left), _parse_date(right), data, ma_len, "bull", max_dd)
        for idx, (left, right) in enumerate(full_cfg["bull_cycles"], start=1)
    ]
    bear_cycles = [
        _cycle_result(symbol, f"bear_{idx}", _parse_date(left), _parse_date(right), data, ma_len, "bear", max_dd)
        for idx, (left, right) in enumerate(full_cfg["bear_cycles"], start=1)
    ]
    oos_cycles = [
        _cycle_result(symbol, name, _parse_date(left), _parse_date(right), data, ma_len, "oos", max_dd)
        for name, left, right in OOS_SUBCYCLES
    ]
    in_s3b, in_bh = _overall_result(symbol, f"{label}_in_sample", in_start, in_end, data, ma_len)
    oos_s3b, oos_bh = _overall_result(symbol, f"{label}_oos", oos_start, oos_end, data, ma_len)
    oos_pass = (
        oos_s3b["expectancy"] > float(gate1["expectancy_after_cost_gt"])
        and oos_s3b["profit_factor"] >= float(gate1["profit_factor_min"])
        and oos_s3b["max_drawdown"] <= max_dd
    )
    bull_pass = all(item.passed for item in bull_cycles)
    bear_pass = all(item.passed for item in bear_cycles)
    return {
        "symbol": symbol,
        "label": label,
        "in_sample": (in_s3b, in_bh),
        "oos": (oos_s3b, oos_bh, oos_pass),
        "bull_cycles": bull_cycles,
        "bear_cycles": bear_cycles,
        "oos_cycles": oos_cycles,
        "passed": bool(bull_pass and bear_pass and oos_pass),
        "bull_pass": bool(bull_pass),
        "bear_pass": bool(bear_pass),
    }


def _sensitivity_rows(symbol: str, data: dict[str, pd.DataFrame]) -> list[tuple[int, dict[str, float], dict[str, float]]]:
    backtest_cfg = _load_yaml("backtest.yaml")
    strategy_cfg = _load_yaml("strategy.yaml")["s3b_trend"]
    start = _parse_date(backtest_cfg["full_history"]["in_sample"]["start"])
    end = _parse_date(backtest_cfg["full_history"]["in_sample"]["end"])
    rows = []
    for ma_len in strategy_cfg["ma_sensitivity"]:
        s3b, buy_hold = _run_pair(symbol, f"{symbol}_ma_{ma_len}", start, end, data, int(ma_len))
        rows.append((int(ma_len), s3b, buy_hold))
    return rows


def _metric_table(cycles: list[CycleResult], include_pass: bool = True) -> str:
    header = "| cycle | start | end | S3b_return | S3b_DD | S3b_trades | BH_return | BH_DD | pass | criterion |"
    lines = [header, "|---|---:|---:|---:|---:|---:|---:|---:|---|---|"]
    for item in cycles:
        passed = "N/A" if item.passed is None else "PASS" if item.passed else "FAIL"
        if not include_pass:
            passed = "N/A"
        lines.append(
            f"| {item.name} | {item.start} | {item.end} | {_fmt_pct(item.s3b['return'])} | "
            f"{_fmt_pct(item.s3b['max_drawdown'])} | {int(item.s3b['trades'])} | "
            f"{_fmt_pct(item.buy_hold['return'])} | {_fmt_pct(item.buy_hold['max_drawdown'])} | {passed} | {item.criterion} |"
        )
    return "\n".join(lines)


def _overall_table(result: dict[str, Any]) -> str:
    in_s3b, in_bh = result["in_sample"]
    oos_s3b, oos_bh, oos_pass = result["oos"]
    rows = [("in_sample", in_s3b, in_bh, None), ("oos", oos_s3b, oos_bh, oos_pass)]
    lines = [
        "| span | S3b_return | S3b_DD | S3b_trades | S3b_expectancy | S3b_PF | S3b_win_rate | BH_return | BH_DD | pass |",
        "|---|---:|---:|---:|---:|---:|---:|---:|---:|---|",
    ]
    for name, s3b, bh, passed in rows:
        pass_text = "N/A" if passed is None else "PASS" if passed else "FAIL"
        lines.append(
            f"| {name} | {_fmt_pct(s3b['return'])} | {_fmt_pct(s3b['max_drawdown'])} | {int(s3b['trades'])} | "
            f"{s3b['expectancy']:.2f} | {_fmt_float(s3b['profit_factor'])} | {_fmt_pct(s3b['win_rate'])} | "
            f"{_fmt_pct(bh['return'])} | {_fmt_pct(bh['max_drawdown'])} | {pass_text} |"
        )
    return "\n".join(lines)


def _sensitivity_table(main_symbol: str, confirm_symbol: str, datasets: dict[str, dict[str, pd.DataFrame]]) -> str:
    lines = [
        "| series | ma_len | in_sample_return | in_sample_DD | trades | expectancy | PF | win_rate | BH_return | BH_DD |",
        "|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for symbol, label in ((main_symbol, "main"), (confirm_symbol, "confirm")):
        for ma_len, s3b, bh in _sensitivity_rows(symbol, datasets[symbol]):
            lines.append(
                f"| {label}/{symbol} | {ma_len} | {_fmt_pct(s3b['return'])} | {_fmt_pct(s3b['max_drawdown'])} | "
                f"{int(s3b['trades'])} | {s3b['expectancy']:.2f} | {_fmt_float(s3b['profit_factor'])} | "
                f"{_fmt_pct(s3b['win_rate'])} | {_fmt_pct(bh['return'])} | {_fmt_pct(bh['max_drawdown'])} |"
            )
    return "\n".join(lines)


def render_report(depths: list[SeriesDepth], main_result: dict[str, Any], confirm_result: dict[str, Any], datasets: dict[str, dict[str, pd.DataFrame]]) -> str:
    final_pass = bool(main_result["passed"] and confirm_result["passed"])
    main_symbol = main_result["symbol"]
    confirm_symbol = confirm_result["symbol"]
    lines = [
        "# S3b Full-History Low-Frequency Significance Report",
        "",
        "规则：MA200，D 收盘后 close>MA 则 D+1 持有，否则现金；ma_len 未调，OOS 未用于调参。",
        "",
        "## 数据深度实证",
        "| symbol | rows | earliest | latest | error |",
        "|---|---:|---:|---:|---|",
    ]
    for item in depths:
        lines.append(f"| {item.symbol} | {item.rows} | {item.earliest or 'NA'} | {item.latest or 'NA'} | {item.error or ''} |")
    lines.extend(
        [
            "",
            f"主证据序列：{main_symbol}；可投资确认序列：{confirm_symbol}。两者均覆盖 backtest.yaml full_history 的 2005-04-08 起点，未调整 in_sample 起点。",
            "",
            "## 对照组 ratio 表",
            "### 主证据序列整体",
            _overall_table(main_result),
            "",
            "### 可投资确认序列整体",
            _overall_table(confirm_result),
            "",
            "### 主证据 bull cycles",
            _metric_table(main_result["bull_cycles"]),
            "",
            "### 主证据 bear cycles",
            _metric_table(main_result["bear_cycles"]),
            "",
            "### 主证据 OOS 子周期",
            _metric_table(main_result["oos_cycles"], include_pass=False),
            "",
            "### 可投资确认 bull cycles",
            _metric_table(confirm_result["bull_cycles"]),
            "",
            "### 可投资确认 bear cycles",
            _metric_table(confirm_result["bear_cycles"]),
            "",
            "### 可投资确认 OOS 子周期",
            _metric_table(confirm_result["oos_cycles"], include_pass=False),
            "",
            "## 反假设列表",
            "- 趋势=牛市 beta：用每个独立 bear cycle 的 S3b vs 买入持有回撤比较证伪；若任何熊市 S3b 回撤不低于买入持有或绝对回撤>20%，该机制判 FAIL。",
            "- ma_len 过拟合：以下敏感性表只用 in_sample，不含 OOS，不用于选择参数。",
            _sensitivity_table(main_symbol, confirm_symbol, datasets),
            "- 上证综指不可直接交易：sh000001 只作机制主证据；可投资确认必须看 sh000300。二者若分歧，最终不能只凭不可交易指数通过。",
            "- ETF/指数一字板约束乐观偏差：指数 limit_up/down 为 NaN，constraints 不触发一字板拒单；偏差方向是略乐观。",
            "",
            "## flag/参数调查记录",
            "- 未调 ma_len，默认仍为 200。",
            "- 未触碰 OOS 调参；OOS 只用于最终裁决。",
            "- 未为提高低频交易数而改变换仓频率。",
            "",
            "## low_freq_significance 判定表",
            "| series | bull_cycles_all_pass | bear_cycles_all_pass | oos_overall_pass | final |",
            "|---|---|---|---|---|",
            f"| main/{main_symbol} | {'PASS' if main_result['bull_pass'] else 'FAIL'} | {'PASS' if main_result['bear_pass'] else 'FAIL'} | {'PASS' if main_result['oos'][2] else 'FAIL'} | {'PASS' if main_result['passed'] else 'FAIL'} |",
            f"| confirm/{confirm_symbol} | {'PASS' if confirm_result['bull_pass'] else 'FAIL'} | {'PASS' if confirm_result['bear_pass'] else 'FAIL'} | {'PASS' if confirm_result['oos'][2] else 'FAIL'} | {'PASS' if confirm_result['passed'] else 'FAIL'} |",
            f"| TOTAL | - | - | - | {'PASS' if final_pass else 'FAIL'} |",
            "",
            f"最终判定：{'PASS' if final_pass else 'FAIL'}",
        ]
    )
    return "\n".join(lines) + "\n"


def run() -> dict[str, Any]:
    depths = [probe_index_depth("sh000300"), probe_index_depth("sh000001")]
    valid_depths = [item for item in depths if item.earliest is not None]
    if not valid_depths:
        raise RuntimeError("No index depth available")
    main_symbol = "sh000001" if any(item.symbol == "sh000001" and item.earliest is not None for item in depths) else min(valid_depths, key=lambda item: item.earliest or date.max).symbol
    confirm_symbol = "sh000300"

    end = _parse_date(_load_yaml("backtest.yaml")["full_history"]["oos"]["end"])
    datasets = {
        main_symbol: {main_symbol: _load_series_data(main_symbol, end)},
        confirm_symbol: {confirm_symbol: _load_series_data(confirm_symbol, end)},
    }
    main_result = _series_report(main_symbol, "main", datasets[main_symbol])
    confirm_result = _series_report(confirm_symbol, "confirm", datasets[confirm_symbol])
    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    report = render_report(depths, main_result, confirm_result, datasets)
    path = REPORT_DIR / "s3b_fullhistory.md"
    path.write_text(report, encoding="utf-8")
    return {"depths": depths, "main": main_result, "confirm": confirm_result, "path": path}


def main() -> None:
    result = run()
    print(f"wrote {result['path']}")
    for item in result["depths"]:
        print(f"{item.symbol}: rows={item.rows} earliest={item.earliest} latest={item.latest} error={item.error}")
    for key in ("main", "confirm"):
        item = result[key]
        print(
            f"{key}/{item['symbol']}: bull={'PASS' if item['bull_pass'] else 'FAIL'} "
            f"bear={'PASS' if item['bear_pass'] else 'FAIL'} "
            f"oos={'PASS' if item['oos'][2] else 'FAIL'} final={'PASS' if item['passed'] else 'FAIL'}"
        )
    print(f"TOTAL final: {'PASS' if result['main']['passed'] and result['confirm']['passed'] else 'FAIL'}")


if __name__ == "__main__":
    main()
