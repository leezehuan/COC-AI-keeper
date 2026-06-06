import os

def apply_fixes(path, replacements):
    with open(path, 'r', encoding='utf-8') as f:
        content = f.read()
    ok = 0
    for old, new in replacements:
        if old in content:
            content = content.replace(old, new)
            ok += 1
        else:
            print(f'  MISS: {path.split(chr(92))[-1]}: {old[:60]}...')
    with open(path, 'w', encoding='utf-8') as f:
        f.write(content)
    print(f'{path.split(chr(92))[-1]}: {ok}/{len(replacements)} applied')

# ========== context_agent.py ==========
apply_fixes(r'd:\Project\coc-lite\backend\app\services\agents\context_agent.py', [
    # run() method variables
    ('payload = envelope.get("payload", {})',
     'payload = envelope.get("payload", {})  # payload = 负载数据：信封中携带的实际内容'),
    ('db: Session = payload["db"]',
     'db: Session = payload["db"]  # db = 数据库会话：用于查询 PostgreSQL'),
    ('session_id: str = payload["session_id"]',
     'session_id: str = payload["session_id"]  # session_id = 会话ID：标识当前游戏'),
    ('player_input: str = payload["player_input"]',
     'player_input: str = payload["player_input"]  # player_input = 玩家输入：玩家发送的自然语言文本'),
    ('debug_emit: DebugEmitter | None = payload.get("debug_emit")',
     'debug_emit: DebugEmitter | None = payload.get("debug_emit")  # debug_emit = 调试发射器：向前端发送实时调试事件'),

    # session loading
    ('session = (\n            db.query(models.GameSession)',
     'session = (  # session = 游戏会话：从数据库加载的 GameSession ORM 对象\n            db.query(models.GameSession)'),
    ('character = session.character',
     'character = session.character  # character = 角色：当前玩家的 Character ORM 对象'),
    ('story_state = ensure_story_state(',
     'story_state = ensure_story_state(  # story_state = 剧情状态：结构完整的游戏世界状态字典'),

    # intent parsing
    ('intent = self._parse_intent(session, player_input, debug_emit)',
     'intent = self._parse_intent(session, player_input, debug_emit)  # intent = 意图：解析后的结构化意图字典'),

    # visible context
    ('visible_context = {',
     'visible_context = {  # visible_context = 可见上下文：玩家能看到的信息（地点/场景/物品/已知线索）'),
    ('keeper_only_context = {"story_state": story_state}  # 完整剧情状态（含隐藏信息）',
     'keeper_only_context = {"story_state": story_state}  # keeper_only_context = 守秘人上下文：含隐藏信息，玩家不可见'),

    # RAG retrieval
    ('scenario_context, entity_context, clue_context, memory_context, rule_context = self._retrieve_context(',
     'scenario_context, entity_context, clue_context, memory_context, rule_context = self._retrieve_context(  # 五个检索结果'),

    # _parse_intent variables
    ('fallback = heuristic_intent(player_input)  # 启发式回退意图',
     'fallback = heuristic_intent(player_input)  # fallback = 回退意图：基于关键词匹配的意图（LLM失败时使用）'),
    ('clarification_context = self._build_clarification_context(session)  # 追问上下文',
     'clarification_context = self._build_clarification_context(session)  # clarification_context = 追问上下文：上一轮追问信息'),
    ('prompt = build_intent_prompt(session.current_location, session.current_scene, player_input, clarification_context)',
     'prompt = build_intent_prompt(session.current_location, session.current_scene, player_input, clarification_context)  # prompt = 提示词：发给LLM的意图解析指令'),
    ('parsed = self.context.llm.chat_json(prompt, fallback=fallback)  # LLM 解析意图',
     'parsed = self.context.llm.chat_json(prompt, fallback=fallback)  # parsed = LLM解析结果：LLM返回的结构化意图'),

    # _build_clarification_context
    ('latest_log = max(session.turn_logs, key=lambda log: log.turn_index)  # 最近一条日志',
     'latest_log = max(session.turn_logs, key=lambda log: log.turn_index)  # latest_log = 最新日志：最近一条回合日志'),

    # _retrieve_context
    ('query = " ".join([',
     'query = " ".join([  # query = 检索查询：拼接地点/场景/玩家输入/目标/技能'),
    ('retrieval = self.context.retrieval',
     'retrieval = self.context.retrieval  # retrieval = 检索服务：用于查询 ChromaDB 向量库'),
    ('scenario_context: list[dict[str, Any]] = []  # 剧本片段',
     'scenario_context: list[dict[str, Any]] = []  # scenario_context = 剧本片段：从 ChromaDB 检索的剧情描述'),
    ('entity_context: list[dict[str, Any]] = []  # 实体信息',
     'entity_context: list[dict[str, Any]] = []  # entity_context = 实体信息：场景中的 NPC/物品/地点'),
    ('clue_context: list[dict[str, Any]] = []  # 线索索引',
     'clue_context: list[dict[str, Any]] = []  # clue_context = 线索索引：可被发现的线索列表'),
    ('memory_context: list[dict[str, Any]] = []  # 会话记忆',
     'memory_context: list[dict[str, Any]] = []  # memory_context = 会话记忆：之前回合的记录'),
    ('rule_context: list[dict[str, Any]] = []  # 规则片段',
     'rule_context: list[dict[str, Any]] = []  # rule_context = 规则片段：相关的游戏规则'),
])

