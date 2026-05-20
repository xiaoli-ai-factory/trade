"""Event-driven backtest engine.

The original M2 placeholder pipeline is kept for review continuity. The S3
path below runs the real daily momentum Gate1 without touching S1/S2.
"""

from __future__ import annotations

import argparse
import math
import re
from dataclasses import asdict, dataclass, replace
from datetime import date, timedelta
from pathlib import Path
from typing import Any, Callable

import numpy as np
import pandas as pd
import yaml

from backtest.constraints import (
    CostConfig,
    ExecutionResult,
    MarketBar,
    Order,
    Position,
    apply_execution,
    mark_sellable,
    match_order,
    trade_cost,
    slippage_price,
)
from data.akshare_source import get_etf_daily, get_index_daily
from strategies.s3b_trend import S3BTrendStrategy
from strategies.s3_momentum import S3_ASSET_POOL, S3MomentumStrategy


PROJECT_ROOT = Path(__file__).resolve().parents[1]
CONFIG_DIR = PROJECT_ROOT / "configs"
REPORT_DIR = PROJECT_ROOT / "reports"
INITIAL_CASH = 1_000_000.0
LOT_SIZE = 100
RANDOM_SEED = 20260519


@dataclass(frozen=True)
class TradeRecord:
    symbol: str
    entry_date: date
    exit_date: date
    quantity: int
    pnl: float
    entry_basis: float
    exit_cash: float


@dataclass(frozen=True)
class BacktestRun:
    name: str
    regime: str
    start: date
    end: date
    initial_cash: float
    final_nav: float
    total_return: float
    max_drawdown: float
    trades: tuple[TradeRecord, ...]
    filled_orders: tuple[dict[str, Any], ...]
    rejected_orders: tuple[dict[str, Any], ...]
    events: tuple[dict[str, Any], ...]
    nav_curve: pd.DataFrame


def _load_yaml(name: str) -> dict[str, Any]:
    with (CONFIG_DIR / name).open("r", encoding="utf-8") as fh:
        return yaml.safe_load(fh) or {}


def _parse_date(value: str | date | pd.Timestamp) -> date:
    if isinstance(value, pd.Timestamp):
        return value.date()
    if isinstance(value, date):
        return value
    return pd.Timestamp(str(value)).date()


def _fmt_pct(value: float | None) -> str:
    if value is None or pd.isna(value):
        return "NA"
    return f"{value:.2%}"


def _fmt_float(value: float | None) -> str:
    if value is None or pd.isna(value):
        return "NA"
    if math.isinf(value):
        return "inf"
    return f"{value:.4f}"


def _metric_ratio(left: float, right: float) -> float | None:
    if pd.isna(left) or pd.isna(right) or abs(right) < 1e-12:
        return None
    return left / right


def _ratio_note(*ratios: float | None) -> str:
    clean = [abs(item) for item in ratios if item is not None and not pd.isna(item) and not math.isinf(item)]
    if any(item > 2.0 for item in clean):
        return "ratio>2x，需调查"
    return ""


def _placeholder_days(start: date, end: date, count: int = 5) -> list[date]:
    dates = pd.bdate_range(start=start, end=end).date.tolist()
    return dates[:count]


def _placeholder_bar(symbol: str, trade_date: date, index: int) -> MarketBar:
    prices = [10.00, 10.20, 10.10, 9.09, 9.30]
    opens = prices[index]
    limit_down = opens if index == 3 else round(opens * 0.90, 2)
    return MarketBar(
        symbol=symbol,
        date=trade_date,
        open=opens,
        limit_up_price=round(opens * 1.10, 2),
        limit_down_price=limit_down,
        is_suspended=False,
    )


def _placeholder_orders(symbol: str, trade_date: date, index: int) -> list[Order]:
    if index in {0, 2}:
        return [Order(symbol=symbol, side="buy", quantity=100, submitted_date=trade_date)]
    if index in {1, 3, 4}:
        return [Order(symbol=symbol, side="sell", quantity=100, submitted_date=trade_date)]
    return []


def _position_for_symbol(positions: tuple[Position, ...], symbol: str) -> Position | None:
    for item in positions:
        if item.symbol == symbol:
            return item
    return None


def run_placeholder_pipeline(strategy: str, regime: str) -> dict[str, Any]:
    backtest_cfg = _load_yaml("backtest.yaml")
    cost_cfg = CostConfig.from_mapping(_load_yaml("cost.yaml"))
    regime_cfg = backtest_cfg["regimes"][regime]
    days = _placeholder_days(_parse_date(regime_cfg["start"]), _parse_date(regime_cfg["end"]))
    symbol = "000001"
    cash = 100000.0
    positions: tuple[Position, ...] = ()
    executions = []
    events = []

    for index, trade_date in enumerate(days):
        positions = mark_sellable(positions, trade_date)
        bar = _placeholder_bar(symbol, trade_date, index)
        for order in _placeholder_orders(symbol, trade_date, index):
            position = _position_for_symbol(positions, order.symbol) if order.side == "sell" else None
            result = match_order(order, bar, cost_cfg, position=position)
            cash += result.cash_delta
            positions = apply_execution(positions, result, trade_date)
            executions.append(
                {
                    "date": trade_date.isoformat(),
                    "order": asdict(order),
                    "status": result.status,
                    "reason": result.reason,
                    "quantity": result.quantity,
                    "base_price": result.base_price,
                    "fill_price": result.fill_price,
                    "amount": result.amount,
                    "cost": result.cost,
                    "cash_delta": result.cash_delta,
                }
            )
            events.extend(result.events)

    return {
        "strategy": strategy,
        "regime": regime,
        "cash": cash,
        "positions": [asdict(item) for item in positions],
        "executions": executions,
        "events": events,
        "gate1": backtest_cfg["gate1"],
    }


def _gate1_placeholder_table(gate1: dict[str, Any], regime: str) -> str:
    rows = [
        ("expectancy_after_cost", gate1["expectancy_after_cost_gt"], "M3未执行", "N/A"),
        ("profit_factor", gate1["profit_factor_min"], "M3未执行", "N/A"),
        ("max_drawdown", gate1["max_drawdown_max"], "M3未执行", "N/A"),
        ("trades", gate1["min_trades"], "M3未执行", "N/A"),
    ]
    lines = ["| regime | metric | actual | threshold | result |", "|---|---:|---:|---:|---|"]
    for metric, threshold, actual, result in rows:
        lines.append(f"| {regime} | {metric} | {actual} | {threshold} | {result} |")
    return "\n".join(lines)


