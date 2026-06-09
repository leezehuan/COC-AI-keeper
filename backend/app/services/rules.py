from dataclasses import asdict, dataclass, field
from typing import Any

from app.services.dice import sanity_check, skill_check


@dataclass
class RequiredCheck:
    """必须执行的检定（RequiredCheck = 必须检定）。描述一次具体的检定需求。"""
    kind: str  # kind = 检定类型：如 "技能"、"属性"、"理智"
    skill: str  # skill = 检定技能名：如 "侦查"、"力量"
    skill_value: int  # skill_value = 技能数值：如 60
    difficulty: str = "常规"  # difficulty = 难度等级：常规/困难/极难
    reason: str = "行动存在不确定性，需要检定。"  # reason = 检定原因


@dataclass
class RuleAdjudication:
    """规则裁定（RuleAdjudication = 规则裁定）。综合判断玩家行动需要哪些检定。"""
    needs_roll: bool  # needs_roll = 是否需要掷骰
    skill: str  # skill = 使用技能
    skill_value: int  # skill_value = 技能值
    difficulty: str  # difficulty = 难度
    needs_sanity: bool  # needs_sanity = 是否需要理智检定
    time_cost_minutes: int  # time_cost_minutes = 预计耗时（分钟）
    risk_level: int  # risk_level = 风险等级 1-5
    required_checks: list[RequiredCheck] = field(default_factory=list)  # required_checks = 必须执行的检定列表
    consequences: dict[str, Any] = field(default_factory=dict)  # consequences = 成功/失败的后果描述
    reason: str = "根据行动类型和当前场景进行轻量裁定。"  # reason = 裁定依据


