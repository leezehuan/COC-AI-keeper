# coc-lite Skills 扩展指南

> 面向 Agent 开发学生：  
> 这份文档教你如何给 `backend/app/services/skills/` 新增一种复合行动能力。

---

## 1. 先理解 Skill 是什么

在本项目里，Skill 不是角色卡上的“侦查/聆听/图书馆使用”那种技能值。  
这里的 Skill 更像 Agent 的“行动模板”。

可以先这样区分：

| 名称 | 含义 |
|---|---|
| CoC 技能值 | 角色属性，例如侦查 60、聆听 50 |
| Tool | 原子能力，例如检索、查询物品、规则检定 |
| Skill | 复合行动模板，例如调查、移动、社交、使用物品 |

一句话：

```text
Skill = 面向一类玩家行动的流程模板
```

比如：

- `InvestigateSkill`：处理调查、观察、阅读文献
- `MoveSkill`：处理移动
- `SocialInteractionSkill`：处理交谈、说服、恐吓
- `UseItemSkill`：处理使用物品
- `CombatLiteSkill`：处理轻量战斗和逃跑
- `WaitOrObserveSkill`：处理等待、查询状态、剧情回顾

---

## 2. Skill 在回合链路里的位置

一次玩家行动的大致流程是：

```text
ContextAgent
  -> 解析 intent.action_type
PlannerAgent
  -> 根据 action_type 选择 allowed_skills
ExecutorAgent
  -> 取 allowed_skills 中的第一个 Skill
Skill
  -> 按白名单调用多个 Tool
ExecutorAgent
  -> 汇总 SkillResult
NarratorAgent
  -> 根据 SkillResult 生成叙事和 state_delta
GuardAgent
  -> 校验叙事和状态
```

所以新增 Skill 的核心是：

> 让系统知道“某类 action_type 应该由哪个 Skill 处理”。

---

## 3. Skill 通常由哪些文件组成

建议先看一个最简单的例子：

[backend/app/services/skills/move/skill.py](D:/Project/coc-lite/backend/app/services/skills/move/skill.py)

一个标准 Skill 目录通常是：

```text
backend/app/services/skills/your_skill/
├── __init__.py
├── prompt.py
└── skill.py
```

### `prompt.py`

放 Skill 的说明文本，主要用于 `SkillSpec.description`。

### `skill.py`

定义：

- `SPEC`
- `run()`

### `__init__.py`

可以为空，表示这是一个 Python 包。

---

## 4. `SkillSpec` 怎么理解

定义在：

[backend/app/services/skills/base.py](D:/Project/coc-lite/backend/app/services/skills/base.py)

核心字段：

| 字段 | 含义 |
|---|---|
| `name` | Skill 名称 |
| `action_types` | 能处理哪些玩家行动类型 |
| `allowed_tools` | 允许调用哪些 Tool |
| `description` | Skill 说明 |
| `constraints` | 约束条件 |

示例：

```python
SPEC = SkillSpec(
    name="MoveSkill",
    action_types=["移动"],
    allowed_tools=["SceneAffordanceTool", "ContextSearchTool"],
    description=SKILL_PROMPT,
    constraints=["不直接改变地点", "地点变化必须由 state_delta 和 guardrails 处理"],
)
```

这里非常重要的一点是：

```text
Skill 不直接改状态。
Skill 只收集观察和候选裁定。
最终状态变化由 state_delta + GuardAgent + _commit_state 处理。
```

---

## 5. 大部分 Skill 怎么执行

大部分现有 Skill 都只有很薄的一层：

```python
def run(state: dict[str, Any], runtime: dict[str, Any]) -> SkillResult:
    return run_generic_skill(spec=SPEC, state=state, runtime=runtime)
```

通用逻辑在：

[backend/app/services/skills/common.py](D:/Project/coc-lite/backend/app/services/skills/common.py)

`run_generic_skill()` 会按固定顺序尝试调用 Tool：

```text
ContextSearchTool
InventoryLookupTool
SceneAffordanceTool
ClueEligibilityTool
MemoryRecallTool
RuleCheckTool
```

但每个 Tool 是否真的执行，要看：

```text
PlannerAgent 生成的 allowed_tools
SkillSpec.allowed_tools
run_generic_skill() 内部是否支持调用该 Tool
```

---

## 6. 新增 Skill 的标准步骤

下面用一个假想 Skill 举例：

```text
RestAndRecoverSkill
作用：处理玩家休息、包扎、恢复状态这类行动。
```

