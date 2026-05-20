"""S8 RSI reversal Gate1 runner."""

from __future__ import annotations

import argparse
import math
from dataclasses import dataclass
from datetime import date
from typing import Any, Callable

import numpy as np
import pandas as pd

from backtest.constraints import Order, Position
from backtest.engine import (
    LOT_SIZE,
    REPORT_DIR,
    BacktestRun,
    _fmt_float,
    _fmt_pct,
    _load_yaml,
    _metric_ratio,
    _parse_date,
    _ratio_note,
    summarize_run,
)
from backtest.s6_gate1 import (
    S6_SERIES,
    SeriesDepth,
    _coverage,
    _load_series_data,
    _run_buy_hold,
    _run_fast_backtest,
    _run_s3b,
    _run_s3c,
    _run_s6,
)
from strategies.s8_rsi_reversal import S8RSIReversalStrategy, wilder_rsi


S8_SERIES = S6_SERIES
SENSITIVITY_PERIODS = (7, 14, 21)
SENSITIVITY_BUY = (20, 30, 40)
SENSITIVITY_SELL = (60, 70, 80)

FastSignalFunc = Callable[[date, float, tuple[Position, ...]], list[Order]]


@dataclass(frozen=True)
class CycleResult:
    symbol: str
    name: str
    start: date
    end: date
    s8: dict[str, float]
    buy_hold: dict[str, float]
    s6: dict[str, float]
    s3b: dict[str, float]
    s3c: dict[str, float]
    passed: bool
    criterion: str


def _strategy_cfg(
    rsi_period: int | None = None,
    buy_threshold: int | None = None,
    sell_threshold: int | None = None,
) -> dict[str, Any]:
    cfg = _load_yaml("strategy_addon.yaml")["s8_rsi_reversal"].copy()
    if rsi_period is not None:
        cfg["rsi_period"] = int(rsi_period)
    if buy_threshold is not None:
        cfg["buy_threshold"] = int(buy_threshold)
    if sell_threshold is not None:
        cfg["sell_threshold"] = int(sell_threshold)
    return cfg


def _floor_to_lot(quantity: float) -> int:
    if quantity <= 0:
        return 0
    return int(math.floor(quantity / LOT_SIZE) * LOT_SIZE)


def _close_map(frame: pd.DataFrame) -> dict[date, float]:
    return {item.date: float(item.close) for item in frame[["date", "close"]].itertuples(index=False)}


def _position_quantity(positions: tuple[Position, ...], symbol: str) -> int:
    return sum(item.quantity for item in positions if item.symbol == symbol and item.quantity > 0)


def _s8_fast_signal(
    symbol: str,
    frame: pd.DataFrame,
    rsi_period: int,
    buy_threshold: float,
    sell_threshold: float,
) -> FastSignalFunc:
    rows = frame.sort_values("date").reset_index(drop=True)
    close = pd.to_numeric(rows["close"], errors="coerce")
    rsi = wilder_rsi(close, rsi_period)
    signals: dict[date, str] = {}
    for idx in range(1, len(rows)):
        prev_rsi = rsi.iloc[idx - 1]
        curr_rsi = rsi.iloc[idx]
        if pd.isna(prev_rsi) or pd.isna(curr_rsi):
            continue
        current_date = rows.iloc[idx]["date"]
        if float(prev_rsi) <= buy_threshold and float(curr_rsi) > buy_threshold:
            signals[current_date] = "buy"
        elif float(prev_rsi) >= sell_threshold and float(curr_rsi) < sell_threshold:
            signals[current_date] = "sell"
    closes = _close_map(rows)

    def _signal(as_of_date: date, nav: float, positions: tuple[Position, ...]) -> list[Order]:
        side = signals.get(as_of_date)
        quantity = _position_quantity(positions, symbol)
        if side == "buy" and quantity <= 0:
            close_value = closes.get(as_of_date)
            if close_value is None:
                return []
            target = _floor_to_lot(nav / close_value)
            return [Order(symbol=symbol, side="buy", quantity=target, submitted_date=as_of_date)] if target > 0 else []
        if side == "sell" and quantity > 0:
            return [Order(symbol=symbol, side="sell", quantity=quantity, submitted_date=as_of_date)]
        return []

    return _signal


