"""Elasticsearch client for indexing and searching emails."""

from typing import Any, Generator, Optional

from elasticsearch import Elasticsearch
from elasticsearch.helpers import bulk, BulkIndexError

from .models import EmailMessage


# Elasticsearch index mapping for emails
EMAIL_INDEX_MAPPING = {
    "settings": {
        "number_of_shards": 1,
        "number_of_replicas": 0,
        "analysis": {
            "analyzer": {
                "email_analyzer": {
                    "type": "custom",
                    "tokenizer": "standard",
                    "filter": ["lowercase", "stop", "snowball"],
                }
            }
        },
    },
    "mappings": {
        "properties": {
            "message_id": {"type": "keyword"},
            "subject": {
                "type": "text",
                "analyzer": "email_analyzer",
                "fields": {"keyword": {"type": "keyword"}},
            },
            "sender": {
                "type": "text",
                "fields": {"keyword": {"type": "keyword"}},
            },
            "sender_email": {"type": "keyword"},
            "recipients_to": {
                "type": "text",
                "fields": {"keyword": {"type": "keyword"}},
            },
            "recipients_cc": {
                "type": "text",
                "fields": {"keyword": {"type": "keyword"}},
            },
            "recipients_bcc": {
                "type": "text",
                "fields": {"keyword": {"type": "keyword"}},
            },
            "date_sent": {"type": "date"},
            "date_received": {"type": "date"},
            "body_text": {"type": "text", "analyzer": "email_analyzer"},
            "body_html": {"type": "text", "analyzer": "email_analyzer"},
            "folder_path": {"type": "keyword"},
            "attachments": {
                "type": "nested",
                "properties": {
                    "filename": {
                        "type": "text",
                        "fields": {"keyword": {"type": "keyword"}},
                    },
                    "size": {"type": "long"},
                    "content_type": {"type": "keyword"},
                },
            },
            "has_attachments": {"type": "boolean"},
            "importance": {"type": "keyword"},
            "pst_file": {"type": "keyword"},
        }
    },
}


class ElasticsearchClientError(Exception):
    """Exception raised for Elasticsearch client errors."""

    pass


