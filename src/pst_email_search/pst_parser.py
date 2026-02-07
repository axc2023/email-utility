"""PST file parser using libpst (readpst command-line tool)."""

import email
import hashlib
import mailbox
import os
import shutil
import subprocess
import tempfile
from datetime import datetime
from email.utils import parsedate_to_datetime
from typing import Generator, Optional

from .models import EmailAttachment, EmailMessage


class PSTParserError(Exception):
    """Exception raised for PST parsing errors."""

    pass


def _find_readpst() -> Optional[str]:
    """Find the readpst executable path."""
    import shutil as sh

    readpst_path = sh.which("readpst")
    if readpst_path:
        return readpst_path

    common_paths = [
        "/opt/homebrew/bin/readpst",
        "/usr/local/bin/readpst",
        "/usr/bin/readpst",
    ]
    for path in common_paths:
        if os.path.isfile(path) and os.access(path, os.X_OK):
            return path

    return None


class PSTParser:
    """Parser for Outlook PST files using libpst (readpst)."""

    def __init__(self, pst_path: str):
        """
        Initialize the PST parser.

        Args:
            pst_path: Path to the PST file to parse.

        Raises:
            PSTParserError: If readpst is not installed or file doesn't exist.
        """
        self._readpst_path = _find_readpst()
        if not self._readpst_path:
            raise PSTParserError(
                "readpst (libpst) is not installed. "
                "On macOS, install with: brew install libpst\n"
                "On Linux (Ubuntu/Debian): sudo apt-get install pst-utils"
            )

        if not os.path.exists(pst_path):
            raise PSTParserError(f"PST file not found: {pst_path}")

        self.pst_path = os.path.abspath(pst_path)
        self.pst_filename = os.path.basename(pst_path)
        self._temp_dir: Optional[str] = None
        self._extracted = False

    def __enter__(self) -> "PSTParser":
        """Open the PST file (extract to temp directory)."""
        self.open()
        return self

    def __exit__(self, exc_type, exc_val, exc_tb) -> None:  # type: ignore
        """Close the PST file (cleanup temp directory)."""
        self.close()

    def open(self) -> None:
        """Extract the PST file to a temporary directory."""
        if self._extracted:
            return

        self._temp_dir = tempfile.mkdtemp(prefix="pst_extract_")

        try:
            result = subprocess.run(
                [
                    self._readpst_path,
                    "-r",
                    "-o",
                    self._temp_dir,
                    self.pst_path,
                ],
                capture_output=True,
                text=True,
                timeout=3600,
            )

            if result.returncode != 0:
                error_msg = result.stderr or result.stdout or "Unknown error"
                raise PSTParserError(f"readpst failed: {error_msg}")

            self._extracted = True

        except subprocess.TimeoutExpired:
            self.close()
            raise PSTParserError("PST extraction timed out (exceeded 1 hour)")
        except subprocess.SubprocessError as e:
            self.close()
            raise PSTParserError(f"Failed to run readpst: {e}") from e

    def close(self) -> None:
        """Clean up the temporary directory."""
        if self._temp_dir and os.path.exists(self._temp_dir):
            try:
                shutil.rmtree(self._temp_dir)
            except Exception:
                pass
        self._temp_dir = None
        self._extracted = False

    def _generate_message_id(
        self, subject: str, sender: str, date_str: str, folder_path: str
    ) -> str:
        """Generate a unique message ID based on message properties."""
        id_components = [
            self.pst_filename,
            folder_path,
            subject,
            sender,
            date_str,
        ]
        id_string = "|".join(id_components)
        return hashlib.sha256(id_string.encode("utf-8")).hexdigest()[:32]

    def _parse_datetime(self, date_str: Optional[str]) -> Optional[datetime]:
        """Parse datetime from email date header."""
        if not date_str:
            return None
        try:
            return parsedate_to_datetime(date_str)
        except Exception:
            return None

    def _decode_header(self, header_value: Optional[str]) -> str:
        """Decode an email header value."""
        if not header_value:
            return ""
        try:
            decoded_parts = email.header.decode_header(header_value)
            result_parts = []
            for part, charset in decoded_parts:
                if isinstance(part, bytes):
                    result_parts.append(part.decode(charset or "utf-8", errors="replace"))
                else:
                    result_parts.append(part)
            return " ".join(result_parts)
        except Exception:
            return str(header_value)

    def _extract_recipients(
        self, msg: email.message.Message
    ) -> tuple[list[str], list[str], list[str]]:
        """Extract recipients from an email message."""
        to_list: list[str] = []
        cc_list: list[str] = []
        bcc_list: list[str] = []

        to_header = msg.get("To", "")
        if to_header:
            to_list = [self._decode_header(addr.strip()) for addr in to_header.split(",")]

        cc_header = msg.get("Cc", "")
        if cc_header:
            cc_list = [self._decode_header(addr.strip()) for addr in cc_header.split(",")]

        bcc_header = msg.get("Bcc", "")
        if bcc_header:
            bcc_list = [self._decode_header(addr.strip()) for addr in bcc_header.split(",")]

        return to_list, cc_list, bcc_list

    def _extract_body(self, msg: email.message.Message) -> tuple[str, str]:
        """Extract plain text and HTML body from an email message."""
        body_text = ""
        body_html = ""

        if msg.is_multipart():
            for part in msg.walk():
                content_type = part.get_content_type()
                content_disposition = str(part.get("Content-Disposition", ""))

                if "attachment" in content_disposition:
                    continue

                try:
                    payload = part.get_payload(decode=True)
                    if payload is None:
                        continue

                    charset = part.get_content_charset() or "utf-8"
                    text = payload.decode(charset, errors="replace")

                    if content_type == "text/plain" and not body_text:
                        body_text = text
                    elif content_type == "text/html" and not body_html:
                        body_html = text
                except Exception:
                    continue
        else:
            try:
                payload = msg.get_payload(decode=True)
                if payload:
                    charset = msg.get_content_charset() or "utf-8"
                    text = payload.decode(charset, errors="replace")
                    content_type = msg.get_content_type()
                    if content_type == "text/html":
                        body_html = text
                    else:
                        body_text = text
            except Exception:
                pass

        return body_text, body_html

    def _extract_attachments(self, msg: email.message.Message) -> list[EmailAttachment]:
        """Extract attachment metadata from an email message."""
        attachments: list[EmailAttachment] = []

        if not msg.is_multipart():
            return attachments

        for part in msg.walk():
            content_disposition = str(part.get("Content-Disposition", ""))

            if "attachment" in content_disposition:
                filename = part.get_filename()
                if filename:
                    filename = self._decode_header(filename)
                else:
                    filename = "unnamed_attachment"

                try:
                    payload = part.get_payload(decode=True)
                    size = len(payload) if payload else 0
                except Exception:
                    size = 0

                content_type = part.get_content_type()

                attachments.append(
                    EmailAttachment(
                        filename=filename,
                        size=size,
                        content_type=content_type,
                    )
                )

        return attachments

    def _parse_email_message(
        self, msg: email.message.Message, folder_path: str
    ) -> Optional[EmailMessage]:
        """Parse an email message into an EmailMessage object."""
        try:
            subject = self._decode_header(msg.get("Subject", ""))
            from_header = self._decode_header(msg.get("From", ""))

            sender_name = from_header
            sender_email = ""
            if "<" in from_header and ">" in from_header:
                parts = from_header.split("<")
                sender_name = parts[0].strip().strip('"')
                sender_email = parts[1].rstrip(">").strip()
            elif "@" in from_header:
                sender_email = from_header
                sender_name = from_header.split("@")[0]

            date_str = msg.get("Date", "")
            date_sent = self._parse_datetime(date_str)
            date_received = self._parse_datetime(msg.get("Received", ""))

            to_list, cc_list, bcc_list = self._extract_recipients(msg)
            body_text, body_html = self._extract_body(msg)
            attachments = self._extract_attachments(msg)

            importance = "normal"
            priority = msg.get("X-Priority", "") or msg.get("Importance", "")
            if priority:
                priority_lower = priority.lower()
                if "1" in priority_lower or "high" in priority_lower:
                    importance = "high"
                elif "5" in priority_lower or "low" in priority_lower:
                    importance = "low"

            message_id = self._generate_message_id(subject, from_header, date_str, folder_path)

            return EmailMessage(
                message_id=message_id,
                subject=subject,
                sender=sender_name,
                sender_email=sender_email,
                recipients_to=to_list,
                recipients_cc=cc_list,
                recipients_bcc=bcc_list,
                date_sent=date_sent,
                date_received=date_received,
                body_text=body_text,
                body_html=body_html,
                folder_path=folder_path,
                attachments=attachments,
                has_attachments=len(attachments) > 0,
                importance=importance,
                pst_file=self.pst_filename,
            )

        except Exception as e:
            print(f"Warning: Failed to parse email: {e}")
            return None

    def _parse_mbox_file(
        self, mbox_path: str, folder_path: str
    ) -> Generator[EmailMessage, None, None]:
        """Parse an mbox file and yield EmailMessage objects."""
        try:
            mbox = mailbox.mbox(mbox_path)
            for msg in mbox:
                email_msg = self._parse_email_message(msg, folder_path)
                if email_msg is not None:
                    yield email_msg
            mbox.close()
        except Exception as e:
            print(f"Warning: Failed to parse mbox {mbox_path}: {e}")

    def _process_directory(
        self, dir_path: str, folder_path: str = ""
    ) -> Generator[EmailMessage, None, None]:
        """Recursively process a directory of extracted emails."""
        try:
            dir_name = os.path.basename(dir_path)
            current_path = f"{folder_path}/{dir_name}" if folder_path else dir_name

            for item in sorted(os.listdir(dir_path)):
                item_path = os.path.join(dir_path, item)

                if os.path.isfile(item_path):
                    if item == "mbox" or item.endswith(".mbox"):
                        yield from self._parse_mbox_file(item_path, current_path)
                    elif item.lower().endswith(".eml"):
                        with open(item_path, "rb") as f:
                            msg = email.message_from_binary_file(f)
                        email_msg = self._parse_email_message(msg, current_path)
                        if email_msg is not None:
                            yield email_msg

                elif os.path.isdir(item_path):
                    yield from self._process_directory(item_path, current_path)

        except Exception as e:
            print(f"Warning: Failed to process directory {dir_path}: {e}")

    def parse_emails(self) -> Generator[EmailMessage, None, None]:
        """
        Parse all emails from the PST file.

        Yields:
            EmailMessage objects for each email in the PST file.

        Raises:
            PSTParserError: If the PST file is not extracted.
        """
        if not self._extracted or not self._temp_dir:
            raise PSTParserError("PST file is not open. Call open() first.")

        for item in sorted(os.listdir(self._temp_dir)):
            item_path = os.path.join(self._temp_dir, item)
            if os.path.isdir(item_path):
                yield from self._process_directory(item_path)
            elif os.path.isfile(item_path):
                if item == "mbox" or item.endswith(".mbox"):
                    yield from self._parse_mbox_file(item_path, "")
                elif item.lower().endswith(".eml"):
                    with open(item_path, "rb") as f:
                        msg = email.message_from_binary_file(f)
                    email_msg = self._parse_email_message(msg, "")
                    if email_msg is not None:
                        yield email_msg

    def get_email_count(self) -> int:
        """
        Get an estimate of the total number of emails in the PST file.

        Returns:
            Estimated number of emails.
        """
        if not self._extracted or not self._temp_dir:
            raise PSTParserError("PST file is not open. Call open() first.")

        count = 0
        for root, _dirs, files in os.walk(self._temp_dir):
            for f in files:
                file_path = os.path.join(root, f)
                if f == "mbox" or f.endswith(".mbox"):
                    try:
                        mbox = mailbox.mbox(file_path)
                        count += len(mbox)
                        mbox.close()
                    except Exception:
                        pass
                elif f.lower().endswith(".eml"):
                    count += 1

        return count