def adjudicate_action(  # adjudicate_action = 裁定行动
    # 【中文名称】裁定行动
    # 【功能说明】根据玩家意图和场景上下文裁定需要哪些检定。

    message: str,
    intent: dict[str, Any],
    character_skills: dict[str, Any],
    character_attributes: dict[str, Any],
    scenario_context: list[dict[str, Any]],
    default_skill: str,
    luck: int = 50,
) -> RuleAdjudication:
    """根据玩家动作和当前场景做轻量规则裁定。

    【中文名称】裁定行动

    【功能说明】
    这是规则系统的核心入口之一。它不负责真正掷骰，而是先回答：
    1. 这次行动要不要检定？
    2. 检定什么？用哪个技能或属性？
    3. 难度是多少？
    4. 会不会触发理智检定？
    5. 这次行动大概会花多久、风险多大？

    你可以把它理解成“桌面跑团里的守秘人快速裁定”：
    玩家说一句自然语言动作后，系统先把这句话变成一张结构化的裁定单，
    后面的 RuleCheckTool 和 NarratorAgent 都按这张裁定单继续工作。

    【实现方法】
    1. 从 intent 里读取 action_type 和玩家指定技能。
    2. 用 resolve_check_target 把“技能名/属性名/幸运”解析成真实检定目标和值。
    3. 用 needs_skill_roll / needs_sanity_check 判断要不要掷骰、要不要过 SAN。
    4. 用 infer_difficulty / infer_time_cost / infer_risk_level 补齐环境难度、耗时、风险。
    5. 把这些判断整理成 RuleAdjudication 数据对象返回。

    【为什么拆成“裁定”和“执行”两步】
    因为系统希望把“规则判断”与“随机结果”分开：
    - adjudicate_action 负责判断规则
    - execute_rule_tools 负责真正掷骰
    这样更容易调试，也更适合在日志里回放“为什么会做这次检定”。

    【参数说明】
    - message: 玩家原始自然语言输入
    - intent: ContextAgent 解析出的结构化意图
    - character_skills: 角色技能表
    - character_attributes: 角色属性表
    - scenario_context: 当前相关剧本片段，用于判断难度和理智压力
    - default_skill: 没有明确技能时的默认技能
    - luck: 角色幸运值

    【返回值】
    - RuleAdjudication: 一份结构化裁定结果，描述接下来要执行哪些检定
    """
    # 1. 读取行动类型；如果 ContextAgent 没解析出来，就按最常见的“调查”处理。
    action_type = str(intent.get("action_type") or "调查")  # action_type = 调查/移动/社交/战斗等高层意图
    # 2. 读取玩家或计划指定的技能；没有明确技能时使用调用方传入的默认技能。
    requested = str(intent.get("skill") or default_skill)  # requested = 玩家希望使用的技能/属性名称
    # 3. 将 requested 解析成实际检定目标：可能是角色技能，也可能是力量/敏捷/幸运等属性。
    skill, skill_value, check_kind = resolve_check_target(requested, message, character_skills, character_attributes, luck)  # skill/skill_value/check_kind = 实际检定名、数值和类型
    # 4. 判断本行动是否需要 D100 检定；元问题和低风险描述通常不掷骰。
    needs_roll = needs_skill_roll(message, action_type, intent)  # needs_roll = 是否需要技能/属性/幸运检定
    # 5. 判断是否需要理智检定；基于玩家输入和剧本上下文中的恐怖关键词。
    needs_sanity = needs_sanity_check(message, scenario_context)  # needs_sanity = 是否触发 SAN 检定
    # 6. 推断检定难度；黑暗、风雨、隐藏等上下文会提高难度。
    difficulty = infer_difficulty(message, scenario_context)  # difficulty = 常规/困难/极难
    # 7. 推断本行动消耗的游戏内时间，后续 story_state 会据此推进 current_time。
    time_cost = infer_time_cost(message, action_type, needs_roll)  # time_cost = 本回合耗时分钟数
    # 8. 推断风险等级；战斗、怪物、SAN 冲击等因素会提高危险等级。
    risk_level = infer_risk_level(message, action_type, needs_sanity)  # risk_level = 1-5 的轻量风险评估
    # 9. 初始化必须检定列表；这里只描述要检定什么，真正掷骰在 execute_rule_tools。
    checks: list[RequiredCheck] = []  # checks = 本回合需要执行的检定需求
    if needs_roll:
        # 10a. 技能/属性检定需求：记录类型、名称、数值、难度和原因，供 RuleCheckTool 执行。
        checks.append(RequiredCheck(kind=check_kind, skill=skill, skill_value=skill_value, difficulty=difficulty, reason=f"玩家行动需要通过{check_kind}判断效果。"))
    if needs_sanity:
        # 10b. 理智检定需求：SAN 具体损失骰由 execute_rule_tools 的 sanity_check 处理。
        checks.append(RequiredCheck(kind="理智", skill="理智", skill_value=0, difficulty="常规", reason="当前行动或场景可能造成精神冲击。"))
    # 11. 返回结构化裁定；这是“裁判判定书”，不是骰点结果本身。
    return RuleAdjudication(  # RuleAdjudication = 本回合规则裁定的标准对象
        needs_roll=needs_roll,  # needs_roll = 是否要执行技能/属性检定
        skill=skill,  # skill = 实际检定名称，可能是侦查/力量/幸运等
        skill_value=skill_value,  # skill_value = 实际检定目标值
        difficulty=difficulty,  # difficulty = 检定难度
        needs_sanity=needs_sanity,  # needs_sanity = 是否还要理智检定
        time_cost_minutes=time_cost,  # time_cost_minutes = 本行动耗时
        risk_level=risk_level,  # risk_level = 本行动风险等级
        required_checks=checks,  # required_checks = 需要执行的检定清单
        consequences={  # consequences = 给叙事阶段使用的成功/失败后果框架
            "成功": "获得更明确的信息或推进当前目标。",  # 成功后果 = 推进目标或获得清晰信息
            "失败": "获得有限信息、消耗时间或提高危险等级。",  # 失败后果 = 信息有限、耗时或危险上升
        },
    )


