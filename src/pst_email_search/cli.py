"""Command-line interface for PST Email Search."""

import json
import os
import sys
from typing import Optional

import click
from tqdm import tqdm

from .database import EmailDatabase, DatabaseError
from .olm_parser import OLMParser, OLMParserError
from .pst_parser import PSTParser, PSTParserError


@click.group()
@click.version_option(version="0.2.0")
def main() -> None:
    """Email Search - Parse Outlook PST/OLM files and search with SQLite."""
    pass


@main.command()
@click.argument("archive_files", nargs=-1, required=True, type=click.Path(exists=True))
@click.option("--database", "-d", default="emails.db", help="SQLite database file path")
@click.option("--batch-size", default=500, type=int, help="Batch size for bulk indexing")
@click.option("--recreate", is_flag=True, help="Delete and recreate the database")
def index(
    archive_files: tuple[str, ...],
    database: str,
    batch_size: int,
    recreate: bool,
) -> None:
    """
    Parse PST/OLM files and index emails to SQLite database.

    ARCHIVE_FILES: One or more PST or OLM files to parse and index.
    """
    try:
        db = EmailDatabase(database)
        if recreate:
            db.recreate_database()
            click.echo(f"Recreated database: {database}")
        else:
            db.connect()
            click.echo(f"Using database: {database}")
    except DatabaseError as e:
        click.echo(f"Error: Failed to open database: {e}", err=True)
        sys.exit(1)

    total_success = 0
    total_errors = 0

    for archive_path in archive_files:
        archive_path = os.path.abspath(archive_path)
        file_ext = os.path.splitext(archive_path)[1].lower()
        click.echo(f"\nProcessing: {archive_path}")

        try:
            if file_ext == ".olm":
                parser = OLMParser(archive_path)
            elif file_ext == ".pst":
                parser = PSTParser(archive_path)
            else:
                click.echo(f"Unsupported file type: {file_ext} (use .pst or .olm)", err=True)
                continue

            with parser:
                email_count = parser.get_email_count()
                click.echo(f"Found approximately {email_count} emails")

                with tqdm(total=email_count, desc="Indexing", unit="emails") as pbar:
                    def email_generator():  # type: ignore
                        for email in parser.parse_emails():
                            pbar.update(1)
                            yield email

                    success, errors = db.bulk_index_emails(
                        email_generator(),
                        batch_size=batch_size,
                    )

                total_success += success
                total_errors += errors
                click.echo(f"Indexed {success} emails, {errors} errors")

        except (PSTParserError, OLMParserError) as e:
            click.echo(f"Error parsing {archive_path}: {e}", err=True)
            continue

    click.echo(f"\nTotal: Indexed {total_success} emails, {total_errors} errors")
    click.echo(f"Database: {os.path.abspath(database)}")
    db.close()


@main.command()
@click.argument("query")
@click.option("--database", "-d", default="emails.db", help="SQLite database file path")
@click.option("--size", "-n", default=20, type=int, help="Number of results to return")
@click.option("--page", default=1, type=int, help="Page number (1-indexed)")
@click.option(
    "--format",
    "-f",
    "output_format",
    type=click.Choice(["table", "json", "detailed"]),
    default="table",
    help="Output format",
)
def search(
    query: str,
    database: str,
    size: int,
    page: int,
    output_format: str,
) -> None:
    """
    Search indexed emails by keyword.

    QUERY: Search query string.
    """
    if not os.path.exists(database):
        click.echo(f"Error: Database not found: {database}", err=True)
        click.echo("Run 'pst-search index' first to create the database.", err=True)
        sys.exit(1)

    try:
        db = EmailDatabase(database)
        db.connect()
    except DatabaseError as e:
        click.echo(f"Error: Failed to open database: {e}", err=True)
        sys.exit(1)

    # Calculate offset for pagination
    offset = (page - 1) * size

    try:
        results = db.search(query=query, size=size, offset=offset)
    except DatabaseError as e:
        click.echo(f"Error: Search failed: {e}", err=True)
        sys.exit(1)

    total = results["total"]
    documents = results["results"]

    click.echo(f"Found {total} results (showing page {page}, {len(documents)} results)\n")

    if output_format == "json":
        # JSON output
        click.echo(json.dumps(documents, indent=2, default=str))

    elif output_format == "detailed":
        # Detailed output
        for i, doc in enumerate(documents, 1):
            score = doc.get("score", 0)
            click.echo(f"--- Result {offset + i} (score: {abs(score):.2f}) ---")
            click.echo(f"Subject: {doc.get('subject', 'N/A')}")
            click.echo(f"From: {doc.get('sender', 'N/A')} <{doc.get('sender_email', '')}>")
            recipients = doc.get("recipients_to", [])
            if isinstance(recipients, list):
                click.echo(f"To: {', '.join(recipients)}")
            else:
                click.echo(f"To: {recipients}")
            click.echo(f"Date: {doc.get('date_sent', 'N/A')}")
            click.echo(f"Folder: {doc.get('folder_path', 'N/A')}")
            click.echo(f"PST File: {doc.get('pst_file', 'N/A')}")
            click.echo(f"Has Attachments: {doc.get('has_attachments', False)}")
            click.echo(f"Message ID: {doc.get('message_id', 'N/A')}")

            if doc.get("snippet"):
                click.echo(f"Snippet: ...{doc['snippet']}...")

            click.echo()

    else:
        # Table output (default)
        click.echo(f"{'#':<4} {'Date':<12} {'From':<25} {'Subject':<50}")
        click.echo("-" * 95)

        for i, doc in enumerate(documents, 1):
            date = doc.get("date_sent", "")[:10] if doc.get("date_sent") else "N/A"
            sender = (doc.get("sender") or "Unknown")[:24]
            subject = (doc.get("subject") or "No Subject")[:49]

            click.echo(f"{offset + i:<4} {date:<12} {sender:<25} {subject:<50}")

    db.close()


