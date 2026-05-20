# S7 Limit-Up Follow-Up Gate1 Report

规则：每日 D 收盘后在 PIT 全市场 panel 中筛 D 日 close==limit_up_price、amount>=500000000、非 ST、上市>=60 日且未退市股票；按成交额降序取 max_positions=3，D+1 开盘买入，D+2 开盘无条件卖出。
撮合完全复用 constraints.py：一字涨停买单拒绝，一字跌停/停牌卖单拒绝并记录 forced_hold；成本含佣金地板、印花税、过户费、滑点。

## 数据与 universe
- panel=7528942 rows / 5420 symbols；active=5204, delisted=216。
- panel span=2019-10-08..2026-05-15；calendar_dates=1601。
- base universe rows=753676, dates=1542；limit_up rows=30816, dates=1542。
- selected rows=4604, unique_symbols=1338, selected_delisted_symbols=22, selected_ST_rows=0。
- selected D-day amount min/median/max=501008236/2546204824/33078472090。
- PIT assertion: candidates satisfy list_date<=D, delist_date>D or null, non-ST, age_days>=exclude_new_days, D-day amount threshold, and close==limit_up_price.

## S7 分段关键指标
| regime | return | max_drawdown | trades | expectancy_after_cost | profit_factor | win_rate | fee_ratio | buy_limit_up_reject | forced_hold/sell_orders |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| bull | -72.49% | 77.97% | 424 | -1715.96 | 0.7010 | 42.45% | 0.08% | 0.70% | 0.47% |
| bear | -93.06% | 93.55% | 506 | -1845.40 | 0.6423 | 39.72% | 0.08% | 2.77% | 9.82% |
| range | -97.78% | 98.09% | 860 | -1137.39 | 0.6223 | 36.63% | 0.08% | 2.14% | 3.80% |
| oos | -96.25% | 97.00% | 1004 | -957.38 | 0.6727 | 41.43% | 0.08% | 1.64% | 3.65% |

## 一字涨停/forced_hold 真实成交约束
| regime | buy_orders | buy_filled | buy_rejected_limit_up | limit_up_reject_ratio | buy_skipped_cash | sell_orders | sell_rejected | forced_hold | forced/sell_orders | forced/sell_rejections |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| bull | 430 | 426 | 3 | 0.70% | 0 | 426 | 2 | 2 | 0.47% | 100.00% |
| bear | 541 | 509 | 15 | 2.77% | 16 | 560 | 55 | 55 | 9.82% | 100.00% |
| range | 889 | 862 | 19 | 2.14% | 7 | 894 | 34 | 34 | 3.80% | 100.00% |
| oos | 1038 | 1006 | 17 | 1.64% | 15 | 1042 | 38 | 38 | 3.65% | 100.00% |
| TOTAL | 2898 | 2803 | 54 | 1.86% | 38 | 2922 | 129 | 129 | 4.41% | 100.00% |

## in-sample vs OOS 差异
| span | avg/period_return | trades | expectancy | profit_factor | win_rate | max_drawdown_worst |
|---|---:|---:|---:|---:|---:|---:|
| in_sample(bull+bear+range) | -87.78% | 1790 | -1474.58 | 0.6542 | 38.88% | 98.09% |
| oos | -96.25% | 1004 | -957.38 | 0.6727 | 41.43% | 97.00% |

## 对照组 ratio 表
| regime | metric | S7 | random3_same_universe | HS300ETF_BH | S7/random | S7/HS300 | note |
|---|---|---:|---:|---:|---:|---:|---|
| bull | return | -72.49% | -77.73% | 37.91% | 0.9326 | -1.9121 |  |
| bull | max_drawdown | 77.97% | 80.66% | 6.77% | 0.9666 | 11.5111 | ratio>2x，需调查 |
| bull | trades | 424.0000 | 453.0000 | 1.0000 | 0.9360 | 424.0000 | ratio>2x，需调查 |
| bull | fee_ratio | 0.08% | 0.08% | 0.08% | 0.9995 | 0.9013 |  |
| bear | return | -93.06% | -72.33% | -29.44% | 1.2866 | 3.1614 | ratio>2x，需调查 |
| bear | max_drawdown | 93.55% | 73.20% | 28.78% | 1.2779 | 3.2503 | ratio>2x，需调查 |
| bear | trades | 506.0000 | 584.0000 | 1.0000 | 0.8664 | 506.0000 | ratio>2x，需调查 |
| bear | fee_ratio | 0.08% | 0.08% | 0.07% | 0.9990 | 1.1235 |  |
| range | return | -97.78% | -86.09% | 10.81% | 1.1358 | -9.0459 | ratio>2x，需调查 |
| range | max_drawdown | 98.09% | 89.09% | 22.13% | 1.1011 | 4.4332 | ratio>2x，需调查 |
| range | trades | 860.0000 | 955.0000 | 1.0000 | 0.9005 | 860.0000 | ratio>2x，需调查 |
| range | fee_ratio | 0.08% | 0.08% | 0.08% | 1.0075 | 0.9723 |  |
| oos | return | -96.25% | -88.85% | 4.19% | 1.0833 | -22.9739 | ratio>2x，需调查 |
| oos | max_drawdown | 97.00% | 90.26% | 16.24% | 1.0747 | 5.9713 | ratio>2x，需调查 |
| oos | trades | 1004.0000 | 1144.0000 | 1.0000 | 0.8776 | 1004.0000 | ratio>2x，需调查 |
| oos | fee_ratio | 0.08% | 0.08% | 0.08% | 1.0054 | 0.9895 |  |

