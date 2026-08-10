import asyncio
import json
import logging
import os
import shutil
import time
import urllib.error
import urllib.request
import uuid
import zipfile
from concurrent.futures import ThreadPoolExecutor

from sqlalchemy.exc import SQLAlchemyError

from config import config
from database import SessionLocal, Link
from whatsapp_parser import iter_chat_items
from websocket_manager import manager
from db_utils import safe_commit

logger = logging.getLogger("importer")

# In-memory registry of active imports. Progress only — no DB table, no schema change.
_IMPORTS: dict = {}
_import_lock = None


class OembedPrivateError(Exception):
    def __init__(self, video_id):
        super().__init__(f"Private video: {video_id}")
        self.video_id = video_id


def _get_import_lock():
    global _import_lock
    if _import_lock is None:
        _import_lock = asyncio.Lock()
    return _import_lock


def _public_progress(entry: dict) -> dict:
    total = None
    if entry["phase"] == "metadata":
        total = entry["metadata_total"]
    elif entry["phase"] == "saving":
        total = entry["links_found"]
    elif entry["phase"] in ("done", "error", "canceled"):
        total = entry["saved"]
    return {
        "import_id": entry["import_id"],
        "filename": entry["filename"],
        "phase": entry["phase"],
        "messages_scanned": entry["messages_scanned"],
        "links_found": entry["links_found"],
        "metadata_total": entry["metadata_total"],
        "metadata_fetched": entry["metadata_fetched"],
        "saved": entry["saved"],
        "duplicates": entry["duplicates"],
        "senders_found": entry["senders_found"],
        "error": entry["error"],
        "done": entry["done"],
        "canceled": entry["canceled"],
        "total": total,
    }


def create_import(filename: str, zip_path: str, extract_dir: str) -> str:
    import_id = str(uuid.uuid4())
    _IMPORTS[import_id] = {
        "import_id": import_id,
        "filename": filename,
        "phase": "parsing",
        "messages_scanned": 0,
        "links_found": 0,
        "metadata_total": 0,
        "metadata_fetched": 0,
        "saved": 0,
        "duplicates": 0,
        "senders_found": 0,
        "error": None,
        "done": False,
        "canceled": False,
        "created_at": time.time(),
        "updated_at": time.time(),
        "_zip_path": zip_path,
        "_extract_dir": extract_dir,
        "_cancel_requested": False,
        "_last_broadcast": 0.0,
    }
    return import_id


def get_import(import_id: str):
    entry = _IMPORTS.get(import_id)
    if not entry:
        return None
    return _public_progress(entry)


def cancel_import(import_id: str) -> bool:
    entry = _IMPORTS.get(import_id)
    if not entry:
        return False
    if entry["done"] or entry["canceled"]:
        return False
    entry["_cancel_requested"] = True
    return True


def _cancelled(entry: dict) -> bool:
    return entry.get("_cancel_requested", False)


def _touch(import_id: str, force: bool = False, **fields):
    entry = _IMPORTS.get(import_id)
    if not entry:
        return
    entry.update(fields)
    entry["updated_at"] = time.time()
    now = time.time()
    # Throttle fast-moving phases to ~1 broadcast per 250ms
    if not force and now - entry.get("_last_broadcast", 0.0) < 0.25:
        return
    entry["_last_broadcast"] = now
    loop = entry.get("_loop")
    if loop and loop.is_running():
        try:
            asyncio.run_coroutine_threadsafe(_do_broadcast(entry), loop)
        except Exception:
            pass


async def _do_broadcast(entry: dict):
    try:
        await manager.broadcast(json.dumps({"type": "import", **_public_progress(entry)}))
    except Exception:
        pass


def _cleanup(entry: dict):
    extract_dir = entry.get("_extract_dir")
    if extract_dir:
        shutil.rmtree(extract_dir, ignore_errors=True)
    zip_path = entry.get("_zip_path")
    if zip_path and os.path.exists(zip_path):
        try:
            os.remove(zip_path)
        except Exception:
            pass


