# 克苏鲁守秘人轻量版 🕯️

> 基于 `FastAPI + React/Vite + PostgreSQL + Chroma`，以 `KeeperSupervisor` 多 Agent 协作架构为核心的《克苏鲁的呼唤》调查游戏 AI 守秘人网页版原型

---

## 📖 项目简介

**克苏鲁守秘人轻量版**是一个面向单玩家跑团场景的 AI 守秘人原型系统。当前以内置剧本《无光的灯塔》为核心，提供网页化调查体验。

后端核心是 **多 Agent 协作架构**（`KeeperSupervisor`），引入 **Plan-and-Solve + ReAct + Reflection** 三种互补范式，让守秘人回合具备“先规划、再执行、后自检”的可控推理能力：

- **Plan-and-Solve**：每回合开始时由 `PlannerAgent` 生成完整的行动计划（目标、所需资料、可能检定、允许调用的工具/技能、预期状态变化）。
- **ReAct**：在计划白名单约束下，由 `ExecutorAgent` 调用原子 Tools 与复合 Skills，边观察边执行。
- **Reflection**：叙事和状态提交前，由 `GuardAgent` 进行自我批判，校验剧情一致性、规则一致性、防剧透与状态合法性。

同时，系统附带独立的 **游戏助手**（`GameAssistantAgent`），让玩家可以随时查询规则、术语和已发现线索，而不会推进剧情或被剧透。

> 说明：旧版 LangGraph 实现仍保留在 `backend/app/services/agent.py` 的 `_OldKeeperAgent` 中供参考；当前主回合链路不再通过 LangGraph `StateGraph` 执行。

### 学习入口

如果你是第一次接触这个项目，建议先读这几份文档：

- [docs/初学者学习指南](D:/Project/coc-lite/docs/初学者学习指南.md)：适合先建立全局认识
- [docs/后端主线精读](D:/Project/coc-lite/docs/后端主线精读.md)：适合顺着“一次请求怎么跑完整条后端链路”来读
- [docs/Agent监控系统学习指南](D:/Project/coc-lite/docs/Agent监控系统学习指南.md)：适合学习如何观察每个 Agent/Tool/LLM 步骤的输入输出
- [docs/变量查询表](D:/Project/coc-lite/docs/变量查询表.md)：适合查变量和状态字段，不容易在代码里迷路
- [docs/Tools扩展指南](D:/Project/coc-lite/docs/Tools扩展指南.md)：适合学习如何新增一个原子 Tool
- [docs/Skills扩展指南](D:/Project/coc-lite/docs/Skills扩展指南.md)：适合学习如何新增一种复合行动 Skill

### 核心能力

- **单玩家跑团界面**：React/Vite 前端提供新手引导、角色选择、会话恢复、玩家行动输入、流式叙事、游戏助手抽屉和实时调试面板。
- **多 Agent 智能守秘人**：`KeeperSupervisor` 调度 `ContextAgent`、`PlannerAgent`、`ExecutorAgent`、`NarratorAgent`、`GuardAgent` 完成每回合推理。
- **Plan-and-Solve 回合计划**：结构化行动计划约束后续执行，降低随意性。
- **ReAct 工具与技能层**：原子 Tools（检索、检定、物品查询、场景交互、线索判定、记忆召回）与复合 Skills（调查、移动、社交、使用物品、危险与理智、轻量战斗、等待观察）分离。
- **RAG 增强检索**：使用 Chroma 存储剧本、规则书、结构化实体、线索索引和会话记忆向量；RAG 元数据包含 `rag_namespace`、`source_type`、`visibility`、`citation` 等字段。
- **游戏助手 RAG**：支持 **MQE 多查询扩展**、**HyDE 假设文档嵌入**、去重排序与引用来源展示。
- **数据库持久化**：PostgreSQL 保存角色、会话、线索、道具、flag 与回合日志。
- **流式响应**：前端通过 NDJSON 流接收叙事、调试事件与助手回答。
- **实时调试窗口**：前端展示 Agent 节点执行、Skill 状态与 Tool 调用日志。
- **Agent 监控页**：独立 `5174` 端口监控台持久化保存每次 Agent/Skill/Tool/LLM 步骤的输入输出，支持实时查看、历史筛选、删除和全局条数上限。
- **AI 场景图片**：关键场景自动触发图片生成，增强叙事沉浸感。
- **防剧透与状态约束**：多层校验（确定性 guardrails + Reflection）降低玩家侧剧透风险。

