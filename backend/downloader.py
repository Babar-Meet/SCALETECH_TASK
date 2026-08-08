import os
import re
import time
import asyncio
import json
from typing import Optional
import yt_dlp
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

def download_audio(url: str, output_dir: str):
    """
    Uses yt-dlp to download audio from a YouTube URL.
    Returns (audio_path, title, channel, duration) on success.
    Raises Exception on failure.
    """
    ydl_opts = {
        'format': 'bestaudio/best',
        'outtmpl': os.path.join(output_dir, '%(title)s_%(id)s.%(ext)s'),
        'postprocessors': [{
            'key': 'FFmpegExtractAudio',
            'preferredcodec': 'mp3',
            'preferredquality': '192',
        }],
        'quiet': True,
        'no_warnings': True,
        'logger': SQLiteLogger(),
    }

    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
        info_dict = ydl.extract_info(url, download=True)
        if not info_dict:
            raise Exception("Failed to extract video info")
            
        title = info_dict.get('title', 'Unknown Title')
        channel = info_dict.get('uploader', 'Unknown Channel')
        duration = info_dict.get('duration', 0)
        video_id = info_dict.get('id', '')
        
        # Calculate the final filename created by postprocessor
        safe_name = sanitize_filename(title, video_id)
        # yt-dlp will save as mp3 because of postprocessor
        # The outtmpl uses the raw title, which yt-dlp handles escaping for.
        # Let's actually force our safe filename format in yt-dlp outtmpl to be perfectly sure.
        pass
        
def download_audio_safe(url: str, video_id: str, output_dir: str, link_id: int = None, loop=None, manager=None):
    # It's better to extract info first, then download with our explicit filename
    ydl_opts_info = {
        'quiet': True,
        'no_warnings': True,
        'extract_flat': True,
    }
    with yt_dlp.YoutubeDL(ydl_opts_info) as ydl:
        info = ydl.extract_info(url, download=False)
        title = info.get('title', 'Unknown Title')
        channel = info.get('uploader', 'Unknown Channel')
        duration = info.get('duration', 0)
        
    safe_name = sanitize_filename(title, video_id)
    outtmpl = os.path.join(output_dir, f"{safe_name}.%(ext)s")
    
    def my_hook(d):
        if d['status'] == 'downloading':
            percent = d.get('_percent_str', '').strip()
            if percent and link_id and loop and manager:
                try:
                    # _percent_str often looks like " 45.6%" or " \x1b[0;94m 45.6%\x1b[0m"
                    clean_percent = re.sub(r'\x1b[^m]*m', '', percent)
                    msg = json.dumps({"type": "progress", "id": link_id, "percent": clean_percent})
                    asyncio.run_coroutine_threadsafe(manager.broadcast(msg), loop)
                except Exception:
                    pass

    ydl_opts = {
        'format': 'bestaudio/best',
        'outtmpl': outtmpl,
        'postprocessors': [{
            'key': 'FFmpegExtractAudio',
            'preferredcodec': 'mp3',
            'preferredquality': '192',
        }],
        'quiet': True,
        'no_warnings': True,
        'progress_hooks': [my_hook],
    }
    
    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
        ydl.download([url])
        
    final_path = os.path.join(output_dir, f"{safe_name}.mp3")
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
