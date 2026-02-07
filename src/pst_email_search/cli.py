"""Command-line interface for PST Email Search."""

import os
import sys
from typing import Optional

import click
from tqdm import tqdm

from .elasticsearch_client import EmailSearchClient, ElasticsearchClientError
from .pst_parser import PSTParser, PSTParserError


def get_es_client(
    host: str,
    port: int,
    username: Optional[str],
    password: Optional[str],
    index: str,
) -> EmailSearchClient:
    """Create and return an Elasticsearch client."""
    return EmailSearchClient(
        host=host,
        port=port,
        username=username,
        password=password,
        index_name=index,
    )


@click.group()
@click.version_option(version="0.1.0")
def main() -> None:
    """PST Email Search - Parse Outlook PST files and search with Elasticsearch."""
    pass


@main.command()
@click.argument("pst_files", nargs=-1, required=True, type=click.Path(exists=True))
@click.option("--host", default="localhost", help="Elasticsearch host")
@click.option("--port", default=9200, type=int, help="Elasticsearch port")
@click.option("--username", "-u", default=None, help="Elasticsearch username")
@click.option("--password", "-p", default=None, help="Elasticsearch password")
@click.option("--index", "-i", default="pst-emails", help="Elasticsearch index name")
@click.option("--batch-size", default=500, type=int, help="Batch size for bulk indexing")
@click.option("--recreate-index", is_flag=True, help="Delete and recreate the index")
def index(
    pst_files: tuple[str, ...],
    host: str,
    port: int,
    username: Optional[str],
    password: Optional[str],
    index: str,
    batch_size: int,
    recreate_index: bool,
) -> None:
    """
    Parse PST files and index emails to Elasticsearch.

    PST_FILES: One or more PST files to parse and index.
    """
    # Create Elasticsearch client
    try:
        es_client = get_es_client(host, port, username, password, index)
    except ElasticsearchClientError as e:
        click.echo(f"Error: Failed to connect to Elasticsearch: {e}", err=True)
        sys.exit(1)

    # Check connection
    if not es_client.ping():
        click.echo(
            f"Error: Cannot connect to Elasticsearch at {host}:{port}. "
            "Make sure Elasticsearch is running.",
            err=True,
        )
        sys.exit(1)

    click.echo(f"Connected to Elasticsearch at {host}:{port}")

    # Create or recreate index
    try:
        es_client.create_index(delete_existing=recreate_index)
        if recreate_index:
            click.echo(f"Recreated index: {index}")
        else:
            click.echo(f"Using index: {index}")
    except ElasticsearchClientError as e:
        click.echo(f"Error: Failed to create index: {e}", err=True)
        sys.exit(1)

    total_success = 0
    total_errors = 0

    # Process each PST file
    for pst_path in pst_files:
        pst_path = os.path.abspath(pst_path)
        click.echo(f"\nProcessing: {pst_path}")

        try:
            with PSTParser(pst_path) as parser:
                # Get email count for progress bar
                email_count = parser.get_email_count()
                click.echo(f"Found approximately {email_count} emails")

                # Create progress bar
                with tqdm(total=email_count, desc="Indexing", unit="emails") as pbar:
                    # Create a generator that updates progress
                    def email_generator():  # type: ignore
                        for email in parser.parse_emails():
                            pbar.update(1)
                            yield email

                    # Bulk index emails
                    success, errors = es_client.bulk_index_emails(
                        email_generator(),
                        batch_size=batch_size,
                    )

                total_success += success
                total_errors += errors
                click.echo(f"Indexed {success} emails, {errors} errors")

        except PSTParserError as e:
            click.echo(f"Error parsing {pst_path}: {e}", err=True)
            continue

    # Refresh index to make documents searchable
    es_client.refresh_index()

    click.echo(f"\nTotal: Indexed {total_success} emails, {total_errors} errors")
    es_client.close()


