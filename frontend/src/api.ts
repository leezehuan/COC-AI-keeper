import type {
  ActionResponse,
  ActionStreamEvent,
  AgentMonitorEvent,
  AgentTraceRecord,
  AgentTraceRun,
  AgentTraceSettings,
  AssistantChatResponse,
  AssistantMode,
  AssistantStreamEvent,
  Character,
  GameSession,
} from './types';

// =============================================================================
// 【前端 API 封装层】
// =============================================================================
// 这个文件是浏览器和后端之间的“翻译层”：
// 1. App.tsx 不直接写 fetch URL，而是调用这里的 api.characters / api.streamAction 等方法。
// 2. 这里统一拼接 /api 路径、设置 JSON 请求头、解析错误。
// 3. 对流式接口，streamRequest 会一行一行读取后端返回的 NDJSON 事件。
// =============================================================================
const apiBase = `${import.meta.env.BASE_URL.replace(/\/$/, '')}/api`;

async function request<T>(url: string, options?: RequestInit): Promise<T> {
  /** 普通 JSON 请求封装（request = 请求）。
   *
   * 【中文名称】请求
   * 【功能说明】统一补充请求头，发送 fetch 请求，把后端错误转换成 Error 抛出。
   * T 是 TypeScript 泛型，用来告诉调用方“这次请求成功后会返回什么类型的数据”。
   */
  const response = await fetch(url, {
    headers: { 'Content-Type': 'application/json', ...(options?.headers ?? {}) },
    ...options
  });
  if (!response.ok) {
    const detail = await response.text();
    throw new Error(parseErrorMessage(detail, response.status));
  }
  return response.json() as Promise<T>;
}

function parseErrorMessage(detail: string, status: number): string {
  /** 解析错误消息（parseErrorMessage = 解析错误消息）。
   *
   * 【中文名称】解析错误消息
   * 【功能说明】FastAPI 通常把业务错误放在 detail 字段；解析失败时保留原始响应文本。
   */
  if (!detail) return `请求失败，状态码：${status}`;
  try {
    const payload = JSON.parse(detail) as { detail?: unknown };
    if (typeof payload.detail === 'string') return payload.detail;
  } catch {
    return detail;
  }
  return detail;
}

export const api = {
  // 页面层只调用这些语义化方法，不直接拼接后端路径。
  characters: () => request<Character[]>(`${apiBase}/characters`),
  sessions: () => request<GameSession[]>(`${apiBase}/sessions`),
  getSession: (sessionId: string) => request<GameSession>(`${apiBase}/sessions/${sessionId}`),
  deleteSession: (sessionId: string) => request<{ status: string; deleted_memory_chunks: number }>(`${apiBase}/sessions/${sessionId}`, { method: 'DELETE' }),
  createSession: (characterId?: string) =>
    request<GameSession>(`${apiBase}/sessions`, {
      method: 'POST',
      body: JSON.stringify({ character_id: characterId ?? null, title: '无光的灯塔' })
    }),
  sendAction: (sessionId: string, message: string) =>
    request<ActionResponse>(`${apiBase}/sessions/${sessionId}/actions`, {
      method: 'POST',
      body: JSON.stringify({ message })
    }),
  streamAction: (sessionId: string, message: string, onEvent: (event: ActionStreamEvent) => void) =>
    streamRequest(`${apiBase}/sessions/${sessionId}/actions/stream`, {
      method: 'POST',
      body: JSON.stringify({ message })
    }, onEvent),
  assistantChat: (payload: AssistantChatPayload) =>
    request<AssistantChatResponse>(`${apiBase}/assistant/chat`, {
      method: 'POST',
      body: JSON.stringify(payload)
    }),
  streamAssistantChat: (payload: AssistantChatPayload, onEvent: (event: AssistantStreamEvent) => void) =>
    streamRequest(`${apiBase}/assistant/chat/stream`, {
      method: 'POST',
      body: JSON.stringify(payload)
    }, onEvent),
  monitorSettings: () => request<AgentTraceSettings>(`${apiBase}/monitor/settings`),
  updateMonitorSettings: (maxRecords: number) =>
    request<AgentTraceSettings>(`${apiBase}/monitor/settings`, {
      method: 'PUT',
      body: JSON.stringify({ max_records: maxRecords })
    }),
  monitorRuns: (filters: MonitorRunFilters = {}) =>
    request<AgentTraceRun[]>(`${apiBase}/monitor/runs${queryString(filters)}`),
  monitorRecords: (filters: MonitorRecordFilters = {}) =>
    request<AgentTraceRecord[]>(`${apiBase}/monitor/records${queryString(filters)}`),
  deleteMonitorRecord: (recordId: string) =>
    request<{ status: string; deleted: number }>(`${apiBase}/monitor/records/${recordId}`, { method: 'DELETE' }),
  deleteMonitorRun: (runId: string) =>
    request<{ status: string; deleted_runs: number; deleted_records: number }>(`${apiBase}/monitor/runs/${runId}`, { method: 'DELETE' }),
  deleteMonitorRecords: (filters: MonitorRecordFilters = {}) =>
    request<{ status: string; deleted: number }>(`${apiBase}/monitor/records${queryString(filters)}`, { method: 'DELETE' }),
  streamMonitorEvents: (onEvent: (event: AgentMonitorEvent) => void, signal?: AbortSignal) =>
    streamGet(`${apiBase}/monitor/events/stream`, onEvent, signal)
};

