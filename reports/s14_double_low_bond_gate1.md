# S14 Convertible Bond Double-Low Gate1 Report

规则：每月最后交易日 D 收盘后，用截至 D 的可转债日线和估值分析表取最新可得行；过滤价格、转股溢价率、成交额近似、强赎/临近退市、新券后，按 `price + premium_rate * 100` 升序取 Top10，下月首交易日开盘等权调仓，只交易差额。
PIT：价格来自 `bond_zh_hs_cov_daily` 的 D 或 D 前最近交易行；`premium_rate` 来自 `bond_zh_cov_value_analysis` 的 D 或 D 前最近行；强赎计数用 `convert_value >= 130` 的最近 30 个交易日计数近似。OOS 未用于调参。

## 数据面板与反幸存者
| metric | value |
|---|---:|
| raw_universe | 983 |
| attempted | 983 |
| panel_symbols | 909 |
| panel_rows | 1403496 |
| panel_start | 2020-01-02 |
| panel_end | 2026-05-21 |
| delisted_or_matured_symbols_in_panel | 580 |
| failed_symbols | 1 |
| fail_rate | 0.10% |
| elapsed_minutes | 0.05 |
| used_cached_panel | True |

面板缓存：`data/cache/cb_panel_pit_2020_2026.parquet`。字段含 `open/high/low/close/volume/premium_rate/pure_bond_value/convert_value/listing_date/delist_date/in_universe`，并额外保留 `amount_proxy` 与 `redeem_trigger_count_30`。
抓取耗时：首次 AkShare 抓取含一次中断续跑，实际从批量续跑到 panel 写出约 14 分钟；本报告最终 Gate1 为缓存复跑，所以上表 `elapsed_minutes` 是缓存加载耗时。

## regime 实际可得区间
| regime | configured_start | configured_end | effective_start | effective_end | adjusted |
|---|---:|---:|---:|---:|---|
| bull | 2020-07-01 | 2021-02-10 | 2020-07-01 | 2021-02-10 | NO |
| bear | 2022-01-01 | 2022-10-31 | 2022-01-04 | 2022-10-31 | YES |
| range | 2023-06-01 | 2024-09-30 | 2023-06-01 | 2024-09-30 | NO |
| oos | 2024-10-01 | 2026-05-15 | 2024-10-08 | 2026-05-15 | YES |

## 候选过滤诊断
| metric | value |
|---|---:|
| months | 70 |
| avg_in_universe | 436.3 |
| avg_after_price | 305.8 |
| avg_after_premium | 108.1 |
| avg_after_liquidity | 99.5 |
| avg_after_redeem | 96.6 |
| avg_selected | 10.0 |
| selected_avg_price | 109.86 |
| selected_avg_premium_rate | 10.89% |

## S14 分段关键指标
| regime | start | end | return | max_drawdown | trades | expectancy_after_cost | profit_factor | win_rate | fee_ratio | filled_orders | rejected_orders |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| bull | 2020-07-01 | 2021-02-10 | -5.27% | 23.48% | 62 | -850.02 | 0.7693 | 40.32% | 0.03% | 124 | 0 |
| bear | 2022-01-04 | 2022-10-31 | -3.92% | 12.81% | 69 | -568.77 | 0.7092 | 30.43% | 0.03% | 133 | 0 |
| range | 2023-06-01 | 2024-09-30 | 9.41% | 14.23% | 106 | 887.43 | 1.4378 | 55.66% | 0.03% | 219 | 1 |
| oos | 2024-10-08 | 2026-05-15 | 4.30% | 12.37% | 124 | 346.72 | 1.1872 | 52.42% | 0.03% | 262 | 4 |

## in-sample vs OOS 差异
| span | avg/period_return | trades | expectancy | profit_factor | win_rate | worst/max_drawdown |
|---|---:|---:|---:|---:|---:|---:|
| in_sample(bull+bear+range) | 0.07% | 237 | 8.95 | 1.0037 | 44.30% | 23.48% |
| oos | 4.30% | 124 | 346.72 | 1.1872 | 52.42% | 12.37% |

