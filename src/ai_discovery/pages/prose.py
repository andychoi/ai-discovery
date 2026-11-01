"""Page prose layer (roadmap item M3.7.a, ADR-0005 §4, ADR-0002).

With ``--prose``, overview, domain, screen, flow, entity and onboarding pages get a **Narrative** section
written by the ``standard`` model from the page's deterministic facts. The builders already decided what
each page covers (which symbols, steps, tables); the LLM only names and narrates it.

- **Key** (``Page.prose_ref``): ``sha256(page facts, prompt id, prompt version, model id, role)`` (ADR-0002).
- **Reuse:** if the page had prose under the same key in the previous build, it is reused with no call;
  otherwise an identical prompt is answered from the ``llm_cache`` response cache when one is attached.
- **Truncated / empty answers are invalid:** the key is remembered as invalid (in the response cache file),
  so the identical request is not sent again; the page is handled like a failure.
- **Failure** (provider error, invalid answer, budget reached before the call): the page keeps the prose it
  had before — marked ``stale`` when it came from older facts — and never loses it; a page that never had
  prose gets a stub section at confidence 0.3 (ADR-0005).

The structural sections are never changed. Pages get confidence 0.8 with prose (ADR-0005), 0.3 with a stub.
"""

from __future__ import annotations

import hashlib
import re
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from typing import Any, Callable, Optional

from ..prompts import (PAGE_PROSE_CORRECTIVE, PAGE_PROSE_FOCUS, PAGE_PROSE_ID, PAGE_PROSE_SYSTEM,
                       PAGE_PROSE_TEMPLATE, PAGE_PROSE_VERSION)
from .model import CONFIDENCE_PROSE, CONFIDENCE_STUB, Page, Section, canonical_json

PROSE_TYPES = frozenset(PAGE_PROSE_FOCUS)
NARRATIVE = "Narrative"
STUB_PREFIX = "_No narrative yet"
TIER = "tier2"                      # the `standard` role
ROLE = "standard"
MAX_TOKENS = 600
MAX_BODY_CHARS = 6000
MAX_FACTS_CHARS = 6000
INVALID_PREFIX = "prose-invalid:"
_HEADING = re.compile(r"^\s*#{1,6}\s")


@dataclass
class ProseResult:
    written: int = 0
    reused: int = 0          # previous build's prose, same key — no call
    kept: int = 0            # previous prose kept after a failure (marked stale when its facts changed)
    stubs: int = 0
    invalid: int = 0         # truncated / empty answers (remembered, not re-asked)
    reasked: int = 0         # M3.8: grounded only after the corrective re-ask
    ungrounded: int = 0      # M3.8: still referred to unknown things after the re-ask
    failed: int = 0          # provider errors
    skipped_budget: int = 0
    errors: list[str] = field(default_factory=list)

    def detail(self) -> str:
        parts = [f"{self.written} written", f"{self.reused} reused"]
        for name in ("reasked", "ungrounded", "kept", "stubs", "invalid", "failed", "skipped_budget"):
            if getattr(self, name):
                parts.append(f"{getattr(self, name)} {name.replace('_', ' ')}")
        return "prose: " + ", ".join(parts)


def prose_key(page: Page, model_id: str) -> str:
    payload = canonical_json({"facts": page.facts, "title": page.title, "type": page.type,
                              "prompt_id": PAGE_PROSE_ID, "prompt_version": PAGE_PROSE_VERSION,
                              "model": model_id, "role": ROLE})
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def build_prompt(page: Page) -> str:
    facts = canonical_json(page.facts)
    body = page.body_md()
    return PAGE_PROSE_TEMPLATE.format(focus=PAGE_PROSE_FOCUS[page.type], title=page.title, page_type=page.type,
                                      facts=facts[:MAX_FACTS_CHARS], body=body[:MAX_BODY_CHARS])


def narrative_of(page: Optional[Page]) -> Optional[str]:
    """The page's real prose (not a stub), if it has any."""
    if page is None or not page.prose_ref:
        return None
    for s in page.sections:
        if s.heading == NARRATIVE and not s.body_md.startswith(STUB_PREFIX):
            return s.body_md
    return None


