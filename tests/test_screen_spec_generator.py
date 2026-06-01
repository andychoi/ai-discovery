"""Tests for screen-centric LLM spec generation (Phase 2)."""

import json
import tempfile
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from ai_discovery.ai.llm_client import StructuredResponse
from ai_discovery.ai.screen_spec_generator import (
    ScreenSpec,
    build_screen_spec_prompt,
    generate_screen_spec,
    persist_screen_specs,
)
from ai_discovery.db import get_conn, init_db
from ai_discovery.menu_detector import Screen
from ai_discovery.screen_mapper import ScreenMapping


@pytest.fixture
def temp_db():
    """Create a temporary SQLite database for testing."""
    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = Path(tmpdir) / "test.db"
        init_db(db_path)
        yield db_path


class TestScreenSpecPrompt:
    """Test prompt building for LLM spec generation."""

    def test_prompt_includes_screen_metadata(self):
        """Test that prompt includes all required screen metadata."""
        screen = Screen(
            screen_id="customer-search",
            menu_path=["Customers", "Search"],
            label="Customer Search",
            path="/customers/search",
            permissions=["ROLE_USER"],
        )
        mapping = ScreenMapping(screen=screen)

        prompt = build_screen_spec_prompt(mapping)

        assert "Customer Search" in prompt
        assert "/customers/search" in prompt
        assert "Customers > Search" in prompt
        assert "ROLE_USER" in prompt

    def test_prompt_includes_api_calls(self):
        """Test that prompt includes detected API calls."""
        screen = Screen(
            screen_id="search",
            menu_path=["Search"],
            label="Search",
            path="/search",
        )
        from ai_discovery.screen_mapper import ApiCall

        mapping = ScreenMapping(
            screen=screen,
            fe_api_calls=[
                ApiCall(method="GET", path="/api/customers/search"),
                ApiCall(method="GET", path="/api/customers/{id}"),
            ],
        )

        prompt = build_screen_spec_prompt(mapping)

        assert "/api/customers/search" in prompt
        assert "GET" in prompt

    def test_prompt_is_json_structured(self):
        """Test that prompt asks for JSON response."""
        screen = Screen(
            screen_id="test",
            menu_path=["Test"],
            label="Test",
            path="/test",
        )
        mapping = ScreenMapping(screen=screen)

        prompt = build_screen_spec_prompt(mapping)

        assert "JSON" in prompt.upper()
        assert "example" in prompt.lower() or "format" in prompt.lower()


class TestScreenSpecGeneration:
    """Test LLM-driven spec generation."""

    def test_generate_screen_spec_success(self):
        """Test successful screen spec generation via the structured-output path."""
        response_data = {
            "purpose": "Allow users to search for customers",
            "when_used": "When handling customer inquiries",
            "interaction_mode": "inquiry",
            "crud_profile": "read_only",
            "user_actions": [
                {"action": "Enter customer ID", "response": "Display customer details"}
            ],
            "rules_narrative": "Only active customers shown",
            "rules": [{"name": "Active Filter", "description": "status=active"}],
            "fields_description": [
                {
                    "name": "customer_id",
                    "type": "string",
                    "source": "customers table",
                    "description": "Customer ID",
                }
            ],
            "downstream_effects": [],
            "open_items": [],
        }

        screen = Screen(
            screen_id="customer-search",
            menu_path=["Customers", "Search"],
            label="Customer Search",
            path="/customers/search",
        )
        mapping = ScreenMapping(screen=screen)

        # generate_screen_spec now calls invoke_structured, which returns a
        # schema-validated dict directly (no text parsing).
        llm_client = MagicMock()
        llm_client.invoke_structured.return_value = StructuredResponse(
            data=response_data, tokens_in=500, tokens_out=200,
            model="claude-sonnet-4-6", tier="screen", via_tool=True,
        )

        spec = generate_screen_spec(mapping, llm_client)

        assert spec is not None
        assert spec.screen_id == "customer-search"
        assert spec.purpose == "Allow users to search for customers"
        assert spec.interaction_mode == "inquiry"
        assert len(spec.user_actions) == 1

    def test_generate_screen_spec_invalid_json(self):
        """A parse failure on the fallback path is swallowed and yields None."""
        screen = Screen(screen_id="test", menu_path=["Test"], label="Test", path="/test")
        mapping = ScreenMapping(screen=screen)

        # Only the Ollama fallback parses text; simulate it failing to find JSON.
        llm_client = MagicMock()
        llm_client.invoke_structured.side_effect = json.JSONDecodeError("x", "doc", 0)

        spec = generate_screen_spec(mapping, llm_client)
        assert spec is None

    def test_generate_screen_spec_lvm_exception(self):
        """Test handling of LLM invocation exception."""
        llm_client = MagicMock()
        llm_client.invoke_structured.side_effect = Exception("LLM service unavailable")

        screen = Screen(screen_id="test", menu_path=["Test"], label="Test", path="/test")
        mapping = ScreenMapping(screen=screen)

        spec = generate_screen_spec(mapping, llm_client)
        assert spec is None


