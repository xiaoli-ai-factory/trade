# S3c Faber Monthly Trend Gate1 Report

规则：Faber(2007) 经典月频趋势；仅用月末收盘序列计算 10个月 SMA，月末 close>SMA 则下月首个交易日开盘持有，否则现金。
参数：ma_len_months=10，未调参；OOS 未用于参数选择；这是硬一次性最终试验。

## 数据深度实证
| symbol | rows | earliest | latest | error |
|---|---:|---:|---:|---|
| sh000300 | 5186 | 2005-01-04 | 2026-05-15 |  |
| sh000001 | 8640 | 1990-12-19 | 2026-05-15 |  |

主证据序列：sh000001；可投资确认序列：sh000300。full_history in_sample/OOS 起止完全使用 configs/backtest.yaml。
月末信号日期由该序列实际交易日历的每月最后一个交易日确定；策略收到的 daily/monthly 数据均截断到 as_of_date。

## 对照组 ratio 表
### 主证据序列整体
| span | S3c_return | S3c_DD | S3c_trades | S3c_expectancy | S3c_PF | S3c_win_rate | BH_return | BH_DD | failed_S3b_return | failed_S3b_DD | pass |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---|
| in_sample | 330.69% | 43.04% | 9 | 367438.44 | 3.7018 | 55.56% | 143.18% | 69.95% | 320.85% | 44.32% | N/A |
| oos | -5.33% | 28.72% | 8 | -6660.06 | 0.7539 | 25.00% | 15.33% | 21.04% | -32.15% | 43.65% | FAIL |

### 可投资确认序列整体
| span | S3c_return | S3c_DD | S3c_trades | S3c_expectancy | S3c_PF | S3c_win_rate | BH_return | BH_DD | failed_S3b_return | failed_S3b_DD | pass |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---|
| in_sample | 512.69% | 47.96% | 8 | 640863.49 | 3.7966 | 50.00% | 271.04% | 71.04% | 625.86% | 41.40% | N/A |
| oos | -7.81% | 37.65% | 10 | -7805.97 | 0.7650 | 30.00% | 14.86% | 39.49% | -0.91% | 36.40% | FAIL |

### 主证据 bull cycles
| cycle | start | end | S3c_return | S3c_DD | S3c_trades | BH_return | BH_DD | S3b_return | S3b_DD | pass | criterion |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---|---|
| bull_1 | 2006-01-01 | 2007-10-16 | 336.45% | 15.99% | 1 | 390.90% | 16.35% | 390.90% | 16.35% | PASS | S3c return>0 and S3c DD<=20% |
| bull_2 | 2008-11-01 | 2009-08-04 | 42.95% | 5.84% | 1 | 87.42% | 11.87% | 43.80% | 5.79% | PASS | S3c return>0 and S3c DD<=20% |
| bull_3 | 2014-07-01 | 2015-06-12 | 118.02% | 8.60% | 1 | 123.82% | 8.35% | 121.44% | 8.45% | PASS | S3c return>0 and S3c DD<=20% |

### 主证据 bear cycles
| cycle | start | end | S3c_return | S3c_DD | S3c_trades | BH_return | BH_DD | S3b_return | S3b_DD | pass | criterion |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---|---|
| bear_1 | 2008-01-14 | 2008-11-04 | 0.00% | 0.00% | 0 | -38.15% | 38.15% | -12.74% | 12.74% | PASS | S3c DD < buy_hold DD and S3c DD<=20% |
| bear_2 | 2011-04-18 | 2012-12-03 | -10.24% | 11.55% | 2 | -32.77% | 32.77% | -9.77% | 9.77% | PASS | S3c DD < buy_hold DD and S3c DD<=20% |
| bear_3 | 2015-06-12 | 2016-01-28 | -21.52% | 25.93% | 1 | -25.39% | 25.39% | -18.24% | 18.24% | FAIL | S3c DD < buy_hold DD and S3c DD<=20% |

### 主证据 OOS 子周期
| cycle | start | end | S3c_return | S3c_DD | S3c_trades | BH_return | BH_DD | S3b_return | S3b_DD | pass | criterion |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---|---|
| 2018_bear | 2018-01-01 | 2018-12-31 | -5.24% | 7.15% | 1 | -17.39% | 20.68% | -6.71% | 10.37% | N/A | listed only; OOS overall is judged separately |
| 2019_2021_bull | 2019-01-01 | 2021-12-31 | 4.44% | 20.10% | 4 | 46.42% | 18.48% | -18.28% | 30.62% | N/A | listed only; OOS overall is judged separately |
| 2022_bear | 2022-01-01 | 2022-12-31 | 0.00% | 0.00% | 0 | -11.15% | 15.00% | -4.86% | 4.86% | N/A | listed only; OOS overall is judged separately |
| 2023_2024_range | 2023-01-01 | 2024-09-30 | -10.13% | 13.67% | 2 | 6.03% | 19.22% | -8.74% | 18.09% | N/A | listed only; OOS overall is judged separately |
| 2024_2026_recent | 2024-10-01 | 2026-05-15 | 25.17% | 10.62% | 1 | 13.74% | 7.43% | 9.44% | 7.90% | N/A | listed only; OOS overall is judged separately |

