import { FormEvent, useEffect, useMemo, useRef, useState } from 'react';
import ReactMarkdown from 'react-markdown';
import { api } from './api';
import type { ActionResponse, AssistantCitation, AssistantMessage, AssistantMode, Character, ChatMessage, DebugEvent, GameSession, InventoryItem } from './types';

// 【阅读顺序 1：前端主界面】
// 如果你是 Web 初学者，建议先从这个文件看起：
// 1. 用户在页面上点击按钮或输入行动。
// 2. React 用 useState 保存“当前会话、聊天消息、输入框、加载状态”等页面状态。
// 3. 用户提交行动后调用 frontend/src/api.ts，把请求发给后端。
// 4. 后端返回守秘人叙事、选项、线索和状态后，本文件再把这些数据渲染到页面上。
const assetBase = `${import.meta.env.BASE_URL.replace(/\/$/, '')}/assets`;
const guideStorageKey = 'coc-lite-new-user-guide-seen';
const assistantOpenStorageKey = 'coc-lite-game-assistant-open';
const maxDebugEvents = 200;

const openingText = '现在是 1926 年四月十二日，晚上八点十五分左右。航标岛上的灯塔在暴风雨前熄灭，埃塞克斯号触礁沉没。你坐在救生艇里，黑暗的海面拍打船舷，远处只有灯塔底部透出微弱的光。';
const openingImageUrl = `${assetBase}/images/opening.jpg`;

