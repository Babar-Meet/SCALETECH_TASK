import os
import re
import time
import asyncio
import json
import logging
import subprocess
from typing import Optional
from sqlalchemy.orm import Session
from sqlalchemy.exc import SQLAlchemyError
from database import SessionLocal, Link
from config import config
from db_utils import safe_commit
from loop_state import loop_state

logger = logging.getLogger("downloader")

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
    logger.info(f"[Link {link_id or '?'}] Fetching metadata for {video_id}...")
    info = get_info_with_retry(url)
    title = info.get('title', 'Unknown Title')
    channel = info.get('uploader', 'Unknown Channel')
    duration = info.get('duration', 0)
    actual_video_id = info.get('id', video_id)
    logger.info(f"[Link {link_id or '?'}] Metadata: \"{title}\" by {channel} ({duration}s)")
    
    safe_name = sanitize_filename(title, actual_video_id)
    outtmpl = os.path.join(output_dir, f"{safe_name}.%(ext)s")
    final_path = os.path.join(output_dir, f"{safe_name}.mp3")
    
    # If the file already exists locally, skip downloading and return immediately
    if os.path.exists(final_path):
        logger.info(f"[Link {link_id or '?'}] File already exists, skipping download: {os.path.basename(final_path)}")
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
    
    logger.info(f"[Link {link_id or '?'}] ✅ Download complete: {os.path.basename(final_path)}")
    return final_path, title, channel, duration

def claim_next_download(db: Session) -> Optional[int]:
    """Atomically claim the next PENDING link (serial worker). Uses a conditional
    UPDATE so a row deleted by the UI mid-flight simply returns None instead of
    raising StaleDataError."""
    try:
        candidate = db.query(Link.id).filter(Link.status == "PENDING").order_by(Link.id.asc()).limit(1).scalar()
        if candidate is None:
            return None
        result = db.query(Link).filter(Link.id == candidate, Link.status == "PENDING").update(
            {"status": "DOWNLOADING"}, synchronize_session=False)
        if result != 1:
            db.rollback()
            return None
        if not safe_commit(db):
            return None
        return candidate
    except SQLAlchemyError as e:
        logger.warning(f"Claim failed: {e}")
        try:
            db.rollback()
        except Exception:
            pass
        return None


async def download_loop():
    global download_loop_running

    while download_loop_running:
        db: Session = SessionLocal()
        try:
            link_id = claim_next_download(db)
            if link_id is None:
                # Idle: broadcast a heartbeat periodically so connected UIs
                # always have a live signal (no unexplained stalls).
                from websocket_manager import manager
                if loop_state.heartbeat_due():
                    db2: Session = SessionLocal()
                    try:
                        pending = db2.query(Link).filter(Link.status == "PENDING").count()
                        active = db2.query(Link).filter(Link.status == "DOWNLOADING").count()
                    finally:
                        db2.close()
                    await manager.broadcast(json.dumps({
                        "type": "status",
                        "is_running": True,
                        "phase": loop_state.phase(True),
                        "pending": pending,
                        "active": active,
                        "idle_seconds": round(loop_state.activity_age(), 1),
                    }))
                await asyncio.sleep(2)
                continue

            loop_state.touch()

            # Read the minimal fields needed BEFORE the long await; never touch
            # this object afterwards (it goes stale the moment we await).
            info = db.get(Link, link_id)
            if info is None:
                continue
            logger.info(f"⬇️ Starting download for Link #{link_id}: {info.normalized_url}")

            # Instant feedback: the user sees activity the moment work is claimed,
            # even while yt-dlp is still fetching metadata (no silent stall).
            from websocket_manager import manager
            loop = asyncio.get_running_loop()
            await manager.broadcast(json.dumps({"type": "progress", "id": link_id, "percent": "STARTING DOWNLOAD..."}))

            target_dir = config.AUDIO_DIRECTORY
            if info.category:
                safe_cat = re.sub(r'[<>:"/\\|?*]', '_', info.category).strip()
                target_dir = os.path.join(config.AUDIO_DIRECTORY, safe_cat)
            if not os.path.exists(target_dir):
                os.makedirs(target_dir, exist_ok=True)

            url = info.normalized_url
            video_id = info.youtube_video_id

            await manager.broadcast("update")

            try:
                path, title, channel, duration = await asyncio.to_thread(
                    download_audio_safe,
                    url,
                    video_id,
                    target_dir,
                    link_id,
                    loop,
                    manager
                )
            except Exception as e:
                # Re-open a FRESH session to write the failure — the row may have
                # been deleted while the download was in flight.
                db2: Session = SessionLocal()
                try:
                    row = db2.get(Link, link_id)
                    if row is None:
                        logger.info(f"Link #{link_id} deleted while downloading; discarding failure.")
                        continue
                    row.status = "FAILED"
                    row.error = str(e)
                    safe_commit(db2)
                    logger.error(f"❌ Link #{link_id} download FAILED: {e}")
                finally:
                    db2.close()
                await manager.broadcast(json.dumps({"type": "progress", "id": link_id, "percent": "FAILED"}))
            else:
                db2: Session = SessionLocal()
                try:
                    row = db2.get(Link, link_id)
                    if row is None:
                        logger.info(f"Link #{link_id} deleted while downloading; removing orphaned file.")
                        try:
                            os.remove(path)
                        except Exception:
                            pass
                        continue
                    row.audio_path = path
                    row.audio_title = title
                    row.channel_name = channel
                    row.duration = duration
                    row.status = "COMPLETED"
                    row.error = None
                    safe_commit(db2)
                    logger.info(f"✅ Link #{link_id} downloaded successfully: {os.path.basename(path)}")
                finally:
                    db2.close()
                await manager.broadcast(json.dumps({"type": "progress", "id": link_id, "percent": "QUEUED FOR TRANSCRIBE..."}))

            # Auto-update Excel file (best-effort side effect)
            try:
                from excel_service import export_links_to_excel
                db3: Session = SessionLocal()
                try:
                    all_links = db3.query(Link).all()
                    export_links_to_excel(all_links)
                finally:
                    db3.close()
            except Exception as ex:
                logger.error(f"Auto-export to Excel failed: {ex}")

            from websocket_manager import manager
            await manager.broadcast("update")
        except Exception as e:
            logger.error(f"Error in download loop: {e}")
            await asyncio.sleep(2)
        finally:
            db.close()
            
def start_download_loop():
    global download_loop_running
    if not download_loop_running:
        download_loop_running = True
        asyncio.create_task(download_loop())

def stop_download_loop():
    global download_loop_running
    download_loop_running = False