@main.command()
@click.argument("query")
@click.option("--host", default="localhost", help="Elasticsearch host")
@click.option("--port", default=9200, type=int, help="Elasticsearch port")
@click.option("--username", "-u", default=None, help="Elasticsearch username")
@click.option("--password", "-p", default=None, help="Elasticsearch password")
@click.option("--index", "-i", default="pst-emails", help="Elasticsearch index name")
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
    host: str,
    port: int,
    username: Optional[str],
    password: Optional[str],
    index: str,
    size: int,
    page: int,
    output_format: str,
) -> None:
    """
    Search indexed emails by keyword.

    QUERY: Search query string.
    """
    import json

    # Create Elasticsearch client
    try:
        es_client = get_es_client(host, port, username, password, index)
    except ElasticsearchClientError as e:
        click.echo(f"Error: Failed to connect to Elasticsearch: {e}", err=True)
        sys.exit(1)

    if not es_client.ping():
        click.echo(
            f"Error: Cannot connect to Elasticsearch at {host}:{port}",
            err=True,
        )
        sys.exit(1)

    # Calculate offset for pagination
    from_ = (page - 1) * size

    try:
        results = es_client.search(query=query, size=size, from_=from_)
    except ElasticsearchClientError as e:
        click.echo(f"Error: Search failed: {e}", err=True)
        sys.exit(1)

    hits = results.get("hits", {})
    total = hits.get("total", {}).get("value", 0)
    documents = hits.get("hits", [])

    click.echo(f"Found {total} results (showing page {page}, {len(documents)} results)\n")

    if output_format == "json":
        # JSON output
        output = []
        for doc in documents:
            source = doc["_source"]
            source["_score"] = doc["_score"]
            output.append(source)
        click.echo(json.dumps(output, indent=2, default=str))

    elif output_format == "detailed":
        # Detailed output
        for i, doc in enumerate(documents, 1):
            source = doc["_source"]
            highlight = doc.get("highlight", {})

            click.echo(f"--- Result {from_ + i} (score: {doc['_score']:.2f}) ---")
            click.echo(f"Subject: {source.get('subject', 'N/A')}")
            click.echo(f"From: {source.get('sender', 'N/A')} <{source.get('sender_email', '')}>")
            click.echo(f"To: {', '.join(source.get('recipients_to', []))}")
            click.echo(f"Date: {source.get('date_sent', 'N/A')}")
            click.echo(f"Folder: {source.get('folder_path', 'N/A')}")
            click.echo(f"PST File: {source.get('pst_file', 'N/A')}")
            click.echo(f"Has Attachments: {source.get('has_attachments', False)}")

            if highlight.get("body_text"):
                click.echo(f"Snippet: ...{highlight['body_text'][0]}...")

            click.echo()

    else:
        # Table output (default)
        click.echo(f"{'#':<4} {'Score':<6} {'Date':<12} {'From':<25} {'Subject':<50}")
        click.echo("-" * 100)

        for i, doc in enumerate(documents, 1):
            source = doc["_source"]
            score = f"{doc['_score']:.1f}"
            date = source.get("date_sent", "")[:10] if source.get("date_sent") else "N/A"
            sender = source.get("sender", "Unknown")[:24]
            subject = source.get("subject", "No Subject")[:49]

            click.echo(f"{from_ + i:<4} {score:<6} {date:<12} {sender:<25} {subject:<50}")

    es_client.close()


