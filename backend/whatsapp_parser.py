import re
import logging
from datetime import datetime
from url_extractor import extract_urls, normalize_youtube_url

logger = logging.getLogger("whatsapp_parser")

# Standard WhatsApp date format regex (supports [date, time] and date, time -)
# Examples: 
# [21/04/2023, 10:30:45] Babar: message
# 21/04/2023, 10:30 - Babar: message
TIMESTAMP_REGEX = re.compile(
    r'^\[?(\d{1,4}[-/.]\d{1,2}[-/.]\d{1,4},\s*\d{1,2}:\d{2}(?::\d{2})?(?:\s*[aApP][mM])?)\]?\s*[-]?\s*'
)

# System messages usually do not have a "Sender: " part. We can detect this.
# If a line doesn't match 'Sender: message', it's likely a system message.
SENDER_REGEX = re.compile(r'^([^:]+):\s*(.*)$', re.DOTALL)

# Some group-chat exports append a trailing colon to system events, e.g.
# "[..] You added Babar Meet: " or "[..] Raju removed Sita: ". Detect these
# by keyword even though they match SENDER_REGEX with an empty message body.
SYSTEM_EVENT_KEYWORDS = re.compile(
    r'\b(?:added|removed|left|joined|created|deleted|changed|pinned|missed|'
    r'ended|started|named|encrypted|archived|unarchived)\b',
    re.IGNORECASE)

def parse_date(date_str: str) -> datetime:
    # A simple fallback, in a real scenario we'd use dateutil.parser or similar for robustness
    # For MVP, we can just return the raw string or try a generic parsing
    try:
        from dateutil import parser
        return parser.parse(date_str, fuzzy=True)
    except:
        return datetime.utcnow()

def parse_whatsapp_chat(filepath: str):
    """
    Parses a WhatsApp chat export (.txt).
    Yields dicts with date, sender, message, and extracted URLs.
    """
    current_message = None
    
    with open(filepath, 'r', encoding='utf-8') as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
                
            match = TIMESTAMP_REGEX.match(line)
            if match:
                # New message starts
                if current_message:
                    yield current_message
                    
                timestamp_str = match.group(1)
                content = line[match.end():]
                
                sender_match = SENDER_REGEX.match(content)
                if sender_match:
                    sender = sender_match.group(1)
                    message_text = sender_match.group(2)

                    if not message_text.strip() and SYSTEM_EVENT_KEYWORDS.search(sender):
                        # System event with trailing colon (e.g. "You added X: ")
                        current_message = None
                        continue

                    current_message = {
                        'date': timestamp_str,
                        'sender': sender,
                        'message': message_text
                    }
                else:
                    # System message, ignore it
                    current_message = None
            else:
                # Continuation of the previous message
                if current_message:
                    current_message['message'] += '\n' + line
                    
    # Yield the last message if exists
    if current_message:
        yield current_message

import urllib.request
import json
from concurrent.futures import ThreadPoolExecutor, as_completed

from downloader import get_info_with_retry

def fetch_youtube_metadata(url, video_id):
    metadata = {
        'audio_title': None,
        'channel_name': None
    }
    if not video_id:
        return metadata
        
    try:
        # Fast oembed fetch first
        oembed_url = f"https://www.youtube.com/oembed?url=https://www.youtube.com/watch?v={video_id}&format=json"
        req = urllib.request.Request(oembed_url, headers={'User-Agent': 'Mozilla/5.0'})
        with urllib.request.urlopen(req, timeout=3) as response:
            data = json.loads(response.read().decode())
            metadata['audio_title'] = data.get('title')
            metadata['channel_name'] = data.get('author_name')
            return metadata
    except Exception as e:
        logger.debug(f"Oembed failed for {video_id}, falling back to yt-dlp: {e}")
        try:
            # Fallback to robust yt-dlp based metadata fetcher
            info = get_info_with_retry(url, retry_on_403=True)
            metadata['audio_title'] = info.get('title')
            metadata['channel_name'] = info.get('uploader', info.get('uploader_id', info.get('channel')))
        except Exception as e2:
            err_msg = str(e2)
            if "Private video" in err_msg or "Sign in if you've been granted access" in err_msg:
                metadata['is_private'] = True
                logger.info(f"Skipping private video: {video_id}")
            else:
                logger.warning(f"Failed to fetch metadata for {video_id}: {e2}")
    return metadata

def process_chat_file(filepath: str):
    results = []
    try:
        import dateutil.parser
    except ImportError:
        import subprocess
        import sys
        subprocess.check_call([sys.executable, "-m", "pip", "install", "python-dateutil"])
        import dateutil.parser

    raw_items = []
    for msg in parse_whatsapp_chat(filepath):
        urls = extract_urls(msg['message'])
        for url in urls:
            normalized, video_id = normalize_youtube_url(url)
            try:
                date_obj = dateutil.parser.parse(msg['date'], fuzzy=True)
            except:
                date_obj = datetime.utcnow()
                
            raw_items.append({
                'message_date': date_obj,
                'sender': msg['sender'],
                'original_message': msg['message'],
                'extracted_url': url,
                'normalized_url': normalized,
                'youtube_video_id': video_id
            })
            
    # Fetch metadata concurrently
    with ThreadPoolExecutor(max_workers=10) as executor:
        future_to_item = {
            executor.submit(fetch_youtube_metadata, item['normalized_url'], item['youtube_video_id']): item 
            for item in raw_items
        }
        
        for future in as_completed(future_to_item):
            item = future_to_item[future]
            try:
                meta = future.result()
                item['audio_title'] = meta.get('audio_title')
                item['channel_name'] = meta.get('channel_name')
                item['is_private'] = meta.get('is_private', False)
            except Exception:
                item['audio_title'] = None
                item['channel_name'] = None
                item['is_private'] = False
            results.append(item)
            
    # Sort results back by date to keep it somewhat chronological
    results.sort(key=lambda x: x['message_date'])
    return results
