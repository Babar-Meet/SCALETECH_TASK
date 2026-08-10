import { useState, useEffect, useRef, useCallback } from 'react';
import { Terminal, Trash2, ChevronUp, ChevronDown, Copy, Check, Wifi, WifiOff } from 'lucide-react';

interface LogEntry {
  type: string;
  timestamp: string;
  level: string;
  source: string;
  message: string;
}

const LEVEL_COLORS: Record<string, string> = {
  DEBUG:    '#6b7280',
  INFO:     '#3b82f6',
  WARNING:  '#eab308',
  ERROR:    '#ef4444',
  CRITICAL: '#dc2626',
};

const SOURCE_COLORS: Record<string, string> = {
  main:           '#a78bfa',
  downloader:     '#34d399',
  ai_processor:   '#f472b6',
  'uvicorn.error':       '#94a3b8',
  'uvicorn.access':      '#64748b',
};

function getSourceColor(source: string): string {
  return SOURCE_COLORS[source] || '#94a3b8';
}

export default function LogPanel() {
  const [logs, setLogs] = useState<LogEntry[]>([]);
  const [expanded, setExpanded] = useState(false);
  const [connected, setConnected] = useState(false);
  const [copied, setCopied] = useState(false);
  const [filter, setFilter] = useState<string>('ALL');
  const [autoScroll, setAutoScroll] = useState(true);
  const scrollRef = useRef<HTMLDivElement>(null);
  const wsRef = useRef<WebSocket | null>(null);

  // Connect to log WebSocket
  useEffect(() => {
    let ws: WebSocket;
    let reconnectTimeout: number;

    const connect = () => {
      ws = new WebSocket('ws://localhost:8000/api/ws/logs');

      ws.onopen = () => {
        setConnected(true);
      };

      ws.onmessage = (event) => {
        try {
          const data = JSON.parse(event.data);
          if (data.type === 'log') {
            setLogs(prev => {
              const next = [...prev, data];
              // Keep last 1000 entries in memory
              return next.length > 1000 ? next.slice(-1000) : next;
            });
          }
        } catch {}
      };

      ws.onclose = () => {
        setConnected(false);
        reconnectTimeout = window.setTimeout(connect, 3000);
      };

      ws.onerror = () => {
        ws.close();
      };

      wsRef.current = ws;
    };

    connect();

    return () => {
      clearTimeout(reconnectTimeout);
      ws?.close();
    };
  }, []);

  // Auto-scroll
  useEffect(() => {
    if (autoScroll && scrollRef.current && expanded) {
      scrollRef.current.scrollTop = scrollRef.current.scrollHeight;
    }
  }, [logs, expanded, autoScroll]);

  const handleScroll = useCallback(() => {
    if (!scrollRef.current) return;
    const { scrollTop, scrollHeight, clientHeight } = scrollRef.current;
    setAutoScroll(scrollHeight - scrollTop - clientHeight < 40);
  }, []);

  const clearLogs = async () => {
    try {
      await fetch('http://localhost:8000/api/logs', { method: 'DELETE' });
      setLogs([]);
    } catch {}
  };

  const copyLogs = () => {
    const filteredLogs = getFilteredLogs();
    const text = filteredLogs.map(l =>
      `[${l.timestamp}] ${l.level}/${l.source}: ${l.message}`
    ).join('\n');
    navigator.clipboard.writeText(text);
    setCopied(true);
    setTimeout(() => setCopied(false), 2000);
  };

  const getFilteredLogs = (): LogEntry[] => {
    if (filter === 'ALL') return logs;
    if (filter === 'ERRORS') return logs.filter(l => l.level === 'ERROR' || l.level === 'CRITICAL' || l.level === 'WARNING');
    return logs.filter(l => l.source === filter);
  };

  const filteredLogs = getFilteredLogs();
  const errorCount = logs.filter(l => l.level === 'ERROR' || l.level === 'CRITICAL').length;
  const warnCount = logs.filter(l => l.level === 'WARNING').length;

  // Unique sources for filter dropdown
  const sources = Array.from(new Set(logs.map(l => l.source))).sort();

  return (
    <div className="log-panel" data-expanded={expanded}>
      {/* Header / Toggle bar */}
      <div className="log-panel-header" onClick={() => setExpanded(!expanded)}>
        <div className="log-panel-title">
          <Terminal size={16} />
          <span>System Logs</span>
          {connected ? (
            <Wifi size={14} style={{ color: 'var(--match-score)' }} />
          ) : (
            <WifiOff size={14} style={{ color: 'var(--netflix-red)' }} />
          )}
          {!expanded && logs.length > 0 && (
            <span className="log-count-badge">{logs.length}</span>
          )}
          {!expanded && errorCount > 0 && (
            <span className="log-error-badge">{errorCount} error{errorCount > 1 ? 's' : ''}</span>
          )}
          {!expanded && warnCount > 0 && (
            <span className="log-warn-badge">{warnCount} warn</span>
          )}
        </div>
        <div className="log-panel-actions" onClick={(e) => e.stopPropagation()}>
          {expanded && (
            <>
              <select
                className="log-filter-select"
                value={filter}
                onChange={(e) => setFilter(e.target.value)}
              >
                <option value="ALL">All Sources</option>
                <option value="ERRORS">Errors & Warnings</option>
                {sources.map(s => (
                  <option key={s} value={s}>{s}</option>
                ))}
              </select>
              <button className="log-action-btn" onClick={copyLogs} title="Copy logs to clipboard">
                {copied ? <Check size={14} /> : <Copy size={14} />}
              </button>
              <button className="log-action-btn" onClick={clearLogs} title="Clear logs">
                <Trash2 size={14} />
              </button>
            </>
          )}
          <button className="log-action-btn" onClick={() => setExpanded(!expanded)}>
            {expanded ? <ChevronDown size={16} /> : <ChevronUp size={16} />}
          </button>
        </div>
      </div>

      {/* Log body */}
      {expanded && (
        <div className="log-panel-body" ref={scrollRef} onScroll={handleScroll}>
          {filteredLogs.length === 0 ? (
            <div className="log-empty">No logs yet. Backend actions will appear here in real-time.</div>
          ) : (
            filteredLogs.map((log, i) => (
              <div key={i} className={`log-entry log-${log.level.toLowerCase()}`}>
                <span className="log-ts">{log.timestamp.split(' ')[1] || log.timestamp}</span>
                <span className="log-level" style={{ color: LEVEL_COLORS[log.level] || '#fff' }}>
                  {log.level.substring(0, 4)}
                </span>
                <span className="log-source" style={{ color: getSourceColor(log.source) }}>
                  {log.source}
                </span>
                <span className="log-msg">{log.message}</span>
              </div>
            ))
          )}
        </div>
      )}
    </div>
  );
}
