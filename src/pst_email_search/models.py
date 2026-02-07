"""Data models for email messages."""

from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional


@dataclass
class EmailAttachment:
    """Represents an email attachment."""

    filename: str
    size: int
    content_type: Optional[str] = None


@dataclass
class EmailMessage:
    """Represents an email message extracted from a PST file."""

    message_id: str
    subject: str
    sender: str
    sender_email: str
    recipients_to: list[str] = field(default_factory=list)
    recipients_cc: list[str] = field(default_factory=list)
    recipients_bcc: list[str] = field(default_factory=list)
    date_sent: Optional[datetime] = None
    date_received: Optional[datetime] = None
    body_text: str = ""
    body_html: str = ""
    folder_path: str = ""
    attachments: list[EmailAttachment] = field(default_factory=list)
    has_attachments: bool = False
    importance: str = "normal"
    pst_file: str = ""

    def to_dict(self) -> dict:
        """Convert to dictionary for Elasticsearch indexing."""
        return {
            "message_id": self.message_id,
            "subject": self.subject,
            "sender": self.sender,
            "sender_email": self.sender_email,
            "recipients_to": self.recipients_to,
            "recipients_cc": self.recipients_cc,
            "recipients_bcc": self.recipients_bcc,
            "date_sent": self.date_sent.isoformat() if self.date_sent else None,
            "date_received": self.date_received.isoformat() if self.date_received else None,
            "body_text": self.body_text,
            "body_html": self.body_html,
            "folder_path": self.folder_path,
            "attachments": [
                {
                    "filename": att.filename,
                    "size": att.size,
                    "content_type": att.content_type,
                }
                for att in self.attachments
            ],
            "has_attachments": self.has_attachments,
            "importance": self.importance,
            "pst_file": self.pst_file,
        }
