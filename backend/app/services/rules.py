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
    action_type = str(intent.get("action_type") or "调查")
    requested = str(intent.get("skill") or default_skill)
    skill, skill_value, check_kind = resolve_check_target(requested, message, character_skills, character_attributes, luck)
    needs_roll = needs_skill_roll(message, action_type, intent)
    needs_sanity = needs_sanity_check(message, scenario_context)
    difficulty = infer_difficulty(message, scenario_context)
    time_cost = infer_time_cost(message, action_type, needs_roll)
    risk_level = infer_risk_level(message, action_type, needs_sanity)
    checks: list[RequiredCheck] = []
    if needs_roll:
        checks.append(RequiredCheck(kind=check_kind, skill=skill, skill_value=skill_value, difficulty=difficulty, reason=f"玩家行动需要通过{check_kind}判断效果。"))
    if needs_sanity:
        checks.append(RequiredCheck(kind="理智", skill="理智", skill_value=0, difficulty="常规", reason="当前行动或场景可能造成精神冲击。"))
    return RuleAdjudication(
        needs_roll=needs_roll,
        skill=skill,
        skill_value=skill_value,
        difficulty=difficulty,
        needs_sanity=needs_sanity,
        time_cost_minutes=time_cost,
        risk_level=risk_level,
        required_checks=checks,
        consequences={
            "成功": "获得更明确的信息或推进当前目标。",
            "失败": "获得有限信息、消耗时间或提高危险等级。",
        },
    )


def execute_rule_tools(adjudication: dict[str, Any], current_san: int) -> dict[str, list[dict[str, Any]]]:
    """执行规则检定（execute_rule_tools = 执行规则检定）。根据裁定结果实际掷骰，返回技能检定和理智检定的结果。"""
    dice_results: list[dict[str, Any]] = []
    skill_checks: list[dict[str, Any]] = []
    sanity_checks: list[dict[str, Any]] = []
    if adjudication.get("needs_roll"):
        check_kind = str((adjudication.get("required_checks") or [{}])[0].get("kind") or "技能")
        check = skill_check(str(adjudication["skill"]), int(adjudication["skill_value"]), str(adjudication.get("difficulty", "常规")))
        payload = asdict(check)
        skill_checks.append(payload)
        dice_results.append({"expression": "1d100", "rolls": [check.roll], "modifier": 0, "total": check.roll, "用途": f"{check_kind}检定"})
    if adjudication.get("needs_sanity"):
        san = sanity_check(current_san, "0", "1d4")
        sanity_checks.append(san)
        loss_roll = dict(san["loss_roll"])
        loss_roll["用途"] = "理智损失"
        dice_results.append(loss_roll)
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
    """解析检定目标（resolve_check_target = 解析检定目标）。根据请求技能和玩家输入，确定实际使用的技能/属性和数值。"""
    skill = normalize_skill_name(requested)
    inferred_attribute = infer_attribute_from_message(message)
    if inferred_attribute and (skill in {"", "侦查"} or skill not in character_skills):
        return format_attribute_name(inferred_attribute), attribute_value(character_attributes, inferred_attribute, luck), "属性"
    if skill in character_skills:
        return skill, int(character_skills.get(skill) or 25), "技能"
    attribute = normalize_attribute_name(skill) or inferred_attribute
    if attribute:
        return format_attribute_name(attribute), attribute_value(character_attributes, attribute, luck), "属性"
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
    """判断是否需要技能检定（needs_skill_roll = 是否需要技能检定）。根据行动类型和关键词判断。"""
    if intent.get("is_meta"):
        return False
    check_words = ["仔细", "搜索", "寻找", "检查", "追踪", "修", "开锁", "说服", "攻击", "射击", "潜行", "医学", "急救", "估价", "辨认", "强行", "偷偷", "推开", "举起", "破门", "跳", "躲", "抵抗", "灵感", "回忆", "碰运气"]
    risky_actions = {"战斗", "社交"}
    return action_type in risky_actions or any(word in message for word in check_words)


def needs_sanity_check(message: str, context: list[dict[str, Any]]) -> bool:
    """判断是否需要理智检定（needs_sanity_check = 是否需要理智检定）。根据恐怖关键词判断。"""
    text = message + "\n" + "\n".join(str(item.get("document", ""))[:300] for item in context[:2])
    return any(word in text for word in ["尸体", "血淋淋", "怪物", "理智检定", "理智损失", "幼徒", "深潜者", "恐怖"])


def infer_difficulty(message: str, context: list[dict[str, Any]]) -> str:
    """推断难度等级（infer_difficulty = 推断难度）。根据环境和行动关键词判断常规/困难/极难。"""
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
    """裁定转为字典（as_adjudication_dict = 裁定转字典）。将 RuleAdjudication 转为可 JSON 序列化的字典。"""
    payload = asdict(adjudication)
    payload["required_checks"] = [asdict(item) for item in adjudication.required_checks]
    return payload