def _finish(entry: dict, phase: str):
    if phase == "canceled":
        entry["canceled"] = True
        entry["error"] = "Import canceled"
    elif phase == "error" and not entry.get("error"):
        entry["error"] = "Import failed"
    entry["phase"] = phase
    entry["done"] = True
    _touch(entry["import_id"], force=True)


def fetch_oembed(video_id: str) -> dict:
    """Fast oembed metadata fetch (no yt-dlp). Raises OembedPrivateError on
    401/403 (private video), any other HTTP error propagates as HTTPError."""
    url = f"https://www.youtube.com/oembed?url=https://www.youtube.com/watch?v={video_id}&format=json"
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    try:
        with urllib.request.urlopen(req, timeout=config.OEMBED_TIMEOUT) as response:
            data = json.loads(response.read().decode())
            return {"audio_title": data.get("title"), "channel_name": data.get("author_name")}
    except urllib.error.HTTPError as e:
        if e.code in (401, 403):
            raise OembedPrivateError(video_id)
        raise


def _extract_and_parse(entry: dict):
    """Sync phase 1: extract only the .txt from the zip, parse it, count messages."""
    zip_path = entry["_zip_path"]
    extract_dir = entry["_extract_dir"]
    os.makedirs(extract_dir, exist_ok=True)

    with zipfile.ZipFile(zip_path, "r") as zf:
        for name in zf.namelist():
            if name.endswith(".txt"):
                target = os.path.join(extract_dir, os.path.basename(name))
                with zf.open(name) as src, open(target, "wb") as dst:
                    shutil.copyfileobj(src, dst)

    txt_file = None
    for root, dirs, files in os.walk(extract_dir):
        for name in files:
            if name.endswith(".txt"):
                txt_file = os.path.join(root, name)
                break
        if txt_file:
            break
    if not txt_file:
        raise ValueError("No .txt file found in ZIP.")

    raw_items = []
    senders = set()
    messages_scanned = 0
    links_found = 0
    for item in iter_chat_items(txt_file):
        messages_scanned = item["message_index"]
        links_found += 1
        senders.add(item["sender"])
        raw_items.append(item)
    return raw_items, messages_scanned, links_found, senders


def _save_phase(entry: dict, raw_items: list, meta_map: dict) -> tuple:
    """Sync phase 3: dedupe + insert all rows in chunks, ONE commit at the end.
    Runs under the import lock so two concurrent imports can't double-insert."""
    db = SessionLocal()
    rows = []
    saved = 0
    duplicates = 0
    seen_in_import = set()
    try:
        for item in raw_items:
            vid = item["youtube_video_id"]
            is_duplicate = False
            if vid:
                if vid in seen_in_import:
                    is_duplicate = True
                else:
                    existing = db.query(Link).filter(Link.youtube_video_id == vid).first()
                    if existing:
                        is_duplicate = True
                    seen_in_import.add(vid)

            if not vid:
                status = "SKIPPED_NON_YOUTUBE"
            elif is_duplicate:
                status = "DUPLICATE"
                duplicates += 1
            else:
                meta = meta_map.get(vid)
                if meta == "private":
                    status = "SKIPPED_PRIVATE"
                else:
                    status = "PENDING"

            meta = meta_map.get(vid) if isinstance(meta_map.get(vid), dict) else None
            rows.append(Link(
                message_date=item["message_date"],
                sender=item["sender"],
                original_message=item["original_message"],
                extracted_url=item["extracted_url"],
                normalized_url=item["normalized_url"],
                youtube_video_id=vid,
                audio_title=(meta or {}).get("audio_title"),
                channel_name=(meta or {}).get("channel_name"),
                status=status,
            ))
            saved += 1
            if len(rows) >= config.IMPORT_CHUNK_SIZE:
                db.add_all(rows)
                rows = []

        if rows:
            db.add_all(rows)

        if not safe_commit(db):
            return False, 0, 0
        return True, saved, duplicates
    except SQLAlchemyError as e:
        logger.error(f"Import save failed: {e}")
        db.rollback()
        return False, 0, 0
    finally:
        db.close()


