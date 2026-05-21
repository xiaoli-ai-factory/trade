# 项目状态机 STATE.md

> Claude 单点维护。每轮 dispatch 前读，结束后更新。
> 任何 agent 完成产物后 Claude 来同步,不让 agent 直接改。

**最后更新**：2026-05-21 by Claude

---

## 顶层进度

| 目标 | 当前进度 | 阻塞点 |
|---|---|---|
| 🎯 目标 1：实盘最优量化策略 | S12 OOS PASS / 整体 Gate1 FAIL；forward paper 1/40 交易日 | 需 ≥40 交易日真实累积 |
| 🎯 目标 2：自媒体变现 | 内容资产 7 件齐备 + profile README v2 已 push（dfe9a43）；0 篇真正发布 | 等首篇小红书发布并拿到真实反馈 |

## 策略调整（用户 2026-05-21 指示）

- **重点平台**：小红书 + 知乎（**主战场**）
- **暂不开通**：公众号 / 微信学习群（等小红书 + 知乎首发反馈后再决定）
- **暂不放二维码**：所有 CTA 走"全网同名 + GitHub 仓库链接"两招
- **内容风格硬约束**：必须确保**小红书用户读得下去并愿意互动**（不能只追求数据严谨而失去可读性）
- profile README 已据此更新：移除公众号 CTA、待开发项目改为 ① 苹果健康 App（热量窗口）② SPEC_in 芯片 bringup 框架

## Agent A · 量化探索（当前 idle，可调度）

| Sub-agent | 上次活动 | 累积成果 | 下次建议 |
|---|---|---|---|
| A1 策略发现 | 2026-05-20 | 12 个候选已扫，5 个跑完(S9-S13) | 暂不再扫，先沉淀 |
| A2 数据探针 | 2026-05-20 | baostock 5min / sina ETF fallback 全可得 | 待新策略需新数据时再启 |
| A3 策略实现 | 2026-05-20 | 13 个策略全部实现 | 当 A1 给出新候选时启 |
| A4 Gate1 验证 | 2026-05-20 | 13 个全跑完，S12 OOS PASS 整体 FAIL | 仅当 A3 出新策略时再跑 |
| A5 Paper 监控 | 2026-05-20 启动 | S12 forward NAV 10000.00 day 1/40 | cron 自动 + 每月 1 次 Claude review |
| A6 组合合成 | 2026-05-20（S11 S13）| 已尝试 2 次预注册合成均 FAIL | 暂停（避免 p-hacking） |
| A7 反诈沉淀 | 2026-05-20 | anti_scam_checklist.md 1.48 万字 | 当出现新失败模式时更新 |

**Agent A 当前结论（v2 更新 2026-05-21）**：股票策略类已穷尽（S1-S13 全 FAIL）。继续在股票池里挖 = p-hacking。
**真正可持续找最优 = 5 个未碰新领域**（见 TASK_QUEUE.md A-W22 至 A-W26）：
1. **A-W22 可转债低溢价**（最优先，A 股传闻真有 alpha 的策略）
2. **A-W23 股指期货 CTA**（IF/IC/IH 跨期）
3. **A-W24 S12 OOS robustness 敏感性**（验证之前的"S12 OOS PASS"是否在不同时段切分上稳健）
4. **A-W25 50ETF/300ETF 期权**（covered call / sell put）
5. **A-W26 海外 ETF 折溢价套利**

每个独立 gate1 验证，预注册不调参。FAIL 即停，PASS 进 paper。
Forward paper 仍每日累积（A-Daily），但不再是 A 域唯一可推进事项。

## Agent B · 自媒体挖掘（当前阻塞于用户侧 0→1 发布）

| Sub-agent | 上次活动 | 累积成果 | 下次建议 |
|---|---|---|---|
| B1 热点抓取 | 2026-05-19 | 知乎/小红书"杨永兴""二八轮动"已扫 | 等 B2 出第二期主题再扫 |
| B2 角度匹配 | 2026-05-19 | S1 反诈"反差型"已匹配 | 等下一期主题（S4/S5/S6/S7）确定 |
| B3 内容生产 | 2026-05-20 | S1 反诈 HTML + 6 图 + 知乎长文 + 反诈 checklist 元文档 + 终局报告齐 | 待用户说"出 S4 图集"等 |
| B4 发布运营 | 2026-05-20 | publishing_kit HTML 含一键复制 | 等用户实际发布反馈 |
| B5 反馈闭环 | 未启动 | xhs-data-content skill §6 为空 | 用户发完小红书反馈数据后启 |
| B6 变现漏斗 | 2026-05-20 | 设计完成（公众号→星球→课程）| 等用户注册公众号 |

**Agent B 当前结论**：所有内容资产就位，**阻塞点在用户侧的"0→1 发布"**：注册公众号 + 4 平台占位 + 发首篇小红书。

## 关键数据快照

```yaml
量化:
  backtested_strategies: 13
  oos_pass: 1 (S12)
  overall_pass: 0
  forward_paper_days: 1/40
  best_strategy: S12 (OOS +12.01% / PF 14.07 / A bear -12.25%)

自媒体:
  github_repos: 2 (trade + profile)
  github_repo_stars: 0 (待发布拉粉)
  ready_to_publish_articles: 3 (xhs S1反驳 / 知乎长文 / 反诈checklist)
  ready_to_publish_image_sets: 1 (S1 6图)
  pending_image_sets: 4 (S4/S5/S6/S7 数据齐缺图)
  published: 0
  公众号: 未注册
  小红书: 未注册
  知乎: 未发布 (老号 1万+ 粉但 4/5 机器人)
  B站: 未注册
  forward_paper_started: 2026-05-19

环境:
  本机 IP: 94.124.118.59 (东京 Tokyo · xTom Japan)
  时区: Asia/Tokyo (与 IP 一致 ✓)
  Anthropic 直连: 通 (api.anthropic.com 200/404)
  封号风险: 低-中
  代理: 127.0.0.1:7890 Clash/V2Ray
  Codex MCP: 历史抖断 ≥6 次但都恢复
```

## 跨 Agent 反馈（一旦有真实数据就更新）

| 类型 | 当前 | 下次 |
|---|---|---|
| B5 → A1 优先级偏置 | 无（无发布反馈）| 等小红书首发数据 |
| A → B 新内容触发 | 无（A 暂停发现） | 等新策略 PASS 或 partial PASS |

## 下一轮 Claude 决策（决策树）

```
用户说"推进":
  ├─ 如果用户给了具体方向 (e.g. "做内容") → 按方向调度对应 agent
  ├─ 如果用户没说方向 → 看 STATE 阻塞点
  │   ├─ Agent A 阻塞于"等 40 天" → 优先 Agent B（动）
  │   └─ Agent B 阻塞于"用户没发布" → 提醒用户做 5 件 0→1 任务
  └─ 如果 STATE 没阻塞点 → 取 TASK_QUEUE.md 第 1 项
```

## 待 Claude 主动 review 的产物

- [x] S13 报告（已 review）
- [x] backtest_final_verdict.md（subagent 已自审 + Claude 核）
- [ ] forward paper 首次月度对账（待 30 天后跑）
- [ ] 用户首次发小红书后的真实反馈（待用户）
