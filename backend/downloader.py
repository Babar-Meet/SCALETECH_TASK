import os
import re
import time
import asyncio
import json
import subprocess
from typing import Optional
from sqlalchemy.orm import Session
from database import SessionLocal, Link
from config import config

# Global flag to control the background loop
download_loop_running = False

def sanitize_filename(title: str, video_id: str, max_length: int = 150) -> str:
    """Creates a safe filename by removing invalid characters and truncating."""
    # Remove invalid windows filename characters
    safe_title = re.sub(r'[<>:"/\\|?*]', '_', title)
    # Truncate
    if len(safe_title) > max_length:
        safe_title = safe_title[:max_length].strip()
    return f"{safe_title}_{video_id}"

class SQLiteLogger:
    """Captures yt-dlp logs if needed."""
    def debug(self, msg): pass
    def warning(self, msg): pass
    def error(self, msg): pass

YTDLP_EXE = os.path.join(os.path.dirname(__file__), "extenctions", "yt-dlp.exe")

def get_info_with_retry(url, retry_on_403=True):
    base_args = [
        YTDLP_EXE,
        '--dump-json',
        '--no-warnings',
        '--no-playlist',
        '--extractor-args', 'youtube:player_client=web,web_safari,web_embedded,web_music,web_creator,mweb,ios,android,android_vr,tv,tv_simply,tv_embedded'
    ]
    
    headers_args = [
        '--user-agent', 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/121.0 Safari/537.36',
        '--add-header', 'Referer:https://www.youtube.com/',
        '--add-header', 'Origin:https://www.youtube.com'
    ]
    
    cmd = base_args + headers_args + [url]
    
    # Hide window on Windows
    creationflags = subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0

    try:
        result = subprocess.run(cmd, capture_output=True, text=True, check=True, encoding='utf-8', errors='replace', creationflags=creationflags)
        return json.loads(result.stdout)
    except subprocess.CalledProcessError as e:
        if retry_on_403 and '403' in e.stderr:
            retry_args = base_args + [
                '--user-agent', 'Mozilla/5.0 (Linux; Android 10; Mobile; rv:109.0) Gecko/20100101 Firefox/119.0',
                '--add-header', 'Referer:https://www.youtube.com/',
                '--add-header', 'Origin:https://www.youtube.com',
                '--force-ipv4',
                url
            ]
            res2 = subprocess.run(retry_args, capture_output=True, text=True, check=True, encoding='utf-8', errors='replace', creationflags=creationflags)
            return json.loads(res2.stdout)
        raise Exception(f"Failed to fetch metadata: {e.stderr}")

def download_audio(url: str, output_dir: str):
    return download_audio_safe(url, "unknown_id", output_dir)

