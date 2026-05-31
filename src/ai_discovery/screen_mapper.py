"""
Screen-to-backend mapping.

Links detected screens to:
- Frontend API calls
- Backend controllers and services
- Database tables
- Batch jobs
- External interfaces
"""

import hashlib
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Any, Optional

from .menu_detector import Screen


@dataclass
class ApiCall:
    """Represents an API call made by a screen."""

    method: str  # GET, POST, PUT, DELETE, PATCH
    path: str  # /api/customer/search
    line_number: Optional[int] = None  # Source file line
    source_file: Optional[str] = None  # FE component file
    confidence: float = 0.5  # 0.0-1.0, based on detection heuristic


@dataclass
class BackendComponent:
    """Represents a backend component (controller, service, repository)."""

    component_type: str  # controller, service, repository
    class_name: str
    file_path: str
    line_number: Optional[int] = None
    methods: list[str] = field(default_factory=list)  # Method names
    confidence: float = 0.5


@dataclass
class ScreenMapping:
    """Enhanced screen with backend linkages."""

    screen: Screen
    fe_component: Optional[str] = None
    fe_api_calls: list[ApiCall] = field(default_factory=list)
    be_controllers: list[BackendComponent] = field(default_factory=list)
    be_services: list[BackendComponent] = field(default_factory=list)
    db_tables: list[str] = field(default_factory=list)
    batch_jobs: list[str] = field(default_factory=list)
    external_interfaces: list[str] = field(default_factory=list)
    source_files: list[str] = field(default_factory=list)  # All source files touched
    source_hashes: dict[str, str] = field(default_factory=dict)  # file_path -> SHA256

    def to_dict(self) -> dict:
        """Convert to dictionary for YAML/JSON serialization."""
        result = asdict(self)
        result["screen"] = asdict(self.screen)
        result["fe_api_calls"] = [asdict(call) for call in self.fe_api_calls]
        result["be_controllers"] = [asdict(comp) for comp in self.be_controllers]
        result["be_services"] = [asdict(comp) for comp in self.be_services]
        return result


