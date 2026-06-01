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


def build_screen_map(menu_items: list[MenuItem], repo_path: Path) -> list[Screen]:
    """
    Build list of Screen objects from menu items.

    Traverses menu tree and creates a Screen for each leaf menu item.
    """
    screens = []

    def traverse(items: list[MenuItem], path: list[str]):
        for item in items:
            current_path = path + [item.label]

            if item.children:
                # Non-leaf: recurse
                traverse(item.children, current_path)
            else:
                # Leaf: create screen
                screen = Screen(
                    screen_id=item.id,
                    menu_path=current_path,
                    label=item.label,
                    path=item.path,
                    permissions=item.roles,
                    metadata=item.metadata,
                )
                screens.append(screen)

    traverse(menu_items, [])
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
