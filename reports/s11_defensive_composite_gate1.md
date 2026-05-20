# S11 Defensive Composite Gate1 Report

规则：每月最后交易日 D 收盘后以及 sh000300 MA200 趋势翻转日，先用 sh000300 close > MA200 判 trend_on/off；trend_on 时对 510300, 510500, 512880, 512010, 511010 计算 60 日 inverse-vol 权重；trend_off 时 100% 511010；D+1 开盘按目标权重再平衡。
PIT：MA200 与 sigma_i 均在策略文件中断言 data.date<=D，且要求当前信号日各序列 max(date)==D；成交全部在下一交易日开盘。
flag: 基于 S3b+S9 partial PASS 的 ex-ante 预注册合成,未碰OOS调参
本策略合成是基于 S3b/S9 各自的 partial PASS，先于看到 S11 OOS 结果之前预注册，非 p-hacking；参数、资产池、MA200、lookback_vol_days=60、成本和 regimes 均未因 S11 结果改动。

## 数据覆盖
| symbol | name | rows | earliest | latest | amount_median | oos_amount_median | source |
|---|---|---:|---:|---:|---:|---:|---|
| sh000300 | trend_signal_index | 5393 | 2004-03-04 | 2026-05-15 | NA | NA | sina_stock_zh_index_daily |
| 510300 | 沪深300ETF | 1489 | 2020-03-23 | 2026-05-15 | 25.48亿 | 40.18亿 | eastmoney_fund_etf_hist_em |
| 510500 | 中证500ETF | 1489 | 2020-03-23 | 2026-05-15 | 13.75亿 | 20.19亿 | eastmoney_fund_etf_hist_em |
| 512880 | 证券ETF | 1567 | 2019-11-25 | 2026-05-15 | 13.37亿 | 19.17亿 | eastmoney_fund_etf_hist_em |
| 512010 | 医药ETF | 1566 | 2019-11-25 | 2026-05-15 | 4.08亿 | 5.48亿 | eastmoney_fund_etf_hist_em |
| 511010 | 国债ETF | 1784 | 2019-01-02 | 2026-05-15 | 1.77亿 | 4.87亿 | eastmoney_fund_etf_hist_em |

### regime 实际可得区间
| regime | configured_start | configured_end | effective_start | effective_end | adjusted |
|---|---:|---:|---:|---:|---|
| bull | 2020-07-01 | 2021-02-10 | 2020-07-01 | 2021-02-10 | NO |
| bear | 2022-01-01 | 2022-10-31 | 2022-01-04 | 2022-10-31 | YES |
| range | 2023-06-01 | 2024-09-30 | 2023-06-01 | 2024-09-30 | NO |
| oos | 2024-10-01 | 2026-05-15 | 2024-10-08 | 2026-05-15 | YES |

## SPEC §3.1 对照组真实数字与提升量
### S11 分段关键指标
| regime | start | end | return | max_drawdown | trades | expectancy_after_cost | profit_factor | win_rate | fee_ratio | filled_orders |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| bull | 2020-07-01 | 2021-02-10 | 9.30% | 3.30% | 26 | 3576.44 | 14.4535 | 88.46% | 0.08% | 44 |
| bear | 2022-01-04 | 2022-10-31 | 1.97% | 0.92% | 1 | 19740.35 | inf | 100.00% | 0.08% | 2 |
| range | 2023-06-01 | 2024-09-30 | 2.55% | 2.94% | 46 | 554.44 | 1.5585 | 32.61% | 0.08% | 95 |
| oos | 2024-10-08 | 2026-05-15 | -1.09% | 4.01% | 65 | -167.22 | 0.8466 | 63.08% | 0.08% | 131 |

### S11 vs S9/S3b 逐 regime 对比
| regime | S11_return | S11_DD | S11_PF | S9_return | S9_DD | S9_PF | S11-S9_return | S9_DD-S11_DD | S3b_return | S3b_DD | S3b_PF | S11-S3b_return | S3b_DD-S11_DD |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| bull | 9.30% | 3.30% | 14.4535 | 9.30% | 3.30% | 14.4535 | 0.00% | 0.00% | 37.91% | 6.77% | inf | -28.61% | 3.47% |
| bear | 1.97% | 0.92% | inf | -7.63% | 8.18% | 0.1559 | 9.60% | 7.26% | 0.00% | 0.00% | 0.0000 | 1.97% | -0.92% |
| range | 2.55% | 2.94% | 1.5585 | 6.16% | 5.19% | 3.8322 | -3.61% | 2.25% | -1.24% | 13.19% | 0.9061 | 3.79% | 10.25% |
| oos | -1.09% | 4.01% | 0.8466 | 1.64% | 3.25% | 1.5051 | -2.73% | -0.76% | -4.13% | 21.01% | 0.8173 | 3.05% | 16.99% |

