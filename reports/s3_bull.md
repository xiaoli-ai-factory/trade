# S3 Gate1 Regime Report / bull

区间：2020-07-01..2021-02-10。信号在 D 日收盘后用 ≤D 日线计算，D+1 开盘撮合。

## 对照组 ratio 表
| metric | S3 | equal_weight_buy_hold | random_rotation | S3/EW | S3/random | note |
|---|---:|---:|---:|---:|---:|---|
| return | -14.37% | 13.90% | -16.92% | -1.0337 | 0.8494 |  |
| max_drawdown | 31.80% | 9.05% | 36.27% | 3.5124 | 0.8768 | ratio>2x，需调查 |
| trades | 118.0000 | 4.0000 | 194.0000 | 29.5000 | 0.6082 | ratio>2x，需调查 |
| fee_ratio | 0.0008 | 0.0008 | 0.0008 | 0.9519 | 1.0134 |  |

## 反假设列表
- 动量只是牛市 beta：本段 S3 return=-14.37%，max_drawdown=31.80%；需结合总报告 bear 段与等权对照裁决。
- 对 lookback/top_k 过拟合：本段不单独调参，敏感性表只在总报告用 bull/bear/range 计算，未触碰 oos 调参。
- ETF/指数一字板约束：S3 数据 limit_up/down 为 NaN，不触发一字板拒单；偏差方向是略乐观，实盘可能出现买不到/卖不出。

## flag/参数调查记录
- 本轮未修改 `configs/strategy.yaml` 的 S3 参数。
- 未触碰 oos 调参；本报告只按默认参数评估该区间。

## Gate1 判定表
| regime | metric | actual | threshold | result |
|---|---:|---:|---:|---|
| bull | expectancy_after_cost | -1370.5722 | 0.0000 | FAIL |
| bull | profit_factor | 0.5463 | 1.3000 | FAIL |
| bull | max_drawdown | 0.3180 | 0.2000 | FAIL |
| bull | trades | 118.0000 | A不要求 | N/A |

## 交易摘要
- filled_orders: 232
- rejected_orders: 0
- forced_hold_events: 0
- final_nav: 856296.04
