"""Regenerate ``bench/agent/tasks.toml`` from the curated task list (roadmap item M5.7).

The task *selection* and *prompts* below are the curated, human-written part; gold
files are derived from each commit's diff with the ``mine.py`` filters. Run once
when assembling the task set; after the pre-registration is frozen, the TOML is the
record and this script must not be re-run.

    python -m bench.agent.build_tasks
"""

from __future__ import annotations

import json
import re
import subprocess
from pathlib import Path

from .mine import IGNORE, SOURCE_EXT, TEST_PATH

HERE = Path(__file__).parent
ROOT = HERE.parent.parent

REPOS = {
    "spring-petclinic": {"stack": "java", "url": "https://github.com/spring-projects/spring-petclinic.git",
                         "local": "../sample-apps/spring-petclinic"},
    "eshop": {"stack": "dotnet", "url": "https://github.com/dotnet/eShop.git", "local": "../sample-apps/eShop"},
    "node-express-realworld": {"stack": "node",
                               "url": "https://github.com/gothinkster/node-express-realworld-example-app.git",
                               "local": "bench/agent/.repos/node-express-realworld-example-app"},
}

# (task id, repo, commit, prompt) — prompts state the requirement like a ticket would, never a file name.
TASKS = [
    ("pc-page-bounds", "spring-petclinic", "500158f",
     "When a user asks for a page of the owner search results or of the veterinarian list that does not exist "
     "(below 1 or beyond the last page), redirect them to the first page instead of showing an empty or broken "
     "page. Keep the last-name search filter when redirecting the owner list."),
    ("pc-pet-association", "spring-petclinic", "676db04",
     "Fix two pet bugs: adding a pet to an owner must not create a duplicate when the same pet (same object, or "
     "same saved id) is added again, and adding a null pet must be ignored. Also reject pet names longer than 30 "
     "characters with a validation error on the name."),
    ("pc-unique-pet-names", "spring-petclinic", "e0db9b1",
     "Pet names must be unique per owner, even when two requests create pets at the same time. Enforce it in the "
     "database schema for every supported database, and when a duplicate slips through on save, show the "
     "'already exists' error on the pet form's name field instead of an error page."),
    ("pc-edit-pet-name", "spring-petclinic", "3a61275",
     "When editing an existing pet, reject the change if the new name is already used by another pet of the same "
     "owner (ignoring case), with an 'already exists' error on the name field."),
    ("pc-birthdate", "spring-petclinic", "3be2895",
     "Reject pets whose birth date is in the future, both when adding and when editing a pet, using the existing "
     "birth-date validation message; make sure that message is translated in the German and Spanish message "
     "bundles."),
    ("pc-confirmations", "spring-petclinic", "2fe9613",
     "After an owner is created or updated, a pet is added or updated, or a visit is booked, show a confirmation "
     "message on the owner details page; when creating or updating an owner fails, show an error message there "
     "instead."),
    ("es-mask-cards", "eshop", "0ace928",
     "Raw credit-card numbers must never be stored or logged. Mask card numbers to their last four digits in "
     "seeded and mock data and on incoming order requests before they are saved, and stop logging the full order "
     "request when an order is created."),
    ("es-chat-images", "eshop", "57b73de",
     "Product images in the AI chat assistant's answers are broken. Build product image links in the web app "
     "from the catalog item picture endpoint instead of storing a picture URI on catalog items, and remove the "
     "now-unused code in the Catalog API that filled that URI."),
    ("es-remove-card-ui", "eshop", "47835fa",
     "Remove the payment-card section from the web checkout page: the order is submitted without collecting card "
     "details in the UI, and the basket checkout data no longer carries them from the form."),
    ("es-catalog-openapi", "eshop", "aaf1857",
     "Document the Catalog API in OpenAPI: give every catalog endpoint a summary, description, tags and an "
     "operation id, describe every parameter (including pagination), and declare the correct response types."),
    ("nx-tags", "node-express-realworld", "8063559",
     "Add an API endpoint GET /api/tags that returns the distinct tags used across all articles as "
     "{tags: [...]}."),
    ("nx-follow", "node-express-realworld", "6ed76fb",
     "Let users follow and unfollow other users: authenticated POST and DELETE on /api/profiles/:username/follow "
     "return the profile, and profile responses say whether the current user follows that profile."),
    ("nx-comments", "node-express-realworld", "a9e04cd",
     "Add comments to articles under /api/articles/:slug/comments: list an article's comments, add a comment "
     "(authenticated), and delete your own comment."),
    ("nx-favorites", "node-express-realworld", "005afc5",
     "Let authenticated users favorite and unfavorite articles (POST and DELETE /api/articles/:slug/favorite), "
     "keep each article's favorites count up to date, and say in article responses whether the current user "
     "favorited it."),
    ("nx-profiles", "node-express-realworld", "99bba4c",
     "Add a public profile endpoint GET /api/profiles/:username returning the username, bio, image (with a "
     "default image when none is set) and following=false; respond 404 for unknown users."),
]


def _git(repo: Path, *args: str) -> str:
    return subprocess.run(["git", *args], cwd=repo, capture_output=True, text=True, check=True).stdout.strip()


def leaks(prompt: str, path: str) -> bool:
    """A prompt leaks a gold file when it names it: the full file name, or a CamelCase class-like stem."""
    name = Path(path).name
    stem = name.split(".")[0]
    return name in prompt or (bool(re.search(r"[a-z][A-Z]", stem)) and stem in prompt)


def build() -> str:
    lines = ["# Agent-loop eval tasks (roadmap item M5.7) — generated by bench/agent/build_tasks.py.",
             "# Frozen with the pre-registration; do not edit after freezing.",
             "# gold_files / gold_test_files = the commit's diff vs its parent, filtered by bench/agent/mine.py.", ""]
    for rid, r in REPOS.items():
        lines += [f"[repos.{json.dumps(rid)}]", f'stack = "{r["stack"]}"', f'url = "{r["url"]}"',
                  f'local = "{r["local"]}"', ""]
    for tid, rid, short, prompt in TASKS:
        repo = ROOT / REPOS[rid]["local"]
        gold = _git(repo, "rev-parse", short)
        base = _git(repo, "rev-parse", f"{gold}^")
        exts = SOURCE_EXT[REPOS[rid]["stack"]]
        names = [p for p in _git(repo, "diff", "--name-only", base, gold).splitlines()
                 if not IGNORE.search(p) and Path(p).suffix in exts]
        src = [p for p in names if not TEST_PATH.search(p)]
        tst = [p for p in names if TEST_PATH.search(p)]
        bad = [p for p in src + tst if leaks(prompt, p)]
        if bad:
            raise SystemExit(f"{tid}: prompt names gold files {bad}")
        lines += ["[[task]]", f'id = "{tid}"', f'repo = "{rid}"', f'base = "{base}"', f'gold = "{gold}"',
                  f"commit_subject = {json.dumps(_git(repo, 'log', '-1', '--format=%s', gold))}",
                  f"prompt = {json.dumps(prompt)}",
                  "gold_files = [" + ", ".join(json.dumps(x) for x in src) + "]",
                  "gold_test_files = [" + ", ".join(json.dumps(x) for x in tst) + "]", ""]
    return "\n".join(lines)


if __name__ == "__main__":
    out = HERE / "tasks.toml"
    out.write_text(build(), encoding="utf-8")
    print(f"wrote {out.relative_to(ROOT)}")
