import { useState, useEffect, useRef } from 'react';
import axios from 'axios';
import { UploadCloud, CheckCircle, XCircle, X, Play, Pause, Activity } from 'lucide-react';

interface ImportProgress {
  import_id: string;
  filename: string;
  phase: string;
  messages_scanned: number;
  links_found: number;
  metadata_total: number;
  metadata_fetched: number;
  saved: number;
  duplicates: number;
  senders_found: number;
  error: string | null;
  done: boolean;
  canceled: boolean;
  total: number | null;
}

interface LoopStatus {
  is_running: boolean;
  phase: string;
  pending: number;
  active: number;
  idle_seconds: number;
}

interface Stats {
  total_links: number;
  pending: number;
  downloading: number;
  transcribing: number;
  categorizing: number;
  completed: number;
  failed: number;
}

const PHASE_LABELS: Record<string, string> = {
  parsing: 'Parsing chat...',
  metadata: 'Fetching metadata',
  saving: 'Saving links',
  done: 'Import complete',
  error: 'Import failed',
  canceled: 'Canceled',
};

export default function ImportPage() {
  const [file, setFile] = useState<File | null>(null);
  const [uploading, setUploading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [importId, setImportId] = useState<string | null>(null);
  const [progress, setProgress] = useState<ImportProgress | null>(null);
  const [loopStatus, setLoopStatus] = useState<LoopStatus | null>(null);
  const [stats, setStats] = useState<Stats | null>(null);
  const [starting, setStarting] = useState(false);
  const wsRef = useRef<WebSocket | null>(null);
  const importIdRef = useRef<string | null>(null);

  const isActive = uploading && importId !== null && !(progress?.done);

  const handleFileChange = (e: React.ChangeEvent<HTMLInputElement>) => {
    if (e.target.files && e.target.files[0]) {
      setFile(e.target.files[0]);
    }
  };

  const handleUpload = async () => {
    if (!file) return;
    setUploading(true);
    setError(null);
    setProgress(null);
    setImportId(null);

    const formData = new FormData();
    formData.append('file', file);

    try {
      const res = await axios.post('http://localhost:8000/api/upload', formData);
      if (res.data && res.data.error) {
        setError(res.data.error);
        setUploading(false);
      } else {
        setImportId(res.data.import_id);
        importIdRef.current = res.data.import_id;
      }
    } catch (err) {
      if (axios.isAxiosError(err)) {
        setError(err.response?.data?.error || 'Upload failed');
      } else {
        setError('Upload failed');
      }
      setUploading(false);
    }
  };

  const startDownloads = async () => {
    setStarting(true);
    setError(null);
    try {
      await axios.post('http://localhost:8000/api/downloads/start');
      const res = await axios.get('http://localhost:8000/api/downloads/status');
      setLoopStatus(res.data);
    } catch (err) {
      setError('Failed to start downloads');
    } finally {
      setStarting(false);
    }
  };

  const cancelImport = async () => {
    if (!importId) return;
    try {
      await axios.delete(`http://localhost:8000/api/imports/${importId}`);
    } catch (err) {
      console.error(err);
    }
  };

  // Live loop status + stats board — always connected so the user always
  // knows what the backend is doing (no indeterminate stalls).
  useEffect(() => {
    const fetchStatus = async () => {
      try {
        const res = await axios.get('http://localhost:8000/api/downloads/status');
        setLoopStatus(res.data);
      } catch (err) {}
    };
    const fetchStats = async () => {
      try {
        const res = await axios.get('http://localhost:8000/api/stats');
        setStats(res.data);
      } catch (err) {}
    };
    fetchStatus();
    fetchStats();

    const pollTimer = window.setInterval(() => {
      fetchStatus();
      fetchStats();
    }, 3000);

    let reconnectTimeout: number;
    const connect = () => {
      const ws = new WebSocket('ws://localhost:8000/api/ws');
      ws.onmessage = (event) => {
        if (event.data === 'update') {
          fetchStatus();
          fetchStats();
          return;
        }
        try {
          const data = JSON.parse(event.data);
          if (data.type === 'status') {
            setLoopStatus(data);
          } else if (data.type === 'import' && data.import_id === importIdRef.current) {
            setProgress(data);
            if (data.done) setUploading(false);
          }
        } catch (e) {}
      };
      ws.onclose = () => {
        reconnectTimeout = window.setTimeout(connect, 3000);
      };
    };
    connect();

    return () => {
      clearInterval(pollTimer);
      clearTimeout(reconnectTimeout);
    };
  }, []);

  // Live import progress while an import is active
  useEffect(() => {
    if (!isActive || !importId) return;

    const pollTimer = window.setInterval(async () => {
      try {
        const res = await axios.get(`http://localhost:8000/api/imports/${importId}`);
        setProgress(res.data);
        if (res.data.done) setUploading(false);
      } catch (err) {
        console.error(err);
      }
    }, 2000);

    return () => {
      clearInterval(pollTimer);
      wsRef.current?.close();
      wsRef.current = null;
    };
  }, [isActive, importId]);

  const done = progress?.phase === 'metadata' ? progress.metadata_fetched : progress?.saved ?? 0;
  const pct = progress?.total && progress.total > 0
    ? Math.min(100, Math.round((done / progress.total) * 100))
    : 0;
  const remaining = progress?.total != null ? Math.max(0, progress.total - done) : null;

  const phaseLabel = progress ? (PHASE_LABELS[progress.phase] || progress.phase) : '';

  return (
    <div>
      <div className="row-header">
        <h1>Import WhatsApp Chat</h1>
      </div>
      
      <p style={{color: 'var(--text-secondary)', fontSize: '16px'}}>
        Upload your exported WhatsApp chat zip to automatically extract and categorise YouTube Music links.
      </p>

      <div 
        className="file-drop-zone" 
        onClick={() => document.getElementById('file-upload')?.click()}
      >
        <UploadCloud size={64} color="var(--netflix-red)" style={{marginBottom: '16px'}} />
        <h2 style={{marginBottom: '8px'}}>{file ? file.name : 'Click or drag a ZIP file here'}</h2>
        <p className="text-muted">Must contain the exported _chat.txt file</p>
        <input 
          id="file-upload" 
          type="file" 
          accept=".zip" 
          style={{display: 'none'}} 
          onChange={handleFileChange}
        />
      </div>

      <div style={{marginTop: '32px', display: 'flex', justifyContent: 'center'}}>
        <button 
          className="btn btn-primary" 
          style={{fontSize: '18px', padding: '16px 32px'}}
          onClick={handleUpload} 
          disabled={!file || uploading}
        >
          <UploadCloud size={20} style={{verticalAlign: 'middle', marginRight: '8px'}} />
          {uploading ? 'Processing...' : 'Process Import'}
        </button>
      </div>
      <p style={{textAlign: 'center', color: 'var(--text-muted)', fontSize: '13px', marginTop: '8px'}}>
        Step 1 of 2 — parsing the chat. Downloads start only when you press "Start Downloading" below.
      </p>

      {/* ---------- LIVE PROCESSING BOARD: always visible so the user is never left guessing ---------- */}
      <div style={{marginTop: '32px', background: 'var(--bg-surface)', borderRadius: '6px', padding: '24px', maxWidth: '640px', marginLeft: 'auto', marginRight: 'auto'}}>
        <div style={{display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: '16px'}}>
          <h2 style={{margin: 0, display: 'flex', alignItems: 'center', gap: '8px'}}>
            <Activity size={20} color="var(--text-secondary)" /> Processing Status
          </h2>
          {loopStatus && (
            <span style={{fontSize: '13px', fontWeight: 'bold'}}>
              {loopStatus.is_running ? (
                loopStatus.phase === 'working' ? (
                  <span style={{color: 'var(--match-score)', display: 'inline-flex', alignItems: 'center', gap: '6px'}}>
                    <span style={{width: '10px', height: '10px', borderRadius: '50%', backgroundColor: 'var(--match-score)', animation: 'pulse 1.5s infinite', display: 'inline-block'}} />
                    PROCESSING ACTIVE
                  </span>
                ) : (
                  <span style={{color: '#eab308', display: 'inline-flex', alignItems: 'center', gap: '6px'}}>
                    <span style={{width: '10px', height: '10px', borderRadius: '50%', backgroundColor: '#eab308', animation: 'pulse 1.5s infinite', display: 'inline-block'}} />
                    WAITING FOR WORK
                  </span>
                )
              ) : (
                <span style={{color: 'var(--text-secondary)'}}>PAUSED</span>
              )}
            </span>
          )}
        </div>

        <div style={{display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(100px, 1fr))', gap: '16px', marginBottom: '20px'}}>
          <div><div style={{color: 'var(--text-muted)', fontSize: '12px'}}>Total</div><div style={{fontSize: '20px', fontWeight: 'bold'}}>{stats?.total_links ?? '—'}</div></div>
          <div><div style={{color: 'var(--text-muted)', fontSize: '12px'}}>Pending</div><div style={{fontSize: '20px', fontWeight: 'bold', color: '#eab308'}}>{stats?.pending ?? '—'}</div></div>
          <div><div style={{color: 'var(--text-muted)', fontSize: '12px'}}>Downloading</div><div style={{fontSize: '20px', fontWeight: 'bold', color: 'var(--badge-new)'}}>{stats?.downloading ?? '—'}</div></div>
          <div><div style={{color: 'var(--text-muted)', fontSize: '12px'}}>Transcribing</div><div style={{fontSize: '20px', fontWeight: 'bold', color: 'var(--badge-new)'}}>{stats?.transcribing ?? '—'}</div></div>
          <div><div style={{color: 'var(--text-muted)', fontSize: '12px'}}>Categorizing</div><div style={{fontSize: '20px', fontWeight: 'bold', color: '#c084fc'}}>{stats?.categorizing ?? '—'}</div></div>
          <div><div style={{color: 'var(--text-muted)', fontSize: '12px'}}>Completed</div><div style={{fontSize: '20px', fontWeight: 'bold', color: 'var(--match-score)'}}>{stats?.completed ?? '—'}</div></div>
          <div><div style={{color: 'var(--text-muted)', fontSize: '12px'}}>Failed</div><div style={{fontSize: '20px', fontWeight: 'bold', color: 'var(--netflix-red)'}}>{stats?.failed ?? '—'}</div></div>
        </div>

        <div style={{display: 'flex', justifyContent: 'space-between', alignItems: 'center', gap: '16px', borderTop: '1px solid rgba(255,255,255,0.08)', paddingTop: '16px'}}>
          <div style={{fontSize: '13px', color: 'var(--text-secondary)'}}>
            {!loopStatus ? 'Checking server status...' : loopStatus.is_running ? (
              loopStatus.phase === 'working'
                ? `Working on it — last activity ${Math.max(0, Math.round(loopStatus.idle_seconds))}s ago${loopStatus.active > 0 ? `, ${loopStatus.active} link(s) in flight` : ''}`
                : 'All caught up — the loop is waiting for new links.'
            ) : stats && stats.pending > 0
              ? `${stats.pending} link(s) ready. Press "Start Downloading" to begin.`
              : 'Downloads are paused. Press "Start Downloading" to resume.'
            }
          </div>
          <button
            className="btn btn-play"
            onClick={startDownloads}
            disabled={loopStatus?.is_running || starting}
            style={{whiteSpace: 'nowrap', opacity: loopStatus?.is_running ? 0.5 : 1, cursor: loopStatus?.is_running ? 'not-allowed' : 'pointer'}}
          >
            {loopStatus?.is_running ? (
              <><Pause size={16} fill="currentColor" /> Processing Active</>
            ) : (
              <><Play size={16} fill="currentColor" /> {starting ? 'Starting...' : 'Start Downloading'}</>
            )}
          </button>
        </div>
      </div>

      {isActive && progress && (
        <div style={{marginTop: '32px', maxWidth: '520px', margin: '32px auto 0'}}>
          <div className="progress-label">
            <strong>{phaseLabel}</strong>
            {progress.phase === 'parsing' && ` — ${progress.messages_scanned} messages scanned`}
            {progress.phase === 'metadata' && ` (${progress.metadata_fetched}/${progress.metadata_total})`}
            {progress.phase === 'saving' && ` (${progress.saved}/${progress.total})`}
          </div>
          <div className="progress-track">
            <div 
              className={`progress-fill${progress.total == null ? ' indeterminate' : ''}`}
              style={progress.total != null ? {width: `${pct}%`} : undefined}
            />
          </div>
          <div style={{display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginTop: '8px'}}>
            <span style={{color: 'var(--text-muted)', fontSize: '13px'}}>
              {progress.total != null
                ? `${done} of ${progress.total} done${remaining !== null ? ` — ${remaining} left` : ''}`
                : 'Reading chat file...'}
            </span>
            <button className="btn" style={{padding: '6px 12px', fontSize: '12px'}} onClick={cancelImport}>
              <X size={12} /> Cancel
            </button>
          </div>
        </div>
      )}

      {error && (
        <div style={{marginTop: '32px', padding: '16px', background: 'var(--bg-surface-elevated)', color: 'var(--netflix-red)', borderRadius: '4px', textAlign: 'center'}}>
          <strong>{error}</strong>
        </div>
      )}

      {progress?.done && progress.phase === 'done' && (
        <div style={{marginTop: '32px', padding: '24px', background: 'var(--bg-surface-elevated)', borderRadius: '6px', maxWidth: '400px', margin: '32px auto 0'}}>
          <h3 style={{display: 'flex', alignItems: 'center', gap: '8px', color: 'var(--match-score)', marginBottom: '16px', justifyContent: 'center'}}>
            <CheckCircle size={24} /> Import Complete
          </h3>
          <div style={{display: 'flex', flexDirection: 'column', gap: '12px'}}>
            <div style={{display: 'flex', justifyContent: 'space-between'}}>
              <span style={{color: 'var(--text-secondary)'}}>Messages Found</span>
              <strong style={{fontSize: '18px'}}>{progress.messages_scanned}</strong>
            </div>
            <div style={{display: 'flex', justifyContent: 'space-between'}}>
              <span style={{color: 'var(--text-secondary)'}}>URLs Found</span>
              <strong style={{fontSize: '18px'}}>{progress.links_found}</strong>
            </div>
            <div style={{display: 'flex', justifyContent: 'space-between'}}>
              <span style={{color: 'var(--text-secondary)'}}>URLs Saved</span>
              <strong style={{fontSize: '18px'}}>{progress.saved}</strong>
            </div>
            <div style={{display: 'flex', justifyContent: 'space-between'}}>
              <span style={{color: 'var(--text-secondary)'}}>Duplicates</span>
              <strong style={{fontSize: '18px'}}>{progress.duplicates}</strong>
            </div>
            <div style={{display: 'flex', justifyContent: 'space-between'}}>
              <span style={{color: 'var(--text-secondary)'}}>Senders Found</span>
              <strong style={{fontSize: '18px'}}>{progress.senders_found}</strong>
            </div>
          </div>
        </div>
      )}

      {progress?.done && progress.phase === 'error' && (
        <div style={{marginTop: '32px', padding: '16px', background: 'var(--bg-surface-elevated)', color: 'var(--netflix-red)', borderRadius: '4px', textAlign: 'center'}}>
          <strong><XCircle size={18} style={{verticalAlign: 'middle', marginRight: '6px'}} />Import failed: {progress.error}</strong>
        </div>
      )}

      {progress?.done && progress.phase === 'canceled' && (
        <div style={{marginTop: '32px', padding: '16px', background: 'var(--bg-surface-elevated)', color: 'var(--text-secondary)', borderRadius: '4px', textAlign: 'center'}}>
          <strong>Import canceled.</strong>
        </div>
      )}
    </div>
  );
}
