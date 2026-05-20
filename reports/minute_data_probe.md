# Minute Data Probe

BaoStock version `0.9.1`; login error_code=0, error_msg=success.

## BaoStock 5min Active Stock Probes
| code | request_start | rows | earliest | latest | fields | sample_day | rows_on_sample_day | time_min | time_max | has_14:50_bar | has_15:00_bar | chunk_seconds | errors_sample |
|---|---:|---:|---:|---:|---|---:|---:|---:|---:|---|---|---:|---|
| sh.600000 | 2015-01-01 | 74016 | 2020-01-02 | 2026-05-19 | date, time, code, open, high, low, close, volume, amount, adjustflag | 2020-01-02 | 48 | 20200102093500000 | 20200102150000000 | True | True | 80.6 | [] |
| sh.600000 | 2018-01-01 | 74016 | 2020-01-02 | 2026-05-19 | date, time, code, open, high, low, close, volume, amount, adjustflag | 2020-01-02 | 48 | 20200102093500000 | 20200102150000000 | True | True | 72.1 | [] |
| sh.600000 | 2020-01-01 | 74016 | 2020-01-02 | 2026-05-19 | date, time, code, open, high, low, close, volume, amount, adjustflag | 2020-01-02 | 48 | 20200102093500000 | 20200102150000000 | True | True | 70.4 | [] |

Additional active sanity check:
| code | request_start | rows | earliest | latest | has_14:50_bar |
|---|---:|---:|---:|---:|---|
| sz.000001 | 2020-01-01 | 74016 | 2020-01-02 | 2026-05-19 | True |

Sample intraday times from sh.600000: `20200102093500000, 20200102094000000, 20200102094500000, ..., 20200102144000000, 20200102144500000, 20200102145000000, 20200102145500000, 20200102150000000`.

## BaoStock Delisted Stock Probe
| code | name | request_span | rows | earliest | latest | has_14:50_bar | chunk_seconds | errors_sample |
|---|---|---|---:|---:|---:|---|---:|---|
| sz.000005 | ST星源 | 2023-01-01..2024-04-25 | 13536 | 2023-01-03 | 2024-03-05 | True | 9.3 | [] |
| sz.000038 | 大通退 | 2021-01-01..2022-06-30 | 17232 | 2021-01-04 | 2022-06-30 | True | 11.8 | [] |
| sh.600001 | 邯郸钢铁 | 2008-01-01..2009-12-28 | 0 | NA | NA | False | 2.9 | [] |

退市股实测结论：2018 年后退市样例 `sz.000005`、`sz.000038` 均返回退市前 5min 历史，反幸存者在这两个样例上成立；旧退市样例 `sh.600001` 在 2008-2009 返回 0 行，说明 BaoStock 5min 深度不是 2010 前全覆盖。

## Regime Coverage Using sh.600000
| regime | span | coverage |
|---|---|---|
| bull | 2020-07-01..2021-02-10 | covered |
| bear | 2022-01-01..2022-10-31 | covered |
| range | 2023-06-01..2024-09-30 | covered |
| oos | 2024-10-01..2026-05-15 | covered |

## S1 14:50 PIT Field Rebuild Assessment From BaoStock 5min
| S1 field | verdict | reason / bias |
|---|---|---|
| price_at_1450 | 近似可算 | 存在 14:50 endpoint bar，取该 5min close；不是逐笔/tick exact，若 bar timestamp 是区间终点则代表 14:45-14:50 聚合收盘。 |
| pct_chg_at_1450 | 近似可算 | 14:50 5min close / 昨收 - 1；偏差来自 5min endpoint 与真实 14:50 last price差异。 |
| ≤14:50累计量比 | 近似可算 | 5min volume 可累加到 14:50，再除过去5日均量与已过交易时长比例；偏差来自 5min 聚合口径与停牌/半日异常处理。 |
| turnover_at_1450 | 近似/需外部股本 | 实测 5min `turn` 字段报错：`5分钟线指标参数传入错误:turn`。可用累计量 / 流通股本近似，但 BaoStock 5min 本身不直接给分钟换手。 |
| VWAP曲线/全程在均价线上 | 近似可算 | amount 和 volume 可算 5min endpoint 累计 VWAP，并检查每根 5min close>=VWAP；偏差是无法发现 5min 内跌破 VWAP。 |
| 14:30后创新高 | 近似可算 | 用 14:30 后 5min high 与之前 high 对比；偏差是 5min 内部先后顺序不可见。 |
| 涨跌停/停牌撮合 | 需日线/约束层补充 | 5min bars 本身不提供完整 limit_up/down；需接项目日线字段和 constraints.py。 |

## Field Support Notes
- `turn` field test: error_code=10004012, error_msg=5分钟线指标参数传入错误:turn, returned_fields=[], rows=0.
- Actual 5min fields used: `date,time,code,open,high,low,close,volume,amount,adjustflag`.
- 单次 `2015-01-01..2026-05-19` 长请求超过 6 分钟无返回，已中断；上表行数来自按月分块实测。
- BaoStock 免费登录成功，样本股按月分块稳定；全市场多年分钟拉取的限频/吞吐仍需工程压测。

## Verdict
S1 历史无未来函数 Gate1 可以用 BaoStock 重开为“5min PIT 近似版”：活跃股覆盖 2020-2026 四个 regimes，2018 年后退市股样例可取；但它不是 tick/1min exact 的 14:50，turnover 需外部股本/日线近似补齐，VWAP路径和 14:30 后创新高存在 5min 聚合偏差。
