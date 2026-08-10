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

def iter_chat_items(filepath: str):
    """
    Parses a WhatsApp chat export (.txt) and yields one dict per extracted URL:
    {message_date, sender, original_message, extracted_url, normalized_url,
     youtube_video_id, message_index}. Pure parsing — no network or DB work,
     so it is fast even for chats with thousands of links.
    """
    try:
        import dateutil.parser
    except ImportError:
        import subprocess
        import sys
        subprocess.check_call([sys.executable, "-m", "pip", "install", "python-dateutil"])
        import dateutil.parser

    message_index = 0
    for msg in parse_whatsapp_chat(filepath):
        message_index += 1
        urls = extract_urls(msg['message'])
        for url in urls:
            normalized, video_id = normalize_youtube_url(url)
            try:
                date_obj = dateutil.parser.parse(msg['date'], fuzzy=True)
            except Exception:
                date_obj = datetime.utcnow()

            yield {
                'message_date': date_obj,
                'sender': msg['sender'],
                'original_message': msg['message'],
                'extracted_url': url,
                'normalized_url': normalized,
                'youtube_video_id': video_id,
                'message_index': message_index,
            }

