import { FormEvent, useEffect, useMemo, useState } from 'react';
import { api } from './api';
import type { ActionResponse, Character, ChatMessage, GameSession, InventoryItem } from './types';

const assetBase = `${import.meta.env.BASE_URL.replace(/\/$/, '')}/assets`;

const openingText = '现在是 1926 年四月十二日，晚上八点十五分左右。航标岛上的灯塔在暴风雨前熄灭，埃塞克斯号触礁沉没。你坐在救生艇里，黑暗的海面拍打船舷，远处只有灯塔底部透出微弱的光。';

export default function App() {
  const [characters, setCharacters] = useState<Character[]>([]);
  const [savedSessions, setSavedSessions] = useState<GameSession[]>([]);
  const [selectedSession, setSelectedSession] = useState('');
  const [selectedCharacter, setSelectedCharacter] = useState('');
  const [session, setSession] = useState<GameSession | null>(null);
  const [messages, setMessages] = useState<ChatMessage[]>([{ role: 'keeper', content: openingText }]);
  const [options, setOptions] = useState<string[]>(['观察海面和灯塔', '划向北岸码头', '检查救生艇', '自定义行动']);
  const [input, setInput] = useState('');
  const [busy, setBusy] = useState(false);
  const [status, setStatus] = useState('等待导入资料');
  const [error, setError] = useState('');

  useEffect(() => {
    void loadCharacters();
    void loadSessions();
  }, []);

  const sortedSkills = useMemo<[string, number][]>(() => {
    if (!session) return [];
    return (Object.entries(session.character.skills) as [string, number][])
      .sort((a, b) => b[1] - a[1])
      .slice(0, 10);
  }, [session]);
  const storyState = asRecord(session?.state['剧情']);
  const sceneState = asRecord(session?.state['场景']);
  const memoryState = asRecord(session?.state['记忆']);
  const visitedLocations = asStringArray(storyState['已访问地点']);
  const availableLocations = asStringArray(storyState['当前可前往地点']);
  const investigatedObjects = asStringArray(sceneState['已调查对象']);
  const recentActions = asStringArray(memoryState['最近行动']).slice(-5);
  const coreAttributes = buildAttributeRows(session?.character.attributes);
  const derivedAttributes = asRecord(asRecord(session?.character.attributes)['派生属性']);
  const storyFlags = formatFlagEntries(storyState['剧情flag']);
  const auditEntries = formatAuditEntries(asRecord(session?.state['last_audit']));
  const debugEntries = [
    `危险等级：${String(session?.danger_level ?? '无')}`,
    `时间压力：${String(storyState['时间压力'] ?? '普通')}`,
    `仪式进度：${String(storyState['仪式进度'] ?? 0)}`,
    `结局倾向：${String(storyState['结局倾向'] ?? '未定')}`,
    `连续无新线索回合：${String(memoryState['连续无新线索回合'] ?? 0)}`,
    ...storyFlags.map((item) => `剧情 flag：${item}`),
    ...auditEntries,
  ];

  async function loadCharacters() {
    try {
      const list = await api.characters();
      setCharacters(list);
      const defaultCharacter = list.find((item) => item.archetype === '调查局探员') ?? list[0];
      if (defaultCharacter) setSelectedCharacter(defaultCharacter.id);
      setStatus(list.length ? '已加载角色' : '请在服务器命令行导入资料');
    } catch {
      setStatus('请在服务器命令行初始化数据库并导入资料');
    }
  }

  async function loadSessions() {
    try {
      const list = await api.sessions();
      setSavedSessions(list);
      if (list[0] && !list.some((item) => item.id === selectedSession)) setSelectedSession(list[0].id);
      if (!list.length) setSelectedSession('');
    } catch {
      setSavedSessions([]);
      setSelectedSession('');
    }
  }

  async function startSession() {
    setBusy(true);
    setError('');
    try {
      const created = await api.createSession(selectedCharacter || undefined);
      setSession(created);
      setMessages([{ role: 'keeper', content: openingText }]);
      setOptions(['观察海面和灯塔', '划向北岸码头', '检查救生艇', '自定义行动']);
      setStatus(`会话已创建：${created.character.archetype}`);
      await loadSessions();
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setBusy(false);
    }
  }

  async function resumeSession() {
    if (!selectedSession || busy) return;
    await resumeSessionById(selectedSession);
  }

  async function resumeSessionById(sessionId: string) {
    if (!sessionId || busy) return;
    setBusy(true);
    setError('');
    try {
      const restored = await api.getSession(sessionId);
      setSession(restored);
      setSelectedSession(restored.id);
      setMessages(buildMessagesFromSession(restored));
      const lastOptions = normalizeOptions(restored.state['last_options']);
      setOptions(lastOptions.length ? lastOptions : normalizeOptions(['继续调查', '查看角色状态', '自定义行动']));
      const lastTurn = restored.recent_turns[restored.recent_turns.length - 1];
      setStatus(`已恢复会话：${restored.title}，第 ${lastTurn?.turn_index ?? 0} 回合`);
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setBusy(false);
    }
  }

  async function deleteSavedSession(sessionId: string) {
    if (!sessionId || busy) return;
    setBusy(true);
    setError('');
    try {
      const result = await api.deleteSession(sessionId);
      const remaining = savedSessions.filter((item) => item.id !== sessionId);
      setSavedSessions(remaining);
      setSelectedSession(remaining[0]?.id ?? '');
      if (session?.id === sessionId) {
        resetCurrentSession();
      }
      setStatus(`已删除历史会话，清理会话记忆 ${result.deleted_memory_chunks} 条`);
      void loadSessions();
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setBusy(false);
    }
  }

  function resetCurrentSession() {
    setSession(null);
    setMessages([{ role: 'keeper', content: openingText }]);
    setOptions(['观察海面和灯塔', '划向北岸码头', '检查救生艇', '自定义行动']);
    setInput('');
  }

  async function send(message: string) {
    const content = message.trim();
    if (!content || !session || busy) return;
    setBusy(true);
    setError('');
    setInput('');
    setMessages((prev) => [...prev, { role: 'player', content }, { role: 'keeper', content: '' }]);
    try {
      await api.streamAction(session.id, content, (event) => {
        if (event.type === 'chunk') {
          appendToLastKeeperMessage(event.content);
        } else if (event.type === 'final') {
          applyActionResponse(event.response);
        } else if (event.type === 'error') {
          throw new Error(event.detail);
        }
      });
    } catch (err) {
      const detail = err instanceof Error ? err.message : String(err);
      setError(detail);
      setMessages((prev) => [...prev, { role: 'system', content: `行动处理失败：${detail}` }]);
    } finally {
      setBusy(false);
    }
  }

  function appendToLastKeeperMessage(chunk: string) {
    setMessages((prev) => {
      const next = [...prev];
      const index = next.length - 1;
      if (index >= 0 && next[index].role === 'keeper') {
        next[index] = { ...next[index], content: `${next[index].content}${chunk}` };
      }
      return next;
    });
  }

  function applyActionResponse(response: ActionResponse) {
    setSession(response.session);
    setOptions(normalizeOptions(response.options));
    const meta = buildActionMeta(response);
    setMessages((prev) => {
      const next = [...prev];
      const index = next.length - 1;
      if (index >= 0 && next[index].role === 'keeper') {
        next[index] = { ...next[index], content: response.narration, meta };
      }
      return next;
    });
  }

  function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    void send(input);
  }

  return (
    <div className="app-shell">
      <header className="topbar">
        <div>
          <p className="eyebrow">守秘人记录</p>
          <h1>《无光的灯塔》</h1>
        </div>
        <div className="toolbar">
          <details className="picker-panel character-panel">
            <summary>{selectedCharacterLabel(characters, selectedCharacter)}</summary>
            <div className="picker-list character-picker">
              {characters.length === 0 ? <span className="picker-empty">暂无角色</span> : characters.map((character) => (
                <button
                  className={`picker-item character-chip ${selectedCharacter === character.id ? 'active' : ''}`}
                  key={character.id}
                  onClick={() => setSelectedCharacter(character.id)}
                  disabled={busy}
                >
                  <span>{character.archetype}</span>
                  <small>{character.occupation || '调查员'}</small>
                </button>
              ))}
            </div>
          </details>
          <button className="primary" onClick={startSession} disabled={busy || !characters.length}>开始会话</button>
          <details className="session-panel">
            <summary>历史会话 {savedSessions.length ? `(${savedSessions.length})` : ''}</summary>
            <div className="session-picker">
              {savedSessions.length === 0 ? <span className="session-empty">暂无会话</span> : savedSessions.map((item) => (
                <button
                  className={`session-chip ${selectedSession === item.id ? 'active' : ''}`}
                  key={item.id}
                  onClick={() => void resumeSessionById(item.id)}
                  disabled={busy}
                >
                  <span>{item.title} · {item.current_location}</span>
                  <small>{formatDateTime(item.updated_at)}</small>
                  <span
                    className="delete-session"
                    role="button"
                    tabIndex={0}
                    onClick={(event) => {
                      event.stopPropagation();
                      void deleteSavedSession(item.id);
                    }}
                    onKeyDown={(event) => {
                      if (event.key === 'Enter' || event.key === ' ') {
                        event.preventDefault();
                        event.stopPropagation();
                        void deleteSavedSession(item.id);
                      }
                    }}
                  >
                    ×
                  </span>
                </button>
              ))}
            </div>
          </details>
        </div>
      </header>

      {error && <div className="error">{error}</div>}
      <div className="status">{busy ? '处理中...' : status}</div>

      <main className="layout">
        <aside className="left-panel">
          <section className="card">
            <h2>角色状态</h2>
            {session ? (
              <>
                <h3>{session.character.archetype}</h3>
                <div className="stats-grid">
                  <Stat label="生命值" value={`${session.character.hp_current}/${session.character.hp_max}`} />
                  <Stat label="理智值" value={`${session.character.san_current}/${session.character.san_max}`} />
                  <Stat label="魔法值" value={`${session.character.mp_current}/${session.character.mp_max}`} />
                  <Stat label="幸运" value={String(session.character.luck)} />
                  <Stat label="伤害加值" value={String(derivedAttributes['伤害加值'] ?? '0')} />
                  <Stat label="体格" value={String(derivedAttributes['体格'] ?? 0)} />
                  <Stat label="移动力" value={String(derivedAttributes['MOV'] ?? '无')} />
                </div>
                <AttributeTable rows={coreAttributes} />
                <p className="location">当前位置：{session.current_location}</p>
                <p className="location">当前场景：{session.current_scene}</p>
                <p className="location">当前时间：{session.current_time}</p>
              </>
            ) : <p>尚未创建会话。</p>}
          </section>

          <section className="card">
            <h2>主要技能</h2>
            {sortedSkills.length ? sortedSkills.map(([skill, value]) => (
              <div className="skill" key={skill}><span>{skill}</span><strong>{value}%</strong></div>
            )) : <p>暂无技能数据。</p>}
          </section>
        </aside>

        <section className="chat-panel">
          <div className="messages">
            {messages.map((message, index) => (
              <article key={`${message.role}-${index}`} className={`message ${message.role}`}>
                <div className="message-role">{message.role === 'keeper' ? '守秘人' : message.role === 'player' ? '调查员' : '系统'}</div>
                <p>{message.content}</p>
                {message.meta && <span className="meta">{message.meta}</span>}
              </article>
            ))}
          </div>

          <div className="options">
            {options.map((option) => (
              <button key={option} onClick={() => option === '自定义行动' ? setInput('') : void send(option)} disabled={!session || busy}>
                {option}
              </button>
            ))}
          </div>

          <form className="input-row" onSubmit={submit}>
            <input
              value={input}
              onChange={(event) => setInput(event.target.value)}
              placeholder={session ? '描述你的行动，例如：我仔细检查走廊里的脚印' : '请先创建会话'}
              disabled={!session || busy}
            />
            <button className="primary" disabled={!session || busy || !input.trim()}>发送</button>
          </form>
        </section>

        <aside className="side-panel">
          <section className="card">
            <h2>剧情状态</h2>
            {session ? (
              <>
                <TagList title="已访问地点" items={visitedLocations} emptyText="暂无访问记录。" />
                <TagList title="可前往地点" items={availableLocations} emptyText="暂无地点数据。" />
              </>
            ) : <p>尚未创建会话。</p>}
          </section>

          <section className="card">
            <h2>场景记录</h2>
            {session ? (
              <>
                {session.summary && <p>{session.summary}</p>}
                <TagList title="已调查对象" items={investigatedObjects} emptyText="暂无已调查对象。" />
                <TagList title="最近行动" items={recentActions} emptyText="暂无行动记录。" />
              </>
            ) : <p>尚未创建会话。</p>}
          </section>

          <section className="card">
            <h2>物品栏</h2>
            {session ? <InventoryList items={session.inventory_items} /> : <p>尚未创建会话。</p>}
          </section>

          <section className="card">
            <h2>线索簿</h2>
            {session?.clues.length ? session.clues.map((clue) => (
              <details key={clue.id}>
                <summary>{clue.name}</summary>
                <p>{clue.content}</p>
                {clue.source_location && <small>来源：{clue.source_location}</small>}
              </details>
            )) : <p>尚未记录线索。</p>}
          </section>

          <section className="card">
            <h2>材料与地图</h2>
            <div className="asset-links">
              <a href={`${assetBase}/附件/航标岛地图.png`} target="_blank">航标岛地图</a>
              <a href={`${assetBase}/附件/航标岛灯塔地图.png`} target="_blank">灯塔地图</a>
              <a href={`${assetBase}/附件/材料1.png`} target="_blank">材料 1</a>
              <a href={`${assetBase}/附件/材料2.png`} target="_blank">材料 2</a>
              <a href={`${assetBase}/附件/材料3.png`} target="_blank">材料 3</a>
            </div>
          </section>

          <details className="card debug-card">
            <summary>主持人调试信息</summary>
            {session ? <TagList title="内部状态" items={debugEntries} emptyText="暂无内部状态。" /> : <p>尚未创建会话。</p>}
          </details>
        </aside>
      </main>
    </div>
  );
}

