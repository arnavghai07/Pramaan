"""
storage/ — PRAMAAN persistence layer.

Kept apart from both engine/ (pure vision + rules, runs with no database at
all) and api/ (HTTP shape only). See storage/database.py for the on-disk
layout and storage/repository.py for every read and write.
"""
from storage.database import DATA_DIR, EVIDENCE_DIR, get_db, init_db, session_scope
from storage.models import Inspection

__all__ = ["DATA_DIR", "EVIDENCE_DIR", "Inspection",
           "get_db", "init_db", "session_scope"]
