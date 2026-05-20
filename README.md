# 小李的 AI 工厂 · 用代码扒穿量化神话

> A-share PIT quantitative strategy validation framework with realistic retail constraints.

这是一个面向 A 股散户环境的量化策略验证框架：严格 point-in-time、反幸存者、含真实交易成本、T+1、涨跌停/停牌撮合约束，并把 OOS 隔离写进配置和报告。目标不是制造神话，而是把网红策略、经典轮动、趋势和多因子放到同一套可复跑的 Gate 里验证。

## ✅ 已验证策略

| ID | 策略 | 结果 | 一句话归因 |
|---|---|---:|---|
| S1 | 杨永兴尾盘法 5min PIT | FAIL | 去掉未来函数后触发少、分段全负，OOS 也亏。 |
| S2 | 周频多因子选股 | FAIL | PIT 修复后 OOS PF/DD 不达标，且小盘冲击成本风险很高。 |
| S3 | ETF/指数横截面动量轮动 | FAIL | 高频轮动没有 alpha，成本和 churn 吞掉收益。 |
| S3b | 日频 MA200 趋势 | FAIL | 熊市防御有效，但震荡牛/反弹行情反复 whipsaw。 |
| S3c | 月频 Faber 10M 趋势 | FAIL | 文献标准月频仍未通过 OOS，2019-2021 牛市捕获不足。 |
| S4 | 经典二八轮动 | FAIL | OOS 为正，但 bear/range 失败，且不能稳定优于 50/50。 |

## 🧭 唯一部分验证的 Edge

趋势型熊市资本保全是目前唯一相对稳健的部分 edge：多段独立熊市里，趋势过滤通常能显著降低回撤。但它尚不足以构成独立可交易盈利策略，因为牛市捕获和震荡市表现不稳定。

## 📁 项目结构

| 目录 | 用途 |
|---|---|
| `data/` | BaoStock / akshare 数据源、PIT 面板生成、缓存接口。 |
| `strategies/` | S1-S4、S3b、S3c 策略信号实现。 |
| `backtest/` | 事件驱动回测、成本/滑点/T+1/涨跌停约束、Gate1 runner。 |
| `paper/` | 前向 paper trading harness 和 OOS walk-forward 代理。 |
| `exec/` | Broker 抽象、纸面撮合、东方财富 GUI stub。 |
| `reports/` | Gate1 报告、sanity review、传播素材。 |
| `configs/` | 策略参数、成本、回测 regime 和 Gate 规则。 |
| `scripts/` | 图表和内容生成脚本。 |
| `tests/` | 撮合约束和 paper broker 单测。 |

## 🚀 自己复跑

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
python3 -m data.akshare_source --check
python3 -m pytest
```

常用 Gate1 入口：

```bash
python3 -m backtest.s1_gate1
python3 -m backtest.s2_gate1
python3 -m backtest.engine --strategy s3 --all-gate1
python3 -m backtest.engine --strategy s3b --all-gate1
python3 -m backtest.s3b_fullhistory
python3 -m backtest.s3c_gate1
python3 -m backtest.s4_erba_gate1
```

`data/cache/` 不入库：里面是 BaoStock / akshare 的本地 parquet 缓存和 PIT 面板，体积可达数百 MB。复跑时按上面的数据检查和各 Gate runner 自动重新生成。

## 🧱 防作弊铁律

1. **PIT / no look-ahead**: 信号只能读取 `as_of_date` 及以前的数据，分钟策略必须截断到决策时点。
2. **反幸存者**: 股票池保留退市/ST，不能用 2026 年仍活着的股票反推历史。
3. **真实成本**: 佣金万 2.5、单笔最低 5 元、印花税、过户费、0.2% 滑点统一来自 `configs/cost.yaml`。
4. **OOS 隔离**: `configs/backtest.yaml` 的 OOS 只做最终裁决，不参与调参和选型。

## 🛑 死线声明

本项目不接荐股、不做代客理财、不卖金融广告、不承诺收益。所有报告只用于研究复现和风险教育。

## 🌐 内容矩阵

全网同名：**小李的 AI 工厂**

小红书 / 知乎 / 公众号 / B站：用工程化方法拆解 AI、量化、Agent 和 app 实战。

## License

MIT
