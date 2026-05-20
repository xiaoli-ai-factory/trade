# S2 Multi-Factor Gate1 Report

旧版作废说明：上一版 S2 使用当前在市/当前成交额预筛，属于幸存者+未来属性污染，已按 Claude review 裁决作废。本报告覆盖旧报告；下表只保留旧污染数字用于审计对比。
| regime | old_invalid_return | new_PIT_return | old_invalid_DD | new_PIT_DD | old_invalid_PF | new_PIT_PF |
|---|---:|---:|---:|---:|---:|---:|
| bull | 201.40% | 195.81% | 9.18% | 7.01% | 3.3776 | 3.3624 |
| bear | 106.03% | 104.45% | 25.40% | 18.99% | 2.0548 | 1.9173 |
| range | 293.47% | 398.47% | 24.23% | 17.98% | 2.2368 | 2.4266 |
| oos | 170.26% | 36.74% | 25.90% | 24.17% | 1.6454 | 1.1696 |

参数：严格使用 `configs/strategy.yaml` 的 S2 配置：weekly rebalance、hold_n=15、factors=mom_20/mom_60/vol_20/turnover_20/amount_20、model=lightgbm。
信号在调仓日 D 收盘后只用 ≤D 的日线因子，D+1 开盘按 constraints.py 撮合；OOS 未参与训练、早停、选特征或调参。

## 数据与 scope
- 取数区间：2019-10-01..2026-05-15，warmup=120日。
- requested TopN=300；本轮执行 TopN=200，流动性排名只用每个调仓日 ≤D 的 amount_20。
- registry_source=data._universe active+delisted registry; no spot/current-liquidity prefilter。
- registry: active=5205, delisted=216, requested_symbols=5421。
- fetched: ok_symbols=5420, failed_symbols=1, panel_symbols=5420, panel_rows=7528942, fetch_seconds=1330.7。
- list_date: inferred_for_active_or_missing_meta_symbols=5204; inferred date is first historical bar in the panel, never a current liquidity attribute.
- PIT assertion: eligible feature rows checked=6744777; assertion=list_date<=D and (delist_date is null or delist_date>D)。
- PIT liquidity pool size by rebalance date: min=200, median=200.0。
- delisted evidence: symbols_with_rows=216, eligible_feature_rows=187571, sample=000005@2019-10-08, 000018@2019-10-08, 000023@2019-10-08, 000038@2019-10-08, 000040@2019-10-08, 000046@2019-10-08, 000150@2019-10-08, 000413@2019-10-08。
- delisted data caveat: many delisted rows come from Tencent fallback; amount is approximated as volume*close and turnover is unavailable/zero-filled, so delisted factor quality is weaker than active Sina rows.
- scope_note: strict_pit_registry_all_symbols_with_history; liquidity ranking uses only rolling amount_20 at each as_of_date。
- failures_sample: 001365:empty。

## 训练/OOS 隔离证据
- LightGBM version=4.6.0，固定默认参数训练一次，无 early stopping，无 shuffle/K-fold。
- train_rows=28200, train_dates=141, train_date_range=2020-07-06..2024-09-23。
- oos_min_date=2024-10-01；assert train_max_date < oos_min_date 已通过。
- prediction_rows=60000。
| feature | importance |
|---|---:|
| amount_20 | 648 |
| vol_20 | 624 |
| mom_60 | 613 |
| mom_20 | 564 |
| turnover_20 | 551 |

## S2 分段关键指标
| regime | return | max_drawdown | trades | expectancy_after_cost | profit_factor | win_rate | fee_ratio |
|---|---:|---:|---:|---:|---:|---:|---:|
| bull | 195.81% | 7.01% | 380 | 5152.99 | 3.3624 | 68.42% | 0.08% |
| bear | 104.45% | 18.99% | 439 | 2379.27 | 1.9173 | 56.72% | 0.08% |
| range | 398.47% | 17.98% | 790 | 5043.97 | 2.4266 | 57.22% | 0.08% |
| oos | 36.74% | 24.17% | 950 | 386.72 | 1.1696 | 50.63% | 0.08% |

## in-sample vs OOS 过拟合体检
| span | avg/period_return | trades | expectancy | profit_factor | win_rate | max_drawdown_worst |
|---|---:|---:|---:|---:|---:|---:|
| in_sample(bull+bear+range) | 232.91% | 1609 | 4342.68 | 2.4677 | 59.73% | 18.99% |
| oos | 36.74% | 950 | 386.72 | 1.1696 | 50.63% | 24.17% |

