# S1 Tail 5min PIT Gate1 Report

参数：严格使用 `configs/strategy.yaml` 的 `s1_tail`，未改 pct/量比/换手/VWAP/14:30后新高/max_positions。
信号在 D 日 14:50 生成，只读取当日 BaoStock 5min `time<=14:50` bar 和 D-1 及更早日线字段；OOS 未用于调参。

## 数据与 PIT 证据
- BaoStock version=00.9.10。
- daily_panel_source=<PROJECT_ROOT>/data/cache/s2_panel_v2pit_2019-10-01_2026-05-15.parquet, rows=7056968, symbols=5413, active_symbols=5204, delisted_symbols=209。
- Gate1 signal span=2020-07-01..2026-05-15；日线预筛只用 D-1 及以前派生字段。
- prefilter rows=319181, dates=1423, symbols=4803, per_day min/median/max=78/211.0/1599。
- delisted evidence: prefilter_rows=13666, symbols=155, sample=000005@2020-07-13, 000023@2020-07-13, 000038@2020-07-13, 000040@2020-07-07, 000046@2021-01-20, 000150@2020-07-07, 000413@2020-07-07, 000502@2020-09-02, 000540@2020-07-03, 000584@2020-07-30。
- BaoStock snapshot cache rows=319181, ok=318020, failed=1161, failures_sample=603488@2020-07-03:no_bars_before_cutoff, 601599@2020-07-06:no_bars_before_cutoff, 603488@2020-07-06:no_bars_before_cutoff, 601599@2020-07-07:no_bars_before_cutoff, 603488@2020-07-07:no_bars_before_cutoff, 603922@2020-07-07:no_bars_before_cutoff, 601599@2020-07-08:no_bars_before_cutoff, 603488@2020-07-08:no_bars_before_cutoff, 603922@2020-07-08:no_bars_before_cutoff, 601599@2020-07-09:no_bars_before_cutoff。
- snapshot clusters total=65127, fetched_this_run=0, fetch_seconds=0.0。
- skipped_signal_rows_no_next_open=1094；无下一交易日开盘数据时不新开隔夜仓，避免用收盘强平替代 S1 卖出模型。

## S1 分段关键指标
| regime | return | max_drawdown | trades | expectancy_after_cost | profit_factor | win_rate | fee_ratio | forced_hold_events |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| bull | -2.12% | 2.12% | 3 | -7073.36 | 0.0000 | 0.00% | 0.08% | 0 |
| bear | -6.18% | 6.18% | 5 | -12354.67 | 0.0000 | 0.00% | 0.08% | 0 |
| range | -6.11% | 6.77% | 9 | -6789.82 | 0.0095 | 11.11% | 0.08% | 0 |
| oos | -7.52% | 8.14% | 19 | -3957.63 | 0.2026 | 31.58% | 0.08% | 0 |

## forced_hold 占比
| regime | sell_rejections | forced_hold_events | forced_hold占卖单拒单 |
|---|---:|---:|---:|
| bull | 0 | 0 | 0.00% |
| bear | 0 | 0 | 0.00% |
| range | 0 | 0 | 0.00% |
| oos | 0 | 0 | 0.00% |

## in-sample vs OOS 差异
| span | avg/period_return | trades | expectancy | profit_factor | win_rate | max_drawdown_worst |
|---|---:|---:|---:|---:|---:|---:|
| in_sample(bull+bear+range) | -4.80% | 17 | -8476.58 | 0.0040 | 5.88% | 6.77% |
| oos | -7.52% | 19 | -3957.63 | 0.2026 | 31.58% | 8.14% |