def render_placeholder_report(result: dict[str, Any]) -> str:
    filled = [item for item in result["executions"] if item["status"] == "filled"]
    rejected = [item for item in result["executions"] if item["status"] == "rejected"]
    total_cost = sum(float(item["cost"]) for item in filled)
    traded_amount = sum(float(item["amount"]) for item in filled)
    fee_ratio = total_cost / traded_amount if traded_amount else 0.0
    lines = [
        f"# {result['strategy']} / {result['regime']} M2 Pipeline Report",
        "",
        "本报告只验证 M2 事件管线和约束复用；未执行 M3 策略回测，不能用于收益结论。",
        "",
        "## 对照组 ratio 表",
        "| metric | strategy | equal_weight_buy_hold | random_pick | ratio_note |",
        "|---|---:|---:|---:|---|",
        "| return | M3未执行 | M3未执行 | M3未执行 | N/A |",
        "| max_drawdown | M3未执行 | M3未执行 | M3未执行 | N/A |",
        f"| trades | {len(filled)} | M3未执行 | M3未执行 | 占位订单 |",
        f"| fee_ratio | {fee_ratio:.6f} | M3未执行 | M3未执行 | 占位订单 |",
        "",
        "## 反假设列表",
        "- 未来函数：M2 只用同一交易日占位开盘 bar 撮合，未产生策略信号结论；M3 需逐策略复查 as_of。",
        "- 牛市 beta：本轮未计算收益，无法支持或反驳 beta 解释；M3 需用对照组证伪。",
        "",
        "## flag/参数调查记录",
        "- 本轮未修改 `configs/strategy.yaml` 参数。",
        "- 本轮未用 oos 调参；若 CLI 指向 oos，也只生成占位管线报告。",
        "",
        "## Gate1 判定表",
        _gate1_placeholder_table(result["gate1"], result["regime"]),
        "",
        "## M2 事件摘要",
        f"- filled_orders: {len(filled)}",
        f"- rejected_orders: {len(rejected)}",
        f"- forced_hold_events: {len(result['events'])}",
        f"- ending_cash: {result['cash']:.2f}",
    ]
    return "\n".join(lines) + "\n"


def write_placeholder_report(result: dict[str, Any]) -> Path:
    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    path = REPORT_DIR / f"{result['strategy']}_{result['regime']}.md"
    path.write_text(render_placeholder_report(result), encoding="utf-8")
    return path


def _warmup_start(start: date) -> date:
    return start - timedelta(days=220)


def load_s3_data(start: date, end: date, refresh: bool = False, warmup: bool = True) -> dict[str, pd.DataFrame]:
    data: dict[str, pd.DataFrame] = {}
    query_start = _warmup_start(start) if warmup else start
    for asset in S3_ASSET_POOL:
        getter = get_index_daily if asset.kind == "index" else get_etf_daily
        frame = getter(asset.symbol, start=query_start, end=end, refresh=refresh)
        if frame.empty:
            raise RuntimeError(f"S3 data unavailable for {asset.symbol}")
        frame = frame.copy()
        frame["date"] = pd.to_datetime(frame["date"], errors="coerce").dt.date
        frame = frame.dropna(subset=["date"]).sort_values("date").reset_index(drop=True)
        data[asset.symbol] = frame
    return data


def load_s3_gate1_data(refresh: bool = False) -> tuple[dict[str, pd.DataFrame], str]:
    backtest_cfg = _load_yaml("backtest.yaml")
    global_start = min(_parse_date(span["start"]) for span in backtest_cfg["regimes"].values())
    global_end = max(_parse_date(span["end"]) for span in backtest_cfg["regimes"].values())
    full = load_s3_data(global_start, global_end, refresh=refresh, warmup=False)
    bull_end = _parse_date(backtest_cfg["regimes"]["bull"]["end"])
    try:
        warm = load_s3_data(global_start, bull_end, refresh=refresh, warmup=True)
    except Exception as exc:
        return full, f"warmup_prefix_unavailable:{type(exc).__name__}"

    merged = {}
    for symbol, frame in full.items():
        prefix = warm.get(symbol, pd.DataFrame())
        if prefix.empty:
            merged[symbol] = frame
            continue
        combined = pd.concat([prefix[prefix["date"] < global_start], frame], ignore_index=True)
        combined = combined.drop_duplicates("date", keep="last").sort_values("date").reset_index(drop=True)
        merged[symbol] = combined
    return merged, "warmup_prefix_loaded"


def load_s3b_gate1_data(refresh: bool = False) -> tuple[dict[str, pd.DataFrame], str]:
    backtest_cfg = _load_yaml("backtest.yaml")
    strategy_cfg = _load_yaml("strategy.yaml")["s3b_trend"]
    global_start = min(_parse_date(span["start"]) for span in backtest_cfg["regimes"].values())
    global_end = max(_parse_date(span["end"]) for span in backtest_cfg["regimes"].values())
    ma_len = int(strategy_cfg["ma_len"])
    asset = str(strategy_cfg["asset"])
    query_start = min(global_start - timedelta(days=max(ma_len * 3, 260)), date(global_start.year - 2, 1, 1))
    try:
        frame = get_index_daily(asset, start=query_start, end=global_end, refresh=refresh)
        note = f"asset={asset}; warmup_start={query_start}"
    except Exception as exc:
        frame = get_index_daily(asset, start=global_start, end=global_end, refresh=False)
        note = f"asset={asset}; warmup_prefix_unavailable:{type(exc).__name__}; start={global_start}"
    if frame.empty:
        raise RuntimeError(f"S3b data unavailable for {asset}")
    frame = frame.copy()
    frame["date"] = pd.to_datetime(frame["date"], errors="coerce").dt.date
    frame = frame.dropna(subset=["date"]).sort_values("date").reset_index(drop=True)
    return {asset: frame}, note


def _trading_dates(data: dict[str, pd.DataFrame], start: date, end: date) -> list[date]:
    values = set()
    for frame in data.values():
        values.update(item for item in frame["date"].tolist() if start <= item <= end)
    return sorted(values)


def _slice_data(data: dict[str, pd.DataFrame], as_of_date: date) -> dict[str, pd.DataFrame]:
    return {symbol: frame[frame["date"] <= as_of_date].copy() for symbol, frame in data.items()}


def _row_on(frame: pd.DataFrame, trade_date: date) -> pd.Series | None:
    row = frame[frame["date"] == trade_date]
    if row.empty:
        return None
    return row.iloc[-1]


def _last_close(data: dict[str, pd.DataFrame], symbol: str, as_of_date: date) -> float | None:
    frame = data[symbol]
    rows = frame[frame["date"] <= as_of_date]
    if rows.empty:
        return None
    close = rows.iloc[-1]["close"]
    if pd.isna(close):
        return None
    return float(close)


def _none_if_nan(value: Any) -> float | None:
    if value is None or pd.isna(value):
        return None
    return float(value)


def _market_bar(data: dict[str, pd.DataFrame], symbol: str, trade_date: date) -> MarketBar:
    row = _row_on(data[symbol], trade_date)
    if row is None:
        last = _last_close(data, symbol, trade_date)
        return MarketBar(symbol=symbol, date=trade_date, open=float(last or 0.0), is_suspended=True)
    return MarketBar(
        symbol=symbol,
        date=trade_date,
        open=float(row["open"]),
        limit_up_price=_none_if_nan(row.get("limit_up_price")),
        limit_down_price=_none_if_nan(row.get("limit_down_price")),
        is_suspended=bool(row.get("is_suspended", False)),
    )


