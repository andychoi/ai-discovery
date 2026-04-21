"""Tests for push integration (API + Gitea)."""

from __future__ import annotations

import base64
import sqlite3
from pathlib import Path
from unittest.mock import MagicMock, patch

import httpx
import pytest

from ai_discovery.output.push import push_docs, _update_push_status


@pytest.fixture
def sample_docs(tmp_path: Path) -> list[dict]:
    """Create sample written_docs with real markdown files."""
    md_file = tmp_path / "AS-IS-001.md"
    md_file.write_text("# As-Is Document\n\nSome content here.")
    return [
        {
            "doc_id": "AS-IS-001",
            "doc_type": "as-is",
            "domain": "orders",
            "file_path": str(md_file),
            "confidence": 0.85,
        }
    ]


@pytest.fixture
def db_with_generated_docs(tmp_path: Path) -> Path:
    """Create a test DB with generated_docs table and a row."""
    db_path = tmp_path / "test.db"
    conn = sqlite3.connect(str(db_path))
    conn.execute(
        """CREATE TABLE IF NOT EXISTS generated_docs (
            id                INTEGER PRIMARY KEY AUTOINCREMENT,
            scan_id           INTEGER NOT NULL,
            domain            TEXT,
            doc_type          TEXT NOT NULL,
            doc_id            TEXT,
            title             TEXT,
            content_md        TEXT,
            confidence        REAL,
            unverified_claims INTEGER DEFAULT 0,
            push_status       TEXT DEFAULT 'local',
            push_url          TEXT,
            created_at        TEXT NOT NULL,
            UNIQUE(scan_id, domain, doc_type)
        )"""
    )
    conn.execute(
        "INSERT INTO generated_docs (scan_id, domain, doc_type, doc_id, push_status, created_at) "
        "VALUES (1, 'orders', 'as-is', 'AS-IS-001', 'local', '2026-03-11')"
    )
    conn.commit()
    conn.close()
    return db_path


class TestPushApi:
    """Tests for API push mode."""

    @patch("ai_discovery.output.push.httpx.post")
    def test_push_api_sends_correct_request(self, mock_post: MagicMock, sample_docs: list[dict]) -> None:
        # Batch ingest response: one results entry per doc
        mock_resp = MagicMock()
        mock_resp.json.return_value = {
            "results": [
                {"doc_id": "AS-IS-001", "url": "/projects/myproj/docs/AS-IS-001"},
            ],
            "errors": [],
        }
        mock_resp.raise_for_status = MagicMock()
        mock_post.return_value = mock_resp

        results = push_docs(
            sample_docs,
            push_mode="api",
            project_slug="myproj",
            api_url="http://dochub",
            api_token="tok123",
        )

        assert len(results) == 1
        assert results[0]["status"] == "pushed"
        assert results[0]["doc_id"] == "AS-IS-001"
        assert results[0]["error"] is None

        mock_post.assert_called_once()
        call_kwargs = mock_post.call_args
        assert call_kwargs.args[0] == "http://dochub/api/projects/myproj/docs/ingest/batch"
        assert call_kwargs.kwargs["headers"] == {"Authorization": "Bearer tok123"}
        batch = call_kwargs.kwargs["json"]["docs"]
        assert len(batch) == 1
        assert batch[0]["doc_id"] == "AS-IS-001"
        assert batch[0]["doc_type"] == "as-is"
        assert "# As-Is Document" in batch[0]["body"]

    def test_push_api_requires_api_url(self, sample_docs: list[dict]) -> None:
        with pytest.raises(ValueError, match="--api-url required"):
            push_docs(sample_docs, push_mode="api", project_slug="myproj")

    @patch("ai_discovery.output.push.httpx.post")
    def test_push_api_handles_failure(self, mock_post: MagicMock, sample_docs: list[dict]) -> None:
        mock_post.side_effect = httpx.HTTPStatusError(
            "500 Server Error",
            request=MagicMock(),
            response=MagicMock(status_code=500),
        )

        results = push_docs(
            sample_docs,
            push_mode="api",
            project_slug="myproj",
            api_url="http://dochub",
            api_token="tok123",
        )

        assert len(results) == 1
        assert results[0]["status"] == "failed"
        assert results[0]["error"] is not None


