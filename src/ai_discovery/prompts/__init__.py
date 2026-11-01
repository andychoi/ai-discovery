"""Prompt templates (roadmap item M3.7.a).

Each prompt has an id and a version: both go into the prose cache key (ADR-0002), so editing a template
re-asks only the pages it writes. Bump ``PAGE_PROSE_VERSION`` whenever the wording below changes.
"""

from __future__ import annotations

PAGE_PROSE_ID = "page_prose"
PAGE_PROSE_VERSION = "1"

PAGE_PROSE_SYSTEM = (
    "You write the short orientation narrative at the top of a page in a codebase wiki. The page's structure "
    "(which symbols, routes, tables and steps it covers) was derived from the code and is correct; you only "
    "name and narrate it. Rules: use only the facts and page text you are given; never invent behaviour, "
    "files, tables or numbers; write identifiers in backticks exactly as given; plain markdown paragraphs, no "
    "headings, no lists of every item, at most 120 words; if the facts are thin, say briefly what the page "
    "covers and stop."
)

PAGE_PROSE_FOCUS = {
    "overview": "Say what this system appears to be for and how it is organised: its languages, main domains, "
                "entry points and the external systems it talks to.",
    "domain": "Name the business capability this domain implements and summarise what its entry points do and "
              "which data they touch.",
    "screen": "Explain what a user does on this screen and what happens in the backend when they do it.",
    "flow": "Narrate this flow in order, from its trigger to its data and system effects, and say where the "
            "trace stops and why that matters for a reader.",
    "entity": "Describe this entity's lifecycle: its states, what moves it between them, and the rules that "
              "guard the transitions.",
    "onboarding": "Welcome a newcomer to this domain: where to start reading and why the learning path goes in "
                  "this order.",
}

PAGE_PROSE_TEMPLATE = """{focus}

Page: {title} (a {page_type} page)

Facts (JSON):
{facts}

Structural page text (markdown, may be cut short):
{body}

Write the narrative now."""
