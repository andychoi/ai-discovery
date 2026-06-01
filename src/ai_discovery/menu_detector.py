"""
Menu system detection and parsing.

Supports multiple menu formats:
- JSON/YAML menu files (menu.json, navigation.json, etc.)
- TypeScript/JavaScript constants (export const MENU = [...])
- Framework routing configs (Vue Router, React Router, etc.)

Auto-detects which format applies and extracts screen definitions.
"""

import json
import logging
import re
from abc import ABC, abstractmethod
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Any, Optional
import yaml
from .route_parser import parse_route_file, RouteNode
from .webforms_extractor import extract_webforms_page
from .jsp_extractor import extract_jsp_page
from .repo.lang_detector import _SKIP_DIRS

logger = logging.getLogger(__name__)


@dataclass
class MenuItem:
    """Represents a single menu item."""

    id: str  # kebab-case identifier
    label: str  # Display label
    path: str  # URL or route path
    icon: Optional[str] = None
    roles: list[str] = field(default_factory=list)  # Required roles
    children: list["MenuItem"] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict:
        """Convert to dictionary, excluding empty fields."""
        result = {
            "id": self.id,
            "label": self.label,
            "path": self.path,
        }
        if self.icon:
            result["icon"] = self.icon
        if self.roles:
            result["roles"] = self.roles
        if self.children:
            result["children"] = [child.to_dict() for child in self.children]
        if self.metadata:
            result["metadata"] = self.metadata
        return result


@dataclass
class Screen:
    """Represents a detected screen."""

    screen_id: str  # kebab-case from menu path
    menu_path: list[str]  # Breadcrumb [root, ..., leaf]
    label: str  # Screen title
    path: str  # Route/URL path
    fe_component: Optional[str] = None  # Path to FE component
    crud_profile: Optional[str] = None  # Read-only, Create/Edit, Manage, etc.
    interaction_mode: Optional[str] = None  # inquiry, monitoring, workflow_step, etc.
    permissions: list[str] = field(default_factory=list)  # Required roles
    related_screens: list[str] = field(default_factory=list)  # Other screen IDs
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict:
        """Convert to dictionary."""
        return asdict(self)


def _humanize(name: str) -> str:
    """Turn a component identifier into a human label, e.g.
    'CustomerListPage' -> 'Customer List'. Generic UI suffixes
    (Page/View/Screen/Component) are dropped only when other words remain."""
    spaced = re.sub(r"(?<=[a-z0-9])(?=[A-Z])", " ", name).replace("-", " ").replace("_", " ")
    words = [w for w in spaced.split() if w]
    if len(words) > 1 and words[-1] in ("Page", "View", "Screen", "Component"):
        words = words[:-1]
    return " ".join(words).title() or name


def _routenode_to_menuitem(rn: RouteNode, is_route_format: bool) -> "MenuItem":
    label = rn.title or (_humanize(rn.component) if rn.component else None) \
        or (rn.path.strip("/").split("/")[-1].replace(":", "") if rn.path else "") or "Untitled"
    metadata = dict(rn.raw)
    metadata["component_source"] = rn.component_source
    metadata["redirect_to"] = rn.redirect_to
    metadata["is_catch_all"] = rn.is_catch_all
    if is_route_format:
        has_component = bool(rn.component or rn.component_source)
        metadata["is_screen"] = bool(has_component and not rn.is_catch_all and not rn.redirect_to)
    children = [_routenode_to_menuitem(c, is_route_format) for c in rn.children]
    return MenuItem(
        id=JsonYamlDetector._slugify(rn.path or label),
        label=label, path=rn.path or "", roles=rn.roles, children=children, metadata=metadata,
    )


class MenuDetector(ABC):
    """Base class for menu detection strategies."""

    @abstractmethod
    def detect(self, repo_path: Path) -> Optional[list[MenuItem]]:
        """
        Detect and parse menu from repository.

        Returns list of root menu items, or None if format not found.
        """
        pass


