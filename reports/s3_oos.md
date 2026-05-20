# S3 Gate1 Regime Report / oos

区间：2024-10-01..2026-05-15。信号在 D 日收盘后用 ≤D 日线计算，D+1 开盘撮合。

## 对照组 ratio 表
| metric | S3 | equal_weight_buy_hold | random_rotation | S3/EW | S3/random | note |
|---|---:|---:|---:|---:|---:|---|
| return | -25.70% | 5.04% | -74.13% | -5.1032 | 0.3466 | ratio>2x，需调查 |
| max_drawdown | 36.33% | 11.58% | 74.13% | 3.1371 | 0.4901 | ratio>2x，需调查 |
| trades | 272.0000 | 4.0000 | 436.0000 | 68.0000 | 0.6239 | ratio>2x，需调查 |
| fee_ratio | 0.0008 | 0.0008 | 0.0008 | 1.0043 | 1.0264 |  |

## 反假设列表
- 动量只是牛市 beta：本段 S3 return=-25.70%，max_drawdown=36.33%；需结合总报告 bear 段与等权对照裁决。
- 对 lookback/top_k 过拟合：本段不单独调参，敏感性表只在总报告用 bull/bear/range 计算，未触碰 oos 调参。
- ETF/指数一字板约束：S3 数据 limit_up/down 为 NaN，不触发一字板拒单；偏差方向是略乐观，实盘可能出现买不到/卖不出。

## flag/参数调查记录
- 本轮未修改 `configs/strategy.yaml` 的 S3 参数。
- 未触碰 oos 调参；本报告只按默认参数评估该区间。

## Gate1 判定表
| regime | metric | actual | threshold | result |
|---|---:|---:|---:|---|
| oos | expectancy_after_cost | -1192.2641 | 0.0000 | FAIL |
| oos | profit_factor | 0.4970 | 1.3000 | FAIL |
| oos | max_drawdown | 0.3633 | 0.2000 | FAIL |
| oos | trades | 272.0000 | 60.0000 | PASS |

## 交易摘要
- filled_orders: 497
- rejected_orders: 0
- forced_hold_events: 0
- final_nav: 743036.11