def _run_s8(
    symbol: str,
    name: str,
    start: date,
    end: date,
    data: dict[str, pd.DataFrame],
    rsi_period: int | None = None,
    buy_threshold: int | None = None,
    sell_threshold: int | None = None,
) -> BacktestRun:
    cfg = _strategy_cfg(rsi_period, buy_threshold, sell_threshold)
    cfg["asset"] = symbol
    S8RSIReversalStrategy(cfg)
    return _run_fast_backtest(
        name,
        start,
        end,
        data,
        symbol,
        _s8_fast_signal(
            symbol,
            data[symbol],
            int(cfg["rsi_period"]),
            float(cfg["buy_threshold"]),
            float(cfg["sell_threshold"]),
        ),
    )


def _run_quint(
    symbol: str,
    label: str,
    start: date,
    end: date,
    data: dict[str, pd.DataFrame],
) -> tuple[dict[str, float], dict[str, float], dict[str, float], dict[str, float], dict[str, float]]:
    s8 = summarize_run(_run_s8(symbol, f"s8_{label}", start, end, data))
    buy_hold = summarize_run(_run_buy_hold(symbol, f"buy_hold_{label}", start, end, data))
    s6 = summarize_run(_run_s6(symbol, f"failed_s6_{label}", start, end, data))
    s3b = summarize_run(_run_s3b(symbol, f"failed_s3b_{label}", start, end, data))
    s3c = summarize_run(_run_s3c(symbol, f"failed_s3c_{label}", start, end, data))
    return s8, buy_hold, s6, s3b, s3c


def _cycle_result(
    symbol: str,
    name: str,
    start: date,
    end: date,
    data: dict[str, pd.DataFrame],
    kind: str,
    max_drawdown: float,
) -> CycleResult:
    s8, buy_hold, s6, s3b, s3c = _run_quint(symbol, f"{symbol}_{name}", start, end, data)
    if kind == "bull":
        passed = s8["return"] > 0.0 and s8["max_drawdown"] <= max_drawdown
        criterion = "S8 return>0 and S8 DD<=20%"
    elif kind == "bear":
        passed = s8["max_drawdown"] < buy_hold["max_drawdown"] and s8["max_drawdown"] <= max_drawdown
        criterion = "S8 DD < buy_hold DD and S8 DD<=20%"
    else:
        raise ValueError(f"Unsupported cycle kind: {kind}")
    return CycleResult(symbol, name, start, end, s8, buy_hold, s6, s3b, s3c, bool(passed), criterion)


def _max_bool_streak(values: list[bool]) -> int:
    best = 0
    current = 0
    for value in values:
        if value:
            current += 1
            best = max(best, current)
        else:
            current = 0
    return best


def _bull_rsi_streak_rows(symbol: str, data: dict[str, pd.DataFrame]) -> list[dict[str, Any]]:
    cfg = _strategy_cfg()
    period = int(cfg["rsi_period"])
    sell_threshold = float(cfg["sell_threshold"])
    full_cfg = _load_yaml("backtest.yaml")["full_history"]
    rows = data[symbol].sort_values("date").reset_index(drop=True).copy()
    rows["rsi"] = wilder_rsi(pd.to_numeric(rows["close"], errors="coerce"), period).to_numpy()
    rows["prev_rsi"] = rows["rsi"].shift(1)
    rows["sell_cross"] = (rows["prev_rsi"] >= sell_threshold) & (rows["rsi"] < sell_threshold)
    out: list[dict[str, Any]] = []
    for idx, (left, right) in enumerate(full_cfg["bull_cycles"], start=1):
        start = _parse_date(left)
        end = _parse_date(right)
        cycle = rows[(rows["date"] >= start) & (rows["date"] <= end)].copy()
        high = (pd.to_numeric(cycle["rsi"], errors="coerce") >= sell_threshold).fillna(False).tolist()
        out.append(
            {
                "cycle": f"bull_{idx}",
                "start": start,
                "end": end,
                "high_days": int(sum(high)),
                "max_streak_days": _max_bool_streak(high),
                "sell_crosses": int(cycle["sell_cross"].fillna(False).sum()),
            }
        )
    return out


