# S3b Full-History Low-Frequency Significance Report

规则：MA200，D 收盘后 close>MA 则 D+1 持有，否则现金；ma_len 未调，OOS 未用于调参。

## 数据深度实证
| symbol | rows | earliest | latest | error |
|---|---:|---:|---:|---|
| sh000300 | 5186 | 2005-01-04 | 2026-05-15 |  |
| sh000001 | 8640 | 1990-12-19 | 2026-05-15 |  |

主证据序列：sh000001；可投资确认序列：sh000300。两者均覆盖 backtest.yaml full_history 的 2005-04-08 起点，未调整 in_sample 起点。

## 对照组 ratio 表
### 主证据序列整体
| span | S3b_return | S3b_DD | S3b_trades | S3b_expectancy | S3b_PF | S3b_win_rate | BH_return | BH_DD | pass |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---|
| in_sample | 320.85% | 44.32% | 38 | 84434.52 | 2.4093 | 13.16% | 143.18% | 69.95% | N/A |
| oos | -32.15% | 43.65% | 39 | -8244.40 | 0.3480 | 12.82% | 15.33% | 21.04% | FAIL |

### 可投资确认序列整体
| span | S3b_return | S3b_DD | S3b_trades | S3b_expectancy | S3b_PF | S3b_win_rate | BH_return | BH_DD | pass |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---|
| in_sample | 625.86% | 41.40% | 30 | 208620.03 | 3.2453 | 20.00% | 271.04% | 71.04% | N/A |
| oos | -0.91% | 36.40% | 32 | -285.63 | 0.9790 | 15.62% | 14.86% | 39.49% | FAIL |

### 主证据 bull cycles
| cycle | start | end | S3b_return | S3b_DD | S3b_trades | BH_return | BH_DD | pass | criterion |
|---|---:|---:|---:|---:|---:|---:|---:|---|---|
| bull_1 | 2006-01-01 | 2007-10-16 | 390.90% | 16.35% | 1 | 390.90% | 16.35% | PASS | return>0 and DD<=20% |
| bull_2 | 2008-11-01 | 2009-08-04 | 43.80% | 5.79% | 1 | 87.42% | 11.87% | PASS | return>0 and DD<=20% |
| bull_3 | 2014-07-01 | 2015-06-12 | 121.44% | 8.45% | 1 | 123.82% | 8.35% | PASS | return>0 and DD<=20% |

### 主证据 bear cycles
| cycle | start | end | S3b_return | S3b_DD | S3b_trades | BH_return | BH_DD | pass | criterion |
|---|---:|---:|---:|---:|---:|---:|---:|---|---|
| bear_1 | 2008-01-14 | 2008-11-04 | -12.74% | 12.74% | 2 | -38.15% | 38.15% | PASS | S3b DD < buy_hold DD and S3b DD<=20% |
| bear_2 | 2011-04-18 | 2012-12-03 | -9.77% | 9.77% | 2 | -32.77% | 32.77% | PASS | S3b DD < buy_hold DD and S3b DD<=20% |
| bear_3 | 2015-06-12 | 2016-01-28 | -18.24% | 18.24% | 1 | -25.39% | 25.39% | PASS | S3b DD < buy_hold DD and S3b DD<=20% |

### 主证据 OOS 子周期
| cycle | start | end | S3b_return | S3b_DD | S3b_trades | BH_return | BH_DD | pass | criterion |
|---|---:|---:|---:|---:|---:|---:|---:|---|---|
| 2018_bear | 2018-01-01 | 2018-12-31 | -6.71% | 10.37% | 3 | -17.39% | 20.68% | N/A | listed only; OOS overall is judged separately |
| 2019_2021_bull | 2019-01-01 | 2021-12-31 | -18.28% | 30.62% | 18 | 46.42% | 18.48% | N/A | listed only; OOS overall is judged separately |
| 2022_bear | 2022-01-01 | 2022-12-31 | -4.86% | 4.86% | 3 | -11.15% | 15.00% | N/A | listed only; OOS overall is judged separately |
| 2023_2024_range | 2023-01-01 | 2024-09-30 | -8.74% | 18.09% | 14 | 6.03% | 19.22% | N/A | listed only; OOS overall is judged separately |
| 2024_2026_recent | 2024-10-01 | 2026-05-15 | 9.44% | 7.90% | 3 | 13.74% | 7.43% | N/A | listed only; OOS overall is judged separately |

