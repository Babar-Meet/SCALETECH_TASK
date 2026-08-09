import os
import shutil
from fastapi import FastAPI, UploadFile, File, Depends, BackgroundTasks
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy.orm import Session
import zipfile
from pydantic import BaseModel
from typing import List, Optional
from fastapi import WebSocket, WebSocketDisconnect
from websocket_manager import manager

from config import config
from database import SessionLocal, Link
from whatsapp_parser import process_chat_file
from excel_service import export_links_to_excel
from downloader import start_download_loop, stop_download_loop, download_loop_running

app = FastAPI(title="WhatsApp to YouTube Music Extractor MVP")

@app.on_event("startup")
async def startup_event():
    pass # Wait for user to manually trigger downloads from the UI

@app.on_event("shutdown")
async def shutdown_event():
    stop_download_loop()

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
    pending_download: int
    downloaded: int
    failed_download: int

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

class CategoryUpdateRequest(BaseModel):
    category: str

@app.put("/api/links/{link_id}/category")
def update_category(link_id: int, request: CategoryUpdateRequest, db: Session = Depends(get_db)):
    link = db.query(Link).filter(Link.id == link_id).first()
    if not link:
        return {"error": "Link not found"}
        
    old_cat = link.category
    link.category = request.category
    
    # If the file is already downloaded, move it to the new folder
    if link.audio_path and os.path.exists(link.audio_path):
        import re
        safe_cat = re.sub(r'[<>:"/\\|?*]', '_', request.category).strip() if request.category else ""
        new_dir = os.path.join(config.AUDIO_DIRECTORY, safe_cat) if safe_cat else config.AUDIO_DIRECTORY
        os.makedirs(new_dir, exist_ok=True)
        
        filename = os.path.basename(link.audio_path)
        new_path = os.path.join(new_dir, filename)
        
        if new_path != link.audio_path:
            shutil.move(link.audio_path, new_path)
            link.audio_path = new_path
            
    db.commit()
    
    # Auto-update Excel file
    try:
        from excel_service import export_links_to_excel
        all_links = db.query(Link).all()
        export_links_to_excel(all_links)
    except Exception as ex:
        print(f"Auto-export to Excel failed: {ex}")
        
    return {"message": "Category updated", "audio_path": link.audio_path}

@app.post("/api/upload")
async def upload_whatsapp_zip(file: UploadFile = File(...), db: Session = Depends(get_db)):
    if not os.path.exists(config.IMPORTS_DIRECTORY):
        os.makedirs(config.IMPORTS_DIRECTORY, exist_ok=True)
        
    zip_path = os.path.join(config.IMPORTS_DIRECTORY, file.filename)
    with open(zip_path, "wb") as buffer:
        shutil.copyfileobj(file.file, buffer)
        
    import uuid
    unique_id = str(uuid.uuid4())
    temp_dir = os.path.join(os.path.dirname(os.path.dirname(__file__)), "temp")
    extract_dir = os.path.join(temp_dir, f"extracted_{unique_id}")
    os.makedirs(extract_dir, exist_ok=True)
    
    with zipfile.ZipFile(zip_path, 'r') as zip_ref:
        zip_ref.extractall(extract_dir)
        
    txt_file = None
    for root, dirs, files in os.walk(extract_dir):
        for name in files:
            if name.endswith('.txt'):
                txt_file = os.path.join(root, name)
                break
        if txt_file:
            break
            
    if not txt_file:
        return {"error": "No .txt file found in ZIP."}
        
    results = process_chat_file(txt_file)
    
    saved_count = 0
    duplicate_count = 0
    senders = set()
    
    seen_in_this_import = set()
    
    for res in results:
        senders.add(res['sender'])
        # Check duplicates by youtube_video_id if exists
        is_duplicate = False
        vid = res['youtube_video_id']
        if vid:
            if vid in seen_in_this_import:
                is_duplicate = True
            else:
                existing = db.query(Link).filter(Link.youtube_video_id == vid).first()
                if existing:
                    is_duplicate = True
                seen_in_this_import.add(vid)
                
        # Determine status
        if not vid:
            status = "SKIPPED_NON_YOUTUBE"
        elif res.get('is_private'):
            status = "SKIPPED_PRIVATE"
        elif is_duplicate:
            status = "DUPLICATE"
        else:
            status = "PENDING"
            
        # We save all messages to retain context, but if it's a duplicate video we can mark it
        db_link = Link(
            message_date=res['message_date'],
            sender=res['sender'],
            original_message=res['original_message'],
            extracted_url=res['extracted_url'],
            normalized_url=res['normalized_url'],
            youtube_video_id=vid,
            audio_title=res.get('audio_title'),
            channel_name=res.get('channel_name'),
            status=status
        )
        db.add(db_link)
        saved_count += 1
        if is_duplicate:
            duplicate_count += 1
            
    db.commit()
    
    # Auto-update Excel file
    try:
        from excel_service import export_links_to_excel
        all_links = db.query(Link).all()
        export_links_to_excel(all_links)
    except Exception as ex:
        print(f"Auto-export to Excel failed: {ex}")
        
    return {
        "messages_found": len(results),
        "urls_saved": saved_count,
        "unique_youtube_videos": saved_count - duplicate_count,
        "senders_found": len(senders)
    }