注意：这只是教学示例。真实恢复 HP/SAN 要谨慎接入规则和状态校验。

---

### 第 1 步：新增目录

```text
backend/app/services/skills/rest_and_recover/
├── __init__.py
├── prompt.py
└── skill.py
```

---

### 第 2 步：写 `prompt.py`

```python
SKILL_PROMPT = """
RestAndRecoverSkill 用于处理玩家休息、包扎、短暂停留、检查伤势等行动。

职责：
- 检索当前场景是否安全
- 查询角色状态和物品栏
- 判断是否需要规则检定
- 输出候选裁定，不直接修改 HP/SAN/MP 或数据库

约束：
- 不直接恢复角色数值
- 恢复效果必须通过 state_delta 表达
- 需要 GuardAgent 校验后由 _commit_state 落库
"""
```

---

### 第 3 步：写 `skill.py`

```python
from __future__ import annotations

from typing import Any

from app.services.skills.base import SkillResult, SkillSpec
from app.services.skills.common import run_generic_skill
from app.services.skills.rest_and_recover.prompt import SKILL_PROMPT


SPEC = SkillSpec(
    name="RestAndRecoverSkill",
    action_types=["休息", "包扎", "恢复", "处理伤口"],
    allowed_tools=[
        "ContextSearchTool",
        "InventoryLookupTool",
        "SceneAffordanceTool",
        "RuleCheckTool",
    ],
    description=SKILL_PROMPT,
    constraints=[
        "不直接修改 HP/SAN/MP。",
        "恢复效果必须由 state_delta 表达并经过 GuardAgent 校验。",
        "不能在危险场景中无条件允许恢复。",
    ],
)


def run(state: dict[str, Any], runtime: dict[str, Any]) -> SkillResult:
    return run_generic_skill(spec=SPEC, state=state, runtime=runtime)
```

---

### 第 4 步：注册到 Skill 注册表

文件：

[backend/app/services/skills/registry.py](D:/Project/coc-lite/backend/app/services/skills/registry.py)

增加 import：

```python
from app.services.skills.rest_and_recover.skill import SPEC as REST_AND_RECOVER_SPEC, run as run_rest_and_recover
```

加入 `SKILL_HANDLERS`：

```python
SKILL_HANDLERS = {
    ...
    REST_AND_RECOVER_SPEC.name: run_rest_and_recover,
}
```

加入 `SKILL_SPECS`：

```python
SKILL_SPECS: dict[str, SkillSpec] = {
    ...
    REST_AND_RECOVER_SPEC.name: REST_AND_RECOVER_SPEC,
}
```

加入 `ACTION_TYPE_TO_SKILL`：

```python
ACTION_TYPE_TO_SKILL = {
    ...
    "休息": "RestAndRecoverSkill",
    "包扎": "RestAndRecoverSkill",
    "恢复": "RestAndRecoverSkill",
    "处理伤口": "RestAndRecoverSkill",
}
```

这一步是新增 Skill 最关键的注册点。

---

### 第 5 步：确认意图解析能产出对应 action_type

即使你注册了 Skill，如果 `ContextAgent` 从玩家输入里解析不出对应 action_type，系统也不一定会选到它。

意图解析有两层：

1. LLM prompt：在 `prompt_config.py` 里组织
2. 关键词回退：在 `agents/utils.py` 的 `infer_action_type()` 里

如果你新增的是常见中文行动类型，建议至少更新：

[backend/app/services/agents/utils.py](D:/Project/coc-lite/backend/app/services/agents/utils.py)

例如：

```python
if any(word in message for word in ["休息", "包扎", "处理伤口", "恢复"]):
    return "休息"
```

这样即使 LLM 不可用，系统也能用启发式规则选到新 Skill。

---

### 第 6 步：确认 PlannerAgent 会允许它

`PlannerAgent` 会读取：

```python
available_skills=list(SKILL_SPECS.keys())
```

只要你已经加入 `SKILL_SPECS`，它就会出现在可用 Skill 列表里。

然后计划校验会确保：

```text
allowed_skills 必须存在于 SKILL_SPECS
allowed_tools 必须存在于 available_tool_names()
```

---

### 第 7 步：观察调试面板

触发一次输入：

```text
我找个相对安全的地方包扎伤口。
```

你应该能在调试事件里看到：

```text
PlannerAgent -> allowed_skills 包含 RestAndRecoverSkill
ExecutorAgent -> 开始执行 Skill
phase=skill name=RestAndRecoverSkill status=start/success
phase=tool ... status=start/success
```

如果看不到，优先查：