class JsonYamlDetector(MenuDetector):
    """Detect menus from JSON/YAML files."""

    MENU_FILE_NAMES = [
        "menu.json",
        "navigation.json",
        "routes.json",
        "menu.yaml",
        "navigation.yaml",
        "routes.yaml",
    ]

    SEARCH_PATHS = [
        Path("."),
        Path("config"),
        Path("src"),
        Path("src/config"),
        Path("public"),
    ]

    def detect(self, repo_path: Path) -> Optional[list[MenuItem]]:
        """Detect JSON/YAML menu file."""
        repo = Path(repo_path)

        for search_dir in self.SEARCH_PATHS:
            for filename in self.MENU_FILE_NAMES:
                filepath = repo / search_dir / filename
                if filepath.exists():
                    try:
                        return self._parse_file(filepath)
                    except Exception as e:
                        print(f"Warning: Failed to parse {filepath}: {e}")

        return None

    def _parse_file(self, filepath: Path) -> list[MenuItem]:
        """Parse JSON/YAML menu file."""
        content = filepath.read_text()

        if filepath.suffix.lower() in [".json"]:
            data = json.loads(content)
        else:  # .yaml, .yml
            data = yaml.safe_load(content)

        # Handle different root structures
        if isinstance(data, list):
            items = data
        elif isinstance(data, dict):
            # Try common top-level keys
            items = data.get("menu") or data.get("navigation") or data.get("routes") or data.get("items")
            if not items:
                items = list(data.values()) if data else []
        else:
            items = []

        return [self._build_menu_item(item) for item in items]

    def _build_menu_item(self, data: dict) -> MenuItem:
        """Build MenuItem from dict."""
        return MenuItem(
            id=data.get("id") or self._slugify(data.get("label", "unknown")),
            label=data.get("label") or data.get("name") or data.get("title") or "Untitled",
            path=data.get("path") or data.get("route") or "",
            icon=data.get("icon"),
            roles=data.get("roles") or data.get("permissions") or [],
            children=[
                self._build_menu_item(child)
                for child in (data.get("children") or data.get("submenu") or [])
            ],
            metadata=data.get("metadata") or data.get("extra") or {},
        )

    @staticmethod
    def _slugify(text: str) -> str:
        """Convert text to kebab-case slug."""
        text = re.sub(r"[^\w\s-]", "", text.lower())
        text = re.sub(r"[-\s]+", "-", text)
        return text.strip("-")


class TypeScriptConstantDetector(MenuDetector):
    """Detect menus from TypeScript/JavaScript constants."""

    MENU_CONSTANT_PATTERNS = [
        "export const MENU",
        "export const NAVIGATION",
        "export const ROUTES",
        "export const SIDEBAR",
        "const MENU",
        "const NAVIGATION",
    ]

    SEARCH_PATTERNS = [
        "src/**/*.ts",
        "src/**/*.tsx",
        "src/**/*.js",
        "src/**/*.jsx",
    ]

    def detect(self, repo_path: Path) -> Optional[list[MenuItem]]:
        """Detect TypeScript/JavaScript menu constants."""
        # Simplified: look for common patterns in src/
        src_path = Path(repo_path) / "src"

        if not src_path.exists():
            return None

        # Search for files containing menu constants
        for pattern in self.SEARCH_PATTERNS:
            for filepath in Path(repo_path).glob(pattern):
                if self._is_menu_file(filepath):
                    try:
                        return self._parse_file(filepath)
                    except Exception as e:
                        print(f"Warning: Failed to parse {filepath}: {e}")

        return None

    def _is_menu_file(self, filepath: Path) -> bool:
        """Check if file likely contains menu constant."""
        content = filepath.read_text(errors="ignore")
        return any(pattern in content for pattern in self.MENU_CONSTANT_PATTERNS)

    def _parse_file(self, filepath: Path) -> Optional[list[MenuItem]]:
        root = parse_route_file(filepath, "ts-const")
        if root is None or not root.children:
            return None
        return [_routenode_to_menuitem(c, is_route_format=False) for c in root.children]


