# Agent 监控系统学习指南

> 这份文档专门解释新增的 Agent 监控系统。  
> 如果你正在学习 Agent 项目，它的价值是：**把一次 Agent 运行从“黑箱”变成可回放、可对照、可删除管理的记录。**

---

## 1. 先抓住一句话

监控系统做三件事：

```text
创建一次 run
  -> 在每个 Agent / Step / Skill / Tool 前后记录输入输出
  -> 写入 PostgreSQL，同时推送给 5174 端口的监控页
```

你可以把它理解成“给 Agent 调用链装上行车记录仪”。

主游戏仍然走原来的链路：

```text
前端玩家行动
  -> /coc/api/sessions/{id}/actions/stream
  -> KeeperSupervisor.run_turn()
  -> ContextAgent / PlannerAgent / ExecutorAgent / NarratorAgent / GuardAgent
  -> 返回叙事
```

监控系统只是在旁边多做一件事：

```text
同一条链路旁路记录
  -> agent_trace_runs
  -> agent_trace_records
  -> /coc/api/monitor/events/stream
  -> frontend/src/monitor.tsx
```

---

## 2. 三张表怎么理解

### 2.1 `agent_trace_runs`

`run` 表示“一次完整请求”。

例如：

- 玩家发送一次行动：创建 1 条 `action` run
- 游戏助手回答一次问题：创建 1 条 `assistant` run

核心字段：

| 字段 | 含义 |
|---|---|
| `id` | 这次运行的 ID |
| `session_id` | 所属游戏会话，可为空 |
| `source` | 来源：`action` 或 `assistant` |
| `status` | `running` / `success` / `error` |
| `metadata` | 运行级附加信息，如是否流式、用户输入 |
| `started_at` / `ended_at` | 开始和结束时间 |

### 2.2 `agent_trace_records`

`record` 表示“一次运行里的一个步骤”。

例如一次玩家行动可能包含：

```text
ContextAgent.run
ContextAgent.parse_intent
ContextAgent.retrieve_context
PlannerAgent.run
PlannerAgent.generate_plan
ExecutorAgent.run
SkillTool.ContextSearchTool
SkillTool.RuleCheckTool
NarratorAgent.generate_narration
GuardAgent.reflection_review
KeeperSupervisor.commit_state
```

核心字段：

| 字段 | 含义 |
|---|---|
| `run_id` | 属于哪一次 run |
| `sequence` | 在 run 内的顺序号 |
| `agent_name` | 哪个 Agent 或组件 |
| `step_name` | 具体步骤名 |
| `phase` | 所属阶段，如 `context`、`plan`、`tool` |
| `input_payload` | 步骤输入 |
| `output_payload` | 步骤输出 |
| `duration_ms` | 耗时 |
| `error` | 异常信息 |

### 2.3 `agent_trace_settings`

目前只有一个全局设置：

| 字段 | 含义 |
|---|---|
| `max_records` | 最多保存多少条 step 记录，默认 5000 |

超过上限时，系统会删除最旧的 `agent_trace_records`，并清理没有步骤记录的已结束 run。

---

## 3. 后端代码从哪里读

建议按这个顺序读：

```text
backend/app/api.py
  -> create_trace_run()
  -> KeeperSupervisor.run_turn(..., trace_recorder=...)
  -> 各 Agent 中的 trace_recorder.step(...)
  -> backend/app/services/agent_monitor.py
  -> frontend/src/monitor.tsx
```

### 3.1 `backend/app/api.py`

这里负责创建和结束 run。

玩家行动接口中大致是：

```python
trace_recorder = create_trace_run(...)
try:
    result = get_agent().run_turn(..., trace_recorder=trace_recorder)
    finish_trace_run(trace_recorder, "success")
except Exception as exc:
    finish_trace_run(trace_recorder, "error", str(exc))
    raise
```

这段代码的学习重点：

- `create_trace_run()` 创建运行记录
- `trace_recorder` 被一路传给 Agent
- 请求成功就标记 `success`
- 请求失败就标记 `error`
- 监控失败不会阻断主业务

### 3.2 `backend/app/services/agent_monitor.py`

这是监控系统的核心文件。

重点函数：

| 函数 | 作用 |
|---|---|
| `AgentTraceRecorder.step()` | 用上下文管理器记录一个步骤的输入、输出、耗时和异常 |
| `record_trace_step()` | 真正写入 `agent_trace_records` |
| `safe_serialize()` | 把 ORM、Session、函数等复杂对象转换成可存 JSON |
| `enforce_retention()` | 执行全局记录条数上限 |
| `monitor_event_stream()` | 给监控页提供实时 NDJSON 事件 |

`step()` 的使用方式是这个系统最值得学的点：

```python
with trace_recorder.step(
    agent_name="PlannerAgent",
    step_name="generate_plan",
    phase="agent_step",
    input_payload={...},
) as trace_step:
    generated = self.context.llm.chat_json(prompt, fallback=fallback)
    trace_step["output"] = generated
```

这个模式的好处：

- 进入 `with` 时记录开始时间
- 正常退出时记录输出和耗时
- 抛异常时记录错误，再把异常继续抛给原业务

### 3.3 各 Agent 文件

每个 Agent 现在都有两层记录：

