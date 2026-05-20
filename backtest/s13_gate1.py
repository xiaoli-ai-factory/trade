"""S13 vol-targeted cross-asset ETF risk parity Gate1 runner."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Any

import numpy as np
import pandas as pd

from backtest.constraints import CostConfig, MarketBar, Order, Position, apply_execution, mark_sellable, match_order
from backtest.engine import (
    INITIAL_CASH,
    LOT_SIZE,
    REPORT_DIR,
    BacktestRun,
    TradeRecord,
    _apply_basis_on_fill,
    _fmt_float,
    _fmt_pct,
    _last_close,
    _load_yaml,
    _market_bar,
    _max_drawdown,
    _position_for_symbol,
    _record_execution,
    _slice_data,
    summarize_run,
)
from backtest.s9_gate1 import (
    SignalFunc,
    _common_dates,
    _effective_span,
    _effective_span_table,
    _equal_weight_signal,
    _gate_checks,
    _gate_table,
    _insample_oos_table,
    _month_end_dates,
    _next_trading_date,
    _sixty_forty_signal,
    _single_etf_buy_hold_signal,
    _summary_table,
    load_s9_data,
    run_monthly_backtest,
)
from backtest.s12_gate1 import REGIMES, _data_status_table, _final_pool_table, load_s12_data
from strategies.s9_risk_parity import S9RiskParityStrategy
from strategies.s13_vol_targeted_global_rp import S13VolTargetedGlobalRPStrategy


@dataclass(frozen=True)
class S13LeveragePoint:
    signal_date: date
    trade_date: date
    regime: str
    portfolio_vol_annual: float
    leverage_target: float
    leverage: float
    cash_weight: float


def _s13_cfg_from_s12(s12_cfg: dict[str, Any]) -> dict[str, Any]:
    addon = _load_yaml("strategy_addon.yaml")
    cfg = addon["s13_vol_targeted_global_rp"].copy()
    if str(cfg["base_pool"]) != "s12_global_risk_parity":
        raise ValueError("S13 must reuse s12_global_risk_parity as base_pool")
    cfg["pool"] = [dict(item) for item in s12_cfg["pool"]]
    return cfg


def _gate_cfg() -> dict[str, Any]:
    return _load_yaml("backtest.yaml")["gate1"]


def load_s13_data(refresh: bool = False) -> tuple[dict[str, pd.DataFrame], list[Any], dict[str, Any], dict[str, Any]]:
    data, statuses, s12_cfg = load_s12_data(refresh=refresh)
    if len(s12_cfg["pool"]) != 7:
        raise RuntimeError(f"S13 requires the full S12 7 ETF pool, got {len(s12_cfg['pool'])}")
    return data, statuses, _s13_cfg_from_s12(s12_cfg), s12_cfg


def _mark_nav(cash: float, positions: tuple[Position, ...], data: dict[str, pd.DataFrame], as_of_date: date) -> float:
    nav = cash
    for item in positions:
        close = _last_close(data, item.symbol, as_of_date)
        if close is not None:
            nav += item.quantity * close
    return nav


def _ctx_for_signal(
    as_of_date: date,
    cash: float,
    positions: tuple[Position, ...],
    data: dict[str, pd.DataFrame],
    month_ends: set[date],
) -> dict[str, Any]:
    return {
        "data": _slice_data(data, as_of_date),
        "positions": positions,
        "cash": cash,
        "nav": _mark_nav(cash, positions, data, as_of_date),
        "lot_size": LOT_SIZE,
        "month_end_dates": month_ends,
    }


def _execute_orders_allow_margin(
    orders: list[Order],
    trade_date: date,
    cash: float,
    positions: tuple[Position, ...],
    basis: dict[str, float],
    trades: list[TradeRecord],
    filled_orders: list[dict[str, Any]],
    rejected_orders: list[dict[str, Any]],
    events: list[dict[str, Any]],
    data: dict[str, pd.DataFrame],
    cost_config: CostConfig,
) -> tuple[float, tuple[Position, ...]]:
    for order in sorted(orders, key=lambda item: 0 if item.side == "sell" else 1):
        bar = _market_bar(data, order.symbol, trade_date)
        position_before = _position_for_symbol(positions, order.symbol)
        position_arg = position_before if order.side == "sell" else None
        result = match_order(order, bar, cost_config, position=position_arg)
        if result.status == "filled":
            _apply_basis_on_fill(basis, result, position_before, trade_date, trades)
            cash += result.cash_delta
            positions = apply_execution(positions, result, trade_date)
            filled_orders.append(_record_execution(result, trade_date))
        else:
            rejected_orders.append(_record_execution(result, trade_date))
        events.extend(result.events)
    return cash, positions


def run_monthly_backtest_allow_margin(
    name: str,
    regime: str,
    start: date,
    end: date,
    data: dict[str, pd.DataFrame],
    calendar_dates: list[date],
    month_ends: set[date],
    signal_func: SignalFunc,
    cost_config: CostConfig,
) -> BacktestRun:
    dates = [item for item in calendar_dates if start <= item <= end]
    if not dates:
        raise RuntimeError(f"Not enough S13 dates for {regime}")

    cash = INITIAL_CASH
    positions: tuple[Position, ...] = ()
    basis: dict[str, float] = {}
    trades: list[TradeRecord] = []
    filled_orders: list[dict[str, Any]] = []
    rejected_orders: list[dict[str, Any]] = []
    events: list[dict[str, Any]] = []
    pending_orders: dict[date, list[Order]] = {}

    def schedule_signal(signal_date: date) -> None:
        trade_date = _next_trading_date(calendar_dates, signal_date)
        if trade_date is None or trade_date < start or trade_date > end:
            return
        ctx = _ctx_for_signal(signal_date, cash, positions, data, month_ends)
        orders = signal_func(signal_date, ctx)
        if orders:
            pending_orders.setdefault(trade_date, []).extend(orders)

    first_date = dates[0]
    previous_signals = [item for item in month_ends if item < first_date]
    if previous_signals:
        previous_month_end = max(previous_signals)
        if _next_trading_date(calendar_dates, previous_month_end) == first_date:
            schedule_signal(previous_month_end)

    nav_rows: list[dict[str, Any]] = [{"date": first_date.isoformat(), "nav": cash}]
    for trade_date in dates:
        positions = mark_sellable(positions, trade_date)
        orders = pending_orders.pop(trade_date, [])
        if orders:
            cash, positions = _execute_orders_allow_margin(
                orders,
                trade_date,
                cash,
                positions,
                basis,
                trades,
                filled_orders,
                rejected_orders,
                events,
                data,
                cost_config,
            )
        nav_rows.append({"date": trade_date.isoformat(), "nav": _mark_nav(cash, positions, data, trade_date)})
        if trade_date in month_ends:
            schedule_signal(trade_date)

    final_date = dates[-1]
    positions = mark_sellable(positions, final_date)
    for position in sorted(positions, key=lambda item: item.symbol):
        if not position.sellable:
            continue
        close = _last_close(data, position.symbol, final_date)
        if close is None:
            continue
        result = match_order(
            Order(symbol=position.symbol, side="sell", quantity=position.quantity, submitted_date=final_date),
            MarketBar(symbol=position.symbol, date=final_date, open=close, is_suspended=False),
            cost_config,
            position=position,
        )
        if result.status == "filled":
            _apply_basis_on_fill(basis, result, position, final_date, trades)
            cash += result.cash_delta
            positions = apply_execution(positions, result, final_date)
            filled_orders.append(_record_execution(result, final_date))
        else:
            rejected_orders.append(_record_execution(result, final_date))
        events.extend(result.events)

    final_nav = _mark_nav(cash, positions, data, final_date)
    nav_rows[-1] = {"date": final_date.isoformat(), "nav": final_nav}
    nav_curve = pd.DataFrame(nav_rows)
    return BacktestRun(
        name=name,
        regime=regime,
        start=start,
        end=end,
        initial_cash=INITIAL_CASH,
        final_nav=float(final_nav),
        total_return=float(final_nav) / INITIAL_CASH - 1.0,
        max_drawdown=_max_drawdown(nav_curve),
        trades=tuple(trades),
        filled_orders=tuple(filled_orders),
        rejected_orders=tuple(rejected_orders),
        events=tuple(events),
        nav_curve=nav_curve,
    )


def _regime_for_trade_date(trade_date: date, spans: dict[str, Any]) -> str | None:
    for regime in REGIMES:
        span = spans[regime]
        if span.effective_start <= trade_date <= span.effective_end:
            return regime
    return None


def _leverage_history(
    strategy: S13VolTargetedGlobalRPStrategy,
    data: dict[str, pd.DataFrame],
    calendar_dates: list[date],
    month_ends: set[date],
    spans: dict[str, Any],
) -> list[S13LeveragePoint]:
    points: list[S13LeveragePoint] = []
    min_start = min(spans[name].effective_start for name in REGIMES)
    max_end = max(spans[name].effective_end for name in REGIMES)
    for signal_date in sorted(item for item in month_ends if item <= max_end):
        trade_date = _next_trading_date(calendar_dates, signal_date)
        if trade_date is None or trade_date < min_start or trade_date > max_end:
            continue
        regime = _regime_for_trade_date(trade_date, spans)
        if regime is None:
            continue
        ctx = {
            "data": _slice_data(data, signal_date),
            "positions": (),
            "cash": INITIAL_CASH,
            "nav": INITIAL_CASH,
            "lot_size": LOT_SIZE,
            "month_end_dates": month_ends,
        }
        profile = strategy.target_profile(signal_date, ctx)
        if profile is None:
            continue
        points.append(
            S13LeveragePoint(
                signal_date=signal_date,
                trade_date=trade_date,
                regime=regime,
                portfolio_vol_annual=profile.portfolio_vol_annual,
                leverage_target=profile.leverage_target,
                leverage=profile.leverage,
                cash_weight=profile.cash_weight,
            )
        )
    return points


def _pct(values: list[float], percentile: float) -> float:
    return float(np.percentile(np.array(values, dtype=float), percentile)) if values else float("nan")


def _leverage_stats_table(points: list[S13LeveragePoint], strategy: S13VolTargetedGlobalRPStrategy) -> str:
    lines = [
        "| regime | signals | avg_leverage | min | p25 | median | p75 | max | avg_port_vol_annual | pct_at_min | pct_at_max | avg_cash_weight |",
        "|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for regime in REGIMES:
        rows = [item for item in points if item.regime == regime]
        leverages = [item.leverage for item in rows]
        vols = [item.portfolio_vol_annual for item in rows]
        cash = [item.cash_weight for item in rows]
        pct_at_min = float(np.mean(np.isclose(leverages, strategy.leverage_min))) if leverages else float("nan")
        pct_at_max = float(np.mean(np.isclose(leverages, strategy.leverage_max))) if leverages else float("nan")
        lines.append(
            f"| {regime} | {len(rows)} | {_fmt_float(float(np.mean(leverages)) if leverages else np.nan)} | "
            f"{_fmt_float(min(leverages) if leverages else np.nan)} | {_fmt_float(_pct(leverages, 25))} | "
            f"{_fmt_float(_pct(leverages, 50))} | {_fmt_float(_pct(leverages, 75))} | "
            f"{_fmt_float(max(leverages) if leverages else np.nan)} | {_fmt_pct(float(np.mean(vols)) if vols else np.nan)} | "
            f"{_fmt_pct(pct_at_min)} | {_fmt_pct(pct_at_max)} | {_fmt_pct(float(np.mean(cash)) if cash else np.nan)} |"
        )
    return "\n".join(lines)


def _bear_leverage_table(points: list[S13LeveragePoint]) -> str:
    lines = [
        "| signal_date(D close) | trade_date(D+1 open) | sigma_port_annual | leverage_target | clipped_leverage | cash_weight |",
        "|---|---|---:|---:|---:|---:|",
    ]
    for item in [point for point in points if point.regime == "bear"]:
        lines.append(
            f"| {item.signal_date} | {item.trade_date} | {_fmt_pct(item.portfolio_vol_annual)} | "
            f"{_fmt_float(item.leverage_target)} | {_fmt_float(item.leverage)} | {_fmt_pct(item.cash_weight)} |"
        )
    return "\n".join(lines)


def _oos_leverage_note(points: list[S13LeveragePoint], strategy: S13VolTargetedGlobalRPStrategy) -> str:
    oos = [item.leverage for item in points if item.regime == "oos"]
    if not oos:
        return "OOS leverage 无可用记录。"
    avg = float(np.mean(oos))
    pct_at_max = float(np.mean(np.isclose(oos, strategy.leverage_max)))
    pct_below_half = float(np.mean(np.array(oos) < 0.5))
    if pct_at_max >= 0.8:
        return f"OOS leverage 平均={avg:.4f}，{pct_at_max:.2%} 信号日在 1.5 上限，vol targeting 大部分时间形同固定顶格杠杆。"
    if pct_below_half >= 0.5:
        return f"OOS leverage 平均={avg:.4f}，{pct_below_half:.2%} 信号日低于 0.5，存在过度防御丢收益风险。"
    return f"OOS leverage 平均={avg:.4f}，未长期卡在 1.5 上限，也未长期低于 0.5；是否有效以 C 组和 S13/S12 差异为准。"


def _baseline_table(regime_runs: dict[str, BacktestRun]) -> str:
    names = (
        ("s13", "S13_vol_targeted_RP"),
        ("s12_no_vol_target", "S12_no_vol_target"),
        ("s9_single", "S9_single"),
        ("equal_weight_7etf", "equal_weight_7ETF"),
        ("sixty_forty", "60_40_HS300_bond"),
        ("hs300_buy_hold", "HS300ETF_BH"),
    )
    lines = [
        "| strategy | return | max_drawdown | trades | expectancy | gross_loss | profit_factor | win_rate | fee_ratio | filled_orders |",
        "|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for key, label in names:
        metrics = summarize_run(regime_runs[key])
        lines.append(
            f"| {label} | {_fmt_pct(metrics['return'])} | {_fmt_pct(metrics['max_drawdown'])} | {int(metrics['trades'])} | "
            f"{metrics['expectancy']:.2f} | {metrics['gross_loss']:.2f} | {_fmt_float(metrics['profit_factor'])} | "
            f"{_fmt_pct(metrics['win_rate'])} | {_fmt_pct(metrics['fee_ratio'])} | {len(regime_runs[key].filled_orders)} |"
        )
    return "\n".join(lines)


def _s13_vs_s12_table(all_runs: dict[str, dict[str, BacktestRun]]) -> str:
    lines = [
        "| regime | S13_return | S12_return | return_delta | S13_DD | S12_DD | DD_reduction | S13_expectancy | S12_expectancy | expectancy_delta | gross_loss_reduction | S13_PF | S12_PF |",
        "|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for regime in REGIMES:
        s13 = summarize_run(all_runs[regime]["s13"])
        s12 = summarize_run(all_runs[regime]["s12_no_vol_target"])
        lines.append(
            f"| {regime} | {_fmt_pct(s13['return'])} | {_fmt_pct(s12['return'])} | {_fmt_pct(s13['return'] - s12['return'])} | "
            f"{_fmt_pct(s13['max_drawdown'])} | {_fmt_pct(s12['max_drawdown'])} | {_fmt_pct(s12['max_drawdown'] - s13['max_drawdown'])} | "
            f"{s13['expectancy']:.2f} | {s12['expectancy']:.2f} | {s13['expectancy'] - s12['expectancy']:.2f} | "
            f"{s12['gross_loss'] - s13['gross_loss']:.2f} | {_fmt_float(s13['profit_factor'])} | {_fmt_float(s12['profit_factor'])} |"
        )
    return "\n".join(lines)


def _abc_line(checks: dict[str, Any]) -> str:
    a = "PASS" if checks["a_pass"] else "FAIL"
    b = "PASS" if checks["b_pass"] else "FAIL"
    c = "PASS" if checks["c_pass"] else "FAIL"
    total = "PASS" if checks["overall_pass"] else "FAIL"
    return f"A={a} / B={b} / C={c} / 最终={total}"


def render_report(
    all_runs: dict[str, dict[str, BacktestRun]],
    checks: dict[str, Any],
    statuses: list[Any],
    s13_cfg: dict[str, Any],
    s12_cfg: dict[str, Any],
    spans: dict[str, Any],
    leverage_points: list[S13LeveragePoint],
) -> str:
    final = "PASS" if checks["overall_pass"] else "FAIL"
    s13_runs = {name: values["s13"] for name, values in all_runs.items()}
    strategy = S13VolTargetedGlobalRPStrategy(s13_cfg)
    bear_s13 = summarize_run(all_runs["bear"]["s13"])
    bear_s12 = summarize_run(all_runs["bear"]["s12_no_vol_target"])
    oos_s13 = summarize_run(all_runs["oos"]["s13"])
    oos_s12 = summarize_run(all_runs["oos"]["s12_no_vol_target"])
    lines = [
        "# S13 Vol-Targeted Global Risk Parity Gate1 Report",
        "",
        f"规则：S13 100% 复用 S12 七 ETF 池。月末 D 收盘后，用过去 lookback_vol_days={s13_cfg['lookback_vol_days']} 个日日收益先算 inverse-vol 权重 w_i=(1/sigma_i)/sum(1/sigma_j)，再用同一 60 日收益协方差矩阵计算 sigma_port=sqrt(w'Σw)*sqrt(252)，leverage_target={s13_cfg['target_portfolio_vol_annual']:.2%}/sigma_port，按 [{s13_cfg['leverage_min']}, {s13_cfg['leverage_max']}] 裁剪，最终权重=w_i*leverage；1-leverage 作为现金或融资现金，现金收益/风险按 0 处理。",
        "执行：下个共同交易日开盘成交，S13 为表达预注册 1.5x 上限允许现金为负；每笔成交仍复用 constraints.py 的 5 元佣金地板、滑点、涨跌停/停牌拒单与 T+1。S12/S9/等权/60-40/买入持有对照组使用原现金约束。",
        "PIT：S13 策略在 target_profile 中断言所有输入 data.date<=D，并要求每个 ETF 序列 max(date)==D；sigma_i 与协方差矩阵都只由 D 及以前的收盘收益计算。",
        "",
        "## 数据覆盖",
        _data_status_table(statuses),
        "",
        "## 最终池配置",
        _final_pool_table(s12_cfg),
        "",
        "## regime 实际可得区间",
        _effective_span_table(spans),
        "",
        "## SPEC §3.1 对照组真实数字与 S13/S12 提升退化",
        "### S13 分段关键指标",
        _summary_table(s13_runs),
        "",
        "### S13 vs S12 逐 regime 提升/退化表",
        _s13_vs_s12_table(all_runs),
        "",
    ]
    for regime in REGIMES:
        lines.extend([f"### {regime} 对照组真实数字", _baseline_table(all_runs[regime]), ""])
    lines.extend(
        [
            "## SPEC §3.2 反假设",
            f"1. Vol Targeting 是否真的把 bear 救回：bear 段 S12 return={_fmt_pct(bear_s12['return'])}, DD={_fmt_pct(bear_s12['max_drawdown'])}, expectancy={bear_s12['expectancy']:.2f}, gross_loss={bear_s12['gross_loss']:.2f}；S13 return={_fmt_pct(bear_s13['return'])}, DD={_fmt_pct(bear_s13['max_drawdown'])}, expectancy={bear_s13['expectancy']:.2f}, gross_loss={bear_s13['gross_loss']:.2f}。DD_reduction={_fmt_pct(bear_s12['max_drawdown'] - bear_s13['max_drawdown'])}，gross_loss_reduction={bear_s12['gross_loss'] - bear_s13['gross_loss']:.2f}，以此判断是否只是换了收益路径。",
            "### bear leverage 历史变化",
            _bear_leverage_table(leverage_points),
            "",
            "2. OOS leverage 平均值与分布：若长期等于 1.5 上限，vol targeting 形同固定杠杆；若长期低于 0.5，则可能过度防御丢收益。",
            _leverage_stats_table(leverage_points, strategy),
            _oos_leverage_note(leverage_points, strategy),
            f"OOS 对照：S13 return={_fmt_pct(oos_s13['return'])}, DD={_fmt_pct(oos_s13['max_drawdown'])}, PF={_fmt_float(oos_s13['profit_factor'])}；S12 return={_fmt_pct(oos_s12['return'])}, DD={_fmt_pct(oos_s12['max_drawdown'])}, PF={_fmt_float(oos_s12['profit_factor'])}。",
            "",
            "3. 与 Bridgewater 全天候原版差异：原版 All Weather 的风险预算覆盖美元债、通胀联结债、商品、股票等，并能使用机构级期货/互换/融资和再平衡执行；本实验只用国内可交易 ETF 代理 A 股、港股、美股、黄金、国债，QDII 折溢价、A 股 T+1、涨跌停/停牌、散户佣金地板、滑点和无融资成本假设都会让结果不同。这里验证的是国内 ETF 近似机制，不是 Bridgewater 原产品复刻。",
            "",
            "4. flag: 参数全预注册不调 / 未碰OOS / S12 池子100%复用未挑选。target_vol、leverage_min/max、lookback、资产池、成本、regime、Gate1 阈值均未因结果改动；S13 是 backtest 阶段最后一次试验。",
            "",
            "## SPEC §3.3 A/B/C 判定与最终结论",
            "### in-sample vs OOS",
            _insample_oos_table(s13_runs),
            "",
            "### Gate1 判定表",
            _gate_table(checks, _gate_cfg()),
            "",
            f"A/B/C 汇总：{_abc_line(checks)}。",
            f"结论：S13 是 backtest 阶段最后一次试验，最终判定={final}。无论 PASS/FAIL，之后停止 hunting 转 forward paper。",
        ]
    )
    return "\n".join(lines) + "\n"


def run(refresh: bool = False) -> dict[str, Any]:
    cost_config = CostConfig.from_mapping(_load_yaml("cost.yaml"))
    data, statuses, s13_cfg, s12_cfg = load_s13_data(refresh=refresh)
    s9_data, _s9_coverages, _s9_probes, s9_cfg, _s9_note = load_s9_data(refresh=refresh)

    calendar_dates = _common_dates(data)
    if not calendar_dates:
        raise RuntimeError("No common S13 ETF calendar dates")
    month_ends = _month_end_dates(calendar_dates)
    spans = {name: _effective_span(name, calendar_dates) for name in REGIMES}

    s9_calendar_dates = _common_dates(s9_data)
    if not s9_calendar_dates:
        raise RuntimeError("No common S9 ETF calendar dates")
    s9_month_ends = _month_end_dates(s9_calendar_dates)
    s9_spans = {name: _effective_span(name, s9_calendar_dates) for name in REGIMES}

    s13_strategy = S13VolTargetedGlobalRPStrategy(s13_cfg)
    s12_strategy = S9RiskParityStrategy(s12_cfg)
    s9_strategy = S9RiskParityStrategy(s9_cfg)
    symbols = s13_strategy.symbols
    bond_symbol = next(str(item["code"]) for item in s12_cfg["pool"] if str(item["code"]).startswith("511"))

    all_runs: dict[str, dict[str, BacktestRun]] = {}
    for regime in REGIMES:
        span = spans[regime]
        common_args = (
            span.effective_start,
            span.effective_end,
            data,
            calendar_dates,
            month_ends,
        )
        s9_span = s9_spans[regime]
        s9_args = (
            s9_span.effective_start,
            s9_span.effective_end,
            s9_data,
            s9_calendar_dates,
            s9_month_ends,
        )
        all_runs[regime] = {
            "s13": run_monthly_backtest_allow_margin("s13", regime, *common_args, s13_strategy.generate_signals, cost_config),
            "s12_no_vol_target": run_monthly_backtest("s12_no_vol_target", regime, *common_args, s12_strategy.generate_signals, cost_config),
            "s9_single": run_monthly_backtest("s9_single", regime, *s9_args, s9_strategy.generate_signals, cost_config),
            "equal_weight_7etf": run_monthly_backtest("equal_weight_7etf", regime, *common_args, _equal_weight_signal(symbols), cost_config),
            "sixty_forty": run_monthly_backtest("sixty_forty", regime, *common_args, _sixty_forty_signal(symbols, bond_symbol), cost_config),
            "hs300_buy_hold": run_monthly_backtest("hs300_buy_hold", regime, *common_args, _single_etf_buy_hold_signal("510300"), cost_config),
        }

    s13_runs = {name: values["s13"] for name, values in all_runs.items()}
    checks = _gate_checks(s13_runs, _gate_cfg())
    leverage_points = _leverage_history(s13_strategy, data, calendar_dates, month_ends, spans)
    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    path = REPORT_DIR / "s13_vol_targeted_gate1.md"
    path.write_text(render_report(all_runs, checks, statuses, s13_cfg, s12_cfg, spans, leverage_points), encoding="utf-8")
    return {
        "path": path,
        "runs": all_runs,
        "checks": checks,
        "statuses": statuses,
        "s13_cfg": s13_cfg,
        "s12_cfg": s12_cfg,
        "spans": spans,
        "leverage_points": leverage_points,
    }


def main() -> None:
    result = run(refresh=False)
    s13_runs = {name: values["s13"] for name, values in result["runs"].items()}
    total_trades = int(sum(summarize_run(run)["trades"] for run in s13_runs.values()))
    final = "PASS" if result["checks"]["overall_pass"] else "FAIL"
    print(f"wrote {result['path']}")
    print(f"S13 trades={total_trades} final={final}")
    for regime in REGIMES:
        metrics = summarize_run(s13_runs[regime])
        print(
            f"{regime}: return={metrics['return']:.4%} dd={metrics['max_drawdown']:.4%} "
            f"trades={int(metrics['trades'])} expectancy={metrics['expectancy']:.2f} "
            f"gross_loss={metrics['gross_loss']:.2f} pf={_fmt_float(metrics['profit_factor'])}"
        )
    rows = pd.DataFrame([item.__dict__ for item in result["leverage_points"]])
    if not rows.empty:
        print("leverage_stats:")
        for regime in REGIMES:
            vals = rows[rows["regime"] == regime]["leverage"]
            if vals.empty:
                continue
            print(
                f"{regime}: n={len(vals)} mean={float(vals.mean()):.4f} min={float(vals.min()):.4f} "
                f"median={float(vals.median()):.4f} max={float(vals.max()):.4f}"
            )


if __name__ == "__main__":
    main()