def _mark_nav(cash: float, positions: tuple[Position, ...], data: dict[str, pd.DataFrame], as_of_date: date) -> float:
    nav = cash
    for item in positions:
        close = _last_close(data, item.symbol, as_of_date)
        if close is not None:
            nav += item.quantity * close
    return nav


def _max_drawdown(nav_curve: pd.DataFrame) -> float:
    if nav_curve.empty:
        return 0.0
    nav = pd.to_numeric(nav_curve["nav"], errors="coerce")
    peak = nav.cummax()
    drawdown = nav / peak - 1.0
    return abs(float(drawdown.min())) if not drawdown.empty else 0.0


def _trade_metrics(trades: tuple[TradeRecord, ...]) -> dict[str, float]:
    pnls = [item.pnl for item in trades]
    wins = [item for item in pnls if item > 0]
    losses = [item for item in pnls if item < 0]
    gross_profit = float(sum(wins))
    gross_loss = float(abs(sum(losses)))
    profit_factor = math.inf if gross_profit > 0 and gross_loss == 0 else gross_profit / gross_loss if gross_loss > 0 else 0.0
    return {
        "trades": float(len(pnls)),
        "expectancy": float(np.mean(pnls)) if pnls else 0.0,
        "profit_factor": profit_factor,
        "win_rate": float(len(wins) / len(pnls)) if pnls else 0.0,
        "gross_profit": gross_profit,
        "gross_loss": gross_loss,
    }


def summarize_run(run: BacktestRun) -> dict[str, float]:
    metrics = _trade_metrics(run.trades)
    amount = sum(float(item["amount"]) for item in run.filled_orders)
    cost = sum(float(item["cost"]) for item in run.filled_orders)
    metrics.update(
        {
            "return": run.total_return,
            "max_drawdown": run.max_drawdown,
            "filled_orders": float(len(run.filled_orders)),
            "fee_ratio": cost / amount if amount else 0.0,
            "total_cost": cost,
            "traded_amount": amount,
        }
    )
    return metrics


def _apply_basis_on_fill(
    basis: dict[str, float],
    result: ExecutionResult,
    position_before: Position | None,
    trade_date: date,
    trades: list[TradeRecord],
) -> None:
    if result.status != "filled":
        return
    symbol = result.order.symbol
    if result.order.side == "buy":
        basis[symbol] = basis.get(symbol, 0.0) + result.amount + result.cost
        return
    if position_before is None or position_before.quantity <= 0:
        return
    existing_basis = basis.get(symbol, 0.0)
    sold_quantity = min(result.quantity, position_before.quantity)
    basis_used = existing_basis * (sold_quantity / position_before.quantity)
    exit_cash = result.amount - result.cost
    trades.append(
        TradeRecord(
            symbol=symbol,
            entry_date=position_before.buy_date,
            exit_date=trade_date,
            quantity=sold_quantity,
            pnl=exit_cash - basis_used,
            entry_basis=basis_used,
            exit_cash=exit_cash,
        )
    )
    remaining_basis = existing_basis - basis_used
    if position_before.quantity == sold_quantity:
        basis.pop(symbol, None)
    else:
        basis[symbol] = remaining_basis


def _record_execution(result: ExecutionResult, trade_date: date) -> dict[str, Any]:
    return {
        "date": trade_date.isoformat(),
        "symbol": result.order.symbol,
        "side": result.order.side,
        "status": result.status,
        "reason": result.reason,
        "quantity": result.quantity,
        "base_price": result.base_price,
        "fill_price": result.fill_price,
        "amount": result.amount,
        "cost": result.cost,
        "cash_delta": result.cash_delta,
    }


def _affordable_quantity(desired_quantity: int, base_price: float, cash: float, cost_config: CostConfig) -> int:
    quantity = desired_quantity - desired_quantity % LOT_SIZE
    while quantity > 0:
        fill_price = slippage_price(base_price, "buy", cost_config)
        amount = fill_price * quantity
        if amount + trade_cost(amount, "buy", cost_config) <= cash + 1e-9:
            return quantity
        quantity -= LOT_SIZE
    return 0


SignalFunc = Callable[[date, dict[str, Any]], list[Order]]


def _run_signal_backtest(
    name: str,
    regime: str,
    start: date,
    end: date,
    data: dict[str, pd.DataFrame],
    signal_func: SignalFunc,
    cost_config: CostConfig,
) -> BacktestRun:
    dates = _trading_dates(data, start, end)
    if len(dates) < 2:
        raise RuntimeError(f"Not enough S3 dates for {regime}")
    cash = INITIAL_CASH
    positions: tuple[Position, ...] = ()
    basis: dict[str, float] = {}
    trades: list[TradeRecord] = []
    filled_orders: list[dict[str, Any]] = []
    rejected_orders: list[dict[str, Any]] = []
    events: list[dict[str, Any]] = []
    nav_rows = [{"date": dates[0].isoformat(), "nav": cash}]

    for signal_date, trade_date in zip(dates[:-1], dates[1:], strict=False):
        positions = mark_sellable(positions, trade_date)
        ctx = {
            "data": _slice_data(data, signal_date),
            "positions": positions,
            "cash": cash,
            "nav": _mark_nav(cash, positions, data, signal_date),
            "lot_size": LOT_SIZE,
        }
        orders = signal_func(signal_date, ctx)
        orders = sorted(orders, key=lambda item: 0 if item.side == "sell" else 1)
        for order in orders:
            bar = _market_bar(data, order.symbol, trade_date)
            order_to_match = order
            if order.side == "buy":
                affordable = _affordable_quantity(order.quantity, bar.open, cash, cost_config)
                if affordable <= 0:
                    continue
                order_to_match = replace(order, quantity=affordable)
            position_before = _position_for_symbol(positions, order_to_match.symbol)
            position_arg = position_before if order_to_match.side == "sell" else None
            result = match_order(order_to_match, bar, cost_config, position=position_arg)
            if result.status == "filled":
                _apply_basis_on_fill(basis, result, position_before, trade_date, trades)
                cash += result.cash_delta
                positions = apply_execution(positions, result, trade_date)
                filled_orders.append(_record_execution(result, trade_date))
            else:
                rejected_orders.append(_record_execution(result, trade_date))
            events.extend(result.events)
        nav_rows.append({"date": trade_date.isoformat(), "nav": _mark_nav(cash, positions, data, trade_date)})

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
    final_nav = float(final_nav)
    return BacktestRun(
        name=name,
        regime=regime,
        start=start,
        end=end,
        initial_cash=INITIAL_CASH,
        final_nav=final_nav,
        total_return=final_nav / INITIAL_CASH - 1.0,
        max_drawdown=_max_drawdown(nav_curve),
        trades=tuple(trades),
        filled_orders=tuple(filled_orders),
        rejected_orders=tuple(rejected_orders),
        events=tuple(events),
        nav_curve=nav_curve,
    )


