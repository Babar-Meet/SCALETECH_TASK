import os
from dotenv import load_dotenv

load_dotenv(os.path.join(os.path.dirname(os.path.dirname(__file__)), '.env'))

class Config:
    GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")
    GEMINI_MODEL = os.getenv("GEMINI_MODEL", "gemma-4-31b-it")
    AUDIO_DIRECTORY = os.getenv("AUDIO_DIRECTORY", "./data/audio")
    MUSIC_DIRECTORY = os.getenv("MUSIC_DIRECTORY", "./data/music")
    TRANSCRIPTS_DIRECTORY = os.getenv("TRANSCRIPTS_DIRECTORY", "./data/transcripts")
    REPORTS_DIRECTORY = os.getenv("REPORTS_DIRECTORY", "./data/reports")
    IMPORTS_DIRECTORY = os.getenv("IMPORTS_DIRECTORY", "./data/imports")
    WHISPER_MODEL = os.getenv("WHISPER_MODEL", "small")
    WHISPER_COMPUTE_TYPE = os.getenv("WHISPER_COMPUTE_TYPE", "int8")
    
    # Import pipeline tuning
    METADATA_WORKERS = int(os.getenv("METADATA_WORKERS", "10"))
    IMPORT_CHUNK_SIZE = int(os.getenv("IMPORT_CHUNK_SIZE", "200"))
    OEMBED_TIMEOUT = int(os.getenv("OEMBED_TIMEOUT", "3"))
    
    DB_PATH = os.getenv("DB_PATH", "./data/app.db")
    OLLAMA_MODEL = os.getenv("OLLAMA_MODEL", "") # e.g. "gemma:2b" or "llama3"
    OLLAMA_HOST = os.getenv("OLLAMA_HOST", "http://localhost:11434")

config = Config()
