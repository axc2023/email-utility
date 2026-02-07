# PST Email Search

A macOS utility to parse Outlook PST files and index emails into Elasticsearch for fast, full-text searching.

## Features

- Parse multiple PST files in batch
- Extract email metadata: subject, sender, recipients, dates, folder path, attachments
- Index emails to Elasticsearch with optimized mappings for search
- Simple keyword search with relevance scoring
- Multiple output formats (table, JSON, detailed)
- Progress tracking for large PST files

## Requirements

- macOS (or Linux)
- Python 3.9+
- Elasticsearch 8.x
- libpff (for PST parsing)

## Installation

### 1. Install libpff (PST parsing library)

On macOS with Homebrew:

```bash
brew install libpff
```

On Linux (Ubuntu/Debian):

```bash
sudo apt-get install libpff-dev
```

### 2. Install Elasticsearch

On macOS with Homebrew:

```bash
brew install elasticsearch
brew services start elasticsearch
```

Or using Docker:

```bash
docker run -d --name elasticsearch \
  -p 9200:9200 \
  -e "discovery.type=single-node" \
  -e "xpack.security.enabled=false" \
  elasticsearch:8.11.0
```

### 3. Install the Python package

```bash
# Clone the repository
git clone https://github.com/yourusername/pst-email-search.git
cd pst-email-search

# Create a virtual environment (recommended)
python3 -m venv venv
source venv/bin/activate

# Install the package
pip install -e .

# Or install dependencies directly
pip install -r requirements.txt
```

## Usage

### Index PST Files

Parse and index one or more PST files:

```bash
# Index a single PST file
pst-search index /path/to/mailbox.pst

# Index multiple PST files
pst-search index /path/to/file1.pst /path/to/file2.pst /path/to/file3.pst

# Index all PST files in a directory
pst-search index /path/to/pst-files/*.pst

# Recreate the index (delete existing data)
pst-search index --recreate-index /path/to/mailbox.pst

# Custom Elasticsearch settings
pst-search index --host localhost --port 9200 --index my-emails /path/to/mailbox.pst
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
```

### View Statistics

```bash
pst-search stats
```

### Show Email Details

```bash
pst-search show <message_id>
```

### Delete Index

```bash
pst-search delete-index --yes
```

## Configuration Options

All commands support these Elasticsearch connection options:

| Option | Default | Description |
|--------|---------|-------------|
| `--host` | localhost | Elasticsearch host |
| `--port` | 9200 | Elasticsearch port |
| `--username` / `-u` | None | Username for authentication |
| `--password` / `-p` | None | Password for authentication |
| `--index` / `-i` | pst-emails | Index name |

## Indexed Fields

The following email fields are extracted and indexed:

| Field | Type | Description |
|-------|------|-------------|
| `message_id` | keyword | Unique identifier |
| `subject` | text | Email subject (searchable) |
| `sender` | text | Sender name |
| `sender_email` | keyword | Sender email address |
| `recipients_to` | text | TO recipients |
| `recipients_cc` | text | CC recipients |
| `recipients_bcc` | text | BCC recipients |
| `date_sent` | date | Date email was sent |
| `date_received` | date | Date email was received |
| `body_text` | text | Plain text body (searchable) |
| `body_html` | text | HTML body (searchable) |
| `folder_path` | keyword | Folder path in PST |
| `attachments` | nested | Attachment metadata |
| `has_attachments` | boolean | Has attachments flag |
| `importance` | keyword | Email importance level |
| `pst_file` | keyword | Source PST filename |

## Troubleshooting

### Cannot connect to Elasticsearch

Make sure Elasticsearch is running:

```bash
# Check if Elasticsearch is running
curl http://localhost:9200

# Start Elasticsearch (Homebrew)
brew services start elasticsearch

# Start Elasticsearch (Docker)
docker start elasticsearch
```

### libpff-python installation fails

On macOS, you may need to install libpff first:

```bash
brew install libpff
pip install libpff-python
```

If pip installation fails, try building from source:

```bash
git clone https://github.com/libyal/libpff.git
cd libpff
./synclibs.sh
./autogen.sh
./configure --enable-python
make
sudo make install
```

### Large PST files are slow to process

- Increase the batch size: `--batch-size 1000`
- Ensure Elasticsearch has enough memory
- Consider running on an SSD

## License

MIT License