class EmailSearchClient:
    """Client for indexing and searching emails in Elasticsearch."""

    DEFAULT_INDEX = "pst-emails"

    def __init__(
        self,
        host: str = "localhost",
        port: int = 9200,
        scheme: str = "http",
        username: Optional[str] = None,
        password: Optional[str] = None,
        api_key: Optional[str] = None,
        index_name: str = DEFAULT_INDEX,
        verify_certs: bool = True,
    ):
        """
        Initialize the Elasticsearch client.

        Args:
            host: Elasticsearch host.
            port: Elasticsearch port.
            scheme: Connection scheme (http or https).
            username: Username for basic auth.
            password: Password for basic auth.
            api_key: API key for authentication.
            index_name: Name of the index to use.
            verify_certs: Whether to verify SSL certificates.
        """
        self.index_name = index_name

        # Build connection parameters
        es_params: dict[str, Any] = {
            "hosts": [{"host": host, "port": port, "scheme": scheme}],
            "verify_certs": verify_certs,
        }

        if api_key:
            es_params["api_key"] = api_key
        elif username and password:
            es_params["basic_auth"] = (username, password)

        try:
            self.es = Elasticsearch(**es_params)
        except Exception as e:
            raise ElasticsearchClientError(f"Failed to create Elasticsearch client: {e}") from e

    def ping(self) -> bool:
        """Check if Elasticsearch is reachable."""
        try:
            return self.es.ping()
        except Exception:
            return False

    def create_index(self, delete_existing: bool = False) -> None:
        """
        Create the email index with proper mappings.

        Args:
            delete_existing: If True, delete existing index first.

        Raises:
            ElasticsearchClientError: If index creation fails.
        """
        try:
            if self.es.indices.exists(index=self.index_name):
                if delete_existing:
                    self.es.indices.delete(index=self.index_name)
                else:
                    return  # Index already exists

            self.es.indices.create(index=self.index_name, body=EMAIL_INDEX_MAPPING)
        except Exception as e:
            raise ElasticsearchClientError(f"Failed to create index: {e}") from e

    def delete_index(self) -> None:
        """Delete the email index."""
        try:
            if self.es.indices.exists(index=self.index_name):
                self.es.indices.delete(index=self.index_name)
        except Exception as e:
            raise ElasticsearchClientError(f"Failed to delete index: {e}") from e

    def index_email(self, email: EmailMessage) -> None:
        """
        Index a single email.

        Args:
            email: The email message to index.
        """
        try:
            self.es.index(
                index=self.index_name,
                id=email.message_id,
                document=email.to_dict(),
            )
        except Exception as e:
            raise ElasticsearchClientError(f"Failed to index email: {e}") from e

    def bulk_index_emails(
        self,
        emails: Generator[EmailMessage, None, None],
        batch_size: int = 500,
        raise_on_error: bool = False,
    ) -> tuple[int, int]:
        """
        Bulk index emails for better performance.

        Args:
            emails: Generator of EmailMessage objects.
            batch_size: Number of documents per batch.
            raise_on_error: Whether to raise on indexing errors.

        Returns:
            Tuple of (success_count, error_count).
        """
        success_count = 0
        error_count = 0

        def generate_actions() -> Generator[dict[str, Any], None, None]:
            for email in emails:
                yield {
                    "_index": self.index_name,
                    "_id": email.message_id,
                    "_source": email.to_dict(),
                }

        try:
            # Process in batches
            batch: list[dict[str, Any]] = []
            for action in generate_actions():
                batch.append(action)
                if len(batch) >= batch_size:
                    try:
                        success, errors = bulk(
                            self.es,
                            batch,
                            raise_on_error=raise_on_error,
                            stats_only=True,
                        )
                        success_count += success
                        error_count += errors
                    except BulkIndexError as e:
                        success_count += len(batch) - len(e.errors)
                        error_count += len(e.errors)
                    batch = []

            # Process remaining items
            if batch:
                try:
                    success, errors = bulk(
                        self.es,
                        batch,
                        raise_on_error=raise_on_error,
                        stats_only=True,
                    )
                    success_count += success
                    error_count += errors
                except BulkIndexError as e:
                    success_count += len(batch) - len(e.errors)
                    error_count += len(e.errors)

        except Exception as e:
            raise ElasticsearchClientError(f"Bulk indexing failed: {e}") from e

        return success_count, error_count

    def search(
        self,
        query: str,
        fields: Optional[list[str]] = None,
        size: int = 20,
        from_: int = 0,
        sort_by: str = "_score",
        sort_order: str = "desc",
    ) -> dict[str, Any]:
        """
        Search emails by keyword.

        Args:
            query: Search query string.
            fields: Fields to search in (default: subject, body_text, sender).
            size: Number of results to return.
            from_: Offset for pagination.
            sort_by: Field to sort by.
            sort_order: Sort order (asc or desc).

        Returns:
            Search results dictionary.
        """
        if fields is None:
            fields = ["subject^2", "body_text", "sender", "sender_email", "recipients_to"]

        search_body = {
            "query": {
                "multi_match": {
                    "query": query,
                    "fields": fields,
                    "type": "best_fields",
                    "fuzziness": "AUTO",
                }
            },
            "size": size,
            "from": from_,
            "sort": [{sort_by: {"order": sort_order}}] if sort_by != "_score" else ["_score"],
            "highlight": {
                "fields": {
                    "subject": {},
                    "body_text": {"fragment_size": 150, "number_of_fragments": 3},
                }
            },
        }

        try:
            response = self.es.search(index=self.index_name, body=search_body)
            return response.body
        except Exception as e:
            raise ElasticsearchClientError(f"Search failed: {e}") from e

    def get_email_by_id(self, message_id: str) -> Optional[dict[str, Any]]:
        """
        Get a specific email by its message ID.

        Args:
            message_id: The message ID to retrieve.

        Returns:
            Email document or None if not found.
        """
        try:
            response = self.es.get(index=self.index_name, id=message_id)
            return response.body["_source"]
        except Exception:
            return None

    def get_stats(self) -> dict[str, Any]:
        """
        Get index statistics.

        Returns:
            Dictionary with index stats.
        """
        try:
            stats = self.es.indices.stats(index=self.index_name)
            count = self.es.count(index=self.index_name)
            return {
                "document_count": count.body["count"],
                "index_size": stats.body["indices"][self.index_name]["total"]["store"][
                    "size_in_bytes"
                ],
            }
        except Exception as e:
            raise ElasticsearchClientError(f"Failed to get stats: {e}") from e

    def refresh_index(self) -> None:
        """Refresh the index to make recent changes searchable."""
        try:
            self.es.indices.refresh(index=self.index_name)
        except Exception as e:
            raise ElasticsearchClientError(f"Failed to refresh index: {e}") from e

    def close(self) -> None:
        """Close the Elasticsearch client connection."""
        try:
            self.es.close()
        except Exception:
            pass