def run_s3_regime(
    regime: str,
    refresh: bool = False,
    config_override: dict[str, Any] | None = None,
    data: dict[str, pd.DataFrame] | None = None,
) -> dict[str, BacktestRun]:
    backtest_cfg = _load_yaml("backtest.yaml")
    strategy_cfg = _load_yaml("strategy.yaml")["s3_momentum"].copy()
    if config_override:
        strategy_cfg.update(config_override)
    span = backtest_cfg["regimes"][regime]
    start = _parse_date(span["start"])
    end = _parse_date(span["end"])
    if data is None:
        data = load_s3_data(start, end, refresh=refresh)
    strategy = S3MomentumStrategy(strategy_cfg)
    cost_config = CostConfig.from_mapping(_load_yaml("cost.yaml"))
    rng = np.random.default_rng(RANDOM_SEED + sum(ord(ch) for ch in regime))

    return {
        "s3": _run_signal_backtest(
            "s3",
            regime,
            start,
            end,
            data,
            strategy.generate_signals,
            cost_config,
        ),
        "equal_weight": _run_signal_backtest(
            "equal_weight_buy_hold",
            regime,
            start,
            end,
            data,
            _equal_weight_buy_hold_signal,
            cost_config,
        ),
        "random": _run_signal_backtest(
            "random_rotation",
            regime,
            start,
            end,
            data,
            _random_rotation_signal_factory(int(strategy_cfg["top_k"]), rng),
            cost_config,
        ),
    }


def _equal_weight_buy_hold_signal(as_of_date: date, ctx: dict[str, Any]) -> list[Order]:
    positions: tuple[Position, ...] = tuple(ctx.get("positions", ()))
    if positions:
        return []
    data: dict[str, pd.DataFrame] = ctx["data"]
    nav = float(ctx["nav"])
    target_value = nav / len(S3_ASSET_POOL)
    orders = []
    for asset in S3_ASSET_POOL:
        frame = data.get(asset.symbol)
        if frame is None or frame.empty:
            continue
        close = float(frame.iloc[-1]["close"])
        quantity = int(math.floor((target_value / close) / LOT_SIZE) * LOT_SIZE)
        if quantity > 0:
            orders.append(Order(symbol=asset.symbol, side="buy", quantity=quantity, submitted_date=as_of_date))
    return orders


def _random_rotation_signal_factory(top_k: int, rng: np.random.Generator) -> SignalFunc:
    def _signal(as_of_date: date, ctx: dict[str, Any]) -> list[Order]:
        data: dict[str, pd.DataFrame] = ctx["data"]
        positions: tuple[Position, ...] = tuple(ctx.get("positions", ()))
        available = [asset.symbol for asset in S3_ASSET_POOL if asset.symbol in data and not data[asset.symbol].empty]
        if not available:
            return []
        selected = set(rng.choice(available, size=min(top_k, len(available)), replace=False).tolist())
        nav = float(ctx["nav"])
        target_value = nav / len(selected)
        current_qty = {item.symbol: item.quantity for item in positions if item.quantity > 0}
        orders: list[Order] = []
        for symbol, quantity in sorted(current_qty.items()):
            if symbol not in selected:
                orders.append(Order(symbol=symbol, side="sell", quantity=quantity, submitted_date=as_of_date))
        for symbol in sorted(selected):
            close = float(data[symbol].iloc[-1]["close"])
            target_quantity = int(math.floor((target_value / close) / LOT_SIZE) * LOT_SIZE)
            diff = target_quantity - current_qty.get(symbol, 0)
            if diff > 0:
                orders.append(Order(symbol=symbol, side="buy", quantity=diff, submitted_date=as_of_date))
            elif diff < 0:
                orders.append(Order(symbol=symbol, side="sell", quantity=abs(diff), submitted_date=as_of_date))
        return sorted(orders, key=lambda item: 0 if item.side == "sell" else 1)

    return _signal


def _merged_trade_metrics(runs: dict[str, BacktestRun]) -> dict[str, float]:
    trades = tuple(item for run in runs.values() for item in run.trades)
    return _trade_metrics(trades)


def _gate_checks(runs: dict[str, BacktestRun], gate1: dict[str, Any]) -> dict[str, Any]:
    summaries = {name: summarize_run(run) for name, run in runs.items()}
    merged = _merged_trade_metrics(runs)
    a_rows = []
    a_pass = True
    for name in ("bull", "bear", "range"):
        metrics = summaries[name]
        checks = {
            "expectancy": metrics["expectancy"] > float(gate1["expectancy_after_cost_gt"]),
            "profit_factor": metrics["profit_factor"] >= float(gate1["profit_factor_min"]),
            "max_drawdown": metrics["max_drawdown"] <= float(gate1["max_drawdown_max"]),
        }
        passed = all(checks.values())
        a_pass = a_pass and passed
        a_rows.append((name, metrics, checks, passed))
    b_checks = {
        "trades": merged["trades"] >= float(gate1["min_trades"]),
        "expectancy": merged["expectancy"] > float(gate1["expectancy_after_cost_gt"]),
        "profit_factor": merged["profit_factor"] >= float(gate1["profit_factor_min"]),
    }
    oos_metrics = summaries["oos"]
    c_checks = {
        "expectancy": oos_metrics["expectancy"] > float(gate1["expectancy_after_cost_gt"]),
        "profit_factor": oos_metrics["profit_factor"] >= float(gate1["profit_factor_min"]),
        "max_drawdown": oos_metrics["max_drawdown"] <= float(gate1["max_drawdown_max"]),
        "trades": oos_metrics["trades"] >= float(gate1["oos_min_trades"]),
    }
    return {
        "summaries": summaries,
        "merged": merged,
        "a_rows": a_rows,
        "a_pass": a_pass,
        "b_checks": b_checks,
        "b_pass": all(b_checks.values()),
        "c_checks": c_checks,
        "c_pass": all(c_checks.values()),
        "overall_pass": a_pass and all(b_checks.values()) and all(c_checks.values()),
    }


def _comparison_table(s3: BacktestRun, equal_weight: BacktestRun, random_run: BacktestRun) -> str:
    s3_m = summarize_run(s3)
    ew_m = summarize_run(equal_weight)
    rnd_m = summarize_run(random_run)
    rows = [
        ("return", s3_m["return"], ew_m["return"], rnd_m["return"], True),
        ("max_drawdown", s3_m["max_drawdown"], ew_m["max_drawdown"], rnd_m["max_drawdown"], True),
        ("trades", s3_m["trades"], ew_m["trades"], rnd_m["trades"], False),
        ("fee_ratio", s3_m["fee_ratio"], ew_m["fee_ratio"], rnd_m["fee_ratio"], False),
    ]
    lines = [
        "| metric | S3 | equal_weight_buy_hold | random_rotation | S3/EW | S3/random | note |",
        "|---|---:|---:|---:|---:|---:|---|",
    ]
    for metric, s3_v, ew_v, rnd_v, pct in rows:
        r1 = _metric_ratio(s3_v, ew_v)
        r2 = _metric_ratio(s3_v, rnd_v)
        fmt = _fmt_pct if pct else _fmt_float
        lines.append(
            f"| {metric} | {fmt(s3_v)} | {fmt(ew_v)} | {fmt(rnd_v)} | {_fmt_float(r1)} | {_fmt_float(r2)} | {_ratio_note(r1, r2)} |"
        )
    return "\n".join(lines)