## 对照组 ratio 表
| regime | metric | S1 | random_2_same_eligible | prefilter_equal | S1/random | S1/prefilter | note |
|---|---|---:|---:|---:|---:|---:|---|
| bull | return | -2.12% | -2.12% | -72.22% | 1.0000 | 0.0294 |  |
| bull | max_drawdown | 2.12% | 2.12% | 72.20% | 1.0000 | 0.0294 |  |
| bull | trades | 3.0000 | 3.0000 | 24406.0000 | 1.0000 | 0.0001 |  |
| bull | fee_ratio | 0.08% | 0.08% | 0.23% | 1.0000 | 0.3249 |  |
| bear | return | -6.18% | -6.18% | -85.20% | 1.0000 | 0.0725 |  |
| bear | max_drawdown | 6.18% | 6.18% | 85.17% | 1.0000 | 0.0725 |  |
| bear | trades | 5.0000 | 5.0000 | 28976.0000 | 1.0000 | 0.0002 |  |
| bear | fee_ratio | 0.08% | 0.08% | 0.33% | 1.0000 | 0.2272 |  |
| range | return | -6.11% | -6.11% | -95.40% | 1.0000 | 0.0641 |  |
| range | max_drawdown | 6.77% | 6.77% | 95.39% | 1.0000 | 0.0710 |  |
| range | trades | 9.0000 | 9.0000 | 28543.0000 | 1.0000 | 0.0003 |  |
| range | fee_ratio | 0.08% | 0.08% | 0.27% | 1.0000 | 0.2827 |  |
| oos | return | -7.52% | -7.52% | -90.99% | 1.0000 | 0.0826 |  |
| oos | max_drawdown | 8.14% | 8.14% | 90.98% | 1.0000 | 0.0895 |  |
| oos | trades | 19.0000 | 19.0000 | 44709.0000 | 1.0000 | 0.0004 |  |
| oos | fee_ratio | 0.08% | 0.08% | 0.43% | 1.0000 | 0.1775 |  |

## 反假设列表
- 5min 近似偏差：price_at_1450 用 14:50 endpoint bar close，不是 tick；VWAP/全程在均价线上方看不到 5min 内跌破，14:30 后新高也不知道 bar 内先后。偏差方向偏乐观，可能高估可执行性。
- 次日跌停/停牌卖不掉：卖出完全复用 constraints.py；一字跌停或停牌产生 forced_hold 并顺延下一可成交开盘，上表披露 forced_hold 占比。
- 幸存者偏差：日线面板含 delisted 标记，预筛中实际出现退市股；上方列出退市候选行数和样例。仍承认免费数据的退市日线质量弱于在市股。
- edge 是否只是小盘/低价 beta：预筛本身限定流通市值 <200亿且近4日涨停，报告加入同 S1 eligible 随机2只与预筛者等权隔夜对照；若 S1 不优于对照，不能声称有独立 alpha。

## flag/参数调查记录
- 本轮没有修改 `configs/strategy.yaml`、`configs/backtest.yaml` 或 `configs/cost.yaml`。
- 未触碰 OOS 调参；OOS 只在本次固定规则跑完后用于 C 组最终裁决。
- 没有使用 D 日收盘涨跌幅、收盘换手或 14:50 之后的任何分钟 bar 做预筛或信号。

## Gate1 判定表
| group | metric | actual | threshold | result |
|---|---:|---:|---:|---|
| A/bull | expectancy_after_cost | -7073.3636 | 0.0000 | FAIL |
| A/bull | profit_factor | 0.0000 | 1.3000 | FAIL |
| A/bull | max_drawdown | 0.0212 | 0.2000 | PASS |
| A/bear | expectancy_after_cost | -12354.6665 | 0.0000 | FAIL |
| A/bear | profit_factor | 0.0000 | 1.3000 | FAIL |
| A/bear | max_drawdown | 0.0618 | 0.2000 | PASS |
| A/range | expectancy_after_cost | -6789.8181 | 0.0000 | FAIL |
| A/range | profit_factor | 0.0095 | 1.3000 | FAIL |
| A/range | max_drawdown | 0.0677 | 0.2000 | PASS |
| B/merged | trades | 36.0000 | 200.0000 | FAIL |
| B/merged | expectancy_after_cost | -6091.5778 | 0.0000 | FAIL |
| B/merged | profit_factor | 0.0824 | 1.3000 | FAIL |
| C/oos | expectancy_after_cost | -3957.6324 | 0.0000 | FAIL |
| C/oos | profit_factor | 0.2026 | 1.3000 | FAIL |
| C/oos | max_drawdown | 0.0814 | 0.2000 | PASS |
| C/oos | trades | 19.0000 | 60.0000 | FAIL |
| TOTAL | A+B+C | - | - | FAIL |

最终判定：FAIL
