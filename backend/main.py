import os
import re
import shutil
import asyncio
import logging
import uuid
from fastapi import FastAPI, UploadFile, File, Depends, BackgroundTasks
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from sqlalchemy.orm import Session
from sqlalchemy.exc import SQLAlchemyError
import zipfile
from pydantic import BaseModel
from typing import List, Optional
from fastapi import WebSocket, WebSocketDisconnect
from websocket_manager import manager

from config import config
from database import SessionLocal, Link
from excel_service import export_links_to_excel
from downloader import start_download_loop, stop_download_loop, download_loop_running
from log_streamer import install_global_logging, ws_log_handler, log_buffer
from importer import create_import, get_import, cancel_import, run_import_task
from db_utils import safe_commit

logger = logging.getLogger("main")

app = FastAPI(title="WhatsApp to YouTube Music Extractor MVP")

@app.on_event("startup")
async def startup_event():
    install_global_logging()
    ws_log_handler.set_loop(asyncio.get_running_loop())
    logger.info("🚀 Backend server started successfully.")
    logger.info(f"Audio directory: {os.path.abspath(config.AUDIO_DIRECTORY)}")
    logger.info(f"Whisper model: {config.WHISPER_MODEL} (compute: {config.WHISPER_COMPUTE_TYPE})")
    logger.info(f"Gemini API key: {'configured ✓' if config.GEMINI_API_KEY else '✗ NOT SET'}")
    logger.info(f"Ollama model: {config.OLLAMA_MODEL or 'not configured'}")
    recover_interrupted_states()
    try:
        from ai_processor import probe_whisper
        await asyncio.to_thread(probe_whisper)
    except Exception as e:
        logger.error(f"Whisper probe error: {e}")
    logger.info("⏸️ Processing loops on standby — user starts downloads via the 'Start Downloading' button.")

@app.on_event("shutdown")
async def shutdown_event():
    stop_download_loop()
    try:
        from ai_processor import stop_ai_processor_loop
        stop_ai_processor_loop()
    except Exception:
        pass

def recover_interrupted_states():
    """
    After a crash or manual kill, links can be stuck in mid-flight states
    (DOWNLOADING / TRANSCRIBING / CLASSIFYING). Reset those back to PENDING
    so the auto-started loops resume them instead of skipping forever.
    """
    db = SessionLocal()
    try:
        recovered = {"download": 0, "transcription": 0, "classification": 0}
        for link in db.query(Link).filter(Link.status == "DOWNLOADING").all():
            link.status = "PENDING"
            link.error = "Interrupted - queued for resume"
            recovered["download"] += 1
        for link in db.query(Link).filter(Link.transcription_status == "TRANSCRIBING").all():
            link.transcription_status = "PENDING"
            recovered["transcription"] += 1
            transcript_path = os.path.join(config.TRANSCRIPTS_DIRECTORY, f"{link.id}_transcript.txt")
            if os.path.exists(transcript_path):
                try:
                    os.remove(transcript_path)
                except Exception:
                    pass
        for link in db.query(Link).filter(Link.classification_status == "CLASSIFYING").all():
            link.classification_status = "PENDING"
            recovered["classification"] += 1
        db.commit()
        if any(recovered.values()):
            logger.info(f"♻️ Recovered interrupted states: {recovered}")
    finally:
        db.close()

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()

# Pydantic models for responses
class StatsResponse(BaseModel):
    total_links: int
    pending: int
    downloading: int
    transcribing: int
    categorizing: int
    completed: int
    failed: int

class LinkResponse(BaseModel):
    id: int
    sender: str
    original_message: str
    extracted_url: str
    status: str
    youtube_video_id: Optional[str] = None
    audio_title: Optional[str] = None
    channel_name: Optional[str] = None
    duration: Optional[int] = None
    error: Optional[str] = None
    category: Optional[str] = None
    transcription_status: Optional[str] = None
    classification_status: Optional[str] = None
    ai_language: Optional[str] = None
    ai_category: Optional[str] = None
    ai_confidence: Optional[str] = None
    audio_path: Optional[str] = None

class CategoryUpdateRequest(BaseModel):
    category: str

