import type { ActionResponse, ActionStreamEvent, Character, GameSession } from './types';

const apiBase = `${import.meta.env.BASE_URL.replace(/\/$/, '')}/api`;

async function request<T>(url: string, options?: RequestInit): Promise<T> {
  // 普通 JSON 请求封装：统一补充请求头，并把后端错误转换成 Error。
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
    }, onEvent)
};

async function streamRequest(url: string, options: RequestInit, onEvent: (event: ActionStreamEvent) => void): Promise<void> {
  // 流式接口使用 NDJSON：每收到一行 JSON 就立即通知页面更新叙事文本。
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
      onEvent(JSON.parse(text) as ActionStreamEvent);
    }
    if (done) break;
  }
  const remaining = buffer.trim();
  if (remaining) onEvent(JSON.parse(remaining) as ActionStreamEvent);
}
