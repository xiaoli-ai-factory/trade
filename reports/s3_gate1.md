# S3 Full Historical Gate1 Report

参数：使用 `configs/strategy.yaml` 默认 S3 参数，lookback_days=20，top_k=2，trend_filter_ma=60，rebalance=daily。
未修改参数，未用 oos 做任何参数选择。信号 D 收盘后生成，D+1 开盘成交。
数据说明：warmup_prefix_loaded。

## S3 分段关键指标
| regime | return | max_drawdown | trades | expectancy_after_cost | profit_factor | win_rate | fee_ratio |
|---|---:|---:|---:|---:|---:|---:|---:|
| bull | -14.37% | 31.80% | 118 | -1370.57 | 0.5463 | 55.08% | 0.08% |
| bear | -27.91% | 27.97% | 64 | -4361.02 | 0.1911 | 50.00% | 0.08% |
| range | -13.39% | 34.20% | 202 | -753.02 | 0.6658 | 52.48% | 0.08% |
| oos | -25.70% | 36.33% | 272 | -1192.26 | 0.4970 | 63.60% | 0.08% |

## 对照组 ratio 表
### bull
| metric | S3 | equal_weight_buy_hold | random_rotation | S3/EW | S3/random | note |
|---|---:|---:|---:|---:|---:|---|
| return | -14.37% | 13.90% | -16.92% | -1.0337 | 0.8494 |  |
| max_drawdown | 31.80% | 9.05% | 36.27% | 3.5124 | 0.8768 | ratio>2x，需调查 |
| trades | 118.0000 | 4.0000 | 194.0000 | 29.5000 | 0.6082 | ratio>2x，需调查 |
| fee_ratio | 0.0008 | 0.0008 | 0.0008 | 0.9519 | 1.0134 |  |

### bear
| metric | S3 | equal_weight_buy_hold | random_rotation | S3/EW | S3/random | note |
|---|---:|---:|---:|---:|---:|---|
| return | -27.91% | -18.84% | -45.00% | 1.4816 | 0.6202 |  |
| max_drawdown | 27.97% | 18.90% | 45.89% | 1.4793 | 0.6095 |  |
| trades | 64.0000 | 4.0000 | 244.0000 | 16.0000 | 0.2623 | ratio>2x，需调查 |
| fee_ratio | 0.0008 | 0.0007 | 0.0008 | 1.1270 | 1.0055 |  |

### range
| metric | S3 | equal_weight_buy_hold | random_rotation | S3/EW | S3/random | note |
|---|---:|---:|---:|---:|---:|---|
| return | -13.39% | 5.25% | -51.92% | -2.5498 | 0.2579 | ratio>2x，需调查 |
| max_drawdown | 34.20% | 13.43% | 58.70% | 2.5460 | 0.5827 | ratio>2x，需调查 |
| trades | 202.0000 | 4.0000 | 410.0000 | 50.5000 | 0.4927 | ratio>2x，需调查 |
| fee_ratio | 0.0008 | 0.0008 | 0.0008 | 0.9885 | 1.0112 |  |

### oos
| metric | S3 | equal_weight_buy_hold | random_rotation | S3/EW | S3/random | note |
|---|---:|---:|---:|---:|---:|---|
| return | -25.70% | 5.04% | -74.13% | -5.1032 | 0.3466 | ratio>2x，需调查 |
| max_drawdown | 36.33% | 11.58% | 74.13% | 3.1371 | 0.4901 | ratio>2x，需调查 |
| trades | 272.0000 | 4.0000 | 436.0000 | 68.0000 | 0.6239 | ratio>2x，需调查 |
| fee_ratio | 0.0008 | 0.0008 | 0.0008 | 1.0043 | 1.0264 |  |

## 反假设列表
- 动量只是牛市 beta：bear 段 S3 return=-27.91%，等权买入持有 return=-18.84%；若 S3 在 bear 显著优于等权，才能削弱该假设。
- 对 lookback/top_k 过拟合：以下敏感性表只用 bull/bear/range 计算，未触碰 oos 调参。
| lookback | top_k | in_sample_avg_return | in_sample_worst_drawdown | trades | expectancy | profit_factor | win_rate |
|---:|---:|---:|---:|---:|---:|---:|---:|
| 10 | 1 | -11.57% | 38.84% | 118 | -2940.99 | 0.7617 | 29.66% |
| 10 | 2 | -10.79% | 32.24% | 412 | -863.81 | 0.7046 | 55.10% |
| 10 | 3 | -15.07% | 31.05% | 443 | -1092.70 | 0.5440 | 49.21% |
| 20 | 1 | -28.51% | 50.56% | 117 | -7310.25 | 0.4252 | 25.64% |
| 20 | 2 | -18.56% | 34.20% | 384 | -1544.12 | 0.4873 | 52.86% |
| 20 | 3 | -14.51% | 30.87% | 438 | -1053.80 | 0.5464 | 54.57% |
| 40 | 1 | -17.95% | 38.23% | 100 | -5385.03 | 0.5658 | 28.00% |
| 40 | 2 | -12.79% | 30.12% | 351 | -1150.29 | 0.5793 | 56.13% |
| 40 | 3 | -11.00% | 28.46% | 414 | -858.87 | 0.6160 | 60.87% |
- ETF/指数一字板约束乐观偏差：S3 日线 limit_up/down 为 NaN，constraints 不会拒绝一字涨停买入或一字跌停卖出；偏差方向是高估可成交性、略乐观。该偏差未在本轮修正。

## flag/参数调查记录
- 本轮未修改 `configs/strategy.yaml`，未调整 lookback/top_k/trend_filter_ma。
- 未触碰oos调参；oos 只用于最终 C 组裁决。

## Gate1 判定表
| group | metric | actual | threshold | result |
|---|---:|---:|---:|---|
| A/bull | expectancy_after_cost | -1370.5722 | 0.0000 | FAIL |
| A/bull | profit_factor | 0.5463 | 1.3000 | FAIL |
| A/bull | max_drawdown | 0.3180 | 0.2000 | FAIL |
| A/bear | expectancy_after_cost | -4361.0151 | 0.0000 | FAIL |
| A/bear | profit_factor | 0.1911 | 1.3000 | FAIL |
| A/bear | max_drawdown | 0.2797 | 0.2000 | FAIL |
| A/range | expectancy_after_cost | -753.0200 | 0.0000 | FAIL |
| A/range | profit_factor | 0.6658 | 1.3000 | FAIL |
| A/range | max_drawdown | 0.3420 | 0.2000 | FAIL |
| B/merged | trades | 656.0000 | 200.0000 | PASS |
| B/merged | expectancy_after_cost | -1398.2292 | 0.0000 | FAIL |
| B/merged | profit_factor | 0.4908 | 1.3000 | FAIL |
| C/oos | expectancy_after_cost | -1192.2641 | 0.0000 | FAIL |
| C/oos | profit_factor | 0.4970 | 1.3000 | FAIL |
| C/oos | max_drawdown | 0.3633 | 0.2000 | FAIL |
| C/oos | trades | 272.0000 | 60.0000 | PASS |
| TOTAL | A+B+C | - | - | FAIL |

最终判定：FAIL
