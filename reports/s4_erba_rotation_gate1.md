# S4 二八轮动 Gate1 Report

规则：月末收盘后比较 sh000300 与 sh000905 最近 lookback_days=20 个交易日累计收益；次月首个交易日开盘持有动量更强者。
信号用指数：sh000300/sh000905；成交用 ETF：510300/510500。trend_filter_ma=None，allow_cash=False。
本次使用低频显著性原则；月频策略不按高换手 trades>=200 / oos_min_trades=60 卡死。

## 数据覆盖
| kind | symbol | name | rows | earliest | latest | source |
|---|---|---|---:|---:|---:|---|
| index_signal | sh000300 | 沪深300 | 1489 | 2020-03-23 | 2026-05-15 | eastmoney_stock_zh_index_daily_em |
| etf_execution | 510300 | 沪深300ETF | 1489 | 2020-03-23 | 2026-05-15 | eastmoney_fund_etf_hist_em |
| index_signal | sh000905 | 中证500 | 1489 | 2020-03-23 | 2026-05-15 | eastmoney_stock_zh_index_daily_em |
| etf_execution | 510500 | 中证500ETF | 1489 | 2020-03-23 | 2026-05-15 | eastmoney_fund_etf_hist_em |

### regime 实际可得区间
| regime | configured_start | configured_end | effective_start | effective_end | adjusted |
|---|---:|---:|---:|---:|---|
| bull | 2020-07-01 | 2021-02-10 | 2020-07-01 | 2021-02-10 | NO |
| bear | 2022-01-01 | 2022-10-31 | 2022-01-04 | 2022-10-31 | YES |
| range | 2023-06-01 | 2024-09-30 | 2023-06-01 | 2024-09-30 | NO |
| oos | 2024-10-01 | 2026-05-15 | 2024-10-08 | 2026-05-15 | YES |

若 effective_start 晚于 configured_start，报告按真实可得 ETF/指数共同交易日起算；本次无需伪造补齐。

## S4 分段关键指标
| regime | start | end | return | max_drawdown | trades | expectancy_after_cost | profit_factor | win_rate | fee_ratio | filled_orders |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| bull | 2020-07-01 | 2021-02-10 | 34.50% | 8.70% | 2 | 172507.73 | inf | 100.00% | 0.08% | 4 |
| bear | 2022-01-04 | 2022-10-31 | -42.09% | 42.09% | 7 | -60126.56 | 0.1222 | 14.29% | 0.07% | 14 |
| range | 2023-06-01 | 2024-09-30 | -0.37% | 27.13% | 10 | -374.39 | 0.9830 | 20.00% | 0.08% | 20 |
| oos | 2024-10-08 | 2026-05-15 | 19.59% | 18.10% | 9 | 21769.54 | 2.6233 | 55.56% | 0.08% | 18 |

## in-sample vs OOS 差异
| span | avg/period_return | trades | expectancy | profit_factor | win_rate | worst/max_drawdown |
|---|---:|---:|---:|---:|---:|---:|
| in_sample(bull+bear+range) | -2.65% | 19 | -4190.23 | 0.8862 | 26.32% | 42.09% |
| oos | 19.59% | 9 | 21769.54 | 2.6233 | 55.56% | 18.10% |

## 对照组 ratio 表
### bull
| metric | S4 | HS300ETF_BH | CSI500ETF_BH | 50/50_monthly | random_monthly | S4/50_50 | note |
|---|---:|---:|---:|---:|---:|---:|---|
| return | 34.50% | 37.91% | 11.97% | 24.34% | 23.32% | 1.4173 |  |
| max_drawdown | 8.70% | 6.77% | 10.88% | 7.71% | 6.77% | 1.1282 |  |
| trades | 2.0000 | 1.0000 | 1.0000 | 9.0000 | 4.0000 | 0.2222 |  |
| fee_ratio | 0.08% | 0.08% | 0.08% | 0.08% | 0.08% | 0.9582 |  |

### bear
| metric | S4 | HS300ETF_BH | CSI500ETF_BH | 50/50_monthly | random_monthly | S4/50_50 | note |
|---|---:|---:|---:|---:|---:|---:|---|
| return | -42.09% | -29.44% | -30.20% | -29.51% | -29.57% | 1.4262 |  |
| max_drawdown | 42.09% | 29.44% | 31.99% | 29.51% | 30.67% | 1.4262 |  |
| trades | 7.0000 | 1.0000 | 1.0000 | 11.0000 | 7.0000 | 0.6364 |  |
| fee_ratio | 0.07% | 0.07% | 0.07% | 0.07% | 0.07% | 1.0455 |  |

