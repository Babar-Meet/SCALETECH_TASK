import os
import re
import asyncio
import json
import logging
import shutil
import wave
from typing import Optional
from faster_whisper import WhisperModel
from google import genai
from google.genai import types

from database import SessionLocal, Link
from config import config
from db_utils import safe_commit
from loop_state import loop_state

logger = logging.getLogger("ai_processor")

processor_loop_running = False

# Global state: model loaded ONCE per process, reused across all links.
_whisper_model = None
global_whisper_device = "cuda"
global_whisper_compute = "float16"


def _load_whisper(device: str, compute: str) -> WhisperModel:
    return WhisperModel(config.WHISPER_MODEL, device=device, compute_type=compute)


def get_whisper_model() -> WhisperModel:
    """Lazy singleton. Falls back to CPU (int8) permanently if CUDA fails to load."""
    global _whisper_model, global_whisper_device, global_whisper_compute
    if _whisper_model is not None:
        return _whisper_model
    try:
        _whisper_model = _load_whisper(global_whisper_device, global_whisper_compute)
        logger.info(f"Whisper model loaded on {global_whisper_device} ({global_whisper_compute})")
    except Exception as e:
        if global_whisper_device == "cuda":
            logger.warning(f"Whisper: CUDA unavailable ({e}). Using CPU permanently. Run Scripts/setup.bat to install the cuBLAS DLLs (cublas64_12.dll) needed for GPU transcription.")
            global_whisper_device = "cpu"
            global_whisper_compute = "int8"
            _whisper_model = _load_whisper("cpu", "int8")
            logger.info("Whisper: Using CPU (int8)")
        else:
            raise
    return _whisper_model


