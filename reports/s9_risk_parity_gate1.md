# S9 Risk Parity / 低波动策略 Gate1 Report

规则：月末 D 收盘后，对池中 ETF 过去 lookback_vol_days=60 个日日收益计算标准差 sigma_i，目标权重 w_i=(1/sigma_i)/sum(1/sigma_j)，下月首个交易日开盘只交易目标持仓与当前持仓差额。
资产池：510300, 510500, 512880, 512800, 159995, 512010, 511010。本次使用低频显著性原则；月频策略不按高换手 trades>=200 / oos_min_trades=60 卡死，但笔数如实列示。
PIT：策略文件在 target_weights/generate_signals 中断言所有输入 data.date<=as_of_date，波动率只由 D 及以前的收盘收益计算。

## 数据覆盖
### 国债 ETF get_etf_daily 实测
| symbol | rows | earliest | latest | amount_median | oos_amount_median | source | error |
|---|---:|---:|---:|---:|---:|---|---|
| 511010 | 1784 | 2019-01-02 | 2026-05-15 | 1.77亿 | 4.87亿 | eastmoney_fund_etf_hist_em |  |
| 511260 | 1784 | 2019-01-02 | 2026-05-15 | 2.44亿 | 24.00亿 | eastmoney_fund_etf_hist_em |  |

511010 get_etf_daily 可得，按预注册配置使用。

### S9 ETF 数据覆盖
| symbol | name | rows | earliest | latest | amount_median | oos_amount_median | source |
|---|---|---:|---:|---:|---:|---:|---|
| 510300 | 沪深300ETF | 1489 | 2020-03-23 | 2026-05-15 | 25.48亿 | 40.18亿 | eastmoney_fund_etf_hist_em |
| 510500 | 中证500ETF | 1489 | 2020-03-23 | 2026-05-15 | 13.75亿 | 20.19亿 | eastmoney_fund_etf_hist_em |
| 512880 | 证券ETF | 1567 | 2019-11-25 | 2026-05-15 | 13.37亿 | 19.17亿 | eastmoney_fund_etf_hist_em |
| 512800 | 银行ETF | 1567 | 2019-11-25 | 2026-05-15 | 2.99亿 | 6.54亿 | eastmoney_fund_etf_hist_em |
| 159995 | 芯片ETF | 1519 | 2020-02-10 | 2026-05-15 | 6.64亿 | 8.10亿 | eastmoney_fund_etf_hist_em |
| 512010 | 医药ETF | 1566 | 2019-11-25 | 2026-05-15 | 4.08亿 | 5.48亿 | eastmoney_fund_etf_hist_em |
| 511010 | 国债ETF | 1784 | 2019-01-02 | 2026-05-15 | 1.77亿 | 4.87亿 | eastmoney_fund_etf_hist_em |

### regime 实际可得区间
| regime | configured_start | configured_end | effective_start | effective_end | adjusted |
|---|---:|---:|---:|---:|---|
| bull | 2020-07-01 | 2021-02-10 | 2020-07-01 | 2021-02-10 | NO |
| bear | 2022-01-01 | 2022-10-31 | 2022-01-04 | 2022-10-31 | YES |
| range | 2023-06-01 | 2024-09-30 | 2023-06-01 | 2024-09-30 | NO |
| oos | 2024-10-01 | 2026-05-15 | 2024-10-08 | 2026-05-15 | YES |

## S9 分段关键指标
| regime | start | end | return | max_drawdown | trades | expectancy_after_cost | profit_factor | win_rate | fee_ratio | filled_orders |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| bull | 2020-07-01 | 2021-02-10 | 10.35% | 4.72% | 37 | 2796.04 | 18.5466 | 91.89% | 0.08% | 61 |
| bear | 2022-01-04 | 2022-10-31 | -9.47% | 9.47% | 38 | -2492.78 | 0.1168 | 15.79% | 0.08% | 75 |
| range | 2023-06-01 | 2024-09-30 | 7.24% | 6.15% | 56 | 1293.73 | 4.1919 | 42.86% | 0.08% | 114 |
| oos | 2024-10-08 | 2026-05-15 | 2.32% | 3.46% | 74 | 314.10 | 1.4304 | 51.35% | 0.08% | 141 |

