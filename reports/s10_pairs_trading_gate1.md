# S10 Pairs Trading Gate1 Report

规则：每个 formation date 用过去 formation_window_days=252 个交易日做 Engle-Granger 协整检验；每次从当月 PIT 流动性 universe 随机抽样 1000 对，p<0.05 后按 |t-stat| 排序取 num_pairs=5；交易窗口 trading_window_days=126，每日用 formation mean/std 计算 z-score，不滚动重估。
执行：allow_short=False，因此使用 long-only 近似。z>2.0 只买 B，z<-2.0 只买 A；|z|<=0.5 平仓，|z|>=3.5 记为 stop_z 协整失效止损。

## 数据与 universe
- panel cache=data/cache/s2_panel_v2pit_2019-10-01_2026-05-15.parquet，rows=7528942，symbols=5420，delisted_symbols=216，span=2019-10-08..2026-05-15。
- universe monthly dates=78，PIT rolling20 amount Top300 size min/median/max=300/300.0/300。
- formation_count=11，selected_pairs=55，unique_pairs=55，selected_unique_symbols=97。
- Engle-Granger p 值来自固定种子 Monte Carlo 零分布，sims=5000，5% critical t=-3.3477。
- scope_note=reused S2 PIT panel cache only; csi300_constituents implemented as monthly PIT rolling-20d amount Top300 from that panel, no fresh constituent/data pull。

### formation 选择审计
| formation_date | trading_end | universe | valid_price_symbols | sampled_pairs | coint_p_lt_0_05 | selected | min_p | max_abs_t |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| 2020-10-30 | 2021-05-10 | 300 | 292 | 1000 | 22 | 5 | 0.0082 | 4.0369 |
| 2021-05-31 | 2021-12-03 | 300 | 292 | 1000 | 36 | 5 | 0.0010 | 4.8612 |
| 2021-12-31 | 2022-07-13 | 300 | 298 | 1000 | 59 | 5 | 0.0038 | 4.3567 |
| 2022-07-29 | 2023-02-08 | 300 | 296 | 1000 | 74 | 5 | 0.0018 | 4.6320 |
| 2023-02-28 | 2023-08-31 | 300 | 297 | 1000 | 48 | 5 | 0.0002 | 6.7868 |
| 2023-08-31 | 2024-03-13 | 300 | 293 | 1000 | 61 | 5 | 0.0008 | 5.0030 |
| 2024-03-29 | 2024-10-10 | 300 | 300 | 1000 | 53 | 5 | 0.0002 | 5.4452 |
| 2024-10-31 | 2025-05-12 | 300 | 300 | 1000 | 35 | 5 | 0.0002 | 5.9186 |
| 2025-05-30 | 2025-12-03 | 300 | 300 | 1000 | 33 | 5 | 0.0018 | 4.6301 |
| 2025-12-31 | 2026-05-15 | 300 | 300 | 1000 | 42 | 5 | 0.0020 | 4.5877 |
| 2026-05-15 | 2026-05-15 | 300 | 298 | 1000 | 22 | 5 | 0.0020 | 4.5812 |

## S10 分段关键指标
| regime | return | max_drawdown | trades | expectancy_after_cost | profit_factor | win_rate | fee_ratio | forced_hold | filled_orders | avg_long_exposure |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| bull | -8.60% | 8.90% | 27 | -3183.64 | 0.0033 | 11.11% | 0.08% | 0 | 58 | 8.26% |
| bear | -4.88% | 12.54% | 111 | -368.38 | 0.8049 | 44.14% | 0.08% | 0 | 224 | 34.42% |
| range | 5.11% | 15.59% | 160 | 319.29 | 1.3023 | 46.88% | 0.08% | 0 | 351 | 34.90% |
| oos | 2.03% | 8.96% | 160 | 68.05 | 1.0499 | 59.38% | 0.08% | 0 | 349 | 21.94% |

