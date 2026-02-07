# Email Search

A macOS utility to parse Outlook PST and OLM files and index emails into a SQLite database for fast, full-text searching.

## Features

- Parse multiple PST (Windows Outlook) and OLM (Mac Outlook) files in batch
- Extract email metadata: subject, sender, recipients, dates, folder path, attachments
- Index emails to SQLite with FTS5 full-text search
- Simple keyword search with relevance scoring
- Multiple output formats (table, JSON, detailed)
- Progress tracking for large archive files
- No external database server required - everything stored in a single file

## Supported Formats

| Format | Extension | Description |
|--------|-----------|-------------|
| PST | `.pst` | Windows Outlook Personal Storage Table |
| OLM | `.olm` | Mac Outlook archive format |

## Requirements

- macOS (or Linux)
- Python 3.9+
- libpst (for PST parsing only)

## Installation

### 1. Install libpst (for PST file support)

On macOS with Homebrew:

```bash
brew install libpst
```

On Linux (Ubuntu/Debian):

```bash
sudo apt-get install pst-utils
```

**Note:** OLM files don't require any external dependencies - they're parsed natively by Python.

### 2. Install the Python package

```bash
# Clone the repository
git clone https://github.com/axc2023/email-utility.git
cd email-utility

# Create a virtual environment (recommended)
python3 -m venv venv
source venv/bin/activate

# Install the package
pip install -e .

# Or install dependencies directly
pip install -r requirements.txt
```

## Usage

### Index Email Archives

Parse and index PST and/or OLM files:

```bash
# Index a single PST file
pst-search index /path/to/mailbox.pst

# Index a single OLM file
pst-search index /path/to/archive.olm

# Index multiple files (mixed formats supported)
pst-search index /path/to/file1.pst /path/to/file2.olm /path/to/file3.pst

# Index all PST files in a directory
pst-search index /path/to/archives/*.pst

# Index all OLM files in a directory
pst-search index /path/to/archives/*.olm

# Recreate the database (delete existing data)
pst-search index --recreate /path/to/mailbox.pst

# Custom database path
pst-search index --database /path/to/my_emails.db /path/to/mailbox.pst
```

### Search Emails

Search indexed emails by keyword:

```bash
# Simple search
pst-search search "project update"

# Search with more results
pst-search search "meeting notes" --size 50

# Paginate results
pst-search search "quarterly report" --page 2 --size 20

# JSON output
pst-search search "budget" --format json

# Detailed output with snippets
pst-search search "contract" --format detailed

# Use a specific database
pst-search search "keyword" --database /path/to/my_emails.db
```

### View Statistics

```bash
pst-search stats
pst-search stats --database /path/to/my_emails.db
```

### Show Email Details

```bash
pst-search show <message_id>
```

### Delete Database

```bash
pst-search delete-db --yes
pst-search delete-db --database /path/to/my_emails.db --yes
```

## Configuration Options

All commands support these options:

| Option | Default | Description |
|--------|---------|-------------|
| `--database` / `-d` | emails.db | SQLite database file path |

### Index Command Options

| Option | Default | Description |
|--------|---------|-------------|
| `--batch-size` | 500 | Batch size for bulk indexing |
| `--recreate` | False | Delete and recreate the database |

### Search Command Options

| Option | Default | Description |
|--------|---------|-------------|
| `--size` / `-n` | 20 | Number of results to return |
| `--page` | 1 | Page number (1-indexed) |
| `--format` / `-f` | table | Output format (table, json, detailed) |

## Indexed Fields

The following email fields are extracted and indexed:

| Field | Type | Description |
|-------|------|-------------|
| `message_id` | text | Unique identifier |
| `subject` | text | Email subject (searchable) |
| `sender` | text | Sender name (searchable) |
| `sender_email` | text | Sender email address (searchable) |
| `recipients_to` | text | TO recipients (searchable) |
| `recipients_cc` | text | CC recipients |
| `recipients_bcc` | text | BCC recipients |
| `date_sent` | text | Date email was sent |
| `date_received` | text | Date email was received |
| `body_text` | text | Plain text body (searchable) |
| `body_html` | text | HTML body |
| `folder_path` | text | Folder path in archive |
| `attachments` | json | Attachment metadata |
| `has_attachments` | integer | Has attachments flag |
| `importance` | text | Email importance level |
| `pst_file` | text | Source archive filename |

## Troubleshooting

### readpst command not found (PST files only)

Make sure libpst is installed:

```bash
# macOS
brew install libpst

# Linux (Ubuntu/Debian)
sudo apt-get install pst-utils
```

Verify installation:

```bash
readpst --version
```

### OLM file not parsing correctly

OLM files are ZIP archives containing XML. If parsing fails:
- Ensure the file is a valid OLM export from Outlook for Mac
- Check that the file isn't corrupted (try opening in Finder)

### Large files are slow to process

- Increase the batch size: `--batch-size 1000`
- Consider running on an SSD
- The first indexing takes time, but subsequent searches are fast

### Search not finding expected results

SQLite FTS5 uses a specific query syntax. For best results:
- Use simple keywords: `pst-search search "meeting"`
- Use quotes for phrases: `pst-search search '"project update"'`
- Use OR for alternatives: `pst-search search "meeting OR conference"`

## License

MIT License