## 对照组 ratio 表
| regime | metric | S2 | equal_weight_pool | random_15 | S2/EW | S2/random | note |
|---|---|---:|---:|---:|---:|---:|---|
| bull | return | 195.81% | -8.12% | -14.60% | -24.1282 | -13.4124 | ratio>2x需调查 |
| bull | max_drawdown | 7.01% | 14.71% | 27.86% | 0.4766 | 0.2516 |  |
| bull | trades | 380.0000 | 875.0000 | 456.0000 | 0.4343 | 0.8333 |  |
| bull | fee_ratio | 0.08% | 0.21% | 0.08% | 0.3705 | 1.0208 |  |
| bear | return | 104.45% | -27.77% | -54.61% | -3.7611 | -1.9126 | ratio>2x需调查 |
| bear | max_drawdown | 18.99% | 27.77% | 54.61% | 0.6840 | 0.3478 |  |
| bear | trades | 439.0000 | 1083.0000 | 573.0000 | 0.4054 | 0.7661 |  |
| bear | fee_ratio | 0.08% | 0.23% | 0.08% | 0.3361 | 1.0205 |  |
| range | return | 398.47% | -22.07% | -46.07% | -18.0554 | -8.6499 | ratio>2x需调查 |
| range | max_drawdown | 17.98% | 36.21% | 60.86% | 0.4966 | 0.2954 |  |
| range | trades | 790.0000 | 1834.0000 | 948.0000 | 0.4308 | 0.8333 |  |
| range | fee_ratio | 0.08% | 0.23% | 0.08% | 0.3308 | 1.0154 |  |
| oos | return | 36.74% | 2.33% | -18.13% | 15.7523 | -2.0264 | ratio>2x需调查 |
| oos | max_drawdown | 24.17% | 19.61% | 40.70% | 1.2327 | 0.5938 |  |
| oos | trades | 950.0000 | 2290.0000 | 1159.0000 | 0.4148 | 0.8197 |  |
| oos | fee_ratio | 0.08% | 0.21% | 0.08% | 0.3682 | 1.0085 |  |

## 反假设列表
- 因子收益只是小盘/低流动性 beta：候选池每个调仓日先按过去20日成交额做 PIT TopN；另跑 `exclude_small_mv`（剔除当日候选中 float_mv 最小20%）作反证。
  OOS 原 S2 return=36.74%/DD=24.17%/PF=1.1696；exclude_small_mv OOS return=21.43%/DD=19.18%/PF=1.1189。
- ML 泄漏/过拟合：label 为调仓日之后到下个周频调仓的 forward return；训练只用 bull+bear+range 且 label_end < OOS 起点；OOS 不参与训练/早停/调参。上方 in-sample vs OOS 表用于观察性能塌缩。
- 周频15只调仓成本拖累：报告展示 fee_ratio；所有成本含 5 元佣金地板、印花税、过户费和 0.2% 滑点。

## flag/参数调查记录
- 未修改 `configs/strategy.yaml`，未调 hold_n/factors/model/LightGBM 超参来改善 OOS。
- 未触碰 OOS 训练/调参；OOS 只用于最终 C 组裁决。
- 已移除当前流动性预筛，改纯PIT：候选 symbol 注册表来自 active+delisted 清单，调仓日资格由历史 list_date/delist_date/amount_20 决定。
- 未调用 `_load_spot_liquidity()` 或任何当前快照成交额接口；S2 代码中该函数已删除。
- scope 限制：active 清单仍来自 AkShare 当前 symbol 注册表作为代码目录，但不按当前在市状态或当前成交额做入池排序；新上市股票在其首个历史 bar/list_date 之前被 PIT 断言排除。

## Gate1 判定表
| group | metric | actual | threshold | result |
|---|---:|---:|---:|---|
| A/bull | expectancy_after_cost | 5152.9853 | 0.0000 | PASS |
| A/bull | profit_factor | 3.3624 | 1.3000 | PASS |
| A/bull | max_drawdown | 0.0701 | 0.2000 | PASS |
| A/bear | expectancy_after_cost | 2379.2663 | 0.0000 | PASS |
| A/bear | profit_factor | 1.9173 | 1.3000 | PASS |
| A/bear | max_drawdown | 0.1899 | 0.2000 | PASS |
| A/range | expectancy_after_cost | 5043.9697 | 0.0000 | PASS |
| A/range | profit_factor | 2.4266 | 1.3000 | PASS |
| A/range | max_drawdown | 0.1798 | 0.2000 | PASS |
| B/merged | trades | 2559.0000 | 200.0000 | PASS |
| B/merged | expectancy_after_cost | 2874.0715 | 0.0000 | PASS |
| B/merged | profit_factor | 2.0617 | 1.3000 | PASS |
| C/oos | expectancy_after_cost | 386.7166 | 0.0000 | PASS |
| C/oos | profit_factor | 1.1696 | 1.3000 | FAIL |
| C/oos | max_drawdown | 0.2417 | 0.2000 | FAIL |
| C/oos | trades | 950.0000 | 60.0000 | PASS |
| TOTAL | A+B+C | - | - | FAIL |

最终判定：FAIL