def execute_rule_tools(adjudication: dict[str, Any], current_san: int) -> dict[str, list[dict[str, Any]]]:
    """执行规则检定（execute_rule_tools = 执行规则检定）。根据裁定结果实际掷骰，返回技能检定和理智检定的结果。"""
    # 1. dice_results 是前端展示用的统一骰点列表，技能骰和理智损失骰都会放进来。
    dice_results: list[dict[str, Any]] = []  # dice_results = 所有可展示骰点
    # 2. skill_checks 只保存技能/属性/幸运检定的结构化结果。
    skill_checks: list[dict[str, Any]] = []  # skill_checks = D100 检定结果列表
    # 3. sanity_checks 只保存理智检定和 SAN 损失结果。
    sanity_checks: list[dict[str, Any]] = []  # sanity_checks = 理智检定结果列表
    if adjudication.get("needs_roll"):
        # 4. 从裁定里的 required_checks 取检定类型；缺失时按“技能检定”展示。
        check_kind = str((adjudication.get("required_checks") or [{}])[0].get("kind") or "技能")  # check_kind = 技能/属性/幸运
        # 5. 真正执行 D100 检定；这里调用 dice.skill_check，LLM 不参与随机数生成。
        check = skill_check(str(adjudication["skill"]), int(adjudication["skill_value"]), str(adjudication.get("difficulty", "常规")))  # check = 一次完整 D100 检定
        # 6. dataclass 转 dict，便于写入 JSON 字段、TurnLog 和 API 响应。
        payload = asdict(check)  # payload = 可序列化的技能检定结果
        # 7. 将结构化检定结果加入 skill_checks，供 NarratorAgent 和前端读取。
        skill_checks.append(payload)  # skill_checks += 本次检定
        # 8. 同时把原始骰点整理进 dice_results，前端可以统一显示“掷了什么骰”。
        dice_results.append({"expression": "1d100", "rolls": [check.roll], "modifier": 0, "total": check.roll, "用途": f"{check_kind}检定"})
    if adjudication.get("needs_sanity"):
        # 9. 执行理智检定；当前规则为成功损失 0、失败损失 1d4。
        san = sanity_check(current_san, "0", "1d4")  # san = 理智检定和 SAN 损失完整结果
        # 10. 保存理智检定结构化结果，后续叙事和前端都从这里读 SAN 变化。
        sanity_checks.append(san)  # sanity_checks += 本次理智检定
        # 11. 复制损失骰记录，避免直接修改 sanity_check 返回对象的内部引用。
        loss_roll = dict(san["loss_roll"])  # loss_roll = 理智损失骰点
        # 12. 给骰点加用途标签，前端能显示“这是理智损失”而不是普通骰点。
        loss_roll["用途"] = "理智损失"  # 用途 = 前端展示标签
        # 13. 理智损失骰也放入统一 dice_results，便于回合日志完整审计。
        dice_results.append(loss_roll)  # dice_results += 理智损失骰
    # 14. 返回三类结果；ExecutorAgent 会把它们并入 resolution 和最终 API 响应。
    return {"dice_results": dice_results, "skill_checks": skill_checks, "sanity_checks": sanity_checks}


def normalize_skill_name(skill: str) -> str:
    """规范化技能名（normalize_skill_name = 规范化技能名）。将简称映射为完整技能名，如"射击"→"射击（手枪）"。"""
    aliases = {"射击": "射击（手枪）", "驾驶": "驾驶（船）", "科学": "博物学", "": "侦查"}
    return aliases.get(skill, skill)


def resolve_check_target(
    requested: str,
    message: str,
    character_skills: dict[str, Any],
    character_attributes: dict[str, Any],
    luck: int,
) -> tuple[str, int, str]:
    """把玩家请求解析成真正要检定的目标。

    【中文名称】解析检定目标

    【功能说明】
    玩家输入里提到的“要用什么检定”并不总是标准化的。
    有时玩家会直接说技能名，比如“我想侦查一下”；
    有时会说动作，比如“我推开门”，这更像力量检定；
    有时甚至会说“我碰碰运气”，这实际上是幸运检定。
    本函数就是把这些模糊表达，统一解析成：
    - 检定名称
    - 检定数值
    - 检定类型（技能 / 属性）

    【实现方法】
    1. 先对 requested 做技能别名归一化。
    2. 再从 message 中尝试推断属性倾向。
    3. 如果明确命中角色技能表，优先按技能检定。
    4. 如果更像属性检定，就读取属性值。
    5. 都不满足时，回退到 requested 或“侦查”的技能值。

    【返回值结构】
    - tuple[0]: 最终用于展示和记录的检定名称
    - tuple[1]: 检定目标值
    - tuple[2]: 检定类型（“技能”或“属性”）
    """
    # 1. 先规范化技能别名，例如“射击”统一到“射击（手枪）”。
    skill = normalize_skill_name(requested)  # skill = 规范化后的技能名
    # 2. 再从玩家文本中推断属性检定，例如“推开门”倾向使用 STR。
    inferred_attribute = infer_attribute_from_message(message)  # inferred_attribute = 推断出的属性缩写
    if inferred_attribute and (skill in {"", "侦查"} or skill not in character_skills):
        # 3a. 如果文本强烈暗示属性，且没有明确有效技能，就优先做属性检定。
        return format_attribute_name(inferred_attribute), attribute_value(character_attributes, inferred_attribute, luck), "属性"
    if skill in character_skills:
        # 3b. 如果角色卡上存在该技能，直接使用技能值；空值兜底为 25。
        return skill, int(character_skills.get(skill) or 25), "技能"
    # 4. 如果 requested 本身就是“力量/DEX/幸运”等属性名，则转换成标准属性。
    attribute = normalize_attribute_name(skill) or inferred_attribute  # attribute = 标准属性缩写或空字符串
    if attribute:
        # 5. 属性存在时返回中文展示名和属性数值。
        return format_attribute_name(attribute), attribute_value(character_attributes, attribute, luck), "属性"
    # 6. 最后兜底：保留原技能名，数值用该技能值或“侦查”技能值，再不行用 25。
    return skill, int(character_skills.get(skill, character_skills.get("侦查", 25)) or 25), "技能"