class FrameworkRoutingDetector(MenuDetector):
    """Detect screens from framework routing configurations."""

    def detect(self, repo_path: Path) -> Optional[list[MenuItem]]:
        """Detect Vue/React/Angular routing configs."""
        repo = Path(repo_path)

        # Try Vue Router
        vue_routes = self._detect_vue_router(repo)
        if vue_routes:
            return vue_routes

        # Try React Router
        react_routes = self._detect_react_router(repo)
        if react_routes:
            return react_routes

        # Try Angular routing
        angular_routes = self._detect_angular_routing(repo)
        if angular_routes:
            return angular_routes

        return None

    def _detect_vue_router(self, repo: Path) -> Optional[list[MenuItem]]:
        for fp in [repo/"src"/"router"/"routes.ts", repo/"src"/"router"/"index.ts", repo/"src"/"router"/"routes.js"]:
            if fp.exists():
                root = parse_route_file(fp, "vue")
                if root and root.children:
                    return [_routenode_to_menuitem(c, is_route_format=True) for c in root.children]
        return None

    def _detect_react_router(self, repo: Path) -> Optional[list[MenuItem]]:
        for fp in [repo/"src"/"router.tsx", repo/"src"/"routes.tsx", repo/"src"/"App.tsx", repo/"src"/"router"/"index.tsx"]:
            if fp.exists():
                root = parse_route_file(fp, "react")
                if root and root.children:
                    return [_routenode_to_menuitem(c, is_route_format=True) for c in root.children]
        return None

    def _detect_angular_routing(self, repo: Path) -> Optional[list[MenuItem]]:
        for fp in [repo/"src"/"app"/"app-routing.module.ts", repo/"src"/"app"/"app.routes.ts"]:
            if fp.exists():
                root = parse_route_file(fp, "angular")
                if root and root.children:
                    return [_routenode_to_menuitem(c, is_route_format=True) for c in root.children]
        return None


class WebFormsMenuDetector(MenuDetector):
    """Build screens from ASP.NET WebForms .aspx pages (folder hierarchy = menu).

    Fallback for apps with no JS menu/router. .ascx user controls are excluded
    (they are partial components, not top-level screens)."""

    def detect(self, repo_path: Path) -> Optional[list[MenuItem]]:
        repo = Path(repo_path)
        aspx = [
            p for p in sorted(repo.rglob("*.aspx"))
            if not (_SKIP_DIRS & set(p.parts))
        ]
        if not aspx:
            return None

        root: dict = {"_dirs": {}, "_pages": []}
        for path in aspx:
            rel = path.relative_to(repo)
            parts = rel.parts[:-1]
            node = root
            for seg in parts:
                node = node["_dirs"].setdefault(seg, {"_dirs": {}, "_pages": []})
            node["_pages"].append(path)

        def build(node: dict, url_prefix: str) -> list[MenuItem]:
            items: list[MenuItem] = []
            for seg, child in sorted(node["_dirs"].items()):
                items.append(MenuItem(
                    id=JsonYamlDetector._slugify(seg),
                    label=_humanize(seg),
                    path=f"{url_prefix}/{seg}",
                    metadata={"is_screen": False},
                    children=build(child, f"{url_prefix}/{seg}"),
                ))
            for path in sorted(node["_pages"]):
                page = extract_webforms_page(path)
                rel = path.relative_to(repo)
                label = (page.title if page and page.title else None) or _humanize(path.stem)
                items.append(MenuItem(
                    id=JsonYamlDetector._slugify(str(rel)),
                    label=label,
                    path=f"{url_prefix}/{path.name}",
                    metadata={
                        "is_screen": True,
                        "component_source": str(rel),
                        "code_behind_class": page.code_behind_class if page else None,
                        "master_page": page.master_page if page else None,
                        "framework": "webforms",
                    },
                ))
            return items

        return build(root, "")


class JspMenuDetector(MenuDetector):
    """Build screens from JSP pages (.jsp/.jspx; folder hierarchy = menu).

    Fallback for apps with no JS menu/router. .tag/.tagx tag files are excluded
    (they are reusable components, not top-level screens)."""

    def detect(self, repo_path: Path) -> Optional[list[MenuItem]]:
        repo = Path(repo_path)
        pages = [
            p for p in sorted(list(repo.rglob("*.jsp")) + list(repo.rglob("*.jspx")))
            if not (_SKIP_DIRS & set(p.parts))
        ]
        if not pages:
            return None

        root: dict = {"_dirs": {}, "_pages": []}
        for path in pages:
            rel = path.relative_to(repo)
            node = root
            for seg in rel.parts[:-1]:
                node = node["_dirs"].setdefault(seg, {"_dirs": {}, "_pages": []})
            node["_pages"].append(path)

        def build(node: dict, url_prefix: str) -> list[MenuItem]:
            items: list[MenuItem] = []
            for seg, child in sorted(node["_dirs"].items()):
                items.append(MenuItem(
                    id=JsonYamlDetector._slugify(seg),
                    label=seg,
                    path=f"{url_prefix}/{seg}",
                    metadata={"is_screen": False},
                    children=build(child, f"{url_prefix}/{seg}"),
                ))
            for path in sorted(node["_pages"]):
                page = extract_jsp_page(path)
                rel = path.relative_to(repo)
                label = (page.title if page and page.title else None) or _humanize(path.stem)
                items.append(MenuItem(
                    id=JsonYamlDetector._slugify(str(rel)),
                    label=label,
                    path=f"{url_prefix}/{path.name}",
                    metadata={
                        "is_screen": True,
                        "component_source": str(rel),
                        "bean_classes": page.bean_classes if page else [],
                        "form_actions": page.form_actions if page else [],
                        "framework": "jsp",
                    },
                ))
            return items

        return build(root, "")