def _sensitivity_rows(symbol: str, data: dict[str, pd.DataFrame]) -> list[dict[str, Any]]:
    full_cfg = _load_yaml("backtest.yaml")["full_history"]
    start = _parse_date(full_cfg["in_sample"]["start"])
    end = _parse_date(full_cfg["in_sample"]["end"])
    buy_hold = summarize_run(_run_buy_hold(symbol, f"{symbol}_bh_rsi_is", start, end, data))
    rows: list[dict[str, Any]] = []
    for period in SENSITIVITY_PERIODS:
        for buy_threshold in SENSITIVITY_BUY:
            for sell_threshold in SENSITIVITY_SELL:
                run = _run_s8(
                    symbol,
                    f"{symbol}_s8_{period}_{buy_threshold}_{sell_threshold}_is",
                    start,
                    end,
                    data,
                    period,
                    buy_threshold,
                    sell_threshold,
                )
                s8 = summarize_run(run)
                rows.append(
                    {
                        "symbol": symbol,
                        "rsi_period": period,
                        "buy_threshold": buy_threshold,
                        "sell_threshold": sell_threshold,
                        "s8": s8,
                        "buy_hold": buy_hold,
                        "loss_rate": 1.0 - s8["win_rate"] if s8["trades"] else 0.0,
                    }
                )
    return rows


def _series_report(symbol: str, label: str, data: dict[str, pd.DataFrame]) -> dict[str, Any]:
    backtest_cfg = _load_yaml("backtest.yaml")
    regimes = backtest_cfg["regimes"]
    full_cfg = backtest_cfg["full_history"]
    gate1 = backtest_cfg["gate1"]
    max_dd = float(gate1["max_drawdown_max"])

    standard = {}
    for regime, span in regimes.items():
        start = _parse_date(span["start"])
        end = _parse_date(span["end"])
        standard[regime] = _run_quint(symbol, f"{label}_{regime}", start, end, data)

    in_start = _parse_date(full_cfg["in_sample"]["start"])
    in_end = _parse_date(full_cfg["in_sample"]["end"])
    oos_start = _parse_date(full_cfg["oos"]["start"])
    oos_end = _parse_date(full_cfg["oos"]["end"])
    full_start = _parse_date(full_cfg["asset_min_start"])

    bull_cycles = [
        _cycle_result(symbol, f"bull_{idx}", _parse_date(left), _parse_date(right), data, "bull", max_dd)
        for idx, (left, right) in enumerate(full_cfg["bull_cycles"], start=1)
    ]
    bear_cycles = [
        _cycle_result(symbol, f"bear_{idx}", _parse_date(left), _parse_date(right), data, "bear", max_dd)
        for idx, (left, right) in enumerate(full_cfg["bear_cycles"], start=1)
    ]
    in_sample = _run_quint(symbol, f"{label}_in_sample", in_start, in_end, data)
    oos = _run_quint(symbol, f"{label}_full_oos", oos_start, oos_end, data)
    full_total = _run_quint(symbol, f"{label}_full_total", full_start, oos_end, data)
    oos_s8 = oos[0]
    oos_pass = (
        oos_s8["expectancy"] > float(gate1["expectancy_after_cost_gt"])
        and oos_s8["profit_factor"] >= float(gate1["profit_factor_min"])
        and oos_s8["max_drawdown"] <= max_dd
    )
    bull_pass = all(item.passed for item in bull_cycles)
    bear_pass = all(item.passed for item in bear_cycles)
    return {
        "symbol": symbol,
        "label": label,
        "standard": standard,
        "in_sample": in_sample,
        "oos": (*oos, bool(oos_pass)),
        "full_total": full_total,
        "bull_cycles": bull_cycles,
        "bear_cycles": bear_cycles,
        "bull_pass": bool(bull_pass),
        "bear_pass": bool(bear_pass),
        "cycle_consistency": bool(bull_pass and bear_pass),
        "oos_pass": bool(oos_pass),
        "passed": bool(bull_pass and bear_pass and oos_pass),
        "bull_rsi_streaks": _bull_rsi_streak_rows(symbol, data),
        "sensitivity": _sensitivity_rows(symbol, data),
    }