def _run_gate_row(label: str, metric: str, actual: float, threshold: float, passed: bool) -> str:
    return f"| {label} | {metric} | {_fmt_float(actual)} | {_fmt_float(threshold)} | {'PASS' if passed else 'FAIL'} |"


def _gate_row_text(label: str, metric: str, actual: str, threshold: str, result: str) -> str:
    return f"| {label} | {metric} | {actual} | {threshold} | {result} |"


def _gate1_table(checks: dict[str, Any], gate1: dict[str, Any]) -> str:
    lines = ["| group | metric | actual | threshold | result |", "|---|---:|---:|---:|---|"]
    for name, metrics, item_checks, _passed in checks["a_rows"]:
        lines.append(_run_gate_row(f"A/{name}", "expectancy_after_cost", metrics["expectancy"], gate1["expectancy_after_cost_gt"], item_checks["expectancy"]))
        lines.append(_run_gate_row(f"A/{name}", "profit_factor", metrics["profit_factor"], gate1["profit_factor_min"], item_checks["profit_factor"]))
        lines.append(_run_gate_row(f"A/{name}", "max_drawdown", metrics["max_drawdown"], gate1["max_drawdown_max"], item_checks["max_drawdown"]))
    merged = checks["merged"]
    b = checks["b_checks"]
    lines.append(_run_gate_row("B/merged", "trades", merged["trades"], gate1["min_trades"], b["trades"]))
    lines.append(_run_gate_row("B/merged", "expectancy_after_cost", merged["expectancy"], gate1["expectancy_after_cost_gt"], b["expectancy"]))
    lines.append(_run_gate_row("B/merged", "profit_factor", merged["profit_factor"], gate1["profit_factor_min"], b["profit_factor"]))
    oos = checks["summaries"]["oos"]
    c = checks["c_checks"]
    lines.append(_run_gate_row("C/oos", "expectancy_after_cost", oos["expectancy"], gate1["expectancy_after_cost_gt"], c["expectancy"]))
    lines.append(_run_gate_row("C/oos", "profit_factor", oos["profit_factor"], gate1["profit_factor_min"], c["profit_factor"]))
    lines.append(_run_gate_row("C/oos", "max_drawdown", oos["max_drawdown"], gate1["max_drawdown_max"], c["max_drawdown"]))
    lines.append(_run_gate_row("C/oos", "trades", oos["trades"], gate1["oos_min_trades"], c["trades"]))
    lines.append(f"| TOTAL | A+B+C | - | - | {'PASS' if checks['overall_pass'] else 'FAIL'} |")
    return "\n".join(lines)