def _excel_export_phase():
    try:
        from excel_service import export_links_to_excel
        db = SessionLocal()
        try:
            all_links = db.query(Link).all()
            export_links_to_excel(all_links)
        finally:
            db.close()
    except Exception as e:
        logger.error(f"Auto-export to Excel failed: {e}")


async def _metadata_phase(entry: dict, raw_items: list) -> dict:
    """Async phase 2: pre-dedupe by video_id, fetch oembed metadata in parallel,
    mark private videos. Returns {video_id: meta_dict|'private'|None}."""
    unique_ids = []
    seen = set()
    for item in raw_items:
        vid = item["youtube_video_id"]
        if vid and vid not in seen:
            seen.add(vid)
            unique_ids.append(vid)

    db = SessionLocal()
    try:
        existing_ids = set()
        if unique_ids:
            rows = db.query(Link.youtube_video_id).filter(Link.youtube_video_id.in_(unique_ids)).all()
            existing_ids = {r[0] for r in rows}
    finally:
        db.close()

    entry["metadata_total"] = len(unique_ids)
    ids_to_fetch = [vid for vid in unique_ids if vid not in existing_ids]

    meta_map = {}
    fetched = 0

    def _bump():
        nonlocal fetched
        fetched += 1
        _touch(entry["import_id"], metadata_fetched=fetched)

    with ThreadPoolExecutor(max_workers=config.METADATA_WORKERS) as executor:
        vids = ids_to_fetch
        wrapped = [asyncio.wrap_future(executor.submit(fetch_oembed, vid)) for vid in vids]
        results = await asyncio.gather(*wrapped, return_exceptions=True)
        for vid, res in zip(vids, results):
            if isinstance(res, OembedPrivateError):
                meta_map[vid] = "private"
                logger.info(f"Private video skipped: {vid}")
            elif isinstance(res, Exception):
                meta_map[vid] = None
            else:
                meta_map[vid] = res
            _bump()
            if _cancelled(entry):
                return meta_map
    _touch(entry["import_id"], force=True)
    return meta_map


async def run_import_task(import_id: str):
    entry = _IMPORTS.get(import_id)
    if not entry:
        return
    entry["_loop"] = asyncio.get_running_loop()
    try:
        # ---- parsing ----
        raw_items, msgs, links, senders = await asyncio.to_thread(_extract_and_parse, entry)
        entry["messages_scanned"] = msgs
        entry["links_found"] = links
        entry["senders_found"] = len(senders)
        _touch(import_id, force=True)
        if _cancelled(entry):
            _finish(entry, "canceled")
            return
        if not raw_items:
            _finish(entry, "done")
            return

        # ---- metadata ----
        entry["phase"] = "metadata"
        _touch(import_id, force=True)
        meta_map = await _metadata_phase(entry, raw_items)
        if _cancelled(entry):
            _finish(entry, "canceled")
            return

        # ---- saving (one commit, under lock) ----
        entry["phase"] = "saving"
        _touch(import_id, force=True)
        async with _get_import_lock():
            ok, saved, duplicates = await asyncio.to_thread(_save_phase, entry, raw_items, meta_map)
        if not ok:
            entry["error"] = "Failed to save links to database"
            _finish(entry, "error")
            return
        entry["saved"] = saved
        entry["duplicates"] = duplicates
        _touch(import_id, force=True)

        # ---- done ----
        await asyncio.to_thread(_excel_export_phase)
        _finish(entry, "done")
        logger.info(f"✅ Import {import_id} complete: {saved} saved, {duplicates} duplicates, {len(senders)} senders.")
    except Exception as e:
        logger.error(f"Import {import_id} failed: {e}")
        entry["error"] = str(e)[:500]
        _finish(entry, "error")
    finally:
        _cleanup(entry)
