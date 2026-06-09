from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timedelta
from typing import Any

from app.utils import safe_key

# =============================================================================
# 【剧情状态管理 story_state】
# =============================================================================
# 这个文件负责管理游戏的长期剧情状态（story_state）。
# 初学者可以这样理解：
# 1. agent.py 负责“本回合怎么判断、怎么叙事”。
# 2. story_state.py 负责“本回合结束后，游戏世界状态应该怎么变”。
# 3. ensure_story_state 保证存档结构完整，build_turn_delta 构造变化，apply_turn_delta 真正合并变化。
# =============================================================================


# 【重要变量】STATE_VERSION
# 这是长期剧情状态的数据结构版本号。
# 当 story_state 的字段设计以后发生变化时，可以通过提升这个版本号，
# 提醒开发者“旧存档可能需要迁移或补字段”。
# 当前项目虽然还没有做专门的迁移器，但保留版本号能让状态结构演进更可控。
STATE_VERSION = 1


def ensure_story_state(raw_state: dict[str, Any] | None, current_location: str, current_scene: str, current_time: str) -> dict[str, Any]:
    """确保剧情状态结构完整（ensure_story_state = 确保剧情状态）。

    【中文名称】确保剧情状态

    【功能说明】
    将旧会话或空会话状态补齐为统一结构。每次读写剧情状态前都先调用它，
    确保“剧情/场景/记忆/秘密”四个分区存在且所有必要字段都有默认值。

    【参数说明】
    - raw_state: 原始状态字典（可为 None）
    - current_location: 当前地点
    - current_scene: 当前场景
    - current_time: 当前时间

    【返回值】
    - dict: 结构完整的剧情状态字典
    """
    state = deepcopy(raw_state or {})
    state.setdefault("版本", STATE_VERSION)
    state.setdefault("剧情", {})
    state.setdefault("场景", {})
    state.setdefault("记忆", {})
    state.setdefault("秘密", {})
    story = state["剧情"]
    story.setdefault("已访问地点", [])
    story.setdefault("已发现线索", [])
    story.setdefault("未解析线索", [])
    story.setdefault("已触发事件", [])
    story.setdefault("已关闭事件", [])
    story.setdefault("当前可前往地点", [current_location])
    story.setdefault("当前NPC状态", {})
    story.setdefault("NPC态度", {})
    story.setdefault("敌对势力警觉", 1)
    story.setdefault("时间压力", "普通")
    story.setdefault("仪式进度", 0)
    story.setdefault("剧情flag", {})
    story.setdefault("结局倾向", "未定")
    scene = state["场景"]
    scene.setdefault("当前地点", current_location)
    scene.setdefault("当前场景", current_scene)
    scene.setdefault("当前时间", current_time)
    scene.setdefault("房间内对象", [])
    scene.setdefault("已调查对象", [])
    scene.setdefault("未调查对象", [])
    scene.setdefault("可见异常", [])
    scene.setdefault("隐藏线索", [])
    scene.setdefault("当前NPC", [])
    scene.setdefault("当前危险", [])
    scene.setdefault("光照情况", "未知")
    scene.setdefault("门窗状态", "未知")
    scene.setdefault("声音和气味", [])
    scene.setdefault("玩家已做动作", [])
    memory = state["记忆"]
    memory.setdefault("最近行动", [])
    memory.setdefault("当前场景摘要", "")
    memory.setdefault("重要裁定记录", [])
    state["秘密"].setdefault("已屏蔽条目", [])
    # 地点列表使用归一化名称去重，避免“岛上灯塔”和“灯塔”等重复显示。
    story["已访问地点"] = unique_locations(story.get("已访问地点", []))
    story["当前可前往地点"] = unique_locations(story.get("当前可前往地点", []))
    add_unique(story["已访问地点"], normalize_location_name(current_location))
    sync_available_locations(story["当前可前往地点"], current_location)
    return state


