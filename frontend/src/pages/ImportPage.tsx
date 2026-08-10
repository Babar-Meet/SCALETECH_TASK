import { useState } from 'react';
import axios from 'axios';
import { UploadCloud, CheckCircle } from 'lucide-react';

export default function ImportPage() {
  const [file, setFile] = useState<File | null>(null);
  const [uploading, setUploading] = useState(false);
  const [results, setResults] = useState<Record<string, number> | null>(null);
  const [error, setError] = useState<string | null>(null);

  const handleFileChange = (e: React.ChangeEvent<HTMLInputElement>) => {
    if (e.target.files && e.target.files[0]) {
      setFile(e.target.files[0]);
    }
  };

  const handleUpload = async () => {
    if (!file) return;
    setUploading(true);
    setError(null);
    setResults(null);

    const formData = new FormData();
    formData.append('file', file);

    try {
      const res = await axios.post('http://localhost:8000/api/upload', formData);
      if (res.data && res.data.error) {
        setError(res.data.error);
      } else {
        setResults(res.data);
      }
    } catch (err) {
      if (axios.isAxiosError(err)) {
        setError(err.response?.data?.error || 'Upload failed');
      } else {
        setError('Upload failed');
      }
    } finally {
      setUploading(false);
    }
  };

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
          {uploading ? 'Processing...' : 'Process Import'}
        </button>
      </div>

      {error && (
        <div style={{marginTop: '32px', padding: '16px', background: 'var(--bg-surface-elevated)', color: 'var(--netflix-red)', borderRadius: '4px', textAlign: 'center'}}>
          <strong>{error}</strong>
        </div>
      )}

      {results && (
        <div style={{marginTop: '32px', padding: '24px', background: 'var(--bg-surface-elevated)', borderRadius: '6px', maxWidth: '400px', margin: '32px auto 0'}}>
          <h3 style={{display: 'flex', alignItems: 'center', gap: '8px', color: 'var(--match-score)', marginBottom: '16px', justifyContent: 'center'}}>
            <CheckCircle size={24} /> Import Complete
          </h3>
          <div style={{display: 'flex', flexDirection: 'column', gap: '12px'}}>
            <div style={{display: 'flex', justifyContent: 'space-between'}}>
              <span style={{color: 'var(--text-secondary)'}}>Messages Found</span>
              <strong style={{fontSize: '18px'}}>{results.messages_found}</strong>
            </div>
            <div style={{display: 'flex', justifyContent: 'space-between'}}>
              <span style={{color: 'var(--text-secondary)'}}>Total URLs Saved</span>
              <strong style={{fontSize: '18px'}}>{results.urls_saved}</strong>
            </div>
            <div style={{display: 'flex', justifyContent: 'space-between'}}>
              <span style={{color: 'var(--text-secondary)'}}>Unique YouTube Videos</span>
              <strong style={{fontSize: '18px', color: 'var(--text-primary)'}}>{results.unique_youtube_videos}</strong>
            </div>
            <div style={{display: 'flex', justifyContent: 'space-between'}}>
              <span style={{color: 'var(--text-secondary)'}}>Senders Found</span>
              <strong style={{fontSize: '18px'}}>{results.senders_found}</strong>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