class TestScreenSpecPersistence:
    """Test database persistence of screen specs."""

    def test_persist_screen_specs(self, temp_db):
        """Test persisting specs to database."""
        # Insert a dummy scan_run to satisfy foreign key constraint
        from ai_discovery.db import now_iso
        conn = get_conn(temp_db)
        conn.execute(
            """INSERT INTO scan_runs (repo_url, repo_path, project_slug, started_at, status)
               VALUES (?, ?, ?, ?, ?)""",
            ("https://test.git", "/test", "test-proj", now_iso(), "completed")
        )
        conn.commit()
        conn.close()

        screen = Screen(
            screen_id="test",
            menu_path=["Test"],
            label="Test",
            path="/test",
        )
        spec = ScreenSpec(
            screen_id="test",
            screen_label="Test Screen",
            menu_path=["Test"],
            purpose="Test purpose",
            when_used="When needed",
            interaction_mode="inquiry",
            crud_profile="read_only",
            user_actions=[],
            rules_narrative="No rules",
            rules=[],
            fields_description=[],
            downstream_effects=[],
            open_items=[],
            related_docs={},
            tokens_in=100,
            tokens_out=50,
            model="test-model",
        )

        persist_screen_specs([spec], temp_db, scan_id=1)

        # Verify in database
        conn = get_conn(temp_db)
        row = conn.execute(
            "SELECT spec_json FROM screen_specs WHERE screen_id = ?", ("test",)
        ).fetchone()
        conn.close()

        assert row is not None
        data = json.loads(row["spec_json"])
        assert data["purpose"] == "Test purpose"
        assert data["screen_id"] == "test"


class TestScreenSpecDataclass:
    """Test ScreenSpec dataclass."""

    def test_screen_spec_creation(self):
        """Test creating a ScreenSpec object."""
        spec = ScreenSpec(
            screen_id="test",
            screen_label="Test",
            menu_path=["Test"],
            purpose="Test",
            when_used="When",
            interaction_mode="inquiry",
            crud_profile="read_only",
            user_actions=[],
            rules_narrative="Rules",
            rules=[],
            fields_description=[],
            downstream_effects=[],
            open_items=[],
            related_docs={},
        )

        assert spec.screen_id == "test"
        assert spec.purpose == "Test"
        assert spec.fe_api_calls == []
        assert spec.db_tables == []

    def test_screen_spec_defaults(self):
        """Test ScreenSpec default values."""
        spec = ScreenSpec(
            screen_id="test",
            screen_label="Test",
            menu_path=["Test"],
            purpose="Test",
            when_used="When",
            interaction_mode="inquiry",
            crud_profile="read_only",
            user_actions=[],
            rules_narrative="Rules",
            rules=[],
            fields_description=[],
            downstream_effects=[],
            open_items=[],
            related_docs={},
        )

        assert spec.confidence == 0.8
        assert spec.tokens_in == 0
        assert spec.tokens_out == 0
        assert spec.model == ""
