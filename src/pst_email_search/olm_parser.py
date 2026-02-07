"""OLM file parser for Outlook Mac archives."""

import hashlib
import os
import shutil
import tempfile
import zipfile
import xml.etree.ElementTree as ET
from datetime import datetime
from typing import Generator, Optional

from .models import EmailAttachment, EmailMessage


class OLMParserError(Exception):
    """Exception raised for OLM parsing errors."""

    pass


class OLMParser:
    """Parser for Outlook Mac OLM archive files."""

    def __init__(self, olm_path: str):
        """
        Initialize the OLM parser.

        Args:
            olm_path: Path to the OLM file to parse.

        Raises:
            OLMParserError: If file doesn't exist or is not a valid OLM file.
        """
        if not os.path.exists(olm_path):
            raise OLMParserError(f"OLM file not found: {olm_path}")

        if not zipfile.is_zipfile(olm_path):
            raise OLMParserError(f"Not a valid OLM file (not a ZIP archive): {olm_path}")

        self.olm_path = os.path.abspath(olm_path)
        self.olm_filename = os.path.basename(olm_path)
        self._temp_dir: Optional[str] = None
        self._extracted = False

    def __enter__(self) -> "OLMParser":
        """Open the OLM file (extract to temp directory)."""
        self.open()
        return self

    def __exit__(self, exc_type, exc_val, exc_tb) -> None:  # type: ignore
        """Close the OLM file (cleanup temp directory)."""
        self.close()

    def open(self) -> None:
        """Extract the OLM file to a temporary directory."""
        if self._extracted:
            return

        self._temp_dir = tempfile.mkdtemp(prefix="olm_extract_")

        try:
            with zipfile.ZipFile(self.olm_path, "r") as zf:
                zf.extractall(self._temp_dir)
            self._extracted = True
        except zipfile.BadZipFile as e:
            self.close()
            raise OLMParserError(f"Failed to extract OLM file: {e}") from e
        except Exception as e:
            self.close()
            raise OLMParserError(f"Failed to extract OLM file: {e}") from e

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
            self.olm_filename,
            folder_path,
            subject,
            sender,
            date_str,
        ]
        id_string = "|".join(id_components)
        return hashlib.sha256(id_string.encode("utf-8")).hexdigest()[:32]

    def _parse_datetime(self, date_str: Optional[str]) -> Optional[datetime]:
        """Parse datetime from OLM date string."""
        if not date_str:
            return None
        try:
            date_str = date_str.strip()
            for fmt in [
                "%Y-%m-%dT%H:%M:%S",
                "%Y-%m-%dT%H:%M:%SZ",
                "%Y-%m-%d %H:%M:%S",
                "%a, %d %b %Y %H:%M:%S %z",
                "%a, %d %b %Y %H:%M:%S",
            ]:
                try:
                    return datetime.strptime(date_str[:19], fmt[:19].replace("%z", ""))
                except ValueError:
                    continue
            return None
        except Exception:
            return None

    def _get_text(self, element: Optional[ET.Element], default: str = "") -> str:
        """Safely get text content from an XML element."""
        if element is None:
            return default
        return element.text or default

    def _parse_email_addresses(self, addresses_elem: Optional[ET.Element]) -> list[str]:
        """Parse email addresses from an OLM emailAddresses element."""
        if addresses_elem is None:
            return []

        addresses = []
        for addr_elem in addresses_elem.findall(".//emailAddress"):
            name = self._get_text(addr_elem.find("OPFContactEmailAddressName"))
            email_addr = self._get_text(addr_elem.find("OPFContactEmailAddressAddress"))

            if email_addr:
                if name and name != email_addr:
                    addresses.append(f"{name} <{email_addr}>")
                else:
                    addresses.append(email_addr)

        return addresses

    def _parse_attachments(self, attachments_elem: Optional[ET.Element]) -> list[EmailAttachment]:
        """Parse attachments from an OLM messageAttachment element."""
        if attachments_elem is None:
            return []

        attachments = []
        for att_elem in attachments_elem.findall(".//messageAttachment"):
            filename = self._get_text(att_elem.find("OPFAttachmentName"), "unnamed_attachment")
            content_type = self._get_text(att_elem.find("OPFAttachmentContentType"), "application/octet-stream")

            size_str = self._get_text(att_elem.find("OPFAttachmentContentFileSize"), "0")
            try:
                size = int(size_str)
            except ValueError:
                size = 0

            attachments.append(
                EmailAttachment(
                    filename=filename,
                    size=size,
                    content_type=content_type,
                )
            )

        return attachments

    def _parse_email_xml(self, xml_path: str, folder_path: str) -> Optional[EmailMessage]:
        """Parse an OLM email XML file into an EmailMessage object."""
        try:
            tree = ET.parse(xml_path)
            root = tree.getroot()

            email_elem = root.find(".//email")
            if email_elem is None:
                email_elem = root

            subject = self._get_text(email_elem.find(".//OPFMessageCopySubject"))
            
            sender_elem = email_elem.find(".//OPFMessageCopySenderAddress")
            sender_name = ""
            sender_email = ""
            if sender_elem is not None:
                sender_name = self._get_text(sender_elem.find("OPFContactEmailAddressName"))
                sender_email = self._get_text(sender_elem.find("OPFContactEmailAddressAddress"))
            
            if not sender_name:
                sender_name = self._get_text(email_elem.find(".//OPFMessageCopyFromAddresses"))
            if not sender_email and sender_name and "@" in sender_name:
                sender_email = sender_name

            date_sent_str = self._get_text(email_elem.find(".//OPFMessageCopySentTime"))
            date_sent = self._parse_datetime(date_sent_str)

            date_received_str = self._get_text(email_elem.find(".//OPFMessageCopyReceivedTime"))
            date_received = self._parse_datetime(date_received_str)

            to_elem = email_elem.find(".//OPFMessageCopyToAddresses")
            to_list = self._parse_email_addresses(to_elem)

            cc_elem = email_elem.find(".//OPFMessageCopyCCAddresses")
            cc_list = self._parse_email_addresses(cc_elem)

            bcc_elem = email_elem.find(".//OPFMessageCopyBCCAddresses")
            bcc_list = self._parse_email_addresses(bcc_elem)

            body_text = self._get_text(email_elem.find(".//OPFMessageCopyBody"))
            body_html = self._get_text(email_elem.find(".//OPFMessageCopyHTMLBody"))

            attachments_elem = email_elem.find(".//OPFMessageCopyAttachmentList")
            attachments = self._parse_attachments(attachments_elem)

            importance = "normal"
            priority_str = self._get_text(email_elem.find(".//OPFMessageCopyPriority"), "0")
            try:
                priority = int(priority_str)
                if priority == 1:
                    importance = "high"
                elif priority == -1:
                    importance = "low"
            except ValueError:
                pass

            message_id = self._generate_message_id(
                subject, sender_email or sender_name, date_sent_str, folder_path
            )

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
                pst_file=self.olm_filename,
            )

        except ET.ParseError as e:
            print(f"Warning: Failed to parse XML {xml_path}: {e}")
            return None
        except Exception as e:
            print(f"Warning: Failed to parse email {xml_path}: {e}")
            return None

    def _process_directory(
        self, dir_path: str, folder_path: str = ""
    ) -> Generator[EmailMessage, None, None]:
        """Recursively process a directory of extracted emails."""
        try:
            dir_name = os.path.basename(dir_path)
            
            if dir_name.startswith(".") or dir_name == "__MACOSX":
                return

            if dir_name.endswith(".olk15Message") or dir_name.endswith(".olk14Message"):
                xml_files = [f for f in os.listdir(dir_path) if f.endswith(".xml")]
                for xml_file in xml_files:
                    xml_path = os.path.join(dir_path, xml_file)
                    email_msg = self._parse_email_xml(xml_path, folder_path)
                    if email_msg is not None:
                        yield email_msg
                return

            current_path = f"{folder_path}/{dir_name}" if folder_path else dir_name

            for item in sorted(os.listdir(dir_path)):
                item_path = os.path.join(dir_path, item)

                if os.path.isfile(item_path):
                    if item.endswith(".xml") and not item.startswith("."):
                        email_msg = self._parse_email_xml(item_path, current_path)
                        if email_msg is not None:
                            yield email_msg

                elif os.path.isdir(item_path):
                    yield from self._process_directory(item_path, current_path)

        except Exception as e:
            print(f"Warning: Failed to process directory {dir_path}: {e}")

    def parse_emails(self) -> Generator[EmailMessage, None, None]:
        """
        Parse all emails from the OLM file.

        Yields:
            EmailMessage objects for each email in the OLM file.

        Raises:
            OLMParserError: If the OLM file is not extracted.
        """
        if not self._extracted or not self._temp_dir:
            raise OLMParserError("OLM file is not open. Call open() first.")

        for item in sorted(os.listdir(self._temp_dir)):
            item_path = os.path.join(self._temp_dir, item)
            if os.path.isdir(item_path):
                yield from self._process_directory(item_path)

    def get_email_count(self) -> int:
        """
        Get an estimate of the total number of emails in the OLM file.

        Returns:
            Estimated number of emails.
        """
        if not self._extracted or not self._temp_dir:
            raise OLMParserError("OLM file is not open. Call open() first.")

        count = 0
        for root, dirs, files in os.walk(self._temp_dir):
            dirs[:] = [d for d in dirs if not d.startswith(".") and d != "__MACOSX"]
            
            dir_name = os.path.basename(root)
            if dir_name.endswith(".olk15Message") or dir_name.endswith(".olk14Message"):
                count += 1
            else:
                for f in files:
                    if f.endswith(".xml") and not f.startswith("."):
                        if "message" in root.lower() or "mail" in root.lower():
                            count += 1

        return count