def clean(text: str) -> Optional[str]:
    """Answer → narrative markdown, or None when it is unusable."""
    lines = [ln for ln in (text or "").strip().splitlines() if not _HEADING.match(ln)]
    out = "\n".join(lines).strip()
    return out or None


def _with_narrative(page: Page, body: str, *, confidence: float, prose_ref: Optional[str],
                    freshness: Optional[str] = None) -> None:
    page.sections = [Section(NARRATIVE, body)] + [s for s in page.sections if s.heading != NARRATIVE]
    page.confidence = min(page.confidence, confidence)
    page.prose_ref = prose_ref
    if freshness:
        page.freshness = freshness


def apply_prose(pages: list[Page], llm: Any, *, model_id: str, previous: dict[str, Page] | None = None,
                memo: Any = None, budget_ok: Callable[[], bool] = lambda: True,
                max_workers: int = 4, ground: Optional[Callable[[str, str], Any]] = None) -> ProseResult:
    """Add (or reuse) a Narrative section on every prose page of *pages*, in place.

    *llm*: anything with ``invoke(tier, prompt, max_tokens, *, system=...)`` returning an object with
    ``text`` and ``truncated`` (``ai.llm_client.LLMClient``, or a test double). *memo*: a
    ``llm.cache.ResponseCache`` used to remember invalid keys (optional). *budget_ok* is checked before
    every call. *ground* (``pages.grounding.Grounder.check``, M3.8) checks a narrative against what the model
    was shown and the index; an ungrounded answer gets one corrective re-ask, then counts as a failure.
    """
    previous = previous or {}
    result = ProseResult()
    todo: list[tuple[Page, str]] = []
    for page in pages:
        if page.type not in PROSE_TYPES:
            continue
        key = prose_key(page, model_id)
        prev = previous.get(page.page_id)
        old = narrative_of(prev)
        if old is not None and prev.prose_ref == key and (ground is None or ground(old, build_prompt(page)).ok):
            _with_narrative(page, old, confidence=CONFIDENCE_PROSE, prose_ref=key)
            result.reused += 1
            continue
        todo.append((page, key))

    def ask(item: tuple[Page, str]) -> tuple[Page, str, Optional[str], str]:
        page, key = item
        if memo is not None and memo.get(INVALID_PREFIX + key) is not None:
            return page, key, None, "invalid"
        if not budget_ok():
            return page, key, None, "budget"
        prompt = build_prompt(page)
        outcome = "ok"
        for attempt in range(2 if ground is not None else 1):
            try:
                resp = llm.invoke(TIER, prompt, MAX_TOKENS, system=PAGE_PROSE_SYSTEM)
            except Exception as exc:   # noqa: BLE001 — a provider failure must never fail the page build
                return page, key, None, f"failed: {type(exc).__name__}: {exc}"[:300]
            text = clean(getattr(resp, "text", ""))
            if getattr(resp, "truncated", False) or text is None:
                if memo is not None and attempt == 0:
                    memo.put(INVALID_PREFIX + key, {"reason": "truncated" if getattr(resp, "truncated", False)
                                                    else "empty"}, role=ROLE, model_id=model_id,
                             kind="prose_invalid", usage={})
                return page, key, None, "invalid"
            if ground is None:
                return page, key, text, outcome
            g = ground(text, build_prompt(page))
            if g.ok and g.text:
                return page, key, g.text, outcome
            refs = ", ".join(f"`{r}`" for r in g.unverified[:8]) or "a diagram that could not be kept"
            prompt = build_prompt(page) + PAGE_PROSE_CORRECTIVE.format(refs=refs)
            outcome = "reasked"
        return page, key, None, f"ungrounded: {refs}"

    with ThreadPoolExecutor(max_workers=max(1, max_workers)) as pool:
        answers = list(pool.map(ask, todo))
    for page, key, text, outcome in answers:      # applied in page order: deterministic
        if outcome in ("ok", "reasked"):
            _with_narrative(page, text, confidence=CONFIDENCE_PROSE, prose_ref=key)
            result.written += 1
            result.reasked += outcome == "reasked"
            continue
        if outcome == "invalid":
            result.invalid += 1
        elif outcome.startswith("ungrounded"):
            result.ungrounded += 1
            result.errors.append(f"{page.page_id}: {outcome}")
        elif outcome == "budget":
            result.skipped_budget += 1
        else:
            result.failed += 1
            result.errors.append(f"{page.page_id}: {outcome}")
        prev = previous.get(page.page_id)
        old = narrative_of(prev)
        if old is not None:      # never overwrite existing prose; it describes older facts
            _with_narrative(page, old, confidence=CONFIDENCE_PROSE, prose_ref=prev.prose_ref, freshness="stale")
            result.kept += 1
        else:
            reason = {"invalid": "the model's answer was cut off or empty",
                      "budget": "the LLM budget was reached"}.get(outcome, "the LLM call failed")
            if outcome.startswith("ungrounded"):
                reason = "the narrative referred to things not found in the scan (" + outcome.split(": ", 1)[1] + ")"
            _with_narrative(page, f"{STUB_PREFIX}: {reason}. The structural sections below are complete.",
                            confidence=CONFIDENCE_STUB, prose_ref=None)
            result.stubs += 1
    return result


