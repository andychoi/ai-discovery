"""
Screen-centric LLM specification generator (Phase 2).

Generates business-focused specifications for each detected screen
by invoking Sonnet to create prose descriptions of purpose, workflows,
rules, and downstream effects.
"""

import json
import logging
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Optional

from .llm_client import LLMClient
from ..db import get_conn, now_iso
from ..screen_mapper import ScreenMapping
from ..shared.json_extract import extract_json_object

logger = logging.getLogger(__name__)

# Re-exported for callers/tests; the durable path uses tool-use schema
# enforcement (LLMClient.invoke_structured) and only falls back to this on
# providers without tool support.
_extract_json_object = extract_json_object

# JSON schema for screen-spec tool use. Mirrors the example structure in
# build_screen_spec_prompt; with Bedrock tool use the model MUST return an
# object of this shape (no fences, no truncated free text).
SCREEN_SPEC_SCHEMA: dict = {
    "type": "object",
    "properties": {
        "purpose": {"type": "string"},
        "when_used": {"type": "string"},
        "interaction_mode": {
            "type": "string",
            "enum": ["inquiry", "monitoring", "workflow_step",
                     "admin_panel", "configuration", "reporting"],
        },
        "crud_profile": {
            "type": "string",
            "enum": ["read_only", "create_edit", "manage", "admin", "bulk_operation"],
        },
        "user_actions": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {"action": {"type": "string"}, "response": {"type": "string"}},
                "required": ["action", "response"],
            },
        },
        "rules_narrative": {"type": "string"},
        "rules": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {"name": {"type": "string"}, "description": {"type": "string"}},
                "required": ["name", "description"],
            },
        },
        "fields_description": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "name": {"type": "string"},
                    "type": {"type": "string"},
                    "source": {"type": "string"},
                    "description": {"type": "string"},
                },
                "required": ["name", "description"],
            },
        },
        "downstream_effects": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {"action": {"type": "string"}, "effect": {"type": "string"}},
                "required": ["action", "effect"],
            },
        },
        "open_items": {"type": "array", "items": {"type": "string"}},
    },
    "required": [
        "purpose", "when_used", "interaction_mode", "crud_profile",
        "user_actions", "rules_narrative", "rules", "fields_description",
        "downstream_effects", "open_items",
    ],
}


@dataclass
class ScreenSpec:
    """Generated screen specification with all prose and metadata."""

    screen_id: str
    screen_label: str
    menu_path: list[str]
    purpose: str
    when_used: str
    interaction_mode: str
    crud_profile: str
    user_actions: list[dict]
    rules_narrative: str
    rules: list[dict]
    fields_description: list[dict]
    downstream_effects: list[dict]
    open_items: list[dict]
    related_docs: dict
    fe_component: Optional[str] = None
    fe_api_calls: list = None
    be_controllers: list = None
    be_services: list = None
    db_tables: list = None
    batch_jobs: list = None
    external_interfaces: list = None
    source_hashes: dict = None
    confidence: float = 0.8
    tokens_in: int = 0
    tokens_out: int = 0
    model: str = ""
    raw_response: str = ""

    def __post_init__(self):
        if self.fe_api_calls is None:
            self.fe_api_calls = []
        if self.be_controllers is None:
            self.be_controllers = []
        if self.be_services is None:
            self.be_services = []
        if self.db_tables is None:
            self.db_tables = []
        if self.batch_jobs is None:
            self.batch_jobs = []
        if self.external_interfaces is None:
            self.external_interfaces = []
        if self.source_hashes is None:
            self.source_hashes = {}