@app.put("/api/links/{link_id}/category")
def update_category(link_id: int, request: CategoryUpdateRequest, db: Session = Depends(get_db)):
    link = db.query(Link).filter(Link.id == link_id).first()
    if not link:
        return {"error": "Link not found"}
        
    old_cat = link.category
    stripped = request.category.strip() if request.category else ""
    link.category = stripped or None
    
    # If the file is already downloaded, move it to the final Music/{Language}/{Category} layout
    if link.audio_path and os.path.exists(link.audio_path):
        from organizer import resolve_final_path
        new_dir = resolve_final_path(link)
        os.makedirs(new_dir, exist_ok=True)
        
        filename = os.path.basename(link.audio_path)
        new_path = os.path.join(new_dir, filename)
        
        if new_path != link.audio_path:
            shutil.move(link.audio_path, new_path)
            link.audio_path = new_path
            link.final_path = new_path
            
    safe_commit(db)
    
    # Auto-update Excel file
    try:
        from excel_service import export_links_to_excel
        all_links = db.query(Link).all()
        export_links_to_excel(all_links)
    except Exception as ex:
        print(f"Auto-export to Excel failed: {ex}")
        
    return {"message": "Category updated", "audio_path": link.audio_path}

@app.post("/api/upload")
async def upload_whatsapp_zip(file: UploadFile = File(...)):
    logger.info(f"📦 Upload received: {file.filename}")
    if not os.path.exists(config.IMPORTS_DIRECTORY):
        os.makedirs(config.IMPORTS_DIRECTORY, exist_ok=True)
        
    # Filenames from WhatsApp exports often contain emoji/non-ASCII chars which
    # break on Windows. Store under a sanitized name so uploads never 500.
    raw_name = file.filename or "upload.zip"
    safe_base = re.sub(r'[<>:"/\\|?*\x00-\x1f]', '_', os.path.basename(raw_name)).strip() or "upload"
    safe_base = re.sub(r'_+$', '', safe_base)
    if not safe_base.lower().endswith(".zip"):
        safe_base += ".zip"
    unique_id = str(uuid.uuid4())
    zip_path = os.path.join(config.IMPORTS_DIRECTORY, f"{unique_id[:8]}_{safe_base}")
    with open(zip_path, "wb") as buffer:
        shutil.copyfileobj(file.file, buffer)
    logger.info(f"ZIP saved to: {zip_path}")
    
    # Fast validation: peek inside the archive for a .txt entry (no extraction)
    try:
        with zipfile.ZipFile(zip_path, 'r') as zip_ref:
            has_txt = any(name.endswith('.txt') for name in zip_ref.namelist())
    except zipfile.BadZipFile as e:
        logger.error(f"❌ Uploaded file is not a valid ZIP: {e}")
        try:
            os.remove(zip_path)
        except Exception:
            pass
        return JSONResponse(status_code=400, content={"error": "Uploaded file is not a valid ZIP archive. Export the chat as .zip from WhatsApp first."})
    except Exception as e:
        logger.error(f"❌ Failed to read ZIP: {e}")
        try:
            os.remove(zip_path)
        except Exception:
            pass
        return JSONResponse(status_code=400, content={"error": f"Failed to read ZIP: {e}"})
        
    if not has_txt:
        logger.error("❌ No .txt file found inside the ZIP archive.")
        try:
            os.remove(zip_path)
        except Exception:
            pass
        return JSONResponse(status_code=400, content={"error": "No .txt file found in ZIP."})
    
    temp_dir = os.path.join(os.path.dirname(os.path.dirname(__file__)), "temp")
    extract_dir = os.path.join(temp_dir, f"extracted_{unique_id}")
    
    import_id = create_import(safe_base, zip_path, extract_dir)
    asyncio.create_task(run_import_task(import_id))
    logger.info(f"✅ Import {import_id} queued for background processing.")
    
    return {"import_id": import_id, "filename": safe_base, "phase": "queued"}

@app.get("/api/imports/{import_id}")
def get_import_status(import_id: str):
    progress = get_import(import_id)
    if not progress:
        return JSONResponse(status_code=404, content={"error": "Import not found"})
    return progress

@app.delete("/api/imports/{import_id}")
def cancel_import_endpoint(import_id: str):
    if not cancel_import(import_id):
        if not get_import(import_id):
            return JSONResponse(status_code=404, content={"error": "Import not found"})
        return JSONResponse(status_code=409, content={"error": "Import already finished"})
    return {"message": "Import canceled"}

