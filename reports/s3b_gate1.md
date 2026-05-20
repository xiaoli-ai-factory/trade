# S3b Full Historical Gate1 Report

参数：使用 `configs/strategy.yaml` 默认 S3b 参数，asset=sh000300，ma_len=200，rebalance=daily_signal。
未修改 ma_len；ma_sensitivity=[100,150,200] 只做 in-sample 展示。未用 oos 做任何参数选择。
信号 D 收盘后生成，D+1 开盘成交；只有 close/MA 穿越时产生订单。
数据说明：asset=sh000300; warmup_start=2018-01-01。

## S3b 分段关键指标
| regime | return | max_drawdown | trades | expectancy_after_cost | profit_factor | win_rate | fee_ratio |
|---|---:|---:|---:|---:|---:|---:|---:|
| bull | 30.80% | 6.20% | 1 | 307961.64 | inf | 100.00% | 0.08% |
| bear | 0.00% | 0.00% | 0 | 0.00 | 0.0000 | 0.00% | 0.00% |
| range | -3.18% | 10.75% | 8 | -3975.88 | 0.7042 | 12.50% | 0.08% |
| oos | 5.98% | 11.84% | 4 | 14949.75 | 1.4414 | 50.00% | 0.08% |

## 对照组 ratio 表
### bull
| metric | S3b | sh000300_buy_hold | failed_S3_rotation | S3b/BH | S3b/S3 | note |
|---|---:|---:|---:|---:|---:|---|
| return | 30.80% | 30.80% | -14.37% | 1.0000 | -2.1431 | ratio>2x，需调查 |
| max_drawdown | 6.20% | 6.20% | 31.80% | 1.0000 | 0.1949 |  |
| trades | 1.0000 | 1.0000 | 118.0000 | 1.0000 | 0.0085 |  |
| fee_ratio | 0.0008 | 0.0008 | 0.0008 | 1.0000 | 1.0463 |  |

### bear
| metric | S3b | sh000300_buy_hold | failed_S3_rotation | S3b/BH | S3b/S3 | note |
|---|---:|---:|---:|---:|---:|---|
| return | 0.00% | -28.44% | -27.91% | -0.0000 | -0.0000 |  |
| max_drawdown | 0.00% | 28.44% | 27.97% | 0.0000 | 0.0000 |  |
| trades | 0.0000 | 1.0000 | 64.0000 | 0.0000 | 0.0000 |  |
| fee_ratio | 0.0000 | 0.0007 | 0.0008 | 0.0000 | 0.0000 |  |

### range
| metric | S3b | sh000300_buy_hold | failed_S3_rotation | S3b/BH | S3b/S3 | note |
|---|---:|---:|---:|---:|---:|---|
| return | -3.18% | 3.49% | -13.39% | -0.9114 | 0.2375 |  |
| max_drawdown | 10.75% | 16.59% | 34.20% | 0.6480 | 0.3144 |  |
| trades | 8.0000 | 1.0000 | 202.0000 | 8.0000 | 0.0396 | ratio>2x，需调查 |
| fee_ratio | 0.0008 | 0.0008 | 0.0008 | 0.9838 | 0.9488 |  |

### oos
| metric | S3b | sh000300_buy_hold | failed_S3_rotation | S3b/BH | S3b/S3 | note |
|---|---:|---:|---:|---:|---:|---|
| return | 5.98% | 13.31% | -25.70% | 0.4493 | -0.2327 |  |
| max_drawdown | 11.84% | 11.78% | 36.33% | 1.0054 | 0.3259 |  |
| trades | 4.0000 | 1.0000 | 272.0000 | 4.0000 | 0.0147 | ratio>2x，需调查 |
| fee_ratio | 0.0008 | 0.0008 | 0.0008 | 0.9592 | 0.9559 |  |

## 反假设列表
- 趋势跟踪只是牛市 beta：bear 段 S3b max_drawdown=0.00%，买入持有 max_drawdown=28.44%；若明显低于买入持有，才支持回撤管理假设。
- 对 ma_len 过拟合：以下敏感性表只用 bull/bear/range 计算，未触碰 oos 调参。
| ma_len | in_sample_avg_return | in_sample_worst_drawdown | trades | expectancy | profit_factor | win_rate |
|---:|---:|---:|---:|---:|---:|---:|
| 100 | 7.95% | 9.95% | 10 | 23841.44 | 2.6411 | 20.00% |
| 150 | 8.14% | 10.59% | 11 | 22204.57 | 2.7516 | 18.18% |
| 200 | 9.21% | 10.75% | 9 | 30683.85 | 3.5679 | 22.22% |
- ETF/指数一字板约束乐观偏差：S3b 日线 limit_up/down 为 NaN，constraints 不会拒绝一字涨停买入或一字跌停卖出；偏差方向是高估可成交性、略乐观。该偏差未在本轮修正。

## flag/参数调查记录
- 本轮未修改 `configs/strategy.yaml`，ma_len 保持预注册默认值 200。
- 未触碰oos调参；oos 只用于最终 C 组裁决。
- 低换手导致交易数很少，未提高交易频率凑样本量。

## Gate1 判定表
| group | metric | actual | threshold | result |
|---|---:|---:|---:|---|
| A/bull | expectancy_after_cost | 307961.6394 | 0.0000 | PASS |
| A/bull | profit_factor | inf | 1.3000 | PASS |
| A/bull | max_drawdown | 0.0620 | 0.2000 | PASS |
| A/bear | expectancy_after_cost | 0.0000 | 0.0000 | FAIL |
| A/bear | profit_factor | 0.0000 | 1.3000 | FAIL |
| A/bear | max_drawdown | 0.0000 | 0.2000 | PASS |
| A/range | expectancy_after_cost | -3975.8780 | 0.0000 | FAIL |
| A/range | profit_factor | 0.7042 | 1.3000 | FAIL |
| A/range | max_drawdown | 0.1075 | 0.2000 | PASS |
| B/merged | trades | 13.0000 | 200.0000 | FAIL |
| B/merged | expectancy_after_cost | 25842.5857 | 0.0000 | PASS |
| B/merged | profit_factor | 2.3824 | 1.3000 | PASS |
| C/oos | expectancy_after_cost | 14949.7497 | 0.0000 | PASS |
| C/oos | profit_factor | 1.4414 | 1.3000 | PASS |
| C/oos | max_drawdown | 0.1184 | 0.2000 | PASS |
| C/oos | trades | 4.0000 | 60.0000 | FAIL |
| TOTAL | A+B+C | - | - | FAIL |

最终判定：FAIL
