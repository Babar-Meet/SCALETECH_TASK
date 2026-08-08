import re
from urllib.parse import urlparse, parse_qs

# Regex to find URLs in text, robust against punctuation
URL_REGEX = re.compile(r'(https?://[^\s<>"\'\[\]{}|\\^`]+)')

def extract_urls(text: str):
    """Extracts all URLs from a given text."""
    urls = []
    # Find all matches
    for match in URL_REGEX.finditer(text):
        url = match.group(1)
        # Clean trailing punctuation
        url = url.rstrip('.,;!?)')
        urls.append(url)
    return urls

def normalize_youtube_url(url: str):
    """
    Normalizes a YouTube URL and extracts the 11-character video ID.
    Returns (normalized_url, video_id) or (url, None) if not a YouTube URL.
    """
    try:
        parsed = urlparse(url)
        netloc = parsed.netloc.lower()
        path = parsed.path
        
        video_id = None
        
        # Check if it's a YouTube domain
        if netloc in ('youtube.com', 'www.youtube.com', 'm.youtube.com', 'music.youtube.com'):
            if path == '/watch':
                qs = parse_qs(parsed.query)
                if 'v' in qs:
                    video_id = qs['v'][0]
            elif path.startswith('/embed/'):
                video_id = path.split('/')[2]
            elif path.startswith('/shorts/'):
                video_id = path.split('/')[2]
        elif netloc == 'youtu.be':
            video_id = path.lstrip('/')
            
        if video_id and len(video_id) == 11:
            normalized = f"https://youtube.com/watch?v={video_id}"
            return normalized, video_id
            
        return url, None
    except Exception:
        return url, None
