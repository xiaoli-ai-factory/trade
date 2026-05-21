# 双层多 Agent 协调框架 v1

> 目标 1：找到可以实盘操作的最优量化模型
> 目标 2：自媒体涨粉 + 接广告变现
>
> Claude = 总指挥（规划+review+裁决）· Codex MCP = 执行（代码+回测）· subagents = 隔离 context 的研究/搜索

---

## 1. 顶层架构

```
Tier 1: Claude（你正在对话的我）
  └─ 维护 STATE.md / TASK_QUEUE.md / sanity_review.md
  └─ 每轮决定调度哪个 sub-agent
  └─ review 所有 agent 产物（重点 review 碰钱/碰数据的代码）

Tier 2: 两个顶层 Agent（功能域）
  ├─ Agent A · 量化探索 (Quant Research)
  └─ Agent B · 自媒体挖掘 (Content Mining)

Tier 3: Sub-agents（最小化任务）
  按需从 Codex MCP / Explore / general-purpose / Plan 中调度
```

## 2. Agent A · 量化探索（7 个 sub-agent）

每个 sub-agent 一次只做一件可量化产物，失败可重启。

| ID | 名称 | 工具 | 单次产物 | 通过判据 |
|---|---|---|---|---|
| A1 | **策略发现** | Explore subagent | 5-10 个候选策略清单 + 评分 | 含 url+star+反诈价值评估 |
| A2 | **数据探针** | Codex MCP（短任务）| 数据源覆盖表 | regimes 覆盖明确 |
| A3 | **策略实现** | Codex MCP（中任务）| strategies/sX_*.py | py_compile + pytest pass |
| A4 | **Gate1 验证** | Codex MCP（中任务）+ Claude review | reports/sX_gate1.md + A/B/C 判定 | 报告含 SPEC §3 三节 |
| A5 | **Paper 监控** | cron 脚本 + Claude 每月 review | paper/dashboard.md 更新 | 每月对账偏离 &lt; 阈值 |
| A6 | **组合合成** | Codex MCP（仅当 ≥2 partial PASS 时启动）| sXX_composite_*.py | ex-ante 预注册 |
| A7 | **反诈沉淀** | general-purpose subagent | reports/anti_scam_checklist.md 更新 | 新加共性失败机制 |

### A 的工作流（一轮迭代）
```
[新策略灵感] → A1 发现 → Claude 排序选 1 个 → A2 数据探针 → A3 实现 → A4 Gate1
                              ↓                                       ↓
                              ↑————————— FAIL 写入 sanity_review ←—————┘
                              ↓
                         PASS / partial PASS → A6 合成尝试 → A4 再验证
                              ↓
                         所有 PASS 策略 → A5 paper 累积 → 每月 review
                              ↓
                         每 3-5 个 FAIL → A7 沉淀新失败模式
```

### A 的预承诺纪律（不可违反）
- 每个策略 ex-ante 预注册参数，OOS 永不调参
- 所有数字必须来自固定规则跑出来的 report，禁止粉饰
- FAIL 是合法结果，比假 PASS 价值高千倍
- 数据残缺就 skip 不伪造（如 sina ETF 延迟 1 日 → 跳过当日）

## 3. Agent B · 自媒体挖掘（6 个 sub-agent）

| ID | 名称 | 工具 | 单次产物 | 通过判据 |
|---|---|---|---|---|
| B1 | **热点抓取** | Explore subagent | 知乎/小红书/B站当周热议话题清单 | 含搜索证据 + 话题热度估算 |
| B2 | **角度匹配** | Claude（推理为主）| "实验结果 × 当前热点"映射表 | 至少 1 个强匹配 |
| B3 | **内容生产** | xhs-data-content skill | 1080×1350 信息图 + HTML | 数据全部来自 reports/ |
| B4 | **发布运营** | Claude（编辑+合规检查）| 标题/正文/标签三套 | 0 个平台违规词 |
| B5 | **反馈闭环** | xhs-data-content §6 + Claude | likes/views 写回 skill | 更新经验库 |
| B6 | **变现漏斗** | Claude（规划）| 内容矩阵 + 公众号/星球架构 | 每步可量化 |

### B 的工作流
```
A4 出新 FAIL 报告 → B1 看本周热点 → B2 匹配角度
                                          ↓
                                   B3 出 HTML + 6 图（30 分钟）
                                          ↓
                                   B4 合规审查 + 文案优化
                                          ↓
                                  [用户实际发布]
                                          ↓
                                   B5 收集真实反馈 → 更新 skill §6
                                          ↓
                                   B6 评估漏斗转化（粉→公众号→星球→课程）
```

