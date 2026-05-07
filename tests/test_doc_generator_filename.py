"""Track 3 — output folder + filename restructure.

Filenames decouple from doc_id: the folder names the type, the project root
folder names the project, so per-file names only need domain (+ scenario for PF).
Bootstrap/config scenarios are filtered before disk write.
"""

from __future__ import annotations

import pytest

from ai_discovery.generators.doc_generator import (
    _is_bootstrap_scenario,
    _make_filename,
    _make_scenario_filename,
)


# ---------------------------------------------------------------------------
# Filename construction
# ---------------------------------------------------------------------------

class TestMakeFilename:
    def test_strips_project_prefix_and_type_suffix(self):
        assert _make_filename("Auth Service", "as-is") == "auth-service.md"
        assert _make_filename("Auth Service", "as-is-detail") == "auth-service.md"
        assert _make_filename("Auth Service", "as-is-schema") == "auth-service.md"

    def test_handles_punctuation_in_domain(self):
        assert _make_filename("auth/service.v2", "as-is") == "auth-service-v2.md"

    def test_falls_back_to_doc_type_when_domain_is_empty(self):
        assert _make_filename("", "as-is") == "as-is.md"


class TestMakeScenarioFilename:
    def test_domain_grouped_by_default(self):
        # Slugifier collapses non-alphanumeric runs; it does NOT split camelCase
        # (that would be too magical and break domain names that genuinely use
        # camelCase as identifiers).
        assert _make_scenario_filename("cart", "addtocart", 1) == "cart-addtocart.md"
        assert _make_scenario_filename("user", "signin", 2) == "user-signin.md"
        assert _make_scenario_filename("order", "place_order", 3) == "order-place-order.md"

    def test_drops_project_prefix_and_step_counter(self):
        # The legacy filename was `java-springboot-scenario-addtocart-42-process-flow.md`.
        # New: just `cart-addtocart.md`.
        assert (
            _make_scenario_filename("cart", "addtocart", 42)
            == "cart-addtocart.md"
        )

    def test_uses_index_fallback_when_scenario_id_blank(self):
        assert _make_scenario_filename("cart", "", 7) == "cart-flow-007.md"

    def test_no_domain_means_no_prefix(self):
        assert _make_scenario_filename(None, "signin", 1) == "signin.md"
        assert _make_scenario_filename("", "signin", 1) == "signin.md"


# ---------------------------------------------------------------------------
# Bootstrap-scenario filter
# ---------------------------------------------------------------------------

class TestIsBootstrapScenario:
    @pytest.mark.parametrize("scenario_id", [
        "main",
        "addresourcehandlers",
        "configure",
        "corsConfigurer",
        "addViewControllers",
        "apiDocket",
    ])
    def test_recognizes_known_bootstrap_names(self, scenario_id: str):
        assert _is_bootstrap_scenario(scenario_id) is True

    @pytest.mark.parametrize("scenario_id", [
        "scenario-main",
        "flow-main",
        "scenario-addresourcehandlers-20",  # legacy-format with counter
        "addresourcehandlers-20",
    ])
    def test_recognizes_bootstrap_with_prefixes_and_counters(self, scenario_id: str):
        assert _is_bootstrap_scenario(scenario_id) is True

    @pytest.mark.parametrize("scenario_id", [
        "addToCart",
        "signin",
        "placeOrder",
        "scenario-addtocart-42",  # real business scenario in legacy format
        "checkoutlist",
    ])
    def test_real_business_scenarios_pass_through(self, scenario_id: str):
        assert _is_bootstrap_scenario(scenario_id) is False

    def test_empty_scenario_id_treated_as_bootstrap(self):
        # An unnamed scenario can't be a meaningful business flow.
        assert _is_bootstrap_scenario("") is True
        assert _is_bootstrap_scenario(None) is True  # type: ignore[arg-type]
