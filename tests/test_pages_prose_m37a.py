"""Page prose layer (roadmap item M3.7.a) — with a stub LLM."""

from __future__ import annotations

import copy
import json
import sqlite3
import threading
from pathlib import Path
from types import SimpleNamespace

import pytest
from typer.testing import CliRunner

from ai_discovery.llm.cache import ResponseCache
from ai_discovery.pages.model import CONFIDENCE_PROSE, CONFIDENCE_STUB
from ai_discovery.pages.prose import NARRATIVE, PROSE_TYPES, STUB_PREFIX, apply_prose, prose_key
from ai_discovery.pages.registry import BuildContext, run_builders
from tests.test_builders_flows_m34 import FIXTURES, _git_scan

MODEL = "stub-standard"


class Stub:
    """invoke(tier, prompt, max_tokens, system=) → text naming the page; records every call."""

    def __init__(self, *, fail: bool = False, truncated: bool = False, text: str | None = None):
        self.calls: list[str] = []
        self.fail, self.truncated, self.text = fail, truncated, text
        self._lock = threading.Lock()

    def invoke(self, tier, prompt, max_tokens=4096, *, system=""):
        with self._lock:
            self.calls.append(prompt)
        assert tier == "tier2" and system
        if self.fail:
            raise ConnectionError("provider down")
        title = prompt.split("Page: ", 1)[1].split("\n", 1)[0].rsplit(" (a ", 1)[0]
        return SimpleNamespace(text=self.text if self.text is not None else f"## Heading\nNarrative for {title}.",
                               truncated=self.truncated)


@pytest.fixture(scope="module")
def built(tmp_path_factory):
    import shutil

    repo = tmp_path_factory.mktemp("prose") / "repo"
    shutil.copytree(FIXTURES / "projects" / "spring-boot-app", repo)
    conn, sid = _git_scan(repo)
    return run_builders(BuildContext(conn=conn, scan_id=sid, repo=repo))


def _fresh(pages):
    return copy.deepcopy(pages)


def _narratives(pages):
    return {p.page_id: p for p in pages if any(s.heading == NARRATIVE for s in p.sections)}


def test_prose_adds_narratives_to_the_six_page_types_only(built) -> None:
    pages = _fresh(built)
    before = {p.page_id: [s for s in p.sections] for p in pages}
    llm = Stub()
    res = apply_prose(pages, llm, model_id=MODEL)
    narrated = _narratives(pages)
    eligible = {p.page_id for p in pages if p.type in PROSE_TYPES}
    assert set(narrated) == eligible and {"overview", "flow", "domain", "screen"} <= {p.type for p in narrated.values()}
    assert res.written == len(eligible) == len(llm.calls)
    for p in narrated.values():
        assert p.sections[0].heading == NARRATIVE and p.sections[0].body_md == f"Narrative for {p.title}."
        assert p.sections[1:] == before[p.page_id]                           # structure untouched
        assert p.confidence == CONFIDENCE_PROSE and p.prose_ref == prose_key(p, MODEL)
    others = [p for p in pages if p.type not in PROSE_TYPES]
    assert all(p.prose_ref is None and p.sections == before[p.page_id] for p in others)


def test_prose_unchanged_facts_make_no_call(built) -> None:
    first = _fresh(built)
    apply_prose(first, Stub(), model_id=MODEL)
    previous = {p.page_id: p for p in first}
    again, llm = _fresh(built), Stub()
    res = apply_prose(again, llm, model_id=MODEL, previous=previous)
    assert llm.calls == [] and res.reused == len(_narratives(again)) and res.written == 0
    # a changed fact (or another model) is a new key: only that page is asked again
    changed = _fresh(built)
    flow = next(p for p in changed if p.type == "flow")
    flow.facts["reached"] = 999
    llm = Stub()
    apply_prose(changed, llm, model_id=MODEL, previous=previous)
    assert len(llm.calls) == 1 and f"Page: {flow.title}" in llm.calls[0]
    assert len(Stub().calls) == 0
    other_model = Stub()
    apply_prose(_fresh(built), other_model, model_id="another-model", previous=previous)
    assert len(other_model.calls) == len(_narratives(first))


def test_prose_failure_writes_a_stub_but_never_overwrites_existing_prose(built) -> None:
    pages = _fresh(built)
    res = apply_prose(pages, Stub(fail=True), model_id=MODEL)
    stubs = _narratives(pages)
    assert res.failed == res.stubs == len(stubs) and res.written == 0
    assert all(p.sections[0].body_md.startswith(STUB_PREFIX) and p.confidence == CONFIDENCE_STUB
               and p.prose_ref is None for p in stubs.values())
    # with prose from an earlier build, a failure keeps it (stale when its facts changed)
    good = _fresh(built)
    apply_prose(good, Stub(), model_id=MODEL)
    previous = {p.page_id: p for p in good}
    changed = _fresh(built)
    for p in changed:
        p.facts["_edited"] = True
    res = apply_prose(changed, Stub(fail=True), model_id=MODEL, previous=previous)
    kept = _narratives(changed)
    assert res.kept == len(kept) and res.stubs == 0
    for p in kept.values():
        assert p.sections[0].body_md == f"Narrative for {p.title}." and p.freshness == "stale"
        assert p.confidence == CONFIDENCE_PROSE and p.prose_ref == previous[p.page_id].prose_ref