def _write_probe_wav(path: str):
    rate = 16000
    with wave.open(path, "w") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(b"\x00\x00" * (rate // 2))


def probe_whisper():
    """Startup device probe. cublas/cuBLAS DLL errors only surface at first
    inference, so this runs one tiny real transcription on the loaded model."""
    global _whisper_model, global_whisper_device, global_whisper_compute
    probe_path = os.path.join(os.path.dirname(os.path.dirname(__file__)), "temp", "whisper_probe.wav")
    try:
        model = get_whisper_model()
        if global_whisper_device == "cuda":
            try:
                os.makedirs(os.path.dirname(probe_path), exist_ok=True)
                _write_probe_wav(probe_path)
                segments, info = model.transcribe(probe_path)
                list(segments)
                logger.info("Whisper: GPU probe OK — using CUDA.")
            except Exception as e:
                logger.warning(f"Whisper: GPU runtime unavailable ({e}). Using CPU permanently. Run Scripts/setup.bat to install the cuBLAS DLLs (cublas64_12.dll) needed for GPU transcription.")
                global_whisper_device = "cpu"
                global_whisper_compute = "int8"
                _whisper_model = _load_whisper("cpu", "int8")
                logger.info("Whisper: Using CPU (int8)")
    except Exception as e:
        logger.warning(f"Whisper probe failed ({e}). Using CPU permanently.")
        global_whisper_device = "cpu"
        global_whisper_compute = "int8"
        try:
            _whisper_model = _load_whisper("cpu", "int8")
        except Exception:
            _whisper_model = None
    finally:
        try:
            if os.path.exists(probe_path):
                os.remove(probe_path)
        except Exception:
            pass


def transcribe_audio(file_path: str, link_id: int = None, loop=None):
    global _whisper_model, global_whisper_device, global_whisper_compute
    logger.info(f"[Link {link_id or '?'}] Using Whisper model: {config.WHISPER_MODEL}")
    model = get_whisper_model()

    logger.info(f"[Link {link_id or '?'}] 🎤 Transcription started for: {os.path.basename(file_path)}")

    try:
        segments, info = model.transcribe(file_path, beam_size=5)
    except Exception as e:
        if global_whisper_device == "cuda":
            # DLL errors (e.g. cublas64_12.dll) surface at inference, not load
            logger.warning(f"[Link {link_id or '?'}] GPU runtime error: {e}. Falling back to CPU permanently.")
            global_whisper_device = "cpu"
            global_whisper_compute = "int8"
            _whisper_model = _load_whisper("cpu", "int8")
            model = _whisper_model
            segments, info = model.transcribe(file_path, beam_size=5)
        else:
            raise
    logger.info(f"[Link {link_id or '?'}] Audio duration: {info.duration:.1f}s, detected language: {info.language} (prob: {info.language_probability:.2f})")

    transcript_text = ""
    for segment in segments:
        transcript_text += segment.text + " "
        if link_id and loop and info.duration > 0:
            percent = min(100, int((segment.end / info.duration) * 100))
            from websocket_manager import manager
            manager.broadcast_sync(json.dumps({"type": "progress", "id": link_id, "percent": f"TRANSCRIBING ({percent}%)"}), loop)

    logger.info(f"[Link {link_id or '?'}] ✅ Transcription complete ({len(transcript_text.split())} words)")
    return transcript_text.strip(), info.language, info.language_probability

def categorize_text(text: str, title: str):
    logger.info(f"🤖 AI categorization request for: \"{title[:60]}\"")
    prompt = f"""
You are an expert audio and music categorizer.
Based on the transcription and title of this audio file, categorize it.

Title: {title}
Transcription: {text[:2000]}  # limit to first 2000 chars for context

Determine the single most logical and standardized category for this audio.
Do NOT use a hardcoded list, but ensure your categories are broad, standard, and normalized (e.g., use "Love" instead of "Loves", use "Sad" instead of "Mood Off").
Keep the category name extremely concise (1-2 words max, like "Devotional", "Garba", "Podcast", "Comedy", "Pop").

Also, accurately determine the primary spoken language of the audio (e.g., "Hindi", "Gujarati", "English", "Punjabi"). Normalize the language name to its proper full English spelling.

Output only a valid JSON object in the exact format:
{{"category": "SelectedCategory", "language": "SelectedLanguage", "confidence": "0.XX"}}
"""

    if config.OLLAMA_MODEL:
        logger.info(f"Using Ollama model: {config.OLLAMA_MODEL} at {config.OLLAMA_HOST}")
        import urllib.request
        url = f"{config.OLLAMA_HOST}/api/generate"
        data = {
            "model": config.OLLAMA_MODEL,
            "prompt": prompt,
            "stream": False,
            "format": "json"
        }
        last_error = None
        for attempt in range(2):
            try:
                req = urllib.request.Request(url, data=json.dumps(data).encode(), headers={'Content-Type': 'application/json'})
                with urllib.request.urlopen(req, timeout=60) as response:
                    resp_data = json.loads(response.read().decode())
                    result = json.loads(resp_data.get('response', '{}'))
                    cat = result.get('category', 'Other')
                    lang = result.get('language', 'Unknown')
                    conf = str(result.get('confidence', '0.0'))
                    logger.info(f"✅ Ollama result: category={cat}, language={lang}, confidence={conf}")
                    return cat, lang, conf
            except Exception as e:
                last_error = e
                logger.warning(f"Ollama attempt {attempt + 1} failed: {e}")
                time.sleep(2 * (attempt + 1))
        # Never silently accept garbage: surface the failure so the row is marked FAILED
        raise Exception(f"Ollama failed after retries: {last_error}")

    if config.GEMINI_API_KEY:
        import time
        logger.info(f"Calling Gemini API ({config.GEMINI_MODEL})...")
        client = genai.Client(api_key=config.GEMINI_API_KEY)
        last_error = None
        for attempt in range(2):
            start_time = time.time()
            try:
                response = client.models.generate_content(
                    model=config.GEMINI_MODEL,
                    contents=prompt,
                    config=types.GenerateContentConfig(
                        response_mime_type="application/json",
                    ),
                )
                elapsed = time.time() - start_time
                logger.info(f"Gemini API returned in {elapsed:.2f} seconds.")
                try:
                    data = json.loads(response.text)
                    cat = data.get("category", "Other")
                    lang = data.get("language", "Unknown")
                    conf = str(data.get("confidence", "0.0"))
                    logger.info(f"✅ Gemini result: category={cat}, language={lang}, confidence={conf}")
                    return cat, lang, conf
                except Exception as e:
                    last_error = e
                    logger.warning(f"Failed to parse Gemini response (attempt {attempt + 1}): {e}, raw text: {response.text}")
            except Exception as e:
                last_error = e
                logger.warning(f"Gemini API call failed (attempt {attempt + 1}): {e}")
            time.sleep(2 * (attempt + 1))
        # Never silently accept malformed output: surface the failure so the row is marked FAILED
        raise Exception(f"Gemini classification failed after retries: {last_error}")

    logger.warning("No AI API configured (Gemini or Ollama). Skipping categorization.")
    return "Skipped (No API Key or Local Model)", "Unknown", "0.0"

def claim_next_classify(db) -> Optional[int]:
    """Atomically claim the next link ready for AI classification."""
    try:
        candidate = db.query(Link.id).filter(
            Link.transcription_status == "COMPLETED",
            Link.classification_status == "PENDING"
        ).order_by(Link.id.asc()).limit(1).scalar()
        if candidate is None:
            return None
        result = db.query(Link).filter(
            Link.id == candidate,
            Link.transcription_status == "COMPLETED",
            Link.classification_status == "PENDING"
        ).update({"classification_status": "CLASSIFYING"}, synchronize_session=False)
        if result != 1:
            db.rollback()
            return None
        if not safe_commit(db):
            return None
        return candidate
    except Exception as e:
        logger.warning(f"Classify claim failed: {e}")
        try:
            db.rollback()
        except Exception:
            pass
        return None


def claim_next_transcribe(db) -> Optional[int]:
    """Atomically claim the next link ready for transcription."""
    try:
        candidate = db.query(Link.id).filter(
            Link.status == "COMPLETED",
            Link.transcription_status == "PENDING",
            Link.audio_path.isnot(None)
        ).order_by(Link.id.asc()).limit(1).scalar()
        if candidate is None:
            return None
        result = db.query(Link).filter(
            Link.id == candidate,
            Link.transcription_status == "PENDING",
            Link.audio_path.isnot(None)
        ).update({"transcription_status": "TRANSCRIBING"}, synchronize_session=False)
        if result != 1:
            db.rollback()
            return None
        if not safe_commit(db):
            return None
        return candidate
    except Exception as e:
        logger.warning(f"Transcribe claim failed: {e}")
        try:
            db.rollback()
        except Exception:
            pass
        return None


async def ai_processor_loop():
    global processor_loop_running
    logger.info("AI Processor loop started.")

    while processor_loop_running:
        db = SessionLocal()
        try:
            # 1. Look for items to classify (COMPLETED transcription, PENDING classification)
            classify_id = claim_next_classify(db)
            if classify_id is not None:
                loop_state.touch()
                from websocket_manager import manager
                info = db.get(Link, classify_id)
                if info is None:
                    continue
                audio_title = info.audio_title or "unknown"
                logger.info(f"🧠 Starting AI classification for Link #{classify_id}: \"{audio_title}\"")
                await manager.broadcast(json.dumps({"type": "progress", "id": classify_id, "percent": "AI CATEGORIZING..."}))

                transcript_path = os.path.join(config.TRANSCRIPTS_DIRECTORY, f"{classify_id}_transcript.txt")
                if not os.path.exists(transcript_path):
                    logger.warning(f"⚠️ [Link #{classify_id}] Transcript file missing, re-queueing transcription.")
                    db.query(Link).filter(Link.id == classify_id).update(
                        {"transcription_status": "PENDING", "classification_status": "PENDING"},
                        synchronize_session=False)
                    safe_commit(db)
                    await manager.broadcast("update")
                    continue

                try:
                    with open(transcript_path, 'r', encoding='utf-8') as f:
                        text = f.read()

                    category, ai_lang, confidence = await asyncio.to_thread(
                        categorize_text, text, audio_title
                    )

                    # Fresh session — the row may have been deleted during the API call
                    db2 = SessionLocal()
                    try:
                        row = db2.get(Link, classify_id)
                        if row is None:
                            logger.info(f"Link #{classify_id} deleted during classification; discarding.")
                            continue
                        row.ai_category = category
                        row.ai_language = ai_lang
                        row.ai_confidence = confidence
                        if category and "Skipped" not in category and not row.category:
                            row.category = category
                        row.classification_status = "COMPLETED"
                        logger.info(f"✅ Link #{classify_id} classified: {category} / {ai_lang} (conf: {confidence})")

                        # Auto-move file to the final Music/{Language}/{Category} layout
                        if row.ai_category and row.audio_path and os.path.exists(row.audio_path):
                            from organizer import resolve_final_path

                            new_dir = resolve_final_path(row)
                            os.makedirs(new_dir, exist_ok=True)

                            filename = os.path.basename(row.audio_path)
                            new_path = os.path.join(new_dir, filename)

                            if new_path != row.audio_path:
                                shutil.move(row.audio_path, new_path)
                                row.audio_path = new_path
                                row.final_path = new_path
                                logger.info(f"[Link #{classify_id}] 📁 File moved to: {new_path}")

                        safe_commit(db2)
                    finally:
                        db2.close()
                except Exception as e:
                    logger.error(f"❌ Classification FAILED for Link #{classify_id}: {e}")
                    db2 = SessionLocal()
                    try:
                        row = db2.get(Link, classify_id)
                        if row is not None:
                            row.classification_status = "FAILED"
                            row.error = str(e)[:500]
                            safe_commit(db2)
                    finally:
                        db2.close()
                    await manager.broadcast(json.dumps({"type": "progress", "id": classify_id, "percent": "AI FAILED"}))

                # Update Excel (best-effort)
                try:
                    from excel_service import export_links_to_excel
                    db3 = SessionLocal()
                    try:
                        export_links_to_excel(db3.query(Link).all())
                    finally:
                        db3.close()
                except Exception as ex:
                    logger.error(f"Auto-export to Excel failed: {ex}")

                await manager.broadcast(json.dumps({"type": "progress", "id": classify_id, "percent": "CATEGORIZED"}))
                await manager.broadcast("update")
                continue

            # 2. Look for items to transcribe (COMPLETED download, PENDING transcription)
            transcribe_id = claim_next_transcribe(db)
            if transcribe_id is not None:
                loop_state.touch()
                from websocket_manager import manager
                info = db.get(Link, transcribe_id)
                if info is None:
                    continue
                audio_path = info.audio_path
                audio_title = info.audio_title or "unknown"

                if not audio_path or not os.path.exists(audio_path):
                    # Audio was deleted/moved externally; re-queue the download AND the
                    # downstream stages (expressed as a conditional UPDATE, no stale objects)
                    db.query(Link).filter(Link.id == transcribe_id).update(
                        {"status": "PENDING", "error": "Audio file missing - queued for re-download",
                         "transcription_status": "PENDING", "classification_status": "PENDING",
                         "ai_language": None, "ai_category": None, "ai_confidence": None,
                         "final_path": None},
                        synchronize_session=False)
                    transcript_path = os.path.join(config.TRANSCRIPTS_DIRECTORY, f"{transcribe_id}_transcript.txt")
                    if os.path.exists(transcript_path):
                        try:
                            os.remove(transcript_path)
                        except Exception:
                            pass
                    safe_commit(db)
                    logger.warning(f"⚠️ [Link #{transcribe_id}] Audio file missing ({audio_path}), re-queueing download.")
                    await manager.broadcast("update")
                    continue

                logger.info(f"🎤 Starting transcription for Link #{transcribe_id}: \"{audio_title}\"")
                await manager.broadcast(json.dumps({"type": "progress", "id": transcribe_id, "percent": "TRANSCRIBING..."}))

                try:
                    text, lang, prob = await asyncio.to_thread(transcribe_audio, audio_path, transcribe_id, asyncio.get_running_loop())

                    db2 = SessionLocal()
                    try:
                        row = db2.get(Link, transcribe_id)
                        if row is None:
                            logger.info(f"Link #{transcribe_id} deleted during transcription; discarding.")
                            transcript_path = os.path.join(config.TRANSCRIPTS_DIRECTORY, f"{transcribe_id}_transcript.txt")
                            if os.path.exists(transcript_path):
                                try:
                                    os.remove(transcript_path)
                                except Exception:
                                    pass
                            continue
                        # Save transcript to file
                        if not os.path.exists(config.TRANSCRIPTS_DIRECTORY):
                            os.makedirs(config.TRANSCRIPTS_DIRECTORY, exist_ok=True)
                        transcript_path = os.path.join(config.TRANSCRIPTS_DIRECTORY, f"{transcribe_id}_transcript.txt")
                        with open(transcript_path, 'w', encoding='utf-8') as f:
                            f.write(text)
                        row.transcription_status = "COMPLETED"
                        row.ai_language = lang
                        row.classification_status = "PENDING"  # Now ready for classification
                        safe_commit(db2)
                        logger.info(f"✅ Link #{transcribe_id} transcription complete. Queued for classification.")
                    finally:
                        db2.close()
                    await manager.broadcast(json.dumps({"type": "progress", "id": transcribe_id, "percent": "WAITING AI..."}))
                except Exception as e:
                    logger.error(f"❌ Transcription FAILED for Link #{transcribe_id}: {e}")
                    db2 = SessionLocal()
                    try:
                        row = db2.get(Link, transcribe_id)
                        if row is not None:
                            row.transcription_status = "FAILED"
                            row.error = str(e)[:500]
                            safe_commit(db2)
                    finally:
                        db2.close()
                    await manager.broadcast(json.dumps({"type": "progress", "id": transcribe_id, "percent": "TRANSCRIPTION FAILED"}))
                await manager.broadcast("update")
                continue

            await asyncio.sleep(2)
        except Exception as e:
            logger.error(f"Error in ai_processor loop: {e}")
            await asyncio.sleep(2)
        finally:
            db.close()

def start_ai_processor_loop():
    global processor_loop_running
    if not processor_loop_running:
        processor_loop_running = True
        asyncio.create_task(ai_processor_loop())

def stop_ai_processor_loop():
    global processor_loop_running
    processor_loop_running = False