### 可投资确认 bull cycles
| cycle | start | end | S3c_return | S3c_DD | S3c_trades | BH_return | BH_DD | S3b_return | S3b_DD | pass | criterion |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---|---|
| bull_1 | 2006-01-01 | 2007-10-16 | 435.81% | 16.46% | 1 | 491.21% | 16.61% | 491.21% | 16.61% | PASS | S3c return>0 and S3c DD<=20% |
| bull_2 | 2008-11-01 | 2009-08-04 | 37.46% | 5.53% | 1 | 127.46% | 13.19% | 57.26% | 6.78% | PASS | S3c return>0 and S3c DD<=20% |
| bull_3 | 2014-07-01 | 2015-06-12 | 118.85% | 8.73% | 1 | 125.95% | 8.34% | 121.61% | 8.57% | PASS | S3c return>0 and S3c DD<=20% |

### 可投资确认 bear cycles
| cycle | start | end | S3c_return | S3c_DD | S3c_trades | BH_return | BH_DD | S3b_return | S3b_DD | pass | criterion |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---|---|
| bear_1 | 2008-01-14 | 2008-11-04 | 0.00% | 0.00% | 0 | -41.34% | 41.34% | -16.02% | 16.02% | PASS | S3c DD < buy_hold DD and S3c DD<=20% |
| bear_2 | 2011-04-18 | 2012-12-03 | -12.45% | 13.93% | 2 | -24.81% | 24.81% | -11.07% | 11.07% | PASS | S3c DD < buy_hold DD and S3c DD<=20% |
| bear_3 | 2015-06-12 | 2016-01-28 | -22.66% | 27.87% | 1 | -25.22% | 25.22% | -19.40% | 19.40% | FAIL | S3c DD < buy_hold DD and S3c DD<=20% |

### 可投资确认 OOS 子周期
| cycle | start | end | S3c_return | S3c_DD | S3c_trades | BH_return | BH_DD | S3b_return | S3b_DD | pass | criterion |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---|---|
| 2018_bear | 2018-01-01 | 2018-12-31 | -8.03% | 8.91% | 1 | -22.00% | 26.46% | -7.03% | 12.11% | N/A | listed only; OOS overall is judged separately |
| 2019_2021_bull | 2019-01-01 | 2021-12-31 | 11.46% | 16.00% | 3 | 58.64% | 17.12% | 4.36% | 22.59% | N/A | listed only; OOS overall is judged separately |
| 2022_bear | 2022-01-01 | 2022-12-31 | 0.00% | 0.00% | 0 | -21.20% | 28.21% | 0.00% | 0.00% | N/A | listed only; OOS overall is judged separately |
| 2023_2024_range | 2023-01-01 | 2024-09-30 | -17.41% | 17.67% | 3 | 2.19% | 19.64% | -8.81% | 18.25% | N/A | listed only; OOS overall is judged separately |
| 2024_2026_recent | 2024-10-01 | 2026-05-15 | 10.53% | 10.59% | 3 | 13.31% | 11.78% | 5.98% | 11.84% | N/A | listed only; OOS overall is judged separately |

## 反假设列表
- 趋势=牛市 beta：用每个独立 bear cycle 的 S3c vs 买入持有回撤比较证伪；若任何熊市 S3c 回撤不低于买入持有或绝对回撤>20%，该机制判 FAIL。
- 月频是否仍在 OOS 牛市 whipsaw/掉队：直接看 2019-2021 牛市段。
  main/sh000001 2019-2021: S3c return=4.44%, DD=20.10%; buy_hold return=46.42%, DD=18.48%; failed_S3b return=-18.28%, DD=30.62%.
  confirm/sh000300 2019-2021: S3c return=11.46%, DD=16.00%; buy_hold return=58.64%, DD=17.12%; failed_S3b return=4.36%, DD=22.59%.
- 上证综指不可直接交易：sh000001 只作机制主证据；可投资确认必须看 sh000300。二者若分歧，最终不能只凭不可交易指数通过。
- 指数一字板约束乐观偏差：limit_up/down 为 NaN，constraints 不触发一字板拒单；偏差方向是略乐观。

## flag/参数调查记录
- 未调 ma_len_months，固定 10个月 SMA。
- 未触碰 OOS 调参；OOS 只用于最终裁决。
- 硬一次性最终试验；未为通过闸门改变信号频率、周期表或判据。
- 未修改 S1/S2/S3/S3b 代码路径；S3b 仅作为已失败对照重跑同周期指标。

## low_freq_significance 判定表
| series | bull_cycles_all_pass | bear_cycles_all_pass | oos_overall_pass | final |
|---|---|---|---|---|
| main/sh000001 | PASS | FAIL | FAIL | FAIL |
| confirm/sh000300 | PASS | FAIL | FAIL | FAIL |
| TOTAL | - | - | - | FAIL |

### OOS overall 判据明细
| series | expectancy>0 | PF>=1.3 | DD<=20% | actual_expectancy | actual_PF | actual_DD | result |
|---|---|---|---|---:|---:|---:|---|
| main/sh000001 | FAIL | FAIL | FAIL | -6660.06 | 0.7539 | 28.72% | FAIL |
| confirm/sh000300 | FAIL | FAIL | FAIL | -7805.97 | 0.7650 | 37.65% | FAIL |

最终判定：FAIL
