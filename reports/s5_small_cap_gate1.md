# S5 Small-Cap Gate1 Report

规则：月末 D 收盘后，在 PIT 全市场 panel 中剔除 ST=True、上市不足 60 个交易日、价格低于 1.0 的股票；按 D-1 `prev_float_mv` 升序取 hold_n=10，下月首个交易日开盘等权调仓。
关键无未来函数约束：排名只用 `mv_date < as_of_date` 的 float_mv，严禁 D 日 close 计算市值；D 日 close 只用于月末已知的价格过滤和目标股数估算。

## 数据与 universe
- panel=7528942 rows / 5420 symbols；active=5204, delisted=216。
- panel span=2019-10-08..2026-05-15；month_end_count=80。
- PIT candidate rows=338504, dates=78, per-month universe min/median/max=3285/4526.5/4923。
- selected rows=780, unique_symbols=162, selected_delisted_symbols=12, selected_ST_rows_at_signal=0。
- D-1 market-cap assertion rows=338504; all candidates satisfy mv_date < as_of_date。
- selected prev_float_mv min/median=49407325/467790026; D-day amount min/median=0/17385876。
- ST during holding window rows/symbols=0/0。
- ST caveat: panel ST flag is the best available AkShare-derived approximation; historical point-in-time ST transitions are not complete in free data.

## S5 分段关键指标
| regime | return | max_drawdown | trades | expectancy_after_cost | profit_factor | win_rate | fee_ratio | forced_hold | filled_orders |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| bull | -18.42% | 28.71% | 39 | -4723.00 | 0.3893 | 48.72% | 0.08% | 0 | 86 |
| bear | -21.65% | 34.43% | 57 | -3798.35 | 0.4299 | 40.35% | 0.08% | 0 | 129 |
| range | -5.37% | 46.07% | 78 | -688.49 | 0.8891 | 42.31% | 0.08% | 14 | 185 |
| oos | 49.10% | 17.83% | 92 | 5337.00 | 4.0526 | 79.35% | 0.08% | 16 | 204 |

## 月均换手率
| regime | rebalance_count | avg_target_turnover | avg_filled_orders_per_rebalance |
|---|---:|---:|---:|
| bull | 8 | 28.57% | 10.75 |
| bear | 10 | 34.44% | 12.90 |
| range | 16 | 36.00% | 11.56 |
| oos | 20 | 23.68% | 10.20 |

## in-sample vs OOS 差异
| span | avg/period_return | trades | expectancy | profit_factor | win_rate | max_drawdown_worst |
|---|---:|---:|---:|---:|---:|---:|
| in_sample(bull+bear+range) | -15.15% | 174 | -2611.52 | 0.6102 | 43.10% | 46.07% |
| oos | 49.10% | 92 | 5337.00 | 4.0526 | 79.35% | 17.83% |

## 对照组 ratio 表
| regime | metric | S5 | HS300ETF_BH | CSI500ETF_BH | universe_all_equal_synth | random10_monthly | S5/HS300 | S5/CSI500 | note |
|---|---|---:|---:|---:|---:|---:|---:|---:|---|
| bull | return | -18.42% | 37.91% | 11.97% | -1.38% | -12.64% | -0.4858 | -1.5384 |  |
| bull | max_drawdown | 28.71% | 6.77% | 10.88% | 18.01% | 27.19% | 4.2379 | 2.6387 | ratio>2x，需调查 |
| bull | trades | 39.0000 | 1.0000 | 1.0000 | 8.0000 | 79.0000 | 39.0000 | 39.0000 | ratio>2x，需调查 |
| bull | fee_ratio | 0.08% | 0.08% | 0.08% | 0.00% | 0.08% | 0.9083 | 0.9675 |  |
| bear | return | -21.65% | -29.44% | -30.20% | -18.07% | -25.86% | 0.7355 | 0.7168 |  |
| bear | max_drawdown | 34.43% | 28.78% | 31.67% | 29.41% | 29.49% | 1.1963 | 1.0873 |  |
| bear | trades | 57.0000 | 1.0000 | 1.0000 | 10.0000 | 100.0000 | 57.0000 | 57.0000 | ratio>2x，需调查 |
| bear | fee_ratio | 0.08% | 0.07% | 0.07% | 0.00% | 0.08% | 1.1457 | 1.1502 |  |
| range | return | -5.37% | 10.81% | -2.17% | -9.76% | -19.15% | -0.4968 | 2.4795 | ratio>2x，需调查 |
| range | max_drawdown | 46.07% | 22.13% | 27.54% | 31.99% | 44.92% | 2.0819 | 1.6728 | ratio>2x，需调查 |
| range | trades | 78.0000 | 1.0000 | 1.0000 | 16.0000 | 159.0000 | 78.0000 | 78.0000 | ratio>2x，需调查 |
| range | fee_ratio | 0.08% | 0.08% | 0.08% | 0.00% | 0.08% | 0.9894 | 1.0302 |  |
| oos | return | 49.10% | 4.19% | 29.85% | 37.96% | 83.40% | 11.7201 | 1.6450 | ratio>2x，需调查 |
| oos | max_drawdown | 17.83% | 16.24% | 17.90% | 16.54% | 23.75% | 1.0979 | 0.9963 |  |
| oos | trades | 92.0000 | 1.0000 | 1.0000 | 20.0000 | 196.0000 | 92.0000 | 92.0000 | ratio>2x，需调查 |
| oos | fee_ratio | 0.08% | 0.08% | 0.08% | 0.00% | 0.08% | 1.0496 | 0.9800 |  |