## 反假设列表
- 一字涨停买不进：上表 buy_rejected_limit_up 明确把未成交买单计入分母；如果只统计成功买入后的收益，会系统性高估打板成功率。
- 退市股亏损贡献：
| regime | delisted_trades | delisted_pnl | delisted_losing_trades | all_pnl | delisted_loss_share_of_abs_losses |
|---|---:|---:|---:|---:|---:|
| bull | 7 | -38790.11 | 5 | -727568.80 | 1.94% |
| bear | 2 | 43692.02 | 0 | -933770.38 | 0.00% |
| range | 6 | -35159.54 | 5 | -978154.35 | 1.39% |
| oos | 3 | -18536.62 | 3 | -961204.96 | 0.63% |
- 成交额 5亿过滤是否过松：以下敏感性仅使用 bull/bear/range in-sample，未触碰 OOS，不用于改参数。
| prefilter_min_amount | in_sample_trades | avg_return | worst_DD | expectancy | PF | win_rate | limit_up_reject_ratio | forced_hold/sell_orders |
|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| 100000000 | 1805 | -87.90% | 98.45% | -1464.24 | 0.6537 | 38.89% | 2.02% | 4.85% |
| 500000000 | 1790 | -87.78% | 98.09% | -1474.58 | 0.6542 | 38.88% | 1.99% | 4.84% |
| 1000000000 | 1572 | -86.73% | 97.36% | -1659.78 | 0.6467 | 38.61% | 1.72% | 5.13% |
- 牛市 beta/追涨共振：对照组加入同 universe 随机 3 只和 510300 买入持有；若 S7 不优于随机或只在 bull 好，不能声称有独立 alpha。

## 与 S1 杨永兴法对比
同属追涨打板流派：S1 是 14:50 尾盘追强，S7 是涨停后次日开盘接力。S1 OOS return=-7.52%, trades=19, expectancy=-3957.63, PF=0.2026, win_rate=31.58%；S7 OOS return=-96.25%, trades=1004, expectancy=-957.38, PF=0.6727, win_rate=41.43%。S7 in-sample merged trades=1790, expectancy=-1474.58, PF=0.6542。

## flag/参数调查记录
- 未调参、未碰 OOS。
- 默认 prefilter_min_amount 固定为 strategy_addon.yaml 的 5e8；敏感性只展示 in-sample。
- 未修改成本、滑点、regime 或 Gate1 阈值。
- 未静默忽略一字涨停买不进或跌停/停牌卖不掉；拒单与 forced_hold 均在报告披露。

## Gate1 判定表
| group | metric | actual | threshold | result |
|---|---:|---:|---:|---|
| A/bull | expectancy_after_cost | -1715.9641 | 0.0000 | FAIL |
| A/bull | profit_factor | 0.7010 | 1.3000 | FAIL |
| A/bull | max_drawdown | 0.7797 | 0.2000 | FAIL |
| A/bear | expectancy_after_cost | -1845.3960 | 0.0000 | FAIL |
| A/bear | profit_factor | 0.6423 | 1.3000 | FAIL |
| A/bear | max_drawdown | 0.9355 | 0.2000 | FAIL |
| A/range | expectancy_after_cost | -1137.3888 | 0.0000 | FAIL |
| A/range | profit_factor | 0.6223 | 1.3000 | FAIL |
| A/range | max_drawdown | 0.9809 | 0.2000 | FAIL |
| B/merged | trades | 2794.0000 | 200.0000 | PASS |
| B/merged | expectancy_after_cost | -1288.7253 | 0.0000 | FAIL |
| B/merged | profit_factor | 0.6594 | 1.3000 | FAIL |
| C/oos | expectancy_after_cost | -957.3755 | 0.0000 | FAIL |
| C/oos | profit_factor | 0.6727 | 1.3000 | FAIL |
| C/oos | max_drawdown | 0.9700 | 0.2000 | FAIL |
| C/oos | trades | 1004.0000 | 60.0000 | PASS |
| TOTAL | A+B+C | - | - | FAIL |

最终判定：FAIL