function Stat({ label, value }: { label: string; value: string }) {
  return <div className="stat"><span>{label}</span><strong>{value}</strong></div>;
}

function AttributeTable({ rows }: { rows: AttributeRow[] }) {
  if (!rows.length) return null;
  return (
    <div className="attribute-table">
      <div className="attribute-head"><span>属性</span><span>简单鉴定</span><span>中等鉴定</span><span>困难鉴定</span></div>
      {rows.map((row) => (
        <div className="attribute-row" key={row.key}>
          <span>{row.label}<small>{row.key}</small></span>
          <strong>{row.value}</strong>
          <span>{row.half}</span>
          <span>{row.fifth}</span>
        </div>
      ))}
    </div>
  );
}

function TagList({ title, items, emptyText }: { title: string; items: string[]; emptyText: string }) {
  return (
    <div className="tag-list">
      <h3>{title}</h3>
      {items.length ? <div className="tags">{items.map((item) => <span className="tag" key={item}>{item}</span>)}</div> : <p>{emptyText}</p>}
    </div>
  );
}

function InventoryList({ items }: { items: InventoryItem[] }) {
  if (!items.length) return <p>暂无物品。</p>;
  return (
    <div className="inventory-list">
      {items.map((item) => {
        const metadata = asRecord(item.metadata_);
        const extra = buildInventoryMeta(metadata);
        return (
          <article className="inventory-item" key={item.id}>
            <div>
              <strong>{item.name}</strong>
              {item.description && <p>{item.description}</p>}
              {extra && <small className="inventory-meta">{extra}</small>}
            </div>
            <span className="inventory-quantity">×{item.quantity}</span>
          </article>
        );
      })}
    </div>
  );
}

