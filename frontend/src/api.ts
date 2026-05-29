import type { ActionResponse, ActionStreamEvent, AssistantChatResponse, AssistantMode, AssistantStreamEvent, Character, GameSession } from './types';

// 【阅读顺序 2：前端 API 封装】
// 这个文件是浏览器和后端之间的“翻译层”：
// 1. App.tsx 不直接写 fetch URL，而是调用这里的 api.characters / api.streamAction 等方法。
// 2. 这里统一拼接 /api 路径、设置 JSON 请求头、解析错误。
// 3. 对流式接口，streamRequest 会一行一行读取后端返回的 NDJSON 事件。
const apiBase = `${import.meta.env.BASE_URL.replace(/\/$/, '')}/api`;

async function request<T>(url: string, options?: RequestInit): Promise<T> {
  // 普通 JSON 请求封装：统一补充请求头，并把后端错误转换成 Error。
  // T 是 TypeScript 泛型，用来告诉调用方“这次请求成功后会返回什么类型的数据”。
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
  // FastAPI 通常把业务错误放在 detail 字段；解析失败时保留原始响应文本。
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
    }, onEvent)
};

export interface AssistantChatPayload {
  session_id?: string | null;
  message: string;
  mode?: AssistantMode;
  enable_mqe?: boolean;
  mqe_expansions?: number;
  enable_hyde?: boolean | null;
  top_k?: number;
  candidate_pool_multiplier?: number;
}

async function streamRequest<TEvent>(url: string, options: RequestInit, onEvent: (event: TEvent) => void): Promise<void> {
  // 流式接口使用 NDJSON：每收到一行 JSON 就立即通知页面更新叙事文本。
  // 对 Web 初学者来说，可以把它理解为“边下载边解析”，不用等整段守秘人回复全部生成完。
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
