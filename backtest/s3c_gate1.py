"""S3c Faber monthly trend final full-history Gate1 test."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Any, Callable

import pandas as pd

from backtest.constraints import CostConfig, Order, Position
from backtest.engine import (
    INITIAL_CASH,
    LOT_SIZE,
    REPORT_DIR,
    BacktestRun,
    _fmt_float,
    _fmt_pct,
    _load_yaml,
    _parse_date,
    _run_signal_backtest,
    summarize_run,
)
from data.akshare_source import get_index_daily
from strategies.s3b_trend import S3BTrendStrategy
from strategies.s3c_trend_monthly import S3CTrendMonthlyStrategy


DEPTH_START = "1990-01-01"
MAIN_SYMBOL = "sh000001"
CONFIRM_SYMBOL = "sh000300"
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
    s3c: dict[str, float]
    buy_hold: dict[str, float]
    s3b: dict[str, float]
    passed: bool | None
    criterion: str


SignalFunc = Callable[[date, dict[str, Any]], list[Order]]


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


def _monthly_close_frame(frame: pd.DataFrame) -> pd.DataFrame:
    rows = frame.copy()
    rows["date_ts"] = pd.to_datetime(rows["date"], errors="coerce")
    rows = rows.dropna(subset=["date_ts", "close"]).sort_values("date_ts")
    rows["month"] = rows["date_ts"].dt.to_period("M")
    monthly = rows.groupby("month", as_index=False).tail(1).copy()
    monthly["date"] = monthly["date_ts"].dt.date
    return monthly[["date", "close"]].sort_values("date").reset_index(drop=True)


def _monthly_signal_wrapper(asset: str, data: dict[str, pd.DataFrame], strategy: S3CTrendMonthlyStrategy) -> SignalFunc:
    monthly = _monthly_close_frame(data[asset])
    month_end_dates = set(monthly["date"].tolist())

    def _signal(as_of_date: date, ctx: dict[str, Any]) -> list[Order]:
        if as_of_date not in month_end_dates:
            return []
        ctx2 = dict(ctx)
        ctx2["monthly_data"] = {asset: monthly[monthly["date"] <= as_of_date].copy()}
        return strategy.generate_signals(as_of_date, ctx2)

    return _signal


def _single_asset_buy_hold_signal(asset: str) -> SignalFunc:
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


def _run_s3c(symbol: str, name: str, start: date, end: date, data: dict[str, pd.DataFrame]) -> BacktestRun:
    cfg = _load_yaml("strategy.yaml")["s3c_trend_monthly"].copy()
    cfg["asset"] = symbol
    strategy = S3CTrendMonthlyStrategy(cfg)
    cost_cfg = CostConfig.from_mapping(_load_yaml("cost.yaml"))
    return _run_signal_backtest(
        name,
        "full_history",
        start,
        end,
        data,
        _monthly_signal_wrapper(symbol, data, strategy),
        cost_cfg,
    )


def _run_buy_hold(symbol: str, name: str, start: date, end: date, data: dict[str, pd.DataFrame]) -> BacktestRun:
    cost_cfg = CostConfig.from_mapping(_load_yaml("cost.yaml"))
    return _run_signal_backtest(name, "full_history", start, end, data, _single_asset_buy_hold_signal(symbol), cost_cfg)


def _run_s3b(symbol: str, name: str, start: date, end: date, data: dict[str, pd.DataFrame]) -> BacktestRun:
    cfg = _load_yaml("strategy.yaml")["s3b_trend"].copy()
    cfg["asset"] = symbol
    strategy = S3BTrendStrategy(cfg)
    cost_cfg = CostConfig.from_mapping(_load_yaml("cost.yaml"))
    return _run_signal_backtest(name, "full_history", start, end, data, strategy.generate_signals, cost_cfg)


def _run_triplet(symbol: str, label: str, start: date, end: date, data: dict[str, pd.DataFrame]) -> tuple[dict[str, float], dict[str, float], dict[str, float]]:
    s3c = summarize_run(_run_s3c(symbol, f"s3c_{label}", start, end, data))
    buy_hold = summarize_run(_run_buy_hold(symbol, f"buy_hold_{label}", start, end, data))
    s3b = summarize_run(_run_s3b(symbol, f"s3b_failed_ref_{label}", start, end, data))
    return s3c, buy_hold, s3b


def _cycle_result(
    symbol: str,
    name: str,
    start: date,
    end: date,
    data: dict[str, pd.DataFrame],
    kind: str,
    max_drawdown: float,
) -> CycleResult:
    s3c, buy_hold, s3b = _run_triplet(symbol, f"{symbol}_{name}", start, end, data)
    if kind == "bull":
        passed = s3c["return"] > 0.0 and s3c["max_drawdown"] <= max_drawdown
        criterion = "S3c return>0 and S3c DD<=20%"
    elif kind == "bear":
        passed = s3c["max_drawdown"] < buy_hold["max_drawdown"] and s3c["max_drawdown"] <= max_drawdown
        criterion = "S3c DD < buy_hold DD and S3c DD<=20%"
    else:
        passed = None
        criterion = "listed only; OOS overall is judged separately"
    return CycleResult(name=name, start=start, end=end, s3c=s3c, buy_hold=buy_hold, s3b=s3b, passed=passed, criterion=criterion)


def _series_report(symbol: str, label: str, data: dict[str, pd.DataFrame]) -> dict[str, Any]:
    backtest_cfg = _load_yaml("backtest.yaml")
    full_cfg = backtest_cfg["full_history"]
    gate1 = backtest_cfg["gate1"]
    max_dd = float(gate1["max_drawdown_max"])

    in_start = _parse_date(full_cfg["in_sample"]["start"])
    in_end = _parse_date(full_cfg["in_sample"]["end"])
    oos_start = _parse_date(full_cfg["oos"]["start"])
    oos_end = _parse_date(full_cfg["oos"]["end"])

    bull_cycles = [
        _cycle_result(symbol, f"bull_{idx}", _parse_date(left), _parse_date(right), data, "bull", max_dd)
        for idx, (left, right) in enumerate(full_cfg["bull_cycles"], start=1)
    ]
    bear_cycles = [
        _cycle_result(symbol, f"bear_{idx}", _parse_date(left), _parse_date(right), data, "bear", max_dd)
        for idx, (left, right) in enumerate(full_cfg["bear_cycles"], start=1)
    ]
    oos_cycles = [
        _cycle_result(symbol, name, _parse_date(left), _parse_date(right), data, "oos", max_dd)
        for name, left, right in OOS_SUBCYCLES
    ]
    in_s3c, in_bh, in_s3b = _run_triplet(symbol, f"{label}_in_sample", in_start, in_end, data)
    oos_s3c, oos_bh, oos_s3b = _run_triplet(symbol, f"{label}_oos", oos_start, oos_end, data)
    oos_pass = (
        oos_s3c["expectancy"] > float(gate1["expectancy_after_cost_gt"])
        and oos_s3c["profit_factor"] >= float(gate1["profit_factor_min"])
        and oos_s3c["max_drawdown"] <= max_dd
    )
    bull_pass = all(item.passed for item in bull_cycles)
    bear_pass = all(item.passed for item in bear_cycles)
    return {
        "symbol": symbol,
        "label": label,
        "in_sample": (in_s3c, in_bh, in_s3b),
        "oos": (oos_s3c, oos_bh, oos_s3b, oos_pass),
        "bull_cycles": bull_cycles,
        "bear_cycles": bear_cycles,
        "oos_cycles": oos_cycles,
        "passed": bool(bull_pass and bear_pass and oos_pass),
        "bull_pass": bool(bull_pass),
        "bear_pass": bool(bear_pass),
    }


def _metric_table(cycles: list[CycleResult], include_pass: bool = True) -> str:
    lines = [
        "| cycle | start | end | S3c_return | S3c_DD | S3c_trades | BH_return | BH_DD | S3b_return | S3b_DD | pass | criterion |",
        "|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---|---|",
    ]
    for item in cycles:
        passed = "N/A" if item.passed is None else "PASS" if item.passed else "FAIL"
        if not include_pass:
            passed = "N/A"
        lines.append(
            f"| {item.name} | {item.start} | {item.end} | {_fmt_pct(item.s3c['return'])} | "
            f"{_fmt_pct(item.s3c['max_drawdown'])} | {int(item.s3c['trades'])} | "
            f"{_fmt_pct(item.buy_hold['return'])} | {_fmt_pct(item.buy_hold['max_drawdown'])} | "
            f"{_fmt_pct(item.s3b['return'])} | {_fmt_pct(item.s3b['max_drawdown'])} | {passed} | {item.criterion} |"
        )
    return "\n".join(lines)


def _overall_table(result: dict[str, Any]) -> str:
    in_s3c, in_bh, in_s3b = result["in_sample"]
    oos_s3c, oos_bh, oos_s3b, oos_pass = result["oos"]
    rows = [("in_sample", in_s3c, in_bh, in_s3b, None), ("oos", oos_s3c, oos_bh, oos_s3b, oos_pass)]
    lines = [
        "| span | S3c_return | S3c_DD | S3c_trades | S3c_expectancy | S3c_PF | S3c_win_rate | BH_return | BH_DD | failed_S3b_return | failed_S3b_DD | pass |",
        "|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---|",
    ]
    for name, s3c, bh, s3b, passed in rows:
        pass_text = "N/A" if passed is None else "PASS" if passed else "FAIL"
        lines.append(
            f"| {name} | {_fmt_pct(s3c['return'])} | {_fmt_pct(s3c['max_drawdown'])} | {int(s3c['trades'])} | "
            f"{s3c['expectancy']:.2f} | {_fmt_float(s3c['profit_factor'])} | {_fmt_pct(s3c['win_rate'])} | "
            f"{_fmt_pct(bh['return'])} | {_fmt_pct(bh['max_drawdown'])} | "
            f"{_fmt_pct(s3b['return'])} | {_fmt_pct(s3b['max_drawdown'])} | {pass_text} |"
        )
    return "\n".join(lines)


def _oos_2019_line(result: dict[str, Any]) -> str:
    for item in result["oos_cycles"]:
        if item.name == "2019_2021_bull":
            return (
                f"{result['label']}/{result['symbol']} 2019-2021: "
                f"S3c return={_fmt_pct(item.s3c['return'])}, DD={_fmt_pct(item.s3c['max_drawdown'])}; "
                f"buy_hold return={_fmt_pct(item.buy_hold['return'])}, DD={_fmt_pct(item.buy_hold['max_drawdown'])}; "
                f"failed_S3b return={_fmt_pct(item.s3b['return'])}, DD={_fmt_pct(item.s3b['max_drawdown'])}."
            )
    return f"{result['label']}/{result['symbol']} 2019-2021: NA."


def render_report(depths: list[SeriesDepth], main_result: dict[str, Any], confirm_result: dict[str, Any]) -> str:
    final_pass = bool(main_result["passed"] and confirm_result["passed"])
    strategy_cfg = _load_yaml("strategy.yaml")["s3c_trend_monthly"]
    lines = [
        "# S3c Faber Monthly Trend Gate1 Report",
        "",
        "规则：Faber(2007) 经典月频趋势；仅用月末收盘序列计算 10个月 SMA，月末 close>SMA 则下月首个交易日开盘持有，否则现金。",
        f"参数：ma_len_months={strategy_cfg['ma_len_months']}，未调参；OOS 未用于参数选择；这是硬一次性最终试验。",
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
            f"主证据序列：{main_result['symbol']}；可投资确认序列：{confirm_result['symbol']}。full_history in_sample/OOS 起止完全使用 configs/backtest.yaml。",
            "月末信号日期由该序列实际交易日历的每月最后一个交易日确定；策略收到的 daily/monthly 数据均截断到 as_of_date。",
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
            "- 趋势=牛市 beta：用每个独立 bear cycle 的 S3c vs 买入持有回撤比较证伪；若任何熊市 S3c 回撤不低于买入持有或绝对回撤>20%，该机制判 FAIL。",
            "- 月频是否仍在 OOS 牛市 whipsaw/掉队：直接看 2019-2021 牛市段。",
            f"  {_oos_2019_line(main_result)}",
            f"  {_oos_2019_line(confirm_result)}",
            "- 上证综指不可直接交易：sh000001 只作机制主证据；可投资确认必须看 sh000300。二者若分歧，最终不能只凭不可交易指数通过。",
            "- 指数一字板约束乐观偏差：limit_up/down 为 NaN，constraints 不触发一字板拒单；偏差方向是略乐观。",
            "",
            "## flag/参数调查记录",
            "- 未调 ma_len_months，固定 10个月 SMA。",
            "- 未触碰 OOS 调参；OOS 只用于最终裁决。",
            "- 硬一次性最终试验；未为通过闸门改变信号频率、周期表或判据。",
            "- 未修改 S1/S2/S3/S3b 代码路径；S3b 仅作为已失败对照重跑同周期指标。",
            "",
            "## low_freq_significance 判定表",
            "| series | bull_cycles_all_pass | bear_cycles_all_pass | oos_overall_pass | final |",
            "|---|---|---|---|---|",
            f"| main/{main_result['symbol']} | {'PASS' if main_result['bull_pass'] else 'FAIL'} | {'PASS' if main_result['bear_pass'] else 'FAIL'} | {'PASS' if main_result['oos'][3] else 'FAIL'} | {'PASS' if main_result['passed'] else 'FAIL'} |",
            f"| confirm/{confirm_result['symbol']} | {'PASS' if confirm_result['bull_pass'] else 'FAIL'} | {'PASS' if confirm_result['bear_pass'] else 'FAIL'} | {'PASS' if confirm_result['oos'][3] else 'FAIL'} | {'PASS' if confirm_result['passed'] else 'FAIL'} |",
            f"| TOTAL | - | - | - | {'PASS' if final_pass else 'FAIL'} |",
            "",
            "### OOS overall 判据明细",
            "| series | expectancy>0 | PF>=1.3 | DD<=20% | actual_expectancy | actual_PF | actual_DD | result |",
            "|---|---|---|---|---:|---:|---:|---|",
        ]
    )
    gate1 = _load_yaml("backtest.yaml")["gate1"]
    for result in (main_result, confirm_result):
        oos_s3c = result["oos"][0]
        checks = (
            oos_s3c["expectancy"] > float(gate1["expectancy_after_cost_gt"]),
            oos_s3c["profit_factor"] >= float(gate1["profit_factor_min"]),
            oos_s3c["max_drawdown"] <= float(gate1["max_drawdown_max"]),
        )
        lines.append(
            f"| {result['label']}/{result['symbol']} | {'PASS' if checks[0] else 'FAIL'} | {'PASS' if checks[1] else 'FAIL'} | "
            f"{'PASS' if checks[2] else 'FAIL'} | {oos_s3c['expectancy']:.2f} | {_fmt_float(oos_s3c['profit_factor'])} | "
            f"{_fmt_pct(oos_s3c['max_drawdown'])} | {'PASS' if result['oos'][3] else 'FAIL'} |"
        )
    lines.extend(["", f"最终判定：{'PASS' if final_pass else 'FAIL'}"])
    return "\n".join(lines) + "\n"


def run() -> dict[str, Any]:
    backtest_cfg = _load_yaml("backtest.yaml")
    end = _parse_date(backtest_cfg["full_history"]["oos"]["end"])
    depths = [probe_index_depth(CONFIRM_SYMBOL), probe_index_depth(MAIN_SYMBOL)]
    datasets = {
        MAIN_SYMBOL: {MAIN_SYMBOL: _load_series_data(MAIN_SYMBOL, end)},
        CONFIRM_SYMBOL: {CONFIRM_SYMBOL: _load_series_data(CONFIRM_SYMBOL, end)},
    }
    main_result = _series_report(MAIN_SYMBOL, "main", datasets[MAIN_SYMBOL])
    confirm_result = _series_report(CONFIRM_SYMBOL, "confirm", datasets[CONFIRM_SYMBOL])
    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    path = REPORT_DIR / "s3c_gate1.md"
    path.write_text(render_report(depths, main_result, confirm_result), encoding="utf-8")
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
            f"oos={'PASS' if item['oos'][3] else 'FAIL'} final={'PASS' if item['passed'] else 'FAIL'}"
        )
        oos = item["oos"][0]
        print(
            f"{key}/{item['symbol']} oos_metrics: return={oos['return']:.4%} "
            f"dd={oos['max_drawdown']:.4%} expectancy={oos['expectancy']:.2f} pf={_fmt_float(oos['profit_factor'])}"
        )
    final_pass = result["main"]["passed"] and result["confirm"]["passed"]
    print(f"TOTAL final: {'PASS' if final_pass else 'FAIL'}")


if __name__ == "__main__":
    main()