月均换手率：93.80%；全期成交额/平均 NAV/月数口径，traded_amount=52071001.46，months=54。

## 对照组真实数字与 ratio
### bull
| metric | S14 | all_filtered_EW | random10 | HS300ETF_BH | S12_RP | S14/all | S14/random | S14/HS300 | S14/S12 |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| return | -5.27% | 1.61% | -10.25% | 37.91% | 8.20% | -3.2676 | 0.5144 | -0.1390 | -0.6424 |
| max_drawdown | 23.48% | 15.39% | 24.03% | 6.77% | 4.08% | 1.5249 | 0.9771 | 3.4657 | 5.7478 |
| trades | 62.0000 | 586.0000 | 76.0000 | 1.0000 | 35.0000 | 0.1058 | 0.8158 | 62.0000 | 1.7714 |
| profit_factor | 0.7693 | 1.0956 | 0.6306 | inf | 10.5732 | 0.7022 | 1.2199 | NA | 0.0728 |
| expectancy | -850.0154 | 27.5231 | -1348.1528 | 379123.6497 | 2344.0310 | -30.8838 | 0.6305 | -0.0022 | -0.3626 |

### bear
| metric | S14 | all_filtered_EW | random10 | HS300ETF_BH | S12_RP | S14/all | S14/random | S14/HS300 | S14/S12 |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| return | -3.92% | -3.65% | -0.51% | -29.44% | -12.25% | 1.0756 | 7.7506 | 0.1333 | 0.3204 |
| max_drawdown | 12.81% | 12.33% | 13.28% | 29.44% | 12.25% | 1.0384 | 0.9644 | 0.4350 | 1.0453 |
| trades | 69.0000 | 446.0000 | 93.0000 | 1.0000 | 37.0000 | 0.1547 | 0.7419 | 69.0000 | 1.8649 |
| profit_factor | 0.7092 | 0.7367 | 0.9711 | 0.0000 | 0.1199 | 0.9627 | 0.7303 | NA | 5.9146 |
| expectancy | -568.7682 | -81.8060 | -54.4463 | -294369.9478 | -3310.8152 | 6.9526 | 10.4464 | 0.0019 | 0.1718 |

### range
| metric | S14 | all_filtered_EW | random10 | HS300ETF_BH | S12_RP | S14/all | S14/random | S14/HS300 | S14/S12 |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| return | 9.41% | -3.41% | -4.81% | 10.81% | 12.14% | -2.7622 | -1.9576 | 0.8703 | 0.7747 |
| max_drawdown | 14.23% | 15.99% | 20.47% | 22.13% | 2.85% | 0.8899 | 0.6951 | 0.6429 | 4.9888 |
| trades | 106.0000 | 949.0000 | 152.0000 | 1.0000 | 59.0000 | 0.1117 | 0.6974 | 106.0000 | 1.7966 |
| profit_factor | 1.4378 | 0.8084 | 0.8401 | inf | 10.2339 | 1.7785 | 1.7114 | NA | 0.1405 |
| expectancy | 887.4323 | -35.8854 | -316.1301 | 108092.2974 | 2057.9550 | -24.7296 | -2.8072 | 0.0082 | 0.4312 |

### oos
| metric | S14 | all_filtered_EW | random10 | HS300ETF_BH | S12_RP | S14/all | S14/random | S14/HS300 | S14/S12 |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| return | 4.30% | 14.24% | 10.70% | 4.19% | 12.01% | 0.3019 | 0.4019 | 1.0262 | 0.3580 |
| max_drawdown | 12.37% | 9.57% | 10.46% | 20.81% | 4.52% | 1.2922 | 1.1825 | 0.5944 | 2.7376 |
| trades | 124.0000 | 769.0000 | 182.0000 | 1.0000 | 75.0000 | 0.1612 | 0.6813 | 124.0000 | 1.6533 |
| profit_factor | 1.1872 | 1.7733 | 1.3927 | inf | 14.0651 | 0.6695 | 0.8524 | NA | 0.0844 |
| expectancy | 346.7203 | 185.1676 | 587.8181 | 41894.3137 | 1601.1371 | 1.8725 | 0.5898 | 0.0083 | 0.2165 |

