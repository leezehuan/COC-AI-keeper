# 克苏鲁守秘人轻量版 🕯️

> 基于 `FastAPI + LangGraph + React/Vite + PostgreSQL + Chroma`，以 `KeeperAgent` 为核心的《克苏鲁的呼唤》调查游戏 AI 守秘人网页版原型

---

# 使用必看

请先确保本机已安装并启动 **PostgreSQL**，并创建数据库 `coc_lite`。首次运行前需要复制 `.env.example` 为 `.env`，填写数据库、聊天模型和 Embedding 配置；启动后可在网页中点击“初始化/导入”完成数据库建表、剧本/规则书向量化和预设调查员导入。

如果你是 **LangGraph** 或 **Web 开发** 初学者，建议先阅读：[coc-lite 初学者学习指南](./docs/初学者学习指南.md)。

---

## 📖 项目简介

**克苏鲁守秘人轻量版**是一个面向单玩家跑团场景的 AI 守秘人原型系统。当前以内置剧本《无光的灯塔》为核心，提供网页化调查体验，并通过 `KeeperAgent` 编排守秘人回合逻辑。

系统的核心是后端 `KeeperAgent`：它使用 LangGraph `StateGraph` 将一次玩家行动拆解为“读档、理解意图、检索剧本与规则、裁定与骰点、生成叙事、校验状态、防剧透、提交持久化”的可追踪流程，让玩家可以在浏览器中进行连续调查。

核心能力包括：

- **单玩家跑团界面**：React/Vite 前端提供角色选择、会话创建、旧档恢复、玩家行动输入和下一步行动选项。
- **KeeperAgent 智能守秘人**：后端核心 Agent 串联意图解析、RAG 检索、规则裁定、骰点工具、叙事生成、状态校验、防剧透与记忆写入。
- **RAG 增强检索**：使用 Chroma 存储剧本、规则书、结构化实体、线索索引和会话记忆向量。
- **数据库持久化**：PostgreSQL 保存角色、会话、线索、道具、flag 与回合日志。
- **规则工具层**：内置 D100、技能检定、基础理智检定和轻量行动裁定。
- **流式响应**：前端通过 `/actions/stream` 接收后端 NDJSON 流式叙事输出。
- **防剧透与状态约束**：通过提示词、检索过滤和状态变更校验降低玩家侧剧透风险。

---

## ✨ 核心特性

| 特性 | 说明 |
|------|------|
| **后端框架** | FastAPI，提供初始化、导入、角色、会话、行动提交等 API |
| **Agent 编排** | `KeeperAgent` 基于 LangGraph `StateGraph` 编排守秘人回合，是玩家行动后的核心决策链路 |
| **前端** | React + Vite + TypeScript 网页界面 |
| **关系数据库** | PostgreSQL，保存游戏会话和结构化状态 |
| **向量数据库** | Chroma，本地持久化剧本、规则和记忆索引 |
| **聊天模型** | 第三方 OpenAI 兼容接口，通过 `.env` 配置 |
| **Embedding** | 千问 `text-embedding-v4`，默认 1024 维 |
| **内置剧本** | 《无光的灯塔》及其预设人物卡 |
| **规则资料** | 守秘人规则书、调查员手册等本地资料 |
| **流式输出** | 支持玩家行动后的叙事分块返回 |

---

## 🏗 系统架构