export default function App() {
  // 主组件集中管理会话、聊天记录、角色选择、新手引导和右侧状态栏数据。
  // 对初学者来说，可以把 App 先理解成“页面总控制器”：所有按钮事件和展示数据最终都汇总到这里。
  const sessionPanelRef = useRef<HTMLDetailsElement>(null);
  const [characters, setCharacters] = useState<Character[]>([]);
  const [savedSessions, setSavedSessions] = useState<GameSession[]>([]);
  const [selectedSession, setSelectedSession] = useState('');
  const [selectedCharacter, setSelectedCharacter] = useState('');
  const [session, setSession] = useState<GameSession | null>(null);
  const [messages, setMessages] = useState<ChatMessage[]>([{ role: 'keeper', content: openingText, imageUrl: openingImageUrl }]);
  const [options, setOptions] = useState<string[]>(['观察海面和灯塔', '划向北岸码头', '检查救生艇', '自定义行动']);
  const [input, setInput] = useState('');
  const [busy, setBusy] = useState(false);
  const [status, setStatus] = useState('等待导入资料');
  const [error, setError] = useState('');
  const [showGuide, setShowGuide] = useState(false);
  const [showCharacterDialog, setShowCharacterDialog] = useState(false);
  const [debugOpen, setDebugOpen] = useState(false);
  const [debugEvents, setDebugEvents] = useState<DebugEvent[]>([]);
  const [assistantOpen, setAssistantOpen] = useState(false);
  const [assistantInput, setAssistantInput] = useState('');
  const [assistantBusy, setAssistantBusy] = useState(false);
  const [assistantMode, setAssistantMode] = useState<AssistantMode>('auto');
  const [assistantStatus, setAssistantStatus] = useState('可查询规则、术语或已知线索');
  const [assistantMessages, setAssistantMessages] = useState<AssistantMessage[]>([
    { role: 'assistant', content: '我是场外游戏助手。你可以问我 COC 规则、术语解释、网页操作，或让我基于已发现线索给出非剧透提示。' },
  ]);

  useEffect(() => {
    // 首次进入应用时加载角色和历史会话，为开始/恢复游戏做准备。
    void loadCharacters();
    void loadSessions();
  }, []);

  useEffect(() => {
    // 新手引导只在首次访问自动弹出，用户关闭后用 localStorage 记住状态。
    try {
      if (window.localStorage.getItem(guideStorageKey) !== 'true') setShowGuide(true);
    } catch {
      setShowGuide(true);
    }
  }, []);

  useEffect(() => {
    try {
      setAssistantOpen(window.localStorage.getItem(assistantOpenStorageKey) === 'true');
    } catch {
      setAssistantOpen(false);
    }
  }, []);

  useEffect(() => {
    try {
      window.localStorage.setItem(assistantOpenStorageKey, assistantOpen ? 'true' : 'false');
    } catch {
      void 0;
    }
  }, [assistantOpen]);

  useEffect(() => {
    // 历史会话下拉菜单点击外部区域时自动关闭。
    function closeMenusOnOutsideClick(event: MouseEvent) {
      const target = event.target;
      if (!(target instanceof Node)) return;
      const panel = sessionPanelRef.current;
      if (panel?.open && !panel.contains(target)) panel.open = false;
    }

    document.addEventListener('pointerdown', closeMenusOnOutsideClick);
    return () => document.removeEventListener('pointerdown', closeMenusOnOutsideClick);
  }, []);

  useEffect(() => {
    if (!showGuide) return;

    function closeGuideOnEscape(event: KeyboardEvent) {
      if (event.key === 'Escape') closeGuide();
    }

    document.addEventListener('keydown', closeGuideOnEscape);
    return () => document.removeEventListener('keydown', closeGuideOnEscape);
  }, [showGuide]);

  useEffect(() => {
    if (!showCharacterDialog) return;

    function closeCharacterDialogOnEscape(event: KeyboardEvent) {
      if (event.key === 'Escape' && !busy) setShowCharacterDialog(false);
    }

    document.addEventListener('keydown', closeCharacterDialogOnEscape);
    return () => document.removeEventListener('keydown', closeCharacterDialogOnEscape);
  }, [showCharacterDialog, busy]);

  const sortedSkills = useMemo<[string, number][]>(() => {
    // 只展示数值最高的前十个技能，减少角色面板信息噪音。
    if (!session) return [];
    return (Object.entries(session.character.skills) as [string, number][])
      .sort((a, b) => b[1] - a[1])
      .slice(0, 10);
  }, [session]);
  const storyState = asRecord(session?.state['剧情']);
  // 后端 state 是宽松 JSON，前端先归一化再提取可展示字段。
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
  const recommendedCharacter = useMemo(() => characters.find((item) => item.archetype === '调查局探员') ?? null, [characters]);
  const characterOptions = useMemo(() => {
    // 推荐角色排在首位，其余角色保持后端返回顺序。
    if (!recommendedCharacter) return characters;
    return [recommendedCharacter, ...characters.filter((item) => item.id !== recommendedCharacter.id)];
  }, [characters, recommendedCharacter]);
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
    // 角色列表也是资料是否已导入的信号：失败时提示用户先初始化后端数据。
    // 【Web 流程 1】页面加载后先向后端请求角色列表，成功后才能开始新会话。
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
    // 历史会话只缓存概要；真正恢复时再按 id 拉取完整会话。
    // 【Web 流程 2】历史会话列表用于“继续游戏”，这里不会运行 Agent，只读取数据库中的会话摘要。
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

  function openCharacterDialog() {
    // 开始新会话前先让用户确认角色，默认选中推荐角色。
    if (busy || !characters.length) return;
    const selectedExists = characters.some((item) => item.id === selectedCharacter);
    const defaultCharacter = recommendedCharacter ?? characters[0];
    if (!selectedExists && defaultCharacter) setSelectedCharacter(defaultCharacter.id);
    setError('');
    setShowCharacterDialog(true);
  }

  async function startSession(characterId = selectedCharacter) {
    // 创建成功后重置聊天窗口，让新会话从固定开场白开始。
    // 【Web 流程 3】开始会话会调用 POST /sessions，后端会创建 GameSession 并初始化剧情状态。
    const characterToUse = characterId || recommendedCharacter?.id || characters[0]?.id;
    if (!characterToUse) return;
    setBusy(true);
    setError('');
    try {
      const created = await api.createSession(characterToUse);
      setSession(created);
      setMessages([{ role: 'keeper', content: openingText, imageUrl: openingImageUrl }]);
      setOptions(['观察海面和灯塔', '划向北岸码头', '检查救生艇', '自定义行动']);
      setStatus(`会话已创建：${created.character.archetype}`);
      setShowCharacterDialog(false);
      await loadSessions();
    } catch (err) {
      setShowCharacterDialog(false);
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
    // 恢复会话时根据最近回合重建聊天消息，并复用上次可选行动。
    // 【Web 流程 4】恢复会话只读取后端保存过的状态，不会重新执行上一回合的 LangGraph。
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
    // 删除当前正在查看的会话时，同时把页面回到未开始状态。
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
    setMessages([{ role: 'keeper', content: openingText, imageUrl: openingImageUrl }]);
    setOptions(['观察海面和灯塔', '划向北岸码头', '检查救生艇', '自定义行动']);
    setInput('');
  }

  function closeGuide() {
    // localStorage 可能因隐私模式不可用，因此失败时只关闭弹窗不阻断页面。
    setShowGuide(false);
    try {
      window.localStorage.setItem(guideStorageKey, 'true');
    } catch {
      void 0;
    }
  }

  function appendDebugEvent(event: DebugEvent) {
    const normalized = { ...event, timestamp: event.timestamp || new Date().toISOString() };
    setDebugEvents((prev) => [...prev, normalized].slice(-maxDebugEvents));
  }

  function makeDebugEvent(phase: string, name: string, statusValue: DebugEvent['status'], message: string): DebugEvent {
    return { phase, name, status: statusValue, message, timestamp: new Date().toISOString() };
  }

  async function send(message: string) {
    // 发送玩家行动后先追加一个空守秘人消息，流式 chunk 会持续写入这条消息。
    // 【Web 流程 5】这是玩家行动的入口：输入文本 -> api.streamAction -> 后端 Agent -> 流式返回叙事。
    // 初学者可重点观察 messages 的变化：先追加玩家消息，再追加空的守秘人消息，最后不断填充守秘人文本。
    const content = message.trim();
    if (!content || !session || busy) return;
    setBusy(true);
    setError('');
    setInput('');
    setDebugOpen(true);
    appendDebugEvent(makeDebugEvent('frontend', 'player_action', 'start', `发送行动：${content.slice(0, 60)}`));
    setMessages((prev) => [...prev, { role: 'player', content }, { role: 'keeper', content: '' }]);
    try {
      await api.streamAction(session.id, content, (event) => {
        // 【Web 流程 6】后端流式返回三类事件：chunk 是一段文本，final 是完整结果，error 是异常。
        if (event.type === 'start') {
          appendDebugEvent(makeDebugEvent('stream', 'action_stream', 'start', '行动流已连接。'));
        } else if (event.type === 'debug') {
          appendDebugEvent(event.event);
        } else if (event.type === 'chunk') {
          appendToLastKeeperMessage(event.content);
        } else if (event.type === 'final') {
          appendDebugEvent(makeDebugEvent('frontend', 'render_result', 'success', '最终结果已应用到界面。'));
          applyActionResponse(event.response);
        } else if (event.type === 'image') {
          attachImageToKeeperMessage(event.turnId, event.url, event.metadata);
        } else if (event.type === 'error') {
          appendDebugEvent(makeDebugEvent('stream', 'action_stream', 'error', event.detail));
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
    // 保持 React 状态不可变更新，只替换最后一条守秘人消息。
    setMessages((prev) => {
      const next = [...prev];
      const index = next.length - 1;
      if (index >= 0 && next[index].role === 'keeper') {
        next[index] = { ...next[index], content: `${next[index].content}${chunk}` };
      }
      return next;
    });
  }

  async function sendAssistantMessage(message: string) {
    const content = message.trim();
    if (!content || assistantBusy) return;
    setAssistantBusy(true);
    setAssistantInput('');
    setAssistantStatus('正在准备检索');
    setAssistantMessages((prev) => [...prev, { role: 'user', content }, { role: 'assistant', content: '' }]);
    try {
      await api.streamAssistantChat({
        session_id: session?.id ?? null,
        message: content,
        mode: assistantMode,
        enable_mqe: true,
        mqe_expansions: 2,
        enable_hyde: null,
        top_k: 5,
        candidate_pool_multiplier: 4,
      }, (event) => {
        if (event.type === 'debug') {
          setDebugEvents((prev) => [...prev.slice(-maxDebugEvents + 1), event.event]);
        } else if (event.type === 'retrieval') {
          setAssistantStatus(event.status);
        } else if (event.type === 'chunk') {
          appendToLastAssistantMessage(event.content);
        } else if (event.type === 'citations') {
          attachAssistantCitations(event.citations);
        } else if (event.type === 'final') {
          finalizeAssistantMessage(event.response.answer, event.response.citations, event.response.spoiler_blocked);
          setAssistantStatus(event.response.spoiler_blocked ? '已进行非剧透处理' : '回答完成');
        } else if (event.type === 'error') {
          throw new Error(event.detail);
        }
      });
    } catch (err) {
      const detail = err instanceof Error ? err.message : String(err);
      setAssistantStatus('助手请求失败');
      setAssistantMessages((prev) => [...prev, { role: 'system', content: `助手请求失败：${detail}` }]);
    } finally {
      setAssistantBusy(false);
    }
  }

  function appendToLastAssistantMessage(chunk: string) {
    setAssistantMessages((prev) => {
      const next = [...prev];
      const index = next.length - 1;
      if (index >= 0 && next[index].role === 'assistant') {
        next[index] = { ...next[index], content: `${next[index].content}${chunk}` };
      }
      return next;
    });
  }

  function attachAssistantCitations(citations: AssistantCitation[]) {
    setAssistantMessages((prev) => {
      const next = [...prev];
      const index = next.length - 1;
      if (index >= 0 && next[index].role === 'assistant') {
        next[index] = { ...next[index], citations };
      }
      return next;
    });
  }

  function finalizeAssistantMessage(content: string, citations: AssistantCitation[], spoilerBlocked: boolean) {
    setAssistantMessages((prev) => {
      const next = [...prev];
      const index = next.length - 1;
      if (index >= 0 && next[index].role === 'assistant') {
        next[index] = { ...next[index], content, citations, spoilerBlocked };
      }
      return next;
    });
  }

  function applyActionResponse(response: ActionResponse) {
    // final 事件带有完整回合结果，用它覆盖流式文本并刷新状态栏数据。
    // 【Web 流程 7】final 到达后，页面才更新会话状态、下一步选项、线索、物品栏和检定摘要。
    setSession(response.session);
    setOptions(normalizeOptions(response.options));
    const meta = buildActionMeta(response);
    setMessages((prev) => {
      const next = [...prev];
      const index = next.length - 1;
      if (index >= 0 && next[index].role === 'keeper') {
        next[index] = {
          ...next[index],
          content: response.narration,
          meta,
          imageUrl: response.image_url ?? undefined,
          imageMetadata: response.image_metadata,
          imageLoading: response.needs_image && !response.image_url,
          imageAspectRatio: response.image_aspect_ratio,
        };
      }
      return next;
    });
  }

  function attachImageToKeeperMessage(turnId: string, url: string, metadata?: Record<string, unknown>) {
    setMessages((prev) => {
      const next = [...prev];
      // 优先精确匹配尚未附带图片的 keeper 消息；兜底则匹配最后一条 keeper
      for (let i = next.length - 1; i >= 0; i--) {
        if (next[i].role === 'keeper' && !next[i].imageUrl) {
          next[i] = { ...next[i], imageUrl: url, imageMetadata: metadata, imageLoading: false };
          break;
        }
      }
      return next;
    });
  }

  function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    void send(input);
  }

  function submitAssistant(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    void sendAssistantMessage(assistantInput);
  }

  return (
    <div className="app-shell">
      <header className="topbar">
        <div>
          <p className="eyebrow">守秘人记录</p>
          <h1>《无光的灯塔》</h1>
        </div>
        <div className="toolbar">
          <button className="guide-button" onClick={() => setShowGuide(true)}>新手引导</button>
          <button className="primary" onClick={openCharacterDialog} disabled={busy || !characters.length}>开始会话</button>
          <details className="session-panel" ref={sessionPanelRef}>
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

      {showGuide && (
        <div className="guide-overlay" onMouseDown={(event) => {
          if (event.target === event.currentTarget) closeGuide();
        }}>
          <section className="guide-modal" role="dialog" aria-modal="true" aria-labelledby="guide-title">
            <div className="guide-header">
              <div>
                <p className="eyebrow">调查员简报</p>
                <h2 id="guide-title">新手引导</h2>
              </div>
              <button className="guide-close" onClick={closeGuide} aria-label="关闭新手引导">×</button>
            </div>
            <p className="guide-intro">你将扮演调查员，在守秘人的叙事中探索航标岛与熄灭的灯塔。</p>
            <div className="guide-sections">
              <article className="guide-section">
                <h3>游戏背景</h3>
                <p>1926 年暴风雨前夜，灯塔熄灭，船只触礁。岛上微光、失踪者与异常现象等待调查。</p>
              </article>
              <article className="guide-section">
                <h3>游戏目标</h3>
                <p>收集线索、管理物品与状态，判断危险来源，并尽可能揭开灯塔熄灭的真相。</p>
              </article>
              <article className="guide-section">
                <h3>操作方法</h3>
                <ul>
                  <li>点击“开始会话”后选择角色，推荐使用“调查局探员”。</li>
                  <li>点击行动选项，或在输入框描述自定义行动。</li>
                  <li>查看右侧物品、地图和线索辅助决策。</li>
                </ul>
              </article>
            </div>
            <div className="guide-actions">
              <button className="primary" onClick={closeGuide}>开始调查</button>
            </div>
          </section>
        </div>
      )}

      {showCharacterDialog && (
        <div className="character-overlay" onMouseDown={(event) => {
          if (event.target === event.currentTarget && !busy) setShowCharacterDialog(false);
        }}>
          <section className="character-modal" role="dialog" aria-modal="true" aria-labelledby="character-title">
            <div className="guide-header">
              <div>
                <p className="eyebrow">调查员建档</p>
                <h2 id="character-title">选择角色</h2>
              </div>
              <button className="guide-close" onClick={() => setShowCharacterDialog(false)} disabled={busy} aria-label="关闭角色选择">×</button>
            </div>
            <p className="guide-intro">选择一名调查员开始新的会话。</p>
            <div className="character-picker-grid">
              {characterOptions.map((character) => {
                const recommended = character.id === recommendedCharacter?.id;
                return (
                  <button
                    className={`character-option ${selectedCharacter === character.id ? 'active' : ''} ${recommended ? 'recommended' : ''}`}
                    key={character.id}
                    onClick={() => setSelectedCharacter(character.id)}
                    disabled={busy}
                    aria-pressed={selectedCharacter === character.id}
                  >
                    <span>
                      <strong>{character.archetype}</strong>
                      {recommended && <small className="recommend-badge">推荐角色</small>}
                    </span>
                    <small>{character.occupation || '调查员'}</small>
                  </button>
                );
              })}
            </div>
            <div className="character-actions">
              <button onClick={() => setShowCharacterDialog(false)} disabled={busy}>取消</button>
              <button className="primary" onClick={() => void startSession()} disabled={busy || !selectedCharacter}>
                {busy ? '创建中...' : '确认开始'}
              </button>
            </div>
          </section>
        </div>
      )}

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
                {message.imageUrl && (
                  <img
                    src={message.imageUrl}
                    alt="场景配图"
                    className="message-image"
                    loading="lazy"
                  />
                )}
                {!message.imageUrl && message.imageLoading && (
                  <div
                    className="image-placeholder"
                    style={{ aspectRatio: message.imageAspectRatio || '16/9' }}
                  >
                    正在生成场景图…
                  </div>
                )}
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
            <h2>物品栏</h2>
            {session ? <InventoryList items={session.inventory_items} /> : <p>尚未创建会话。</p>}
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

          <details className="card debug-card">
            <summary>主持人调试信息</summary>
            {session ? <TagList title="内部状态" items={debugEntries} emptyText="暂无内部状态。" /> : <p>尚未创建会话。</p>}
          </details>
        </aside>
      </main>

      <DebugConsolePanel
        open={debugOpen}
        events={debugEvents}
        onToggle={() => setDebugOpen((value) => !value)}
        onClear={() => setDebugEvents([])}
      />

      <button className="assistant-launcher" onClick={() => setAssistantOpen(true)} aria-label="打开游戏助手">
        游戏助手
      </button>

      <GameAssistantPanel
        open={assistantOpen}
        messages={assistantMessages}
        input={assistantInput}
        mode={assistantMode}
        busy={assistantBusy}
        status={assistantStatus}
        hasSession={Boolean(session)}
        onClose={() => setAssistantOpen(false)}
        onInputChange={setAssistantInput}
        onModeChange={setAssistantMode}
        onSubmit={submitAssistant}
      />
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

function DebugConsolePanel({
  open,
  events,
  onToggle,
  onClear,
}: {
  open: boolean;
  events: DebugEvent[];
  onToggle: () => void;
  onClear: () => void;
}) {
  const latest = events[events.length - 1];
  const visibleEvents = [...events].reverse();
  return (
    <aside className={`debug-console ${open ? 'open' : ''}`} aria-label="实时调试窗口">
      {open && (
        <section className="debug-console-panel">
          <div className="debug-console-header">
            <div>
              <p className="eyebrow">实时状态</p>
              <h2>Agent 调试</h2>
            </div>
            <button onClick={onClear} disabled={!events.length}>清空</button>
          </div>
          <div className="debug-console-events">
            {visibleEvents.length ? visibleEvents.map((event, index) => (
              <article className={`debug-event ${event.status}`} key={`${event.timestamp}-${event.phase}-${event.name}-${index}`}>
                <div className="debug-event-meta">
                  <span>{formatDebugTime(event.timestamp)}</span>
                  <strong>{formatDebugPhase(event.phase)}</strong>
                  <em>{formatDebugStatus(event.status)}</em>
                </div>
                <div className="debug-event-name">{event.name}</div>
                <p>{event.message || '状态已更新。'}</p>
                {event.metadata && Object.keys(event.metadata).length > 0 && (
                  <details className="debug-event-detail">
                    <summary>详细数据</summary>
                    <pre>{formatDebugMetadata(event.metadata)}</pre>
                  </details>
                )}
              </article>
            )) : <p className="debug-empty">发送行动后会显示 Agent、Skill 与 Tool 的运行状态。</p>}
          </div>
        </section>
      )}
      <button className="debug-console-toggle" onClick={onToggle} aria-expanded={open}>
        <span>调试</span>
        <small>{latest ? formatDebugSummary(latest) : '等待运行状态'}</small>
      </button>
    </aside>
  );
}

function formatDebugSummary(event: DebugEvent): string {
  return `${formatDebugPhase(event.phase)} · ${event.name} · ${formatDebugStatus(event.status)}`;
}

function formatDebugPhase(phase: string): string {
  const labels: Record<string, string> = {
    stream: '流',
    frontend: '前端',
    agent_node: 'Agent',
    agent_step: '步骤',
    skill: 'Skill',
    tool: 'Tool',
    assistant: '助手',
  };
  return labels[phase] ?? phase;
}

function formatDebugStatus(statusValue: string): string {
  const labels: Record<string, string> = {
    start: '开始',
    success: '完成',
    warning: '警告',
    error: '错误',
  };
  return labels[statusValue] ?? statusValue;
}

function formatDebugTime(timestamp: string): string {
  const date = new Date(timestamp);
  if (Number.isNaN(date.getTime())) return '--:--:--';
  return date.toLocaleTimeString('zh-CN', { hour12: false });
}

function formatDebugMetadata(metadata: Record<string, unknown>): string {
  try {
    return JSON.stringify(metadata, null, 2);
  } catch {
    return String(metadata);
  }
}

function GameAssistantPanel({
  open,
  messages,
  input,
  mode,
  busy,
  status,
  hasSession,
  onClose,
  onInputChange,
  onModeChange,
  onSubmit,
}: {
  open: boolean;
  messages: AssistantMessage[];
  input: string;
  mode: AssistantMode;
  busy: boolean;
  status: string;
  hasSession: boolean;
  onClose: () => void;
  onInputChange: (value: string) => void;
  onModeChange: (value: AssistantMode) => void;
  onSubmit: (event: FormEvent<HTMLFormElement>) => void;
}) {
  return (
    <aside className={`assistant-drawer ${open ? 'open' : ''}`} aria-hidden={!open}>
      <div className="assistant-header">
        <div>
          <p className="eyebrow">场外帮助</p>
          <h2>游戏助手</h2>
        </div>
        <button className="assistant-close" onClick={onClose} aria-label="关闭游戏助手">×</button>
      </div>
      <p className="assistant-status">{status}</p>
      <div className="assistant-mode-row" role="group" aria-label="助手模式">
        <button className={mode === 'auto' ? 'active' : ''} onClick={() => onModeChange('auto')}>自动</button>
        <button className={mode === 'rules' ? 'active' : ''} onClick={() => onModeChange('rules')}>规则问答</button>
        <button className={mode === 'session_help' ? 'active' : ''} onClick={() => onModeChange('session_help')} disabled={!hasSession}>当前局势</button>
      </div>
      <div className="assistant-messages">
        {messages.map((message, index) => (
          <article className={`assistant-message ${message.role}`} key={`${message.role}-${index}`}>
            <div className="message-role">{message.role === 'assistant' ? '助手' : message.role === 'user' ? '你' : '系统'}</div>
            {message.content ? (
              <div className="assistant-message-content">
                <ReactMarkdown>{message.content}</ReactMarkdown>
              </div>
            ) : (busy && index === messages.length - 1 ? <p>思考中...</p> : null)}
            {message.spoilerBlocked && <small className="spoiler-note">已进行非剧透处理</small>}
            {message.citations?.length ? <AssistantCitationList citations={message.citations} /> : null}
          </article>
        ))}
      </div>
      <form className="assistant-input-row" onSubmit={onSubmit}>
        <input
          value={input}
          onChange={(event) => onInputChange(event.target.value)}
          placeholder="询问规则、术语或非剧透提示"
          disabled={busy}
        />
        <button className="primary" disabled={busy || !input.trim()}>{busy ? '回答中' : '发送'}</button>
      </form>
    </aside>
  );
}

function AssistantCitationList({ citations }: { citations: AssistantCitation[] }) {
  return (
    <details className="assistant-citations">
      <summary>引用来源 ({citations.length})</summary>
      <div>
        {citations.map((citation, index) => (
          <article className="assistant-citation" key={`${citation.id}-${index}`}>
            <strong>{citation.citation || citation.title}</strong>
            <small>{citation.source_type}</small>
            {citation.snippet && <p>{citation.snippet}</p>}
          </article>
        ))}
      </div>
    </details>
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
  // 后端只返回最近回合，因此恢复界面展示的是近期上下文而非完整历史。
  // 初学者可以把 recent_turns 理解成“服务器保存的聊天记录切片”，这里把它重新拼成前端消息数组。
  if (!session.recent_turns.length) return [{ role: 'keeper', content: openingText, imageUrl: openingImageUrl }];
  const messages: ChatMessage[] = [];
  session.recent_turns.forEach((turn) => {
    messages.push({ role: 'player', content: turn.player_input });
    messages.push({
      role: 'keeper',
      content: turn.keeper_response,
      imageUrl: turn.image_url ?? undefined,
      imageMetadata: turn.image_metadata,
    });
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

function buildActionMeta(response: ActionResponse): string {
  // 把检定、理智、线索、耗时和物品变化压缩成消息下方的一行摘要。
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
  // 优先使用后端已执行的物品结果摘要；没有结果时再展示 LLM 建议的变更。
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
  // state 中的列表可能混入空值或重复地点，这里统一清洗为展示文本。
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
  // 兼容角色卡中的“核心属性”嵌套结构，计算简单/中等/困难鉴定值。
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
  // 后端选项可能是字符串或对象；前端统一转成去重后的按钮文本。
  // 这样即使 LLM 偶尔返回了对象格式，页面按钮也能尽量正常显示。
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
  // 调试面板仅展示审计摘要，不直接暴露完整内部状态对象。
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
