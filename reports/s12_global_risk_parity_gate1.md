# S12 Cross-Asset Risk Parity Gate1 Report

规则：月末 D 收盘后，对实际可得 ETF 池过去 lookback_vol_days=60 个日日收益计算标准差 sigma_i，目标权重 w_i=(1/sigma_i)/sum(1/sigma_j)，下月首个共同交易日开盘调仓。
PIT：复用 S9RiskParityStrategy，target_weights/generate_signals 断言输入 data.date<=as_of_date；OOS 未用于调参。

## 数据可得性
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

## S12 分段关键指标
| regime | start | end | return | max_drawdown | trades | expectancy_after_cost | profit_factor | win_rate | fee_ratio | filled_orders |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| bull | 2020-07-01 | 2021-02-10 | 8.20% | 4.08% | 35 | 2344.03 | 10.5732 | 74.29% | 0.08% | 62 |
| bear | 2022-01-04 | 2022-10-31 | -12.25% | 12.25% | 37 | -3310.82 | 0.1199 | 29.73% | 0.08% | 77 |
| range | 2023-06-01 | 2024-09-30 | 12.14% | 2.85% | 59 | 2057.96 | 10.2339 | 67.80% | 0.08% | 114 |
| oos | 2024-10-08 | 2026-05-15 | 12.01% | 4.52% | 75 | 1601.14 | 14.0651 | 78.67% | 0.08% | 142 |

## in-sample vs OOS 差异
| span | avg/period_return | trades | expectancy | profit_factor | win_rate | worst/max_drawdown |
|---|---:|---:|---:|---:|---:|---:|
| in_sample(bull+bear+range) | 2.70% | 131 | 618.02 | 1.5031 | 58.78% | 12.25% |
| oos | 12.01% | 75 | 1601.14 | 14.0651 | 78.67% | 4.52% |

## 对照组 ratio 表
### bull
| metric | S12 | equal_weight_monthly | 60_40_monthly | HS300ETF_BH | random_weight_monthly |
|---|---:|---:|---:|---:|---:|
| return | 8.20% | 13.16% | 21.22% | 37.91% | 8.84% |
| max_drawdown | 4.08% | 8.35% | 4.00% | 6.77% | 9.92% |
| trades | 35.0000 | 28.0000 | 9.0000 | 1.0000 | 33.0000 |
| fee_ratio | 0.08% | 0.09% | 0.08% | 0.08% | 0.08% |

### bear
| metric | S12 | equal_weight_monthly | 60_40_monthly | HS300ETF_BH | random_weight_monthly |
|---|---:|---:|---:|---:|---:|
| return | -12.25% | -30.90% | -17.85% | -29.44% | -33.00% |
| max_drawdown | 12.25% | 31.45% | 17.85% | 29.44% | 33.58% |
| trades | 37.0000 | 37.0000 | 11.0000 | 1.0000 | 37.0000 |
| fee_ratio | 0.08% | 0.08% | 0.07% | 0.07% | 0.07% |

### range
| metric | S12 | equal_weight_monthly | 60_40_monthly | HS300ETF_BH | random_weight_monthly |
|---|---:|---:|---:|---:|---:|
| return | 12.14% | 20.31% | 9.13% | 10.81% | 14.38% |
| max_drawdown | 2.85% | 7.00% | 13.26% | 22.13% | 8.86% |
| trades | 59.0000 | 50.0000 | 12.0000 | 1.0000 | 58.0000 |
| fee_ratio | 0.08% | 0.09% | 0.08% | 0.08% | 0.08% |

### oos
| metric | S12 | equal_weight_monthly | 60_40_monthly | HS300ETF_BH | random_weight_monthly |
|---|---:|---:|---:|---:|---:|
| return | 12.01% | 26.51% | 2.95% | 4.19% | 22.41% |
| max_drawdown | 4.52% | 11.98% | 12.68% | 20.81% | 12.58% |
| trades | 75.0000 | 66.0000 | 14.0000 | 1.0000 | 72.0000 |
| fee_ratio | 0.08% | 0.09% | 0.08% | 0.08% | 0.08% |

对照组定义：实际可得池等权月度再平衡、60/40(510300/国债ETF)月度再平衡、510300ETF 买入持有、随机权重月度再平衡。

## Gate1 判定表
| group | metric | actual | threshold | result |
|---|---:|---:|---:|---|
| A/bull | expectancy_after_cost | 2344.0310 | 0.0000 | PASS |
| A/bull | profit_factor | 10.5732 | 1.3000 | PASS |
| A/bull | max_drawdown | 0.0408 | 0.2000 | PASS |
| A/bear | expectancy_after_cost | -3310.8152 | 0.0000 | FAIL |
| A/bear | profit_factor | 0.1199 | 1.3000 | FAIL |
| A/bear | max_drawdown | 0.1225 | 0.2000 | PASS |
| A/range | expectancy_after_cost | 2057.9550 | 0.0000 | PASS |
| A/range | profit_factor | 10.2339 | 1.3000 | PASS |
| A/range | max_drawdown | 0.0285 | 0.2000 | PASS |
| B/low_freq | trades | 131.0000 | 月频策略不按200笔卡死 | N/A |
| B/low_freq | expectancy_after_cost | 618.0173 | 0.0000 | PASS |
| B/low_freq | profit_factor | 1.5031 | 1.3000 | PASS |
| C/oos | expectancy_after_cost | 1601.1371 | 0.0000 | PASS |
| C/oos | profit_factor | 14.0651 | 1.3000 | PASS |
| C/oos | max_drawdown | 0.0452 | 0.2000 | PASS |
| C/oos | trades | 75.0000 | 低频不适用；原闸门60 | N/A |
| TOTAL | A+B+C(低频显著性) | - | - | FAIL |

## flag/参数调查记录
- 数据池使用实际可得标的，未补造或外推价格。
- 固定使用 strategy_addon.yaml 的 lookback_vol_days=60，未因结果修改 lookback 或资产池。
- OOS 只在规则、数据源 fallback 和资产池固定后用于最终裁决。
- 未修改成本、滑点、regime 或 Gate1 阈值。

最终判定：FAIL，按低频显著性原则。