@main.command()
@click.option("--database", "-d", default="emails.db", help="SQLite database file path")
def stats(database: str) -> None:
    """Show statistics about the indexed emails."""
    if not os.path.exists(database):
        click.echo(f"Error: Database not found: {database}", err=True)
        sys.exit(1)

    try:
        db = EmailDatabase(database)
        db.connect()
    except DatabaseError as e:
        click.echo(f"Error: Failed to open database: {e}", err=True)
        sys.exit(1)

    try:
        db_stats = db.get_stats()
        doc_count = db_stats["document_count"]
        size_bytes = db_stats["database_size"]

        # Convert size to human readable
        if size_bytes > 1024 * 1024 * 1024:
            size_str = f"{size_bytes / (1024 * 1024 * 1024):.2f} GB"
        elif size_bytes > 1024 * 1024:
            size_str = f"{size_bytes / (1024 * 1024):.2f} MB"
        elif size_bytes > 1024:
            size_str = f"{size_bytes / 1024:.2f} KB"
        else:
            size_str = f"{size_bytes} bytes"

        click.echo(f"Database: {db_stats['database_path']}")
        click.echo(f"Email count: {doc_count:,}")
        click.echo(f"Database size: {size_str}")

    except DatabaseError as e:
        click.echo(f"Error: Failed to get stats: {e}", err=True)
        sys.exit(1)

    db.close()


@main.command()
@click.argument("message_id")
@click.option("--database", "-d", default="emails.db", help="SQLite database file path")
def show(message_id: str, database: str) -> None:
    """
    Show full details of a specific email by message ID.

    MESSAGE_ID: The message ID to retrieve.
    """
    if not os.path.exists(database):
        click.echo(f"Error: Database not found: {database}", err=True)
        sys.exit(1)

    try:
        db = EmailDatabase(database)
        db.connect()
    except DatabaseError as e:
        click.echo(f"Error: Failed to open database: {e}", err=True)
        sys.exit(1)

    email = db.get_email_by_id(message_id)
    if email is None:
        click.echo(f"Email not found: {message_id}", err=True)
        sys.exit(1)

    click.echo(json.dumps(email, indent=2, default=str))
    db.close()


@main.command()
@click.option("--database", "-d", default="emails.db", help="SQLite database file path")
@click.option("--yes", "-y", is_flag=True, help="Skip confirmation prompt")
def delete_db(database: str, yes: bool) -> None:
    """Delete the email database."""
    if not os.path.exists(database):
        click.echo(f"Database not found: {database}")
        return

    if not yes:
        if not click.confirm(f"Are you sure you want to delete '{database}'?"):
            click.echo("Aborted.")
            return

    try:
        db = EmailDatabase(database)
        db.delete_database()
        click.echo(f"Deleted database: {database}")
    except Exception as e:
        click.echo(f"Error: Failed to delete database: {e}", err=True)
        sys.exit(1)


if __name__ == "__main__":
    main()