### bull 对照组真实数字
| strategy | return | max_drawdown | trades | expectancy | profit_factor | win_rate | fee_ratio | filled_orders |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| S11 | 9.30% | 3.30% | 26 | 3576.44 | 14.4535 | 88.46% | 0.08% | 44 |
| S9_no_trend_5ETF | 9.30% | 3.30% | 26 | 3576.44 | 14.4535 | 88.46% | 0.08% | 44 |
| S3b_HS300_cash | 37.91% | 6.77% | 1 | 379123.65 | inf | 100.00% | 0.08% | 2 |
| HS300ETF_BH | 37.91% | 6.77% | 1 | 379123.65 | inf | 100.00% | 0.08% | 2 |
| 60_40_HS300_bond | 21.22% | 4.00% | 9 | 23580.38 | 46.7046 | 77.78% | 0.08% | 17 |
| equal_5ETF | 19.95% | 6.83% | 23 | 8673.38 | 85.3047 | 91.30% | 0.08% | 40 |

### bull S11 相对各 baseline 提升量
| baseline | S11_return_minus_baseline | baseline_DD_minus_S11_DD | S11_expectancy_minus_baseline | S11_PF_minus_baseline |
|---|---:|---:|---:|---:|
| S9_no_trend_5ETF | 0.00% | 0.00% | 0.00 | 0.0000 |
| S3b_HS300_cash | -28.61% | 3.47% | -375547.21 | NA |
| HS300ETF_BH | -28.61% | 3.47% | -375547.21 | NA |
| 60_40_HS300_bond | -11.92% | 0.70% | -20003.94 | -32.2511 |
| equal_5ETF | -10.65% | 3.53% | -5096.94 | -70.8512 |

### bear 对照组真实数字
| strategy | return | max_drawdown | trades | expectancy | profit_factor | win_rate | fee_ratio | filled_orders |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| S11 | 1.97% | 0.92% | 1 | 19740.35 | inf | 100.00% | 0.08% | 2 |
| S9_no_trend_5ETF | -7.63% | 8.18% | 28 | -2724.56 | 0.1559 | 14.29% | 0.08% | 53 |
| S3b_HS300_cash | 0.00% | 0.00% | 0 | 0.00 | 0.0000 | 0.00% | 0.00% | 0 |
| HS300ETF_BH | -29.44% | 29.44% | 1 | -294369.95 | 0.0000 | 0.00% | 0.07% | 2 |
| 60_40_HS300_bond | -17.85% | 17.85% | 11 | -16226.97 | 0.0374 | 54.55% | 0.07% | 20 |
| equal_5ETF | -24.17% | 24.17% | 25 | -9669.24 | 0.0127 | 24.00% | 0.08% | 50 |

### bear S11 相对各 baseline 提升量
| baseline | S11_return_minus_baseline | baseline_DD_minus_S11_DD | S11_expectancy_minus_baseline | S11_PF_minus_baseline |
|---|---:|---:|---:|---:|
| S9_no_trend_5ETF | 9.60% | 7.26% | 22464.91 | NA |
| S3b_HS300_cash | 1.97% | -0.92% | 19740.35 | NA |
| HS300ETF_BH | 31.41% | 28.52% | 314110.30 | NA |
| 60_40_HS300_bond | 19.82% | 16.93% | 35967.32 | NA |
| equal_5ETF | 26.15% | 23.25% | 29409.59 | NA |

### range 对照组真实数字
| strategy | return | max_drawdown | trades | expectancy | profit_factor | win_rate | fee_ratio | filled_orders |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| S11 | 2.55% | 2.94% | 46 | 554.44 | 1.5585 | 32.61% | 0.08% | 95 |
| S9_no_trend_5ETF | 6.16% | 5.19% | 39 | 1579.50 | 3.8322 | 38.46% | 0.08% | 82 |
| S3b_HS300_cash | -1.24% | 13.19% | 8 | -1548.67 | 0.9061 | 12.50% | 0.08% | 16 |
| HS300ETF_BH | 10.81% | 22.13% | 1 | 108092.30 | inf | 100.00% | 0.08% | 2 |
| 60_40_HS300_bond | 9.13% | 13.26% | 12 | 7605.73 | 51.8443 | 75.00% | 0.08% | 25 |
| equal_5ETF | 6.02% | 19.66% | 35 | 1719.39 | 4.2807 | 37.14% | 0.09% | 76 |