---

## 🏗 系统架构

```text
┌──────────────────────────────────────────────┐
│              React/Vite 前端                  │
│  - 新手引导    - 角色选择   - 会话恢复         │
│  - 玩家行动输入 - 流式叙事  - 游戏助手抽屉     │
│  - 状态展示    - 调试面板   - 场景图片         │
└──────────────────────┬───────────────────────┘
                       │ HTTP / NDJSON Stream
┌──────────────────────▼───────────────────────┐
│              FastAPI 后端                     │
│  /coc/api/init           初始化数据库          │
│  /coc/api/import         导入剧本/规则/角色    │
│  /coc/api/characters     查询预设调查员        │
│  /coc/api/sessions       创建/查询/恢复会话    │
│  /coc/api/sessions/{id}/actions/stream       │
│  /coc/api/assistant/chat/stream              │
└──────────────────────┬───────────────────────┘
                       │
┌──────────────────────▼───────────────────────┐
│        KeeperSupervisor：多 Agent 调度        │
│  ContextAgent   → 加载会话、意图识别、构建可见上下文 │
│  PlannerAgent   → Plan-and-Solve 生成回合计划     │
│  ExecutorAgent  → ReAct 调用 Tools / Skills     │
│  NarratorAgent  → 生成守秘人叙事与状态变化        │
│  GuardAgent     → Reflection + 确定性 guardrails │
└──────────────┬───────────────────┬───────────┘
               │                   │
┌──────────────▼─────────────┐ ┌───▼────────────────────┐
│ PostgreSQL                  │ │ Chroma                  │
│ - 角色/会话/回合日志         │ │ - 剧本分块              │
│ - 线索/道具/Story Flag      │ │ - 规则分块              │
│ - 当前地点/场景/时间         │ │ - 实体/线索索引          │
└────────────────────────────┘ │ - 会话长期记忆           │
                               └───────────┬────────────┘
                                           │
                               ┌───────────▼────────────┐
                               │ OpenAI 兼容 LLM / Embedding │
                               │ - 意图 JSON              │
                               │ - 守秘人叙事             │
                               │ - 向量化检索             │
                               └────────────────────────┘
```

---

## 🧠 Agent 设计重点

`KeeperSupervisor` 位于 `backend/app/services/agents/supervisor.py`。前端提交玩家行动后，它会调度各子 Agent 完成一次完整守秘人回合。

### 子 Agent 职责

| Agent | 职责 |
|-------|------|
| **ContextAgent** | 从 PostgreSQL 加载会话、角色、线索、道具、flag；识别玩家意图；从 Chroma 检索剧本、规则、实体、线索索引和长期记忆 |
| **PlannerAgent** | 生成包含意图、目标、允许工具/技能、可能检定、预期状态变化的结构化回合计划 |
| **ExecutorAgent** | 在计划白名单约束下调用 Tools / Skills，读取观察并持续执行，输出 `react_trace`、`tool_observations`、`skill_results` |
| **NarratorAgent** | 基于检索上下文、裁定结果和角色状态生成玩家可见叙事；提取候选状态变化；生成下一步行动建议 |
| **GuardAgent** | Reflection 自检：剧情一致性、规则一致性、防剧透、状态合法性、叙事质量；配合代码层 `deterministic_guardrails` 与 `final_guardrails` |

### 回合数据流

当前回合内部数据主要通过 `AgentMessage.payload` 在子 Agent 间传递；`KeeperSupervisor.run_turn()` 最终返回面向 API 层的结果字典，字段保持对旧版 `KeeperState` 的兼容。核心字段包括：

