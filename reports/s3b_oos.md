# S3b Gate1 Regime Report / oos

区间：2024-10-01..2026-05-15。信号在 D 日收盘后用 ≤D 日线计算，D+1 开盘撮合。

## 对照组 ratio 表
| metric | S3b | sh000300_buy_hold | failed_S3_rotation | S3b/BH | S3b/S3 | note |
|---|---:|---:|---:|---:|---:|---|
| return | 5.98% | 13.31% | -25.70% | 0.4493 | -0.2327 |  |
| max_drawdown | 11.84% | 11.78% | 36.33% | 1.0054 | 0.3259 |  |
| trades | 4.0000 | 1.0000 | 272.0000 | 4.0000 | 0.0147 | ratio>2x，需调查 |
| fee_ratio | 0.0008 | 0.0008 | 0.0008 | 0.9592 | 0.9559 |  |

## 反假设列表
- 趋势跟踪只是牛市 beta：本段 S3b return=5.98%，max_drawdown=11.84%；需结合总报告 bear 段与买入持有对比裁决。
- 对 ma_len 过拟合：本段不调参，敏感性表只在总报告用 bull/bear/range 计算，未触碰 oos 调参。
- ETF/指数一字板约束：S3b 数据 limit_up/down 为 NaN，不触发一字板拒单；偏差方向是略乐观，实盘可能出现买不到/卖不出。

## flag/参数调查记录
- 本轮未修改 `configs/strategy.yaml` 的 S3b 参数，ma_len 保持 200。
- 未触碰 oos 调参；本报告只按默认参数评估该区间。

## Gate1 判定表
| regime | metric | actual | threshold | result |
|---|---:|---:|---:|---|
| oos | expectancy_after_cost | 14949.7497 | 0.0000 | PASS |
| oos | profit_factor | 1.4414 | 1.3000 | PASS |
| oos | max_drawdown | 0.1184 | 0.2000 | PASS |
| oos | trades | 4.0000 | 60.0000 | FAIL |

## 交易摘要
- filled_orders: 8
- rejected_orders: 0
- forced_hold_events: 0
- final_nav: 1059799.00