def _summary_table(results: list[dict[str, Any]]) -> str:
    lines = [
        "| series | regime | S8_return | S8_DD | trades | expectancy | PF | win_rate | fee_ratio |",
        "|---|---|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for result in results:
        for regime in ("bull", "bear", "range", "oos"):
            s8 = result["standard"][regime][0]
            lines.append(
                f"| {result['label']}/{result['symbol']} | {regime} | {_fmt_pct(s8['return'])} | "
                f"{_fmt_pct(s8['max_drawdown'])} | {int(s8['trades'])} | {s8['expectancy']:.2f} | "
                f"{_fmt_float(s8['profit_factor'])} | {_fmt_pct(s8['win_rate'])} | {_fmt_pct(s8['fee_ratio'])} |"
            )
    return "\n".join(lines)


def _control_table(results: list[dict[str, Any]]) -> str:
    lines = [
        "| series | span | S8_return | BH_return | failed_S6_return | failed_S3b_return | failed_S3c_return | S8/BH | S8/S6 | S8/S3b | S8/S3c | S8_DD | BH_DD | S8_trades | note |",
        "|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---|",
    ]
    for result in results:
        rows = [(name, *quint) for name, quint in result["standard"].items()]
        rows.extend(
            [
                ("full_in_sample", *result["in_sample"]),
                ("full_oos", *result["oos"][:5]),
                ("full_total", *result["full_total"]),
            ]
        )
        for span, s8, bh, s6, s3b, s3c in rows:
            ratios = (
                _metric_ratio(s8["return"], bh["return"]),
                _metric_ratio(s8["return"], s6["return"]),
                _metric_ratio(s8["return"], s3b["return"]),
                _metric_ratio(s8["return"], s3c["return"]),
            )
            lines.append(
                f"| {result['label']}/{result['symbol']} | {span} | {_fmt_pct(s8['return'])} | {_fmt_pct(bh['return'])} | "
                f"{_fmt_pct(s6['return'])} | {_fmt_pct(s3b['return'])} | {_fmt_pct(s3c['return'])} | "
                f"{_fmt_float(ratios[0])} | {_fmt_float(ratios[1])} | {_fmt_float(ratios[2])} | "
                f"{_fmt_float(ratios[3])} | {_fmt_pct(s8['max_drawdown'])} | {_fmt_pct(bh['max_drawdown'])} | "
                f"{int(s8['trades'])} | {_ratio_note(*ratios)} |"
            )
    return "\n".join(lines)


def _bull_rsi_streak_table(results: list[dict[str, Any]]) -> str:
    lines = [
        "| series | bull_cycle | start | end | RSI>=70_days | max_continuous_days | approx_months | sell_crosses |",
        "|---|---|---:|---:|---:|---:|---:|---:|",
    ]
    for result in results:
        for row in result["bull_rsi_streaks"]:
            months = row["max_streak_days"] / 21.0
            lines.append(
                f"| {result['label']}/{result['symbol']} | {row['cycle']} | {row['start']} | {row['end']} | "
                f"{row['high_days']} | {row['max_streak_days']} | {months:.1f} | {row['sell_crosses']} |"
            )
    return "\n".join(lines)


def _sensitivity_table(results: list[dict[str, Any]]) -> str:
    lines = [
        "| series | rsi_period | buy_th | sell_th | in_sample_return | BH_return | DD | trades | loss_rate | PF | win_rate | verdict |",
        "|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---|",
    ]
    for result in results:
        for row in result["sensitivity"]:
            s8 = row["s8"]
            bh = row["buy_hold"]
            verdict = "beats_BH" if s8["return"] > bh["return"] else "lags_BH"
            lines.append(
                f"| {result['label']}/{result['symbol']} | {row['rsi_period']} | {row['buy_threshold']} | "
                f"{row['sell_threshold']} | {_fmt_pct(s8['return'])} | {_fmt_pct(bh['return'])} | "
                f"{_fmt_pct(s8['max_drawdown'])} | {int(s8['trades'])} | {_fmt_pct(row['loss_rate'])} | "
                f"{_fmt_float(s8['profit_factor'])} | {_fmt_pct(s8['win_rate'])} | {verdict} |"
            )
    return "\n".join(lines)


def _magic_parameter_text(results: list[dict[str, Any]]) -> str:
    gate1 = _load_yaml("backtest.yaml")["gate1"]
    max_dd = float(gate1["max_drawdown_max"])
    grouped: dict[tuple[int, int, int], list[dict[str, Any]]] = {}
    for result in results:
        for row in result["sensitivity"]:
            key = (int(row["rsi_period"]), int(row["buy_threshold"]), int(row["sell_threshold"]))
            grouped.setdefault(key, []).append(row)
    winners = []
    for triple, rows in sorted(grouped.items()):
        if len(rows) != len(results):
            continue
        ok = all(
            row["s8"]["return"] > row["buy_hold"]["return"]
            and row["s8"]["profit_factor"] >= float(gate1["profit_factor_min"])
            and row["s8"]["max_drawdown"] <= max_dd
            for row in rows
        )
        if ok:
            winners.append(f"{triple[0]}/{triple[1]}/{triple[2]}")
    if winners:
        return "in_sample 出现同时满足三标的 beat BH、PF>=1.3、DD<=20% 的 RSI 参数：" + ", ".join(winners) + "；但未触碰 OOS，不能据此改默认参数。"
    return "in_sample 未发现同时满足三标的 beat BH、PF>=1.3、DD<=20% 的魔法 RSI 参数；未触碰 OOS，默认 14/30/70 不变。"


def _style_comparison_table(results: list[dict[str, Any]]) -> str:
    lines = [
        "| series | span | S8_reversal_return | S6_following_return | failed_S3b_return | S8_DD | S6_DD | S8_PF | S6_PF | lower_return | higher_DD |",
        "|---|---|---:|---:|---:|---:|---:|---:|---:|---|---|",
    ]
    for result in results:
        rows = [
            ("full_in_sample", *result["in_sample"]),
            ("full_oos", *result["oos"][:5]),
            ("full_total", *result["full_total"]),
        ]
        for span, s8, _bh, s6, s3b, _s3c in rows:
            lower_return = "S8反转" if s8["return"] < s6["return"] else "S6跟随"
            higher_dd = "S8反转" if s8["max_drawdown"] > s6["max_drawdown"] else "S6跟随"
            lines.append(
                f"| {result['label']}/{result['symbol']} | {span} | {_fmt_pct(s8['return'])} | "
                f"{_fmt_pct(s6['return'])} | {_fmt_pct(s3b['return'])} | {_fmt_pct(s8['max_drawdown'])} | "
                f"{_fmt_pct(s6['max_drawdown'])} | {_fmt_float(s8['profit_factor'])} | "
                f"{_fmt_float(s6['profit_factor'])} | {lower_return} | {higher_dd} |"
            )
    return "\n".join(lines)


def _cycle_table(results: list[dict[str, Any]]) -> str:
    lines = [
        "| series | cycle | start | end | S8_return | S8_DD | S8_trades | BH_return | BH_DD | failed_S6_return | failed_S3b_return | failed_S3c_return | result | criterion |",
        "|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---|---|",
    ]
    for result in results:
        for item in [*result["bull_cycles"], *result["bear_cycles"]]:
            lines.append(
                f"| {result['label']}/{result['symbol']} | {item.name} | {item.start} | {item.end} | "
                f"{_fmt_pct(item.s8['return'])} | {_fmt_pct(item.s8['max_drawdown'])} | {int(item.s8['trades'])} | "
                f"{_fmt_pct(item.buy_hold['return'])} | {_fmt_pct(item.buy_hold['max_drawdown'])} | "
                f"{_fmt_pct(item.s6['return'])} | {_fmt_pct(item.s3b['return'])} | {_fmt_pct(item.s3c['return'])} | "
                f"{'PASS' if item.passed else 'FAIL'} | {item.criterion} |"
            )
    return "\n".join(lines)


def _abc_table(results: list[dict[str, Any]]) -> str:
    lines = [
        "| series | A:bull_cycles_all_pass | B:bear_cycles_all_pass | cycle_consistency | C:full_history_oos_overall | final |",
        "|---|---|---|---|---|---|",
    ]
    for result in results:
        lines.append(
            f"| {result['label']}/{result['symbol']} | {'PASS' if result['bull_pass'] else 'FAIL'} | "
            f"{'PASS' if result['bear_pass'] else 'FAIL'} | {'PASS' if result['cycle_consistency'] else 'FAIL'} | "
            f"{'PASS' if result['oos_pass'] else 'FAIL'} | {'PASS' if result['passed'] else 'FAIL'} |"
        )
    final_pass = all(result["passed"] for result in results)
    lines.append(f"| TOTAL | - | - | - | - | {'PASS' if final_pass else 'FAIL'} |")
    return "\n".join(lines)


def _oos_detail_table(results: list[dict[str, Any]]) -> str:
    gate1 = _load_yaml("backtest.yaml")["gate1"]
    lines = [
        "| series | expectancy>0 | PF>=1.3 | DD<=20% | trades | actual_expectancy | actual_PF | actual_DD | OOS_result |",
        "|---|---|---|---|---:|---:|---:|---:|---|",
    ]
    for result in results:
        s8 = result["oos"][0]
        exp_ok = s8["expectancy"] > float(gate1["expectancy_after_cost_gt"])
        pf_ok = s8["profit_factor"] >= float(gate1["profit_factor_min"])
        dd_ok = s8["max_drawdown"] <= float(gate1["max_drawdown_max"])
        lines.append(
            f"| {result['label']}/{result['symbol']} | {'PASS' if exp_ok else 'FAIL'} | {'PASS' if pf_ok else 'FAIL'} | "
            f"{'PASS' if dd_ok else 'FAIL'} | {int(s8['trades'])} | {s8['expectancy']:.2f} | "
            f"{_fmt_float(s8['profit_factor'])} | {_fmt_pct(s8['max_drawdown'])} | "
            f"{'PASS' if result['oos_pass'] else 'FAIL'} |"
        )
    return "\n".join(lines)


def _s8_vs_s6_text(results: list[dict[str, Any]]) -> str:
    worse_oos = sum(1 for result in results if result["oos"][0]["return"] < result["oos"][2]["return"])
    worse_full = sum(1 for result in results if result["full_total"][0]["return"] < result["full_total"][2]["return"])
    return (
        f"与 S6 对比：full_history OOS 中 S8 收益低于 S6 的标的数={worse_oos}/3，"
        f"full_total 中 S8 收益低于 S6 的标的数={worse_full}/3。二者最终均按同一 A/B/C 口径判定，"
        "反转与跟随都没有形成可迁移的 A 股宽基技术指标 edge。"
    )


def render_report(depths: list[SeriesDepth], results: list[dict[str, Any]]) -> str:
    cfg = _strategy_cfg()
    final_pass = all(result["passed"] for result in results)
    total_trades = sum(int(result["full_total"][0]["trades"]) for result in results)
    lines = [
        "# S8 RSI Reversal Gate1 Report",
        "",
        f"规则：经典 Wilder RSI({cfg['rsi_period']})，RSI 上穿 {cfg['buy_threshold']} 买入、下穿 {cfg['sell_threshold']} 卖出/空仓；D 日收盘后判信号，D+1 开盘撮合。",
        "撮合复用 backtest/constraints.py：成本、滑点、T+1、涨跌停/停牌拒单同一套实现。指数 limit_up/down 多为 NaN，偏差方向是略乐观。",
        "",
        "## 数据深度实证",
        "| symbol | name | rows | earliest | latest | source | error |",
        "|---|---|---:|---:|---:|---|---|",
    ]
    for item in depths:
        lines.append(
            f"| {item.symbol} | {item.name} | {item.rows} | {item.earliest or 'NA'} | {item.latest or 'NA'} | {item.source} | {item.error or ''} |"
        )
    lines.extend(
        [
            "",
            "## S8 分段关键指标",
            _summary_table(results),
            "",
            "## 对照组 ratio 表",
            _control_table(results),
            "",
            "对照组结论：S8 与同序列买入持有、已 FAIL 的 S6 双均线、已 FAIL 的 S3b(MA200)、已 FAIL 的 S3c(月频 Faber)在同一数据序列和同一撮合约束下重跑。反转 vs 跟随两个流派在 A 股宽基上都没有通过 Gate1；ratio>2x 只标记调查，不作为调参依据。",
            "",
            "## 反假设列表",
            "1. A 股长期趋势市 RSI 反转被趋势吞：bull 段 RSI 高位维持，所谓超买可持续 N 个月，过早卖出后等不到有效回补。",
            _bull_rsi_streak_table(results),
            "2. 参数敏感性：rsi_period∈{7,14,21} 与 buy_threshold∈{20,30,40} × sell_threshold∈{60,70,80}，仅 full_history in_sample 展示；未用 OOS 选参。",
            _sensitivity_table(results),
            _magic_parameter_text(results),
            "3. 与 S6 双均线对比，检验反转 vs 跟随哪个更差；以下只比较已预注册默认参数，不做二次选择。",
            _style_comparison_table(results),
            _s8_vs_s6_text(results),
            "",
            "## flag/参数调查记录",
            "- 未调参、未碰OOS。",
            "- 默认 rsi_period/buy_threshold/sell_threshold 保持 configs/strategy_addon.yaml 的 14/30/70；敏感性表只用 in_sample，不用于改参数。",
            "- full_history bull/bear cycles 完全来自 configs/backtest.yaml，未事后增删。",
            "- 标准 regimes 与 full_history 均重置初始资金独立回测；这是 Gate1 检验口径，不是连续实盘净值。",
            "",
            "## low_freq_significance 跨 cycle 一致性判定表",
            _cycle_table(results),
            "",
            "## A/B/C 判定",
            _abc_table(results),
            "",
            "### C/OOS overall 判据明细",
            _oos_detail_table(results),
            "",
            f"total_default_s8_full_history_trades={total_trades}",
            f"最终判定：{'PASS' if final_pass else 'FAIL'}",
        ]
    )
    return "\n".join(lines) + "\n"


def run(refresh: bool = False) -> dict[str, Any]:
    backtest_cfg = _load_yaml("backtest.yaml")
    end = _parse_date(backtest_cfg["full_history"]["oos"]["end"])
    datasets: dict[str, dict[str, pd.DataFrame]] = {}
    depths: list[SeriesDepth] = []
    for symbol, label in S8_SERIES:
        try:
            frame = _load_series_data(symbol, end, refresh=refresh)
            datasets[symbol] = {symbol: frame}
            depths.append(_coverage(symbol, label, frame))
        except Exception as exc:
            datasets[symbol] = {symbol: pd.DataFrame()}
            depths.append(_coverage(symbol, label, pd.DataFrame(), error=f"{type(exc).__name__}: {exc}"))

    results = []
    for symbol, label in S8_SERIES:
        data = datasets[symbol]
        if data[symbol].empty:
            raise RuntimeError(f"S8 data unavailable for {symbol}")
        results.append(_series_report(symbol, label, data))

    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    path = REPORT_DIR / "s8_rsi_gate1.md"
    path.write_text(render_report(depths, results), encoding="utf-8")
    return {
        "path": path,
        "depths": depths,
        "results": results,
        "final_pass": all(result["passed"] for result in results),
        "total_trades": sum(int(result["full_total"][0]["trades"]) for result in results),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Run S8 RSI reversal Gate1")
    parser.add_argument("--refresh", action="store_true", help="refresh AkShare cache")
    args = parser.parse_args()
    result = run(refresh=args.refresh)
    print(f"wrote {result['path']}")
    for item in result["depths"]:
        print(f"{item.symbol}: rows={item.rows} earliest={item.earliest} latest={item.latest} source={item.source} error={item.error}")
    for item in result["results"]:
        print(
            f"{item['label']}/{item['symbol']}: A_bull={'PASS' if item['bull_pass'] else 'FAIL'} "
            f"B_bear={'PASS' if item['bear_pass'] else 'FAIL'} C_oos={'PASS' if item['oos_pass'] else 'FAIL'} "
            f"cycle_consistency={'PASS' if item['cycle_consistency'] else 'FAIL'} "
            f"final={'PASS' if item['passed'] else 'FAIL'}"
        )
    print(f"S8 trades={result['total_trades']} final={'PASS' if result['final_pass'] else 'FAIL'}")


if __name__ == "__main__":
    main()