## long-only 市场中性折扣量化
| regime | avg_long_exposure | max_long_exposure | daily_beta_to_HS300ETF | daily_corr_to_HS300ETF |
|---|---:|---:|---:|---:|
| bull | 8.26% | 40.64% | 0.0066 | 0.0293 |
| bear | 34.42% | 79.24% | 0.4041 | 0.6404 |
| range | 34.90% | 99.49% | 0.5566 | 0.6399 |
| oos | 21.94% | 80.27% | 0.1663 | 0.3193 |

## in-sample vs OOS 差异
| span | avg/period_return | trades | expectancy | profit_factor | win_rate | max_drawdown_worst |
|---|---:|---:|---:|---:|---:|---:|
| in_sample(bull+bear+range) | -2.79% | 298 | -254.23 | 0.8370 | 42.62% | 15.59% |
| oos | 2.03% | 160 | 68.05 | 1.0499 | 59.38% | 8.96% |

## 对照组 ratio 表
| regime | metric | S10 | HS300ETF_BH | random10_monthly | universe_equal | S10/HS300 | S10/random10 | S10/universe | note |
|---|---|---:|---:|---:|---:|---:|---:|---:|---|
| bull | return | -8.60% | 37.91% | -0.51% | -2.09% | -0.2267 | 17.0018 | 4.1034 | ratio>2x，需调查 |
| bull | max_drawdown | 8.90% | 6.77% | 19.35% | 12.78% | 1.3140 | 0.4599 | 0.6962 |  |
| bull | trades | 27.0000 | 1.0000 | 77.0000 | 775.0000 | 27.0000 | 0.3506 | 0.0348 | ratio>2x，需调查 |
| bull | fee_ratio | 0.08% | 0.08% | 0.08% | 0.25% | 0.9815 | 1.0846 | 0.3333 |  |
| bear | return | -4.88% | -29.44% | -50.31% | -19.27% | 0.1657 | 0.0970 | 0.2531 |  |
| bear | max_drawdown | 12.54% | 28.78% | 50.38% | 19.27% | 0.4355 | 0.2488 | 0.6504 |  |
| bear | trades | 111.0000 | 1.0000 | 97.0000 | 854.0000 | 111.0000 | 1.1443 | 0.1300 | ratio>2x，需调查 |
| bear | fee_ratio | 0.08% | 0.07% | 0.07% | 0.28% | 1.2044 | 1.0951 | 0.2934 |  |
| range | return | 5.11% | 10.81% | -23.83% | -13.02% | 0.4726 | -0.2144 | -0.3922 |  |
| range | max_drawdown | 15.59% | 22.13% | 43.91% | 26.94% | 0.7046 | 0.3551 | 0.5787 |  |
| range | trades | 160.0000 | 1.0000 | 158.0000 | 1445.0000 | 160.0000 | 1.0127 | 0.1107 | ratio>2x，需调查 |
| range | fee_ratio | 0.08% | 0.08% | 0.08% | 0.28% | 1.0804 | 1.1240 | 0.3081 |  |
| oos | return | 2.03% | 4.19% | 17.41% | 2.10% | 0.4841 | 0.1165 | 0.9657 |  |
| oos | max_drawdown | 8.96% | 16.24% | 23.37% | 13.04% | 0.5517 | 0.3835 | 0.6873 |  |
| oos | trades | 160.0000 | 1.0000 | 181.0000 | 1640.0000 | 160.0000 | 0.8840 | 0.0976 | ratio>2x，需调查 |
| oos | fee_ratio | 0.08% | 0.08% | 0.08% | 0.26% | 1.0567 | 1.0671 | 0.3189 |  |