### range S11 相对各 baseline 提升量
| baseline | S11_return_minus_baseline | baseline_DD_minus_S11_DD | S11_expectancy_minus_baseline | S11_PF_minus_baseline |
|---|---:|---:|---:|---:|
| S9_no_trend_5ETF | -3.61% | 2.25% | -1025.06 | -2.2737 |
| S3b_HS300_cash | 3.79% | 10.25% | 2103.11 | 0.6524 |
| HS300ETF_BH | -8.26% | 19.18% | -107537.86 | NA |
| 60_40_HS300_bond | -6.58% | 10.31% | -7051.29 | -50.2858 |
| equal_5ETF | -3.47% | 16.71% | -1164.95 | -2.7222 |

### oos 对照组真实数字
| strategy | return | max_drawdown | trades | expectancy | profit_factor | win_rate | fee_ratio | filled_orders |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| S11 | -1.09% | 4.01% | 65 | -167.22 | 0.8466 | 63.08% | 0.08% | 131 |
| S9_no_trend_5ETF | 1.64% | 3.25% | 53 | 309.85 | 1.5051 | 45.28% | 0.08% | 101 |
| S3b_HS300_cash | -4.13% | 21.01% | 4 | -10336.43 | 0.8173 | 50.00% | 0.08% | 11 |
| HS300ETF_BH | 4.19% | 20.81% | 1 | 41894.31 | inf | 100.00% | 0.08% | 2 |
| 60_40_HS300_bond | 2.95% | 12.68% | 14 | 2104.96 | 4.9606 | 57.14% | 0.08% | 29 |
| equal_5ETF | -1.08% | 17.16% | 44 | -245.78 | 0.8665 | 43.18% | 0.09% | 89 |

### oos S11 相对各 baseline 提升量
| baseline | S11_return_minus_baseline | baseline_DD_minus_S11_DD | S11_expectancy_minus_baseline | S11_PF_minus_baseline |
|---|---:|---:|---:|---:|
| S9_no_trend_5ETF | -2.73% | -0.76% | -477.06 | -0.6585 |
| S3b_HS300_cash | 3.05% | 16.99% | 10169.21 | 0.0293 |
| HS300ETF_BH | -5.28% | 16.80% | -42061.53 | NA |
| 60_40_HS300_bond | -4.03% | 8.67% | -2272.17 | -4.1140 |
| equal_5ETF | -0.01% | 13.15% | 78.56 | -0.0199 |

### 趋势状态诊断
| regime | trading_days | trend_on_days | trend_off_days | flip_days | signal_dates | signal_trend_on | signal_trend_off |
|---|---:|---:|---:|---:|---:|---:|---:|
| bull | 154 | 154 | 0 | 0 | 7 | 7 | 0 |
| bear | 198 | 0 | 198 | 0 | 10 | 0 | 10 |
| range | 325 | 47 | 278 | 15 | 31 | 12 | 19 |
| oos | 389 | 383 | 6 | 6 | 26 | 23 | 3 |