class TestPushGitea:
    """Tests for Gitea push mode."""

    @patch("ai_discovery.output.push.httpx.get")
    @patch("ai_discovery.output.push.httpx.put")
    def test_push_gitea_sends_correct_request(
        self, mock_put: MagicMock, mock_get: MagicMock, sample_docs: list[dict]
    ) -> None:
        # File does not exist yet
        mock_get_resp = MagicMock()
        mock_get_resp.status_code = 404
        mock_get.return_value = mock_get_resp

        mock_put_resp = MagicMock()
        mock_put_resp.json.return_value = {"content": {"html_url": "http://gitea/file"}}
        mock_put_resp.raise_for_status = MagicMock()
        mock_put.return_value = mock_put_resp

        results = push_docs(
            sample_docs,
            push_mode="gitea",
            project_slug="myproj",
            gitea_url="http://gitea",
            gitea_token="gittok",
        )

        assert len(results) == 1
        assert results[0]["status"] == "pushed"
        assert results[0]["url"] == "http://gitea/file"

        put_kwargs = mock_put.call_args
        assert "/api/v1/repos/ai-docs/ai-docs-content/contents/" in put_kwargs.args[0]
        assert put_kwargs.kwargs["headers"] == {"Authorization": "token gittok"}
        body = put_kwargs.kwargs["json"]
        assert "sha" not in body  # new file, no sha
        # Verify base64 content
        decoded = base64.b64decode(body["content"]).decode()
        assert "# As-Is Document" in decoded

    def test_push_gitea_requires_gitea_url(self, sample_docs: list[dict]) -> None:
        with pytest.raises(ValueError, match="--gitea-url required"):
            push_docs(sample_docs, push_mode="gitea", project_slug="myproj")

    @patch("ai_discovery.output.push.httpx.get")
    @patch("ai_discovery.output.push.httpx.put")
    def test_push_gitea_updates_existing_file(
        self, mock_put: MagicMock, mock_get: MagicMock, sample_docs: list[dict]
    ) -> None:
        # File exists with a SHA
        mock_get_resp = MagicMock()
        mock_get_resp.status_code = 200
        mock_get_resp.json.return_value = {"sha": "abc123deadbeef"}
        mock_get.return_value = mock_get_resp

        mock_put_resp = MagicMock()
        mock_put_resp.json.return_value = {"content": {"html_url": "http://gitea/file"}}
        mock_put_resp.raise_for_status = MagicMock()
        mock_put.return_value = mock_put_resp

        results = push_docs(
            sample_docs,
            push_mode="gitea",
            project_slug="myproj",
            gitea_url="http://gitea",
            gitea_token="gittok",
        )

        assert results[0]["status"] == "pushed"
        put_body = mock_put.call_args.kwargs["json"]
        assert put_body["sha"] == "abc123deadbeef"


class TestPushDocsDispatch:
    """Tests for push_docs dispatch and DB integration."""

    def test_push_docs_invalid_mode(self, sample_docs: list[dict]) -> None:
        with pytest.raises(ValueError, match="Invalid push_mode"):
            push_docs(sample_docs, push_mode="invalid", project_slug="myproj")

    @patch("ai_discovery.output.push.httpx.post")
    def test_update_push_status_writes_db(
        self,
        mock_post: MagicMock,
        sample_docs: list[dict],
        db_with_generated_docs: Path,
    ) -> None:
        mock_resp = MagicMock()
        mock_resp.json.return_value = {
            "results": [
                {"doc_id": "AS-IS-001", "url": "/docs/AS-IS-001"},
            ],
            "errors": [],
        }
        mock_resp.raise_for_status = MagicMock()
        mock_post.return_value = mock_resp

        push_docs(
            sample_docs,
            push_mode="api",
            project_slug="myproj",
            api_url="http://dochub",
            api_token="tok",
            db_path=db_with_generated_docs,
        )

        # Verify DB was updated
        conn = sqlite3.connect(str(db_with_generated_docs))
        row = conn.execute(
            "SELECT push_status, push_url FROM generated_docs WHERE doc_id = 'AS-IS-001'"
        ).fetchone()
        conn.close()

        assert row[0] == "pushed"
        assert row[1] == "http://dochub/docs/AS-IS-001"