1. `run` 级别：记录整个 Agent 的输入输出
2. 内部步骤级别：记录关键 LLM、检索、Reflection、Tool 等步骤

例如：

- `ContextAgent.run`：整个上下文 Agent 的输入输出
- `ContextAgent.parse_intent`：意图解析 prompt、fallback、LLM JSON
- `ContextAgent.retrieve_context`：检索 query 和五类检索结果
- `PlannerAgent.generate_plan`：计划生成 prompt 和计划 JSON
- `NarratorAgent.generate_narration`：叙事生成 prompt 和输出
- `GuardAgent.reflection_review`：Reflection 自检 prompt 和报告

---

## 4. 前端监控页怎么工作

前端入口：

```text
frontend/monitor.html
  -> frontend/src/monitor.tsx
  -> frontend/src/api.ts
  -> /coc/api/monitor/*
```

启动：

```powershell
cd frontend
npm run dev:monitor
```

默认端口：

```text
http://127.0.0.1:5174/coc/monitor.html
```

页面分三栏：

| 区域 | 作用 |
|---|---|
| Runs | 一次玩家行动或助手请求 |
| Records | 某次 run 下的步骤记录 |
| Detail | 展示单条步骤的完整输入输出 JSON |

它同时使用两种数据来源：

1. REST 查询历史记录：`/monitor/runs`、`/monitor/records`
2. NDJSON 实时流：`/monitor/events/stream`

---

## 5. 为什么要 `safe_serialize`

Agent 内部有很多对象不能直接写 JSON：

- SQLAlchemy `Session`
- ORM 对象，如 `GameSession`、`Character`
- 回调函数，如 `debug_emit`
- LLM client、retrieval service
- 很长的 prompt 或检索结果
- 可能循环引用的 Python 对象

所以监控系统不会直接 `json.dumps(payload)`，而是先走 `safe_serialize()`。

它会做几件事：

- 基本类型原样保存
- ORM 对象只保存列字段
- 函数和连接对象标记为 omitted
- 长字符串截断
- 容器深度和数量限制
- 循环引用标记为 `__cycle__`

这就是为什么监控记录既尽量完整，又不会轻易把系统写崩。

---

## 6. 和旧调试面板的区别

主游戏页已有“实时调试窗口”，它适合看运行状态：

```text
ContextAgent 开始
PlannerAgent 完成
Tool 返回 3 条结果
GuardAgent 通过
```

新增监控页适合看完整数据：

```text
这个 Agent 收到了什么 payload
这个 prompt 是什么
LLM 返回了什么 JSON
Tool 输入参数是什么
最终写库前 state_delta 是什么
```

简单说：

| 工具 | 适合 |
|---|---|
| 主页面调试窗口 | 观察当前运行状态 |
| 5174 监控页 | 学习、排错、回放输入输出 |

---

## 7. 学习时建议怎么用

建议你按这个节奏练习：

1. 启动后端和主前端。
2. 启动监控页。
3. 在主前端发送一句简单行动。
4. 去监控页选最新的 `action` run。
5. 按顺序打开以下记录：

```text
ContextAgent.parse_intent
PlannerAgent.generate_plan
ExecutorAgent.run
SkillTool.*
NarratorAgent.generate_narration
GuardAgent.reflection_review
KeeperSupervisor.commit_state
```

读的时候问自己三个问题：

- 这一步的输入从哪里来？
- 这一步的输出给谁用？
- 这一步如果失败，会不会影响主流程？

能回答这三个问题，就基本读懂了 Agent 链路。

---

## 8. 新增或调试 Agent 时怎么接入监控

如果你以后新增一个 Agent，最小接入方式如下：

```python
trace_recorder = payload.get("trace_recorder")

with trace_recorder.step(
    agent_name=self.name,
    step_name="run",
    phase="your_phase",
    input_payload=payload,
) as trace_step:
    result = self._run_impl(...)
    trace_step["output"] = result
    return result
```

如果你只想记录某个内部步骤：

```python
with trace_recorder.step(
    agent_name=self.name,
    step_name="call_llm",
    phase="agent_step",
    input_payload={"prompt": prompt},
) as trace_step:
    output = self.context.llm.chat_json(prompt, fallback=fallback)
    trace_step["output"] = output
```

注意事项：

- 不要手动捕获后吞掉异常，除非业务本来就需要兜底。
- 大对象可以直接放进 `input_payload`，系统会自动安全序列化。
- 如果某个字段特别敏感，可以在传入前手动替换成摘要。

---

## 9. 常见问题

### 为什么监控页能看到 keeper-only 内容？

因为它是开发者工具，不是玩家工具。  
学习 Agent 时，完整上下文比隐藏内容更有价值。

### 监控写库失败会不会导致游戏失败？

设计上不会。  
`agent_monitor.py` 的写入是 best-effort，失败时静默跳过，不影响主流程。

### 为什么有些字段显示 `__omitted__`？

表示这个对象不适合写 JSON，比如数据库连接、函数、客户端实例。

### 为什么有些字段显示 `__truncated__`？

表示字段太长或太深，系统做了截断，防止数据库和浏览器被超大 JSON 拖垮。

### 修改了表结构后为什么页面报错？

项目目前没有 Alembic，需要执行现有初始化流程，让 `create_all` 创建新表。

