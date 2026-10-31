"""Agent write-back (roadmap item M5.10): findings, notes, proposed decisions — all opt-in.

Writes are signed (agent, client, session), capped per session, never change graph
facts, and come back through ``get_context`` / ``get_annotations`` labelled as
untrusted text. Decisions stay ``proposed`` until a person accepts them in the wiki.
"""

from __future__ import annotations

from ...query import annotations as ann
from ..tool import Budget, ToolSpec, schema

_TARGET = {"type": "string", "minLength": 1, "maxLength": 500,
           "description": "Symbol (qualified or short name) or page id (`file:…`, `screen:…`, `table:…`, …)."}
_TEXT = {"type": "string", "minLength": 1, "maxLength": ann.MAX_TEXT}
_EVIDENCE = {"type": "array", "maxItems": ann.MAX_EVIDENCE,
             "items": {"anyOf": [{"type": "string", "maxLength": ann.MAX_EVIDENCE_ITEM},
                                 {"type": "object", "additionalProperties": False,
                                  "properties": {"path": {"type": "string"}, "line": {"type": "integer"},
                                                 "note": {"type": "string"}}}]},
             "description": "Where you saw it: {path, line, note} objects or short strings."}
_WRITE = ("Writes to the project's index (not to code). Signed as agent with your client and session; "
          f"at most {ann.DEFAULT_WRITE_LIMIT} writes per session by default.")

TOOLS = [
    ToolSpec(
        name="record_finding", rank=80, title="Record a finding", read_only=False, needs_signer=True,
        description=("Record something you verified that others should know — a bug, risk, open question, "
                     "documentation gap, or a gap in the graph (`category: graph_gap`) — anchored to a symbol "
                     "or page, with file:line evidence. Status starts `open`. " + _WRITE),
        input_schema=schema({"target": _TARGET, "text": _TEXT,
                             "category": {"type": "string", "enum": list(ann.CATEGORIES)},
                             "evidence": _EVIDENCE}, required=("target", "text")),
        handler=lambda project, args, signer: ann.create(
            project, kind="finding", target=args["target"], text=args["text"], signer=signer,
            category=args.get("category"), evidence=args.get("evidence")),
        budget=Budget(max_tokens=2000, shed=("evidence",)), profiles=frozenset()),
    ToolSpec(
        name="set_finding_status", rank=81, title="Update a finding", read_only=False, needs_signer=True,
        description=("Move a finding to open | acknowledged | resolved | false_positive (e.g. after fixing it). "
                     "Decisions cannot be accepted or rejected here — that needs a person in the wiki. " + _WRITE),
        input_schema=schema({"id": {"type": "integer", "minimum": 1},
                             "status": {"type": "string", "enum": list(ann.FINDING_STATUSES)}},
                            required=("id", "status")),
        handler=lambda project, args, signer: ann.set_status(project, args["id"], args["status"], signer=signer),
        budget=Budget(max_tokens=2000, shed=("evidence",)), profiles=frozenset()),
    ToolSpec(
        name="add_note", rank=82, title="Add a note", read_only=False, needs_signer=True,
        description=("Leave a short note on a symbol or page for the next reader (human or agent), e.g. a "
                     "convention you discovered. Notes show in get_context and in the wiki. " + _WRITE),
        input_schema=schema({"target": _TARGET, "text": _TEXT}, required=("target", "text")),
        handler=lambda project, args, signer: ann.create(project, kind="note", target=args["target"],
                                                         text=args["text"], signer=signer),
        budget=Budget(max_tokens=2000, shed=("evidence",)), profiles=frozenset()),
    ToolSpec(
        name="propose_decision", rank=83, title="Propose a decision", read_only=False, needs_signer=True,
        description=("Propose a design or business decision you inferred (why the code is the way it is), with "
                     "evidence. It stays `proposed` — shown as unconfirmed — until a person accepts or rejects "
                     "it in the wiki. " + _WRITE),
        input_schema=schema({"target": _TARGET, "text": _TEXT, "evidence": _EVIDENCE}, required=("target", "text")),
        handler=lambda project, args, signer: ann.create(
            project, kind="decision", target=args["target"], text=args["text"], signer=signer,
            evidence=args.get("evidence")),
        budget=Budget(max_tokens=2000, shed=("evidence",)), profiles=frozenset()),
    ToolSpec(
        name="get_annotations", rank=84, title="List annotations",
        description=("Findings, notes and decisions — by target or all — newest first, with signer kind "
                     "(agent | human), status, and `orphaned: true` when the anchored code no longer exists."),
        input_schema=schema({"target": _TARGET, "kind": {"type": "string", "enum": list(ann.KINDS)},
                             "status": {"type": "string",
                                        "enum": list(ann.FINDING_STATUSES) + list(ann.DECISION_STATUSES)},
                             "limit": {"type": "integer", "minimum": 1, "maximum": 200, "default": 50}}),
        handler=lambda project, args: ann.list_annotations(
            project, target=args.get("target"), kind=args.get("kind"), status=args.get("status"),
            limit=args.get("limit", 50)),
        budget=Budget(max_tokens=5000, shed=("annotations",)), profiles=frozenset()),
]