class HybridMenuDetector:
    """
    Hybrid detector that tries multiple strategies.

    Returns the first format that matches.
    """

    def __init__(self):
        self.detectors = [
            JsonYamlDetector(),
            TypeScriptConstantDetector(),
            FrameworkRoutingDetector(),
            WebFormsMenuDetector(),
            JspMenuDetector(),
        ]

    def detect(self, repo_path: Path) -> Optional[list[MenuItem]]:
        """
        Detect menu using all available strategies.

        Returns list of root MenuItem objects, or None if no menu found.
        """
        repo = Path(repo_path)

        for detector in self.detectors:
            try:
                result = detector.detect(repo)
                if result:
                    return result
            except Exception as e:
                print(f"Warning: {detector.__class__.__name__} failed: {e}")

        return None


def _join_path(parent: str, child: str) -> str:
    if not child:
        return parent or ""
    if child.startswith("/"):
        return child
    if not parent or parent == "/":
        return "/" + child.lstrip("/")
    return parent.rstrip("/") + "/" + child.lstrip("/")


def build_screen_map(menu_items: list[MenuItem], repo_path: Path) -> list[Screen]:
    """Build Screen objects from a MenuItem tree.

    Format-aware: a MenuItem with metadata['is_screen'] honors that flag (route
    formats); without it, the leaf rule applies (JSON/YAML + TS-const menus).
    Pathless/component-less wrappers become breadcrumb ancestors. Redirect items
    are not screens; their source path is attached to the target screen's
    metadata['redirect_aliases'].
    """
    screens: list[Screen] = []
    redirects: list[tuple[str, str, str]] = []
    by_full_path: dict[str, Screen] = {}
    seen_ids: set[str] = set()

    def is_screen(item: MenuItem) -> bool:
        meta = item.metadata or {}
        if "is_screen" in meta:
            return bool(meta["is_screen"])
        return not item.children

    def unique_id(base: str) -> str:
        base_id = base or "screen"
        sid = base_id
        i = 2
        while sid in seen_ids:
            sid = f"{base_id}-{i}"
            i += 1
        seen_ids.add(sid)
        return sid

    def traverse(items: list[MenuItem], crumb: list[str], parent_path: str):
        for item in items:
            meta = item.metadata or {}
            full_path = _join_path(parent_path, item.path)
            current_crumb = crumb + [item.label]
            is_redirect = bool(meta.get("redirect_to"))
            if is_redirect:
                redirects.append((full_path, parent_path, meta["redirect_to"]))
            if is_screen(item) and not is_redirect:
                sid = unique_id(item.id or JsonYamlDetector._slugify(full_path))
                screen = Screen(
                    screen_id=sid, menu_path=current_crumb, label=item.label, path=full_path,
                    fe_component=meta.get("component_source") or None,
                    permissions=item.roles,
                    metadata={k: v for k, v in meta.items() if k not in ("is_screen",)},
                )
                screens.append(screen)
                by_full_path[full_path] = screen
            if item.children:
                traverse(item.children, current_crumb, full_path)

    traverse(menu_items, [], "")

    for src_path, src_parent, target in redirects:
        resolved = target if target.startswith("/") else _join_path(src_parent, target)
        target_screen = by_full_path.get(resolved) or by_full_path.get(target)
        if target_screen is not None:
            target_screen.metadata.setdefault("redirect_aliases", []).append(src_path)
    return screens


def detect_and_build_screens(repo_path: Path) -> tuple[Optional[list[MenuItem]], list[Screen]]:
    """
    Detect menu structure and build screen map.

    Returns (menu_items, screens).
    """
    detector = HybridMenuDetector()
    menu_items = detector.detect(repo_path)

    if not menu_items:
        return None, []

    screens = build_screen_map(menu_items, repo_path)
    return menu_items, screens
