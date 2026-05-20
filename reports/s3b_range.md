# S3b Gate1 Regime Report / range

区间：2023-06-01..2024-09-30。信号在 D 日收盘后用 ≤D 日线计算，D+1 开盘撮合。

## 对照组 ratio 表
| metric | S3b | sh000300_buy_hold | failed_S3_rotation | S3b/BH | S3b/S3 | note |
|---|---:|---:|---:|---:|---:|---|
| return | -3.18% | 3.49% | -13.39% | -0.9114 | 0.2375 |  |
| max_drawdown | 10.75% | 16.59% | 34.20% | 0.6480 | 0.3144 |  |
| trades | 8.0000 | 1.0000 | 202.0000 | 8.0000 | 0.0396 | ratio>2x，需调查 |
| fee_ratio | 0.0008 | 0.0008 | 0.0008 | 0.9838 | 0.9488 |  |

## 反假设列表
- 趋势跟踪只是牛市 beta：本段 S3b return=-3.18%，max_drawdown=10.75%；需结合总报告 bear 段与买入持有对比裁决。
- 对 ma_len 过拟合：本段不调参，敏感性表只在总报告用 bull/bear/range 计算，未触碰 oos 调参。
- ETF/指数一字板约束：S3b 数据 limit_up/down 为 NaN，不触发一字板拒单；偏差方向是略乐观，实盘可能出现买不到/卖不出。

## flag/参数调查记录
- 本轮未修改 `configs/strategy.yaml` 的 S3b 参数，ma_len 保持 200。
- 未触碰 oos 调参；本报告只按默认参数评估该区间。

## Gate1 判定表
| regime | metric | actual | threshold | result |
|---|---:|---:|---:|---|
| range | expectancy_after_cost | -3975.8780 | 0.0000 | FAIL |
| range | profit_factor | 0.7042 | 1.3000 | FAIL |
| range | max_drawdown | 0.1075 | 0.2000 | PASS |
| range | trades | 8.0000 | A不要求 | N/A |

## 交易摘要
- filled_orders: 16
- rejected_orders: 0
- forced_hold_events: 0
- final_nav: 968192.98
