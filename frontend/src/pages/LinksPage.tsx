import { useState, useEffect } from 'react';
import axios from 'axios';
import { DownloadCloud, Trash2, Check, ExternalLink } from 'lucide-react';

interface LinkData {
  id: number;
  sender: string;
  extracted_url: string;
  status: string;
  category: string | null;
  youtube_video_id: string | null;
  audio_title: string | null;
  channel_name: string | null;
}

export default function LinksPage() {
  const [links, setLinks] = useState<LinkData[]>([]);
  const [loading, setLoading] = useState(true);
  const [exporting, setExporting] = useState(false);
  const [editingCategory, setEditingCategory] = useState<{id: number, value: string} | null>(null);
  const [isRunning, setIsRunning] = useState(false);
  const [progressMap, setProgressMap] = useState<Record<number, string>>({});

  useEffect(() => {
    fetchLinks();
    fetchStatus();
    
    let ws: WebSocket;
    let reconnectTimeout: number;
    const connect = () => {
      ws = new WebSocket('ws://localhost:8000/api/ws');
      ws.onmessage = (event) => {
        if (event.data === 'update') {
          fetchLinks();
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
      ws.onclose = () => {
        reconnectTimeout = window.setTimeout(connect, 3000);
      };
    };
    connect();

    // Safety net: periodic refresh in case the websocket drops silently
    const refreshTimer = window.setInterval(() => {
      fetchLinks();
      fetchStatus();
    }, 20000);

    return () => {
      clearTimeout(reconnectTimeout);
      clearInterval(refreshTimer);
      ws?.close();
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

  const startLoop = async () => {
    setIsRunning(true);
    await axios.post('http://localhost:8000/api/downloads/start');
  };

  const fetchLinks = async () => {
    try {
      const res = await axios.get('http://localhost:8000/api/links');
      setLinks(res.data);
    } catch (err) {
      console.error(err);
    } finally {
      setLoading(false);
    }
  };

  const exportExcel = async () => {
    setExporting(true);
    try {
      const res = await axios.post('http://localhost:8000/api/export');
      alert(`Exported to: ${res.data.filepath}`);
    } catch (err) {
      console.error(err);
      alert('Export failed');
    } finally {
      setExporting(false);
    }
  };

  const clearAll = async () => {
    if (!window.confirm("Are you sure you want to clear all imported links? This cannot be undone.")) return;
    try {
      await axios.delete('http://localhost:8000/api/links');
      setLinks([]);
    } catch (err) {
      console.error(err);
      alert('Failed to clear links');
    }
  };

  const saveCategory = async (id: number) => {
    if (!editingCategory) return;
    try {
      await axios.put(`http://localhost:8000/api/links/${id}/category`, {
        category: editingCategory.value
      });
      setLinks(links.map(l => l.id === id ? { ...l, category: editingCategory.value } : l));
      setEditingCategory(null);
    } catch (err) {
      console.error('Failed to save category', err);
      alert('Failed to save category');
    }
  };

  const cleanNonYT = async () => {
    if (!window.confirm('Delete all non-YouTube links from the database?')) return;
    try {
      await axios.delete('http://localhost:8000/api/links/clean/non-youtube');
      fetchLinks();
    } catch (e) {
      console.error(e);
    }
  };

  const deleteLink = async (id: number) => {
    if (!window.confirm('Delete this link?')) return;
    try {
      await axios.delete(`http://localhost:8000/api/links/${id}`);
      fetchLinks();
    } catch (e) {
      console.error(e);
    }
  };

  return (
    <div>
      <div className="row-header">
        <div style={{display: 'flex', alignItems: 'center', gap: '16px'}}>
          <h1>My List</h1>
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
            <DownloadCloud size={18} fill="currentColor" /> {isRunning ? 'Processing...' : 'Process'}
          </button>
          <button className="btn" onClick={cleanNonYT} style={{color: 'var(--text-secondary)'}}>
            <Trash2 size={18} />
            Clean Non-YT
          </button>
          <button className="btn" onClick={clearAll} style={{color: 'var(--netflix-red)'}}>
            <Trash2 size={18} />
            Remove All
          </button>
          <button className="btn" onClick={exportExcel} disabled={exporting}>
            <DownloadCloud size={18} />
            {exporting ? 'Exporting...' : 'Export Excel'}
          </button>
        </div>
      </div>

      {loading ? (
        <p style={{color: 'var(--text-secondary)'}}>Loading...</p>
      ) : (
        <div className="card-grid">
          {links.map((link) => {
            const thumbnailUrl = link.youtube_video_id 
              ? `https://img.youtube.com/vi/${link.youtube_video_id}/mqdefault.jpg`
              : 'https://via.placeholder.com/320x180/1F1F1F/BCBCBC?text=No+Thumbnail';

            return (
              <div key={link.id} className="card">
                <div className="card-image-wrapper">
                  <img src={thumbnailUrl} alt="Thumbnail" className="card-image" />
                  <div className="card-overlay">
                    <div className="card-title">{link.audio_title || 'Unknown Title'}</div>
                  </div>
                </div>
                
                <div className="card-info">
                  <div className="card-meta" style={{justifyContent: 'space-between', marginBottom: '4px'}}>
                    <span className={`match-score badge ${link.status.toLowerCase()}`}>
                      {link.status === 'DOWNLOADING' && progressMap[link.id] ? `DOWNLOADING (${progressMap[link.id]})` : link.status}
                    </span>
                    <span>{link.channel_name || 'Unknown Channel'}</span>
                  </div>
                  
                  <div style={{fontSize: '14px', marginBottom: '8px'}}>
                    <span style={{color: 'var(--text-muted)'}}>Sender: </span>
                    <span>{link.sender}</span>
                  </div>

                  <div style={{display: 'flex', alignItems: 'center', gap: '8px'}}>
                    <span style={{color: 'var(--text-muted)', fontSize: '14px'}}>Category:</span>
                    {editingCategory?.id === link.id ? (
                      <div style={{ display: 'flex', gap: '4px', flex: 1 }}>
                        <input 
                          type="text" 
                          value={editingCategory.value}
                          onChange={(e) => setEditingCategory({...editingCategory, value: e.target.value})}
                          placeholder="e.g. Pop"
                        />
                        <button className="btn btn-primary" style={{padding: '8px'}} onClick={() => saveCategory(link.id)}>
                          <Check size={16} />
                        </button>
                      </div>
                    ) : (
                      <div 
                        style={{ cursor: 'pointer', flex: 1, borderBottom: '1px dashed #8C8C8C', paddingBottom: '2px', fontSize: '14px' }}
                        onClick={() => setEditingCategory({id: link.id, value: link.category || ''})}
                      >
                        {link.category || <span style={{opacity: 0.5}}>Set category...</span>}
                      </div>
                    )}
                  </div>
                  
                  <div style={{marginTop: 'auto', paddingTop: '8px', display: 'flex', justifyContent: 'space-between', alignItems: 'center'}}>
                    <a href={link.extracted_url} target="_blank" rel="noreferrer" style={{color: 'var(--text-primary)', textDecoration: 'none', display: 'flex', alignItems: 'center', gap: '4px', fontSize: '12px'}}>
                      <ExternalLink size={12} /> Watch Original
                    </a>
                    <button 
                      onClick={() => deleteLink(link.id)} 
                      style={{background: 'none', border: 'none', color: 'var(--netflix-red)', cursor: 'pointer', padding: '4px'}}
                      title="Remove Link"
                    >
                      <Trash2 size={18} />
                    </button>
                  </div>
                </div>
              </div>
            );
          })}
          {links.length === 0 && (
            <div style={{gridColumn: '1 / -1', padding: '64px', textAlign: 'center', color: 'var(--text-secondary)'}}>
              <h2>Your list is empty</h2>
              <p>Import a WhatsApp chat zip to automatically extract music links.</p>
            </div>
          )}
        </div>
      )}
    </div>
  );
}
