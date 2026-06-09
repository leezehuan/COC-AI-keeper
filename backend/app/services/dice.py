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
    # 1. 去掉表达式首尾空白，先判断它是不是一个固定数字。
    fixed_number = expression.strip()  # fixed_number = 清理后的骰子表达式
    if fixed_number.isdigit():
        # 2a. 固定数字不需要随机掷骰，例如 SAN 成功损失 "0"。
        total = int(fixed_number)  # total = 固定数值结果
        # 2b. rolls 为空表示没有实际骰子，modifier 为 0，total 就是固定数字。
        return DiceResult(expression=expression, rolls=[], modifier=0, total=total)
    # 3. 用正则解析标准骰子表达式：可省略骰子数量，支持可选加减修正。
    match = re.fullmatch(r"\s*(\d*)d(\d+)([+-]\d+)?\s*", expression.lower())  # match = 解析结果，包含骰子数量、面数、修正值
    if not match:
        # 4. 非法格式直接抛错，让上层 Tool/Agent 记录 error，而不是静默给假结果。
        raise ValueError(f"不支持的骰子表达式：{expression}")
    # 5. 解析骰子数量；"d100" 等价于 "1d100"。
    count = int(match.group(1) or "1")  # count = 骰子数量
    # 6. 解析骰子面数，例如 d6 的 sides 为 6。
    sides = int(match.group(2))  # sides = 每颗骰子的面数
    # 7. 解析修正值，例如 2d6+3 的 modifier 为 3。
    modifier = int(match.group(3) or "0")  # modifier = 总修正值
    if count < 1 or count > 100 or sides < 2 or sides > 1000:
        # 8. 限制表达式规模，避免异常输入造成过多随机调用或不合理骰子。
        raise ValueError(f"骰子表达式超出允许范围：{expression}")
    # 9. 实际掷骰；每颗骰子独立生成 1..sides 的随机整数。
    rolls = [random.randint(1, sides) for _ in range(count)]  # rolls = 每颗骰子的原始结果
    # 10. 返回完整骰点记录，total 为所有骰子之和加修正值。
    return DiceResult(expression=expression, rolls=rolls, modifier=modifier, total=sum(rolls) + modifier)


def roll_d100() -> int:
    """掷 D100（roll_d100 = 掷百分骰）。
    返回 1-100 之间的随机整数，CoC 最常用的检定骰。
    """
    # CoC 7 版的技能/属性/理智检定都使用百分骰，这里统一生成 1..100。
    return random.randint(1, 100)  # 返回 D100 结果


def skill_check(skill: str, skill_value: int, difficulty: str = "常规") -> SkillCheckResult:
    """技能检定（skill_check = 技能检定）。
    执行一次 CoC 技能检定：掷 D100，与技能值比较，判断成功等级。
    """
    # 1. 先掷 D100；这是唯一随机来源，保证检定结果由代码生成。
    result = roll_d100()  # result = 本次百分骰结果
    # 2. 根据难度计算目标阈值：常规=全值，困难=半值，极难=五分之一。
    threshold = difficulty_threshold(skill_value, difficulty)  # threshold = 本次检定通过阈值
    # 3. 骰点小于等于阈值即通过该难度。
    success = result <= threshold  # success = 是否通过当前难度
    # 4. 先按完整技能值计算成功等级，用于区分常规/困难/极难/大成功/大失败。
    success_level = classify_success(result, skill_value)  # success_level = 原始成功等级
    if not success and success_level not in {"大失败"}:
        # 5. 如果没有通过当前难度，即使骰点在常规范围内，也按本次难度显示为失败。
        success_level = "失败"
    # 6. 返回结构化检定结果；RuleCheckTool 会把它写入 skill_checks。
    return SkillCheckResult(  # SkillCheckResult = 一次技能/属性检定的完整记录
        skill=skill,  # skill = 检定名称
        skill_value=skill_value,  # skill_value = 角色卡上的目标值
        difficulty=difficulty,  # difficulty = 本次检定难度
        roll=result,  # roll = D100 结果
        success_level=success_level,  # success_level = 成功等级文本
        success=success,  # success = 是否通过本次难度
    )


def difficulty_threshold(skill_value: int, difficulty: str) -> int:
    """计算难度阈值（difficulty_threshold = 难度阈值）。
    根据难度等级计算检定目标值：常规=技能值，困难=技能值/2，极难=技能值/5。
    """
    # 1. 统一转小写，兼容英文 hard/extreme 和中文困难/极难。
    normalized = difficulty.lower()  # normalized = 规范化后的难度字符串
    if normalized in {"hard", "困难"}:
        # 2a. 困难成功要求骰点小于等于技能值的一半。
        return skill_value // 2
    if normalized in {"extreme", "极难"}:
        # 2b. 极难成功要求骰点小于等于技能值的五分之一。
        return skill_value // 5
    # 2c. 常规检定直接使用完整技能值。
    return skill_value


def classify_success(result: int, skill_value: int) -> str:
    """判定成功等级（classify_success = 判定成功等级）。
    根据骰点结果和技能值判定：大成功/极难成功/困难成功/常规成功/失败/大失败。
    """
    if result == 1:
        # 1. 1 点通常视为大成功，优先于其他等级判断。
        return "大成功"
    if result >= 96 and (skill_value < 50 or result == 100):
        # 2. 96-100 的大失败规则和技能值有关；100 一定大失败。
        return "大失败"
    if result <= skill_value // 5:
        # 3. 达到五分之一阈值，属于极难成功。
        return "极难成功"
    if result <= skill_value // 2:
        # 4. 达到半值阈值，属于困难成功。
        return "困难成功"
    if result <= skill_value:
        # 5. 达到全值阈值，属于常规成功。
        return "常规成功"
    # 6. 以上都不满足就是普通失败。
    return "失败"


def sanity_check(current_san: int, success_loss: str, failure_loss: str) -> dict:
    """理智检定（sanity_check = 理智检定）。
    执行理智检定并计算理智损失：先做理智技能检定，成功/失败分别用不同的损失骰。
    current_san = 当前理智值，success_loss = 成功时的损失骰表达式，failure_loss = 失败时的损失骰表达式。
    """
    # 1. 理智检定本质上也是一次以当前 SAN 为目标值的 D100 常规检定。
    check = skill_check("理智", current_san, "常规")  # check = 理智检定结果
    # 2. 根据检定是否成功，选择成功损失或失败损失的骰子表达式。
    loss_expression = success_loss if check.success else failure_loss  # loss_expression = 本次 SAN 损失表达式
    # 3. 执行 SAN 损失骰；成功损失可能是固定数字 "0"。
    loss = roll(loss_expression)  # loss = 理智损失骰点结果
    # 4. 返回结构化理智检定结果；SAN 不在这里落库，只给后续流程使用。
    return {
        "check": asdict(check),  # check = 理智 D100 检定记录
        "loss_roll": asdict(loss),  # loss_roll = SAN 损失骰记录
        "san_loss": loss.total,  # san_loss = 实际损失点数
        "san_after": max(0, current_san - loss.total),  # san_after = 扣除后的理智值，最低为 0
    }
