import { useState, useEffect } from 'react';
import axios from 'axios';
import { Play, Pause, RefreshCw, ExternalLink } from 'lucide-react';

interface DownloadData {
  id: number;
  sender: string;
  extracted_url: string;
  status: string;
  youtube_video_id: string;
  audio_title: string | null;
  channel_name: string | null;
  duration: number | null;
  error: string | null;
  audio_path: string | null;
}

export default function DownloadsPage() {
  const [downloads, setDownloads] = useState<DownloadData[]>([]);
  const [loading, setLoading] = useState(true);
  const [stats, setStats] = useState<any>(null);
  const [isRunning, setIsRunning] = useState(false);
  const [progressMap, setProgressMap] = useState<Record<number, string>>({});

  useEffect(() => {
    fetchDownloads();
    fetchStats();
    fetchStatus();
    
    const ws = new WebSocket('ws://localhost:8000/api/ws');
    
    ws.onmessage = (event) => {
      if (event.data === 'update') {
        fetchDownloads();
        fetchStats();
        fetchStatus();
      } else {
        try {
          const data = JSON.parse(event.data);
          if (data.type === 'progress') {
            setProgressMap(prev => ({ ...prev, [data.id]: data.percent }));
          }
        } catch (e) {}
      }
    };

    return () => {
      ws.close();
    };
  }, []);

  const fetchStatus = async () => {
    try {
      const res = await axios.get('http://localhost:8000/api/downloads/status');
      setIsRunning(res.data.is_running);
    } catch (err) {
      console.error(err);
    }
  };

  const fetchDownloads = async () => {
    try {
      const res = await axios.get('http://localhost:8000/api/downloads');
      setDownloads(res.data);
    } catch (err) {
      console.error(err);
    } finally {
      setLoading(false);
    }
  };

  const fetchStats = async () => {
    try {
      const res = await axios.get('http://localhost:8000/api/stats');
      setStats(res.data);
    } catch (err) {
      console.error(err);
    }
  };

  const startLoop = async () => {
    setIsRunning(true);
    await axios.post('http://localhost:8000/api/downloads/start');
  };

  const pauseLoop = async () => {
    setIsRunning(false);
    await axios.post('http://localhost:8000/api/downloads/pause');
  };

  const retryDownload = async (id: number) => {
    await axios.post(`http://localhost:8000/api/downloads/retry/${id}`);
    fetchDownloads();
  };

  return (
    <div>
      <div className="row-header">
        <div style={{display: 'flex', alignItems: 'center', gap: '16px'}}>
          <h1>Downloads</h1>
          {isRunning && (
            <div style={{display: 'flex', alignItems: 'center', gap: '8px', color: 'var(--match-score)', fontSize: '14px', fontWeight: 'bold'}}>
              <div style={{width: '10px', height: '10px', borderRadius: '50%', backgroundColor: 'var(--match-score)', animation: 'pulse 1.5s infinite'}}></div>
              Processing Active
            </div>
          )}
        </div>
        <div style={{display: 'flex', gap: '16px'}}>
          <button 
            className="btn btn-play" 
            onClick={startLoop}
            disabled={isRunning}
            style={{ opacity: isRunning ? 0.5 : 1, cursor: isRunning ? 'not-allowed' : 'pointer' }}
          >
            <Play size={18} fill="currentColor" /> {isRunning ? 'Running...' : 'Process All'}
          </button>
          <button 
            className="btn" 
            onClick={pauseLoop}
            disabled={!isRunning}
            style={{ opacity: !isRunning ? 0.5 : 1, cursor: !isRunning ? 'not-allowed' : 'pointer' }}
          >
            <Pause size={18} fill={!isRunning ? 'none' : 'currentColor'} /> Pause
          </button>
        </div>
      </div>

      {stats && (
        <div style={{display: 'flex', gap: '32px', marginBottom: '32px', background: 'var(--bg-surface)', padding: '16px 24px', borderRadius: '4px'}}>
          <div style={{color: 'var(--text-secondary)'}}>Pending: <strong style={{color: '#eab308', marginLeft: '8px', fontSize: '18px'}}>{stats.pending_download}</strong></div>
          <div style={{color: 'var(--text-secondary)'}}>Downloaded: <strong style={{color: 'var(--match-score)', marginLeft: '8px', fontSize: '18px'}}>{stats.downloaded}</strong></div>
          <div style={{color: 'var(--text-secondary)'}}>Failed: <strong style={{color: 'var(--netflix-red)', marginLeft: '8px', fontSize: '18px'}}>{stats.failed_download}</strong></div>
        </div>
      )}

      {loading ? (
        <p style={{color: 'var(--text-secondary)'}}>Loading...</p>
      ) : (
        <div className="card-grid">
          {downloads.map((dl) => {
            const thumbnailUrl = dl.youtube_video_id 
              ? `https://img.youtube.com/vi/${dl.youtube_video_id}/mqdefault.jpg`
              : 'https://via.placeholder.com/320x180/1F1F1F/BCBCBC?text=No+Thumbnail';

            return (
              <div key={dl.id} className="card">
                <div className="card-image-wrapper">
                  <img src={thumbnailUrl} alt="Thumbnail" className="card-image" />
                  <div className="card-overlay">
                    <div className="card-title">{dl.audio_title || 'Unknown Title'}</div>
                  </div>
                </div>
                
                <div className="card-info">
                  <div className="card-meta" style={{justifyContent: 'space-between', marginBottom: '4px'}}>
                    <span className={`badge ${dl.status.toLowerCase()}`}>
                      {(() => {
                        // Websocket realtime override
                        if (progressMap[dl.id]) {
                          if (progressMap[dl.id].includes('%') && !progressMap[dl.id].includes('TRANSCRIBING')) {
                            return `DOWNLOADING (${progressMap[dl.id]})`;
                          }
                          return progressMap[dl.id];
                        }
                        
                        // DB Fallback logic
                        if (dl.status === 'COMPLETED') {
                          if (dl.classification_status === 'COMPLETED') return 'CATEGORIZED';
                          if (dl.classification_status === 'CLASSIFYING') return 'AI CATEGORIZING...';
                          if (dl.transcription_status === 'TRANSCRIBING') return 'TRANSCRIBING...';
                          if (dl.transcription_status === 'COMPLETED' && dl.classification_status === 'PENDING') return 'WAITING AI...';
                          return 'QUEUED FOR TRANSCRIBE...';
                        }
                        return dl.status;
                      })()}
                    </span>
                    <span>{dl.duration ? `${dl.duration}s` : ''}</span>
                  </div>

                  <div style={{fontSize: '14px', marginBottom: '4px'}}>
                    <span style={{color: 'var(--text-muted)'}}>{dl.channel_name || 'Unknown Channel'}</span>
                  </div>

                  {dl.error && (
                    <div style={{fontSize: '12px', color: 'var(--netflix-red)', marginTop: '8px', background: 'rgba(229, 9, 20, 0.1)', padding: '8px', borderRadius: '4px'}}>
                      {dl.error}
                    </div>
                  )}
                  
                  <div style={{marginTop: 'auto', paddingTop: '12px', display: 'flex', flexDirection: 'column', gap: '8px'}}>
                    {dl.status === 'COMPLETED' && dl.audio_path && (
                      <div style={{fontSize: '11px', color: 'var(--text-muted)', wordBreak: 'break-all', background: 'rgba(255,255,255,0.05)', padding: '6px 8px', borderRadius: '4px', borderLeft: '2px solid var(--match-score)'}}>
                        <strong style={{color: '#fff'}}>Location:</strong><br/>
                        {dl.audio_path}
                      </div>
                    )}
                    
                    <div style={{display: 'flex', justifyContent: 'space-between', alignItems: 'center'}}>
                      <a href={dl.extracted_url} target="_blank" rel="noreferrer" style={{color: 'var(--text-secondary)', textDecoration: 'none', display: 'flex', alignItems: 'center', gap: '4px', fontSize: '12px'}}>
                        <ExternalLink size={12} /> Source
                      </a>


                    {dl.status === 'FAILED' && (
                      <button className="btn" style={{padding: '6px 12px', fontSize: '12px'}} onClick={() => retryDownload(dl.id)}>
                        <RefreshCw size={12} /> Retry
                      </button>
                    )}
                    </div>
                  </div>
                </div>
              </div>
            );
          })}
          {downloads.length === 0 && (
             <div style={{gridColumn: '1 / -1', padding: '64px', textAlign: 'center', color: 'var(--text-secondary)'}}>
               <h2>No downloads pending</h2>
             </div>
          )}
        </div>
      )}
    </div>
  );
}
