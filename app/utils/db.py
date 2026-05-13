"""SQLite database for tracking processed articles (deduplication)."""

import sqlite3
import threading
from pathlib import Path
from datetime import datetime
from typing import List, Optional, Dict, Any


DB_DIR = Path.home() / ".paperPilot"
DB_PATH = DB_DIR / "paperpilot.db"


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

        # Feedback history: persists feedback for deleted articles so rejected
        # papers are never re-added across pipeline runs.
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS feedback_history (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                doi TEXT,
                pmid TEXT,
                title TEXT,
                feedback TEXT NOT NULL,
                deleted_at TEXT NOT NULL
            )
        """)
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

    def mark_processed(self, article_data: Dict[str, Any]):
        """Mark an article as processed."""
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

        conn.commit()

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

    def is_rejected(self, doi: str = "", pmid: str = "") -> bool:
        """Return True if this article was previously deleted with 'not_relevant' feedback.

        Used by the pipeline to permanently skip user-rejected articles.
        """
        conn = self._get_conn()
        cursor = conn.cursor()
        if doi:
            cursor.execute(
                "SELECT 1 FROM feedback_history WHERE doi = ? AND feedback = 'not_relevant'",
                (doi,),
            )
            if cursor.fetchone():
                return True
        if pmid:
            cursor.execute(
                "SELECT 1 FROM feedback_history WHERE pmid = ? AND feedback = 'not_relevant'",
                (pmid,),
            )
            if cursor.fetchone():
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

    def close(self):
        """Close database connection."""
        if hasattr(self._local, "conn") and self._local.conn:
            self._local.conn.close()
            self._local.conn = None