# coc-lite Tools 扩展指南

> 面向 Agent 开发学生：  
> 这份文档教你如何给 `backend/app/services/tools/` 新增一个原子 Tool。

---

## 1. 先理解 Tool 是什么

在本项目里，Tool 是 Agent 能调用的最小功能单元。

可以先这样记：

```text
Tool = 只做一件具体事的函数
Skill = 组合多个 Tool 的行动模板
ExecutorAgent = 按计划运行 Skill，再收集 Tool 的观察结果
```

比如：

- `ContextSearchTool`：查 Chroma 检索结果
- `RuleCheckTool`：执行 CoC 技能检定和理智检定
- `InventoryLookupTool`：查询物品栏
- `SceneAffordanceTool`：查询当前场景可交互对象
- `ClueEligibilityTool`：判断某个线索是否有资格被发现
- `MemoryRecallTool`：检索当前会话记忆

Tool 的设计原则是：

> 尽量只读、可审计、返回结构化观察，不直接改数据库。

---

## 2. Tool 在回合链路里的位置

一次玩家行动进入后端后，大致会经过：

```text
PlannerAgent
  -> 生成 allowed_tools 白名单
ExecutorAgent
  -> 选择 Skill
Skill
  -> 在 allowed_tools 允许的范围内调用 Tool
Tool
  -> 返回 ToolObservation
ExecutorAgent
  -> 汇总 tool_observations
NarratorAgent / GuardAgent
  -> 使用观察结果生成叙事和校验状态
```

注意这里有一个关键安全设计：

```text
LLM 不能随便调用不存在的 Tool。
PlannerAgent 会用白名单过滤 allowed_tools。
```

所以新增 Tool 时，除了写代码，还要把它加入白名单。

---

## 3. 一个 Tool 文件通常长什么样

推荐先打开这两个文件对照看：

- [context_search.py](D:/Project/coc-lite/backend/app/services/tools/context_search.py)
- [rule_check.py](D:/Project/coc-lite/backend/app/services/tools/rule_check.py)

一个标准 Tool 通常包含三部分：

```python
TOOL_NAME = "SomeTool"

def tool_spec() -> ToolSpec:
    return ToolSpec(...)

def run_some_tool(...) -> ToolObservation:
    return ToolObservation(...)
```

### `TOOL_NAME`

Tool 的唯一名称。  
这个名字会进入计划白名单、调试日志和 ToolObservation。

命名建议：

```text
XxxTool
```

例如：

```python
TOOL_NAME = "WeatherSenseTool"
```

### `tool_spec()`

描述 Tool 的能力、输入和限制。

它主要服务两个对象：

1. 给 PlannerAgent / LLM 看：这个 Tool 能干什么
2. 给开发者看：这个 Tool 有什么边界

### `run_xxx_tool()`

真正执行 Tool 的函数。

返回值必须是 `ToolObservation`，这样 ExecutorAgent 才能统一收集观察结果。

---

## 4. `ToolObservation` 怎么理解

定义在：

[backend/app/services/tools/base.py](D:/Project/coc-lite/backend/app/services/tools/base.py)

核心字段：

| 字段 | 含义 |
|---|---|
| `tool` | Tool 名称 |
| `input` | 本次调用输入 |
| `output` | 本次调用结果 |
| `success` | 是否成功 |
| `error` | 错误信息 |

你可以把它理解成 Tool 的“实验记录”：

```python
ToolObservation(
    tool="SceneAffordanceTool",
    input={"location": "北岸码头"},
    output={"affordances": [...], "available_locations": [...]},
    success=True,
)
```

后续调试面板、`react_trace`、`tool_observations` 都会依赖这些结构化结果。

---

## 5. 新增 Tool 的标准步骤

下面用一个假想 Tool 举例：

```text
WeatherSenseTool
作用：根据当前地点和剧情状态，返回天气、光照、声音等环境感知信息。
```

### 第 1 步：新增文件

路径示例：

```text
backend/app/services/tools/weather_sense.py
```

示例骨架：

```python
from __future__ import annotations

from typing import Any

from app.services.tools.base import ToolObservation, ToolSpec


TOOL_NAME = "WeatherSenseTool"


def tool_spec() -> ToolSpec:
    return ToolSpec(
        name=TOOL_NAME,
        description="读取当前场景的天气、光照、声音和气味，只返回环境观察，不修改状态。",
        input_schema={
            "story_state": "当前剧情状态",
            "current_location": "当前地点",
        },
        constraints=[
            "只读剧情状态。",
            "不能直接修改地点、时间或危险等级。",
            "不能输出 keeper_only 秘密信息。",
        ],
    )


def run_weather_sense(*, story_state: dict[str, Any], current_location: str) -> ToolObservation:
    scene = story_state.get("场景", {}) if isinstance(story_state.get("场景"), dict) else {}
    output = {
        "current_location": current_location,
        "light": scene.get("光照情况", "未知"),
        "sounds_and_smells": scene.get("声音和气味", []),
        "visible_anomalies": scene.get("可见异常", []),
    }
    return ToolObservation(
        tool=TOOL_NAME,
        input={"current_location": current_location},
        output=output,
    )
```

---

### 第 2 步：在 Tools 包入口导出

文件：

[backend/app/services/tools/__init__.py](D:/Project/coc-lite/backend/app/services/tools/__init__.py)

需要增加 import 和 `__all__`：

```python
from app.services.tools.weather_sense import run_weather_sense
```

```python
__all__ = [
    ...
    "run_weather_sense",
]
```

