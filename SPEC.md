# 实现契约 SPEC（Codex 按此实现；Claude 按此 review/验收）

项目：A股量化策略模拟盘擂台。环境：东方财富散户、T+1、佣金万2.5（单笔最低5元）。
分工：**Codex 实现+执行（最大权限），Claude 规划+逐 Gate review 验收。**
配置唯一来源：`configs/*.yaml`。**严禁在 .py 里硬编码任何成本/区间/策略阈值。**

## 0. 不可违反的铁律（违反即判 FAIL，蟑螂效应全量普查）

1. **无未来函数**：任何 `generate_signals(as_of_ts, data)` 只能读取 `as_of_ts` 及之前的数据。
   S1 在 `14:50` 决策——只能用当日 ≤14:50 的分钟线 + 昨日及更早日线。**严禁用当日收盘价**计算
   涨幅/量比/换手。代码需有 `assert data.index.max() <= as_of_ts`。
2. **成本如实**：成本只能来自 `configs/cost.yaml`，公式见该文件注释，必须含 `min_per_order=5`。
3. **反幸存者**：股票池含退市/ST 股；回测期内已退市股在退市前仍参与。
4. **T+1**：当日买入的持仓 `sellable=False`，次日起方可卖出。
5. **撮合约束**：次日若一字涨停→买单不成交；一字跌停或停牌→卖单不成交（持仓顺延，
   记录 `forced_hold` 事件）。S1 的"次日开盘无条件卖"遇跌停必须如实模拟为卖不掉。
6. **OOS 隔离**：`backtest.yaml` 的 `oos` 区间在 Gate1 通过前禁止用于任何调参。

## 1. 目录与模块接口

```
configs/        已由 Claude 定稿（cost/backtest/strategy.yaml）
data/akshare_source.py
  - get_daily(symbol|all, start, end) -> DataFrame[date,open,high,low,close,vol,amount,
        pct_chg,turnover,float_mv,is_st,is_suspended,limit_up_price,limit_down_price,is_delisted]
  - get_intraday_snapshot(date, cutoff="14:50") -> 各股票截至 cutoff 的分钟聚合
        (price_at_cutoff, vwap_curve, high_after_1430, vol_ratio_at_cutoff)
  - 全量结果 parquet 缓存到 data/cache/；CLI: `python -m data.akshare_source --check`
strategies/base.py    Strategy ABC: generate_signals(as_of_ts, ctx)->List[Order]
strategies/s1_tail.py s2_factor.py s3_momentum.py   # 严格按 strategy.yaml 参数
backtest/constraints.py  成本/滑点/T+1/涨跌停/停牌（纯函数，单测重点）
backtest/engine.py    事件驱动；CLI: `python -m backtest.engine --strategy s1 --regime bull`
                      输出 reports/<strategy>_<regime>.md（模板见 §3）
exec/broker_base.py   BrokerBase: submit(order)/positions()/cash()  确定性，无LLM
exec/paper_broker.py  模拟盘撮合（复用 backtest/constraints.py，保证回测=模拟盘同口径）
exec/eastmoney_gui_stub.py  仅占位 NotImplemented，注明未来接 pytrader
paper/runner.py       两模式：--mode oos_walkforward（用 oos 区间逐日走，session 内可得 Gate2 代理结论）
                                 --mode forward（真实前向，每日落盘，累计交易日）
reports/template.md   报告模板；reports/sanity_review.md 每5轮迭代追加
tests/                pytest；必含 §0 第5条三个边界 case
```

## 2. 关键实现要求

- `constraints.py` 是回测与模拟盘**共用**的唯一撮合/成本实现，二者不得各写一套。
- S1 `is_above_vwap`/`new_high_after_1430` 必须基于 ≤14:50 分钟数据；写出该断言。
- akshare 限频：加重试+本地 parquet 缓存；首次全量拉取后离线可复跑。
- 一切随机性固定 seed；回测可 bit 级复现。

## 3. 报告模板（每份回测/模拟盘报告必含三节，CLAUDE.md 异常检测纪律）

1. **对照组 ratio 表**：本策略 vs 等权买入持有/随机选股 的 收益、回撤、交易数、扣费占比；
   任一 ratio 超预期 2× 标注调查。
2. **反假设列表**：≥2 条（首选「未来函数/测量口径/牛市beta」）+ 各自证伪实验与结论。
3. **flag/参数调查记录**：本轮改了哪些 strategy.yaml 参数、为何、是否触碰 oos。
4. 末尾给 **Gate1 判定表**：每 regime+oos 的 4 项指标实际值 vs 阈值 → PASS/FAIL。

## 4. 验收流程（Claude）

M1→Gate0（数据无未来函数+含退市）；M2→约束单测全绿+逐行审；M3→Gate1；
M5/M6→Gate2（oos_walkforward 代理 + forward 累计）。任一不过：写 sanity_review.md，回退修复，
**不得带病进入下一 Gate 或真实资金**。

## 5. Gate0 实证结论（2026-05-19，数据现实 pivot —— 最高优先级，覆盖前文冲突处）

实证（Codex 跑 `--check`，非推测）：akshare `stock_zh_a_hist_min_em` period=1 硬编码
ndays=5、period=5 仅回溯~50天，bull/bear/range/oos **四段历史分钟数据全部不可得**，
且接口不稳定。结论与路线调整：

- **S1（杨永兴尾盘法）—— 2026-05-19 更新：数据墙已被 BaoStock 免费打破**：
  实证 baostock 0.9.1 提供 A股 **5分钟K线，最早 2020-01-02**（博客称1999/2015 不实，实测为准），
  含退市股退市前5min、bull/bear/range/oos 全覆盖。**S1 现可做"5min PIT 近似版"无未来函数
  历史 Gate1**：只用 time≤14:50 的5min bar，严禁用收盘/未来bar，须 `assert bar.time<=14:50`。
  缺 `turn` 字段→turnover 用 累计量/float_shares(由日线 float_mv/close 估) 近似，docstring 标注。
  5min 聚合已知偏差：看不到 bar 内路径/精确14:50 tick/VWAP穿越先后——对"14:50一次决策"
  可接受，必须在报告反假设里写明偏差方向。S1 高换手→标准 A+B+C 笔数闸门(200/60)适用。
  前向 paper 仍并行累积作真实 OOS 增量。akshare 历史分钟仍不可用（ndays=5），勿再用其 daily proxy。
- **S2（多因子）/ S3（动量轮动）**：仅需历史**日线**，**可立即做合规 Gate1**（含退市股反
  幸存者已实证）。**项目主推进路径 = S2 + S3 走完 Gate1 → Gate2**，S1 并行只做前向 paper。
- **S3 数据前置**：ETF/指数日线源（fund_etf_hist_em / stock_zh_index_daily_em）**尚未实证**，
  M2 前必须由 Codex 补一个数据探针验证可得性，否则 S3 不得进 Gate1。
- 运行环境事实：**Claude 的 Bash 沙箱无外网**，一切需联网的取数/回测/探针**由 Codex 执行**，
  Claude 只读代码与产物做 review。