function buildMessagesFromSession(session: GameSession): ChatMessage[] {
  if (!session.recent_turns.length) return [{ role: 'keeper', content: openingText }];
  const messages: ChatMessage[] = [];
  session.recent_turns.forEach((turn) => {
    messages.push({ role: 'player', content: turn.player_input });
    messages.push({ role: 'keeper', content: turn.keeper_response });
  });
  return messages;
}

function buildInventoryMeta(metadata: Record<string, unknown>): string {
  const parts: string[] = [];
  if (metadata['可消耗'] === true) parts.push('可消耗');
  if (metadata['来源']) parts.push(`来源：${String(metadata['来源'])}`);
  if (metadata['最近原因']) parts.push(`最近：${String(metadata['最近原因'])}`);
  return parts.join(' · ');
}

function selectedCharacterLabel(characters: Character[], selectedCharacter: string): string {
  const character = characters.find((item) => item.id === selectedCharacter);
  return character ? `角色：${character.archetype}` : '选择角色';
}

function buildActionMeta(response: ActionResponse): string {
  const metaParts: string[] = [];
  response.skill_checks.forEach((check) => metaParts.push(`${check.skill} ${check.roll}/${check.skill_value}，${check.success_level}`));
  response.sanity_checks.forEach((check) => metaParts.push(`理智损失 ${check.san_loss}，当前 ${check.san_after}`));
  if (response.discovered_clues.length) metaParts.push(`发现线索 ${response.discovered_clues.length} 条`);
  if (typeof response.state_delta.time_cost_minutes === 'number') metaParts.push(`耗时 ${response.state_delta.time_cost_minutes} 分钟`);
  if (typeof response.state_delta.danger_delta === 'number' && response.state_delta.danger_delta > 0) metaParts.push(`危险 +${response.state_delta.danger_delta}`);
  metaParts.push(...formatInventoryChangeSummary(response.state_delta));
  return metaParts.join(' · ');
}

