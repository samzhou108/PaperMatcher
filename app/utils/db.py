"""SQLite database for tracking processed articles (deduplication)."""

import json
import sqlite3
import threading
from pathlib import Path
from datetime import datetime
from typing import List, Optional, Dict, Any


DB_DIR = Path.home() / ".papermatcher"
DB_PATH = DB_DIR / "papermatcher.db"

# Migrate data from old ~/.paperpilot directory if present
_old_dir = Path.home() / ".paperpilot"
if _old_dir.exists() and not DB_DIR.exists():
    import shutil
    shutil.copytree(str(_old_dir), str(DB_DIR))


class ArticleDatabase:
    """Thread-safe SQLite wrapper for tracking processed articles."""

    def __init__(self, db_path: Optional[Path] = None):
        self.db_path = Path(db_path) if db_path else DB_PATH
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._local = threading.local()
        self._init_db()

    def _get_conn(self) -> sqlite3.Connection:
        """Get thread-local connection."""
        if not hasattr(self._local, "conn") or self._local.conn is None:
            self._local.conn = sqlite3.connect(str(self.db_path), check_same_thread=False)
        return self._local.conn

    def _init_db(self):
        """Create tables if they don't exist."""
        conn = self._get_conn()
        cursor = conn.cursor()

        cursor.execute("""
            CREATE TABLE IF NOT EXISTS processed_articles (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                doi TEXT UNIQUE,
                pmid TEXT UNIQUE,
                title TEXT NOT NULL,
                journal TEXT,
                authors TEXT,
                url TEXT,
                abstract TEXT,
                relevance_score INTEGER,
                relevance_reason TEXT,
                summary TEXT,
                tags TEXT,
                include INTEGER DEFAULT 1,
                feedback TEXT,
                processed_at TEXT NOT NULL
            )
        """)

        # Migrate older DBs that are missing columns or have stale columns
        try:
            cursor.execute("ALTER TABLE processed_articles DROP COLUMN source_email")
        except sqlite3.OperationalError:
            pass  # Column doesn't exist (expected in new DBs)

        for col_def in [
            "authors TEXT", "url TEXT", "abstract TEXT",
            "relevance_reason TEXT", "summary TEXT", "pmid TEXT",
        ]:
            try:
                cursor.execute(f"ALTER TABLE processed_articles ADD COLUMN {col_def}")
            except sqlite3.OperationalError:
                pass  # Column already exists

        # Add 'include' column if missing (for edit/selection feature)
        try:
            cursor.execute("ALTER TABLE processed_articles ADD COLUMN include INTEGER DEFAULT 1")
        except sqlite3.OperationalError:
            pass

        # Add 'feedback' column if missing (Phase 2: user feedback loop)
        try:
            cursor.execute("ALTER TABLE processed_articles ADD COLUMN feedback TEXT")
        except sqlite3.OperationalError:
            pass

        # Migrate old run_history: rename emails_checked -> searches
        try:
            cursor.execute("ALTER TABLE run_history RENAME COLUMN emails_checked TO searches")
        except sqlite3.OperationalError:
            pass  # Already renamed or doesn't exist

        cursor.execute("""
            CREATE TABLE IF NOT EXISTS run_history (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                run_at TEXT NOT NULL,
                searches INTEGER DEFAULT 0,
                articles_found INTEGER DEFAULT 0,
                articles_scored INTEGER DEFAULT 0,
                articles_saved INTEGER DEFAULT 0,
                errors INTEGER DEFAULT 0
            )
        """)

        cursor.execute("""
            CREATE INDEX IF NOT EXISTS idx_doi ON processed_articles(doi)
        """)
        cursor.execute("""
            CREATE INDEX IF NOT EXISTS idx_pmid ON processed_articles(pmid)
        """)

        # Feedback history: persists rejected articles with search context so
        # rejection is topic-scoped rather than global.
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS feedback_history (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                doi TEXT,
                pmid TEXT,
                title TEXT,
                feedback TEXT NOT NULL,
                deleted_at TEXT NOT NULL,
                search_or_keywords TEXT,
                search_and_keywords TEXT
            )
        """)
        # Migrate existing databases that don't have the keyword columns yet
        for col, default in [("search_or_keywords", "NULL"), ("search_and_keywords", "NULL")]:
            try:
                cursor.execute(f"ALTER TABLE feedback_history ADD COLUMN {col} TEXT DEFAULT {default}")
                conn.commit()
            except Exception:
                pass  # column already exists
        cursor.execute("""
            CREATE INDEX IF NOT EXISTS idx_fh_doi ON feedback_history(doi)
        """)
        cursor.execute("""
            CREATE INDEX IF NOT EXISTS idx_fh_pmid ON feedback_history(pmid)
        """)

        conn.commit()

    def is_processed(self, doi: str = "", pmid: str = "", title: str = "") -> bool:
        """Check if an article has already been processed."""
        conn = self._get_conn()
        cursor = conn.cursor()

        if doi:
            cursor.execute("SELECT 1 FROM processed_articles WHERE doi = ?", (doi,))
            if cursor.fetchone():
                return True

        if pmid:
            cursor.execute("SELECT 1 FROM processed_articles WHERE pmid = ?", (pmid,))
            if cursor.fetchone():
                return True

        if title:
            cursor.execute("SELECT 1 FROM processed_articles WHERE title = ?", (title,))
            if cursor.fetchone():
                return True

        return False

    def mark_processed(self, article_data: Dict[str, Any]) -> int:
        """Mark an article as processed and return its database ID."""
        conn = self._get_conn()
        cursor = conn.cursor()

        authors = article_data.get("authors", [])
        authors_str = ", ".join(authors) if isinstance(authors, list) else str(authors or "")

        cursor.execute("""
            INSERT OR REPLACE INTO processed_articles
            (doi, pmid, title, journal, authors, url, abstract,
             relevance_score, relevance_reason, summary, tags, include, feedback, processed_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            article_data.get("doi", ""),
            article_data.get("pmid", ""),
            article_data.get("title", ""),
            article_data.get("journal", ""),
            authors_str,
            article_data.get("url", ""),
            article_data.get("abstract", ""),
            article_data.get("relevance_score", 0),
            article_data.get("relevance_reason", ""),
            article_data.get("summary", ""),
            ",".join(article_data.get("tags", [])),
            article_data.get("include", 1),
            article_data.get("feedback", ""),
            datetime.now().isoformat(),
        ))

        # Return the inserted ID
        conn.commit()
        return cursor.lastrowid

    def update_article(self, article_id: int, **kwargs):
        """Update specific fields of a saved article by ID."""
        conn = self._get_conn()
        cursor = conn.cursor()

        allowed_fields = {
            "relevance_score": "INTEGER",
            "summary": "TEXT",
            "tags": "TEXT",
            "relevance_reason": "TEXT",
            "include": "INTEGER",
            "feedback": "TEXT",
        }

        updates = []
        values = []
        for key, value in kwargs.items():
            if key in allowed_fields:
                updates.append(f"{key} = ?")
                values.append(value)

        if not updates:
            return

        values.append(article_id)
        query = f"UPDATE processed_articles SET {', '.join(updates)} WHERE id = ?"
        cursor.execute(query, values)
        conn.commit()

    def set_feedback(self, article_id: int, feedback: str):
        """Record user feedback (relevant / not_relevant) for an article."""
        conn = self._get_conn()
        cursor = conn.cursor()
        cursor.execute(
            "UPDATE processed_articles SET feedback = ? WHERE id = ?",
            (feedback if feedback else None, article_id),
        )
        conn.commit()

    def get_feedback(self, article_id: int) -> str | None:
        """Get the current feedback for an article."""
        conn = self._get_conn()
        cursor = conn.cursor()
        cursor.execute("SELECT feedback FROM processed_articles WHERE id = ?", (article_id,))
        row = cursor.fetchone()
        return row[0] if row and row[0] else None

    def reject_article(self, article_id: int,
                       or_keywords: Optional[List[str]] = None,
                       and_keywords: Optional[List[str]] = None):
        """Reject an article: log to feedback_history with search context, then delete.

        or_keywords: broad OR-block keywords active at rejection time (soft-match context).
        and_keywords: AND/must-include keywords active at rejection time (hard-match context).
        """
        conn = self._get_conn()
        cursor = conn.cursor()
        cursor.execute(
            "SELECT doi, pmid, title FROM processed_articles WHERE id = ?", (article_id,)
        )
        row = cursor.fetchone()
        if row:
            doi, pmid, title = row
            cursor.execute("""
                INSERT INTO feedback_history
                    (doi, pmid, title, feedback, deleted_at, search_or_keywords, search_and_keywords)
                VALUES (?, ?, ?, 'not_relevant', ?, ?, ?)
            """, (
                doi or "", pmid or "", title or "",
                datetime.now().isoformat(),
                json.dumps([k.lower().strip() for k in (or_keywords or [])]),
                json.dumps([k.lower().strip() for k in (and_keywords or [])]),
            ))
        cursor.execute("DELETE FROM processed_articles WHERE id = ?", (article_id,))
        conn.commit()

    def is_rejected(self, doi: str = "", pmid: str = "",
                    or_keywords: Optional[List[str]] = None,
                    and_keywords: Optional[List[str]] = None) -> bool:
        """Topic-scoped rejection check.

        Returns True only if this article was rejected in a similar search context:
        - Hard match: any current AND/must-include keyword matches stored AND keywords.
        - Soft match: ≥40% overlap between current OR keywords and stored OR keywords.
        - Legacy (no keywords provided): global match for backwards compatibility.
        """
        conn = self._get_conn()
        cursor = conn.cursor()

        records = []
        if doi:
            cursor.execute(
                "SELECT search_or_keywords, search_and_keywords FROM feedback_history "
                "WHERE doi = ? AND feedback = 'not_relevant'", (doi,)
            )
            records.extend(cursor.fetchall())
        if pmid and not records:
            cursor.execute(
                "SELECT search_or_keywords, search_and_keywords FROM feedback_history "
                "WHERE pmid = ? AND feedback = 'not_relevant'", (pmid,)
            )
            records.extend(cursor.fetchall())

        if not records:
            return False

        # Legacy behaviour: no context provided → global rejection
        if not or_keywords and not and_keywords:
            return True

        current_or = {k.lower().strip() for k in (or_keywords or [])}
        current_and = {k.lower().strip() for k in (and_keywords or [])}

        for stored_or_json, stored_and_json in records:
            stored_or = set(json.loads(stored_or_json or "[]"))
            stored_and = set(json.loads(stored_and_json or "[]"))

            # Hard match: any AND keyword in common → same topic context
            if current_and and stored_and and (current_and & stored_and):
                return True

            # Soft match: ≥40% overlap of OR keywords
            if current_or and stored_or:
                overlap = len(current_or & stored_or)
                smaller = min(len(current_or), len(stored_or))
                if smaller > 0 and (overlap / smaller) >= 0.4:
                    return True

        return False

    def get_feedback_history(self, limit: int = 10, feedback: str = "relevant") -> list[dict]:
        """Return recent articles with the given feedback label (for prompt injection)."""
        conn = self._get_conn()
        cursor = conn.cursor()
        cursor.execute(
            "SELECT title, tags FROM processed_articles WHERE feedback = ? ORDER BY processed_at DESC LIMIT ?",
            (feedback, limit),
        )
        return [{"title": row[0], "tags": row[1] or ""} for row in cursor.fetchall()]

    def delete_article(self, article_id: int):
        """Delete a saved article by ID.

        If the article has feedback, saves doi/pmid/title/feedback to
        feedback_history before deleting so the decision persists across runs.
        """
        conn = self._get_conn()
        cursor = conn.cursor()

        # Preserve feedback before deletion
        cursor.execute(
            "SELECT doi, pmid, title, feedback FROM processed_articles WHERE id = ?",
            (article_id,),
        )
        row = cursor.fetchone()
        if row:
            doi, pmid, title, feedback = row
            if feedback:
                cursor.execute("""
                    INSERT INTO feedback_history (doi, pmid, title, feedback, deleted_at)
                    VALUES (?, ?, ?, ?, ?)
                """, (doi or "", pmid or "", title or "", feedback, datetime.now().isoformat()))

        cursor.execute("DELETE FROM processed_articles WHERE id = ?", (article_id,))
        conn.commit()

    def clear_all(self):
        """Delete all processed articles from the database."""
        conn = self._get_conn()
        cursor = conn.cursor()
        cursor.execute("DELETE FROM processed_articles")
        conn.commit()

    def log_run(self, searches: int = 0, articles_found: int = 0,
                articles_scored: int = 0, articles_saved: int = 0, errors: int = 0):
        """Log a pipeline run for stats."""
        conn = self._get_conn()
        cursor = conn.cursor()

        cursor.execute("""
            INSERT INTO run_history (run_at, searches, articles_found, articles_scored, articles_saved, errors)
            VALUES (?, ?, ?, ?, ?, ?)
        """, (datetime.now().isoformat(), searches, articles_found, articles_scored, articles_saved, errors))

        conn.commit()

    def get_stats(self) -> Dict[str, Any]:
        """Get processing statistics."""
        conn = self._get_conn()
        cursor = conn.cursor()

        cursor.execute("SELECT COUNT(*) FROM processed_articles WHERE include = 1")
        total = cursor.fetchone()[0]

        cursor.execute("SELECT COUNT(*) FROM processed_articles WHERE include = 1 AND processed_at > datetime('now', '-7 days')")
        this_week = cursor.fetchone()[0]

        cursor.execute("SELECT tags FROM processed_articles WHERE tags != '' AND include = 1")
        tag_counts = {}
        for row in cursor.fetchall():
            for tag in row[0].split(","):
                tag = tag.strip()
                if tag:
                    tag_counts[tag] = tag_counts.get(tag, 0) + 1

        top_tags = sorted(tag_counts.items(), key=lambda x: x[1], reverse=True)[:5]

        return {
            "total_papers": total,
            "this_week": this_week,
            "top_tags": top_tags,
        }

    def get_all_processed(self, limit: int = 100) -> List[Dict[str, Any]]:
        """Get list of processed articles."""
        conn = self._get_conn()
        cursor = conn.cursor()

        cursor.execute("""
            SELECT id, doi, pmid, title, journal, authors, url, abstract,
                   relevance_score, relevance_reason, summary, tags, include, feedback, processed_at
            FROM processed_articles
            WHERE include = 1
            ORDER BY processed_at DESC
            LIMIT ?
        """, (limit,))

        columns = ["id", "doi", "pmid", "title", "journal", "authors", "url", "abstract",
                   "relevance_score", "relevance_reason", "summary", "tags", "include", "feedback", "processed_at"]
        return [dict(zip(columns, row)) for row in cursor.fetchall()]

    def get_seen_ids(self) -> tuple[set[str], set[str]]:
        """Return (pmids, dois) for all processed and rejected articles.

        Used to pre-filter PubMed search results before fetching abstracts,
        so already-seen articles don't consume slots in max_results.
        """
        conn = self._get_conn()
        cursor = conn.cursor()
        pmids: set[str] = set()
        dois:  set[str] = set()

        cursor.execute("SELECT pmid, doi FROM processed_articles WHERE pmid != '' OR doi != ''")
        for pmid, doi in cursor.fetchall():
            if pmid:
                pmids.add(pmid)
            if doi:
                dois.add(doi)

        cursor.execute("SELECT pmid, doi FROM feedback_history WHERE pmid != '' OR doi != ''")
        for pmid, doi in cursor.fetchall():
            if pmid:
                pmids.add(pmid)
            if doi:
                dois.add(doi)

        return pmids, dois

    def close(self):
        """Close database connection."""
        if hasattr(self._local, "conn") and self._local.conn:
            self._local.conn.close()
            self._local.conn = None