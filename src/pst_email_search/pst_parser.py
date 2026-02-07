"""PST file parser using libpff (pypff) library."""

import hashlib
import os
from datetime import datetime
from typing import Generator, Optional

from .models import EmailAttachment, EmailMessage

# Try to import pypff, provide helpful error if not available
try:
    import pypff
except ImportError:
    pypff = None  # type: ignore


class PSTParserError(Exception):
    """Exception raised for PST parsing errors."""

    pass


class PSTParser:
    """Parser for Outlook PST files using libpff."""

    def __init__(self, pst_path: str):
        """
        Initialize the PST parser.

        Args:
            pst_path: Path to the PST file to parse.

        Raises:
            PSTParserError: If pypff is not installed or file doesn't exist.
        """
        if pypff is None:
            raise PSTParserError(
                "pypff (libpff-python) is not installed. "
                "On macOS, install with: brew install libpff && pip install libpff-python"
            )

        if not os.path.exists(pst_path):
            raise PSTParserError(f"PST file not found: {pst_path}")

        self.pst_path = pst_path
        self.pst_filename = os.path.basename(pst_path)
        self._pff_file: Optional[pypff.file] = None

    def __enter__(self) -> "PSTParser":
        """Open the PST file."""
        self.open()
        return self

    def __exit__(self, exc_type, exc_val, exc_tb) -> None:  # type: ignore
        """Close the PST file."""
        self.close()

    def open(self) -> None:
        """Open the PST file for reading."""
        if pypff is None:
            raise PSTParserError("pypff is not available")

        try:
            self._pff_file = pypff.file()
            self._pff_file.open(self.pst_path)
        except Exception as e:
            raise PSTParserError(f"Failed to open PST file: {e}") from e

    def close(self) -> None:
        """Close the PST file."""
        if self._pff_file is not None:
            try:
                self._pff_file.close()
            except Exception:
                pass
            self._pff_file = None

    def _generate_message_id(self, message: "pypff.message", folder_path: str) -> str:
        """Generate a unique message ID based on message properties."""
        # Create a hash from available message properties
        id_components = [
            self.pst_filename,
            folder_path,
            str(message.subject or ""),
            str(message.sender_name or ""),
            str(message.delivery_time or ""),
        ]
        id_string = "|".join(id_components)
        return hashlib.sha256(id_string.encode("utf-8")).hexdigest()[:32]

    def _parse_datetime(self, dt_value: Optional[datetime]) -> Optional[datetime]:
        """Parse datetime from pypff, handling None values."""
        if dt_value is None:
            return None
        try:
            # pypff returns datetime objects directly
            if isinstance(dt_value, datetime):
                return dt_value
            return None
        except Exception:
            return None

    def _extract_recipients(self, message: "pypff.message") -> tuple[list[str], list[str], list[str]]:
        """Extract recipients from a message."""
        to_list: list[str] = []
        cc_list: list[str] = []
        bcc_list: list[str] = []

        try:
            num_recipients = message.number_of_recipients
            for i in range(num_recipients):
                recipient = message.get_recipient(i)
                if recipient is None:
                    continue

                # Get recipient name and email
                name = recipient.name or ""
                email = recipient.email_address or ""
                recipient_str = f"{name} <{email}>" if name and email else (name or email)

                # Get recipient type (TO, CC, BCC)
                recipient_type = getattr(recipient, "type", 1)
                if recipient_type == 1:  # TO
                    to_list.append(recipient_str)
                elif recipient_type == 2:  # CC
                    cc_list.append(recipient_str)
                elif recipient_type == 3:  # BCC
                    bcc_list.append(recipient_str)
                else:
                    to_list.append(recipient_str)
        except Exception:
            pass

        return to_list, cc_list, bcc_list

    def _extract_attachments(self, message: "pypff.message") -> list[EmailAttachment]:
        """Extract attachment metadata from a message."""
        attachments: list[EmailAttachment] = []

        try:
            num_attachments = message.number_of_attachments
            for i in range(num_attachments):
                attachment = message.get_attachment(i)
                if attachment is None:
                    continue

                filename = getattr(attachment, "name", None) or f"attachment_{i}"
                size = getattr(attachment, "size", 0) or 0
                content_type = getattr(attachment, "content_type", None)

                attachments.append(
                    EmailAttachment(
                        filename=filename,
                        size=size,
                        content_type=content_type,
                    )
                )
        except Exception:
            pass

        return attachments

    def _parse_message(self, message: "pypff.message", folder_path: str) -> Optional[EmailMessage]:
        """Parse a single message into an EmailMessage object."""
        try:
            # Extract basic properties
            subject = message.subject or ""
            sender_name = message.sender_name or ""
            sender_email = getattr(message, "sender_email_address", "") or ""

            # Extract body
            body_text = ""
            body_html = ""
            try:
                body_text = message.plain_text_body or ""
            except Exception:
                pass
            try:
                body_html = message.html_body or ""
            except Exception:
                pass

            # Extract dates
            date_sent = self._parse_datetime(getattr(message, "client_submit_time", None))
            date_received = self._parse_datetime(getattr(message, "delivery_time", None))

            # Extract recipients
            to_list, cc_list, bcc_list = self._extract_recipients(message)

            # Extract attachments
            attachments = self._extract_attachments(message)

            # Get importance
            importance_value = getattr(message, "importance", 1)
            importance_map = {0: "low", 1: "normal", 2: "high"}
            importance = importance_map.get(importance_value, "normal")

            # Generate message ID
            message_id = self._generate_message_id(message, folder_path)

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
            # Log error but continue processing
            print(f"Warning: Failed to parse message in {folder_path}: {e}")
            return None

    def _process_folder(
        self, folder: "pypff.folder", folder_path: str = ""
    ) -> Generator[EmailMessage, None, None]:
        """Recursively process a folder and its subfolders."""
        try:
            folder_name = folder.name or "Unknown"
            current_path = f"{folder_path}/{folder_name}" if folder_path else folder_name

            # Process messages in this folder
            num_messages = folder.number_of_sub_messages
            for i in range(num_messages):
                try:
                    message = folder.get_sub_message(i)
                    if message is not None:
                        email = self._parse_message(message, current_path)
                        if email is not None:
                            yield email
                except Exception as e:
                    print(f"Warning: Failed to process message {i} in {current_path}: {e}")

            # Process subfolders recursively
            num_subfolders = folder.number_of_sub_folders
            for i in range(num_subfolders):
                try:
                    subfolder = folder.get_sub_folder(i)
                    if subfolder is not None:
                        yield from self._process_folder(subfolder, current_path)
                except Exception as e:
                    print(f"Warning: Failed to process subfolder {i} in {current_path}: {e}")

        except Exception as e:
            print(f"Warning: Failed to process folder {folder_path}: {e}")

    def parse_emails(self) -> Generator[EmailMessage, None, None]:
        """
        Parse all emails from the PST file.

        Yields:
            EmailMessage objects for each email in the PST file.

        Raises:
            PSTParserError: If the PST file is not open.
        """
        if self._pff_file is None:
            raise PSTParserError("PST file is not open. Call open() first.")

        try:
            root_folder = self._pff_file.get_root_folder()
            if root_folder is not None:
                yield from self._process_folder(root_folder)
        except Exception as e:
            raise PSTParserError(f"Failed to parse PST file: {e}") from e

    def get_email_count(self) -> int:
        """
        Get an estimate of the total number of emails in the PST file.

        Returns:
            Estimated number of emails.
        """
        if self._pff_file is None:
            raise PSTParserError("PST file is not open. Call open() first.")

        count = 0

        def count_messages(folder: "pypff.folder") -> int:
            nonlocal count
            try:
                count += folder.number_of_sub_messages
                for i in range(folder.number_of_sub_folders):
                    subfolder = folder.get_sub_folder(i)
                    if subfolder is not None:
                        count_messages(subfolder)
            except Exception:
                pass
            return count

        try:
            root_folder = self._pff_file.get_root_folder()
            if root_folder is not None:
                count_messages(root_folder)
        except Exception:
            pass

        return count