### range
| metric | S4 | HS300ETF_BH | CSI500ETF_BH | 50/50_monthly | random_monthly | S4/50_50 | note |
|---|---:|---:|---:|---:|---:|---:|---|
| return | -0.37% | 10.81% | -2.17% | 4.22% | 15.02% | -0.0888 |  |
| max_drawdown | 27.13% | 22.13% | 27.54% | 24.48% | 23.87% | 1.1081 |  |
| trades | 10.0000 | 1.0000 | 1.0000 | 17.0000 | 9.0000 | 0.5882 |  |
| fee_ratio | 0.08% | 0.08% | 0.08% | 0.08% | 0.08% | 0.9257 |  |

### oos
| metric | S4 | HS300ETF_BH | CSI500ETF_BH | 50/50_monthly | random_monthly | S4/50_50 | note |
|---|---:|---:|---:|---:|---:|---:|---|
| return | 19.59% | 4.19% | 29.85% | 16.22% | 26.24% | 1.2082 |  |
| max_drawdown | 18.10% | 20.81% | 20.11% | 20.69% | 23.37% | 0.8747 |  |
| trades | 9.0000 | 1.0000 | 1.0000 | 20.0000 | 8.0000 | 0.4500 |  |
| fee_ratio | 0.08% | 0.08% | 0.08% | 0.08% | 0.08% | 0.9135 |  |

重点对照：50/50 月度再平衡是静态等权持有的可执行近似；若 S4 不能稳定优于该组，不能宣称轮动有独立 alpha。

## 反假设列表
- lookback_days=20 是否过拟合：以下敏感性只跑 in-sample(bull/bear/range)，未触碰 OOS，不用于选择参数。
| lookback_days | in_sample_avg_return | in_sample_worst_DD | trades | expectancy | profit_factor | win_rate |
|---:|---:|---:|---:|---:|---:|---:|
| 10 | -5.45% | 46.59% | 18 | -9089.45 | 0.7669 | 38.89% |
| 20 | -2.65% | 42.09% | 19 | -4190.23 | 0.8862 | 26.32% |
| 40 | -1.71% | 37.49% | 11 | -4668.08 | 0.9273 | 45.45% |
| 60 | -1.58% | 40.64% | 11 | -4306.90 | 0.9303 | 45.45% |
- 指数信号到 ETF 成交偏差：指数序列更长、更干净且无盘口折溢价；实际 ETF 有跟踪误差、折溢价、分红除权和盘口流动性，方向偏向高估信号可迁移性。PnL 已用 ETF 价格成交，但选谁仍来自更理想的指数。
- 月频换仓成本拖累：月频决策数=54，约12.3次/年；filled_orders=56，约12.8笔/年。总成本=39955.06，成交额成本率=0.08%，成本/毛盈利=0.0427。
- ETF/指数 limit 字段为 NaN 时 constraints.py 不触发一字涨跌停拒单；这会高估极端开盘日的可成交性，方向为乐观偏差。

## flag/参数调查记录
- 未调 lookback，固定使用 strategy_addon.yaml 的 lookback_days=20。
- 未碰 OOS 调参；OOS 只在固定规则跑完后用于 C 组最终裁决。
- 未加入 trend filter，未允许空仓，未修改成本、滑点、regime 或 Gate1 阈值。

## Gate1 判定表
| group | metric | actual | threshold | result |
|---|---:|---:|---:|---|
| A/bull | expectancy_after_cost | 172507.7287 | 0.0000 | PASS |
| A/bull | profit_factor | inf | 1.3000 | PASS |
| A/bull | max_drawdown | 0.0870 | 0.2000 | PASS |
| A/bear | expectancy_after_cost | -60126.5622 | 0.0000 | FAIL |
| A/bear | profit_factor | 0.1222 | 1.3000 | FAIL |
| A/bear | max_drawdown | 0.4209 | 0.2000 | FAIL |
| A/range | expectancy_after_cost | -374.3858 | 0.0000 | FAIL |
| A/range | profit_factor | 0.9830 | 1.3000 | FAIL |
| A/range | max_drawdown | 0.2713 | 0.2000 | FAIL |
| B/low_freq/bull | expectancy_after_cost | 172507.7287 | 0.0000 | PASS |
| B/low_freq/bull | profit_factor | inf | 1.3000 | PASS |
| B/low_freq/bear | expectancy_after_cost | -60126.5622 | 0.0000 | FAIL |
| B/low_freq/bear | profit_factor | 0.1222 | 1.3000 | FAIL |
| B/low_freq/range | expectancy_after_cost | -374.3858 | 0.0000 | FAIL |
| B/low_freq/range | profit_factor | 0.9830 | 1.3000 | FAIL |
| C/oos | expectancy_after_cost | 21769.5387 | 0.0000 | PASS |
| C/oos | profit_factor | 2.6233 | 1.3000 | PASS |
| C/oos | max_drawdown | 0.1810 | 0.2000 | PASS |
| C/oos | trades | 9.0000 | 低频不适用9<60 | N/A |
| TOTAL | A+B+C(低频显著性) | - | - | FAIL |

最终判定：FAIL，按低频显著性原则。