## 反假设列表
- 小市值溢价是否是 2017 前 phenomenon：Gate1 panel 从 2019-10 开始，无法直接验证 2017 前；用 post-2019 的 bull(2020-07..2021-02) vs OOS(2024-10..) 看衰减。bull return=-18.42%/PF=0.3893，OOS return=49.10%/PF=4.0526。本次 bull 未强于 OOS；不能用本段证明 2024 后衰减，但仍必须看 A/B/C 是否过关。
- 流动性陷阱：最小市值 10 只的 selected median D-day amount=17385876，min amount=0；0.2% 滑点可能严重低估真实冲击成本，尤其在涨跌停/停牌和小成交额月份。
- ST/退市风险：信号时剔除 ST，但 selected_delisted_symbols=12（退市前仍可入池），持仓窗口内 ST rows/symbols=0/0；forced_hold=30, sell_rejections=30, forced_hold占卖单拒单=100.00%。
- 同 universe 全量等权是 synthetic no-cost benchmark，用于看小市值池整体漂移，不代表散户可逐只实盘复制；S5/random10/ETF 对照均走 constraints.py。

## flag/参数调查记录
- 未调 hold_n，固定使用 strategy_addon.yaml 的 hold_n=10。
- 未碰 OOS 调参；OOS 只在固定规则跑完后用于 C 组最终裁决。
- 未用 D 日 close 计算市值；候选表断言 `mv_date < as_of_date`。
- 未修改成本、滑点、regime 或 Gate1 阈值。

## Gate1 判定表
| group | metric | actual | threshold | result |
|---|---:|---:|---:|---|
| A/bull | expectancy_after_cost | -4723.0050 | 0.0000 | FAIL |
| A/bull | profit_factor | 0.3893 | 1.3000 | FAIL |
| A/bull | max_drawdown | 0.2871 | 0.2000 | FAIL |
| A/bear | expectancy_after_cost | -3798.3459 | 0.0000 | FAIL |
| A/bear | profit_factor | 0.4299 | 1.3000 | FAIL |
| A/bear | max_drawdown | 0.3443 | 0.2000 | FAIL |
| A/range | expectancy_after_cost | -688.4868 | 0.0000 | FAIL |
| A/range | profit_factor | 0.8891 | 1.3000 | FAIL |
| A/range | max_drawdown | 0.4607 | 0.2000 | FAIL |
| B/merged | trades | 266.0000 | 200.0000 | PASS |
| B/merged | expectancy_after_cost | 137.5917 | 0.0000 | PASS |
| B/merged | profit_factor | 1.0276 | 1.3000 | FAIL |
| C/oos | expectancy_after_cost | 5337.0031 | 0.0000 | PASS |
| C/oos | profit_factor | 4.0526 | 1.3000 | PASS |
| C/oos | max_drawdown | 0.1783 | 0.2000 | PASS |
| C/oos | trades | 92.0000 | 60.0000 | PASS |
| TOTAL | A+B+C | - | - | FAIL |

最终判定：FAIL，按高换手标准。
