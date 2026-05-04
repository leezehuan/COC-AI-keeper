import random
import re
from dataclasses import dataclass, asdict


@dataclass
class DiceResult:
    expression: str
    rolls: list[int]
    modifier: int
    total: int


@dataclass
class SkillCheckResult:
    skill: str
    skill_value: int
    difficulty: str
    roll: int
    success_level: str
    success: bool


def roll(expression: str) -> DiceResult:
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
    return random.randint(1, 100)


def skill_check(skill: str, skill_value: int, difficulty: str = "常规") -> SkillCheckResult:
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
    normalized = difficulty.lower()
    if normalized in {"hard", "困难"}:
        return skill_value // 2
    if normalized in {"extreme", "极难"}:
        return skill_value // 5
    return skill_value


def classify_success(result: int, skill_value: int) -> str:
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
    check = skill_check("理智", current_san, "常规")
    loss_expression = success_loss if check.success else failure_loss
    loss = roll(loss_expression)
    return {
        "check": asdict(check),
        "loss_roll": asdict(loss),
        "san_loss": loss.total,
        "san_after": max(0, current_san - loss.total),
    }
