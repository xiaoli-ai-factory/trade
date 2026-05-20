# S13 Vol-Targeted Global Risk Parity Gate1 Report

规则：S13 100% 复用 S12 七 ETF 池。月末 D 收盘后，用过去 lookback_vol_days=60 个日日收益先算 inverse-vol 权重 w_i=(1/sigma_i)/sum(1/sigma_j)，再用同一 60 日收益协方差矩阵计算 sigma_port=sqrt(w'Σw)*sqrt(252)，leverage_target=8.00%/sigma_port，按 [0.3, 1.5] 裁剪，最终权重=w_i*leverage；1-leverage 作为现金或融资现金，现金收益/风险按 0 处理。
执行：下个共同交易日开盘成交，S13 为表达预注册 1.5x 上限允许现金为负；每笔成交仍复用 constraints.py 的 5 元佣金地板、滑点、涨跌停/停牌拒单与 T+1。S12/S9/等权/60-40/买入持有对照组使用原现金约束。
PIT：S13 策略在 target_profile 中断言所有输入 data.date<=D，并要求每个 ETF 序列 max(date)==D；sigma_i 与协方差矩阵都只由 D 及以前的收盘收益计算。

## 数据覆盖
| code | name | class | 成功的数据源 | earliest | latest | rows | covers_regimes(bull/bear/range/oos) | note |
|---|---|---|---|---:|---:|---:|---|---|
| 510300 | 沪深300ETF | a_share_large | sina_fund_etf_hist_sina | 2012-05-28 | 2026-05-15 | 3392 | yes/yes/yes/yes |  |
| 510500 | 中证500ETF | a_share_mid | sina_fund_etf_hist_sina | 2013-03-15 | 2026-05-15 | 3195 | yes/yes/yes/yes |  |
| 159920 | 恒生ETF | hk_share | sina_fund_etf_hist_sina | 2012-10-22 | 2026-05-15 | 3293 | yes/yes/yes/yes |  |
| 513100 | 纳指ETF | us_share | sina_fund_etf_hist_sina | 2013-07-31 | 2026-05-15 | 3106 | yes/yes/yes/yes |  |
| 513500 | 标普500ETF | us_share_alt | sina_fund_etf_hist_sina | 2014-01-15 | 2026-05-15 | 2994 | yes/yes/yes/yes |  |
| 518880 | 黄金ETF | commodity | sina_fund_etf_hist_sina | 2013-07-29 | 2026-05-15 | 3109 | yes/yes/yes/yes |  |
| 511010 | 国债ETF | bond | sina_fund_etf_hist_sina | 2013-04-09 | 2026-05-15 | 3182 | yes/yes/yes/yes |  |

## 最终池配置
| code | name | class |
|---|---|---|
| 510300 | 沪深300ETF | a_share_large |
| 510500 | 中证500ETF | a_share_mid |
| 159920 | 恒生ETF | hk_share |
| 513100 | 纳指ETF | us_share |
| 513500 | 标普500ETF | us_share_alt |
| 518880 | 黄金ETF | commodity |
| 511010 | 国债ETF | bond |

## regime 实际可得区间
| regime | configured_start | configured_end | effective_start | effective_end | adjusted |
|---|---:|---:|---:|---:|---|
| bull | 2020-07-01 | 2021-02-10 | 2020-07-01 | 2021-02-10 | NO |
| bear | 2022-01-01 | 2022-10-31 | 2022-01-04 | 2022-10-31 | YES |
| range | 2023-06-01 | 2024-09-30 | 2023-06-01 | 2024-09-30 | NO |
| oos | 2024-10-01 | 2026-05-15 | 2024-10-08 | 2026-05-15 | YES |

## SPEC §3.1 对照组真实数字与 S13/S12 提升退化
### S13 分段关键指标
| regime | start | end | return | max_drawdown | trades | expectancy_after_cost | profit_factor | win_rate | fee_ratio | filled_orders |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| bull | 2020-07-01 | 2021-02-10 | 10.11% | 4.78% | 27 | 3744.18 | 11.2688 | 77.78% | 0.08% | 60 |
| bear | 2022-01-04 | 2022-10-31 | -17.97% | 17.97% | 38 | -4729.71 | 0.1150 | 34.21% | 0.08% | 75 |
| range | 2023-06-01 | 2024-09-30 | 18.64% | 4.26% | 58 | 3213.54 | 10.4493 | 67.24% | 0.08% | 117 |
| oos | 2024-10-08 | 2026-05-15 | 17.97% | 6.74% | 81 | 2218.47 | 12.7271 | 81.48% | 0.08% | 146 |

### S13 vs S12 逐 regime 提升/退化表
| regime | S13_return | S12_return | return_delta | S13_DD | S12_DD | DD_reduction | S13_expectancy | S12_expectancy | expectancy_delta | gross_loss_reduction | S13_PF | S12_PF |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| bull | 10.11% | 8.20% | 1.91% | 4.78% | 4.08% | -0.70% | 3744.18 | 2344.03 | 1400.15 | -1274.73 | 11.2688 | 10.5732 |
| bear | -17.97% | -12.25% | -5.72% | 17.97% | 12.25% | -5.72% | -4729.71 | -3310.82 | -1418.90 | -63901.68 | 0.1150 | 0.1199 |
| range | 18.64% | 12.14% | 6.50% | 4.26% | 2.85% | -1.41% | 3213.54 | 2057.96 | 1155.58 | -6575.47 | 10.4493 | 10.2339 |
| oos | 17.97% | 12.01% | 5.96% | 6.74% | 4.52% | -2.22% | 2218.47 | 1601.14 | 617.33 | -6131.92 | 12.7271 | 14.0651 |

### bull 对照组真实数字
| strategy | return | max_drawdown | trades | expectancy | gross_loss | profit_factor | win_rate | fee_ratio | filled_orders |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| S13_vol_targeted_RP | 10.11% | 4.78% | 27 | 3744.18 | 9844.64 | 11.2688 | 77.78% | 0.08% | 60 |
| S12_no_vol_target | 8.20% | 4.08% | 35 | 2344.03 | 8569.90 | 10.5732 | 74.29% | 0.08% | 62 |
| S9_single | 10.35% | 4.72% | 37 | 2796.04 | 5895.94 | 18.5466 | 91.89% | 0.08% | 61 |
| equal_weight_7ETF | 13.16% | 8.35% | 28 | 4699.39 | 9306.73 | 15.1385 | 78.57% | 0.09% | 59 |
| 60_40_HS300_bond | 21.22% | 4.00% | 9 | 23580.38 | 4643.37 | 46.7046 | 77.78% | 0.08% | 17 |
| HS300ETF_BH | 37.91% | 6.77% | 1 | 379123.65 | 0.00 | inf | 100.00% | 0.08% | 2 |

### bear 对照组真实数字
| strategy | return | max_drawdown | trades | expectancy | gross_loss | profit_factor | win_rate | fee_ratio | filled_orders |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| S13_vol_targeted_RP | -17.97% | 17.97% | 38 | -4729.71 | 203091.00 | 0.1150 | 34.21% | 0.08% | 75 |
| S12_no_vol_target | -12.25% | 12.25% | 37 | -3310.82 | 139189.32 | 0.1199 | 29.73% | 0.08% | 77 |
| S9_single | -9.47% | 9.47% | 38 | -2492.78 | 107253.66 | 0.1168 | 15.79% | 0.08% | 75 |
| equal_weight_7ETF | -30.90% | 31.45% | 37 | -8352.27 | 317648.62 | 0.0271 | 27.03% | 0.08% | 69 |
| 60_40_HS300_bond | -17.85% | 17.85% | 11 | -16226.97 | 185439.14 | 0.0374 | 54.55% | 0.07% | 20 |
| HS300ETF_BH | -29.44% | 29.44% | 1 | -294369.95 | 294369.95 | 0.0000 | 0.00% | 0.07% | 2 |

### range 对照组真实数字
| strategy | return | max_drawdown | trades | expectancy | gross_loss | profit_factor | win_rate | fee_ratio | filled_orders |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| S13_vol_targeted_RP | 18.64% | 4.26% | 58 | 3213.54 | 19724.71 | 10.4493 | 67.24% | 0.08% | 117 |
| S12_no_vol_target | 12.14% | 2.85% | 59 | 2057.96 | 13149.24 | 10.2339 | 67.80% | 0.08% | 114 |
| S9_single | 7.24% | 6.15% | 56 | 1293.73 | 22697.76 | 4.1919 | 42.86% | 0.08% | 114 |
| equal_weight_7ETF | 20.31% | 7.00% | 50 | 4061.99 | 2223.21 | 92.3543 | 80.00% | 0.09% | 106 |
| 60_40_HS300_bond | 9.13% | 13.26% | 12 | 7605.73 | 1795.06 | 51.8443 | 75.00% | 0.08% | 25 |
| HS300ETF_BH | 10.81% | 22.13% | 1 | 108092.30 | 0.00 | inf | 100.00% | 0.08% | 2 |

### oos 对照组真实数字
| strategy | return | max_drawdown | trades | expectancy | gross_loss | profit_factor | win_rate | fee_ratio | filled_orders |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| S13_vol_targeted_RP | 17.97% | 6.74% | 81 | 2218.47 | 15323.18 | 12.7271 | 81.48% | 0.08% | 146 |
| S12_no_vol_target | 12.01% | 4.52% | 75 | 1601.14 | 9191.27 | 14.0651 | 78.67% | 0.08% | 142 |
| S9_single | 2.32% | 3.46% | 74 | 314.10 | 54003.30 | 1.4304 | 51.35% | 0.08% | 141 |
| equal_weight_7ETF | 26.51% | 11.98% | 66 | 4016.30 | 2727.54 | 98.1850 | 83.33% | 0.09% | 134 |
| 60_40_HS300_bond | 2.95% | 12.68% | 14 | 2104.96 | 7440.67 | 4.9606 | 57.14% | 0.08% | 29 |
| HS300ETF_BH | 4.19% | 20.81% | 1 | 41894.31 | 0.00 | inf | 100.00% | 0.08% | 2 |

## SPEC §3.2 反假设
1. Vol Targeting 是否真的把 bear 救回：bear 段 S12 return=-12.25%, DD=12.25%, expectancy=-3310.82, gross_loss=139189.32；S13 return=-17.97%, DD=17.97%, expectancy=-4729.71, gross_loss=203091.00。DD_reduction=-5.72%，gross_loss_reduction=-63901.68，以此判断是否只是换了收益路径。
### bear leverage 历史变化
| signal_date(D close) | trade_date(D+1 open) | sigma_port_annual | leverage_target | clipped_leverage | cash_weight |
|---|---|---:|---:|---:|---:|
| 2021-12-31 | 2022-01-04 | 3.48% | 2.3005 | 1.5000 | -50.00% |
| 2022-01-28 | 2022-02-07 | 4.31% | 1.8575 | 1.5000 | -50.00% |
| 2022-02-28 | 2022-03-01 | 5.04% | 1.5872 | 1.5000 | -50.00% |
| 2022-03-31 | 2022-04-01 | 5.80% | 1.3786 | 1.3786 | -37.86% |
| 2022-04-29 | 2022-05-05 | 6.04% | 1.3250 | 1.3250 | -32.50% |
| 2022-05-31 | 2022-06-01 | 5.17% | 1.5482 | 1.5000 | -50.00% |
| 2022-06-30 | 2022-07-01 | 4.04% | 1.9815 | 1.5000 | -50.00% |
| 2022-07-29 | 2022-08-01 | 3.58% | 2.2375 | 1.5000 | -50.00% |
| 2022-08-31 | 2022-09-01 | 3.65% | 2.1893 | 1.5000 | -50.00% |
| 2022-09-30 | 2022-10-10 | 4.29% | 1.8638 | 1.5000 | -50.00% |

2. OOS leverage 平均值与分布：若长期等于 1.5 上限，vol targeting 形同固定杠杆；若长期低于 0.5，则可能过度防御丢收益。
| regime | signals | avg_leverage | min | p25 | median | p75 | max | avg_port_vol_annual | pct_at_min | pct_at_max | avg_cash_weight |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| bull | 8 | 1.2983 | 1.0324 | 1.1588 | 1.3089 | 1.4575 | 1.5000 | 6.10% | 0.00% | 25.00% | -29.83% |
| bear | 10 | 1.4704 | 1.3250 | 1.5000 | 1.5000 | 1.5000 | 1.5000 | 4.54% | 0.00% | 80.00% | -47.04% |
| range | 16 | 1.5000 | 1.5000 | 1.5000 | 1.5000 | 1.5000 | 1.5000 | 3.26% | 0.00% | 100.00% | -50.00% |
| oos | 20 | 1.4682 | 1.2391 | 1.5000 | 1.5000 | 1.5000 | 1.5000 | 4.50% | 0.00% | 80.00% | -46.82% |
OOS leverage 平均=1.4682，80.00% 信号日在 1.5 上限，vol targeting 大部分时间形同固定顶格杠杆。
OOS 对照：S13 return=17.97%, DD=6.74%, PF=12.7271；S12 return=12.01%, DD=4.52%, PF=14.0651。

3. 与 Bridgewater 全天候原版差异：原版 All Weather 的风险预算覆盖美元债、通胀联结债、商品、股票等，并能使用机构级期货/互换/融资和再平衡执行；本实验只用国内可交易 ETF 代理 A 股、港股、美股、黄金、国债，QDII 折溢价、A 股 T+1、涨跌停/停牌、散户佣金地板、滑点和无融资成本假设都会让结果不同。这里验证的是国内 ETF 近似机制，不是 Bridgewater 原产品复刻。

4. flag: 参数全预注册不调 / 未碰OOS / S12 池子100%复用未挑选。target_vol、leverage_min/max、lookback、资产池、成本、regime、Gate1 阈值均未因结果改动；S13 是 backtest 阶段最后一次试验。

## SPEC §3.3 A/B/C 判定与最终结论
### in-sample vs OOS
| span | avg/period_return | trades | expectancy | profit_factor | win_rate | worst/max_drawdown |
|---|---:|---:|---:|---:|---:|---:|
| in_sample(bull+bear+range) | 3.59% | 123 | 876.01 | 1.4631 | 59.35% | 17.97% |
| oos | 17.97% | 81 | 2218.47 | 12.7271 | 81.48% | 6.74% |

### Gate1 判定表
| group | metric | actual | threshold | result |
|---|---:|---:|---:|---|
| A/bull | expectancy_after_cost | 3744.1841 | 0.0000 | PASS |
| A/bull | profit_factor | 11.2688 | 1.3000 | PASS |
| A/bull | max_drawdown | 0.0478 | 0.2000 | PASS |
| A/bear | expectancy_after_cost | -4729.7128 | 0.0000 | FAIL |
| A/bear | profit_factor | 0.1150 | 1.3000 | FAIL |
| A/bear | max_drawdown | 0.1797 | 0.2000 | PASS |
| A/range | expectancy_after_cost | 3213.5364 | 0.0000 | PASS |
| A/range | profit_factor | 10.4493 | 1.3000 | PASS |
| A/range | max_drawdown | 0.0426 | 0.2000 | PASS |
| B/low_freq | trades | 123.0000 | 月频策略不按200笔卡死 | N/A |
| B/low_freq | expectancy_after_cost | 876.0081 | 0.0000 | PASS |
| B/low_freq | profit_factor | 1.4631 | 1.3000 | PASS |
| C/oos | expectancy_after_cost | 2218.4696 | 0.0000 | PASS |
| C/oos | profit_factor | 12.7271 | 1.3000 | PASS |
| C/oos | max_drawdown | 0.0674 | 0.2000 | PASS |
| C/oos | trades | 81.0000 | 低频不适用；原闸门60 | N/A |
| TOTAL | A+B+C(低频显著性) | - | - | FAIL |

A/B/C 汇总：A=FAIL / B=PASS / C=PASS / 最终=FAIL。
结论：S13 是 backtest 阶段最后一次试验，最终判定=FAIL。无论 PASS/FAIL，之后停止 hunting 转 forward paper。
