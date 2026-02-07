"""SQLite database client for indexing and searching emails with FTS5."""

import json
import os
import sqlite3
from pathlib import Path
from typing import Any, Generator, Optional

from .models import EmailMessage


class DatabaseError(Exception):
    """Exception raised for database errors."""

    pass


class EmailDatabase:
    """Client for indexing and searching emails in SQLite with FTS5."""

    DEFAULT_DB_PATH = "pst_emails.db"

    def __init__(self, db_path: Optional[str] = None):
        """
        Initialize the SQLite database client.

        Args:
            db_path: Path to the SQLite database file. Defaults to pst_emails.db in current directory.
        """
        self.db_path = db_path or self.DEFAULT_DB_PATH
        self._conn: Optional[sqlite3.Connection] = None

    def __enter__(self) -> "EmailDatabase":
        """Open database connection."""
        self.connect()
        return self

    def __exit__(self, exc_type, exc_val, exc_tb) -> None:  # type: ignore
        """Close database connection."""
        self.close()

    def connect(self) -> None:
        """Connect to the database and create tables if needed."""
        try:
            self._conn = sqlite3.connect(self.db_path)
            self._conn.row_factory = sqlite3.Row
            self._create_tables()
        except sqlite3.Error as e:
            raise DatabaseError(f"Failed to connect to database: {e}") from e

    def close(self) -> None:
        """Close the database connection."""
        if self._conn:
            try:
                self._conn.close()
            except Exception:
                pass
            self._conn = None

    def _create_tables(self) -> None:
        """Create the emails table and FTS5 virtual table."""
        if not self._conn:
            raise DatabaseError("Database not connected")

        cursor = self._conn.cursor()

        # Main emails table
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS emails (
                message_id TEXT PRIMARY KEY,
                subject TEXT,
                sender TEXT,
                sender_email TEXT,
                recipients_to TEXT,
                recipients_cc TEXT,
                recipients_bcc TEXT,
                date_sent TEXT,
                date_received TEXT,
                body_text TEXT,
                body_html TEXT,
                folder_path TEXT,
                attachments TEXT,
                has_attachments INTEGER,
                importance TEXT,
                pst_file TEXT
            )
        """)

        # FTS5 virtual table for full-text search
        cursor.execute("""
            CREATE VIRTUAL TABLE IF NOT EXISTS emails_fts USING fts5(
                message_id,
                subject,
                sender,
                sender_email,
                recipients_to,
                body_text,
                content='emails',
                content_rowid='rowid'
            )
        """)

        # Triggers to keep FTS index in sync
        cursor.execute("""
            CREATE TRIGGER IF NOT EXISTS emails_ai AFTER INSERT ON emails BEGIN
                INSERT INTO emails_fts(rowid, message_id, subject, sender, sender_email, recipients_to, body_text)
                VALUES (new.rowid, new.message_id, new.subject, new.sender, new.sender_email, new.recipients_to, new.body_text);
            END
        """)

        cursor.execute("""
            CREATE TRIGGER IF NOT EXISTS emails_ad AFTER DELETE ON emails BEGIN
                INSERT INTO emails_fts(emails_fts, rowid, message_id, subject, sender, sender_email, recipients_to, body_text)
                VALUES ('delete', old.rowid, old.message_id, old.subject, old.sender, old.sender_email, old.recipients_to, old.body_text);
            END
        """)

        cursor.execute("""
            CREATE TRIGGER IF NOT EXISTS emails_au AFTER UPDATE ON emails BEGIN
                INSERT INTO emails_fts(emails_fts, rowid, message_id, subject, sender, sender_email, recipients_to, body_text)
                VALUES ('delete', old.rowid, old.message_id, old.subject, old.sender, old.sender_email, old.recipients_to, old.body_text);
                INSERT INTO emails_fts(rowid, message_id, subject, sender, sender_email, recipients_to, body_text)
                VALUES (new.rowid, new.message_id, new.subject, new.sender, new.sender_email, new.recipients_to, new.body_text);
            END
        """)

        # Create indexes for common queries
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_date_sent ON emails(date_sent)")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_sender_email ON emails(sender_email)")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_folder_path ON emails(folder_path)")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_pst_file ON emails(pst_file)")

        self._conn.commit()

    def delete_database(self) -> None:
        """Delete the database file."""
        self.close()
        if os.path.exists(self.db_path):
            os.remove(self.db_path)

    def recreate_database(self) -> None:
        """Delete and recreate the database."""
        self.delete_database()
        self.connect()

    def index_email(self, email: EmailMessage) -> None:
        """
        Index a single email.

        Args:
            email: The email message to index.
        """
        if not self._conn:
            raise DatabaseError("Database not connected")

        cursor = self._conn.cursor()

        # Serialize lists to JSON
        recipients_to = json.dumps(email.recipients_to)
        recipients_cc = json.dumps(email.recipients_cc)
        recipients_bcc = json.dumps(email.recipients_bcc)
        attachments = json.dumps([
            {"filename": a.filename, "size": a.size, "content_type": a.content_type}
            for a in email.attachments
        ])

        cursor.execute("""
            INSERT OR REPLACE INTO emails (
                message_id, subject, sender, sender_email,
                recipients_to, recipients_cc, recipients_bcc,
                date_sent, date_received, body_text, body_html,
                folder_path, attachments, has_attachments, importance, pst_file
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            email.message_id,
            email.subject,
            email.sender,
            email.sender_email,
            recipients_to,
            recipients_cc,
            recipients_bcc,
            email.date_sent.isoformat() if email.date_sent else None,
            email.date_received.isoformat() if email.date_received else None,
            email.body_text,
            email.body_html,
            email.folder_path,
            attachments,
            1 if email.has_attachments else 0,
            email.importance,
            email.pst_file,
        ))

        self._conn.commit()

    def bulk_index_emails(
        self,
        emails: Generator[EmailMessage, None, None],
        batch_size: int = 500,
    ) -> tuple[int, int]:
        """
        Bulk index emails for better performance.

        Args:
            emails: Generator of EmailMessage objects.
            batch_size: Number of documents per batch (for commit frequency).

        Returns:
            Tuple of (success_count, error_count).
        """
        if not self._conn:
            raise DatabaseError("Database not connected")

        success_count = 0
        error_count = 0
        batch_count = 0

        cursor = self._conn.cursor()

        for email in emails:
            try:
                recipients_to = json.dumps(email.recipients_to)
                recipients_cc = json.dumps(email.recipients_cc)
                recipients_bcc = json.dumps(email.recipients_bcc)
                attachments = json.dumps([
                    {"filename": a.filename, "size": a.size, "content_type": a.content_type}
                    for a in email.attachments
                ])

                cursor.execute("""
                    INSERT OR REPLACE INTO emails (
                        message_id, subject, sender, sender_email,
                        recipients_to, recipients_cc, recipients_bcc,
                        date_sent, date_received, body_text, body_html,
                        folder_path, attachments, has_attachments, importance, pst_file
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """, (
                    email.message_id,
                    email.subject,
                    email.sender,
                    email.sender_email,
                    recipients_to,
                    recipients_cc,
                    recipients_bcc,
                    email.date_sent.isoformat() if email.date_sent else None,
                    email.date_received.isoformat() if email.date_received else None,
                    email.body_text,
                    email.body_html,
                    email.folder_path,
                    attachments,
                    1 if email.has_attachments else 0,
                    email.importance,
                    email.pst_file,
                ))

                success_count += 1
                batch_count += 1

                # Commit periodically for better performance
                if batch_count >= batch_size:
                    self._conn.commit()
                    batch_count = 0

            except Exception as e:
                error_count += 1
                print(f"Warning: Failed to index email {email.message_id}: {e}")

        # Final commit
        self._conn.commit()

        return success_count, error_count

    def search(
        self,
        query: str,
        size: int = 20,
        offset: int = 0,
    ) -> dict[str, Any]:
        """
        Search emails by keyword using FTS5.

        Args:
            query: Search query string.
            size: Number of results to return.
            offset: Offset for pagination.

        Returns:
            Search results dictionary.
        """
        if not self._conn:
            raise DatabaseError("Database not connected")

        cursor = self._conn.cursor()

        # Get total count
        cursor.execute("""
            SELECT COUNT(*) FROM emails_fts WHERE emails_fts MATCH ?
        """, (query,))
        total = cursor.fetchone()[0]

        # Get matching results with relevance ranking
        cursor.execute("""
            SELECT 
                e.*,
                bm25(emails_fts) as score,
                snippet(emails_fts, 5, '<mark>', '</mark>', '...', 32) as snippet
            FROM emails_fts
            JOIN emails e ON emails_fts.message_id = e.message_id
            WHERE emails_fts MATCH ?
            ORDER BY bm25(emails_fts)
            LIMIT ? OFFSET ?
        """, (query, size, offset))

        rows = cursor.fetchall()

        results = []
        for row in rows:
            result = dict(row)
            # Parse JSON fields
            result["recipients_to"] = json.loads(result["recipients_to"] or "[]")
            result["recipients_cc"] = json.loads(result["recipients_cc"] or "[]")
            result["recipients_bcc"] = json.loads(result["recipients_bcc"] or "[]")
            result["attachments"] = json.loads(result["attachments"] or "[]")
            result["has_attachments"] = bool(result["has_attachments"])
            results.append(result)

        return {
            "total": total,
            "results": results,
        }

    def get_email_by_id(self, message_id: str) -> Optional[dict[str, Any]]:
        """
        Get a specific email by its message ID.

        Args:
            message_id: The message ID to retrieve.

        Returns:
            Email document or None if not found.
        """
        if not self._conn:
            raise DatabaseError("Database not connected")

        cursor = self._conn.cursor()
        cursor.execute("SELECT * FROM emails WHERE message_id = ?", (message_id,))
        row = cursor.fetchone()

        if row is None:
            return None

        result = dict(row)
        result["recipients_to"] = json.loads(result["recipients_to"] or "[]")
        result["recipients_cc"] = json.loads(result["recipients_cc"] or "[]")
        result["recipients_bcc"] = json.loads(result["recipients_bcc"] or "[]")
        result["attachments"] = json.loads(result["attachments"] or "[]")
        result["has_attachments"] = bool(result["has_attachments"])

        return result

    def get_stats(self) -> dict[str, Any]:
        """
        Get database statistics.

        Returns:
            Dictionary with database stats.
        """
        if not self._conn:
            raise DatabaseError("Database not connected")

        cursor = self._conn.cursor()

        # Get document count
        cursor.execute("SELECT COUNT(*) FROM emails")
        doc_count = cursor.fetchone()[0]

        # Get database file size
        db_size = os.path.getsize(self.db_path) if os.path.exists(self.db_path) else 0

        return {
            "document_count": doc_count,
            "database_size": db_size,
            "database_path": os.path.abspath(self.db_path),
        }