## SPEC §3.2 反假设
1. 合成是否真的把 S9 的 bear FAIL 救回来了：bear 段 S9_no_trend return=-7.63%, DD=8.18%, PF=0.1559；S11 return=1.97%, DD=0.92%, PF=inf。bear trend_off_days=198/198，说明规则大部分时间切到国债防御；是否救回以 A/bear 三项 Gate 为准。
2. 趋势翻转 lag 风险：MA200 必然滞后，极端下跌中可能慢 1-2 个月；2008 崩盘这类日历上快速杀跌的阶段尤其容易先亏后防。本次 S11 的 ETF 池受产品上市时间限制，早期 2008/2011 无法用同一 5 ETF 池直接复现，不能把 2022 的防御外推成所有历史崩盘都有效。
3. S11 PASS 是否只是因为 OOS 趋势翻转少：下表把 configs/backtest.yaml 的 full_history cycles 与 OOS 子周期逐一列示，S11 与 HS300ETF 买入持有同口径重跑；NOT_COVERED 表示 5 ETF 池当时未同时存在，不补代理、不拼接指数。
| cycle | kind | configured_start | configured_end | effective_start | effective_end | S11_return | S11_DD | S11_trades | HS300_BH_return | HS300_BH_DD | S11-BH_return | BH_DD-S11_DD | note |
|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---|
| in_bull_1 | bull | 2006-01-01 | 2007-10-16 | NA | NA | NA | NA | NA | NA | NA | NA | NA | NOT_COVERED_BY_5ETF_POOL |
| in_bull_2 | bull | 2008-11-01 | 2009-08-04 | NA | NA | NA | NA | NA | NA | NA | NA | NA | NOT_COVERED_BY_5ETF_POOL |
| in_bull_3 | bull | 2014-07-01 | 2015-06-12 | NA | NA | NA | NA | NA | NA | NA | NA | NA | NOT_COVERED_BY_5ETF_POOL |
| in_bear_1 | bear | 2008-01-14 | 2008-11-04 | NA | NA | NA | NA | NA | NA | NA | NA | NA | NOT_COVERED_BY_5ETF_POOL |
| in_bear_2 | bear | 2011-04-18 | 2012-12-03 | NA | NA | NA | NA | NA | NA | NA | NA | NA | NOT_COVERED_BY_5ETF_POOL |
| in_bear_3 | bear | 2015-06-12 | 2016-01-28 | NA | NA | NA | NA | NA | NA | NA | NA | NA | NOT_COVERED_BY_5ETF_POOL |
| 2018_bear | bear | 2018-01-01 | 2018-12-31 | NA | NA | NA | NA | NA | NA | NA | NA | NA | NOT_COVERED_BY_5ETF_POOL |
| 2019_2021_bull | bull | 2019-01-01 | 2021-12-31 | 2020-03-23 | 2021-12-31 | 1.95% | 6.18% | 83 | 35.93% | 17.19% | -33.98% | 11.00% | ADJUSTED_TO_COMMON_ETF_DATES |
| 2022_bear | bear | 2022-01-01 | 2022-12-31 | 2022-01-04 | 2022-12-30 | 1.54% | 1.20% | 1 | -22.06% | 29.21% | 23.59% | 28.01% | ADJUSTED_TO_COMMON_ETF_DATES |
| 2023_2024_range | range | 2023-01-01 | 2024-09-30 | 2023-01-03 | 2024-09-30 | 2.24% | 2.95% | 79 | 6.93% | 24.18% | -4.68% | 21.23% | ADJUSTED_TO_COMMON_ETF_DATES |
| 2024_2026_recent_oos | oos | 2024-10-01 | 2026-05-15 | 2024-10-08 | 2026-05-15 | -1.09% | 4.01% | 65 | 4.19% | 20.81% | -5.28% | 16.80% | ADJUSTED_TO_COMMON_ETF_DATES |
4. p-hacking 自查：合成规则来自 S3b 的熊市资本保全部分和 S9 的 OOS expectancy/PF/DD partial PASS，写入 strategy_addon.yaml 后才运行 S11；本轮未看 S11 OOS 后修改 MA、lookback、资产池、trend_off 资产、成本或切分。结论无论 PASS/FAIL 均按一次性结果记录。
5. 少亏型而非 alpha 型：即使 Gate1 PASS，也只说明防御组合在这些切分里把大回撤和 bear 段亏损压住，不说明相对沪深300有稳定进攻 alpha。

## SPEC §3.3 Gate1 A/B/C 判定与终局结论
### Gate1 判定表
| group | metric | actual | threshold | result |
|---|---:|---:|---:|---|
| A/bull | expectancy_after_cost | 3576.4435 | 0.0000 | PASS |
| A/bull | profit_factor | 14.4535 | 1.3000 | PASS |
| A/bull | max_drawdown | 0.0330 | 0.2000 | PASS |
| A/bear | expectancy_after_cost | 19740.3476 | 0.0000 | PASS |
| A/bear | profit_factor | inf | 1.3000 | PASS |
| A/bear | max_drawdown | 0.0092 | 0.2000 | PASS |
| A/range | expectancy_after_cost | 554.4404 | 0.0000 | PASS |
| A/range | profit_factor | 1.5585 | 1.3000 | PASS |
| A/range | max_drawdown | 0.0294 | 0.2000 | PASS |
| B/low_freq | trades | 73.0000 | 月频/翻转策略不按200笔卡死 | N/A |
| B/low_freq | expectancy_after_cost | 1893.5909 | 0.0000 | PASS |
| B/low_freq | profit_factor | 3.6289 | 1.3000 | PASS |
| C/oos | expectancy_after_cost | -167.2165 | 0.0000 | FAIL |
| C/oos | profit_factor | 0.8466 | 1.3000 | FAIL |
| C/oos | max_drawdown | 0.0401 | 0.2000 | PASS |
| C/oos | trades | 65.0000 | 低频不适用；原闸门60 | N/A |
| TOTAL | A+B+C(低频显著性) | - | - | FAIL |

OOS 对照：S11 return=-1.09%, DD=4.01%, PF=0.8466；S9_no_trend return=1.64%, DD=3.25%, PF=1.5051；S3b return=-4.13%, DD=21.01%, PF=0.8173。
结论：这是项目第 11 个策略，前 10 个全 FAIL；S11 本次最终判定=FAIL。若 PASS，也仅按少亏型 PASS 记录，不粉饰成赚 alpha 型。