def build_turn_delta(
    story_state: dict[str, Any],
    player_input: str,
    intent: dict[str, Any],
    adjudication: dict[str, Any],
    skill_checks: list[dict[str, Any]],
    sanity_checks: list[dict[str, Any]],
    generated_delta: dict[str, Any],
    generated_clues: list[dict[str, Any]],
    current_location: str,
    current_scene: str,
    location_context: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """构造“本回合发生了什么”的结构化状态增量。

    【中文名称】构建回合状态增量

    【功能说明】
    这是剧情状态流转里非常关键的一步。
    NarratorAgent 和 LLM 可能会产生一些偏自由格式的描述，
    但长期状态不能直接靠自然语言保存，否则后续检索、校验和前端展示都会变得不稳定。
    本函数的职责就是把这些松散信息整理成统一的 delta 字典，明确描述：
    - 地点是否变化
    - 场景是否变化
    - 时间过去了多久
    - 危险等级应不应该上升
    - 本回合调查了什么对象
    - 剧情、场景、记忆三个分区各自新增哪些记录

    【实现方法】
    1. 从 intent 里提取 action_type 和 target。
    2. 从 adjudication 里读取 time_cost_minutes。
    3. 用 infer_danger_delta 推断敌对势力警觉增量。
    4. 从 generated_delta 中提取 location / scene 变化。
    5. 汇总 story_updates、scene_updates、memory_updates 三个分区更新。
    6. 删除空 location / scene 字段，避免把空字符串写进长期状态。

    【为什么先构建 delta 再 apply】
    因为“描述变化”和“真正修改长期状态”是两件不同的事：
    - build_turn_delta 负责标准化变化描述
    - apply_turn_delta 负责把变化真正合并进 story_state
    这种两段式设计更利于 GuardAgent 做校验和修复。

    【返回值】
    - dict: 本回合结构化状态增量，供 GuardAgent 校验、Supervisor 落库
    """
    # 本函数只构造“本回合发生了什么”，真正写入会话状态由 apply_turn_delta 完成。
    # 【状态流程 2】这里把 Agent 和 LLM 的输出整理成标准 delta，避免后面到处解析自由格式文本。
    # 1. 从结构化意图中取出玩家行动目标，例如“码头脚印”“灯塔门口”“救生艇”。
    target = str(intent.get("target") or "").strip()  # target = 本回合调查/移动/交互的目标，去掉首尾空白
    # 2. 从结构化意图中取出行动类型；缺失时按默认调查处理，保证后续状态逻辑有兜底。
    action_type = str(intent.get("action_type") or "调查")  # action_type = 调查/移动/社交/战斗等高层行动类型
    # 3. 从规则裁定结果中读取耗时；没有裁定耗时时按 0 分钟处理。
    time_cost = int(adjudication.get("time_cost_minutes") or 0)  # time_cost = 本回合消耗的游戏内分钟数
    # 4. 根据行动类型、技能检定、理智检定和裁定结果推断危险等级变化。
    danger_delta = infer_danger_delta(action_type, skill_checks, sanity_checks, adjudication)  # danger_delta = 敌对势力警觉增量
    # 5. 从 LLM 生成的 generated_delta 中提取目标地点，兼容中英文字段名。
    target_location = extract_location_delta(generated_delta)  # target_location = 本回合可能移动到的新地点
    # 6. 从 generated_delta 中提取目标场景，场景通常比地点更细。
    target_scene = extract_scene_delta(generated_delta)  # target_scene = 本回合可能进入的新场景/房间/区域
    # 7. 如果 LLM 只给了新地点、没给新场景，并且地点确实变化，则用地点名作为场景兜底。
    if target_location and not target_scene and location_dedupe_key(target_location) != location_dedupe_key(current_location):
        target_scene = target_location  # target_scene = 用新地点兜底，避免“地点变了但场景空白”
    # 8. 构造标准状态增量；这个 delta 还没有写入长期状态，只是描述“本回合发生了什么”。
    delta: dict[str, Any] = {  # delta = 本回合结构化变化，后续会交给 GuardAgent 和 apply_turn_delta
        "location": target_location,  # location = 目标地点；为空表示地点不变
        "scene": target_scene,  # scene = 目标场景；为空表示场景不变或由地点兜底
        "time_cost_minutes": time_cost,  # time_cost_minutes = 本回合耗时
        "danger_delta": danger_delta,  # danger_delta = 危险/警觉等级增量
        "investigated_target": target,  # investigated_target = 本回合调查或交互的目标
        "action_type": action_type,  # action_type = 本回合行动类型
        "story_updates": {  # story_updates = 剧情层更新，影响全局进度和可前往地点
            "visited_location": target_location or current_location,  # visited_location = 本回合访问地点，没移动则记当前地点
            "available_locations": infer_available_locations(player_input, generated_delta, location_context),  # available_locations = 新增或确认的可前往地点
            "triggered_events": infer_triggered_events(player_input, generated_clues),  # triggered_events = 根据行动和新线索推断触发事件
            "flags": build_flags(player_input, intent, skill_checks, sanity_checks),  # flags = 本回合产生的剧情标记
        },
        "scene_updates": {  # scene_updates = 场景层更新，记录当前场景里玩家具体做过什么
            "investigated_object": target,  # investigated_object = 本回合调查过的对象，会进入“已调查对象”
            "action_record": summarize_action(player_input, target, skill_checks, sanity_checks),  # action_record = 本回合行动摘要
        },
        "memory_updates": {  # memory_updates = 记忆层更新，帮助后续回合回顾最近行为和裁定
            "recent_action": summarize_action(player_input, target, skill_checks, sanity_checks),  # recent_action = 最近行动摘要
            "adjudication_record": {  # adjudication_record = 本回合关键裁定记录
                "技能": adjudication.get("skill"),  # 技能 = 本回合建议或使用的技能
                "难度": adjudication.get("difficulty"),  # 难度 = 常规/困难/极难等难度
                "耗时分钟": time_cost,  # 耗时分钟 = 本回合时间成本
                "风险等级": adjudication.get("risk_level"),  # 风险等级 = 规则裁定中的风险评估
            },
        },
    }
    # 9. 如果地点没有变化，就删除 location 字段；这样 apply_turn_delta 会自然保留当前地点。
    if not delta["location"]:
        delta.pop("location")  # 删除空 location，避免把空字符串写入长期状态
    # 10. 如果场景没有变化，就删除 scene 字段；这样 apply_turn_delta 会保留当前场景或用地点兜底。
    if not delta["scene"]:
        delta.pop("scene")  # 删除空 scene，避免把空字符串写入长期状态
    # 11. 返回标准状态增量，交给 NarratorAgent/GuardAgent/Supervisor 后续处理。
    return delta  # 返回本回合变化说明，不直接改 story_state


def extract_location_delta(generated_delta: dict[str, Any]) -> str:
    """从 LLM 生成的增量里尽量提取目标地点。

    【中文名称】提取地点增量

    【功能说明】
    LLM 输出并不总是稳定，有时写 `location`，有时写 `当前地点`，
    还有时把地点塞进嵌套的 `story_updates` 或 `scene_updates` 里。
    本函数会在多套候选字段里兜底查找，并统一做地点名归一化。
    """
    # 兼容 LLM 可能输出的中英文字段名，尽量提取目标地点。
    candidates = [
        generated_delta.get("location"),
        generated_delta.get("current_location"),
        generated_delta.get("当前位置"),
        generated_delta.get("当前地点"),
        generated_delta.get("地点"),
    ]
    for key in ["scene_updates", "story_updates", "location_updates"]:
        nested = generated_delta.get(key)
        if isinstance(nested, dict):
            candidates.extend([
                nested.get("location"),
                nested.get("current_location"),
                nested.get("当前位置"),
                nested.get("当前地点"),
                nested.get("地点"),
                nested.get("visited_location"),
            ])
    for candidate in candidates:
        normalized = normalize_location_name(candidate)
        if normalized:
            return normalized
    return ""


def extract_scene_delta(generated_delta: dict[str, Any]) -> str:
    """从 LLM 生成的增量里尽量提取目标场景。

    【中文名称】提取场景增量

    【功能说明】
    场景通常比地点更细，像“北岸码头仓库门口”“灯塔楼梯间”这样的粒度，
    更适合描述玩家当前具体身处的位置。
    本函数和 extract_location_delta 类似，负责在多种字段名里做兼容提取。
    """
    # 场景比地点更细，用于描述当前房间、码头、走廊等具体处境。
    candidates = [
        generated_delta.get("scene"),
        generated_delta.get("current_scene"),
        generated_delta.get("当前场景"),
        generated_delta.get("场景"),
    ]
    for key in ["scene_updates", "story_updates"]:
        nested = generated_delta.get(key)
        if isinstance(nested, dict):
            candidates.extend([
                nested.get("scene"),
                nested.get("current_scene"),
                nested.get("当前场景"),
                nested.get("场景"),
            ])
    for candidate in candidates:
        normalized = normalize_scene_name(candidate)
        if normalized:
            return normalized
    return ""


def apply_turn_delta(story_state: dict[str, Any], delta: dict[str, Any], current_location: str, current_scene: str, current_time: str) -> dict[str, Any]:
    """把单回合状态增量真正合并进长期剧情状态。

    【中文名称】应用回合状态增量

    【功能说明】
    如果说 build_turn_delta 负责写“变更说明单”，
    那这个函数就是按说明单去真正更新世界状态。
    它会修改长期 story_state 中三个最核心的分区：
    - 剧情：全局进度、访问地点、可前往地点、事件、flag、危险等级
    - 场景：当前地点、当前场景、当前时间、已调查对象、动作历史
    - 记忆：最近行动、重要裁定记录

    【实现方法】
    1. 先调用 ensure_story_state，确保长期状态结构完整。
    2. 计算地点是否真的变化，避免同义地点名反复写入。
    3. 更新剧情层：访问地点、可前往地点、事件、flag、危险等级。
    4. 更新场景层：当前地点/场景/时间、已调查对象、玩家动作历史。
    5. 更新记忆层：最近行动、重要裁定记录。
    6. 返回合并后的完整 story_state。

    【为什么它很关键】
    前面多个 Agent 和 Tool 处理的是“本回合的判断”，
    而这个函数负责把判断沉淀成“下一回合还能继续用的世界状态”。
    没有这一步，游戏就只能停留在单回合问答，而不是连续剧情。

    【返回值】
    - dict: 更新后的长期剧情状态
    """
    # 将结构化增量合并到长期剧情状态，同时推进时间、危险等级和行动记忆。
    # 【状态流程 3】这里才真正修改长期 story_state；修改后的结果会在 agent.py 的 commit_state 中落库。
    # 1. 先补齐 story_state 的基础结构，避免旧存档或空状态缺少“剧情/场景/记忆/秘密”分区。
    state = ensure_story_state(story_state, current_location, current_scene, current_time)  # state = 结构完整的长期剧情状态
    # 2. 取出剧情分区；这里保存全局进度，如已访问地点、可前往地点、flag、警觉等级。
    story = state["剧情"]  # story = 剧情层状态
    # 3. 取出场景分区；这里保存当前地点、当前场景、当前时间、已调查对象等。
    scene = state["场景"]  # scene = 场景层状态
    # 4. 取出记忆分区；这里保存最近行动和重要裁定记录。
    memory = state["记忆"]  # memory = 记忆层状态
    # 5. 计算目标地点：优先使用 delta.location，没有则保留 current_location。
    target_location = normalize_location_name(delta.get("location") or current_location) or current_location  # target_location = 合并后的当前地点
    # 6. 规范化上一地点，用于判断玩家是否真的移动了。
    previous_location = normalize_location_name(current_location) or current_location  # previous_location = 回合开始前地点
    # 7. 用去重 key 比较地点，避免“灯塔”和“岛上灯塔”这种近似名称造成重复移动判断。
    location_changed = location_dedupe_key(target_location) != location_dedupe_key(previous_location)  # location_changed = 本回合地点是否变化
    # 8. 计算目标场景：优先 delta.scene；如果地点变了但没给场景，就用地点名；否则保留原场景。
    target_scene = normalize_scene_name(delta.get("scene")) or (target_location if location_changed else normalize_scene_name(current_scene) or current_scene)  # target_scene = 合并后的当前场景
    # 9. 把目标地点加入已访问地点列表，add_unique 会避免重复。
    add_unique(story["已访问地点"], target_location)  # 已访问地点 += target_location
    # 10. 同步当前可前往地点，确保玩家当前位置也在可前往列表里。
    sync_available_locations(story["当前可前往地点"], target_location)  # 当前可前往地点 += target_location
    # 11. 遍历 delta 中推断出的新可前往地点，逐个同步到剧情状态。
    for location in delta.get("story_updates", {}).get("available_locations", []):
        sync_available_locations(story["当前可前往地点"], location)  # 当前可前往地点 += 新地点
    # 12. 遍历本回合触发事件，写入“已触发事件”列表。
    for event in delta.get("story_updates", {}).get("triggered_events", []):
        add_unique(story["已触发事件"], str(event))  # 已触发事件 += event
    # 13. 合并剧情 flag；同名 flag 会被新值覆盖。
    story["剧情flag"].update(delta.get("story_updates", {}).get("flags", {}))  # 剧情flag.update(flags)
    # 14. 更新敌对势力警觉等级，限制在 1-5，避免数值越界。
    story["敌对势力警觉"] = clamp_int(int(story.get("敌对势力警觉", 1)) + max(0, int(delta.get("danger_delta") or 0)), 1, 5)  # 敌对势力警觉 += danger_delta
    # 15. 写入当前地点；后续 API 会把它同步到 session.current_location。
    scene["当前地点"] = target_location  # 场景.当前地点 = target_location
    # 16. 写入当前场景；后续 API 会把它同步到 session.current_scene。
    scene["当前场景"] = target_scene  # 场景.当前场景 = target_scene
    # 17. 推进游戏内时间；delta.time_cost_minutes 为 0 时表示时间不变。
    scene["当前时间"] = advance_time(str(scene.get("当前时间") or current_time), int(delta.get("time_cost_minutes") or 0))  # 场景.当前时间 += time_cost_minutes
    # 18. 从场景更新中读取被调查对象。
    investigated = str(delta.get("scene_updates", {}).get("investigated_object") or "").strip()  # investigated = 本回合调查对象
    # 19. 如果有调查对象，就加入已调查列表，并从未调查列表中移除。
    if investigated:
        add_unique(scene["已调查对象"], investigated)  # 已调查对象 += investigated
        remove_value(scene["未调查对象"], investigated)  # 未调查对象 -= investigated
    # 20. 从场景更新中读取本回合行动摘要。
    action_record = str(delta.get("scene_updates", {}).get("action_record") or "").strip()  # action_record = 本回合行动摘要
    # 21. 如果行动摘要非空，就写入场景动作历史和短期记忆。
    if action_record:
        append_limited(scene["玩家已做动作"], action_record, 30)  # 玩家已做动作追加，最多保留 30 条
        append_limited(memory["最近行动"], action_record, 10)  # 最近行动追加，最多保留 10 条
    # 22. 从记忆更新中读取规则裁定记录。
    adjudication_record = delta.get("memory_updates", {}).get("adjudication_record")  # adjudication_record = 技能/难度/耗时/风险摘要
    # 23. 只有裁定记录是 dict 时才写入，避免错误类型污染记忆列表。
    if isinstance(adjudication_record, dict):
        append_limited(memory["重要裁定记录"], adjudication_record, 20)  # 重要裁定记录追加，最多保留 20 条
    # 24. 返回合并后的长期剧情状态；真正写入数据库发生在 Supervisor._commit_state()。
    return state  # 返回更新后的 story_state


def infer_available_locations(player_input: str, generated_delta: dict[str, Any], location_context: list[dict[str, Any]] | None = None) -> list[str]:
    """推断当前回合结束后玩家可能还能前往哪些地点。

    【中文名称】推断可前往地点

    【功能说明】
    “当前可前往地点”是前端状态栏和后续引导选项都很依赖的一个字段。
    它不能只依赖单一来源，因为不同回合里，地点信息可能来自：
    1. LLM 生成的 generated_delta
    2. 检索到的地点实体上下文
    3. 玩家输入中直接提到的地点名
    本函数会把这三类来源合并，再统一去重。
    """
    # 可前往地点来自 LLM 增量、结构化实体检索和玩家输入中的明确地点。
    locations: list[str] = []
    append_locations(locations, generated_delta.get("available_locations"))
    story_updates = generated_delta.get("story_updates")
    if isinstance(story_updates, dict):
        append_locations(locations, story_updates.get("available_locations"))
        append_locations(locations, story_updates.get("当前可前往地点"))
        append_locations(locations, story_updates.get("可前往地点"))
    for row in location_context or []:
        location = location_from_context_row(row)
        if location:
            locations.append(location)
    for candidate in ["灯塔小屋", "北岸码头", "灯塔", "宿舍", "书房", "厨房", "灯塔服务室"]:
        if candidate in player_input:
            locations.append(candidate)
    return unique_locations(locations)


def append_locations(locations: list[str], value: Any) -> None:
    if isinstance(value, list):
        locations.extend(normalized for item in value if (normalized := normalize_location_name(item)))
        return
    normalized = normalize_location_name(value)
    if normalized:
        locations.append(normalized)


def location_from_context_row(row: dict[str, Any]) -> str:
    # 只从玩家可见的地点实体中提取名称，避免把隐藏地点提前暴露。
    metadata = row.get("metadata") or {}
    if metadata.get("entity_type") != "地点":
        return ""
    if metadata.get("secret_level") == "主持人秘密":
        return ""
    return str(metadata.get("title") or "").strip()


def unique_locations(locations: list[str]) -> list[str]:
    unique: list[str] = []
    seen: set[str] = set()
    for location in locations:
        value = normalize_location_name(location)
        key = location_dedupe_key(value)
        if value and key not in seen:
            seen.add(key)
            unique.append(value[:120])
    return unique[:12]


def normalize_location_name(value: Any) -> str:
    # 过滤空值、集合对象和常见占位文本，保留可用于展示和去重的地点名。
    if value is None:
        return ""
    if isinstance(value, (dict, list, tuple, set)):
        return ""
    text = str(value).strip(" \t\r\n，。；;:：")
    if not text or text.lower() in {"none", "null", "undefined", "nan"}:
        return ""
    for prefix in ["起点", "当前位置", "当前地点", "地点", "可前往地点"]:
        marker = f"{prefix}："
        if text.startswith(marker):
            text = text.removeprefix(marker).strip(" \t\r\n，。；;:：")
        marker = f"{prefix}:"
        if text.startswith(marker):
            text = text.removeprefix(marker).strip(" \t\r\n，。；;:：")
    return text


def normalize_scene_name(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, (dict, list, tuple, set)):
        return ""
    text = str(value).strip(" \t\r\n，。；;:：")
    if not text or text.lower() in {"none", "null", "undefined", "nan"}:
        return ""
    for prefix in ["当前场景", "场景"]:
        marker = f"{prefix}："
        if text.startswith(marker):
            text = text.removeprefix(marker).strip(" \t\r\n，。；;:：")
        marker = f"{prefix}:"
        if text.startswith(marker):
            text = text.removeprefix(marker).strip(" \t\r\n，。；;:：")
    return text


def location_dedupe_key(value: str) -> str:
    key = normalize_location_name(value)
    for prefix in ["航标岛", "岛上", "岛"]:
        if key.startswith(prefix) and len(key) > len(prefix) + 1:
            key = key.removeprefix(prefix).strip(" 的之-—")
    return key


def infer_triggered_events(player_input: str, generated_clues: list[dict[str, Any]]) -> list[str]:
    events: list[str] = []
    if generated_clues:
        events.append("发现线索")
    if any(word in player_input for word in ["攻击", "开枪", "逃跑", "怪物"]):
        events.append("危险行动")
    return events


def build_flags(player_input: str, intent: dict[str, Any], skill_checks: list[dict[str, Any]], sanity_checks: list[dict[str, Any]]) -> dict[str, Any]:
    """为本回合构造可长期保存的剧情 flag。

    【中文名称】构建剧情标记

    【功能说明】
    flag 是一种轻量、好查、可长期累积的剧情状态记录方式。
    它适合保存“发生过某事”或“最近一次关键结果”这类信息，例如：
    - 是否尝试过调查某个目标
    - 最近一次技能检定结果
    - 最近一次理智检定结果
    - 是否明显在关注灯塔主线

    这些 flag 后续可以被：
    - GuardAgent 做一致性判断
    - NarratorAgent 做引导
    - 前端调试面板做学习展示
    """
    flags: dict[str, Any] = {}
    target = str(intent.get("target") or "").strip()
    if target:
        flags[f"已尝试_{safe_key(target)}"] = True
    if skill_checks:
        flags["最近技能检定"] = skill_checks[-1]
    if sanity_checks:
        flags["最近理智检定"] = sanity_checks[-1]
    if any(word in player_input for word in ["灯塔", "灯"]):
        flags["关注灯塔"] = True
    return flags


def infer_danger_delta(action_type: str, skill_checks: list[dict[str, Any]], sanity_checks: list[dict[str, Any]], adjudication: dict[str, Any]) -> int:
    """根据本回合表现推断敌对势力警觉或整体危险度的增量。

    【中文名称】推断危险增量

    【功能说明】
    项目里的 `danger_delta` 不是直接表示“角色受伤”，
    而更像“世界对玩家行为的反应强度”或“局势变紧张的程度”。
    战斗、失败、SAN 损失、高风险裁定都会让局势趋紧。

    【实现原则】
    - 只增不减：单回合里这里只负责上行压力
    - 单回合封顶 2：避免一次失败把全局难度突然推得过高
    """
    # 危险增量只会上升且单回合封顶，避免一次失败让难度跳变过大。
    delta = 0
    if action_type == "战斗":
        delta += 1
    if skill_checks and not skill_checks[-1].get("success"):
        delta += 1
    if sanity_checks and int(sanity_checks[-1].get("san_loss") or 0) > 0:
        delta += 1
    if int(adjudication.get("risk_level") or 1) >= 4:
        delta += 1
    return min(delta, 2)


def summarize_action(player_input: str, target: str, skill_checks: list[dict[str, Any]], sanity_checks: list[dict[str, Any]]) -> str:
    """把本回合行动压缩成一条便于保存和回看的摘要文本。

    【中文名称】摘要行动

    【功能说明】
    长期记忆和场景动作历史不适合保存整段完整叙事，
    否则冗余太高、难以扫描。
    所以这里会把玩家原始动作、目标、检定结果、理智损失压缩成一行短摘要，
    用于：
    - `场景.玩家已做动作`
    - `记忆.最近行动`
    - 调试和学习时快速回看最近几回合
    """
    parts = [player_input[:120]]
    if target:
        parts.append(f"目标：{target}")
    if skill_checks:
        check = skill_checks[-1]
        parts.append(f"{check.get('skill')} {check.get('roll')}/{check.get('skill_value')} {check.get('success_level')}")
    if sanity_checks:
        parts.append(f"理智损失 {sanity_checks[-1].get('san_loss')}")
    return "；".join(parts)


def advance_time(value: str, minutes: int) -> str:
    # 时间格式正确时按耗时推进；无法解析时保持原值，避免破坏已有存档。
    if minutes <= 0:
        return value
    try:
        parsed = datetime.strptime(value, "%Y-%m-%d %H:%M")
        return (parsed + timedelta(minutes=minutes)).strftime("%Y-%m-%d %H:%M")
    except ValueError:
        return value


def add_unique(items: list[Any], value: Any) -> None:
    if value and value not in items:
        items.append(value)


def remove_value(items: list[Any], value: Any) -> None:
    while value in items:
        items.remove(value)


def append_limited(items: list[Any], value: Any, limit: int) -> None:
    items.append(value)
    del items[:-limit]


def sync_available_locations(items: list[Any], location: str) -> None:
    normalized = normalize_location_name(location)
    if not normalized:
        return
    key = location_dedupe_key(normalized)
    for item in items:
        if location_dedupe_key(str(item)) == key:
            return
    items.append(normalized)


def clamp_int(value: int, minimum: int, maximum: int) -> int:
    return max(minimum, min(maximum, value))
