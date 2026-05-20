# S3 Gate1 Regime Report / range

区间：2023-06-01..2024-09-30。信号在 D 日收盘后用 ≤D 日线计算，D+1 开盘撮合。

## 对照组 ratio 表
| metric | S3 | equal_weight_buy_hold | random_rotation | S3/EW | S3/random | note |
|---|---:|---:|---:|---:|---:|---|
| return | -13.39% | 5.25% | -51.92% | -2.5498 | 0.2579 | ratio>2x，需调查 |
| max_drawdown | 34.20% | 13.43% | 58.70% | 2.5460 | 0.5827 | ratio>2x，需调查 |
| trades | 202.0000 | 4.0000 | 410.0000 | 50.5000 | 0.4927 | ratio>2x，需调查 |
| fee_ratio | 0.0008 | 0.0008 | 0.0008 | 0.9885 | 1.0112 |  |

## 反假设列表
- 动量只是牛市 beta：本段 S3 return=-13.39%，max_drawdown=34.20%；需结合总报告 bear 段与等权对照裁决。
- 对 lookback/top_k 过拟合：本段不单独调参，敏感性表只在总报告用 bull/bear/range 计算，未触碰 oos 调参。
- ETF/指数一字板约束：S3 数据 limit_up/down 为 NaN，不触发一字板拒单；偏差方向是略乐观，实盘可能出现买不到/卖不出。

## flag/参数调查记录
- 本轮未修改 `configs/strategy.yaml` 的 S3 参数。
- 未触碰 oos 调参；本报告只按默认参数评估该区间。

## Gate1 判定表
| regime | metric | actual | threshold | result |
|---|---:|---:|---:|---|
| range | expectancy_after_cost | -753.0200 | 0.0000 | FAIL |
| range | profit_factor | 0.6658 | 1.3000 | FAIL |
| range | max_drawdown | 0.3420 | 0.2000 | FAIL |
| range | trades | 202.0000 | A不要求 | N/A |

## 交易摘要
- filled_orders: 392
- rejected_orders: 0
- forced_hold_events: 0
- final_nav: 866070.74
