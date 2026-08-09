import os
from datetime import datetime
from sqlalchemy import create_engine, Column, Integer, String, DateTime, Text
from sqlalchemy.orm import declarative_base, sessionmaker
from config import config

# Ensure DB directory exists
db_dir = os.path.dirname(config.DB_PATH)
if db_dir and not os.path.exists(db_dir):
    os.makedirs(db_dir, exist_ok=True)

engine = create_engine(f"sqlite:///{config.DB_PATH}")
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

Base = declarative_base()

class Link(Base):
    __tablename__ = "links"
    
    id = Column(Integer, primary_key=True, index=True)
    message_date = Column(DateTime, default=datetime.utcnow)
    sender = Column(String, index=True)
    original_message = Column(String)
    extracted_url = Column(String)
    normalized_url = Column(String)
    youtube_video_id = Column(String, index=True, nullable=True)
    status = Column(String, default="PENDING") # PENDING, DOWNLOADING, COMPLETED, FAILED, DUPLICATE, SKIPPED_NON_YOUTUBE
    
    # Downloaded metadata
    audio_path = Column(String, nullable=True)
    audio_title = Column(String, nullable=True)
    channel_name = Column(String, nullable=True)
    duration = Column(Integer, nullable=True) # in seconds
    error = Column(String, nullable=True)
    category = Column(String, nullable=True) # User-defined category for folder structuring
    transcription_status = Column(String(50), default="PENDING")
    
    # Classification Stage
    classification_status = Column(String(50), default="PENDING")
    ai_language = Column(String(100), nullable=True)
    ai_category = Column(String(100), nullable=True)
    ai_confidence = Column(String(50), nullable=True)
    
    user_language = Column(String(100), nullable=True)
    user_category = Column(String(100), nullable=True)
    final_path = Column(Text, nullable=True)
    
    created_at = Column(DateTime, default=datetime.utcnow)

Base.metadata.create_all(bind=engine)

def ensure_schema_compatibility():
    """
    Adds columns introduced after the table was first created so an existing
    app.db survives upgrades instead of crashing with 'no such column'.
    """
    from sqlalchemy import text, inspect
    inspector = inspect(engine)
    if "links" not in inspector.get_table_names():
        return
    existing = {col["name"] for col in inspector.get_columns("links")}
    additions = {
        "transcription_status": "VARCHAR(50) DEFAULT 'PENDING'",
        "classification_status": "VARCHAR(50) DEFAULT 'PENDING'",
        "ai_language": "VARCHAR(100)",
        "ai_category": "VARCHAR(100)",
        "ai_confidence": "VARCHAR(50)",
        "user_language": "VARCHAR(100)",
        "user_category": "VARCHAR(100)",
        "final_path": "TEXT",
    }
    with engine.begin() as conn:
        for col, ddl in additions.items():
            if col not in existing:
                conn.execute(text(f"ALTER TABLE links ADD COLUMN {col} {ddl}"))

ensure_schema_compatibility()

def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