```text
┌──────────────────────────────────────────────┐
│              React/Vite 前端                  │
│  - 初始化/导入  - 角色选择  - 会话恢复         │
│  - 玩家行动输入 - 流式叙事  - 状态展示         │
└──────────────────────┬───────────────────────┘
                       │ HTTP / NDJSON Stream
┌──────────────────────▼───────────────────────┐
│              FastAPI 后端                     │
│  /coc/api/init          初始化数据库           │
│  /coc/api/import        导入剧本/规则/角色      │
│  /coc/api/characters    查询预设调查员          │
│  /coc/api/sessions      创建/查询/恢复会话       │
│  /coc/api/sessions/{id}/actions/stream         │
└──────────────────────┬───────────────────────┘
                       │
┌──────────────────────▼───────────────────────┐
│        KeeperAgent：AI 守秘人核心             │
│  LangGraph StateGraph 编排每个玩家行动回合     │
│  - 意图识别 / 模糊行动澄清                    │
│  - RAG 检索剧本、规则、实体、线索、长期记忆     │
│  - 规则裁定、D100、技能检定、理智检定          │
│  - 叙事生成、状态变更、防剧透校验、持久化       │
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

## 🧠 KeeperAgent 设计重点

`KeeperAgent` 是本项目最核心的后端模块，位于 `backend/app/services/agent.py`。前端提交玩家行动后，FastAPI 的 `/coc/api/sessions/{session_id}/actions` 或 `/coc/api/sessions/{session_id}/actions/stream` 会调用 `get_agent().run_turn(...)`，由 Agent 完成一次完整守秘人回合。

### Agent 的职责边界

| 职责 | 说明 |
|------|------|
| **会话状态读取** | 从 PostgreSQL 加载角色、会话、线索、道具、Story Flag 和回合日志 |
| **玩家意图理解** | 通过 LLM JSON 输出与启发式规则识别行动类型、目标、技能和是否需要澄清 |
| **检索增强上下文** | 从 Chroma 检索剧本、规则书、结构化实体、线索索引和会话长期记忆 |
| **规则与骰点裁定** | 调用规则工具执行行动难度判断、D100、技能检定和理智检定 |
| **叙事生成** | 将角色状态、场景上下文、裁定结果和检索内容交给 LLM 生成玩家可见回应 |
| **状态变更控制** | 生成并校验地点、场景、时间、危险等级、线索和物品变化 |
| **防剧透处理** | 过滤不应直接暴露给玩家的主持人秘密，并清洗下一步行动选项 |
| **持久化与记忆** | 写入回合日志、更新会话状态、同步线索/道具，并将回合摘要写入长期记忆向量库 |

### Agent 状态流

`KeeperState` 是 Agent 节点之间传递的状态容器，关键字段包括：

- **输入上下文**：`db`、`session_id`、`player_input`、`session`、`character`
- **理解与检索**：`intent`、`scenario_context`、`rule_context`、`entity_context`、`clue_context`、`memory_context`
- **裁定结果**：`adjudication`、`dice_results`、`skill_checks`、`sanity_checks`、`resolution`
- **输出与状态**：`narration`、`options`、`state_delta`、`validation_report`、`leak_report`、`discovered_clues`
- **审计与记忆**：`audit`、`summary`、`story_state`、`needs_clarification`

### Agent 协作模块

| 模块 | Agent 中的作用 |
|------|----------------|
| `llm.py` | 调用 OpenAI 兼容聊天模型，生成意图 JSON、守秘人叙事和摘要 |
| `retrieval.py` | 查询和写入 Chroma 集合，为 RAG 和长期记忆提供数据 |
| `rules.py` | 提供轻量 COC 裁定、D100、技能检定和理智检定 |
| `guardrails.py` | 校验状态变更、识别偏离剧情、清洗剧透文本与选项 |
| `story_state.py` | 构造并应用结构化剧情状态变化 |
| `summary.py` | 生成回合摘要并沉淀为会话记忆 |
| `inventory.py` | 根据 `state_delta` 同步会话物品变化 |

---

## 📂 目录结构

```text
coc-lite/
├── backend/
│   ├── requirements.txt           # 后端 Python 依赖
│   └── app/
│       ├── main.py                # FastAPI 应用入口、CORS、静态资源挂载
│       ├── api.py                 # REST API 与流式行动接口
│       ├── config.py              # 环境变量配置
│       ├── database.py            # SQLAlchemy 数据库连接与初始化
│       ├── models.py              # PostgreSQL 数据模型
│       ├── schemas.py             # API 请求/响应模型
│       └── services/
│           ├── agent.py           # KeeperAgent 核心 Agent 与 LangGraph 状态图节点
│           ├── importer.py        # 剧本、规则书、角色导入
│           ├── retrieval.py       # Chroma 检索服务
│           ├── llm.py             # OpenAI 兼容 LLM 客户端
│           ├── rules.py           # D100、技能、理智等规则裁定
│           ├── guardrails.py      # 防剧透与状态变更约束
│           ├── story_state.py     # 故事状态维护
│           ├── summary.py         # 回合摘要与长期记忆
│           └── characters.py      # 预设人物卡导入与属性补全
├── frontend/
│   ├── package.json               # 前端依赖与脚本
│   └── src/
│       ├── App.tsx                # 网页主界面
│       ├── api.ts                 # 后端 API 调用封装
│       ├── types.ts               # 前端类型定义
│       └── styles.css             # 页面样式
├── data/
│   └── chroma/                    # Chroma 本地向量库目录，导入后生成
├── 无光的灯塔/                    # 内置剧本、资源和预设人物卡
├── keeper-rulebook/               # 守秘人规则书
├── investigator-handbook/         # 调查员手册
├── .env.example                   # 环境变量示例
└── README.md
```

---

## 📦 环境依赖

### 基础环境

| 环境 | 说明 |
|------|------|
| **Python** | 建议使用 Python 3.10+ |
| **Node.js** | 用于运行 React/Vite 前端 |
| **PostgreSQL** | 需要本地或远程可访问的 PostgreSQL 数据库 |

### 后端主要依赖

| 包名 | 用途 |
|------|------|
| `fastapi` | 后端 Web API 框架 |
| `uvicorn` | ASGI 服务 |
| `sqlalchemy` | ORM 与数据库操作 |
| `psycopg` | PostgreSQL 驱动 |
| `chromadb` | 本地向量数据库 |
| `openai` | OpenAI 兼容聊天模型与 Embedding 调用 |
| `langgraph` | 守秘人 Agent 状态图编排 |
| `langchain-core` | LangChain 核心抽象 |
| `pandas` / `openpyxl` | 预设角色 Excel 数据读取 |

### 前端主要依赖

| 包名 | 用途 |
|------|------|
| `react` | 前端 UI 框架 |
| `react-dom` | React DOM 渲染 |
| `vite` | 前端开发与构建工具 |
| `typescript` | 类型检查与开发体验 |

---

## ⚙️ 配置说明

### 1. 复制环境变量文件

```powershell
Copy-Item .env.example .env
```

### 2. 配置 PostgreSQL

确保 PostgreSQL 中已创建数据库 `coc_lite`，然后在 `.env` 中配置连接地址：

```env
DATABASE_URL=postgresql+psycopg://postgres:postgres@localhost:5432/coc_lite
```

如果你的本地 PostgreSQL 密码不是 `postgres`，请将连接串中的密码替换为实际值。

### 3. 配置聊天模型

本项目通过 OpenAI 兼容格式调用第三方聊天模型：

```env
LLM_BASE_URL=https://api.example.com/v1
LLM_API_KEY=replace-with-your-openai-compatible-key
LLM_MODEL=replace-with-your-chat-model
LLM_TEMPERATURE=0.7
```

### 4. 配置 Embedding

默认使用千问 `text-embedding-v4`：

```env
EMBEDDING_BASE_URL=https://dashscope.aliyuncs.com/compatible-mode/v1
EMBEDDING_API_KEY=replace-with-your-qwen-api-key
EMBEDDING_MODEL=text-embedding-v4
EMBEDDING_DIMENSIONS=1024
```

### 5. 配置本地资料路径

`.env.example` 已包含默认路径，通常无需修改：

```env
CHROMA_PATH=./data/chroma
SCENARIO_PATH=./无光的灯塔/无光的灯塔/full.md
RULEBOOK_PATHS=./keeper-rulebook/主持人规则书.md,./investigator-handbook/full.md
CHARACTER_DIR=./无光的灯塔/预设人物卡
ASSETS_DIR=./无光的灯塔
```

---

## 🚀 快速开始

### 1. 安装后端依赖

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r backend\requirements.txt
```

