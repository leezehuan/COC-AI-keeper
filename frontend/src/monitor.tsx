import { FormEvent, useEffect, useMemo, useState } from 'react';
import { createRoot } from 'react-dom/client';
import { api, type MonitorRecordFilters, type MonitorRunFilters } from './api';
import type { AgentMonitorEvent, AgentTraceRecord, AgentTraceRun, AgentTraceSettings } from './types';
import './monitor.css';

const defaultRecordLimit = 120;
const defaultRunLimit = 60;

function MonitorApp() {
  const [settings, setSettings] = useState<AgentTraceSettings | null>(null);
  const [runs, setRuns] = useState<AgentTraceRun[]>([]);
  const [records, setRecords] = useState<AgentTraceRecord[]>([]);
  const [selectedRunId, setSelectedRunId] = useState('');
  const [selectedRecord, setSelectedRecord] = useState<AgentTraceRecord | null>(null);
  const [runFilters, setRunFilters] = useState<MonitorRunFilters>({ limit: defaultRunLimit });
  const [recordFilters, setRecordFilters] = useState<MonitorRecordFilters>({ limit: defaultRecordLimit });
  const [maxRecordsDraft, setMaxRecordsDraft] = useState('5000');
  const [live, setLive] = useState(true);
  const [connection, setConnection] = useState('connecting');
  const [status, setStatus] = useState('正在加载监控数据');
  const [error, setError] = useState('');

  useEffect(() => {
    void refreshAll();
  }, []);

  useEffect(() => {
    if (!settings) return;
    setMaxRecordsDraft(String(settings.max_records));
  }, [settings?.max_records]);

  useEffect(() => {
    let cancelled = false;
    const controller = new AbortController();
    if (!live) {
      setConnection('paused');
      return () => {
        cancelled = true;
        controller.abort();
      };
    }
    setConnection('connecting');
    void api.streamMonitorEvents((event) => {
      if (cancelled) return;
      handleMonitorEvent(event);
    }, controller.signal).catch((err) => {
      if (cancelled) return;
      if (err instanceof DOMException && err.name === 'AbortError') return;
      setConnection('disconnected');
      setError(err instanceof Error ? err.message : String(err));
    });
    return () => {
      cancelled = true;
      controller.abort();
    };
  }, [live, selectedRunId, recordFilters.run_id, recordFilters.agent_name, recordFilters.status, recordFilters.source, recordFilters.session_id]);

  const filteredRecords = useMemo(() => {
    return records.filter((record) => {
      if (selectedRunId && record.run_id !== selectedRunId) return false;
      if (recordFilters.agent_name && record.agent_name !== recordFilters.agent_name) return false;
      if (recordFilters.status && record.status !== recordFilters.status) return false;
      if (recordFilters.source && record.source !== recordFilters.source) return false;
      if (recordFilters.session_id && record.session_id !== recordFilters.session_id) return false;
      return true;
    });
  }, [records, selectedRunId, recordFilters]);

  const agentOptions = useMemo(() => Array.from(new Set(records.map((item) => item.agent_name))).sort(), [records]);

  async function refreshAll() {
    setError('');
    try {
      const [nextSettings, nextRuns, nextRecords] = await Promise.all([
        api.monitorSettings(),
        api.monitorRuns({ ...runFilters, limit: runFilters.limit ?? defaultRunLimit }),
        api.monitorRecords({ ...recordFilters, run_id: selectedRunId || recordFilters.run_id, limit: recordFilters.limit ?? defaultRecordLimit }),
      ]);
      setSettings(nextSettings);
      setRuns(nextRuns);
      setRecords(nextRecords);
      setStatus('监控数据已刷新');
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
      setStatus('刷新失败');
    }
  }

  function handleMonitorEvent(event: AgentMonitorEvent) {
    if (event.type === 'start') {
      setConnection('connected');
      setStatus('实时流已连接');
      return;
    }
    if (event.type === 'heartbeat') {
      setConnection('connected');
      return;
    }
    if (event.type === 'settings') {
      setSettings(event.settings);
      return;
    }
    if (event.type === 'run') {
      setRuns((prev) => upsertById(prev, event.run).slice(0, defaultRunLimit));
      return;
    }
    if (event.type === 'record') {
      setRecords((prev) => upsertById(prev, event.record).slice(0, defaultRecordLimit));
      setSettings((prev) => prev ? { ...prev, record_count: prev.record_count + 1 } : prev);
    }
  }

  async function applySettings(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const value = Number(maxRecordsDraft);
    if (!Number.isFinite(value) || value < 0) return;
    setError('');
    try {
      const next = await api.updateMonitorSettings(Math.floor(value));
      setSettings(next);
      setStatus(`记录上限已更新为 ${next.max_records}`);
      await reloadRecords();
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    }
  }

  async function reloadRuns(nextFilters: MonitorRunFilters = runFilters) {
    const next = await api.monitorRuns({ ...nextFilters, limit: nextFilters.limit ?? defaultRunLimit });
    setRuns(next);
  }

  async function reloadRecords(nextFilters: MonitorRecordFilters = recordFilters, runId = selectedRunId) {
    const next = await api.monitorRecords({ ...nextFilters, run_id: runId || nextFilters.run_id, limit: nextFilters.limit ?? defaultRecordLimit });
    setRecords(next);
    if (selectedRecord && !next.some((item) => item.id === selectedRecord.id)) setSelectedRecord(null);
  }

  async function selectRun(runId: string) {
    const nextRunId = selectedRunId === runId ? '' : runId;
    setSelectedRunId(nextRunId);
    await reloadRecords(recordFilters, nextRunId);
  }

  async function deleteRecord(recordId: string) {
    setError('');
    try {
      await api.deleteMonitorRecord(recordId);
      setRecords((prev) => prev.filter((item) => item.id !== recordId));
      if (selectedRecord?.id === recordId) setSelectedRecord(null);
      const nextSettings = await api.monitorSettings();
      setSettings(nextSettings);
      setStatus('步骤记录已删除');
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    }
  }

  async function deleteRun(runId: string) {
    setError('');
    try {
      await api.deleteMonitorRun(runId);
      setRuns((prev) => prev.filter((item) => item.id !== runId));
      setRecords((prev) => prev.filter((item) => item.run_id !== runId));
      if (selectedRunId === runId) setSelectedRunId('');
      setSelectedRecord(null);
      setSettings(await api.monitorSettings());
      setStatus('运行记录已删除');
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    }
  }

  async function clearRecords() {
    setError('');
    try {
      const filters = { ...recordFilters, run_id: selectedRunId || recordFilters.run_id };
      const result = await api.deleteMonitorRecords(filters);
      await refreshAll();
      setStatus(`已删除 ${result.deleted} 条步骤记录`);
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    }
  }

  async function updateRunFilter(key: keyof MonitorRunFilters, value: string) {
    const next = { ...runFilters, [key]: value || undefined, limit: runFilters.limit ?? defaultRunLimit };
    setRunFilters(next);
    await reloadRuns(next);
  }

  async function updateRecordFilter(key: keyof MonitorRecordFilters, value: string) {
    const next = { ...recordFilters, [key]: value || undefined, limit: recordFilters.limit ?? defaultRecordLimit };
    setRecordFilters(next);
    await reloadRecords(next);
  }

  return (
    <div className="monitor-shell">
      <header className="monitor-header">
        <div>
          <p className="monitor-eyebrow">Developer Console</p>
          <h1>Agent Monitor</h1>
        </div>
        <div className="monitor-actions">
          <span className={`connection ${connection}`}>{connectionLabel(connection)}</span>
          <button onClick={() => setLive((value) => !value)}>{live ? '暂停实时' : '恢复实时'}</button>
          <button onClick={() => void refreshAll()}>刷新</button>
        </div>
      </header>

      {error && <div className="monitor-error">{error}</div>}
      <section className="monitor-status-grid">
        <Metric label="步骤记录" value={String(settings?.record_count ?? 0)} />
        <Metric label="运行记录" value={String(settings?.run_count ?? 0)} />
        <Metric label="记录上限" value={String(settings?.max_records ?? '--')} />
        <Metric label="状态" value={status} />
      </section>

      <main className="monitor-layout">
        <section className="monitor-panel runs-panel">
          <div className="panel-header">
            <div>
              <p className="monitor-eyebrow">Runs</p>
              <h2>运行</h2>
            </div>
            <button onClick={() => void reloadRuns()}>重载</button>
          </div>
          <div className="filter-grid">
            <input placeholder="session id" value={runFilters.session_id ?? ''} onChange={(event) => void updateRunFilter('session_id', event.target.value)} />
            <select value={runFilters.source ?? ''} onChange={(event) => void updateRunFilter('source', event.target.value)}>
              <option value="">全部来源</option>
              <option value="action">action</option>
              <option value="assistant">assistant</option>
            </select>
            <select value={runFilters.status ?? ''} onChange={(event) => void updateRunFilter('status', event.target.value)}>
              <option value="">全部状态</option>
              <option value="running">running</option>
              <option value="success">success</option>
              <option value="error">error</option>
            </select>
          </div>
          <div className="run-list">
            {runs.map((run) => (
              <article className={`run-item ${selectedRunId === run.id ? 'active' : ''}`} key={run.id}>
                <button className="run-main" onClick={() => void selectRun(run.id)}>
                  <strong>{run.source}</strong>
                  <span>{shortId(run.id)}</span>
                  <small>{formatTime(run.started_at)} · {run.status}</small>
                </button>
                <button className="delete-button" onClick={() => void deleteRun(run.id)}>删除</button>
              </article>
            ))}
            {!runs.length && <p className="empty-note">暂无运行记录。</p>}
          </div>
        </section>

        <section className="monitor-panel records-panel">
          <div className="panel-header">
            <div>
              <p className="monitor-eyebrow">Records</p>
              <h2>步骤</h2>
            </div>
            <div className="panel-buttons">
              <button onClick={() => void reloadRecords()}>重载</button>
              <button onClick={() => void clearRecords()}>删除筛选</button>
            </div>
          </div>
          <div className="filter-grid records-filter-grid">
            <input placeholder="session id" value={recordFilters.session_id ?? ''} onChange={(event) => void updateRecordFilter('session_id', event.target.value)} />
            <select value={recordFilters.source ?? ''} onChange={(event) => void updateRecordFilter('source', event.target.value)}>
              <option value="">全部来源</option>
              <option value="action">action</option>
              <option value="assistant">assistant</option>
            </select>
            <select value={recordFilters.agent_name ?? ''} onChange={(event) => void updateRecordFilter('agent_name', event.target.value)}>
              <option value="">全部 Agent</option>
              {agentOptions.map((agent) => <option value={agent} key={agent}>{agent}</option>)}
            </select>
            <select value={recordFilters.status ?? ''} onChange={(event) => void updateRecordFilter('status', event.target.value)}>
              <option value="">全部状态</option>
              <option value="success">success</option>
              <option value="warning">warning</option>
              <option value="error">error</option>
            </select>
          </div>
          {selectedRunId && <button className="selected-run-pill" onClick={() => void selectRun(selectedRunId)}>当前 run：{shortId(selectedRunId)} ×</button>}
          <div className="record-table">
            {filteredRecords.map((record) => (
              <article className={`record-row ${record.status}`} key={record.id}>
                <button className="record-main" onClick={() => setSelectedRecord(record)}>
                  <span className="record-seq">#{record.sequence}</span>
                  <strong>{record.agent_name}</strong>
                  <span>{record.step_name}</span>
                  <small>{record.phase} · {record.status} · {record.duration_ms ?? 0}ms</small>
                </button>
                <button className="delete-button" onClick={() => void deleteRecord(record.id)}>删除</button>
              </article>
            ))}
            {!filteredRecords.length && <p className="empty-note">暂无步骤记录。</p>}
          </div>
        </section>

        <aside className="monitor-panel detail-panel">
          <div className="panel-header">
            <div>
              <p className="monitor-eyebrow">Detail</p>
              <h2>输入输出</h2>
            </div>
          </div>
          {selectedRecord ? (
            <RecordDetail record={selectedRecord} />
          ) : (
            <p className="empty-note">选择一条步骤记录查看完整 JSON。</p>
          )}
          <form className="settings-form" onSubmit={applySettings}>
            <label>
              <span>全局存储上限</span>
              <input value={maxRecordsDraft} onChange={(event) => setMaxRecordsDraft(event.target.value)} inputMode="numeric" />
            </label>
            <button className="primary">保存上限</button>
          </form>
        </aside>
      </main>
    </div>
  );
}

