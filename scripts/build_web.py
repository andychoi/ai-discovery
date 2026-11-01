#!/usr/bin/env python3
"""Build the wiki SPA into src/ai_discovery/server/static (roadmap item M4.2).

    python scripts/build_web.py            # npm ci (or install) + typecheck + vite build + checks
    python scripts/build_web.py --check    # only check an existing build

Node is needed here (build time) only; `discover serve` serves the result with
no Node on PATH. The check fails if any built HTML/CSS/JS would *load* an
external http(s) resource (script/link/img src, CSS url()/@import, JS
import()) — the wiki must work offline / air-gapped. Plain URL strings inside
libraries (docs links, XML namespaces) are not loads and are allowed.
"""

from __future__ import annotations

import argparse
import re
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
WEB = ROOT / "web"
STATIC = ROOT / "src" / "ai_discovery" / "server" / "static"
# M3.10: the `discover export --format html` bundle — one classic script, relative paths (file://)
STATIC_EXPORT = ROOT / "src" / "ai_discovery" / "server" / "static_export"
_MODULE_TAG = re.compile(r'<script type="module" crossorigin src="(\./assets/app\.js)"></script>')

_EXTERNAL = r"""(?:https?:)?//(?!localhost\b|127\.0\.0\.1\b)[A-Za-z0-9.-]+\.[A-Za-z]{2,}"""
LOAD_PATTERNS: dict[str, list[re.Pattern[str]]] = {
    ".html": [re.compile(rf"""\b(?:src|href)\s*=\s*["']?{_EXTERNAL}""", re.I)],
    ".css": [re.compile(rf"""url\(\s*["']?{_EXTERNAL}""", re.I),
             re.compile(rf"""@import\s+(?:url\()?\s*["']{_EXTERNAL}""", re.I)],
    ".js": [re.compile(rf"""\bimport\s*\(\s*["'`]{_EXTERNAL}"""),
            re.compile(rf"""\bfrom\s*["']{_EXTERNAL}"""),
            re.compile(rf"""\bimport\s*["']{_EXTERNAL}"""),
            re.compile(rf"""url\(\s*["']?{_EXTERNAL}""")],
}


def find_external_loads(root: Path) -> list[str]:
    """``path: snippet`` for every external resource load in a build directory."""
    problems: list[str] = []
    for path in sorted(root.rglob("*")):
        patterns = LOAD_PATTERNS.get(path.suffix.lower())
        if not patterns or not path.is_file():
            continue
        text = path.read_text(encoding="utf-8", errors="replace")
        for pat in patterns:
            for m in pat.finditer(text):
                snippet = text[max(0, m.start() - 20): m.end() + 30].replace("\n", " ")
                problems.append(f"{path.relative_to(root)}: …{snippet}…")
    return problems


def _run(cmd: list[str]) -> None:
    print("+", " ".join(cmd), flush=True)
    if subprocess.run(cmd, cwd=WEB).returncode != 0:
        sys.exit(f"web build step failed: {' '.join(cmd[1:])}")


def build() -> None:
    npm = shutil.which("npm")
    if npm is None:
        sys.exit("npm not found: Node >= 18 is needed to build the SPA (not to run it)")
    _run([npm, "ci"] if (WEB / "package-lock.json").exists() else [npm, "install"])
    _run([npm, "run", "test"])
    _run([npm, "run", "build"])
    patch_export_index()


def patch_export_index() -> None:
    """Browsers refuse module scripts from file://: load data.js, then the bundle as a classic script."""
    index = STATIC_EXPORT / "index.html"
    html = index.read_text(encoding="utf-8")
    if "./data.js" not in html:
        html, n = _MODULE_TAG.subn(r'<script src="./data.js"></script>\n    <script defer src="\1"></script>', html)
        if n != 1:
            sys.exit(f"unexpected export index.html (no module script tag to rewrite): {index}")
        index.write_text(html, encoding="utf-8")


def check() -> int:
    for root in (STATIC, STATIC_EXPORT):
        if not (root / "index.html").is_file():
            print(f"no build at {root} (run scripts/build_web.py)", file=sys.stderr)
            return 1
    if 'type="module"' in (STATIC_EXPORT / "index.html").read_text(encoding="utf-8"):
        print("static export index.html still loads a module script (file:// refuses it)", file=sys.stderr)
        return 1
    problems = find_external_loads(STATIC) + find_external_loads(STATIC_EXPORT)
    if problems:
        print("built assets load external resources (must be bundled locally):", file=sys.stderr)
        for p in problems[:50]:
            print("  " + p, file=sys.stderr)
        return 1
    files = [p for p in STATIC.rglob("*") if p.is_file()]
    size = sum(p.stat().st_size for p in files) / 1024 / 1024
    print(f"ok: {len(files)} files, {size:.1f} MiB, no external loads → {STATIC.relative_to(ROOT)}")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--check", action="store_true", help="only verify an existing build")
    args = ap.parse_args()
    if not args.check:
        build()
    return check()


if __name__ == "__main__":
    sys.exit(main())
