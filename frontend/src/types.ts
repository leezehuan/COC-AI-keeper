export interface Character {
  id: string;
  scenario_id: string | null;
  name: string;
  archetype: string;
  occupation: string | null;
  hp_current: number;
  hp_max: number;
  san_current: number;
  san_max: number;
  mp_current: number;
  mp_max: number;
  luck: number;
  attributes: Record<string, unknown>;
  skills: Record<string, number>;
  inventory: unknown[];
  background: Record<string, unknown>;
}

export interface Clue {
  id: string;
  clue_key: string;
  name: string;
  content: string;
  source_location: string | null;
  discovered_turn: number;
  metadata_: Record<string, unknown>;
  created_at: string;
}

export interface InventoryItem {
  id: string;
  item_key: string;
  name: string;
  description: string;
  quantity: number;
  metadata_: Record<string, unknown>;
}

export interface TurnLog {
  id: string;
  turn_index: number;
  player_input: string;
  intent: Record<string, unknown>;
  retrieval: Record<string, unknown>;
  dice_results: unknown[];
  keeper_response: string;
  state_delta: Record<string, unknown>;
  created_at: string;
}

export interface SkillCheck {
  skill: string;
  skill_value: number;
  difficulty: string;
  roll: number;
  success_level: string;
  success: boolean;
}

export interface SanityCheck {
  check: SkillCheck;
  loss_roll: {
    expression: string;
    rolls: number[];
    modifier: number;
    total: number;
  };
  san_loss: number;
  san_after: number;
}

export interface GameSession {
  id: string;
  scenario_id: string;
  character_id: string;
  title: string;
  current_location: string;
  current_scene: string;
  current_time: string;
  story_phase: string;
  danger_level: number;
  summary: string;
  state: Record<string, unknown>;
  created_at: string;
  updated_at: string;
  character: Character;
  clues: Clue[];
  inventory_items: InventoryItem[];
  flags: { key: string; value: Record<string, unknown> }[];
  recent_turns: TurnLog[];
}

export interface ActionResponse {
  session: GameSession;
  narration: string;
  options: string[];
  dice_results: unknown[];
  skill_checks: SkillCheck[];
  sanity_checks: SanityCheck[];
  discovered_clues: Clue[];
  state_delta: Record<string, unknown>;
  needs_clarification: boolean;
}

export type ActionStreamEvent =
  | { type: 'start' }
  | { type: 'debug'; event: DebugEvent }
  | { type: 'chunk'; content: string }
  | { type: 'final'; response: ActionResponse }
  | { type: 'error'; detail: string };

export interface DebugEvent {
  phase: string;
  name: string;
  status: 'start' | 'success' | 'warning' | 'error' | string;
  message: string;
  timestamp: string;
  metadata?: Record<string, unknown>;
}

export interface AssistantCitation {
  id: string;
  title: string;
  source_type: string;
  citation: string;
  snippet: string;
}

export interface AssistantChatResponse {
  answer: string;
  citations: AssistantCitation[];
  retrieval_debug: Record<string, unknown>;
  spoiler_blocked: boolean;
  mode: string;
}

export type AssistantMode = 'auto' | 'rules' | 'session_help';

export type AssistantStreamEvent =
  | { type: 'start' }
  | { type: 'debug'; event: DebugEvent }
  | { type: 'retrieval'; status: string }
  | { type: 'chunk'; content: string }
  | { type: 'citations'; citations: AssistantCitation[] }
  | { type: 'final'; response: AssistantChatResponse }
  | { type: 'error'; detail: string };

export interface AssistantMessage {
  role: 'assistant' | 'user' | 'system';
  content: string;
  citations?: AssistantCitation[];
  spoilerBlocked?: boolean;
}

export interface ChatMessage {
  role: 'keeper' | 'player' | 'system';
  content: string;
  meta?: string;
}