### 2. 配置 `.env`

```powershell
Copy-Item .env.example .env
```

编辑 `.env`，填写 PostgreSQL、LLM 和 Embedding 相关配置。

### 3. 启动后端

```powershell
uvicorn app.main:app --app-dir backend --reload --host 127.0.0.1 --port 8000
```

后端健康检查地址：

```text
http://127.0.0.1:8000/coc/api/health
```

### 4. 安装并启动前端

```powershell
npm install --prefix frontend
npm run dev --prefix frontend
```

浏览器访问：

```text
http://localhost:5173
```

### 5. 初始化与导入资料

进入网页后点击“初始化/导入”，或手动调用：

```powershell
Invoke-RestMethod -Method Post http://127.0.0.1:8000/coc/api/init
Invoke-RestMethod -Method Post http://127.0.0.1:8000/coc/api/import -ContentType 'application/json' -Body '{"reset_chroma":false,"import_characters":true}'
```

---

## 💬 使用流程

1. 确认 PostgreSQL 已启动，并已创建 `coc_lite` 数据库。
2. 配置 `.env` 中的数据库、LLM 和 Embedding 信息。
3. 启动 FastAPI 后端。
4. 启动 React/Vite 前端。
5. 在网页点击“初始化/导入”。
6. 选择一个预设调查员。
7. 点击“开始会话”进入《无光的灯塔》。
8. 输入玩家行动，或点击系统给出的下一步行动选项。
9. 如需继续旧进度，可在顶部选择最近会话并点击“恢复会话”。