1. `ACTION_TYPE_TO_SKILL`
2. `SKILL_SPECS`
3. `SKILL_HANDLERS`
4. `infer_action_type()`
5. `SkillSpec.allowed_tools`

---

## 7. 什么时候需要自定义 Skill 执行逻辑

大部分情况下，使用 `run_generic_skill()` 就够了。

但如果你的 Skill 需要特殊顺序或特殊计算，可以写自己的 `run()`。

适合自定义的情况：

- 需要固定先执行规则检定，再查其他内容
- 需要根据第一个 Tool 的结果决定是否继续
- 需要组合多个观察结果生成特殊结构
- 不适合通用 Tool 顺序

自定义时仍然建议遵守：

```text
返回 SkillResult
不直接写数据库
不绕过 GuardAgent
骰点走 RuleCheckTool 或 rules.py
```

示意结构：

```python
def run(state: dict[str, Any], runtime: dict[str, Any]) -> SkillResult:
    observations: list[dict[str, Any]] = []
    # 按你的顺序调用 Tool，并收集 observation.as_dict()
    return SkillResult(
        skill=SPEC.name,
        input={"player_input": state.get("player_input", ""), "intent": state.get("intent", {})},
        observations=observations,
        result={
            "decision_summary": "自定义 Skill 完成了特殊处理。",
            "candidate_resolution": {"requires_synthesis": True, "no_direct_state_write": True},
        },
    )
```

---

## 8. 新增 Skill 时最容易漏的地方

| 易漏点 | 后果 |
|---|---|
| 没加 `SKILL_HANDLERS` | ExecutorAgent 找不到执行函数，会回退或执行错误 |
| 没加 `SKILL_SPECS` | PlannerAgent 白名单里没有这个 Skill |
| 没加 `ACTION_TYPE_TO_SKILL` | action_type 不会自动选到新 Skill |
| 没更新 `infer_action_type()` | LLM 失败时无法回退到新 Skill |
| `allowed_tools` 写了未知 Tool | PlannerAgent 会过滤掉 |
| Skill 直接写数据库 | 状态变化绕过 GuardAgent，难审计 |
| prompt 只写风格，不写约束 | LLM 更容易生成越界状态 |

---

## 9. Skill 和 Tool 的边界

这是学习 Agent 工程时很重要的一点。

### Tool 应该负责

- 查资料
- 查物品
- 掷骰
- 判断候选
- 返回结构化观察

### Skill 应该负责

- 决定调用哪些 Tool
- 汇总观察结果
- 给后续 Agent 一个候选裁定

### NarratorAgent 应该负责

- 生成玩家可见叙事
- 生成 `state_delta`
- 给玩家下一步选项

### GuardAgent 应该负责

- 校验状态变化
- 清洗剧透
- 判断是否需要修复

### `_commit_state()` 应该负责

- 真正落库
- 写线索、物品、回合日志和会话记忆

如果你发现一个 Skill 想做所有事情，通常说明边界需要重新拆一下。

---

## 10. 推荐练习

### 练习 1：新增一个“状态查询”类 Skill

例如：

```text
SelfCheckSkill
```

处理：

- “我检查自己的伤势”
- “我看看自己的状态”
- “我整理一下身上的物品”

建议允许 Tool：

```python
["InventoryLookupTool", "MemoryRecallTool"]
```

这个练习比较安全，因为它主要是只读。

---

### 练习 2：新增一个“休息/包扎”类 Skill

这个练习更接近真实玩法，但要注意：

- 不直接恢复 HP/SAN
- 通过 `state_delta` 表达候选恢复
- 让 GuardAgent 校验
- 最后由 `_commit_state()` 统一落库

---

### 练习 3：给已有 Skill 加一个 Tool

这比新增完整 Skill 更简单，适合热身。

例如给 `MoveSkill` 增加一个环境感知 Tool：

```text
MoveSkill.allowed_tools += ["WeatherSenseTool"]
```

然后在 `run_generic_skill()` 中调用它。

---

## 11. 一句话总结

新增 Skill 的核心链路是：

```text
新 Skill 目录
  -> SPEC
  -> run()
  -> registry.SKILL_HANDLERS
  -> registry.SKILL_SPECS
  -> registry.ACTION_TYPE_TO_SKILL
  -> intent.action_type
  -> PlannerAgent 白名单
  -> ExecutorAgent 执行
  -> SkillResult
```

这条链走通后，你就不是只“写了一个函数”，而是给守秘人新增了一类可规划、可执行、可审计的行动能力。