def verify_link_states(links: List[Link], db: Session):
    changed = False
    for link in links:
        if link.status == "COMPLETED" and link.audio_path:
            if not os.path.exists(link.audio_path):
                # File vanished: re-queue the whole pipeline (download → transcribe → classify)
                # so a fresh file is re-transcribed and re-classified, never silently
                # classified from an empty transcript.
                link.status = "PENDING"
                link.audio_path = None
                link.final_path = None
                link.transcription_status = "PENDING"
                link.classification_status = "PENDING"
                link.ai_language = None
                link.ai_category = None
                link.ai_confidence = None
                link.error = "Audio file missing - queued for re-download"
                transcript_path = os.path.join(config.TRANSCRIPTS_DIRECTORY, f"{link.id}_transcript.txt")
                if os.path.exists(transcript_path):
                    try:
                        os.remove(transcript_path)
                    except Exception:
                        pass
                changed = True
    if changed:
        safe_commit(db)

@app.delete("/api/links/clean/non-youtube")
def clean_non_youtube(db: Session = Depends(get_db)):
    links = db.query(Link).filter(Link.status == "SKIPPED_NON_YOUTUBE").all()
    count = len(links)
    for link in links:
        db.delete(link)
    safe_commit(db)
    if count > 0:
        try:
            from excel_service import export_links_to_excel
            all_links = db.query(Link).all()
            export_links_to_excel(all_links)
        except Exception:
            pass
    return {"message": f"Deleted {count} non-YouTube links"}

@app.delete("/api/links/{link_id}")
def delete_link(link_id: int, db: Session = Depends(get_db)):
    link = db.query(Link).filter(Link.id == link_id).first()
    if not link:
        return {"error": "Link not found"}
    if link.audio_path and os.path.exists(link.audio_path):
        try:
            os.remove(link.audio_path)
        except Exception as e:
            print(f"Failed to delete local file: {e}")
    transcript_path = os.path.join(config.TRANSCRIPTS_DIRECTORY, f"{link.id}_transcript.txt")
    if os.path.exists(transcript_path):
        try:
            os.remove(transcript_path)
        except Exception:
            pass
    db.delete(link)
    safe_commit(db)
    try:
        from excel_service import export_links_to_excel
        all_links = db.query(Link).all()
        export_links_to_excel(all_links)
    except Exception:
        pass
    return {"message": "Link deleted successfully"}

@app.get("/api/links", response_model=List[LinkResponse])
def get_links(db: Session = Depends(get_db)):
    links = db.query(Link).order_by(Link.id.asc()).all()
    verify_link_states(links, db)
    return links

@app.get("/api/downloads", response_model=List[LinkResponse])
def get_downloads(db: Session = Depends(get_db)):
    # Return links that are youtube videos and not duplicates
    links = db.query(Link).filter(
        Link.status.in_(["PENDING", "DOWNLOADING", "COMPLETED", "FAILED"])
    ).order_by(Link.id.asc()).all()
    verify_link_states(links, db)
    return links

@app.delete("/api/links")
def clear_all_links(db: Session = Depends(get_db)):
    db.query(Link).delete()
    safe_commit(db)
    # Optionally clear the imported directories, but DB clear is minimum.
    return {"message": "All links cleared"}


@app.websocket("/api/ws")
async def websocket_endpoint(websocket: WebSocket):
    await manager.connect(websocket)
    try:
        while True:
            await websocket.receive_text()
    except WebSocketDisconnect:
        manager.disconnect(websocket)

@app.post("/api/downloads/start")
async def start_downloads():
    logger.info("▶️ Starting download + AI processor loops...")
    recover_interrupted_states()
    start_download_loop()
    try:
        from ai_processor import start_ai_processor_loop
        start_ai_processor_loop()
        logger.info("AI processor loop started.")
    except Exception as e:
        logger.error(f"Error starting AI loop: {e}")
    await manager.broadcast("update")
    return {"message": "Download loop started"}

@app.post("/api/downloads/pause")
async def pause_downloads():
    logger.info("⏸️ Pausing download + AI processor loops...")
    stop_download_loop()
    try:
        from ai_processor import stop_ai_processor_loop
        stop_ai_processor_loop()
        logger.info("AI processor loop stopped.")
    except Exception as e:
        logger.error(f"Error stopping AI loop: {e}")
    await manager.broadcast("update")
    return {"message": "Download loop paused"}

