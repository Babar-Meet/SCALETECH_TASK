import logging
from sqlalchemy.exc import SQLAlchemyError

logger = logging.getLogger("db_utils")


def safe_commit(db) -> bool:
    """Commit a session; on any SQLAlchemy error roll back and report instead of
    crashing the caller (avoids the 'expected to update 1 row(s); 0 were matched'
    StaleDataError killing background loops)."""
    try:
        db.commit()
        return True
    except SQLAlchemyError as e:
        logger.warning(f"Commit failed, rolling back: {e}")
        try:
            db.rollback()
        except Exception:
            pass
        return False


def claim_by_id(db, model, link_id, expected_field, expected_value, new_values: dict) -> bool:
    """Atomically claim a row: UPDATE ... WHERE id=:id AND <expected_field>=<expected_value>.

    Returns True only if exactly one row was updated AND the commit succeeded.
    If the row was deleted or already claimed by another session, returns False
    without raising (no StaleDataError)."""
    try:
        result = db.query(model).filter(
            getattr(model, expected_field) == expected_value,
            model.id == link_id,
        ).update(new_values, synchronize_session=False)
        if result != 1:
            db.rollback()
            return False
        return safe_commit(db)
    except SQLAlchemyError as e:
        logger.warning(f"Claim failed for link {link_id}: {e}")
        try:
            db.rollback()
        except Exception:
            pass
        return False
