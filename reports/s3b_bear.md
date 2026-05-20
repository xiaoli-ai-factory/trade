# S3b Gate1 Regime Report / bear

区间：2022-01-01..2022-10-31。信号在 D 日收盘后用 ≤D 日线计算，D+1 开盘撮合。

## 对照组 ratio 表
| metric | S3b | sh000300_buy_hold | failed_S3_rotation | S3b/BH | S3b/S3 | note |
|---|---:|---:|---:|---:|---:|---|
| return | 0.00% | -28.44% | -27.91% | -0.0000 | -0.0000 |  |
| max_drawdown | 0.00% | 28.44% | 27.97% | 0.0000 | 0.0000 |  |
| trades | 0.0000 | 1.0000 | 64.0000 | 0.0000 | 0.0000 |  |
| fee_ratio | 0.0000 | 0.0007 | 0.0008 | 0.0000 | 0.0000 |  |

## 反假设列表
- 趋势跟踪只是牛市 beta：本段 S3b return=0.00%，max_drawdown=0.00%；需结合总报告 bear 段与买入持有对比裁决。
- 对 ma_len 过拟合：本段不调参，敏感性表只在总报告用 bull/bear/range 计算，未触碰 oos 调参。
- ETF/指数一字板约束：S3b 数据 limit_up/down 为 NaN，不触发一字板拒单；偏差方向是略乐观，实盘可能出现买不到/卖不出。

## flag/参数调查记录
- 本轮未修改 `configs/strategy.yaml` 的 S3b 参数，ma_len 保持 200。
- 未触碰 oos 调参；本报告只按默认参数评估该区间。

## Gate1 判定表
| regime | metric | actual | threshold | result |
|---|---:|---:|---:|---|
| bear | expectancy_after_cost | 0.0000 | 0.0000 | FAIL |
| bear | profit_factor | 0.0000 | 1.3000 | FAIL |
| bear | max_drawdown | 0.0000 | 0.2000 | PASS |
| bear | trades | 0.0000 | A不要求 | N/A |

## 交易摘要
- filled_orders: 0
- rejected_orders: 0
- forced_hold_events: 0
- final_nav: 1000000.00
