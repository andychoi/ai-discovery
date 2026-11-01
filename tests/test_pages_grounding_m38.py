"""Groundedness of page prose (roadmap item M3.8)."""

from __future__ import annotations

import copy
import shutil
import threading
from types import SimpleNamespace

import pytest

from ai_discovery.pages.grounding import Grounder
from ai_discovery.pages.model import CONFIDENCE_PROSE, CONFIDENCE_STUB
from ai_discovery.pages.prose import NARRATIVE, STUB_PREFIX, apply_prose, prose_key
from ai_discovery.pages.registry import BuildContext, run_builders
from tests.test_builders_flows_m34 import FIXTURES, _git_scan

NODES = [
    {"file_path": "/r/src/main/java/com/example/service/OrderService.java", "name": "createOrder",
     "qualified_name": "com.example.service.OrderService.createOrder", "framework_hints": {}},
    {"file_path": "/r/src/main/java/com/example/web/OrderController.java", "name": "list",
     "qualified_name": "com.example.web.OrderController.list",
     "framework_hints": {"route": "/api/orders", "method": "GET"}},
    {"file_path": "/r/src/main/java/com/example/entity/Order.java", "name": "Order",
     "qualified_name": "com.example.entity.Order", "framework_hints": {"table": "orders"}},
]
G = Grounder(NODES, ["flow:com.example.web.OrderController.list", "table:orders"])


# ── the checks ─────────────────────────────────────────────────────────────

def test_grounding_accepts_what_was_shown_or_indexed() -> None:
    text = ("`OrderController.list` serves `/api/orders`, calls `createOrder` and writes `orders` via "
            "`com.example.entity.Order` in `OrderService.java` (`src/main/java/com/example/web/OrderController.java:12`). "
            "See [the flow](flow:com.example.web.OrderController.list); status `200`; the `ShownOnlyName` field.")
    g = G.check(text, shown="facts: ShownOnlyName")
    assert g.ok, g.unverified


def test_grounding_flags_invented_references_files_and_links() -> None:
    text = ("`PaymentGatewayService.charge` runs nightly from `src/jobs/Nightly.java:4`; "
            "see [docs](https://example.com) and [the flow](flow:nope.Missing.run).")
    g = G.check(text, shown="")
    assert g.unverified == ["PaymentGatewayService.charge", "src/jobs/Nightly.java", "https://example.com",
                            "flow:nope.Missing.run"]


def test_grounding_sanitizes_or_drops_mermaid() -> None:
    text = ("Intro.\n\n```mermaid\ngraph TD\n  A-->B\n  click A \"javascript:alert(1)\"\n%%{init: {}}%%\n```\n\n"
            "```mermaid\nnot-a-diagram\n  X\n```\nEnd.")
    g = G.check(text, shown="")
    assert g.ok and g.dropped_diagrams == 1
    assert "```mermaid\ngraph TD\n  A-->B\n```" in g.text
    assert "click" not in g.text and "%%{" not in g.text and "not-a-diagram" not in g.text


# ── in the prose layer ───────────────────────────────────────────────────────

class Scripted:
    """Answers from a script, one per call; records prompts."""

    def __init__(self, *answers: str):
        self.answers, self.prompts, self._lock = list(answers), [], threading.Lock()

    def invoke(self, tier, prompt, max_tokens=4096, *, system=""):
        with self._lock:
            self.prompts.append(prompt)
            text = self.answers[min(len(self.prompts) - 1, len(self.answers) - 1)]
        return SimpleNamespace(text=text, truncated=False)


@pytest.fixture(scope="module")
def scanned(tmp_path_factory):
    repo = tmp_path_factory.mktemp("grounding") / "repo"
    shutil.copytree(FIXTURES / "projects" / "spring-boot-app", repo)
    conn, sid = _git_scan(repo)
    pages = run_builders(BuildContext(conn=conn, scan_id=sid, repo=repo))
    one = [copy.deepcopy(next(p for p in pages if p.type == "flow" and "OrderController.create" in p.page_id))]
    return conn, sid, one


def _grounder(scanned):
    conn, sid, pages = scanned
    return Grounder.from_db(conn, sid, [p.page_id for p in pages], [p.title for p in pages])


def test_grounding_reasks_once_then_accepts(scanned) -> None:
    pages = copy.deepcopy(scanned[2])
    llm = Scripted("It calls `InventoryReservationService.reserve` first.",
                   "`OrderController.create` saves the order through `OrderService.createOrder`.")
    res = apply_prose(pages, llm, model_id="m", ground=_grounder(scanned).check)
    assert (res.written, res.reasked, res.ungrounded) == (1, 1, 0) and len(llm.prompts) == 2
    assert "`InventoryReservationService.reserve`" in llm.prompts[1] and "not in the facts" in llm.prompts[1]
    page = pages[0]
    assert page.sections[0].heading == NARRATIVE and "OrderService.createOrder" in page.sections[0].body_md
    assert page.confidence == CONFIDENCE_PROSE


def test_grounding_failure_stubs_or_keeps_existing_prose(scanned) -> None:
    pages = copy.deepcopy(scanned[2])
    llm = Scripted("Calls `FraudCheck.verify`.")
    res = apply_prose(pages, llm, model_id="m", ground=_grounder(scanned).check)
    assert (res.ungrounded, res.stubs, len(llm.prompts)) == (1, 1, 2)          # one re-ask, then stub
    stub = pages[0].sections[0].body_md
    assert stub.startswith(STUB_PREFIX) and "`FraudCheck.verify`" in stub and pages[0].confidence == CONFIDENCE_STUB
    # earlier good prose is kept (stale) instead
    good = copy.deepcopy(scanned[2])
    apply_prose(good, Scripted("`OrderController.create` creates an order."), model_id="m",
                ground=_grounder(scanned).check)
    again = copy.deepcopy(scanned[2])
    again[0].facts["_changed"] = 1
    res = apply_prose(again, Scripted("Calls `FraudCheck.verify`."), model_id="m",
                      previous={good[0].page_id: good[0]}, ground=_grounder(scanned).check)
    assert res.kept == 1 and again[0].freshness == "stale"
    assert again[0].sections[0].body_md == "`OrderController.create` creates an order."


def test_grounding_rechecks_reused_prose(scanned) -> None:
    page = copy.deepcopy(scanned[2][0])
    page.sections.insert(0, type(page.sections[0])(NARRATIVE, "Uses `RemovedSinceLastScan.go`."))
    page.prose_ref = prose_key(page, "m")
    fresh = copy.deepcopy(scanned[2])
    llm = Scripted("`OrderController.create` creates an order.")
    res = apply_prose(fresh, llm, model_id="m", previous={page.page_id: page}, ground=_grounder(scanned).check)
    assert res.reused == 0 and res.written == 1 and len(llm.prompts) == 1     # same key, but no longer grounded