function formatInventoryChangeSummary(delta: Record<string, unknown>): string[] {
  const results = asRecord(delta['inventory_results']);
  const summary = results['summary'];
  if (Array.isArray(summary)) return summary.map(String).filter(Boolean);
  const changes = delta['inventory_changes'];
  if (!Array.isArray(changes)) return [];
  return changes.map(formatInventoryChange).filter(Boolean);
}

function formatInventoryChange(value: unknown): string {
  const item = asRecord(value);
  const operation = String(item.operation ?? item['操作'] ?? '').replace('物品', '');
  const name = String(item.name ?? item['名称'] ?? item.item ?? item['物品'] ?? '').trim();
  const quantity = Number(item.quantity ?? item['数量'] ?? 1);
  if (!operation || !name) return '';
  if (operation === '使用' && !item.consumable && !item['可消耗']) return `使用：${name}`;
  return `${operation}：${name} ×${Number.isFinite(quantity) ? Math.max(quantity, 1) : 1}`;
}

function asRecord(value: unknown): Record<string, unknown> {
  return value && typeof value === 'object' && !Array.isArray(value) ? value as Record<string, unknown> : {};
}

function asStringArray(value: unknown): string[] {
  if (!Array.isArray(value)) return [];
  const result: string[] = [];
  const seen = new Set<string>();
  for (const item of value) {
    const text = normalizeDisplayText(item);
    const key = text.replace(/^(起点|当前位置|当前地点|地点|可前往地点)[:：]/, '').trim();
    if (!key || seen.has(key)) continue;
    seen.add(key);
    result.push(key);
  }
  return result;
}

