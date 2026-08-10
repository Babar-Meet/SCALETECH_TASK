import os
import re

from config import config

INVALID_CHARS = re.compile(r'[<>:"/\\|?*\x00-\x1f]')


def safe_component(name: str, fallback: str) -> str:
    cleaned = INVALID_CHARS.sub('_', (name or '').strip()) or fallback
    return cleaned


def resolve_final_path(link):
    """
    Single source of truth for the final Music/{Language}/{Category} layout.

    User choices (user_language / user_category / category) always win over AI
    guesses (ai_language / ai_category). Returns the target directory path
    (idempotent — does not move anything).
    """
    lang = safe_component(link.user_language or link.ai_language, "Unknown")
    cat = safe_component(link.user_category or link.category or link.ai_category, "Other")
    return os.path.join(config.MUSIC_DIRECTORY, lang, cat)
