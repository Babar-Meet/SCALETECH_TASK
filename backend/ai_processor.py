import os
import asyncio
import json
import logging
from faster_whisper import WhisperModel
from google import genai
from google.genai import types

from database import SessionLocal, Link
from config import config

logger = logging.getLogger("ai_processor")

processor_loop_running = False

# Global state to remember if GPU works or if we should permanently fall back to CPU
global_whisper_device = "cuda"
global_whisper_compute = "float16"

def transcribe_audio(file_path: str, link_id: int = None, loop=None):
    global global_whisper_device, global_whisper_compute
    logger.info(f"[Link {link_id or '?'}] Loading Whisper model: {config.WHISPER_MODEL}")
    
    device = global_whisper_device
    compute_type = global_whisper_compute
    
    try:
        # Attempt to load model on current device
        model = WhisperModel(config.WHISPER_MODEL, device=device, compute_type=compute_type)
        if device == "cuda":
            logger.info(f"[Link {link_id or '?'}] Whisper: Using GPU")
        else:
            logger.info(f"[Link {link_id or '?'}] Whisper: Using CPU")
    except Exception as e:
        if device == "cuda":
            # Fall back to CPU if GPU is unavailable or fails to init
            logger.debug(f"GPU initialization failed: {e}")
            global_whisper_device = "cpu"
            global_whisper_compute = config.WHISPER_COMPUTE_TYPE
            device = global_whisper_device
            compute_type = global_whisper_compute
            model = WhisperModel(config.WHISPER_MODEL, device=device, compute_type=compute_type)
            logger.info(f"[Link {link_id or '?'}] Whisper: Using CPU (GPU unavailable)")
        else:
            raise e
    
    logger.info(f"[Link {link_id or '?'}] 🎤 Transcription started for: {os.path.basename(file_path)}")
    
    try:
        segments, info = model.transcribe(file_path, beam_size=5)
    except Exception as e:
        if device == "cuda":
            # Sometimes initialization succeeds but runtime fails (e.g. missing DLLs)
            logger.warning(f"[Link {link_id or '?'}] GPU runtime error: {e}. Falling back to CPU permanently.")
            global_whisper_device = "cpu"
            global_whisper_compute = config.WHISPER_COMPUTE_TYPE
            device = global_whisper_device
            compute_type = global_whisper_compute
            model = WhisperModel(config.WHISPER_MODEL, device=device, compute_type=compute_type)
            logger.info(f"[Link {link_id or '?'}] Whisper: Using CPU (GPU unavailable)")
            segments, info = model.transcribe(file_path, beam_size=5)
        else:
            raise e
    logger.info(f"[Link {link_id or '?'}] Audio duration: {info.duration:.1f}s, detected language: {info.language} (prob: {info.language_probability:.2f})")
    
    transcript_text = ""
    for segment in segments:
        transcript_text += segment.text + " "
        if link_id and loop and info.duration > 0:
            percent = min(100, int((segment.end / info.duration) * 100))
            from websocket_manager import manager
            import json
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
        try:
            req = urllib.request.Request(url, data=json.dumps(data).encode(), headers={'Content-Type': 'application/json'})
            with urllib.request.urlopen(req) as response:
                resp_data = json.loads(response.read().decode())
                result = json.loads(resp_data.get('response', '{}'))
                cat = result.get('category', 'Other')
                lang = result.get('language', 'Unknown')
                conf = str(result.get('confidence', '0.0'))
                logger.info(f"✅ Ollama result: category={cat}, language={lang}, confidence={conf}")
                return cat, lang, conf
        except Exception as e:
            logger.error(f"Ollama failed: {e}")
            return "Other", "Unknown", "0.0"

    if config.GEMINI_API_KEY:
        import time
        logger.info(f"Calling Gemini API ({config.GEMINI_MODEL})...")
        start_time = time.time()
        client = genai.Client(api_key=config.GEMINI_API_KEY)
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
            logger.error(f"Failed to parse Gemini response: {e}, raw text: {response.text}")
            return "Other", "Unknown", "0.0"

    logger.warning("No AI API configured (Gemini or Ollama). Skipping categorization.")
    return "Skipped (No API Key or Local Model)", "Unknown", "0.0"