# ── the post-scan hook's prose step ─────────────────────────────────────────

def runner(config: Any, layout: Any, conn: Any, scan_id: int, *, llm: Any = None):
    """The ``prose`` callable ``pages.build.build_pages`` takes for a ``--prose`` scan.

    Uses its own ``LLMClient`` (with the project's response cache) and records its spend in ``llm_costs`` under
    ``page_prose`` — next to, never replacing, the pipeline's rows — within what is left of the scan budget.
    Failures become ``degradations`` rows (stage ``pages``).
    """
    import json

    from ..llm.cache import ResponseCache

    cache_path = layout.cache_dir / "llm_cache.db"
    if llm is None:
        from ..ai.llm_client import LLMClient

        llm = LLMClient(config)
        if hasattr(llm, "attach_cache"):
            llm.attach_cache(cache_path)
    memo = ResponseCache(cache_path)
    spent = conn.execute("SELECT COALESCE(SUM(est_usd), 0) FROM llm_costs WHERE scan_id = ?", (scan_id,)).fetchone()[0]
    limit = float(getattr(config, "budget_limit_usd", 0) or 0)
    total = getattr(llm, "total_cost_usd", lambda: 0.0)

    def budget_ok() -> bool:
        return not limit or (spent or 0) + total() < limit

    def run(pages: list[Page], previous: dict[str, Page]) -> str:
        from .grounding import Grounder

        grounder = Grounder.from_db(conn, scan_id, [p.page_id for p in pages], [p.title for p in pages])
        res = apply_prose(pages, llm, model_id=config.get_model(ROLE), previous=previous, memo=memo,
                          budget_ok=budget_ok, max_workers=int(getattr(config, "max_concurrent", 4) or 4),
                          ground=grounder.check)
        costs = llm.get_costs() if hasattr(llm, "get_costs") else {}
        for i, (tier, data) in enumerate(sorted(costs.items())):
            conn.execute("INSERT OR REPLACE INTO llm_costs (scan_id, tier, model, calls, tokens_in, tokens_out, "
                         "est_usd) VALUES (?, ?, ?, ?, ?, ?, ?)",
                         (scan_id, "page_prose" if i == 0 else f"page_prose:{tier}", data.get("model", ""),
                          data["calls"], data["tokens_in"], data["tokens_out"], data["est_usd"]))
        ungrounded = [e for e in res.errors if ": ungrounded" in e][:5]
        for kind, count, sample in (("prose_failed", res.failed, [e for e in res.errors if ": failed" in e][:5]),
                                    ("prose_ungrounded", res.ungrounded, ungrounded),
                                    ("prose_invalid", res.invalid, []),
                                    ("prose_budget_skipped", res.skipped_budget, [])):
            if count:
                conn.execute("INSERT OR REPLACE INTO degradations (scan_id, stage, kind, count, sample_json) "
                             "VALUES (?, 'pages', ?, ?, ?)", (scan_id, kind, count, json.dumps(sample)))
        return res.detail()
    return run