- **输入上下文**：`db`、`session_id`、`player_input`、`session`、`character`
- **理解与检索**：`intent`、`scenario_context`、`rule_context`、`entity_context`、`clue_context`、`memory_context`
- **计划**：`turn_plan`、`plan_validation`、`plan_gap`
- **执行**：`react_trace`、`tool_observations`、`skill_results`
- **裁定结果**：`adjudication`、`dice_results`、`skill_checks`、`sanity_checks`、`resolution`
- **输出与状态**：`narration`、`options`、`state_delta`
- **审计与校验**：`reflection_report`、`final_guardrail_report`、`validation_report`、`leak_report`
- **可见性隔离**：`visible_context`（玩家可见）、`keeper_only_context`（守秘人秘密）
- **记忆**：`audit`、`summary`、`discovered_clues`、`story_state`

### 协作模块

| 模块 | 作用 |
|------|------|
| `llm.py` | 调用 OpenAI 兼容聊天模型 |
| `retrieval.py` | 查询和写入 Chroma 集合 |
| `rules.py` / `dice.py` | COC 裁定、D100、技能检定、理智检定 |
| `guardrails.py` | 确定性状态校验、剧情偏离检测 |
| `story_state.py` | 构造并应用结构化剧情状态变化 |
| `summary.py` | 生成回合摘要并沉淀为会话记忆 |
| `inventory.py` | 根据 `state_delta` 同步会话物品变化 |
| `tools/` | 原子 Tools：ContextSearch、RuleCheck、InventoryLookup、SceneAffordance、ClueEligibility、MemoryRecall |
| `skills/` | 复合 Skills：investigate、move、social_interaction、use_item、danger_and_sanity、combat_lite、wait_or_observe |
| `assistant_agent.py` | 独立 `GameAssistantAgent`，支持 MQE/HyDE 检索与引用 |
| `debug_events.py` | 调试事件推送（Agent 节点、Skill、Tool 日志） |
| `image_generator.py` | AI 场景图片生成 |

---

## 🔄 守秘人回合流程

```text
load_state / build_visible_context（ContextAgent）
  └─ 加载状态、识别意图、检索上下文

plan_turn（PlannerAgent）
  ├─ 生成结构化回合计划
  ├─ 模糊行动 → clarify_action
  └─ 明确行动 → validate_plan

validate_plan（Supervisor 代码层）
  └─ 校验格式、白名单、风险等级

execute_plan_react（ExecutorAgent）
  ├─ 在白名单内调用 Tools / Skills
  ├─ 读取观察、持续执行
  └─ 输出 react_trace、tool_observations、skill_results

synthesize_resolution
  └─ 汇总检索、检定、骰点，形成结构化裁定

generate_response（NarratorAgent）
  └─ 生成玩家可见叙事

generate_state_delta（NarratorAgent）
  └─ 提取候选状态变化

deterministic_guardrails（代码层）
  └─ 校验地点、场景、时间、危险值、线索、物品、flag

reflection_review（GuardAgent）
  └─ 自检剧情、规则、防剧透、合法性、叙事质量

repair_or_replan（Supervisor）
  └─ 修复文本、修正状态、重规划或安全兜底

final_guardrails（代码层）
  └─ 最终防剧透与输出清洗

generate_next_options（NarratorAgent）
  └─ 生成下一步行动建议

commit_state（代码层）
  └─ 落库、写日志、写长期记忆
```

---

## 🎮 游戏助手

独立于守秘人的 **场外规则助手**，不推进剧情、不修改状态、不掷骰。

- 回答 COC 规则问题、术语解释；
- 回顾已发现线索；
- 基于已发现信息给出非剧透提示；
- 引用规则书或已发现信息来源。

检索增强：

| 技术 | 说明 |
|------|------|
| **MQE** | 多查询扩展：LLM 生成语义等价查询，提升召回 |
| **HyDE** | 假设文档嵌入：LLM 生成假设答案段落再用于检索，提升术语匹配 |
| **去重排序** | 合并多查询结果，综合排序 |
| **引用** | 回答末尾展示来源 |
| **防剧透** | 不访问 `keeper_only` 内容 |

