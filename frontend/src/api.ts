import type { ActionResponse, Character, GameSession } from './types';

async function request<T>(url: string, options?: RequestInit): Promise<T> {
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
  init: () => request<{ status: string }>('/api/init', { method: 'POST' }),
  importContent: (resetChroma = false) =>
    request<{ scenario_id: string; scenario_chunks: number; rule_chunks: number; scenario_entities: number; clue_index: number; characters: number }>('/api/import', {
      method: 'POST',
      body: JSON.stringify({ reset_chroma: resetChroma, import_characters: true })
    }),
  characters: () => request<Character[]>('/api/characters'),
  sessions: () => request<GameSession[]>('/api/sessions'),
  getSession: (sessionId: string) => request<GameSession>(`/api/sessions/${sessionId}`),
  deleteSession: (sessionId: string) => request<{ status: string; deleted_memory_chunks: number }>(`/api/sessions/${sessionId}`, { method: 'DELETE' }),
  createSession: (characterId?: string) =>
    request<GameSession>('/api/sessions', {
      method: 'POST',
      body: JSON.stringify({ character_id: characterId ?? null, title: '无光的灯塔' })
    }),
  sendAction: (sessionId: string, message: string) =>
    request<ActionResponse>(`/api/sessions/${sessionId}/actions`, {
      method: 'POST',
      body: JSON.stringify({ message })
    })
};