function normalizeDisplayText(value: unknown): string {
  if (value === null || value === undefined) return '';
  const text = String(value).trim();
  if (!text || ['null', 'none', 'undefined', 'nan'].includes(text.toLowerCase())) return '';
  return text;
}

interface AttributeRow {
  key: string;
  label: string;
  value: number | string;
  half: number | string;
  fifth: number | string;
}

function buildAttributeRows(attributes: unknown): AttributeRow[] {
  const record = asRecord(attributes);
  const core = asRecord(record['核心属性']);
  const order: [string, string][] = [
    ['STR', '力量'],
    ['CON', '体质'],
    ['SIZ', '体型'],
    ['DEX', '敏捷'],
    ['APP', '外貌'],
    ['INT', '智力'],
    ['POW', '意志'],
    ['EDU', '教育'],
    ['Luck', '幸运'],
  ];
  return order.map(([key, label]) => {
    const coreItem = asRecord(core[key]);
    const value = Number(coreItem['简单鉴定'] ?? coreItem['全值'] ?? record[key] ?? 0);
    return {
      key,
      label: String(coreItem['名称'] ?? label),
      value: value || '无',
      half: Number(coreItem['中等鉴定'] ?? coreItem['半值'] ?? Math.floor(value / 2)) || '无',
      fifth: Number(coreItem['困难鉴定'] ?? coreItem['五分之一'] ?? Math.floor(value / 5)) || '无',
    };
  }).filter((row) => row.value !== '无');
}