## 反假设列表
- long-only 近似削弱市场中性：纯 pairs 应接近 net exposure=0；本轮 long-only 平均多头暴露=24.88%，各段 beta/corr 见上表。这个数字就是“市场中性”成立度打折，不能按美股多空配对宣传。
- 协整失效率：stop_z events=75，closed trades=458，stop_z 触发占总交易数=16.38%，stop_z/entry=70.75%。stop_z 是价差偏离到 3.5σ 后承认关系失效，不是无风险套利。
- 金融营销话术风险：即使名字叫市场中性，实际最大回撤最高为 15.59%，OOS return=2.03%/PF=1.0499。若 C 组不过关，不能把 in-sample 片段包装成稳健 alpha。
- A股政策冲击会破坏协整：2022 年中概股/平台经济/地产链和疫情政策预期反复冲击行业相关性；同一行业内股票可能因监管、融资、指数调仓或流动性偏好突然分化，历史 residual mean/std 不再代表未来。
- 多重检验假阳性：每次随机抽样 1000 对，在全为零假设时理论上也会有约 50 对 p<0.05；因此报告只把它当可执行反诈检验，不把“筛出协整对”本身当 alpha 证据。

## flag/参数调查记录
- long-only 近似：allow_short=false，short leg 未真实卖空，只买相对低估一腿；这放宽了纯市场中性假设。
- 未调参：entry_z/exit_z/stop_z/num_pairs/pair_capital_pct/formation_window/trading_window 全部来自 `configs/strategy_addon.yaml`。
- 未碰OOS：未用 OOS 修改参数、阈值、随机种子或 pair sample size；OOS 只按同一 walk-forward 规则生成 ≤D 信号并作 C 组最终裁决。
- random pair sampling 1000 对(种子固定)：每个 formation date 用 RANDOM_SEED=20260519+date.toordinal() 抽样，未跑 N² 全对。
- 成本/撮合：股票腿全部走 constraints.py，含 5 元佣金地板、印花税、过户费、滑点、T+1、涨跌停/停牌拒单。

## A/B/C 判定
| gate | result | note |
|---|---|---|
| A | FAIL | bull/bear/range each require expectancy>0, PF>=1.3, DD<=20% |
| B | FAIL | merged trades>=200 plus expectancy/PF thresholds |
| C | FAIL | OOS expectancy/PF/DD/trades>=60 |
| TOTAL | FAIL | A+B+C simultaneous |

## Gate1 判定表
| group | metric | actual | threshold | result |
|---|---:|---:|---:|---|
| A/bull | expectancy_after_cost | -3183.6419 | 0.0000 | FAIL |
| A/bull | profit_factor | 0.0033 | 1.3000 | FAIL |
| A/bull | max_drawdown | 0.0890 | 0.2000 | PASS |
| A/bear | expectancy_after_cost | -368.3759 | 0.0000 | FAIL |
| A/bear | profit_factor | 0.8049 | 1.3000 | FAIL |
| A/bear | max_drawdown | 0.1254 | 0.2000 | PASS |
| A/range | expectancy_after_cost | 319.2885 | 0.0000 | PASS |
| A/range | profit_factor | 1.3023 | 1.3000 | PASS |
| A/range | max_drawdown | 0.1559 | 0.2000 | PASS |
| B/merged | trades | 458.0000 | 200.0000 | PASS |
| B/merged | expectancy_after_cost | -141.6452 | 0.0000 | FAIL |
| B/merged | profit_factor | 0.9050 | 1.3000 | FAIL |
| C/oos | expectancy_after_cost | 68.0525 | 0.0000 | PASS |
| C/oos | profit_factor | 1.0499 | 1.3000 | FAIL |
| C/oos | max_drawdown | 0.0896 | 0.2000 | PASS |
| C/oos | trades | 160.0000 | 60.0000 | PASS |
| TOTAL | A+B+C | - | - | FAIL |

## 与既有 FAIL 策略对比
| strategy | name | Gate1 final |
|---|---|---|
| S1 | tail | FAIL |
| S2 | multi_factor | FAIL |
| S3 | momentum | FAIL |
| S4 | erba_rotation | FAIL |
| S5 | small_cap | FAIL |
| S6 | dual_ma | FAIL |
| S7 | limit_up_followup | FAIL |
| S8 | rsi_reversal | FAIL |
| S9 | risk_parity | FAIL |
| S10 | pairs_trading | FAIL |
| note | S10 是第二个 OOS PASS 候选? | NO |

最终判定：FAIL
