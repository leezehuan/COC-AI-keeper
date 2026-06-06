import random
import re
from dataclasses import dataclass, asdict


@dataclass
class DiceResult:
    """骰点结果（DiceResult = 骰点结果）。
    记录一次掷骰的完整信息。
    """
    expression: str  # 骰点表达式（expression = 表达式），如 "1d100"、"2d6+3"
    rolls: list[int]  # 各骰子原始值（rolls = 骰子值列表），如 [42] 或 [3, 5]
    modifier: int  # 修正值（modifier = 修正值），如 +3、-2
    total: int  # 最终结果（total = 总计），所有骰子值之和 + 修正值


@dataclass
class SkillCheckResult:
    """技能检定结果（SkillCheckResult = 技能检定结果）。
    记录一次 CoC 技能检定的完整信息。
    """
    skill: str  # 技能名称（skill = 技能名），如 "侦查"、"图书馆使用"
    skill_value: int  # 技能数值（skill_value = 技能值），如 60
    difficulty: str  # 难度等级（difficulty = 难度），如 "常规"、"困难"、"极难"
    roll: int  # 骰点结果（roll = 掷骰值），1-100 之间的随机数
    success_level: str  # 成功等级（success_level = 成功等级），如 "常规成功"、"困难成功"、"大成功"、"失败"
    success: bool  # 是否成功（success = 成功标志），True=检定通过，False=检定失败


def roll(expression: str) -> DiceResult:
    """掷骰（roll = 掷骰）。
    解析骰子表达式并执行掷骰，支持格式如 1d100、2d6+3。
    """
    fixed_number = expression.strip()
    if fixed_number.isdigit():
        total = int(fixed_number)
        return DiceResult(expression=expression, rolls=[], modifier=0, total=total)
    match = re.fullmatch(r"\s*(\d*)d(\d+)([+-]\d+)?\s*", expression.lower())
    if not match:
        raise ValueError(f"不支持的骰子表达式：{expression}")
    count = int(match.group(1) or "1")
    sides = int(match.group(2))
    modifier = int(match.group(3) or "0")
    if count < 1 or count > 100 or sides < 2 or sides > 1000:
        raise ValueError(f"骰子表达式超出允许范围：{expression}")
    rolls = [random.randint(1, sides) for _ in range(count)]
    return DiceResult(expression=expression, rolls=rolls, modifier=modifier, total=sum(rolls) + modifier)


def roll_d100() -> int:
    """掷 D100（roll_d100 = 掷百分骰）。
    返回 1-100 之间的随机整数，CoC 最常用的检定骰。
    """
    return random.randint(1, 100)


def skill_check(skill: str, skill_value: int, difficulty: str = "常规") -> SkillCheckResult:
    """技能检定（skill_check = 技能检定）。
    执行一次 CoC 技能检定：掷 D100，与技能值比较，判断成功等级。
    """
    result = roll_d100()
    threshold = difficulty_threshold(skill_value, difficulty)
    success = result <= threshold
    success_level = classify_success(result, skill_value)
    if not success and success_level not in {"大失败"}:
        success_level = "失败"
    return SkillCheckResult(
        skill=skill,
        skill_value=skill_value,
        difficulty=difficulty,
        roll=result,
        success_level=success_level,
        success=success,
    )


def difficulty_threshold(skill_value: int, difficulty: str) -> int:
    """计算难度阈值（difficulty_threshold = 难度阈值）。
    根据难度等级计算检定目标值：常规=技能值，困难=技能值/2，极难=技能值/5。
    """
    normalized = difficulty.lower()
    if normalized in {"hard", "困难"}:
        return skill_value // 2
    if normalized in {"extreme", "极难"}:
        return skill_value // 5
    return skill_value


def classify_success(result: int, skill_value: int) -> str:
    """判定成功等级（classify_success = 判定成功等级）。
    根据骰点结果和技能值判定：大成功/极难成功/困难成功/常规成功/失败/大失败。
    """
    if result == 1:
        return "大成功"
    if result >= 96 and (skill_value < 50 or result == 100):
        return "大失败"
    if result <= skill_value // 5:
        return "极难成功"
    if result <= skill_value // 2:
        return "困难成功"
    if result <= skill_value:
        return "常规成功"
    return "失败"


def sanity_check(current_san: int, success_loss: str, failure_loss: str) -> dict:
    """理智检定（sanity_check = 理智检定）。
    执行理智检定并计算理智损失：先做理智技能检定，成功/失败分别用不同的损失骰。
    current_san = 当前理智值，success_loss = 成功时的损失骰表达式，failure_loss = 失败时的损失骰表达式。
    """
    check = skill_check("理智", current_san, "常规")
    loss_expression = success_loss if check.success else failure_loss
    loss = roll(loss_expression)
    return {
        "check": asdict(check),
        "loss_roll": asdict(loss),
        "san_loss": loss.total,
        "san_after": max(0, current_san - loss.total),
    }