@main.command()
@click.option("--host", default="localhost", help="Elasticsearch host")
@click.option("--port", default=9200, type=int, help="Elasticsearch port")
@click.option("--username", "-u", default=None, help="Elasticsearch username")
@click.option("--password", "-p", default=None, help="Elasticsearch password")
@click.option("--index", "-i", default="pst-emails", help="Elasticsearch index name")
def stats(
    host: str,
    port: int,
    username: Optional[str],
    password: Optional[str],
    index: str,
) -> None:
    """Show statistics about the indexed emails."""
    # Create Elasticsearch client
    try:
        es_client = get_es_client(host, port, username, password, index)
    except ElasticsearchClientError as e:
        click.echo(f"Error: Failed to connect to Elasticsearch: {e}", err=True)
        sys.exit(1)

    if not es_client.ping():
        click.echo(
            f"Error: Cannot connect to Elasticsearch at {host}:{port}",
            err=True,
        )
        sys.exit(1)

    try:
        index_stats = es_client.get_stats()
        doc_count = index_stats["document_count"]
        size_bytes = index_stats["index_size"]

        # Convert size to human readable
        if size_bytes > 1024 * 1024 * 1024:
            size_str = f"{size_bytes / (1024 * 1024 * 1024):.2f} GB"
        elif size_bytes > 1024 * 1024:
            size_str = f"{size_bytes / (1024 * 1024):.2f} MB"
        elif size_bytes > 1024:
            size_str = f"{size_bytes / 1024:.2f} KB"
        else:
            size_str = f"{size_bytes} bytes"

        click.echo(f"Index: {index}")
        click.echo(f"Document count: {doc_count:,}")
        click.echo(f"Index size: {size_str}")

    except ElasticsearchClientError as e:
        click.echo(f"Error: Failed to get stats: {e}", err=True)
        sys.exit(1)

    es_client.close()


@main.command()
@click.argument("message_id")
@click.option("--host", default="localhost", help="Elasticsearch host")
@click.option("--port", default=9200, type=int, help="Elasticsearch port")
@click.option("--username", "-u", default=None, help="Elasticsearch username")
@click.option("--password", "-p", default=None, help="Elasticsearch password")
@click.option("--index", "-i", default="pst-emails", help="Elasticsearch index name")
def show(
    message_id: str,
    host: str,
    port: int,
    username: Optional[str],
    password: Optional[str],
    index: str,
) -> None:
    """
    Show full details of a specific email by message ID.

    MESSAGE_ID: The message ID to retrieve.
    """
    import json

    # Create Elasticsearch client
    try:
        es_client = get_es_client(host, port, username, password, index)
    except ElasticsearchClientError as e:
        click.echo(f"Error: Failed to connect to Elasticsearch: {e}", err=True)
        sys.exit(1)

    if not es_client.ping():
        click.echo(
            f"Error: Cannot connect to Elasticsearch at {host}:{port}",
            err=True,
        )
        sys.exit(1)

    email = es_client.get_email_by_id(message_id)
    if email is None:
        click.echo(f"Email not found: {message_id}", err=True)
        sys.exit(1)

    click.echo(json.dumps(email, indent=2, default=str))
    es_client.close()


@main.command()
@click.option("--host", default="localhost", help="Elasticsearch host")
@click.option("--port", default=9200, type=int, help="Elasticsearch port")
@click.option("--username", "-u", default=None, help="Elasticsearch username")
@click.option("--password", "-p", default=None, help="Elasticsearch password")
@click.option("--index", "-i", default="pst-emails", help="Elasticsearch index name")
@click.option("--yes", "-y", is_flag=True, help="Skip confirmation prompt")
def delete_index(
    host: str,
    port: int,
    username: Optional[str],
    password: Optional[str],
    index: str,
    yes: bool,
) -> None:
    """Delete the email index from Elasticsearch."""
    if not yes:
        if not click.confirm(f"Are you sure you want to delete index '{index}'?"):
            click.echo("Aborted.")
            return

    # Create Elasticsearch client
    try:
        es_client = get_es_client(host, port, username, password, index)
    except ElasticsearchClientError as e:
        click.echo(f"Error: Failed to connect to Elasticsearch: {e}", err=True)
        sys.exit(1)

    if not es_client.ping():
        click.echo(
            f"Error: Cannot connect to Elasticsearch at {host}:{port}",
            err=True,
        )
        sys.exit(1)

    try:
        es_client.delete_index()
        click.echo(f"Deleted index: {index}")
    except ElasticsearchClientError as e:
        click.echo(f"Error: Failed to delete index: {e}", err=True)
        sys.exit(1)

    es_client.close()


if __name__ == "__main__":
    main()