## in-sample vs OOS 差异
| span | avg/period_return | trades | expectancy | profit_factor | win_rate | worst/max_drawdown |
|---|---:|---:|---:|---:|---:|---:|
| in_sample(bull+bear+range) | 2.71% | 131 | 619.67 | 1.5976 | 48.85% | 9.47% |
| oos | 2.32% | 74 | 314.10 | 1.4304 | 51.35% | 3.46% |

## 对照组 ratio 表
### bull
| metric | S9 | equal_weight_monthly | 60_40_monthly | HS300ETF_BH | random_weight_monthly | S9/EW | S9/60_40 | S9/HS300 | S9/random | note |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---|
| return | 10.35% | 19.43% | 21.22% | 37.91% | 18.26% | 0.5324 | 0.4875 | 0.2729 | 0.5664 |  |
| max_drawdown | 4.72% | 9.48% | 4.00% | 6.77% | 10.11% | 0.4973 | 1.1783 | 0.6964 | 0.4663 |  |
| trades | 37.0000 | 33.0000 | 9.0000 | 1.0000 | 30.0000 | 1.1212 | 4.1111 | 37.0000 | 1.2333 | ratio>2x，需调查 |
| fee_ratio | 0.08% | 0.09% | 0.08% | 0.08% | 0.08% | 0.9560 | 1.0107 | 0.9807 | 1.0637 |  |

### bear
| metric | S9 | equal_weight_monthly | 60_40_monthly | HS300ETF_BH | random_weight_monthly | S9/EW | S9/60_40 | S9/HS300 | S9/random | note |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---|
| return | -9.47% | -24.92% | -17.85% | -29.44% | -26.28% | 0.3801 | 0.5307 | 0.3218 | 0.3605 |  |
| max_drawdown | 9.47% | 24.92% | 17.85% | 29.44% | 26.28% | 0.3801 | 0.5307 | 0.3218 | 0.3605 |  |
| trades | 38.0000 | 33.0000 | 11.0000 | 1.0000 | 36.0000 | 1.1515 | 3.4545 | 38.0000 | 1.0556 | ratio>2x，需调查 |
| fee_ratio | 0.08% | 0.08% | 0.07% | 0.07% | 0.07% | 1.0096 | 1.1111 | 1.2030 | 1.0917 |  |

### range
| metric | S9 | equal_weight_monthly | 60_40_monthly | HS300ETF_BH | random_weight_monthly | S9/EW | S9/60_40 | S9/HS300 | S9/random | note |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---|
| return | 7.24% | 7.08% | 9.13% | 10.81% | 5.09% | 1.0238 | 0.7938 | 0.6702 | 1.4235 |  |
| max_drawdown | 6.15% | 18.44% | 13.26% | 22.13% | 21.61% | 0.3337 | 0.4641 | 0.2781 | 0.2847 |  |
| trades | 56.0000 | 53.0000 | 12.0000 | 1.0000 | 58.0000 | 1.0566 | 4.6667 | 56.0000 | 0.9655 | ratio>2x，需调查 |
| fee_ratio | 0.08% | 0.09% | 0.08% | 0.08% | 0.08% | 0.9121 | 1.0542 | 1.0770 | 1.1058 |  |

### oos
| metric | S9 | equal_weight_monthly | 60_40_monthly | HS300ETF_BH | random_weight_monthly | S9/EW | S9/60_40 | S9/HS300 | S9/random | note |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---|
| return | 2.32% | 5.24% | 2.95% | 4.19% | -4.54% | 0.4438 | 0.7887 | 0.5548 | -0.5124 |  |
| max_drawdown | 3.46% | 12.07% | 12.68% | 20.81% | 15.17% | 0.2870 | 0.2731 | 0.1664 | 0.2283 |  |
| trades | 74.0000 | 69.0000 | 14.0000 | 1.0000 | 74.0000 | 1.0725 | 5.2857 | 74.0000 | 1.0000 | ratio>2x，需调查 |
| fee_ratio | 0.08% | 0.09% | 0.08% | 0.08% | 0.08% | 0.9252 | 1.0639 | 1.0933 | 1.1050 |  |

对照组定义：等权月度再平衡、60/40(510300/国债ETF)月度再平衡、510300ETF 买入持有、随机权重月度再平衡。ratio>2x 只标注调查，不作为调参依据。