async def ai_processor_loop():
    global processor_loop_running
    processor_loop_running = True
    logger.info("AI Processor loop started.")
    
    while processor_loop_running:
        db = SessionLocal()
        try:
            # 1. Look for items to classify (COMPLETED transcription, PENDING classification)
            link_to_classify = db.query(Link).filter(
                Link.transcription_status == "COMPLETED",
                Link.classification_status == "PENDING"
            ).first()
            
            if link_to_classify:
                logger.info(f"🧠 Starting AI classification for Link #{link_to_classify.id}: \"{link_to_classify.audio_title or 'unknown'}\"")
                link_to_classify.classification_status = "CLASSIFYING"
                db.commit()
                
                try:
                    from websocket_manager import manager
                    await manager.broadcast(json.dumps({"type": "progress", "id": link_to_classify.id, "percent": "AI CATEGORIZING..."}))
                    
                    transcript_path = os.path.join(config.TRANSCRIPTS_DIRECTORY, f"{link_to_classify.id}_transcript.txt")
                    text = ""
                    if os.path.exists(transcript_path):
                        with open(transcript_path, 'r', encoding='utf-8') as f:
                            text = f.read()
                            
                    category, ai_lang, confidence = await asyncio.to_thread(
                        categorize_text, text, link_to_classify.audio_title or ""
                    )
                    
                    link_to_classify.ai_category = category
                    link_to_classify.ai_language = ai_lang
                    link_to_classify.ai_confidence = confidence
                    if category and "Skipped" not in category and not link_to_classify.category:
                        link_to_classify.category = category
                        
                    link_to_classify.classification_status = "COMPLETED"
                    logger.info(f"✅ Link #{link_to_classify.id} classified: {category} / {ai_lang} (conf: {confidence})")
                    
                    # Optional: Auto-move file based on ai_category
                    if link_to_classify.ai_category and link_to_classify.audio_path and os.path.exists(link_to_classify.audio_path):
                        import shutil
                        import re
                        
                        full_lang = link_to_classify.ai_language or 'Unknown'
                        safe_cat = re.sub(r'[<>:"/\\|?*]', '_', link_to_classify.ai_category).strip()
                        new_dir = os.path.join(config.AUDIO_DIRECTORY, full_lang, safe_cat)
                        os.makedirs(new_dir, exist_ok=True)
                        
                        filename = os.path.basename(link_to_classify.audio_path)
                        new_path = os.path.join(new_dir, filename)
                        
                        if new_path != link_to_classify.audio_path:
                            shutil.move(link_to_classify.audio_path, new_path)
                            link_to_classify.audio_path = new_path
                            link_to_classify.final_path = new_path
                            logger.info(f"[Link #{link_to_classify.id}] 📁 File moved to: {new_path}")

                    db.commit()
                    
                    # Update Excel
                    try:
                        from excel_service import export_links_to_excel
                        all_links = db.query(Link).all()
                        export_links_to_excel(all_links)
                    except Exception as ex:
                        logger.error(f"Auto-export to Excel failed: {ex}")

                    await manager.broadcast(json.dumps({"type": "progress", "id": link_to_classify.id, "percent": "CATEGORIZED"}))
                    await manager.broadcast("update")
                except Exception as e:
                    logger.error(f"❌ Classification FAILED for Link #{link_to_classify.id}: {e}")
                    link_to_classify.classification_status = "FAILED"
                    db.commit()
                    await manager.broadcast(json.dumps({"type": "progress", "id": link_to_classify.id, "percent": "AI FAILED"}))
                    await manager.broadcast("update")
                continue

            # 2. Look for items to transcribe (COMPLETED download, PENDING transcription)
            link = db.query(Link).filter(
                Link.status == "COMPLETED", 
                Link.transcription_status == "PENDING",
                Link.audio_path.isnot(None)
            ).first()
            
            if link and not os.path.exists(link.audio_path):
                # Audio was deleted/moved externally; re-queue the download
                from websocket_manager import manager
                link.status = "PENDING"
                link.error = "Audio file missing - queued for re-download"
                logger.warning(f"⚠️ [Link #{link.id}] Audio file missing ({link.audio_path}), re-queueing download.")
                db.commit()
                await manager.broadcast("update")
                continue
            
            if link and os.path.exists(link.audio_path):
                logger.info(f"🎤 Starting transcription for Link #{link.id}: \"{link.audio_title or 'unknown'}\"")
                link.transcription_status = "TRANSCRIBING"
                db.commit()
                
                try:
                    from websocket_manager import manager
                    await manager.broadcast(json.dumps({"type": "progress", "id": link.id, "percent": "TRANSCRIBING..."}))
                    
                    text, lang, prob = await asyncio.to_thread(transcribe_audio, link.audio_path, link.id, asyncio.get_running_loop())
                    
                    # Save transcript to file
                    if not os.path.exists(config.TRANSCRIPTS_DIRECTORY):
                        os.makedirs(config.TRANSCRIPTS_DIRECTORY, exist_ok=True)
                    
                    transcript_path = os.path.join(config.TRANSCRIPTS_DIRECTORY, f"{link.id}_transcript.txt")
                    with open(transcript_path, 'w', encoding='utf-8') as f:
                        f.write(text)
                        
                    link.transcription_status = "COMPLETED"
                    link.ai_language = lang
                    link.classification_status = "PENDING" # Now ready for classification
                    db.commit()
                    logger.info(f"✅ Link #{link.id} transcription complete. Queued for classification.")
                    
                    await manager.broadcast(json.dumps({"type": "progress", "id": link.id, "percent": "WAITING AI..."}))
                    await manager.broadcast("update")
                except Exception as e:
                    logger.error(f"❌ Transcription FAILED for Link #{link.id}: {e}")
                    link.transcription_status = "FAILED"
                    db.commit()
                    await manager.broadcast(json.dumps({"type": "progress", "id": link.id, "percent": "TRANSCRIPTION FAILED"}))
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
        asyncio.create_task(ai_processor_loop())

def stop_ai_processor_loop():
    global processor_loop_running
    processor_loop_running = False
