# A-W24 S12 OOS Robustness Sensitivity

本报告只改变 OOS 时段切分，不改变 S12 策略参数、资产池定义、lookback、权重方法、成本或滑点。

## Flag
- S12 参数零调整, 仅改 OOS 时段切分, 这不是 p-hacking 而是 robustness 检验。
- 四个 OOS 方案预先列定，无论结果好坏全部报告。
- 每个 OOS 使用 Gate1 C 维度：expectancy>0、PF>=1.3、DD<=20%、trades>=60。

## ETF 数据覆盖
| code | name | class | earliest | latest | rows | source |
|---|---|---|---:|---:|---:|---|
| 510300 | 沪深300ETF | a_share_large | 2012-05-28 | 2026-05-15 | 3392 | sina_fund_etf_hist_sina |
| 510500 | 中证500ETF | a_share_mid | 2013-03-15 | 2026-05-15 | 3195 | sina_fund_etf_hist_sina |
| 159920 | 恒生ETF | hk_share | 2012-10-22 | 2026-05-15 | 3293 | sina_fund_etf_hist_sina |
| 513100 | 纳指ETF | us_share | 2013-07-31 | 2026-05-15 | 3106 | sina_fund_etf_hist_sina |
| 513500 | 标普500ETF | us_share_alt | 2014-01-15 | 2026-05-15 | 2994 | sina_fund_etf_hist_sina |
| 518880 | 黄金ETF | commodity | 2013-07-29 | 2026-05-15 | 3109 | sina_fund_etf_hist_sina |
| 511010 | 国债ETF | bond | 2013-04-09 | 2026-05-15 | 3182 | sina_fund_etf_hist_sina |

## 4 个 OOS 方案
| scheme | OOS window | effective window | pool | dropped | return | DD | trades | expectancy | PF | C判定 |
|---|---|---|---:|---|---:|---:|---:|---:|---:|---|
| A default_2024_10 | 2024-10-01..2026-05-15 | 2024-10-08..2026-05-15 | 7 | - | 12.01% | 4.52% | 75 | 1601.14 | 14.0651 | PASS |
| B bear_2022_to_2023 | 2022-01-01..2023-12-31 | 2022-01-04..2023-12-29 | 7 | - | -6.07% | 12.23% | 81 | -749.62 | 0.4941 | FAIL |
| C covid_bull_2020_to_2021 | 2020-01-01..2021-12-31 | 2020-01-02..2021-12-31 | 7 | - | 14.50% | 9.53% | 86 | 1686.60 | 6.4832 | PASS |
| D early_2016_to_2017 | 2016-01-01..2017-12-31 | 2016-01-04..2017-12-29 | 7 | - | 10.03% | 5.89% | 87 | 1152.49 | 3.6829 | PASS |

## 总判定
| metric | value |
|---|---:|
| OOS PASS schemes | 3/4 |
| OOS FAIL schemes | 1/4 |
| robustness | weak |
|判定口径|4/4 才 strong；1-3/4 归 weak；0/4 归 fail|

## In-Sample 展示
| scheme | in-sample definition | return | DD | trades | expectancy | PF | win_rate |
|---|---|---:|---:|---:|---:|---:|---:|
| A | 2014-2026 excluding 2024-10-01..2026-05-15 | 118.83% | 11.97% | 425 | 2796.06 | 6.5707 | 75.29% |
| B | 2014-2026 excluding 2022-01-01..2023-12-31 | 164.52% | 9.54% | 420 | 3225.43 | 19.0377 | 85.24% |
| C | 2014-2026 excluding 2020-01-01..2021-12-31 | 116.79% | 12.23% | 419 | 2407.15 | 7.6273 | 75.18% |
| D | 2014-2026 excluding 2016-01-01..2017-12-31 | 125.50% | 11.95% | 419 | 2396.62 | 7.6928 | 75.18% |

## Leave-One-Out 贡献检查
逐个去掉 7 ETF 中的一只，S12 参数仍固定不变；`avg return delta full-minus-removed` 为正，表示完整池平均收益高于去掉该 ETF，说明该 ETF 对结果有正贡献。
| removed | removed_name | PASS schemes | avg return delta full-minus-removed | A | B | C | D | A_delta | B_delta | C_delta | D_delta |
|---|---|---:|---:|---|---|---|---|---:|---:|---:|---:|
| 518880 | 黄金ETF | 3/4 | 2.43% | PASS | FAIL | PASS | PASS | 4.29% | 3.31% | -0.47% | 2.61% |
| 513100 | 纳指ETF | 3/4 | 1.18% | PASS | FAIL | PASS | PASS | 1.81% | -3.22% | 4.03% | 2.12% |
| 513500 | 标普500ETF | 3/4 | 0.52% | PASS | FAIL | PASS | PASS | 0.94% | -2.07% | 1.04% | 2.17% |
| 510300 | 沪深300ETF | 3/4 | -0.12% | PASS | FAIL | PASS | PASS | -0.28% | -2.09% | 1.75% | 0.15% |
| 510500 | 中证500ETF | 3/4 | -0.13% | PASS | FAIL | PASS | PASS | 1.15% | -2.31% | 2.45% | -1.81% |
| 159920 | 恒生ETF | 3/4 | -0.61% | PASS | FAIL | PASS | PASS | -0.42% | -0.46% | -3.39% | 1.86% |
| 511010 | 国债ETF | 3/4 | -9.16% | PASS | FAIL | PASS | PASS | -23.64% | 14.94% | -11.64% | -16.30% |

## 反假设
1. OOS PASS 是否仅靠 2024-2025 海外+黄金大涨：实测不是只靠方案 A，方案 C/D 也 PASS；但方案 B(2022-2023) FAIL，说明 S12 对 2022 熊市窗口不稳健，不能给 strong。
2. 7 ETF 中是否由单一资产支撑：leave-one-out 表显示去掉任一单只 ETF 后仍是 3/4 PASS，没有单一资产决定全部 PASS/FAIL；收益贡献最大的是黄金ETF和纳指ETF，去掉国债ETF反而提高平均 OOS 收益，但这不改变 2022-2023 FAIL。

## Forward Paper 衔接建议
robustness=weak，不建议上真金；只适合继续 forward paper 小资金/纸面跟踪。

最终 robustness=weak，OOS PASS=3/4。