## 反假设列表
1. A股低波动陷阱：低波动资产若长期跑输高波动资产，反波动权重会把资金压到低收益资产上；下表用固定规则生成后的平均权重与同期买入持有收益检查。
| symbol | name | avg_S9_weight | buy_hold_return_gate_span | amount_median |
|---|---|---:|---:|---:|
| 511010 | 国债ETF | 68.14% | 15.48% | 2.91亿 |
| 510300 | 沪深300ETF | 6.76% | 14.19% | 26.26亿 |
| 512800 | 银行ETF | 6.64% | -22.56% | 3.22亿 |
| 510500 | 中证500ETF | 6.09% | 34.06% | 14.01亿 |
| 512010 | 医药ETF | 4.49% | -85.78% | 4.38亿 |
| 512880 | 证券ETF | 4.38% | 1.65% | 13.48亿 |
| 159995 | 芯片ETF | 3.49% | 94.37% | 6.34亿 |

低波动陷阱检查：平均权重最高=511010，Gate span 买入持有收益=15.48%；159995 芯片ETF同期=94.37%。若最高权重资产长期收益低于高波动芯片，S9 的收益拖累来自机制本身而非调参问题。
2. 国债ETF流动性是否真够撑 risk parity：上方数据覆盖表列出全段与 OOS 成交额中位数；本次 511010 OOS 成交额中位数=4.87亿，对 100 万初始资金不构成主要约束，但大资金冲击成本未建模。
3. lookback 敏感性 [30,60,90,120]：以下只跑 in-sample(bull/bear/range)，未触碰 OOS，不用于选择参数。
| lookback_vol_days | in_sample_avg_return | in_sample_worst_DD | trades | expectancy | profit_factor | win_rate |
|---:|---:|---:|---:|---:|---:|---:|
| 30 | 2.59% | 9.54% | 127 | 611.15 | 1.5126 | 50.39% |
| 60 | 2.71% | 9.47% | 131 | 619.67 | 1.5976 | 48.85% |
| 90 | 0.77% | 8.96% | 111 | 208.64 | 1.1944 | 37.84% |
| 120 | 1.45% | 8.72% | 109 | 397.97 | 1.3897 | 42.20% |
4. ETF limit 字段为 NaN 时 constraints.py 不触发一字涨跌停拒单；对 ETF 月频影响较小，但方向仍是略乐观。

## flag/参数调查记录
- 未调参、未碰OOS、国债ETF 选择基于流动性而非收益。
- 固定使用 strategy_addon.yaml 的 lookback_vol_days=60，未因结果修改 lookback 或资产池。
- OOS 只在规则完全固定后用于 C 组最终裁决；敏感性表不包含 OOS。
- 未修改成本、滑点、regime 或 Gate1 阈值。

## Gate1 判定表
| group | metric | actual | threshold | result |
|---|---:|---:|---:|---|
| A/bull | expectancy_after_cost | 2796.0426 | 0.0000 | PASS |
| A/bull | profit_factor | 18.5466 | 1.3000 | PASS |
| A/bull | max_drawdown | 0.0472 | 0.2000 | PASS |
| A/bear | expectancy_after_cost | -2492.7831 | 0.0000 | FAIL |
| A/bear | profit_factor | 0.1168 | 1.3000 | FAIL |
| A/bear | max_drawdown | 0.0947 | 0.2000 | PASS |
| A/range | expectancy_after_cost | 1293.7285 | 0.0000 | PASS |
| A/range | profit_factor | 4.1919 | 1.3000 | PASS |
| A/range | max_drawdown | 0.0615 | 0.2000 | PASS |
| B/low_freq | trades | 131.0000 | 月频策略不按200笔卡死 | N/A |
| B/low_freq | expectancy_after_cost | 619.6688 | 0.0000 | PASS |
| B/low_freq | profit_factor | 1.5976 | 1.3000 | PASS |
| C/oos | expectancy_after_cost | 314.0957 | 0.0000 | PASS |
| C/oos | profit_factor | 1.4304 | 1.3000 | PASS |
| C/oos | max_drawdown | 0.0346 | 0.2000 | PASS |
| C/oos | trades | 74.0000 | 低频不适用；原闸门60 | N/A |
| TOTAL | A+B+C(低频显著性) | - | - | FAIL |

## 与已 FAIL 的 8 个策略对比
S1-S8 既有 Gate1 报告最终均为 FAIL；S3b/S3c 低频趋势衍生也为 FAIL。S9 本轮最终判定=FAIL，因此不是首次过 Gate，A/B/C 仍未同时成立。

最终判定：FAIL，按低频显著性原则。