def download_audio_safe(url: str, video_id: str, output_dir: str, link_id: int = None, loop=None, manager=None):
    info = get_info_with_retry(url)
    title = info.get('title', 'Unknown Title')
    channel = info.get('uploader', 'Unknown Channel')
    duration = info.get('duration', 0)
    actual_video_id = info.get('id', video_id)
    
    safe_name = sanitize_filename(title, actual_video_id)
    outtmpl = os.path.join(output_dir, f"{safe_name}.%(ext)s")
    final_path = os.path.join(output_dir, f"{safe_name}.mp3")
    
    # If the file already exists locally, skip downloading and return immediately
    if os.path.exists(final_path):
        return final_path, title, channel, duration
        
    base_args = [
        YTDLP_EXE,
        '-f', 'bestaudio/best',
        '-x', '--audio-format', 'mp3', '--audio-quality', '192K',
        '-o', outtmpl,
        '--no-playlist',
        '--extractor-args', 'youtube:player_client=web,web_safari,web_embedded,web_music,web_creator,mweb,ios,android,android_vr,tv,tv_simply,tv_embedded',
        '--retries', '10',
        '--fragment-retries', '10',
        '--socket-timeout', '30',
        '--newline'
    ]
    
    headers_args = [
        '--user-agent', 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/121.0 Safari/537.36',
        '--add-header', 'Referer:https://www.youtube.com/',
        '--add-header', 'Origin:https://www.youtube.com'
    ]
    
    creationflags = subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0
    
    def run_cmd(args):
        process = subprocess.Popen(args, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, encoding='utf-8', errors='replace', bufsize=1, creationflags=creationflags)
        error_log = []
        for line in process.stdout:
            error_log.append(line)
            if '[download]' in line and '%' in line:
                match = re.search(r'([\d\.]+)%', line)
                if match and link_id and loop and manager:
                    clean_percent = match.group(1) + "%"
                    msg = json.dumps({"type": "progress", "id": link_id, "percent": clean_percent})
                    try:
                        asyncio.run_coroutine_threadsafe(manager.broadcast(msg), loop)
                    except Exception:
                        pass
            elif '[ExtractAudio]' in line:
                if link_id and loop and manager:
                    msg = json.dumps({"type": "progress", "id": link_id, "percent": "EXTRACTING AUDIO"})
                    try:
                        asyncio.run_coroutine_threadsafe(manager.broadcast(msg), loop)
                    except Exception:
                        pass
            elif '[Merger]' in line or '[Metadata]' in line or '[Thumbnails]' in line:
                if link_id and loop and manager:
                    msg = json.dumps({"type": "progress", "id": link_id, "percent": "POST-PROCESSING"})
                    try:
                        asyncio.run_coroutine_threadsafe(manager.broadcast(msg), loop)
                    except Exception:
                        pass
        process.wait()
        return process.returncode, "".join(error_log)

    code, output = run_cmd(base_args + headers_args + [url])
    
    if code != 0:
        if '403' in output:
            retry_headers = [
                '--user-agent', 'Mozilla/5.0 (Linux; Android 10; Mobile; rv:109.0) Gecko/20100101 Firefox/119.0',
                '--add-header', 'Referer:https://www.youtube.com/',
                '--add-header', 'Origin:https://www.youtube.com',
                '--force-ipv4'
            ]
            code, output = run_cmd(base_args + retry_headers + [url])
            if code != 0:
                raise Exception(f"yt-dlp error after 403 retry: {output}")
        else:
            raise Exception(f"yt-dlp error: {output}")

    if not os.path.exists(final_path):
        raise Exception("Output file not found after download")
        
    return final_path, title, channel, duration

async def download_loop():
    global download_loop_running
    download_loop_running = True
    
    while download_loop_running:
        db: Session = SessionLocal()
        try:
            # Find the next PENDING item
            link = db.query(Link).filter(Link.status == "PENDING").first()
            if not link:
                await asyncio.sleep(2)
                continue
                
            # Mark as DOWNLOADING
            link.status = "DOWNLOADING"
            db.commit()
            from websocket_manager import manager
            await manager.broadcast("update")
            
            try:
                # Execute download
                target_dir = config.AUDIO_DIRECTORY
                if link.category:
                    # Sanitize category name for folder
                    safe_cat = re.sub(r'[<>:"/\\|?*]', '_', link.category).strip()
                    target_dir = os.path.join(config.AUDIO_DIRECTORY, safe_cat)
                    
                if not os.path.exists(target_dir):
                    os.makedirs(target_dir, exist_ok=True)
                    
                from websocket_manager import manager
                loop = asyncio.get_running_loop()
                path, title, channel, duration = await asyncio.to_thread(
                    download_audio_safe,
                    link.normalized_url, 
                    link.youtube_video_id, 
                    target_dir,
                    link.id,
                    loop,
                    manager
                )
                
                link.audio_path = path
                link.audio_title = title
                link.channel_name = channel
                link.duration = duration
                link.status = "COMPLETED"
                link.error = None
                
            except Exception as e:
                link.status = "FAILED"
                link.error = str(e)
                
            db.commit()
            
            # Auto-update Excel file
            try:
                from excel_service import export_links_to_excel
                all_links = db.query(Link).all()
                export_links_to_excel(all_links)
            except Exception as ex:
                print(f"Auto-export to Excel failed: {ex}")
                
            from websocket_manager import manager
            await manager.broadcast("update")
        except Exception as e:
            print(f"Error in download loop: {e}")
            await asyncio.sleep(2)
        finally:
            db.close()
            
def start_download_loop():
    global download_loop_running
    if not download_loop_running:
        asyncio.create_task(download_loop())

def stop_download_loop():
    global download_loop_running
    download_loop_running = False