def normalize_attribute_name(value: str) -> str:
    """规范化属性名（normalize_attribute_name = 规范化属性名）。将中文属性名映射为标准缩写，如"力量"→"STR"。"""
    normalized = value.strip()
    aliases = {
        "力量": "STR",
        "str": "STR",
        "STR": "STR",
        "体质": "CON",
        "con": "CON",
        "CON": "CON",
        "体型": "SIZ",
        "siz": "SIZ",
        "SIZ": "SIZ",
        "敏捷": "DEX",
        "dex": "DEX",
        "DEX": "DEX",
        "外貌": "APP",
        "app": "APP",
        "APP": "APP",
        "智力": "INT",
        "灵感": "INT",
        "int": "INT",
        "INT": "INT",
        "意志": "POW",
        "pow": "POW",
        "POW": "POW",
        "教育": "EDU",
        "知识": "EDU",
        "edu": "EDU",
        "EDU": "EDU",
        "幸运": "Luck",
        "luck": "Luck",
        "Luck": "Luck",
    }
    return aliases.get(normalized, aliases.get(normalized.upper(), ""))


def infer_attribute_from_message(message: str) -> str:
    """从玩家输入推断属性（infer_attribute_from_message = 推断属性）。根据关键词匹配推断玩家想用哪个属性，如"推开"→STR。"""
    mapping = [
        (["推开", "举起", "搬开", "撬开", "破门", "强行"], "STR"),
        (["毒", "疾病", "寒冷", "忍耐", "屏息"], "CON"),
        (["挤过", "钻进", "身材", "体型"], "SIZ"),
        (["跳", "躲", "闪避", "平衡", "快速", "敏捷"], "DEX"),
        (["外貌", "魅力", "吸引", "留下印象"], "APP"),
        (["灵感", "推理", "解谜", "理解", "想起", "分析"], "INT"),
        (["意志", "抵抗", "克制", "忍住", "魔法"], "POW"),
        (["知识", "学术", "记得", "回忆", "教育"], "EDU"),
        (["幸运", "碰运气", "运气"], "Luck"),
    ]
    for keywords, attribute in mapping:
        if any(keyword in message for keyword in keywords):
            return attribute
    return ""


def attribute_value(character_attributes: dict[str, Any], attribute: str, luck: int) -> int:
    """获取属性数值（attribute_value = 属性值）。从角色属性字典中提取指定属性的数值，Luck特殊处理。"""
    core = character_attributes.get("核心属性", {}) if isinstance(character_attributes, dict) else {}
    if attribute == "Luck":
        luck_value = character_attributes.get("Luck") or as_attribute_number(core.get("Luck")) or luck or 50
        return int(luck_value)
    value = character_attributes.get(attribute)
    return int(as_attribute_number(value) or as_attribute_number(core.get(attribute)) or 25)


def as_attribute_number(value: Any) -> int | None:
    """提取属性数值（as_attribute_number = 提取属性数值）。从可能嵌套的字典中提取数值，如{"简单鉴定": 50}→50。"""
    if isinstance(value, dict):
        value = value.get("简单鉴定", value.get("全值"))
    if isinstance(value, (int, float)):
        return int(value)
    try:
        return int(str(value).strip())
    except (TypeError, ValueError):
        return None


def format_attribute_name(attribute: str) -> str:
    """格式化属性名（format_attribute_name = 格式化属性名）。将缩写转为中文显示名，如"STR"→"力量"。"""
    names = {
        "STR": "力量",
        "CON": "体质",
        "SIZ": "体型",
        "DEX": "敏捷",
        "APP": "外貌",
        "INT": "智力",
        "POW": "意志",
        "EDU": "教育",
        "Luck": "幸运",
    }
    return names.get(attribute, attribute)


