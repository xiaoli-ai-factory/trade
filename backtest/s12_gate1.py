"""S12 cross-asset ETF risk parity Gate1 runner."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Any

import numpy as np
import pandas as pd

from backtest.constraints import CostConfig
from backtest.engine import REPORT_DIR, BacktestRun, _fmt_float, _fmt_pct, _load_yaml, summarize_run
from backtest.s9_gate1 import (
    _common_dates,
    _effective_span,
    _gate_checks,
    _gate_table,
    _insample_oos_table,
    _month_end_dates,
    _random_weight_signal,
    _sixty_forty_signal,
    _single_etf_buy_hold_signal,
    _summary_table,
    _effective_span_table,
    _equal_weight_signal,
    run_monthly_backtest,
)
from data.akshare_source import get_etf_daily
from strategies.s9_risk_parity import S9RiskParityStrategy


REGIMES = ("bull", "bear", "range", "oos")


@dataclass(frozen=True)
class S12DataStatus:
    symbol: str
    name: str
    asset_class: str
    source: str
    earliest: date | None
    latest: date | None
    rows: int
    covers: dict[str, bool]
    note: str


def _strategy_cfg() -> dict[str, Any]:
    return _load_yaml("strategy_addon.yaml")["s12_global_risk_parity"].copy()


def _gate_cfg() -> dict[str, Any]:
    return _load_yaml("backtest.yaml")["gate1"]


def _regime_cfg() -> dict[str, Any]:
    return _load_yaml("backtest.yaml")["regimes"]


def _parse_date(value: str | date | pd.Timestamp) -> date:
    if isinstance(value, pd.Timestamp):
        return value.date()
    if isinstance(value, date):
        return value
    return pd.Timestamp(str(value)).date()


def _normalize_frame(frame: pd.DataFrame) -> pd.DataFrame:
    out = frame.copy()
    if out.empty:
        return out
    out["date"] = pd.to_datetime(out["date"], errors="coerce").dt.date
    return out.dropna(subset=["date"]).sort_values("date").reset_index(drop=True)


def _regime_covers(frame: pd.DataFrame) -> dict[str, bool]:
    covers: dict[str, bool] = {}
    if frame.empty:
        return {item: False for item in REGIMES}
    earliest = min(frame["date"].tolist())
    latest = max(frame["date"].tolist())
    for name, span in _regime_cfg().items():
        start = _parse_date(span["start"])
        end = _parse_date(span["end"])
        rows = frame[(frame["date"] >= start) & (frame["date"] <= end)]
        covers[name] = (not rows.empty) and earliest <= start and latest >= end
    return covers


def _data_status(symbol: str, name: str, asset_class: str, frame: pd.DataFrame, note: str = "") -> S12DataStatus:
    if frame.empty:
        return S12DataStatus(symbol, name, asset_class, "", None, None, 0, {item: False for item in REGIMES}, note)
    source = str(frame["source"].dropna().iloc[-1]) if "source" in frame.columns and frame["source"].notna().any() else ""
    return S12DataStatus(
        symbol=symbol,
        name=name,
        asset_class=asset_class,
        source=source,
        earliest=min(frame["date"].tolist()),
        latest=max(frame["date"].tolist()),
        rows=len(frame),
        covers=_regime_covers(frame),
        note=note,
    )


def load_s12_data(refresh: bool = False) -> tuple[dict[str, pd.DataFrame], list[S12DataStatus], dict[str, Any]]:
    cfg = _strategy_cfg()
    global_end = max(_parse_date(item["end"]) for item in _regime_cfg().values())
    data: dict[str, pd.DataFrame] = {}
    statuses: list[S12DataStatus] = []
    pool: list[dict[str, Any]] = []

    for item in cfg["pool"]:
        symbol = str(item["code"])
        name = str(item["name"])
        asset_class = str(item.get("class", ""))
        try:
            frame = _normalize_frame(get_etf_daily(symbol, start=date(1990, 1, 1), end=global_end, refresh=refresh))
        except Exception as exc:
            statuses.append(_data_status(symbol, name, asset_class, pd.DataFrame(), f"{type(exc).__name__}: {exc}"))
            continue
        if frame.empty:
            statuses.append(_data_status(symbol, name, asset_class, frame, "empty frame"))
            continue
        data[symbol] = frame
        pool.append(dict(item))
        statuses.append(_data_status(symbol, name, asset_class, frame))

    selected_cfg = cfg.copy()
    selected_cfg["pool"] = pool
    return data, statuses, selected_cfg


def _data_status_table(statuses: list[S12DataStatus]) -> str:
    lines = [
        "| code | name | class | 成功的数据源 | earliest | latest | rows | covers_regimes(bull/bear/range/oos) | note |",
        "|---|---|---|---|---:|---:|---:|---|---|",
    ]
    for item in statuses:
        covers = "/".join("yes" if item.covers.get(name) else "no" for name in REGIMES)
        lines.append(
            f"| {item.symbol} | {item.name} | {item.asset_class} | {item.source or 'NA'} | "
            f"{item.earliest or 'NA'} | {item.latest or 'NA'} | {item.rows} | {covers} | {item.note} |"
        )
    return "\n".join(lines)


def _final_pool_table(cfg: dict[str, Any]) -> str:
    lines = ["| code | name | class |", "|---|---|---|"]
    for item in cfg["pool"]:
        lines.append(f"| {item['code']} | {item['name']} | {item.get('class', '')} |")
    return "\n".join(lines)


def _comparison_table(regime_runs: dict[str, BacktestRun]) -> str:
    s12_m = summarize_run(regime_runs["s12"])
    equal_m = summarize_run(regime_runs["equal_weight_monthly"])
    sixty_m = summarize_run(regime_runs["sixty_forty_monthly"])
    hs300_m = summarize_run(regime_runs["hs300_buy_hold"])
    random_m = summarize_run(regime_runs["random_weight_monthly"])
    rows = [
        ("return", s12_m["return"], equal_m["return"], sixty_m["return"], hs300_m["return"], random_m["return"], True),
        ("max_drawdown", s12_m["max_drawdown"], equal_m["max_drawdown"], sixty_m["max_drawdown"], hs300_m["max_drawdown"], random_m["max_drawdown"], True),
        ("trades", s12_m["trades"], equal_m["trades"], sixty_m["trades"], hs300_m["trades"], random_m["trades"], False),
        ("fee_ratio", s12_m["fee_ratio"], equal_m["fee_ratio"], sixty_m["fee_ratio"], hs300_m["fee_ratio"], random_m["fee_ratio"], True),
    ]
    lines = [
        "| metric | S12 | equal_weight_monthly | 60_40_monthly | HS300ETF_BH | random_weight_monthly |",
        "|---|---:|---:|---:|---:|---:|",
    ]
    for metric, s12_v, equal_v, sixty_v, hs300_v, random_v, pct in rows:
        fmt = _fmt_pct if pct else _fmt_float
        lines.append(f"| {metric} | {fmt(s12_v)} | {fmt(equal_v)} | {fmt(sixty_v)} | {fmt(hs300_v)} | {fmt(random_v)} |")
    return "\n".join(lines)


def render_report(
    runs: dict[str, dict[str, BacktestRun]],
    checks: dict[str, Any],
    statuses: list[S12DataStatus],
    cfg: dict[str, Any],
    spans: dict[str, Any],
) -> str:
    final = "PASS" if checks["overall_pass"] else "FAIL"
    s12_runs = {name: values["s12"] for name, values in runs.items()}
    lines = [
        "# S12 Cross-Asset Risk Parity Gate1 Report",
        "",
        f"规则：月末 D 收盘后，对实际可得 ETF 池过去 lookback_vol_days={cfg['lookback_vol_days']} 个日日收益计算标准差 sigma_i，目标权重 w_i=(1/sigma_i)/sum(1/sigma_j)，下月首个共同交易日开盘调仓。",
        "PIT：复用 S9RiskParityStrategy，target_weights/generate_signals 断言输入 data.date<=as_of_date；OOS 未用于调参。",
        "",
        "## 数据可得性",
        _data_status_table(statuses),
        "",
        "## 最终池配置",
        _final_pool_table(cfg),
        "",
        "## regime 实际可得区间",
        _effective_span_table(spans),
        "",
        "## S12 分段关键指标",
        _summary_table(s12_runs),
        "",
        "## in-sample vs OOS 差异",
        _insample_oos_table(s12_runs),
        "",
        "## 对照组 ratio 表",
    ]
    for regime in REGIMES:
        lines.extend([f"### {regime}", _comparison_table(runs[regime]), ""])
    lines.extend(
        [
            "对照组定义：实际可得池等权月度再平衡、60/40(510300/国债ETF)月度再平衡、510300ETF 买入持有、随机权重月度再平衡。",
            "",
            "## Gate1 判定表",
            _gate_table(checks, _gate_cfg()),
            "",
            "## flag/参数调查记录",
            "- 数据池使用实际可得标的，未补造或外推价格。",
            f"- 固定使用 strategy_addon.yaml 的 lookback_vol_days={cfg['lookback_vol_days']}，未因结果修改 lookback 或资产池。",
            "- OOS 只在规则、数据源 fallback 和资产池固定后用于最终裁决。",
            "- 未修改成本、滑点、regime 或 Gate1 阈值。",
            "",
            f"最终判定：{final}，按低频显著性原则。",
        ]
    )
    return "\n".join(lines) + "\n"


def run(refresh: bool = False) -> dict[str, Any]:
    cost_config = CostConfig.from_mapping(_load_yaml("cost.yaml"))
    data, statuses, cfg = load_s12_data(refresh=refresh)
    if len(data) < 5:
        raise RuntimeError(f"S12 requires at least 5 accessible ETFs for stage2, got {len(data)}")

    calendar_dates = _common_dates(data)
    if not calendar_dates:
        raise RuntimeError("No common S12 ETF calendar dates")
    month_ends = _month_end_dates(calendar_dates)
    spans = {name: _effective_span(name, calendar_dates) for name in REGIMES}
    strategy = S9RiskParityStrategy(cfg)
    symbols = strategy.symbols
    bond_symbol = next(str(item["code"]) for item in cfg["pool"] if str(item["code"]).startswith("511"))

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
        all_runs[regime] = {
            "s12": run_monthly_backtest("s12", regime, *common_args, strategy.generate_signals, cost_config),
            "equal_weight_monthly": run_monthly_backtest("equal_weight_monthly", regime, *common_args, _equal_weight_signal(symbols), cost_config),
            "sixty_forty_monthly": run_monthly_backtest("sixty_forty_monthly", regime, *common_args, _sixty_forty_signal(symbols, bond_symbol), cost_config),
            "hs300_buy_hold": run_monthly_backtest("hs300_buy_hold", regime, *common_args, _single_etf_buy_hold_signal("510300"), cost_config),
            "random_weight_monthly": run_monthly_backtest("random_weight_monthly", regime, *common_args, _random_weight_signal(symbols), cost_config),
        }

    s12_runs = {name: values["s12"] for name, values in all_runs.items()}
    checks = _gate_checks(s12_runs, _gate_cfg())
    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    path = REPORT_DIR / "s12_global_risk_parity_gate1.md"
    path.write_text(render_report(all_runs, checks, statuses, cfg, spans), encoding="utf-8")
    return {"path": path, "runs": all_runs, "checks": checks, "statuses": statuses, "cfg": cfg, "spans": spans}


def main() -> None:
    result = run(refresh=False)
    s12_runs = {name: values["s12"] for name, values in result["runs"].items()}
    total_trades = int(sum(summarize_run(run)["trades"] for run in s12_runs.values()))
    final = "PASS" if result["checks"]["overall_pass"] else "FAIL"
    print(f"wrote {result['path']}")
    print(f"S12 data_check={len(result['cfg']['pool'])}/7 trades={total_trades} final={final}")
    for item in result["statuses"]:
        print(f"{item.symbol}: source={item.source or 'NA'} earliest={item.earliest or 'NA'} latest={item.latest or 'NA'} rows={item.rows} note={item.note}")
    for regime in REGIMES:
        metrics = summarize_run(s12_runs[regime])
        print(
            f"{regime}: return={metrics['return']:.4%} dd={metrics['max_drawdown']:.4%} "
            f"trades={int(metrics['trades'])} expectancy={metrics['expectancy']:.2f} "
            f"pf={_fmt_float(metrics['profit_factor'])}"
        )


if __name__ == "__main__":
    main()