export interface MonitorRunFilters {
  session_id?: string;
  source?: string;
  status?: string;
  limit?: number;
  offset?: number;
}

export interface MonitorRecordFilters {
  run_id?: string;
  session_id?: string;
  agent_name?: string;
  source?: string;
  status?: string;
  limit?: number;
  offset?: number;
}

export interface AssistantChatPayload {
  session_id?: string | null;        // 当前会话 ID（可选，绑定会话时可检索会话记忆）
  message: string;                   // 玩家问题
  mode?: AssistantMode;              // 助手模式（auto/rules/session_help）
  enable_mqe?: boolean;              // 是否启用 MQE 查询扩展
  mqe_expansions?: number;           // MQE 扩展查询数量
  enable_hyde?: boolean | null;       // 是否启用 HyDE（null=自动）
  top_k?: number;                    // 检索结果数量
  candidate_pool_multiplier?: number; // 候选池倍数
}

async function streamRequest<TEvent>(url: string, options: RequestInit, onEvent: (event: TEvent) => void): Promise<void> {
  /** 流式请求（streamRequest = 流式请求）。
   *
   * 【中文名称】流式请求
   * 【功能说明】使用 NDJSON 格式边下载边解析，每收到一行 JSON 就立即回调 onEvent。
   * 对 Web 初学者来说，可以把它理解为“边下载边解析”，不用等整段回复全部生成完。
   */
  const response = await fetch(url, {
    headers: { 'Content-Type': 'application/json', ...(options.headers ?? {}) },
    ...options
  });
  if (!response.ok) {
    const detail = await response.text();
    throw new Error(parseErrorMessage(detail, response.status));
  }
  if (!response.body) throw new Error('浏览器不支持流式响应');
  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  let buffer = '';
  while (true) {
    // buffer 保存半行数据，避免网络分片导致 JSON 被截断后解析失败。
    const { value, done } = await reader.read();
    buffer += decoder.decode(value ?? new Uint8Array(), { stream: !done });
    const lines = buffer.split('\n');
    buffer = lines.pop() ?? '';
    for (const line of lines) {
      const text = line.trim();
      if (!text) continue;
      onEvent(JSON.parse(text) as TEvent);
    }
    if (done) break;
  }
  const remaining = buffer.trim();
  if (remaining) onEvent(JSON.parse(remaining) as TEvent);
}

async function streamGet<TEvent>(url: string, onEvent: (event: TEvent) => void, signal?: AbortSignal): Promise<void> {
  return streamRequest<TEvent>(url, { method: 'GET', headers: {}, signal }, onEvent);
}

function queryString(filters: object): string {
  const params = new URLSearchParams();
  Object.entries(filters as Record<string, unknown>).forEach(([key, value]) => {
    if (value === undefined || value === null || value === '') return;
    params.set(key, String(value));
  });
  const text = params.toString();
  return text ? `?${text}` : '';
}