# ========== planner_agent.py ==========
apply_fixes(r'd:\Project\coc-lite\backend\app\services\agents\planner_agent.py', [
    ('payload = envelope.get("payload", {})',
     'payload = envelope.get("payload", {})  # payload = 负载数据'),
    ('visible_context: dict[str, Any] = payload.get("visible_context", {})',
     'visible_context: dict[str, Any] = payload.get("visible_context", {})  # visible_context = 可见上下文'),
    ('intent: dict[str, Any] = payload.get("intent", {})',
     'intent: dict[str, Any] = payload.get("intent", {})  # intent = 结构化意图'),
    ('player_input: str = payload.get("player_input", "")',
     'player_input: str = payload.get("player_input", "")  # player_input = 玩家输入'),
    ('debug_emit: DebugEmitter | None = payload.get("debug_emit")',
     'debug_emit: DebugEmitter | None = payload.get("debug_emit")  # debug_emit = 调试发射器'),

    ('partial_state = {',
     'partial_state = {  # partial_state = 部分状态：用于构建回退计划'),
    ('fallback = fallback_turn_plan(partial_state)',
     'fallback = fallback_turn_plan(partial_state)  # fallback = 回退计划：LLM失败时使用的默认计划'),

    ('prompt = build_turn_plan_prompt(',
     'prompt = build_turn_plan_prompt(  # prompt = 提示词：发给LLM的计划生成指令'),

    ('generated = self.context.llm.chat_json(prompt, fallback=fallback)  # LLM 生成计划',
     'generated = self.context.llm.chat_json(prompt, fallback=fallback)  # generated = LLM生成结果：LLM返回的回合计划'),
    ('plan = normalize_turn_plan(generated if isinstance(generated, dict) else {}, fallback)',
     'plan = normalize_turn_plan(generated if isinstance(generated, dict) else {}, fallback)  # plan = 回合计划：规范化后的执行计划'),
    ('needs_clarification = bool(plan.get("needs_clarification"))',
     'needs_clarification = bool(plan.get("needs_clarification"))  # needs_clarification = 需要追问：玩家输入是否太模糊'),

    ('valid_tools = set(available_tool_names())  # 系统中所有合法 Tool 名称',
     'valid_tools = set(available_tool_names())  # valid_tools = 合法Tool集合：系统中所有可用的Tool名称'),
    ('valid_skills = set(SKILL_SPECS.keys())  # 系统中所有合法 Skill 名称',
     'valid_skills = set(SKILL_SPECS.keys())  # valid_skills = 合法Skill集合：系统中所有可用的Skill名称'),
    ('requested_tools = [str(item) for item in ensure_list(plan.get("allowed_tools"))]  # 计划请求的 Tool',
     'requested_tools = [str(item) for item in ensure_list(plan.get("allowed_tools"))]  # requested_tools = 请求的Tool：LLM计划中指定的Tool'),
    ('requested_skills = [str(item) for item in ensure_list(plan.get("allowed_skills"))]  # 计划请求的 Skill',
     'requested_skills = [str(item) for item in ensure_list(plan.get("allowed_skills"))]  # requested_skills = 请求的Skill：LLM计划中指定的Skill'),
    ('allowed_tools = [item for item in requested_tools if item in valid_tools]  # 过滤出合法 Tool',
     'allowed_tools = [item for item in requested_tools if item in valid_tools]  # allowed_tools = 合法Tool：白名单过滤后的Tool列表'),
    ('allowed_skills = [item for item in requested_skills if item in valid_skills]  # 过滤出合法 Skill',
     'allowed_skills = [item for item in requested_skills if item in valid_skills]  # allowed_skills = 合法Skill：白名单过滤后的Skill列表'),
    ('issues: list[str] = []',
     'issues: list[str] = []  # issues = 问题列表：白名单校验中发现的问题'),
    ('risk_level = clamp_int(to_int(plan.get("risk_level"), 1), 1, 5)  # 风险等级 1-5',
     'risk_level = clamp_int(to_int(plan.get("risk_level"), 1), 1, 5)  # risk_level = 风险等级：1-5，数值越高越危险'),
    ('plan_validation = {',
     'plan_validation = {  # plan_validation = 计划校验结果：包含校验问题和最终Tool/Skill列表'),
])

print('\nDone with agents 1-2')