def build_screen_spec_prompt(
    screen_mapping: ScreenMapping,
    context: dict = None,
) -> str:
    """
    Build Sonnet prompt for screen spec generation.

    Provides screen metadata and asks for prose generation.
    """
    if context is None:
        context = {}

    api_calls_str = json.dumps([asdict(c) for c in screen_mapping.fe_api_calls], indent=2)
    controllers_str = json.dumps([asdict(c) for c in screen_mapping.be_controllers], indent=2)
    services_str = json.dumps([asdict(c) for c in screen_mapping.be_services], indent=2)

    permissions = ", ".join(screen_mapping.screen.permissions) if screen_mapping.screen.permissions else "(not specified)"

    return f"""You are a business analyst documenting a software screen for end users and engineers.

Generate a comprehensive specification for this screen:

## Screen Identity
- **Menu Path**: {" > ".join(screen_mapping.screen.menu_path)}
- **Label**: {screen_mapping.screen.label}
- **Route**: {screen_mapping.screen.path}
- **Frontend Component**: {screen_mapping.fe_component or "(not detected)"}
- **Permissions**: {permissions}

## Detected Integrations
**API Calls Made by This Screen**:
{api_calls_str}

**Backend Controllers Handling These APIs**:
{controllers_str}

**Backend Services Called**:
{services_str}

**Database Tables Accessed**:
{json.dumps(screen_mapping.db_tables)}

**Related Batch Jobs**:
{json.dumps(screen_mapping.batch_jobs)}

**External System Interfaces**:
{json.dumps(screen_mapping.external_interfaces)}

## Your Task

Generate the following prose and structured data for the screen spec template.
Respond in JSON only (no markdown, no explanations). Do not include markdown formatting in string values.

Example response structure:
{{
  "purpose": "Allow users to search for and view customer orders",
  "when_used": "Support agents open this when handling customer inquiries",
  "interaction_mode": "inquiry",
  "crud_profile": "read_only",
  "user_actions": [
    {{"action": "Enter customer ID", "response": "System displays matching customers"}},
    {{"action": "Click customer row", "response": "System navigates to customer detail view"}}
  ],
  "rules_narrative": "Only active customers are shown. Results are paginated.",
  "rules": [
    {{"name": "Active Filter", "description": "Only customers with status=active"}},
    {{"name": "Pagination", "description": "Results limited to 50 per page"}}
  ],
  "fields_description": [
    {{"name": "customer_id", "type": "string", "source": "customers table", "description": "Unique customer identifier"}},
    {{"name": "name", "type": "string", "source": "customers table", "description": "Full customer name"}}
  ],
  "downstream_effects": [
    {{"action": "View order details", "effect": "Triggers inventory check batch job"}}
  ],
  "open_items": []
}}

## Guidance

**purpose**: 1-2 sentences describing what this screen lets users do (business language, not technical).

**when_used**: Persona, workflow context, trigger conditions (when/why users open this screen).

**interaction_mode**: One of [inquiry, monitoring, workflow_step, admin_panel, configuration, reporting].
- inquiry: Users search/view data (read-heavy)
- monitoring: Real-time dashboards, status displays
- workflow_step: Part of a business process (e.g., order approval)
- admin_panel: System administration, configuration
- configuration: Settings, rules configuration
- reporting: Analytics, data export

**crud_profile**: One of [read_only, create_edit, manage, admin, bulk_operation].
- read_only: View-only (no create/update)
- create_edit: Create new or edit existing records
- manage: CRUD with status workflows
- admin: System-level administration
- bulk_operation: Batch create/update/delete

**user_actions**: 3-5 primary user interactions (action → system response pairs).

**rules_narrative**: 2-3 sentence summary of business rules (validation, permissions, logic).

**rules**: Structured list of business rules with names and descriptions.

**fields_description**: For major visible fields, include name, type, source (table or API), and description.

**downstream_effects**: What happens after user submits (other screens, batch jobs, notifications, external systems).

**open_items**: Any ambiguities or unclear mappings (empty list if none detected).

Now generate the spec for this screen:"""


