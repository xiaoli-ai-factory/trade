# S3 Gate1 Regime Report / bear

区间：2022-01-01..2022-10-31。信号在 D 日收盘后用 ≤D 日线计算，D+1 开盘撮合。

## 对照组 ratio 表
| metric | S3 | equal_weight_buy_hold | random_rotation | S3/EW | S3/random | note |
|---|---:|---:|---:|---:|---:|---|
| return | -27.91% | -18.84% | -45.00% | 1.4816 | 0.6202 |  |
| max_drawdown | 27.97% | 18.90% | 45.89% | 1.4793 | 0.6095 |  |
| trades | 64.0000 | 4.0000 | 244.0000 | 16.0000 | 0.2623 | ratio>2x，需调查 |
| fee_ratio | 0.0008 | 0.0007 | 0.0008 | 1.1270 | 1.0055 |  |

## 反假设列表
- 动量只是牛市 beta：本段 S3 return=-27.91%，max_drawdown=27.97%；需结合总报告 bear 段与等权对照裁决。
- 对 lookback/top_k 过拟合：本段不单独调参，敏感性表只在总报告用 bull/bear/range 计算，未触碰 oos 调参。
- ETF/指数一字板约束：S3 数据 limit_up/down 为 NaN，不触发一字板拒单；偏差方向是略乐观，实盘可能出现买不到/卖不出。

## flag/参数调查记录
- 本轮未修改 `configs/strategy.yaml` 的 S3 参数。
- 未触碰 oos 调参；本报告只按默认参数评估该区间。

## Gate1 判定表
| regime | metric | actual | threshold | result |
|---|---:|---:|---:|---|
| bear | expectancy_after_cost | -4361.0151 | 0.0000 | FAIL |
| bear | profit_factor | 0.1911 | 1.3000 | FAIL |
| bear | max_drawdown | 0.2797 | 0.2000 | FAIL |
| bear | trades | 64.0000 | A不要求 | N/A |

## 交易摘要
- filled_orders: 126
- rejected_orders: 0
- forced_hold_events: 0
- final_nav: 720895.04