---

## 📂 目录结构

```text
coc-lite/
├── backend/
│   ├── requirements.txt
│   └── app/
│       ├── main.py                # FastAPI 入口
│       ├── api.py                 # REST API 与流式接口
│       ├── config.py              # 环境变量配置
│       ├── database.py            # SQLAlchemy 连接与初始化
│       ├── models.py              # PostgreSQL 数据模型
│       ├── schemas.py             # API 请求/响应模型
│       └── services/
│           ├── agents/
│           │   ├── supervisor.py     # 多 Agent 调度与回合编排
│           │   ├── base.py           # Agent 基类与消息信封
│           │   ├── context_agent.py  # 状态加载、意图识别、检索
│           │   ├── planner_agent.py  # Plan-and-Solve 计划生成
│           │   ├── executor_agent.py # ReAct 执行 Tools / Skills
│           │   ├── narrator_agent.py # 叙事生成与状态变化提取
│           │   ├── guard_agent.py    # Reflection + guardrails
│           │   └── utils.py          # Agent 共享工具函数
│           ├── tools/                # 原子 Tools
│           │   ├── context_search.py
│           │   ├── rule_check.py
│           │   ├── inventory_lookup.py
│           │   ├── scene_affordance.py
│           │   ├── clue_eligibility.py
│           │   └── memory_recall.py
│           ├── skills/               # 复合 Skills
│           │   ├── investigate/
│           │   ├── move/
│           │   ├── social_interaction/
│           │   ├── use_item/
│           │   ├── danger_and_sanity/
│           │   ├── combat_lite/
│           │   └── wait_or_observe/
│           ├── assistant_agent.py    # 游戏助手 Agent
│           ├── assistant_prompts.py  # 游戏助手 Prompt
│           ├── prompt_config.py      # 节点级 Prompt
│           ├── importer.py           # 资料导入
│           ├── retrieval.py          # Chroma 检索服务
│           ├── chunking.py           # 文档分块
│           ├── content_index.py      # 内容索引与元数据增强
│           ├── llm.py                # LLM 客户端
│           ├── rules.py              # COC 规则裁定
│           ├── dice.py               # 骰点逻辑
│           ├── guardrails.py         # 确定性校验
│           ├── story_state.py        # 故事状态维护
│           ├── summary.py            # 回合摘要与长期记忆
│           ├── inventory.py          # 物品同步
│           ├── characters.py         # 预设人物卡导入
│           ├── debug_events.py       # 调试事件推送
│           └── image_generator.py    # AI 场景图片生成
├── frontend/
│   ├── package.json
│   ├── vite.config.ts
│   └── src/
│       ├── App.tsx                # 主界面（新手引导、助手抽屉、调试面板）
│       ├── api.ts                 # API 封装
│       ├── types.ts               # 类型定义
│       ├── main.tsx
│       └── styles.css
├── data/
│   └── chroma/                    # Chroma 本地向量库
├── 无光的灯塔/                    # 内置剧本、资源、预设人物卡
├── keeper-rulebook/               # 守秘人规则书
├── investigator-handbook/         # 调查员手册
├── .env.example                   # 环境变量示例
└── README.md
```

---

## ⚠️ 当前限制

- **规则系统**：战斗、追逐、魔法仅提供轻量裁定入口，尚未实现完整 COC 规则。
- **角色导入**：预设人物卡 Excel 使用宽松解析；字段识别失败时会回退到默认调查员。
- **防剧透**：当前通过提示词、检索过滤、状态约束和 Reflection 实现多层防护，不等于完整安全审计。
- **模型依赖**：没有有效 LLM 或 Embedding 配置时，导入和真实叙事生成会受限。
- **剧本范围**：当前主要围绕单玩家《无光的灯塔》原型设计。
- **Agent 规划稳定性**：Plan-and-Solve 和 Reflection 依赖模型输出质量，复杂场景下可能出现计划偏离或误判。

---

## 📄 许可证

本项目仅供学习、研究与原型验证使用。