function formatDateTime(value: string): string {
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return value;
  return date.toLocaleString('zh-CN', { month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit' });
}

function normalizeOptions(value: unknown): string[] {
  if (!Array.isArray(value)) return ['继续调查', '查看角色状态', '自定义行动'];
  const options: string[] = [];
  value.forEach((item) => {
    const option = normalizeOption(item);
    if (option && option !== '自定义行动' && !options.includes(option)) options.push(option);
  });
  if (!options.length) return ['继续调查', '查看角色状态', '自定义行动'];
  return [...options.slice(0, 5), '自定义行动'];
}

function normalizeOption(value: unknown): string {
  if (value && typeof value === 'object' && !Array.isArray(value)) {
    const record = value as Record<string, unknown>;
    return String(record.action ?? record.label ?? record.title ?? record.name ?? record.description ?? '').trim();
  }
  const option = String(value ?? '').trim();
  if (option.startsWith('{') && option.endsWith('}')) return extractOptionFromObjectText(option);
  return option;
}

function extractOptionFromObjectText(value: string): string {
  for (const key of ['action', 'label', 'title', 'name', 'description']) {
    const match = value.match(new RegExp(`[\"']${key}[\"']\\s*:\\s*[\"']([^\"']+)[\"']`));
    if (match?.[1]) return match[1].trim();
  }
  return value;
}

function formatFlagEntries(value: unknown): string[] {
  return Object.entries(asRecord(value)).slice(-8).map(([key, item]) => `${key}：${formatValue(item)}`);
}

function formatAuditEntries(value: Record<string, unknown>): string[] {
  const validation = asRecord(value['状态校验']);
  const leak = asRecord(value['防剧透']);
  const divergence = asRecord(value['偏离剧情']);
  const retrieval = asRecord(value['检索']);
  const entries: string[] = [];
  if (Object.keys(validation).length) entries.push(`状态校验：${validation['有效'] === false ? '已修正' : '通过'}`);
  if (Object.keys(leak).length) entries.push(`防剧透：${formatLeakStatus(leak)}`);
  if (Object.keys(divergence).length) entries.push(`偏离剧情：${String(divergence['level'] ?? '轻微')}`);
  if (Object.keys(retrieval).length) {
    entries.push(
      `检索：剧本 ${String(retrieval['剧本片段数'] ?? 0)} / 实体 ${String(retrieval['结构化实体数'] ?? 0)} / 线索 ${String(retrieval['线索索引数'] ?? 0)} / 记忆 ${String(retrieval['会话记忆数'] ?? 0)} / 规则 ${String(retrieval['规则片段数'] ?? 0)}`
    );
  }
  return entries;
}

function formatLeakStatus(value: Record<string, unknown>): string {
  const narration = asRecord(value['叙事']);
  const options = asRecord(value['选项']);
  if (narration['通过'] === false || options['通过'] === false) return '已屏蔽';
  return '通过';
}

function formatValue(value: unknown): string {
  if (typeof value === 'boolean') return value ? '是' : '否';
  if (typeof value === 'number' || typeof value === 'string') return String(value);
  if (value && typeof value === 'object') return '已记录';
  return '无';
}