def _run_summary_table(runs: dict[str, BacktestRun]) -> str:
    lines = [
        "| regime | return | max_drawdown | trades | expectancy_after_cost | profit_factor | win_rate | fee_ratio |",
        "|---|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for name in ("bull", "bear", "range", "oos"):
        metrics = summarize_run(runs[name])
        lines.append(
            f"| {name} | {_fmt_pct(metrics['return'])} | {_fmt_pct(metrics['max_drawdown'])} | {int(metrics['trades'])} | "
            f"{metrics['expectancy']:.2f} | {_fmt_float(metrics['profit_factor'])} | {_fmt_pct(metrics['win_rate'])} | {_fmt_pct(metrics['fee_ratio'])} |"
        )
    return "\n".join(lines)


def _sensitivity_table(data: dict[str, pd.DataFrame]) -> str:
    rows = []
    backtest_cfg = _load_yaml("backtest.yaml")
    cost_config = CostConfig.from_mapping(_load_yaml("cost.yaml"))
    for lookback in (10, 20, 40):
        for top_k in (1, 2, 3):
            trades: list[TradeRecord] = []
            returns = []
            drawdowns = []
            for regime in ("bull", "bear", "range"):
                span = backtest_cfg["regimes"][regime]
                start = _parse_date(span["start"])
                end = _parse_date(span["end"])
                cfg = _load_yaml("strategy.yaml")["s3_momentum"].copy()
                cfg.update({"lookback_days": lookback, "top_k": top_k})
                strategy = S3MomentumStrategy(cfg)
                run = _run_signal_backtest("s3_sensitivity", regime, start, end, data, strategy.generate_signals, cost_config)
                trades.extend(run.trades)
                returns.append(run.total_return)
                drawdowns.append(run.max_drawdown)
            metrics = _trade_metrics(tuple(trades))
            rows.append((lookback, top_k, np.mean(returns), max(drawdowns), metrics))
    lines = [
        "| lookback | top_k | in_sample_avg_return | in_sample_worst_drawdown | trades | expectancy | profit_factor | win_rate |",
        "|---:|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for lookback, top_k, avg_return, worst_dd, metrics in rows:
        lines.append(
            f"| {lookback} | {top_k} | {_fmt_pct(float(avg_return))} | {_fmt_pct(float(worst_dd))} | {int(metrics['trades'])} | "
            f"{metrics['expectancy']:.2f} | {_fmt_float(metrics['profit_factor'])} | {_fmt_pct(metrics['win_rate'])} |"
        )
    return "\n".join(lines)


def render_regime_report(regime_runs: dict[str, BacktestRun], gate1: dict[str, Any]) -> str:
    s3 = regime_runs["s3"]
    metrics = summarize_run(s3)
    lines = [
        f"# S3 Gate1 Regime Report / {s3.regime}",
        "",
        f"区间：{s3.start}..{s3.end}。信号在 D 日收盘后用 ≤D 日线计算，D+1 开盘撮合。",
        "",
        "## 对照组 ratio 表",
        _comparison_table(s3, regime_runs["equal_weight"], regime_runs["random"]),
        "",
        "## 反假设列表",
        f"- 动量只是牛市 beta：本段 S3 return={_fmt_pct(metrics['return'])}，max_drawdown={_fmt_pct(metrics['max_drawdown'])}；需结合总报告 bear 段与等权对照裁决。",
        "- 对 lookback/top_k 过拟合：本段不单独调参，敏感性表只在总报告用 bull/bear/range 计算，未触碰 oos 调参。",
        "- ETF/指数一字板约束：S3 数据 limit_up/down 为 NaN，不触发一字板拒单；偏差方向是略乐观，实盘可能出现买不到/卖不出。",
        "",
        "## flag/参数调查记录",
        "- 本轮未修改 `configs/strategy.yaml` 的 S3 参数。",
        "- 未触碰 oos 调参；本报告只按默认参数评估该区间。",
        "",
        "## Gate1 判定表",
        "| regime | metric | actual | threshold | result |",
        "|---|---:|---:|---:|---|",
        _run_gate_row(s3.regime, "expectancy_after_cost", metrics["expectancy"], gate1["expectancy_after_cost_gt"], metrics["expectancy"] > gate1["expectancy_after_cost_gt"]),
        _run_gate_row(s3.regime, "profit_factor", metrics["profit_factor"], gate1["profit_factor_min"], metrics["profit_factor"] >= gate1["profit_factor_min"]),
        _run_gate_row(s3.regime, "max_drawdown", metrics["max_drawdown"], gate1["max_drawdown_max"], metrics["max_drawdown"] <= gate1["max_drawdown_max"]),
        (
            _run_gate_row(s3.regime, "trades", metrics["trades"], gate1["oos_min_trades"], metrics["trades"] >= gate1["oos_min_trades"])
            if s3.regime == "oos"
            else _gate_row_text(s3.regime, "trades", _fmt_float(metrics["trades"]), "A不要求", "N/A")
        ),
        "",
        "## 交易摘要",
        f"- filled_orders: {len(s3.filled_orders)}",
        f"- rejected_orders: {len(s3.rejected_orders)}",
        f"- forced_hold_events: {len(s3.events)}",
        f"- final_nav: {s3.final_nav:.2f}",
    ]
    return "\n".join(lines) + "\n"


def render_gate1_report(
    all_runs: dict[str, dict[str, BacktestRun]],
    gate1: dict[str, Any],
    data: dict[str, pd.DataFrame],
    data_note: str,
) -> str:
    s3_runs = {regime: runs["s3"] for regime, runs in all_runs.items()}
    checks = _gate_checks(s3_runs, gate1)
    bear_s3 = summarize_run(s3_runs["bear"])
    bear_ew = summarize_run(all_runs["bear"]["equal_weight"])
    overall = "PASS" if checks["overall_pass"] else "FAIL"
    lines = [
        "# S3 Full Historical Gate1 Report",
        "",
        "参数：使用 `configs/strategy.yaml` 默认 S3 参数，lookback_days=20，top_k=2，trend_filter_ma=60，rebalance=daily。",
        "未修改参数，未用 oos 做任何参数选择。信号 D 收盘后生成，D+1 开盘成交。",
        f"数据说明：{data_note}。",
        "",
        "## S3 分段关键指标",
        _run_summary_table(s3_runs),
        "",
        "## 对照组 ratio 表",
    ]
    for regime in ("bull", "bear", "range", "oos"):
        lines.extend([f"### {regime}", _comparison_table(all_runs[regime]["s3"], all_runs[regime]["equal_weight"], all_runs[regime]["random"]), ""])
    lines.extend(
        [
            "## 反假设列表",
            f"- 动量只是牛市 beta：bear 段 S3 return={_fmt_pct(bear_s3['return'])}，等权买入持有 return={_fmt_pct(bear_ew['return'])}；若 S3 在 bear 显著优于等权，才能削弱该假设。",
            "- 对 lookback/top_k 过拟合：以下敏感性表只用 bull/bear/range 计算，未触碰 oos 调参。",
            _sensitivity_table(data),
            "- ETF/指数一字板约束乐观偏差：S3 日线 limit_up/down 为 NaN，constraints 不会拒绝一字涨停买入或一字跌停卖出；偏差方向是高估可成交性、略乐观。该偏差未在本轮修正。",
            "",
            "## flag/参数调查记录",
            "- 本轮未修改 `configs/strategy.yaml`，未调整 lookback/top_k/trend_filter_ma。",
            "- 未触碰oos调参；oos 只用于最终 C 组裁决。",
            "",
            "## Gate1 判定表",
            _gate1_table(checks, gate1),
            "",
            f"最终判定：{overall}",
        ]
    )
    return "\n".join(lines) + "\n"


def write_s3_reports(all_runs: dict[str, dict[str, BacktestRun]], data: dict[str, pd.DataFrame], data_note: str) -> list[Path]:
    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    gate1 = _load_yaml("backtest.yaml")["gate1"]
    paths = []
    for regime, runs in all_runs.items():
        path = REPORT_DIR / f"s3_{regime}.md"
        path.write_text(render_regime_report(runs, gate1), encoding="utf-8")
        paths.append(path)
    gate_path = REPORT_DIR / "s3_gate1.md"
    gate_path.write_text(render_gate1_report(all_runs, gate1, data, data_note), encoding="utf-8")
    paths.append(gate_path)
    return paths


def run_s3_gate1(refresh: bool = False) -> dict[str, dict[str, BacktestRun]]:
    data, data_note = load_s3_gate1_data(refresh=refresh)
    all_runs = {}
    for regime in ("bull", "bear", "range", "oos"):
        all_runs[regime] = run_s3_regime(regime, refresh=refresh, data=data)
    write_s3_reports(all_runs, data, data_note)
    return all_runs


def run_s3b_regime(
    regime: str,
    refresh: bool = False,
    config_override: dict[str, Any] | None = None,
    data: dict[str, pd.DataFrame] | None = None,
) -> dict[str, BacktestRun]:
    backtest_cfg = _load_yaml("backtest.yaml")
    strategy_cfg = _load_yaml("strategy.yaml")["s3b_trend"].copy()
    if config_override:
        strategy_cfg.update(config_override)
    span = backtest_cfg["regimes"][regime]
    start = _parse_date(span["start"])
    end = _parse_date(span["end"])
    if data is None:
        data, _note = load_s3b_gate1_data(refresh=refresh)
    strategy = S3BTrendStrategy(strategy_cfg)
    cost_config = CostConfig.from_mapping(_load_yaml("cost.yaml"))
    asset = str(strategy_cfg["asset"])
    return {
        "s3b": _run_signal_backtest(
            "s3b",
            regime,
            start,
            end,
            data,
            strategy.generate_signals,
            cost_config,
        ),
        "buy_hold": _run_signal_backtest(
            "single_asset_buy_hold",
            regime,
            start,
            end,
            data,
            _single_asset_buy_hold_signal_factory(asset),
            cost_config,
        ),
    }


def _single_asset_buy_hold_signal_factory(asset: str) -> SignalFunc:
    def _signal(as_of_date: date, ctx: dict[str, Any]) -> list[Order]:
        positions: tuple[Position, ...] = tuple(ctx.get("positions", ()))
        if any(item.symbol == asset and item.quantity > 0 for item in positions):
            return []
        frame = ctx["data"].get(asset)
        if frame is None or frame.empty:
            return []
        close = float(frame.iloc[-1]["close"])
        quantity = int(math.floor((float(ctx["nav"]) / close) / LOT_SIZE) * LOT_SIZE)
        if quantity <= 0:
            return []
        return [Order(symbol=asset, side="buy", quantity=quantity, submitted_date=as_of_date)]

    return _signal


def _parse_report_number(text: str) -> float | None:
    text = text.strip()
    if text in {"NA", ""}:
        return None
    if text.endswith("%"):
        return float(text[:-1]) / 100.0
    return float(text)


def _load_failed_s3_reference() -> dict[str, dict[str, float]]:
    path = REPORT_DIR / "s3_gate1.md"
    if not path.exists():
        return {}
    refs: dict[str, dict[str, float]] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.startswith("| "):
            continue
        parts = [item.strip() for item in line.strip().strip("|").split("|")]
        if len(parts) < 8 or parts[0] not in {"bull", "bear", "range", "oos"}:
            continue
        refs[parts[0]] = {
            "return": _parse_report_number(parts[1]) or 0.0,
            "max_drawdown": _parse_report_number(parts[2]) or 0.0,
            "trades": _parse_report_number(parts[3]) or 0.0,
            "fee_ratio": _parse_report_number(parts[7]) or 0.0,
        }
    return refs


def _comparison_table_s3b(s3b: BacktestRun, buy_hold: BacktestRun, failed_s3: dict[str, float] | None) -> str:
    s3b_m = summarize_run(s3b)
    bh_m = summarize_run(buy_hold)
    failed_s3 = failed_s3 or {}
    rows = [
        ("return", s3b_m["return"], bh_m["return"], failed_s3.get("return"), True),
        ("max_drawdown", s3b_m["max_drawdown"], bh_m["max_drawdown"], failed_s3.get("max_drawdown"), True),
        ("trades", s3b_m["trades"], bh_m["trades"], failed_s3.get("trades"), False),
        ("fee_ratio", s3b_m["fee_ratio"], bh_m["fee_ratio"], failed_s3.get("fee_ratio"), False),
    ]
    lines = [
        "| metric | S3b | sh000300_buy_hold | failed_S3_rotation | S3b/BH | S3b/S3 | note |",
        "|---|---:|---:|---:|---:|---:|---|",
    ]
    for metric, s3b_v, bh_v, s3_v, pct in rows:
        r1 = _metric_ratio(s3b_v, bh_v)
        r2 = _metric_ratio(s3b_v, s3_v) if s3_v is not None else None
        fmt = _fmt_pct if pct else _fmt_float
        lines.append(
            f"| {metric} | {fmt(s3b_v)} | {fmt(bh_v)} | {fmt(s3_v)} | {_fmt_float(r1)} | {_fmt_float(r2)} | {_ratio_note(r1, r2)} |"
        )
    return "\n".join(lines)


def _s3b_sensitivity_table(data: dict[str, pd.DataFrame]) -> str:
    rows = []
    backtest_cfg = _load_yaml("backtest.yaml")
    strategy_cfg = _load_yaml("strategy.yaml")["s3b_trend"]
    cost_config = CostConfig.from_mapping(_load_yaml("cost.yaml"))
    for ma_len in strategy_cfg["ma_sensitivity"]:
        trades: list[TradeRecord] = []
        returns = []
        drawdowns = []
        for regime in ("bull", "bear", "range"):
            span = backtest_cfg["regimes"][regime]
            start = _parse_date(span["start"])
            end = _parse_date(span["end"])
            cfg = strategy_cfg.copy()
            cfg["ma_len"] = int(ma_len)
            strategy = S3BTrendStrategy(cfg)
            run = _run_signal_backtest("s3b_sensitivity", regime, start, end, data, strategy.generate_signals, cost_config)
            trades.extend(run.trades)
            returns.append(run.total_return)
            drawdowns.append(run.max_drawdown)
        metrics = _trade_metrics(tuple(trades))
        rows.append((int(ma_len), np.mean(returns), max(drawdowns), metrics))
    lines = [
        "| ma_len | in_sample_avg_return | in_sample_worst_drawdown | trades | expectancy | profit_factor | win_rate |",
        "|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for ma_len, avg_return, worst_dd, metrics in rows:
        lines.append(
            f"| {ma_len} | {_fmt_pct(float(avg_return))} | {_fmt_pct(float(worst_dd))} | {int(metrics['trades'])} | "
            f"{metrics['expectancy']:.2f} | {_fmt_float(metrics['profit_factor'])} | {_fmt_pct(metrics['win_rate'])} |"
        )
    return "\n".join(lines)


def render_s3b_regime_report(
    regime_runs: dict[str, BacktestRun],
    gate1: dict[str, Any],
    failed_s3_refs: dict[str, dict[str, float]],
) -> str:
    s3b = regime_runs["s3b"]
    metrics = summarize_run(s3b)
    lines = [
        f"# S3b Gate1 Regime Report / {s3b.regime}",
        "",
        f"区间：{s3b.start}..{s3b.end}。信号在 D 日收盘后用 ≤D 日线计算，D+1 开盘撮合。",
        "",
        "## 对照组 ratio 表",
        _comparison_table_s3b(s3b, regime_runs["buy_hold"], failed_s3_refs.get(s3b.regime)),
        "",
        "## 反假设列表",
        f"- 趋势跟踪只是牛市 beta：本段 S3b return={_fmt_pct(metrics['return'])}，max_drawdown={_fmt_pct(metrics['max_drawdown'])}；需结合总报告 bear 段与买入持有对比裁决。",
        "- 对 ma_len 过拟合：本段不调参，敏感性表只在总报告用 bull/bear/range 计算，未触碰 oos 调参。",
        "- ETF/指数一字板约束：S3b 数据 limit_up/down 为 NaN，不触发一字板拒单；偏差方向是略乐观，实盘可能出现买不到/卖不出。",
        "",
        "## flag/参数调查记录",
        "- 本轮未修改 `configs/strategy.yaml` 的 S3b 参数，ma_len 保持 200。",
        "- 未触碰 oos 调参；本报告只按默认参数评估该区间。",
        "",
        "## Gate1 判定表",
        "| regime | metric | actual | threshold | result |",
        "|---|---:|---:|---:|---|",
        _run_gate_row(s3b.regime, "expectancy_after_cost", metrics["expectancy"], gate1["expectancy_after_cost_gt"], metrics["expectancy"] > gate1["expectancy_after_cost_gt"]),
        _run_gate_row(s3b.regime, "profit_factor", metrics["profit_factor"], gate1["profit_factor_min"], metrics["profit_factor"] >= gate1["profit_factor_min"]),
        _run_gate_row(s3b.regime, "max_drawdown", metrics["max_drawdown"], gate1["max_drawdown_max"], metrics["max_drawdown"] <= gate1["max_drawdown_max"]),
        (
            _run_gate_row(s3b.regime, "trades", metrics["trades"], gate1["oos_min_trades"], metrics["trades"] >= gate1["oos_min_trades"])
            if s3b.regime == "oos"
            else _gate_row_text(s3b.regime, "trades", _fmt_float(metrics["trades"]), "A不要求", "N/A")
        ),
        "",
        "## 交易摘要",
        f"- filled_orders: {len(s3b.filled_orders)}",
        f"- rejected_orders: {len(s3b.rejected_orders)}",
        f"- forced_hold_events: {len(s3b.events)}",
        f"- final_nav: {s3b.final_nav:.2f}",
    ]
    return "\n".join(lines) + "\n"


def render_s3b_gate1_report(
    all_runs: dict[str, dict[str, BacktestRun]],
    gate1: dict[str, Any],
    data: dict[str, pd.DataFrame],
    data_note: str,
    failed_s3_refs: dict[str, dict[str, float]],
) -> str:
    s3b_runs = {regime: runs["s3b"] for regime, runs in all_runs.items()}
    checks = _gate_checks(s3b_runs, gate1)
    bear_s3b = summarize_run(s3b_runs["bear"])
    bear_bh = summarize_run(all_runs["bear"]["buy_hold"])
    overall = "PASS" if checks["overall_pass"] else "FAIL"
    lines = [
        "# S3b Full Historical Gate1 Report",
        "",
        "参数：使用 `configs/strategy.yaml` 默认 S3b 参数，asset=sh000300，ma_len=200，rebalance=daily_signal。",
        "未修改 ma_len；ma_sensitivity=[100,150,200] 只做 in-sample 展示。未用 oos 做任何参数选择。",
        "信号 D 收盘后生成，D+1 开盘成交；只有 close/MA 穿越时产生订单。",
        f"数据说明：{data_note}。",
        "",
        "## S3b 分段关键指标",
        _run_summary_table(s3b_runs),
        "",
        "## 对照组 ratio 表",
    ]
    for regime in ("bull", "bear", "range", "oos"):
        lines.extend([f"### {regime}", _comparison_table_s3b(all_runs[regime]["s3b"], all_runs[regime]["buy_hold"], failed_s3_refs.get(regime)), ""])
    lines.extend(
        [
            "## 反假设列表",
            f"- 趋势跟踪只是牛市 beta：bear 段 S3b max_drawdown={_fmt_pct(bear_s3b['max_drawdown'])}，买入持有 max_drawdown={_fmt_pct(bear_bh['max_drawdown'])}；若明显低于买入持有，才支持回撤管理假设。",
            "- 对 ma_len 过拟合：以下敏感性表只用 bull/bear/range 计算，未触碰 oos 调参。",
            _s3b_sensitivity_table(data),
            "- ETF/指数一字板约束乐观偏差：S3b 日线 limit_up/down 为 NaN，constraints 不会拒绝一字涨停买入或一字跌停卖出；偏差方向是高估可成交性、略乐观。该偏差未在本轮修正。",
            "",
            "## flag/参数调查记录",
            "- 本轮未修改 `configs/strategy.yaml`，ma_len 保持预注册默认值 200。",
            "- 未触碰oos调参；oos 只用于最终 C 组裁决。",
            "- 低换手导致交易数很少，未提高交易频率凑样本量。",
            "",
            "## Gate1 判定表",
            _gate1_table(checks, gate1),
            "",
            f"最终判定：{overall}",
        ]
    )
    return "\n".join(lines) + "\n"


def write_s3b_reports(
    all_runs: dict[str, dict[str, BacktestRun]],
    data: dict[str, pd.DataFrame],
    data_note: str,
) -> list[Path]:
    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    gate1 = _load_yaml("backtest.yaml")["gate1"]
    failed_s3_refs = _load_failed_s3_reference()
    paths = []
    for regime, runs in all_runs.items():
        path = REPORT_DIR / f"s3b_{regime}.md"
        path.write_text(render_s3b_regime_report(runs, gate1, failed_s3_refs), encoding="utf-8")
        paths.append(path)
    gate_path = REPORT_DIR / "s3b_gate1.md"
    gate_path.write_text(render_s3b_gate1_report(all_runs, gate1, data, data_note, failed_s3_refs), encoding="utf-8")
    paths.append(gate_path)
    return paths


def run_s3b_gate1(refresh: bool = False) -> dict[str, dict[str, BacktestRun]]:
    data, data_note = load_s3b_gate1_data(refresh=refresh)
    all_runs = {}
    for regime in ("bull", "bear", "range", "oos"):
        all_runs[regime] = run_s3b_regime(regime, refresh=refresh, data=data)
    write_s3b_reports(all_runs, data, data_note)
    return all_runs


def main() -> None:
    parser = argparse.ArgumentParser(description="Backtest engine")
    parser.add_argument("--strategy", required=True, choices=["s1", "s2", "s3", "s3b", "placeholder"])
    parser.add_argument("--regime", default=None)
    parser.add_argument("--all-gate1", action="store_true", help="run full strategy Gate1 across bull/bear/range/oos")
    parser.add_argument("--refresh", action="store_true", help="refresh AkShare data cache")
    args = parser.parse_args()

    backtest_cfg = _load_yaml("backtest.yaml")
    if args.strategy == "s3" and args.all_gate1:
        all_runs = run_s3_gate1(refresh=args.refresh)
        checks = _gate_checks({name: runs["s3"] for name, runs in all_runs.items()}, backtest_cfg["gate1"])
        print("wrote <PROJECT_ROOT>/reports/s3_gate1.md")
        print(f"S3 Gate1 final: {'PASS' if checks['overall_pass'] else 'FAIL'}")
        print(_run_summary_table({name: runs["s3"] for name, runs in all_runs.items()}))
        return

    if args.strategy == "s3b" and args.all_gate1:
        all_runs = run_s3b_gate1(refresh=args.refresh)
        checks = _gate_checks({name: runs["s3b"] for name, runs in all_runs.items()}, backtest_cfg["gate1"])
        print("wrote <PROJECT_ROOT>/reports/s3b_gate1.md")
        print(f"S3b Gate1 final: {'PASS' if checks['overall_pass'] else 'FAIL'}")
        print(_run_summary_table({name: runs["s3b"] for name, runs in all_runs.items()}))
        return

    if args.strategy == "s3" and args.regime:
        if args.regime not in backtest_cfg["regimes"]:
            raise SystemExit(f"Unknown regime: {args.regime}")
        runs = run_s3_regime(args.regime, refresh=args.refresh)
        path = REPORT_DIR / f"s3_{args.regime}.md"
        REPORT_DIR.mkdir(parents=True, exist_ok=True)
        path.write_text(render_regime_report(runs, backtest_cfg["gate1"]), encoding="utf-8")
        print(f"wrote {path}")
        print("S3 real historical regime backtest executed.")
        return

    if args.strategy == "s3b" and args.regime:
        if args.regime not in backtest_cfg["regimes"]:
            raise SystemExit(f"Unknown regime: {args.regime}")
        runs = run_s3b_regime(args.regime, refresh=args.refresh)
        failed_s3_refs = _load_failed_s3_reference()
        path = REPORT_DIR / f"s3b_{args.regime}.md"
        REPORT_DIR.mkdir(parents=True, exist_ok=True)
        path.write_text(render_s3b_regime_report(runs, backtest_cfg["gate1"], failed_s3_refs), encoding="utf-8")
        print(f"wrote {path}")
        print("S3b real historical regime backtest executed.")
        return

    if args.regime is None:
        raise SystemExit("--regime is required unless --all-gate1")
    if args.regime not in backtest_cfg["regimes"]:
        raise SystemExit(f"Unknown regime: {args.regime}")
    result = run_placeholder_pipeline(args.strategy, args.regime)
    path = write_placeholder_report(result)
    print(f"wrote {path}")
    print("M2 placeholder only; no M3 strategy backtest executed.")


if __name__ == "__main__":
    main()