def test_prose_truncation_is_invalid_and_not_asked_again(built, tmp_path: Path) -> None:
    memo = ResponseCache(tmp_path / "llm_cache.db")
    pages, llm = _fresh(built), Stub(truncated=True)
    res = apply_prose(pages, llm, model_id=MODEL, memo=memo)
    asked = len(llm.calls)
    assert asked and res.invalid == asked and res.stubs == asked
    again = Stub()                                           # an identical request is not sent again
    res = apply_prose(_fresh(built), again, model_id=MODEL, memo=memo)
    assert again.calls == [] and res.invalid == asked
    empty = Stub(text="  \n## only a heading\n")
    assert apply_prose(_fresh(built), empty, model_id="m2", memo=memo).invalid == len(empty.calls)


def test_prose_stops_at_the_budget(built) -> None:
    llm = Stub()
    res = apply_prose(_fresh(built), llm, model_id=MODEL, budget_ok=lambda: False)
    assert llm.calls == [] and res.skipped_budget == res.stubs > 0


def test_prose_scan_end_to_end(tmp_path: Path) -> None:
    """`discover scan --prose --yes`: narratives land in the stored pages; spend and reuse are recorded."""
    from tests.cli.test_cost_gate import _stub_llm
    from tests.golden.test_golden import GOLDEN_REPOS, _build_repo

    from ai_discovery.cli import app

    repo = _build_repo(tmp_path / "repo", GOLDEN_REPOS["java-spring-jsp"])
    (repo / ".discovery").mkdir()
    (repo / ".discovery" / "config.yaml").write_text("provider: bedrock\n")
    stack, stub = _stub_llm()
    with stack:
        res = CliRunner().invoke(app, ["--json", "-C", str(repo), "scan", "--prose", "--yes"])
    assert res.exit_code == 0, res.output
    out = json.loads(res.stdout)
    pages_hook = next(h for h in out["hooks"] if h["name"] == "pages")
    assert "prose:" in pages_hook["detail"] and pages_hook["ok"]
    conn = sqlite3.connect(repo / ".discovery" / "discovery.db")
    try:
        sid = conn.execute("SELECT scan_id FROM current_scan").fetchone()[0]
        rows = conn.execute("SELECT type, confidence, prose_ref, body_md FROM pages WHERE scan_id = ?",
                            (sid,)).fetchall()
        tiers = {r[0] for r in conn.execute("SELECT tier FROM llm_costs WHERE scan_id = ?", (sid,))}
    finally:
        conn.close()
    narrated = [r for r in rows if r[0] in PROSE_TYPES]
    assert narrated and all(r[2] and r[3].startswith(f"## {NARRATIVE}") for r in narrated)
    assert all(r[2] is None for r in rows if r[0] not in PROSE_TYPES)
    assert stub.call_counts().get("invoke:tier2", 0) >= len(narrated)     # one standard-role call per page
    assert "page_prose" not in tiers                         # the stub reports no spend


def test_prose_runner_records_spend_and_degradations(built, tmp_path: Path) -> None:
    from ai_discovery.config import DiscoveryConfig
    from ai_discovery.pages.prose import runner

    class Metered(Stub):
        def get_costs(self):
            return {"tier2": {"model": "m", "calls": len(self.calls), "tokens_in": 10, "tokens_out": 5,
                              "est_usd": 0.01}}

        def total_cost_usd(self):
            return 0.01 * len(self.calls)

    conn = sqlite3.connect(":memory:")
    conn.executescript("CREATE TABLE llm_costs (scan_id INT, tier TEXT, model TEXT, calls INT, tokens_in INT, "
                       "tokens_out INT, est_usd REAL, UNIQUE(scan_id, tier));"
                       "CREATE TABLE degradations (scan_id INT, stage TEXT, kind TEXT, count INT, sample_json TEXT, "
                       "PRIMARY KEY (scan_id, stage, kind));"
                       "INSERT INTO llm_costs VALUES (7, 'tier2', 'm', 3, 1, 1, 0.5);")
    layout = SimpleNamespace(cache_dir=tmp_path)
    detail = runner(DiscoveryConfig(provider="bedrock", budget_limit_usd=0.53), layout, conn, 7, llm=Metered())(
        _fresh(built), {})
    rows = dict(conn.execute("SELECT tier, est_usd FROM llm_costs WHERE scan_id = 7").fetchall())
    assert rows == {"tier2": 0.5, "page_prose": 0.01}                  # the pipeline's row is untouched
    # $0.50 already spent of $0.53: the budget stops the layer after a few calls
    assert "skipped budget" in detail
    assert conn.execute("SELECT count FROM degradations WHERE kind = 'prose_budget_skipped'").fetchone()[0] > 0
