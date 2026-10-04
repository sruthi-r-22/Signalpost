"""
SQLite Persistence Layer for Signalpost.
Manages storage for companies, extracted facts, evidence records, research runs, and field change audit logs.
"""
import json
import sqlite3
from pathlib import Path
from typing import Any, Dict, List, Optional
from datetime import datetime, timezone

from app.config import get_settings
from app.models.company import (
    CompanyProfile,
    ResearchRun,
    CompanyHistoryEntry
)
from app.models.evidence import Evidence, SourceType, ConflictItem


class Database:
    def __init__(self, db_path: Optional[str] = None):
        if db_path is None:
            settings = get_settings()
            db_path = settings.DATABASE_PATH
        self.db_path = db_path
        self._init_db()

    def _get_connection(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON;")
        conn.execute("PRAGMA journal_mode = WAL;")
        return conn

    def _init_db(self) -> None:
        """Create database tables if they do not exist."""
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.executescript("""
                CREATE TABLE IF NOT EXISTS companies (
                    company_number TEXT PRIMARY KEY,
                    name TEXT NOT NULL,
                    status TEXT NOT NULL,
                    profile_json TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    latest_run_id TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS facts (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    company_number TEXT NOT NULL,
                    field_name TEXT NOT NULL,
                    field_value_json TEXT,
                    confidence REAL NOT NULL,
                    is_verified INTEGER NOT NULL DEFAULT 1,
                    updated_at TEXT NOT NULL,
                    UNIQUE(company_number, field_name),
                    FOREIGN KEY(company_number) REFERENCES companies(company_number) ON DELETE CASCADE
                );

                CREATE TABLE IF NOT EXISTS evidence (
                    id TEXT PRIMARY KEY,
                    company_number TEXT NOT NULL,
                    run_id TEXT NOT NULL,
                    field_name TEXT NOT NULL,
                    field_value_json TEXT,
                    source_url TEXT NOT NULL,
                    source_title TEXT,
                    source_type TEXT NOT NULL,
                    retrieved_at TEXT NOT NULL,
                    publication_date TEXT,
                    confidence REAL NOT NULL,
                    explanation TEXT NOT NULL,
                    is_verified INTEGER NOT NULL DEFAULT 1,
                    FOREIGN KEY(company_number) REFERENCES companies(company_number) ON DELETE CASCADE
                );

                CREATE TABLE IF NOT EXISTS research_runs (
                    run_id TEXT PRIMARY KEY,
                    company_number TEXT NOT NULL,
                    run_type TEXT NOT NULL,
                    status TEXT NOT NULL,
                    sources_count INTEGER NOT NULL DEFAULT 0,
                    evidence_count INTEGER NOT NULL DEFAULT 0,
                    conflicts_count INTEGER NOT NULL DEFAULT 0,
                    duration_seconds REAL NOT NULL DEFAULT 0.0,
                    created_at TEXT NOT NULL,
                    error_message TEXT
                );

                CREATE TABLE IF NOT EXISTS company_history (
                    id TEXT PRIMARY KEY,
                    company_number TEXT NOT NULL,
                    run_id TEXT NOT NULL,
                    field_name TEXT NOT NULL,
                    old_value_json TEXT,
                    new_value_json TEXT,
                    change_type TEXT NOT NULL,
                    changed_at TEXT NOT NULL,
                    FOREIGN KEY(company_number) REFERENCES companies(company_number) ON DELETE CASCADE
                );

                CREATE INDEX IF NOT EXISTS idx_evidence_company ON evidence(company_number);
                CREATE INDEX IF NOT EXISTS idx_evidence_url ON evidence(source_url);
                CREATE INDEX IF NOT EXISTS idx_runs_company ON research_runs(company_number);
                CREATE INDEX IF NOT EXISTS idx_history_company ON company_history(company_number);
            """)
            conn.commit()

    def check_health(self) -> bool:
        """Check if SQLite database is responsive."""
        try:
            with self._get_connection() as conn:
                cursor = conn.cursor()
                cursor.execute("SELECT 1;")
                return cursor.fetchone()[0] == 1
        except Exception:
            return False

    def save_company_profile(
        self,
        profile: CompanyProfile,
        run: ResearchRun,
        history_entries: Optional[List[CompanyHistoryEntry]] = None
    ) -> None:
        """Persist or update company profile, research run, evidence, and audit history."""
        now = datetime.now(timezone.utc).isoformat()
        with self._get_connection() as conn:
            cursor = conn.cursor()

            # 1. Upsert company record
            cursor.execute("""
                INSERT INTO companies (company_number, name, status, profile_json, created_at, updated_at, latest_run_id)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(company_number) DO UPDATE SET
                    name = excluded.name,
                    status = excluded.status,
                    profile_json = excluded.profile_json,
                    updated_at = excluded.updated_at,
                    latest_run_id = excluded.latest_run_id
            """, (
                profile.company_number,
                profile.identity.name,
                profile.identity.status,
                profile.model_dump_json(),
                now,
                now,
                run.run_id
            ))

            # 2. Insert Research Run
            cursor.execute("""
                INSERT INTO research_runs (
                    run_id, company_number, run_type, status,
                    sources_count, evidence_count, conflicts_count,
                    duration_seconds, created_at, error_message
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(run_id) DO UPDATE SET
                    status = excluded.status,
                    duration_seconds = excluded.duration_seconds,
                    error_message = excluded.error_message
            """, (
                run.run_id,
                run.company_number,
                run.run_type,
                run.status,
                run.sources_count,
                run.evidence_count,
                run.conflicts_count,
                run.duration_seconds,
                run.created_at,
                run.error_message
            ))

            # 3. Upsert key facts
            for field_name, value in profile.facts.items():
                cursor.execute("""
                    INSERT INTO facts (company_number, field_name, field_value_json, confidence, is_verified, updated_at)
                    VALUES (?, ?, ?, ?, 1, ?)
                    ON CONFLICT(company_number, field_name) DO UPDATE SET
                        field_value_json = excluded.field_value_json,
                        confidence = excluded.confidence,
                        updated_at = excluded.updated_at
                """, (
                    profile.company_number,
                    field_name,
                    json.dumps(value, default=str),
                    profile.overall_confidence,
                    now
                ))

            # 4. Insert new evidence without duplicate URL + field + value combinations
            existing_evidence = set()
            cursor.execute(
                "SELECT field_name, source_url, field_value_json FROM evidence WHERE company_number = ?",
                (profile.company_number,)
            )
            for row in cursor.fetchall():
                existing_evidence.add((row["field_name"], row["source_url"], row["field_value_json"]))

            for ev in profile.evidence_list:
                val_json = json.dumps(ev.value, default=str)
                key = (ev.field, ev.source_url, val_json)
                if key not in existing_evidence:
                    cursor.execute("""
                        INSERT OR IGNORE INTO evidence (
                            id, company_number, run_id, field_name, field_value_json,
                            source_url, source_title, source_type, retrieved_at,
                            publication_date, confidence, explanation, is_verified
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """, (
                        ev.id,
                        profile.company_number,
                        run.run_id,
                        ev.field,
                        val_json,
                        ev.source_url,
                        ev.source_title,
                        ev.source_type.value,
                        ev.retrieved_at,
                        ev.publication_date,
                        ev.confidence,
                        ev.explanation,
                        1 if ev.is_verified else 0
                    ))
                    existing_evidence.add(key)

            # 5. Insert history change audit entries
            if history_entries:
                for entry in history_entries:
                    cursor.execute("""
                        INSERT INTO company_history (
                            id, company_number, run_id, field_name,
                            old_value_json, new_value_json, change_type, changed_at
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    """, (
                        entry.id,
                        entry.company_number,
                        entry.run_id,
                        entry.field_name,
                        json.dumps(entry.old_value, default=str) if entry.old_value is not None else None,
                        json.dumps(entry.new_value, default=str) if entry.new_value is not None else None,
                        entry.change_type,
                        entry.changed_at
                    ))

            conn.commit()

    def get_company_profile(self, company_number: str) -> Optional[CompanyProfile]:
        """Retrieve stored profile for a company number."""
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT profile_json FROM companies WHERE company_number = ?", (company_number,))
            row = cursor.fetchone()
            if not row:
                return None
            return CompanyProfile.model_validate_json(row["profile_json"])

    def get_all_companies(self, limit: int = 50) -> List[Dict[str, Any]]:
        """List summary of researched companies."""
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                SELECT company_number, name, status, created_at, updated_at, latest_run_id
                FROM companies
                ORDER BY updated_at DESC
                LIMIT ?
            """, (limit,))
            return [dict(row) for row in cursor.fetchall()]

    def get_history_for_company(self, company_number: str) -> List[CompanyHistoryEntry]:
        """Fetch audit history log of changed fields for a company."""
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                SELECT id, company_number, run_id, field_name, old_value_json, new_value_json, change_type, changed_at
                FROM company_history
                WHERE company_number = ?
                ORDER BY changed_at DESC
            """, (company_number,))
            results = []
            for row in cursor.fetchall():
                results.append(CompanyHistoryEntry(
                    id=row["id"],
                    company_number=row["company_number"],
                    run_id=row["run_id"],
                    field_name=row["field_name"],
                    old_value=json.loads(row["old_value_json"]) if row["old_value_json"] else None,
                    new_value=json.loads(row["new_value_json"]) if row["new_value_json"] else None,
                    change_type=row["change_type"],
                    changed_at=row["changed_at"]
                ))
            return results

    def get_research_runs(self, company_number: str) -> List[ResearchRun]:
        """Fetch all research runs for a company."""
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                SELECT run_id, company_number, run_type, status, sources_count,
                       evidence_count, conflicts_count, duration_seconds, created_at, error_message
                FROM research_runs
                WHERE company_number = ?
                ORDER BY created_at DESC
            """, (company_number,))
            return [ResearchRun(**dict(row)) for row in cursor.fetchall()]


_db_instance: Optional[Database] = None


def get_db() -> Database:
    """Singleton database accessor."""
    global _db_instance
    if _db_instance is None:
        _db_instance = Database()
    return _db_instance
