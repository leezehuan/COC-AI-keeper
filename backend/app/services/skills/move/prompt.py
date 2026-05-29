SKILL_PROMPT = """MoveSkill 用于处理前往新地点、进入房间、离开区域等移动行动。

约束：
1. 只能读取当前场景 affordance 和可见地点信息。
2. 不能直接改变 GameSession.current_location。
3. 若目标地点不可达，应返回世界内限制或澄清需求。
4. 地点变化只能作为候选 state_delta，由后续 guardrails 校验。"""