示例行动：

```text
我观察附近海面和灯塔方向。
我检查船上的装备。
我想聆听屋内是否有动静。
我尝试撬开这扇门。
我查看当前角色状态。
```

---

## 🛠 API 概览

| 接口 | 方法 | 说明 |
|------|------|------|
| `/coc/api/health` | GET | 健康检查 |
| `/coc/api/init` | POST | 初始化数据库表 |
| `/coc/api/import` | POST | 导入剧本、规则书、实体、线索和角色 |
| `/coc/api/characters` | GET | 获取预设调查员列表 |
| `/coc/api/sessions` | GET | 获取最近会话 |
| `/coc/api/sessions` | POST | 创建新会话 |
| `/coc/api/sessions/{session_id}` | GET | 获取指定会话详情 |
| `/coc/api/sessions/{session_id}` | DELETE | 删除指定会话及其记忆分块 |
| `/coc/api/sessions/{session_id}/actions` | POST | 提交玩家行动，调用 `KeeperAgent` 并返回完整响应 |
| `/coc/api/sessions/{session_id}/actions/stream` | POST | 提交流式玩家行动，调用 `KeeperAgent` 并返回 NDJSON 事件 |

---

## 🔄 守秘人回合流程

每次玩家提交行动后，`KeeperAgent` 会按以下节点处理：

```text
load_state
  └─ 加载会话、角色、线索、道具、flag 和故事状态

parse_intent
  ├─ 识别玩家意图、目标、可能技能和是否需要澄清
  ├─ 模糊行动进入 clarify_action
  └─ 明确行动继续 retrieve_context

clarify_action
  └─ 返回澄清问题、候选行动选项，并直接进入 commit_state

retrieve_context
  ├─ 检索剧本分块 scenario_chunks
  ├─ 检索规则分块 rule_chunks
  ├─ 检索结构化实体 scenario_entities
  ├─ 检索线索索引 clue_index
  └─ 检索当前会话长期记忆 session_memory_chunks

adjudicate / roll_tools / resolve_action
  ├─ 执行轻量规则裁定、D100、技能检定和理智检定
  └─ 结合剧情偏离检测形成行动解决结果

generate_response
  └─ 基于检索上下文、裁定结果和角色状态生成玩家可见叙事

generate_state_delta / validate_state_delta
  └─ 生成并校验地点、场景、时间、线索、道具和危险等级变化

secret_leak_check
  └─ 检查输出是否包含不应直接暴露给玩家的秘密信息

generate_next_options
  └─ 生成下一步行动建议

commit_state
  └─ 写入数据库、回合日志、摘要和会话长期记忆，然后结束本回合
```