对照组定义：全等权持有所有通过同一过滤的候选、固定随机种子月度随机 10 只、510300 沪深300 ETF 买入持有、S12 跨大类风险平价同期表现。

## 反假设列表
1. 双低 alpha 在严格 PIT 下是否仍成立：用 S14 Top10 对比 all_filtered_equal_weight。若 S14 不能稳定优于全候选等权，Top10 排名本身没有提供增量 alpha。
2. amount 缺失偏差：本轮按预注册使用 `close*volume` 近似，过滤阈值 500 万。该近似可能误判深市/沪市成交量单位，方向上会让小流动性券被错选或被误杀；报告保留候选过滤后的平均数量与 fee_ratio。
3. 退市/强赎处理现实性：面板保留退市后 `in_universe=False`；策略排除最近 30 日内退市/赎回，以及最近 30 交易日 `convert_value>=130` 满 15 天的券。若仍持有到 delist_date，回测按最后可得价格强制退出，这比真实强赎兑付更简化。
4. 与已 FAIL 股票策略相比：本轮不因可转债传闻放宽 Gate1；若 A/B/C 任一不达标，则说明这个公开叙事在本项目成本、PIT、反幸存者口径下未被证实。

## flag/参数调查记录
- 参数全预注册不调：hold_n=10，price_max=130.0，premium_max=0.3，min_volume_yuan=5.0e6。
- 未碰 OOS：OOS 仅在规则、数据源、成本、过滤和对照组固定后用于最终 C 组裁决。
- amount 用 `close*volume` 近似；日线源实测无 `amount` 字段。
- 可转债印花税设 0；撮合不使用股票 10% 涨跌停，只有停牌/无当日开盘价拒单。

## Gate1 判定表
| group | metric | actual | threshold | result |
|---|---:|---:|---:|---|
| A/bull | expectancy_after_cost | -850.0154 | 0.0000 | FAIL |
| A/bull | profit_factor | 0.7693 | 1.3000 | FAIL |
| A/bull | max_drawdown | 0.2348 | 0.2000 | FAIL |
| A/bear | expectancy_after_cost | -568.7682 | 0.0000 | FAIL |
| A/bear | profit_factor | 0.7092 | 1.3000 | FAIL |
| A/bear | max_drawdown | 0.1281 | 0.2000 | PASS |
| A/range | expectancy_after_cost | 887.4323 | 0.0000 | PASS |
| A/range | profit_factor | 1.4378 | 1.3000 | PASS |
| A/range | max_drawdown | 0.1423 | 0.2000 | PASS |
| B/in_sample | trades | 237.0000 | 200.0000 | PASS |
| B/in_sample | expectancy_after_cost | 8.9530 | 0.0000 | PASS |
| B/in_sample | profit_factor | 1.0037 | 1.3000 | FAIL |
| C/oos | trades | 124.0000 | 60.0000 | PASS |
| C/oos | expectancy_after_cost | 346.7203 | 0.0000 | PASS |
| C/oos | profit_factor | 1.1872 | 1.3000 | FAIL |
| C/oos | max_drawdown | 0.1237 | 0.2000 | PASS |
| TOTAL | A+B+C | - | - | FAIL |

## 结论段
S14 是项目第 14 个策略。最终判定=FAIL。目前只有 S12 OOS PASS 但整体 FAIL；因此 S14 不是首次整体 Gate1 PASS，项目仍未出现整体 Gate1 PASS。

最终判定：FAIL。