@app.get("/api/downloads/status")
def get_download_status():
    from downloader import download_loop_running
    from ai_processor import processor_loop_running
    from loop_state import loop_state
    is_running = download_loop_running or processor_loop_running
    db = SessionLocal()
    try:
        pending = db.query(Link).filter(Link.status == "PENDING").count()
        active = (
            db.query(Link).filter(Link.status == "DOWNLOADING").count()
            + db.query(Link).filter(Link.status == "COMPLETED", Link.transcription_status == "TRANSCRIBING").count()
            + db.query(Link).filter(Link.status == "COMPLETED", Link.transcription_status == "COMPLETED",
                                   Link.classification_status == "CLASSIFYING").count()
        )
    finally:
        db.close()
    return {
        "is_running": is_running,
        "phase": loop_state.phase(is_running),
        "pending": pending,
        "active": active,
        "idle_seconds": round(loop_state.activity_age(), 1),
    }

@app.post("/api/downloads/retry/{link_id}")
def retry_download(link_id: int, db: Session = Depends(get_db)):
    link = db.query(Link).filter(Link.id == link_id).first()
    if not link:
        return {"error": "Link not found"}

    needs_download_retry = link.status == "FAILED"
    needs_ai_retry = link.transcription_status == "FAILED" or link.classification_status == "FAILED"
    if not (needs_download_retry or needs_ai_retry):
        return {"error": "Link has no failed stage to retry"}

    if link.audio_path:
        # Archive the stale transcript so a re-run starts clean
        transcript_path = os.path.join(config.TRANSCRIPTS_DIRECTORY, f"{link.id}_transcript.txt")
        if os.path.exists(transcript_path):
            try:
                os.remove(transcript_path)
            except Exception:
                pass

    link.error = None
    link.status = "PENDING" if needs_download_retry else link.status
    link.transcription_status = "PENDING" if link.transcription_status == "FAILED" else link.transcription_status
    link.classification_status = "PENDING" if link.classification_status == "FAILED" else link.classification_status
    safe_commit(db)
    return {"message": "Queued for retry"}

@app.get("/api/stats", response_model=StatsResponse)
def get_stats(db: Session = Depends(get_db)):
    # "Completed" means the FULL pipeline is done: downloaded + transcribed + AI categorized
    return StatsResponse(
        total_links=db.query(Link).count(),
        pending=db.query(Link).filter(Link.status == "PENDING").count(),
        downloading=db.query(Link).filter(Link.status == "DOWNLOADING").count(),
        transcribing=db.query(Link).filter(
            Link.status == "COMPLETED", Link.transcription_status == "TRANSCRIBING").count(),
        categorizing=db.query(Link).filter(
            Link.status == "COMPLETED", Link.transcription_status == "COMPLETED",
            Link.classification_status == "CLASSIFYING").count(),
        completed=db.query(Link).filter(
            Link.status == "COMPLETED", Link.transcription_status == "COMPLETED",
            Link.classification_status == "COMPLETED").count(),
        failed=db.query(Link).filter(Link.status == "FAILED").count(),
    )

@app.post("/api/export")
def export_links(db: Session = Depends(get_db)):
    logger.info("📊 Manual Excel export triggered.")
    links = db.query(Link).all()
    filepath = export_links_to_excel(links)
    logger.info(f"Excel exported to: {filepath}")
    return {"message": "Export successful", "filepath": filepath}


# ---------- Log WebSocket endpoint ----------
@app.websocket("/api/ws/logs")
async def log_websocket_endpoint(websocket: WebSocket):
    """Frontend connects here to receive real-time log stream."""
    await ws_log_handler.connect(websocket)
    logger.info("📡 Log viewer connected.")
    try:
        while True:
            await websocket.receive_text()  # keep alive
    except WebSocketDisconnect:
        ws_log_handler.disconnect(websocket)
        logger.info("📡 Log viewer disconnected.")

@app.get("/api/logs")
def get_logs():
    """Returns buffered logs for initial page load."""
    return log_buffer.get_all()

@app.delete("/api/logs")
def clear_logs():
    """Clears the in-memory log buffer."""
    log_buffer.clear()
    logger.info("🗑️ Log buffer cleared.")
    return {"message": "Logs cleared"}
