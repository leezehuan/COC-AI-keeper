/**
 * 【前端类型定义文件】
 * 定义了与后端 API 交互的所有 TypeScript 类型。
 *
 * 类型层次：
 * - 基础实体：Character, Clue, InventoryItem, TurnLog
 * - 检定结果：SkillCheck, SanityCheck
 * - 会话：GameSession（包含角色、线索、物品、回合日志）
 * - 回合响应：ActionResponse（包含叙事、选项、检定结果、线索发现）
 * - 流式事件：ActionStreamEvent（回合流式事件）, AssistantStreamEvent（助手流式事件）
 * - 调试：DebugEvent（实时调试事件）
 * - 助手：AssistantCitation, AssistantChatResponse, AssistantMessage
 * - 聊天：ChatMessage（前端聊天消息）
 */

/** 角色：对应后端 models.Character */
export interface Character {
  id: string;                       // 角色 ID
  scenario_id: string | null;       // 所属剧本 ID
  name: string;                     // 角色名称
  archetype: string;                // 原型（如调查局探员）
  occupation: string | null;        // 职业
  hp_current: number;               // 当前生命值
  hp_max: number;                   // 最大生命值
  san_current: number;              // 当前理智值
  san_max: number;                  // 最大理智值
  mp_current: number;               // 当前魔法值
  mp_max: number;                   // 最大魔法值
  luck: number;                     // 幸运值
  attributes: Record<string, unknown>;  // 属性字典（力量、敏捷等）
  skills: Record<string, number>;       // 技能字典（技能名 -> 技能值）
  inventory: unknown[];                 // 初始物品列表
  background: Record<string, unknown>;  // 背景信息
}

/** 线索：对应后端 models.Clue */
export interface Clue {
  id: string;                       // 线索 ID
  clue_key: string;                 // 线索唯一键（用于去重）
  name: string;                     // 线索名称
  content: string;                  // 线索内容描述
  source_location: string | null;   // 发现地点
  discovered_turn: number;          // 发现回合
  metadata_: Record<string, unknown>; // 元数据
  created_at: string;               // 创建时间
}

/** 物品：对应后端 models.InventoryItem */
export interface InventoryItem {
  id: string;                       // 物品 ID
  item_key: string;                 // 物品唯一键
  name: string;                     // 物品名称
  description: string;              // 物品描述
  quantity: number;                  // 数量
  metadata_: Record<string, unknown>; // 元数据
}

/** 回合日志：对应后端 models.TurnLog */
export interface TurnLog {
  id: string;                       // 日志 ID
  turn_index: number;               // 回合序号
  player_input: string;             // 玩家输入
  intent: Record<string, unknown>;  // 结构化意图
  retrieval: Record<string, unknown>; // 检索调试信息
  dice_results: unknown[];          // 骰点结果
  keeper_response: string;          // 守秘人回应
  state_delta: Record<string, unknown>; // 状态增量
  image_url: string | null;         // 配图 URL
  image_metadata: Record<string, unknown>; // 配图元数据
  created_at: string;               // 创建时间
}

/** 技能检定结果 */
export interface SkillCheck {
  skill: string;                   // 技能名称
  skill_value: number;              // 技能值
  difficulty: string;               // 难度等级
  roll: number;                     // 骰点结果
  success_level: string;            // 成功等级（常规/困难/极难）
  success: boolean;                 // 是否成功
}

/** 理智检定结果 */
export interface SanityCheck {
  check: SkillCheck;                // 基础检定
  loss_roll: {                      // 理智损失掷骰
    expression: string;             // 骰点表达式（如 1d10）
    rolls: number[];                // 各骰结果
    modifier: number;               // 修正值
    total: number;                  // 总计
  };
  san_loss: number;                 // 理智损失量
  san_after: number;                // 检定后理智值
}

/** 游戏会话：对应后端 models.GameSession，包含角色、线索、物品、回合日志 */
export interface GameSession {
  id: string;                       // 会话 ID
  scenario_id: string;              // 剧本 ID
  character_id: string;             // 角色 ID
  title: string;                    // 会话标题
  current_location: string;         // 当前地点
  current_scene: string;            // 当前场景
  current_time: string;             // 当前时间
  story_phase: string;              // 剧情阶段
  danger_level: number;             // 危险等级
  summary: string;                  // 会话摘要
  state: Record<string, unknown>;   // 剧情状态字典
  created_at: string;              // 创建时间
  updated_at: string;              // 更新时间
  character: Character;             // 关联角色
  clues: Clue[];                    // 已发现线索
  inventory_items: InventoryItem[]; // 物品列表
  flags: { key: string; value: Record<string, unknown> }[];  // 标志位
  recent_turns: TurnLog[];          // 最近的回合日志
}