def needs_skill_roll(message: str, action_type: str, intent: dict[str, Any]) -> bool:
    """判断这次行动是否应触发技能或属性检定。

    【中文名称】是否需要技能检定

    【功能说明】
    这一步的目标不是精确模拟所有 CoC 规则细节，而是给项目提供一个稳定、易维护的轻量判定。
    系统会优先避免两类问题：
    1. 明显有风险或不确定性的动作却没有掷骰
    2. 明显只是叙事补充或元问题却被错误要求掷骰

    【实现方法】
    - 如果意图被标成 meta（如问规则、问系统操作），直接不检定
    - 战斗、社交等高风险动作默认检定
    - 其余动作通过关键词表识别“搜索、追踪、修理、说服、破门、碰运气”等典型检定场景

    【返回值】
    - bool: True 表示后续应进入 RuleCheckTool 或 execute_rule_tools
    """
    if intent.get("is_meta"):
        return False
    check_words = ["仔细", "搜索", "寻找", "检查", "追踪", "修", "开锁", "说服", "攻击", "射击", "潜行", "医学", "急救", "估价", "辨认", "强行", "偷偷", "推开", "举起", "破门", "跳", "躲", "抵抗", "灵感", "回忆", "碰运气"]
    risky_actions = {"战斗", "社交"}
    return action_type in risky_actions or any(word in message for word in check_words)


def needs_sanity_check(message: str, context: list[dict[str, Any]]) -> bool:
    """判断这次行动或场景是否可能触发理智检定。

    【中文名称】是否需要理智检定

    【功能说明】
    理智检定往往不仅取决于玩家说了什么，也取决于当前场景里出现了什么。
    所以这里会同时查看：
    1. 玩家输入文本
    2. 检索到的前几条场景上下文

    然后用一组偏保守的恐怖关键词做触发判断。
    这不是完整的规则引擎，但能让系统在“尸体、怪物、深潜者、血腥场面”等典型场景下自动进入 SAN 流程。
    """
    text = message + "\n" + "\n".join(str(item.get("document", ""))[:300] for item in context[:2])
    return any(word in text for word in ["尸体", "血淋淋", "怪物", "理智检定", "理智损失", "幼徒", "深潜者", "恐怖"])


def infer_difficulty(message: str, context: list[dict[str, Any]]) -> str:
    """根据环境和动作文本推断检定难度。

    【中文名称】推断难度

    【功能说明】
    本函数把“暴风雨、黑暗、隐藏、受伤、完全黑暗”等叙事条件，
    转换成规则层的“常规 / 困难 / 极难”。
    这样后续 skill_check 才能用统一阈值计算成功等级。

    【实现思路】
    不是做复杂的规则树，而是将玩家输入与场景片段拼成一段文本，
    再用关键词表快速判断环境压力。
    这让项目在可维护性和可解释性之间保持一个比较好的平衡。
    """
    text = message + "\n" + "\n".join(str(item.get("document", ""))[:300] for item in context[:2])
    if any(word in text for word in ["极难", "几乎不可能", "暴风雨中", "完全黑暗"]):
        return "极难"
    if any(word in text for word in ["困难", "黑暗", "风雨", "受伤", "匆忙", "隐藏"]):
        return "困难"
    return "常规"


def infer_time_cost(message: str, action_type: str, needs_roll: bool) -> int:
    """推断耗时（infer_time_cost = 推断耗时）。根据行动类型和关键词估算消耗分钟数。"""
    if any(word in message for word in ["快速", "立刻", "马上", "冲"]):
        return 1
    if action_type == "移动":
        return 5
    if needs_roll:
        return 10
    return 3


def infer_risk_level(message: str, action_type: str, needs_sanity: bool) -> int:
    """推断风险等级（infer_risk_level = 推断风险等级）。根据行动类型和关键词估算风险 1-5。"""
    risk = 1
    if action_type == "战斗":
        risk += 2
    if any(word in message for word in ["攻击", "开枪", "怪物", "深潜者", "逃跑"]):
        risk += 1
    if needs_sanity:
        risk += 1
    return min(risk, 5)


def as_adjudication_dict(adjudication: RuleAdjudication) -> dict[str, Any]:
    """把 RuleAdjudication 转成可序列化字典。

    【中文名称】裁定转字典

    【功能说明】
    RuleAdjudication 和 RequiredCheck 都是 dataclass，
    便于 Python 内部表达规则结果；但数据库 JSON 字段、API 响应、
    TurnLog 和调试面板都更适合直接消费普通 dict。
    所以在跨模块、跨进程、跨前后端边界时，通常会先经过这个函数。

    【关键调用场景】
    - ExecutorAgent 在需要独立裁定时
    - RuleCheckTool 输出结构化结果时
    - TurnLog / 调试面板记录裁定细节时
    """
    payload = asdict(adjudication)
    payload["required_checks"] = [asdict(item) for item in adjudication.required_checks]
    return payload