这一步不是所有内部调用都绝对依赖，但它能让包导出更完整，也方便后续模块统一引用。

---

### 第 3 步：加入 Tool 白名单

文件：

[backend/app/services/agents/utils.py](D:/Project/coc-lite/backend/app/services/agents/utils.py)

找到：

```python
def available_tool_names() -> list[str]:
```

加入：

```python
"WeatherSenseTool",
```

这是非常关键的一步。  
如果不加，PlannerAgent 生成计划后会把它过滤掉。

---

### 第 4 步：让 Skill 能调用它

大部分 Skill 走通用执行器：

[backend/app/services/skills/common.py](D:/Project/coc-lite/backend/app/services/skills/common.py)

你需要做三件事：

1. import 新 Tool：

```python
from app.services.tools.weather_sense import run_weather_sense
```

2. 在 `run_generic_skill()` 里加入调用逻辑：

```python
if "WeatherSenseTool" in allowed_tools:
    run_tool_with_debug(
        debug_emit,
        observations,
        "WeatherSenseTool",
        "开始读取环境感知信息。",
        lambda: run_weather_sense(
            story_state=state.get("story_state", {}),
            current_location=getattr(session, "current_location", ""),
        ),
        start_metadata={"location": getattr(session, "current_location", "")},
    )
```

3. 决定放在哪个顺序。

推荐规则：

- 纯上下文类 Tool 放在 RuleCheckTool 之前
- 会影响裁定依据的 Tool 放在叙事生成之前收集
- 不要让 Tool 直接写数据库

---

### 第 5 步：把 Tool 加入某个 Skill 的 `allowed_tools`

例如想让 `MoveSkill` 使用它，修改：

[backend/app/services/skills/move/skill.py](D:/Project/coc-lite/backend/app/services/skills/move/skill.py)

```python
SPEC = SkillSpec(
    name="MoveSkill",
    action_types=["移动"],
    allowed_tools=["SceneAffordanceTool", "ContextSearchTool", "WeatherSenseTool"],
    ...
)
```

`PlannerAgent` 生成计划时，会根据 Skill 的 `allowed_tools` 自动补全 Tool 白名单。

---

## 6. 新增 Tool 时最容易漏的地方

| 易漏点 | 后果 |
|---|---|
| 没加 `available_tool_names()` | PlannerAgent 会把 Tool 过滤掉 |
| 没在 `run_generic_skill()` 调用 | 计划里有 Tool，但实际不会执行 |
| 没加到 SkillSpec.allowed_tools | 默认计划不会选择这个 Tool |
| Tool 直接改数据库 | 状态分散，后续难以审计 |
| output 里塞自由文本太多 | Narrator/Guard 难以稳定消费 |
| 忘记防剧透约束 | keeper_only 内容可能进入玩家叙事 |

---

## 7. Tool 的设计建议

### 保持“原子”

一个 Tool 最好只做一件事。

好例子：

```text
InventoryLookupTool：只查物品栏
RuleCheckTool：只做规则检定
```

不太好的例子：

```text
InvestigateAndCreateClueAndMovePlayerTool
```

这种 Tool 会同时负责调查、创建线索、移动角色，太难审计。

---

### 默认只读

本项目里，状态变化集中在：

```text
NarratorAgent 生成 state_delta
GuardAgent 校验 state_delta
KeeperSupervisor._commit_state() 落库
```

所以 Tool 通常只返回观察结果，不直接写数据库。

这样做的好处是：

- 调试更容易
- 防剧透更可控
- 回合日志更清楚
- LLM 不容易绕过状态校验

---

### output 尽量结构化

推荐：

```python
output = {
    "candidates": [
        {"name": "泥泞脚印", "eligible": True, "reason": "目标匹配"}
    ]
}
```

不推荐：

```python
output = {
    "text": "这里有一堆东西，可能还有某个秘密线索，总之自己看。"
}
```

结构化输出更容易被后续 Agent 稳定使用。

---

## 8. 推荐练习

### 练习 1：读懂现有 Tool

按这个顺序看：

1. [base.py](D:/Project/coc-lite/backend/app/services/tools/base.py)
2. [context_search.py](D:/Project/coc-lite/backend/app/services/tools/context_search.py)
3. [clue_eligibility.py](D:/Project/coc-lite/backend/app/services/tools/clue_eligibility.py)
4. [rule_check.py](D:/Project/coc-lite/backend/app/services/tools/rule_check.py)

读的时候只问一个问题：

> 这个 Tool 的 input 和 output 分别是什么？

---

### 练习 2：新增一个只读 Tool

建议从简单只读 Tool 开始，例如：

```text
CharacterStatusTool
```

作用：

- 返回当前 HP/SAN/MP
- 返回最高的 5 个技能
- 不修改角色

这样你能练到 Tool 的完整注册链路，又不会碰到复杂状态写入。

---

### 练习 3：在调试面板观察 Tool

新增 Tool 后，打开前端调试面板，确认是否出现：

```text
phase = tool
name = YourToolName
status = start / success
```

如果没有出现，优先检查：

1. `available_tool_names()`
2. `SkillSpec.allowed_tools`
3. `run_generic_skill()`

---

## 9. 一句话总结

新增 Tool 的核心不是“多写一个函数”，而是让它进入这条链：

```text
Tool 文件
  -> Tools 导出
  -> available_tool_names 白名单
  -> SkillSpec.allowed_tools
  -> run_generic_skill 调用
  -> ToolObservation
  -> ExecutorAgent 汇总
```

这条链走通了，你就真的给 Agent 增加了一个可用能力。