/** 回合响应：KeeperSupervisor.run_turn 结果经后端 build_action_response 整理后的前端结构 */
export interface ActionResponse {
  session: GameSession;             // 更新后的会话
  narration: string;                // 守秘人叙事文本
  options: string[];                // 玩家可选行动
  dice_results: unknown[];          // 骰点结果
  skill_checks: SkillCheck[];      // 技能检定结果
  sanity_checks: SanityCheck[];    // 理智检定结果
  discovered_clues: Clue[];        // 本回合发现的线索
  state_delta: Record<string, unknown>; // 状态增量
  needs_clarification: boolean;    // 是否需要追问
  needs_image: boolean;            // 是否需要配图
  image_aspect_ratio: string;      // 配图宽高比
  image_url: string | null;        // 配图 URL
  image_metadata: Record<string, unknown>; // 配图元数据
}

/** 回合流式事件：/coc/api/sessions/{id}/actions/stream 的 NDJSON 事件类型 */
export type ActionStreamEvent =
  | { type: 'start' }                                              // 回合开始
  | { type: 'debug'; event: DebugEvent }                           // 调试事件
  | { type: 'chunk'; content: string }                             // 叙事文本块
  | { type: 'final'; response: ActionResponse }                    // 最终完整响应
  | { type: 'image'; url: string; turnId: string; metadata?: Record<string, unknown> }  // 配图事件
  | { type: 'error'; detail: string };                            // 错误事件

/** 调试事件：后端 debug_events.py 发出的实时调试信息 */
export interface DebugEvent {
  phase: string;                    // 阶段（agent_node/skill/tool/stream）
  name: string;                     // 组件名称（PlannerAgent/RuleCheckTool 等）
  status: 'start' | 'success' | 'warning' | 'error' | string;  // 状态
  message: string;                  // 简要消息
  timestamp: string;                // 时间戳
  metadata?: Record<string, unknown>; // 附加元数据
}

/** 助手引用：检索结果的来源信息 */
export interface AssistantCitation {
  id: string;                       // 引用 ID
  title: string;                    // 来源标题
  source_type: string;              // 来源类型（rulebook/clue 等）
  citation: string;                 // 引用文本
  snippet: string;                   // 内容片段
}

/** 助手聊天响应：GameAssistantAgent.chat 的返回结果 */
export interface AssistantChatResponse {
  answer: string;                   // 助手回答
  citations: AssistantCitation[];   // 引用列表
  retrieval_debug: Record<string, unknown>; // 检索调试信息
  spoiler_blocked: boolean;         // 是否拦截了剧透
  mode: string;                     // 实际使用的模式
}

/** 助手模式 */
export type AssistantMode = 'auto' | 'rules' | 'session_help';

/** 助手流式事件：/coc/api/assistant/chat/stream 的 NDJSON 事件类型 */
export type AssistantStreamEvent =
  | { type: 'start' }                                              // 对话开始
  | { type: 'debug'; event: DebugEvent }                           // 调试事件
  | { type: 'retrieval'; status: string }                          // 检索状态
  | { type: 'chunk'; content: string }                             // 回答文本块
  | { type: 'citations'; citations: AssistantCitation[] }         // 引用列表
  | { type: 'final'; response: AssistantChatResponse }            // 最终完整响应
  | { type: 'error'; detail: string };                            // 错误事件

/** 助手消息：前端聊天面板中的消息 */
export interface AssistantMessage {
  role: 'assistant' | 'user' | 'system';  // 角色
  content: string;                         // 消息内容
  citations?: AssistantCitation[];         // 引用（仅助手消息）
  spoilerBlocked?: boolean;                // 是否拦截了剧透
}

/** 聊天消息：主聊天面板中的消息（玩家/守秘人/系统） */
export interface ChatMessage {
  role: 'keeper' | 'player' | 'system';  // 角色
  content: string;                         // 消息内容
  meta?: string;                           // 元信息（如检定结果）
  imageUrl?: string;                       // 配图 URL
  imageMetadata?: Record<string, unknown>; // 配图元数据
  imageLoading?: boolean;                  // 配图是否加载中
  imageAspectRatio?: string;               // 配图宽高比
}

/** Agent 监控运行记录 */
export interface AgentTraceRun {
  id: string;
  session_id: string | null;
  source: string;
  status: string;
  metadata_: Record<string, unknown>;
  started_at: string;
  ended_at: string | null;
}

/** Agent 监控步骤记录 */
export interface AgentTraceRecord {
  id: string;
  run_id: string;
  sequence: number;
  session_id: string | null;
  source: string;
  agent_name: string;
  step_name: string;
  phase: string;
  status: string;
  input_payload: Record<string, unknown>;
  output_payload: Record<string, unknown>;
  error: string | null;
  duration_ms: number | null;
  created_at: string;
}

/** Agent 监控配置 */
export interface AgentTraceSettings {
  max_records: number;
  record_count: number;
  run_count: number;
}

/** Agent 监控实时事件 */
export type AgentMonitorEvent =
  | { type: 'start'; timestamp: string }
  | { type: 'heartbeat'; timestamp: string }
  | { type: 'run'; run: AgentTraceRun; timestamp: string }
  | { type: 'record'; record: AgentTraceRecord; timestamp: string }
  | { type: 'settings'; settings: AgentTraceSettings; timestamp: string };