### B 的预承诺纪律
- 不为流量虚构数字（A 已经守的纪律 B 也守）
- 不接金融广告 / 不卖荐股 / 不卖速成致富课（项目死线）
- 所有 CTA 走"全网同名 + GitHub 中转 + 暗号"合规三招

## 4. 跨 Agent 协调

### 4.1 共享状态文件（全部在 reports/ 下）

| 文件 | 维护者 | 内容 |
|---|---|---|
| `STATE.md` | Claude | 项目当前快照（agent 进度、关键指标、下一步） |
| `TASK_QUEUE.md` | Claude | 优先级排序的待执行任务 |
| `sanity_review.md` | Claude+所有 agent | 检查点（每个 agent 完成后追加） |
| `agent_logs/<id>_<ts>.md` | 各 agent 自写 | 单次执行的输入/输出/异常 |

### 4.2 反馈跨域（关键设计）
- B5 收集到"S5 小市值反诈那期点赞 5000，S6 双均线那期才 300" → 写入 STATE.md
- Claude 下一轮 dispatch A1 时优先级倾斜："找另一个曾经被吹为圣杯的因子策略"（不是"找另一个技术指标"）
- 实现：B5 → STATE.md → Claude → A1 prompt 调整

### 4.3 冲突仲裁
- **A vs B 资源冲突**（Codex MCP 单 channel）：A 优先（代码不能等）
- **A 内部冲突**（A3 在跑时收到新 A1 候选）：放进 TASK_QUEUE.md 不打断
- **诚实纪律冲突**（B 想用 A 数据但 A 数据有 partial PASS 模糊）：以 SPEC §0 铁律优先，宁可不发也不模糊

## 5. 一轮迭代的完整流程（用户视角）

用户只需要说 **"继续推进"** 或 **"下一轮"**，Claude 自动：

1. 读 STATE.md 看当前是 A 域还是 B 域空闲
2. 读 TASK_QUEUE.md 取优先级最高的待办
3. 决定调度哪个 sub-agent（A1-A7 / B1-B6）+ 是否可并行
4. dispatch（Codex MCP / Explore / general-purpose）
5. Claude review 产物（碰钱必逐行）
6. 更新 STATE.md + TASK_QUEUE.md + sanity_review.md（按需）
7. 给用户汇报：本轮做了什么 + 下一轮自动推荐做什么

### 用户不需要每次告诉我细节
说"推进"或具体方向（"先做内容这边"/"先做量化"），其余 Claude 自动调度。

## 6. 终极目标的可量化判据

### 目标 1 ·实盘最优量化策略
- 必要条件：A4 整体 Gate1 PASS（不只是 OOS PASS）
- 充分条件：A5 forward paper ≥40 交易日 + 与 backtest 偏离 &lt; 30%
- 当前进度：S12 OOS PASS 但整体 FAIL；forward 1/40

### 目标 2 · 自媒体变现
- 阶段 1：内容矩阵 6 期全部发布 + 公众号 500+ 粉
- 阶段 2：知识星球 49/年 上线 + 50+ 付费
- 阶段 3：8 周陪跑营 999-2999 上线 + 5+ 报名
- 阶段 4：稳定月流水 ≥ ¥3000（接平台广告 + 课程 + 星球）

每阶段判据可量化，达成则升级到下一阶段。

## 7. 反 p-hacking 在 multi-agent 下的护栏（重要）

multi-agent 会引入新的 p-hacking 风险：**B 想要"成功的策略"出内容**→ 反向施压让 A 调出 PASS。
护栏：
- **A 的 Gate 判定文件**（reports/sX_gate1.md）写完后**不可被 B 修改**
- **B 的引用必须保留 FAIL 标记**，不能在 HTML 里包装成 PASS
- **STATE.md 由 Claude 单点维护**，不让 A/B 直接改（避免 B 把 A 的 FAIL 写成 PASS）
- **sanity_review.md 检查点是终审**：任何看起来太美的结果回头被 reviewer 抓出

## 8. 框架的可演化性

本框架是 v1。未来 trigger 升级：
- A 域加入新 sub-agent（如 A8 "策略风格预测器"用 ML 预测哪类策略下次会 PASS）
- B 域加入新 sub-agent（如 B7 "竞品反诈对标"看其他博主在扒什么）
- 跨平台扩展（C agent · App 开发 / D agent · Agent 工作流）

升级条件：当前 7+6 sub-agent 跑稳后才扩展。

---

**文档版本**：v1 · 2026-05-21 · 维护：Claude
**相关文件**：
- `reports/STATE.md` - 项目状态机
- `reports/TASK_QUEUE.md` - 待办优先队列
- `reports/sanity_review.md` - 8+ 检查点累积
- `reports/hub/multi_agent_dashboard.html` - 可视化看板
- `~/.claude/skills/qc-orchestrator/SKILL.md` - 触发关键词调用本框架