def generate_screen_spec(
    screen_mapping: ScreenMapping,
    llm_client: LLMClient,
    context: dict = None,
) -> Optional[ScreenSpec]:
    """
    Invoke LLM to generate prose spec for a single screen.

    Returns ScreenSpec or None on error.
    """
    try:
        prompt = build_screen_spec_prompt(screen_mapping, context)

        # Durable structured output: on Bedrock the model returns the spec as
        # schema-validated tool input (no markdown fences, no mid-object
        # truncation); on Ollama it falls back to tolerant text parsing.
        # max_tokens headroom: content-rich screens emit large objects.
        response = llm_client.invoke_structured(
            "screen", prompt, SCREEN_SPEC_SCHEMA,
            tool_name="emit_screen_spec",
            tool_description="Emit the structured specification for this screen.",
            max_tokens=8192,
        )
        payload = response.data

        # Construct ScreenSpec object
        spec = ScreenSpec(
            screen_id=screen_mapping.screen.screen_id,
            screen_label=screen_mapping.screen.label,
            menu_path=screen_mapping.screen.menu_path,
            purpose=payload.get("purpose", ""),
            when_used=payload.get("when_used", ""),
            interaction_mode=payload.get("interaction_mode", "inquiry"),
            crud_profile=payload.get("crud_profile", "read_only"),
            user_actions=payload.get("user_actions", []),
            rules_narrative=payload.get("rules_narrative", ""),
            rules=payload.get("rules", []),
            fields_description=payload.get("fields_description", []),
            downstream_effects=payload.get("downstream_effects", []),
            open_items=payload.get("open_items", []),
            related_docs={
                "backend_specs": [c.class_name for c in screen_mapping.be_controllers],
                "batch_jobs": screen_mapping.batch_jobs,
                "databases": screen_mapping.db_tables,
                "interfaces": screen_mapping.external_interfaces,
            },
            fe_component=screen_mapping.fe_component,
            fe_api_calls=screen_mapping.fe_api_calls,
            be_controllers=screen_mapping.be_controllers,
            be_services=screen_mapping.be_services,
            db_tables=screen_mapping.db_tables,
            batch_jobs=screen_mapping.batch_jobs,
            external_interfaces=screen_mapping.external_interfaces,
            source_hashes=screen_mapping.source_hashes,
            confidence=payload.get("confidence", 0.8),
            tokens_in=response.tokens_in,
            tokens_out=response.tokens_out,
            model=response.model,
            raw_response=response.raw_text or json.dumps(payload, default=str),
        )
        return spec
    except json.JSONDecodeError as e:
        logger.error(
            f"Screen spec generation failed for {screen_mapping.screen.screen_id}: "
            f"Invalid JSON response: {e}"
        )
        return None
    except Exception as e:
        logger.error(
            f"Screen spec generation failed for {screen_mapping.screen.screen_id}: {e}"
        )
        return None


def generate_all_screen_specs(
    screen_mappings: list[ScreenMapping],
    llm_client: LLMClient,
    db_path: Path,
    max_workers: int = 4,
    on_progress=None,
) -> list[ScreenSpec]:
    """
    Generate specs for all screens in parallel.

    Args:
        screen_mappings: All mapped screens
        llm_client: LLMClient instance for invoking Sonnet
        db_path: Path to SQLite DB (for context queries)
        max_workers: Concurrent LLM calls
        on_progress: Callback(done, total)

    Returns:
        List of ScreenSpec objects
    """
    specs = []
    context = _build_spec_context(db_path)

    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        futures = {
            executor.submit(generate_screen_spec, m, llm_client, context): m
            for m in screen_mappings
        }

        for i, future in enumerate(as_completed(futures)):
            try:
                spec = future.result()
                if spec:
                    specs.append(spec)
            except Exception as e:
                logger.error(f"Screen spec generation failed: {e}")
            if on_progress:
                on_progress(i + 1, len(screen_mappings))

    return specs


def persist_screen_specs(
    specs: list[ScreenSpec],
    db_path: Path,
    scan_id: int,
) -> None:
    """
    Persist screen specs to database.

    Creates or updates screen_specs table with JSON payload.
    """
    conn = get_conn(db_path)
    try:
        for spec in specs:
            conn.execute(
                """INSERT OR REPLACE INTO screen_specs
                   (scan_id, screen_id, spec_json, confidence,
                    tokens_in, tokens_out, model, created_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    scan_id,
                    spec.screen_id,
                    json.dumps(asdict(spec), default=str),
                    spec.confidence,
                    spec.tokens_in,
                    spec.tokens_out,
                    spec.model,
                    now_iso(),
                ),
            )
        conn.commit()
    finally:
        conn.close()


def _build_spec_context(db_path: Path) -> dict:
    """
    Query DB for domain/service/table info to seed screen specs.

    Returns {domains, services, tables, batch_jobs}.
    """
    # Placeholder: in production, would query the DB for context
    # For now, return empty dict—the prompt works without it
    return {}