function Metric({ label, value }: { label: string; value: string }) {
  return (
    <article className="metric">
      <span>{label}</span>
      <strong>{value}</strong>
    </article>
  );
}

function RecordDetail({ record }: { record: AgentTraceRecord }) {
  return (
    <div className="record-detail">
      <dl>
        <div><dt>ID</dt><dd>{record.id}</dd></div>
        <div><dt>Run</dt><dd>{record.run_id}</dd></div>
        <div><dt>Agent</dt><dd>{record.agent_name}</dd></div>
        <div><dt>Step</dt><dd>{record.step_name}</dd></div>
      </dl>
      {record.error && <div className="monitor-error">{record.error}</div>}
      <JsonBlock title="Input" value={record.input_payload} />
      <JsonBlock title="Output" value={record.output_payload} />
    </div>
  );
}

function JsonBlock({ title, value }: { title: string; value: unknown }) {
  return (
    <details className="json-block" open>
      <summary>{title}</summary>
      <pre>{formatJson(value)}</pre>
    </details>
  );
}

function upsertById<T extends { id: string }>(items: T[], item: T): T[] {
  const existing = items.findIndex((value) => value.id === item.id);
  if (existing >= 0) {
    const next = [...items];
    next[existing] = item;
    return next;
  }
  return [item, ...items];
}

function formatJson(value: unknown): string {
  try {
    return JSON.stringify(value, null, 2);
  } catch {
    return String(value);
  }
}

function formatTime(value: string): string {
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return '--:--:--';
  return date.toLocaleTimeString('zh-CN', { hour12: false });
}

function shortId(value: string): string {
  return value.slice(0, 8);
}

function connectionLabel(value: string): string {
  const labels: Record<string, string> = {
    connected: '实时已连接',
    connecting: '连接中',
    disconnected: '已断开',
    paused: '实时已暂停',
  };
  return labels[value] ?? value;
}

createRoot(document.getElementById('root')!).render(<MonitorApp />);