### 可投资确认 bull cycles
| cycle | start | end | S3b_return | S3b_DD | S3b_trades | BH_return | BH_DD | pass | criterion |
|---|---:|---:|---:|---:|---:|---:|---:|---|---|
| bull_1 | 2006-01-01 | 2007-10-16 | 491.21% | 16.61% | 1 | 491.21% | 16.61% | PASS | return>0 and DD<=20% |
| bull_2 | 2008-11-01 | 2009-08-04 | 57.26% | 6.78% | 1 | 127.46% | 13.19% | PASS | return>0 and DD<=20% |
| bull_3 | 2014-07-01 | 2015-06-12 | 121.61% | 8.57% | 1 | 125.95% | 8.34% | PASS | return>0 and DD<=20% |

### 可投资确认 bear cycles
| cycle | start | end | S3b_return | S3b_DD | S3b_trades | BH_return | BH_DD | pass | criterion |
|---|---:|---:|---:|---:|---:|---:|---:|---|---|
| bear_1 | 2008-01-14 | 2008-11-04 | -16.02% | 16.02% | 3 | -41.34% | 41.34% | PASS | S3b DD < buy_hold DD and S3b DD<=20% |
| bear_2 | 2011-04-18 | 2012-12-03 | -11.07% | 11.07% | 5 | -24.81% | 24.81% | PASS | S3b DD < buy_hold DD and S3b DD<=20% |
| bear_3 | 2015-06-12 | 2016-01-28 | -19.40% | 19.40% | 2 | -25.22% | 25.22% | PASS | S3b DD < buy_hold DD and S3b DD<=20% |

### 可投资确认 OOS 子周期
| cycle | start | end | S3b_return | S3b_DD | S3b_trades | BH_return | BH_DD | pass | criterion |
|---|---:|---:|---:|---:|---:|---:|---:|---|---|
| 2018_bear | 2018-01-01 | 2018-12-31 | -7.03% | 12.11% | 2 | -22.00% | 26.46% | N/A | listed only; OOS overall is judged separately |
| 2019_2021_bull | 2019-01-01 | 2021-12-31 | 4.36% | 22.59% | 14 | 58.64% | 17.12% | N/A | listed only; OOS overall is judged separately |
| 2022_bear | 2022-01-01 | 2022-12-31 | 0.00% | 0.00% | 0 | -21.20% | 28.21% | N/A | listed only; OOS overall is judged separately |
| 2023_2024_range | 2023-01-01 | 2024-09-30 | -8.81% | 18.25% | 13 | 2.19% | 19.64% | N/A | listed only; OOS overall is judged separately |
| 2024_2026_recent | 2024-10-01 | 2026-05-15 | 5.98% | 11.84% | 4 | 13.31% | 11.78% | N/A | listed only; OOS overall is judged separately |

## 反假设列表
- 趋势=牛市 beta：用每个独立 bear cycle 的 S3b vs 买入持有回撤比较证伪；若任何熊市 S3b 回撤不低于买入持有或绝对回撤>20%，该机制判 FAIL。
- ma_len 过拟合：以下敏感性表只用 in_sample，不含 OOS，不用于选择参数。
| series | ma_len | in_sample_return | in_sample_DD | trades | expectancy | PF | win_rate | BH_return | BH_DD |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| main/sh000001 | 100 | 401.14% | 50.28% | 50 | 80227.43 | 1.9535 | 16.00% | 143.18% | 69.95% |
| main/sh000001 | 150 | 293.76% | 44.52% | 48 | 61098.10 | 1.8191 | 16.67% | 143.18% | 69.95% |
| main/sh000001 | 200 | 320.85% | 44.32% | 38 | 84434.52 | 2.4093 | 13.16% | 143.18% | 69.95% |
| confirm/sh000300 | 100 | 530.09% | 46.65% | 61 | 86899.61 | 1.9554 | 22.95% | 271.04% | 71.04% |
| confirm/sh000300 | 150 | 713.62% | 40.44% | 34 | 209889.27 | 2.5243 | 20.59% | 271.04% | 71.04% |
| confirm/sh000300 | 200 | 625.86% | 41.40% | 30 | 208620.03 | 3.2453 | 20.00% | 271.04% | 71.04% |
- 上证综指不可直接交易：sh000001 只作机制主证据；可投资确认必须看 sh000300。二者若分歧，最终不能只凭不可交易指数通过。
- ETF/指数一字板约束乐观偏差：指数 limit_up/down 为 NaN，constraints 不触发一字板拒单；偏差方向是略乐观。

## flag/参数调查记录
- 未调 ma_len，默认仍为 200。
- 未触碰 OOS 调参；OOS 只用于最终裁决。
- 未为提高低频交易数而改变换仓频率。

## low_freq_significance 判定表
| series | bull_cycles_all_pass | bear_cycles_all_pass | oos_overall_pass | final |
|---|---|---|---|---|
| main/sh000001 | PASS | PASS | FAIL | FAIL |
| confirm/sh000300 | PASS | PASS | FAIL | FAIL |
| TOTAL | - | - | - | FAIL |

最终判定：FAIL