def verify_link_states(links: List[Link], db: Session):
    changed = False
    for link in links:
        if link.status == "COMPLETED" and link.audio_path:
            if not os.path.exists(link.audio_path):
                link.status = "PENDING"
                link.audio_path = None
                changed = True
    if changed:
        db.commit()

@app.delete("/api/links/clean/non-youtube")
def clean_non_youtube(db: Session = Depends(get_db)):
    links = db.query(Link).filter(Link.status == "SKIPPED_NON_YOUTUBE").all()
    count = len(links)
    for link in links:
        db.delete(link)
    db.commit()
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
    db.delete(link)
    db.commit()
    try:
        from excel_service import export_links_to_excel
        all_links = db.query(Link).all()
        export_links_to_excel(all_links)
    except Exception:
        pass
    return {"message": "Link deleted successfully"}

@app.get("/api/links", response_model=List[LinkResponse])
def get_links(db: Session = Depends(get_db)):
    links = db.query(Link).order_by(Link.id.desc()).all()
    verify_link_states(links, db)
    return links

@app.get("/api/downloads", response_model=List[LinkResponse])
def get_downloads(db: Session = Depends(get_db)):
    # Return links that are youtube videos and not duplicates
    links = db.query(Link).filter(
        Link.status.in_(["PENDING", "DOWNLOADING", "COMPLETED", "FAILED"])
    ).order_by(Link.id.desc()).all()
    verify_link_states(links, db)
    return links

@app.delete("/api/links")
def clear_all_links(db: Session = Depends(get_db)):
    db.query(Link).delete()
    db.commit()
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
    start_download_loop()
    await manager.broadcast("update")
    return {"message": "Download loop started"}

@app.post("/api/downloads/pause")
async def pause_downloads():
    stop_download_loop()
    await manager.broadcast("update")
    return {"message": "Download loop paused"}

@app.get("/api/downloads/status")
def get_download_status():
    from downloader import download_loop_running
    return {"is_running": download_loop_running}

@app.post("/api/downloads/retry/{link_id}")
def retry_download(link_id: int, db: Session = Depends(get_db)):
    link = db.query(Link).filter(Link.id == link_id).first()
    if link and link.status == "FAILED":
        link.status = "PENDING"
        link.error = None
        db.commit()
        return {"message": "Download queued for retry"}
    return {"error": "Link not found or not in FAILED state"}

@app.get("/api/stats", response_model=StatsResponse)
def get_stats(db: Session = Depends(get_db)):
    completed_links = db.query(Link).filter(Link.status == "COMPLETED").all()
    verify_link_states(completed_links, db)
    
    total = db.query(Link).count()
    pending = db.query(Link).filter(Link.status == "PENDING").count()
    downloaded = db.query(Link).filter(Link.status == "COMPLETED").count()
    failed = db.query(Link).filter(Link.status == "FAILED").count()
    return StatsResponse(
        total_links=total,
        pending_download=pending,
        downloaded=downloaded,
        failed_download=failed
    )

@app.post("/api/export")
def export_links(db: Session = Depends(get_db)):
    links = db.query(Link).all()
    filepath = export_links_to_excel(links)
    return {"message": "Export successful", "filepath": filepath}