---

## 📚 知识库与导入内容

导入流程会将本地资料拆分、索引并写入 Chroma：

| 集合 | 内容 |
|------|------|
| `scenario_chunks` | 《无光的灯塔》剧本文本分块 |
| `rule_chunks` | 守秘人规则书与调查员手册分块 |
| `scenario_entities` | 地点、NPC、事件等结构化实体 |
| `clue_index` | 线索索引 |
| `session_memory_chunks` | 每个游戏会话的长期记忆 |

如需重新生成向量库，可在导入时设置 `reset_chroma` 为 `true`，或通过网页中的重置导入入口执行。

---

## 🧩 数据库内容

PostgreSQL 主要保存以下结构化数据：

| 表/模型 | 说明 |
|---------|------|
| `scenarios` | 剧本基本信息 |
| `characters` | 调查员属性、技能、物品和背景 |
| `sessions` | 游戏会话、当前位置、场景、时间和故事状态 |
| `turn_logs` | 玩家行动、检索上下文、骰点结果、守秘人回应和状态变化 |
| `clues` | 当前会话已发现线索 |
| `inventory_items` | 当前会话道具 |
| `story_flags` | 当前会话剧情 flag |

---

## ⚠️ 当前限制

- **规则系统**：战斗、追逐、魔法仅提供轻量裁定入口，尚未实现完整 COC 规则。
- **角色导入**：预设人物卡 Excel 使用宽松解析；字段识别失败时会回退到默认调查员。
- **防剧透**：当前通过提示词、检索过滤和状态约束实现基础防护，不等于完整安全审计。
- **模型依赖**：没有有效 LLM 或 Embedding 配置时，导入和真实叙事生成会受限。
- **剧本范围**：当前主要围绕单玩家《无光的灯塔》原型设计。

---

## ❓ 常见问题

### 数据库连接失败

如果点击“初始化/导入”时提示数据库连接失败，请检查 `.env` 中的 `DATABASE_URL`：

```env
DATABASE_URL=postgresql+psycopg://postgres:你的本地密码@localhost:5432/coc_lite
```

同时确认：

- **PostgreSQL 服务**：本机 PostgreSQL 已启动。
- **数据库名称**：已创建 `coc_lite` 数据库。
- **用户名/密码**：连接串中的账号密码与本地配置一致。

### 导入失败或向量库不可用

请检查：

- **Embedding Key**：`EMBEDDING_API_KEY` 是否有效。
- **Embedding 模型**：`EMBEDDING_MODEL` 是否为可用模型。
- **资料路径**：`SCENARIO_PATH`、`RULEBOOK_PATHS`、`CHARACTER_DIR` 是否指向真实文件。
- **Chroma 目录**：`CHROMA_PATH` 是否有写入权限。

### 前端无法请求后端

请确认：

- **后端地址**：FastAPI 已运行在 `http://127.0.0.1:8000`。
- **前端地址**：Vite 已运行在 `http://localhost:5173`。
- **CORS 配置**：`.env` 中 `CORS_ORIGINS` 包含前端访问地址。

### 模型服务调用失败

请检查：

- **LLM_BASE_URL**：是否为 OpenAI 兼容接口地址。
- **LLM_API_KEY**：是否有效且未过期。
- **LLM_MODEL**：模型名称是否填写正确。
- **额度限制**：服务商账号是否有可用额度。

---

## 🔮 后续优化方向

- 完善战斗、追逐、魔法和更复杂的 COC 规则系统。
- 增强多用户登录、权限隔离和存档管理。
- 支持更多剧本导入格式与自动结构化标注。
- 加强防剧透审计与可解释的守秘人裁定记录。
- 增加管理端，用于查看向量库、回合日志、状态变化和调试信息。

---

## 📄 许可证

本项目仅供学习、研究与原型验证使用。