class ScreenMapper:
    """Maps screens to backend components and databases."""

    def __init__(self, repo_path: Path):
        self.repo_path = Path(repo_path)

    def map_screen(self, screen: Screen) -> ScreenMapping:
        """
        Map a single screen to its backend components.

        This is a skeleton implementation; real implementation would:
        1. Find FE component file
        2. Extract API calls from FE code
        3. Resolve API calls to backend controllers
        4. Trace controllers → services → repositories
        5. Extract database tables
        6. Find related batch jobs
        7. Compute source file hashes
        """
        mapping = ScreenMapping(screen=screen)

        # 1. Find FE component
        mapping.fe_component = self._find_fe_component(screen)

        # 2. Extract API calls
        if mapping.fe_component:
            mapping.fe_api_calls = self._extract_api_calls(mapping.fe_component)
            mapping.source_files.append(mapping.fe_component)

        # 3. Resolve to backend (placeholder for now)
        for api_call in mapping.fe_api_calls:
            controllers = self._resolve_to_controller(api_call)
            mapping.be_controllers.extend(controllers)
            mapping.source_files.extend([c.file_path for c in controllers])

        # 4. Trace to services (placeholder)
        for controller in mapping.be_controllers:
            services = self._resolve_to_services(controller)
            mapping.be_services.extend(services)
            mapping.source_files.extend([s.file_path for s in services])

        # 5. Extract database tables
        mapping.db_tables = self._extract_db_tables(mapping.be_services)

        # 6. Find related batch jobs
        mapping.batch_jobs = self._find_related_batch_jobs(mapping.db_tables)

        # 7. Find external interfaces
        mapping.external_interfaces = self._find_external_interfaces(mapping.be_controllers)

        # 8. Compute hashes
        mapping.source_files = list(set(mapping.source_files))  # Deduplicate
        mapping.source_hashes = self._compute_source_hashes(mapping.source_files)

        return mapping

    def map_screens(self, screens: list[Screen]) -> list[ScreenMapping]:
        """Map multiple screens."""
        return [self.map_screen(screen) for screen in screens]

    def _find_fe_component(self, screen: Screen) -> Optional[str]:
        """
        Find FE component file for a screen.

        Heuristics:
        - If screen.path is set, look for file matching the path
        - Try common naming patterns: PascalCase + Page/View
        - Search in src/pages/, src/screens/, src/components/
        """
        if not screen.path:
            return None

        # Convert path to potential file names
        # /customer/search -> CustomerSearch.vue, customer-search.tsx, etc.
        parts = [p.capitalize() for p in screen.path.strip("/").split("/")]
        camel_name = "".join(parts)

        candidates = []

        # Try Vue
        for pattern in [
            f"src/pages/**/{camel_name}.vue",
            f"src/pages/**/{camel_name}Page.vue",
            f"src/screens/**/{camel_name}.vue",
        ]:
            matches = list(self.repo_path.glob(pattern))
            if matches:
                candidates.append(str(matches[0].relative_to(self.repo_path)))

        # Try React/TypeScript
        for pattern in [
            f"src/pages/**/{camel_name}.tsx",
            f"src/pages/**/{camel_name}Page.tsx",
            f"src/screens/**/{camel_name}.tsx",
        ]:
            matches = list(self.repo_path.glob(pattern))
            if matches:
                candidates.append(str(matches[0].relative_to(self.repo_path)))

        # Try Angular
        for pattern in [
            f"src/app/**/{screen.screen_id}/*.component.ts",
            f"src/app/**/screens/**/index.ts",
        ]:
            matches = list(self.repo_path.glob(pattern))
            if matches:
                candidates.append(str(matches[0].relative_to(self.repo_path)))

        return candidates[0] if candidates else None

    def _extract_api_calls(self, fe_component: str) -> list[ApiCall]:
        """
        Extract API calls from FE component.

        Looks for patterns like:
        - fetch('/api/...')
        - axios.get('/api/...')
        - this.http.get('/api/...')
        """
        filepath = self.repo_path / fe_component

        if not filepath.exists():
            return []

        content = filepath.read_text(errors="ignore")
        calls = []

        # Regex patterns for common API call patterns
        patterns = [
            r"(?:fetch|axios\.(?:get|post|put|delete|patch))\(['\"](/api/[^'\"]+)['\"]",
            r"(?:get|post|put|delete|patch)\(['\"](/api/[^'\"]+)['\"]",
            r"this\.http\.(?:get|post|put|delete)\(['\"](/api/[^'\"]+)['\"]",
        ]

        for pattern in patterns:
            import re

            for match in re.finditer(pattern, content):
                path = match.group(1)
                # Infer HTTP method from context (simplified)
                method = self._infer_http_method(content, match.start())

                calls.append(
                    ApiCall(
                        method=method,
                        path=path,
                        source_file=fe_component,
                        confidence=0.7,
                    )
                )

        return calls

    def _infer_http_method(self, content: str, position: int) -> str:
        """Infer HTTP method from context around position."""
        context = content[max(0, position - 50) : position + 50].lower()

        if "get" in context:
            return "GET"
        if "post" in context:
            return "POST"
        if "put" in context:
            return "PUT"
        if "delete" in context:
            return "DELETE"
        if "patch" in context:
            return "PATCH"

        return "GET"  # Default

    def _resolve_to_controller(self, api_call: ApiCall) -> list[BackendComponent]:
        """
        Resolve API call to backend controller.

        Looks for @RequestMapping, @GetMapping, etc. with matching paths.
        """
        # Placeholder: in real implementation, would parse backend code
        # and resolve API path to controller class
        return []

    def _resolve_to_services(self, controller: BackendComponent) -> list[BackendComponent]:
        """
        Resolve controller to services it uses.

        Looks for @Autowired, @Inject, etc.
        """
        # Placeholder
        return []

    def _extract_db_tables(self, services: list[BackendComponent]) -> list[str]:
        """
        Extract database tables accessed by services.

        Looks for:
        - JPA entity references
        - MyBatis mapper namespaces
        - SQL in strings
        """
        # Placeholder
        return []

    def _find_related_batch_jobs(self, tables: list[str]) -> list[str]:
        """
        Find batch jobs that read/write the same tables.

        Looks for Spring Batch Job definitions that reference these tables.
        """
        # Placeholder
        return []

    def _find_external_interfaces(self, controllers: list[BackendComponent]) -> list[str]:
        """
        Find external system interfaces (EAI/ETL feeds).

        Looks for references to external APIs, message queues, etc.
        """
        # Placeholder
        return []

    def _compute_source_hashes(self, source_files: list[str]) -> dict[str, str]:
        """
        Compute SHA256 hashes of source files.

        Used for drift detection.
        """
        hashes = {}

        for file_path in source_files:
            full_path = self.repo_path / file_path

            if full_path.exists():
                try:
                    content = full_path.read_bytes()
                    hash_value = hashlib.sha256(content).hexdigest()
                    hashes[file_path] = hash_value
                except Exception as e:
                    print(f"Warning: Failed to hash {file_path}: {e}")

        return hashes


def map_all_screens(screens: list[Screen], repo_path: Path) -> list[ScreenMapping]:
    """
    Map all screens to their backend components.

    Returns list of enhanced ScreenMapping objects.
    """
    mapper = ScreenMapper(repo_path)
    return mapper.map_screens(screens)
