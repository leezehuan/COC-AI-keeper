# 克苏鲁守秘人轻量版

一个基于 `FastAPI + LangGraph + React/Vite + PostgreSQL + Chroma` 的克苏鲁调查游戏守秘人网页版原型。

## 功能范围

- 单玩家《无光的灯塔》网页版跑团界面。
- PostgreSQL 保存角色、会话、线索、道具、flag 和回合日志。
- Chroma 本地持久化保存剧本、规则书、结构化实体、线索索引和会话记忆向量。
- 第三方 OpenAI 兼容格式调用聊天模型。
- 千问 `text-embedding-v4` 生成 Embedding。
- 工具层执行 D100、技能检定和基础理智检定。
- 导入时会抽取地点、NPC、线索、事件等结构化索引，并在回合中按当前场景、实体、线索和会话记忆增强检索。
- 每回合会生成玩家可见的剧情摘要与长期记忆，并支持从最近会话恢复游戏。

## 目录结构

```text
backend/      FastAPI、LangGraph、数据库和检索服务
frontend/     React/Vite 网页界面
data/chroma/  Chroma 本地向量库目录，首次导入时生成
```

## 环境配置

复制环境变量示例：

```powershell
Copy-Item .env.example .env
```

编辑 `.env`，至少填写：

```env
DATABASE_URL=postgresql+psycopg://postgres:postgres@localhost:5432/coc_lite
LLM_BASE_URL=https://api.example.com/v1
LLM_API_KEY=请填写你的聊天模型密钥
LLM_MODEL=请填写你的聊天模型名称
EMBEDDING_BASE_URL=https://dashscope.aliyuncs.com/compatible-mode/v1
EMBEDDING_API_KEY=请填写你的千问密钥
EMBEDDING_MODEL=text-embedding-v4
```

## 后端启动

安装依赖：

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r backend\requirements.txt
```

启动 API：

```powershell
uvicorn app.main:app --app-dir backend --reload --host 127.0.0.1 --port 8000
```

初始化数据库与导入资料可以在网页点击“初始化/导入”，也可以调用：

```powershell
Invoke-RestMethod -Method Post http://127.0.0.1:8000/api/init
Invoke-RestMethod -Method Post http://127.0.0.1:8000/api/import -ContentType 'application/json' -Body '{"reset_chroma":false,"import_characters":true}'
```

## 前端启动

```powershell
npm install --prefix frontend
npm run dev --prefix frontend
```

浏览器访问：

```text
http://localhost:5173
```

## 使用流程

1. 确保 PostgreSQL 已创建数据库 `coc_lite`。
2. 配置 `.env` 中的数据库、LLM 和千问 Embedding 信息。
3. 启动后端。
4. 启动前端。
5. 在网页点击“初始化/导入”。
6. 选择预设调查员，点击“开始会话”。
7. 输入玩家行动，或点击下一步行动选项。
8. 如需继续旧进度，可在顶部选择最近会话并点击“恢复会话”。

## 当前限制

- 战斗、追逐、魔法只做轻量裁定入口，未实现完整规则系统。
- 角色卡 Excel 使用宽松导入；若字段识别失败，会回退到内置“调查局探员”默认值。
- 防剧透通过提示词、检索上下文和状态约束实现基础防护，不等于完整安全审计。
- 如果没有有效 LLM 或 Embedding 配置，导入和真实叙事生成会受限；部分回合逻辑有回退逻辑以便调试流程。

## 常见问题

### 数据库连接失败

如果点击“初始化/导入”时提示数据库连接失败，请优先检查 `.env` 中的 `DATABASE_URL`：

```env
DATABASE_URL=postgresql+psycopg://postgres:你的本地密码@localhost:5432/coc_lite
```

同时确认 PostgreSQL 服务已启动，并且本地已经创建 `coc_lite` 数据库。
