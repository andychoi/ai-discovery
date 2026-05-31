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
import re
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
class DataInjectionPoint:
    """Represents a potential data injection point (external EAI or direct DB)."""

    table_name: str
    injection_type: str  # direct_database, stored_procedure, view, unknown
    confidence: float  # 0.0-1.0
    reason: str  # Why we think this is an injection point
    potential_sources: list[str] = field(default_factory=list)  # EAI systems, APIs, etc.


@dataclass
class ETLBatchJob:
    """Represents an ETL/EAI batch job that populates external tables."""

    job_name: str
    job_class: str
    file_path: str
    tables_populated: list[str] = field(default_factory=list)  # Tables this job writes to
    confidence: float = 0.7  # Confidence this is an ETL job
    trigger_type: str = "unknown"  # internal, external_http, external_queue
    reason: str = ""  # Why we believe this is an ETL job
    external_sources: list[str] = field(default_factory=list)  # Where it gets data (FTP, HTTP, etc.)


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
    data_injection_points: list[DataInjectionPoint] = field(default_factory=list)  # External data sources
    etl_batch_jobs: list[ETLBatchJob] = field(default_factory=list)  # ETL jobs populating external tables
    source_files: list[str] = field(default_factory=list)  # All source files touched
    source_hashes: dict[str, str] = field(default_factory=dict)  # file_path -> SHA256

    def to_dict(self) -> dict:
        """Convert to dictionary for YAML/JSON serialization."""
        result = asdict(self)
        result["screen"] = asdict(self.screen)
        result["fe_api_calls"] = [asdict(call) for call in self.fe_api_calls]
        result["be_controllers"] = [asdict(comp) for comp in self.be_controllers]
        result["be_services"] = [asdict(comp) for comp in self.be_services]
        result["data_injection_points"] = [asdict(point) for point in self.data_injection_points]
        result["etl_batch_jobs"] = [asdict(job) for job in self.etl_batch_jobs]
        return result


class ScreenMapper:
    """Maps screens to backend components and databases."""

    def __init__(self, repo_path: Path):
        self.repo_path = Path(repo_path)
        self.tables_with_reads = set()  # Tables we read from
        self.tables_with_writes = set()  # Tables we write to

    def detect_data_injection_points(self, db_tables: list[str], be_services: list[BackendComponent]) -> list[DataInjectionPoint]:
        """
        Detect potential external data injection points.

        Identifies tables that are READ but have NO detected WRITE operations in code.
        These are likely:
        1. Directly injected by external EAI/ETL systems
        2. Written via stored procedures called by external systems
        3. Populated by database views or triggers
        4. Updated through batch jobs not fully analyzed
        5. Imported via FTP/SFTP or other external import mechanisms

        Returns list of injection points with confidence scores.
        """
        injection_points = []

        # First, scan all services to identify which tables are read vs written
        tables_read_by_service = {}
        tables_written_by_service = {}

        for service in be_services:
            service_path = self.repo_path / service.file_path
            if not service_path.exists():
                continue

            try:
                content = service_path.read_text(errors="ignore")

                # Detect table reads (SELECT, repository.findBy, findAll, etc.)
                read_patterns = [
                    r'SELECT\s+.*?\s+FROM\s+(\w+)',
                    r'@Query.*?SELECT\s+.*?\s+FROM\s+(\w+)',
                    r'repository\.find(?:All|By|One)\(',
                    r'\.find.*?\(\)',
                ]

                for pattern in read_patterns:
                    for match in re.finditer(pattern, content, re.IGNORECASE | re.DOTALL):
                        if len(match.groups()) > 0:
                            table = match.group(1)
                            if table not in tables_read_by_service:
                                tables_read_by_service[table] = []
                            tables_read_by_service[table].append(service.class_name)

                # Detect table writes (INSERT, UPDATE, DELETE)
                write_patterns = [
                    r'INSERT\s+INTO\s+(\w+)',
                    r'UPDATE\s+(\w+)\s+SET',
                    r'DELETE\s+FROM\s+(\w+)',
                    r'repository\.save\(',
                    r'repository\.delete\(',
                    r'jpaPersistenceProvider\.persist\(',
                ]

                for pattern in write_patterns:
                    for match in re.finditer(pattern, content, re.IGNORECASE):
                        if len(match.groups()) > 0:
                            table = match.group(1)
                            if table not in tables_written_by_service:
                                tables_written_by_service[table] = []
                            tables_written_by_service[table].append(service.class_name)

            except Exception:
                pass

        # Detect orphaned tables (read but not written)
        for table in db_tables:
            if table in tables_read_by_service and table not in tables_written_by_service:
                # This table is read but never written - likely external injection
                injection_point = DataInjectionPoint(
                    table_name=table,
                    injection_type="direct_database",
                    confidence=0.8,
                    reason=f"Table {table} is read by {', '.join(tables_read_by_service[table])} but no write operations detected in code",
                    potential_sources=self._find_potential_sources(table, be_services),
                )
                injection_points.append(injection_point)

        # Detect stored procedures (additional injection points)
        stored_procs = self._find_stored_procedures(be_services)
        for proc_name, proc_info in stored_procs.items():
            # Stored procedures are often updated by external systems
            injection_point = DataInjectionPoint(
                table_name=proc_name,
                injection_type="stored_procedure",
                confidence=0.6,
                reason=f"Stored procedure {proc_name} called by {proc_info['called_by']} - may be updated by external systems",
                potential_sources=["External ETL/EAI via stored procedure"],
            )
            injection_points.append(injection_point)

        # Detect database views (aggregations from other sources)
        views = self._find_database_views(be_services)
        for view_name in views:
            injection_point = DataInjectionPoint(
                table_name=view_name,
                injection_type="view",
                confidence=0.5,
                reason=f"Database view {view_name} - underlying tables may be externally injected",
                potential_sources=["View aggregates external data sources"],
            )
            injection_points.append(injection_point)

        # Detect external import mechanisms (FTP, SFTP, HTTP)
        external_imports = self._find_external_import_mechanisms(be_services)
        for import_info in external_imports:
            injection_point = DataInjectionPoint(
                table_name=f"{import_info['type']}_import",
                injection_type="external_import",
                confidence=0.7,
                reason=f"External data import mechanism: {import_info['type']} used in {import_info['service']}",
                potential_sources=[f"{import_info['type']} data import"],
            )
            injection_points.append(injection_point)

        return injection_points

    def detect_etl_batch_jobs(self, orphaned_tables: list[str], injection_points: list[DataInjectionPoint]) -> list[ETLBatchJob]:
        """
        Detect ETL/EAI batch jobs that populate orphaned tables.

        ETL jobs are identified by:
        1. Accessing the same tables that are marked as orphaned (externally injected)
        2. Having access to external import mechanisms (FTP, SFTP, HTTP)
        3. Being triggered by external events (REST endpoints, message queues)
        4. Processing large data volumes (batch patterns)

        Returns list of detected ETL jobs with confidence scores.
        """
        etl_jobs = []

        # Get list of orphaned table names
        orphaned_table_names = {ip.table_name for ip in injection_points if ip.injection_type == "direct_database"}

        # Search for batch job files
        job_files = list(self.repo_path.glob("src/**/*Job.java"))
        job_files.extend(list(self.repo_path.glob("src/**/*JobConfig.java")))
        job_files.extend(list(self.repo_path.glob("src/**/batch/**/*.java")))

        for job_file in job_files:
            try:
                content = job_file.read_text(errors="ignore")

                # Check if this job is an ETL job (reads from external sources, writes to orphaned tables)
                has_etl_pattern = False
                tables_written = set()
                external_sources = []
                trigger_type = "unknown"

                # Detect if job has ETL characteristics
                # 1. Accesses orphaned tables (read or write)
                for orphaned_table in orphaned_table_names:
                    if orphaned_table in content:
                        has_etl_pattern = True
                        tables_written.add(orphaned_table)

                # 2. Has external source access (FTP, SFTP, HTTP)
                if "FTPClient" in content or "FTP" in content:
                    external_sources.append("FTP")
                    has_etl_pattern = True
                if "ChannelSftp" in content or "JSch" in content or "SFTP" in content:
                    external_sources.append("SFTP")
                    has_etl_pattern = True
                if "HttpClient" in content or "RestTemplate" in content:
                    external_sources.append("HTTP")
                    has_etl_pattern = True

                # 3. Detect trigger type
                if "@Scheduled" in content:
                    trigger_type = "internal"
                if "@PostMapping" in content or "@RequestMapping" in content:
                    trigger_type = "external_http"
                if "@KafkaListener" in content or "@JmsListener" in content:
                    trigger_type = "external_queue"

                # If this looks like an ETL job, record it
                if has_etl_pattern:
                    class_match = re.search(r'public\s+class\s+(\w+)', content)
                    if class_match:
                        job_class = class_match.group(1)
                        job_name = re.sub(r'(Job|JobConfig|JobDefinition)$', '', job_class)

                        confidence = 0.7
                        if external_sources:
                            confidence = min(0.9, confidence + 0.1 * len(external_sources))

                        reason = f"ETL job {job_name} accesses orphaned tables: {', '.join(sorted(tables_written))}"
                        if external_sources:
                            reason += f" with external source access: {', '.join(external_sources)}"

                        etl_job = ETLBatchJob(
                            job_name=job_name,
                            job_class=job_class,
                            file_path=str(job_file.relative_to(self.repo_path)),
                            tables_populated=sorted(list(tables_written)),
                            confidence=confidence,
                            trigger_type=trigger_type,
                            reason=reason,
                            external_sources=external_sources,
                        )
                        etl_jobs.append(etl_job)

            except Exception:
                pass

        return etl_jobs

    def _find_potential_sources(self, table: str, be_services: list[BackendComponent]) -> list[str]:
        """Identify potential external systems that might inject this table."""
        sources = []

        # Check for FTP/SFTP imports that might populate this table
        for service in be_services:
            service_path = self.repo_path / service.file_path
            if not service_path.exists():
                continue

            try:
                content = service_path.read_text(errors="ignore")

                if "FTP" in content or "SFTP" in content:
                    sources.append(f"FTP/SFTP import ({service.class_name})")

                if "import" in content.lower() and table.lower() in content.lower():
                    sources.append(f"External data import ({service.class_name})")

            except Exception:
                pass

        if not sources:
            sources.append(f"Direct database connection (EAI/ETL)")

        return sources

    def _find_stored_procedures(self, be_services: list[BackendComponent]) -> dict[str, dict]:
        """Find stored procedures that are called from services."""
        stored_procs = {}

        for service in be_services:
            service_path = self.repo_path / service.file_path
            if not service_path.exists():
                continue

            try:
                content = service_path.read_text(errors="ignore")

                # Look for stored procedure calls
                proc_patterns = [
                    r'call\s+(\w+)\s*\(',  # CALL proc_name()
                    r'execute\s+(\w+)',  # EXECUTE proc_name
                    r'@Procedure\s*\(\s*name\s*=\s*"(\w+)"',  # @Procedure(name="proc_name")
                ]

                for pattern in proc_patterns:
                    for match in re.finditer(pattern, content, re.IGNORECASE):
                        proc_name = match.group(1)
                        if proc_name not in stored_procs:
                            stored_procs[proc_name] = {"called_by": []}
                        stored_procs[proc_name]["called_by"].append(service.class_name)

            except Exception:
                pass

        return stored_procs

    def _find_database_views(self, be_services: list[BackendComponent]) -> list[str]:
        """Find references to database views (which aggregate external data)."""
        views = []

        for service in be_services:
            service_path = self.repo_path / service.file_path
            if not service_path.exists():
                continue

            try:
                content = service_path.read_text(errors="ignore")

                # Look for view references (usually named V_* or VIEW_*)
                view_patterns = [
                    r'FROM\s+(V_\w+)',
                    r'FROM\s+(\w*VIEW\w*)',
                    r'@Query.*?FROM\s+(V_\w+)',
                ]

                for pattern in view_patterns:
                    for match in re.finditer(pattern, content, re.IGNORECASE | re.DOTALL):
                        view_name = match.group(1)
                        if view_name not in views:
                            views.append(view_name)

            except Exception:
                pass

        return views

    def _find_external_import_mechanisms(self, be_services: list[BackendComponent]) -> list[dict]:
        """Find external import mechanisms (FTP, SFTP, HTTP APIs)."""
        imports = []

        for service in be_services:
            service_path = self.repo_path / service.file_path
            if not service_path.exists():
                continue

            try:
                content = service_path.read_text(errors="ignore")

                # Detect FTP imports
                if "FTPClient" in content or ("FTP" in content and "import" in content.lower()):
                    imports.append({"type": "FTP", "service": service.class_name})

                # Detect SFTP imports
                if "ChannelSftp" in content or "JSch" in content or ("SFTP" in content and "import" in content.lower()):
                    imports.append({"type": "SFTP", "service": service.class_name})

                # Detect HTTP-based data import
                if "HttpClient" in content or "HttpURLConnection" in content and "download" in content.lower():
                    imports.append({"type": "HTTP", "service": service.class_name})

            except Exception:
                pass

        return imports

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

        # 8. Detect data injection points (orphaned tables, stored procedures, views)
        mapping.data_injection_points = self.detect_data_injection_points(mapping.db_tables, mapping.be_services)

        # 9. Detect ETL batch jobs that populate external tables
        mapping.etl_batch_jobs = self.detect_etl_batch_jobs(mapping.db_tables, mapping.data_injection_points)

        # 10. Compute hashes
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
        - fetch(`/api/...`)
        - axios.get('/api/...')
        - this.http.get('/api/...')
        """
        filepath = self.repo_path / fe_component

        if not filepath.exists():
            return []

        content = filepath.read_text(errors="ignore")
        calls = []

        # Regex patterns for common API call patterns
        # Support single quotes, double quotes, and backticks
        patterns = [
            r"(?:fetch|axios\.(?:get|post|put|delete|patch))\(['\"`](/api/[^'\"` ]+)['\"`]",
            r"(?:get|post|put|delete|patch)\(['\"`](/api/[^'\"` ]+)['\"`]",
            r"this\.http\.(?:get|post|put|delete)\(['\"`](/api/[^'\"` ]+)['\"`]",
            # Also handle template strings with interpolation
            r"\$\{`?/api/([^`$}\"\']+)(?:`?)}\s*(?:\?[^`}]*)?",
        ]

        for pattern in patterns:
            for match in re.finditer(pattern, content):
                if match.group(1).startswith("/"):
                    # First capture group is the full path
                    path = match.group(1)
                else:
                    # For the template string pattern, reconstruct the path
                    path = "/" + match.group(1)

                # Skip if it's not an API path
                if "/api/" not in path:
                    continue

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
        Supports Java Spring framework.
        """
        controllers = []

        # Search for Java controller files
        controller_files = list(self.repo_path.glob("src/**/*Controller.java"))

        for ctrl_file in controller_files:
            try:
                content = ctrl_file.read_text(errors="ignore")

                # Look for class-level @RequestMapping or @RestController
                class_mapping = self._extract_class_mapping(content)

                # Combine with method-level mappings
                method_mappings = self._extract_method_mappings(content, api_call.method)

                for method_path in method_mappings:
                    full_path = (class_mapping + method_path).rstrip("/")

                    # Check if this matches our API call
                    if self._path_matches(api_call.path, full_path):
                        # Extract class name from file
                        class_name = ctrl_file.stem  # e.g., CustomerController
                        controllers.append(
                            BackendComponent(
                                component_type="controller",
                                class_name=class_name,
                                file_path=str(ctrl_file.relative_to(self.repo_path)),
                                confidence=0.7,
                            )
                        )
                        break
            except Exception:
                pass

        return controllers

    def _extract_class_mapping(self, content: str) -> str:
        """Extract class-level @RequestMapping path from Java Spring code."""
        # Look for @RequestMapping(value = "/api/..." or @RestController above class
        match = re.search(r'@(?:RequestMapping|RestController)\s*\([^)]*value\s*=\s*["\']([^"\']*)["\']', content)
        if match:
            return match.group(1)

        # Try simpler pattern
        match = re.search(r'@RequestMapping\s*\(\s*["\']([^"\']*)["\']', content)
        if match:
            return match.group(1)

        return ""

    def _extract_method_mappings(self, content: str, http_method: str) -> list[str]:
        """Extract method-level request mappings matching the HTTP method."""
        paths = []

        # Mapping annotation to HTTP method
        method_to_annotation = {
            "GET": ["GetMapping", "RequestMapping"],
            "POST": ["PostMapping", "RequestMapping"],
            "PUT": ["PutMapping", "RequestMapping"],
            "DELETE": ["DeleteMapping", "RequestMapping"],
            "PATCH": ["PatchMapping", "RequestMapping"],
        }

        annotations = method_to_annotation.get(http_method, ["RequestMapping"])

        for annotation in annotations:
            # Find @GetMapping(value = "/path" or similar patterns
            pattern = rf'@{annotation}\s*\(\s*(?:value\s*=\s*)?["\']([^"\']*)["\']'
            for match in re.finditer(pattern, content):
                path = match.group(1)
                paths.append(path)

            # Also handle @GetMapping with no parameters (empty path)
            pattern_empty = rf'@{annotation}\s*\(\s*\)'
            if re.search(pattern_empty, content):
                paths.append("")

            # Handle @GetMapping with no parentheses at all
            pattern_no_params = rf'@{annotation}(?:\s+|$)'
            if re.search(pattern_no_params, content) and annotation in ["GetMapping", "PostMapping", "PutMapping", "DeleteMapping", "PatchMapping"]:
                # Only add empty path for specific mapping annotations, not RequestMapping
                if annotation != "RequestMapping":
                    paths.append("")

        return paths

    def _path_matches(self, api_path: str, controller_path: str) -> bool:
        """Check if API path matches controller mapping."""
        # Normalize paths
        api_norm = api_path.rstrip("/").lower()
        ctrl_norm = controller_path.rstrip("/").lower()

        # Exact match
        if api_norm == ctrl_norm:
            return True

        # Handle path variables like {id} in controller_path matching api_path
        ctrl_pattern = re.sub(r"\{[^}]+\}", "[^/]+", ctrl_norm)
        if re.match(f"^{ctrl_pattern}$", api_norm):
            return True

        return False

    def _resolve_to_services(self, controller: BackendComponent) -> list[BackendComponent]:
        """
        Resolve controller to services it uses.

        Looks for @Autowired, @Inject, @Qualifier etc. in controller class.
        """
        services = []

        ctrl_path = self.repo_path / controller.file_path
        if not ctrl_path.exists():
            return services

        try:
            content = ctrl_path.read_text(errors="ignore")

            # Look for @Autowired private ServiceName serviceName; pattern
            autowired_pattern = r'@(?:Autowired|Inject)\s+private\s+(\w+)\s+\w+;'
            for match in re.finditer(autowired_pattern, content):
                service_class = match.group(1)

                # Try to find the service file
                service_files = list(self.repo_path.glob(f"src/**/{service_class}.java"))
                if service_files:
                    service_path = service_files[0]
                    services.append(
                        BackendComponent(
                            component_type="service",
                            class_name=service_class,
                            file_path=str(service_path.relative_to(self.repo_path)),
                            confidence=0.8,
                        )
                    )

        except Exception:
            pass

        return services

    def _extract_db_tables(self, services: list[BackendComponent]) -> list[str]:
        """
        Extract database tables accessed by services.

        Looks for:
        - JPA entity references (@Entity @Table)
        - Repository method signatures
        - SQL in strings
        """
        tables = set()

        # Search for entity files
        entity_files = list(self.repo_path.glob("src/**/*Entity.java"))
        entity_files.extend(list(self.repo_path.glob("src/**/entity/*.java")))
        entity_files.extend(list(self.repo_path.glob("src/**/model/*.java")))

        for entity_file in entity_files:
            try:
                content = entity_file.read_text(errors="ignore")

                # Look for @Entity and @Table annotations
                if "@Entity" in content:
                    # Extract table name from @Table annotation
                    match = re.search(r'@Table\s*\(\s*name\s*=\s*["\']([^"\']+)["\']', content)
                    if match:
                        tables.add(match.group(1))
                    else:
                        # Use class name as fallback (convert camelCase to UPPER_SNAKE)
                        class_match = re.search(r'public\s+class\s+(\w+)', content)
                        if class_match:
                            class_name = class_match.group(1)
                            # Remove "Entity" suffix if present
                            entity_name = class_name.replace("Entity", "")
                            table_name = re.sub(r'([A-Z])', r'_\1', entity_name).upper().lstrip("_")
                            tables.add(table_name)
            except Exception:
                pass

        # Also extract from service files
        for service in services:
            service_path = self.repo_path / service.file_path
            if service_path.exists():
                try:
                    content = service_path.read_text(errors="ignore")

                    # Look for @Repository or repository.findBy* patterns
                    # This is a simplified heuristic
                    entity_refs = re.findall(r'\b([A-Z]\w+Entity)\b', content)
                    for entity_ref in entity_refs:
                        # Convert entity class name to table name
                        entity_name = entity_ref.replace("Entity", "")
                        table_name = re.sub(r'([A-Z])', r'_\1', entity_name).upper().lstrip("_")
                        tables.add(table_name)
                except Exception:
                    pass

        return sorted(list(tables))

    def _find_related_batch_jobs(self, tables: list[str]) -> list[str]:
        """
        Find batch jobs that read/write the same tables.

        Looks for Spring Batch Job definitions that reference these tables.
        Detects both internal triggers (@Scheduled, @EnableBatchProcessing) and
        external triggers (REST endpoints, message queue listeners).
        """
        jobs = set()

        if not tables:
            return []

        # Search for job configuration files
        job_files = list(self.repo_path.glob("src/**/*Job.java"))
        job_files.extend(list(self.repo_path.glob("src/**/*JobConfig.java")))
        job_files.extend(list(self.repo_path.glob("src/**/batch/**/*.java")))

        for job_file in job_files:
            try:
                content = job_file.read_text(errors="ignore")

                # Look for table references in Spring Batch jobs
                for table in tables:
                    # Check for direct table name references
                    if table in content:
                        # Extract job class name
                        class_match = re.search(r'public\s+class\s+(\w+)', content)
                        if class_match:
                            job_class = class_match.group(1)
                            # Remove suffixes like Job, JobConfig, JobDefinition
                            job_name = re.sub(r'(Job|JobConfig|JobDefinition)$', '', job_class)
                            jobs.add(job_name)

                    # Check for entity class references
                    entity_pattern = re.sub(r'([A-Z])', r'_\1', table).strip("_").replace("_", "")
                    entity_class = entity_pattern.title().replace("_", "") + "Entity"
                    if entity_class in content:
                        class_match = re.search(r'public\s+class\s+(\w+)', content)
                        if class_match:
                            job_class = class_match.group(1)
                            job_name = re.sub(r'(Job|JobConfig|JobDefinition)$', '', job_class)
                            jobs.add(job_name)

                # INTERNAL TRIGGERS: @EnableBatchProcessing, @Scheduled, @Bean @Job patterns
                if "@EnableBatchProcessing" in content or "@Scheduled" in content:
                    class_match = re.search(r'public\s+class\s+(\w+)', content)
                    if class_match:
                        job_class = class_match.group(1)
                        job_name = re.sub(r'(Job|JobConfig|JobDefinition)$', '', job_class)
                        jobs.add(job_name)

                # EXTERNAL TRIGGERS: REST endpoints that trigger jobs
                if "@PostMapping" in content or "@RequestMapping" in content:
                    # This job can be triggered via HTTP
                    class_match = re.search(r'public\s+class\s+(\w+)', content)
                    if class_match:
                        job_class = class_match.group(1)
                        job_name = re.sub(r'(Job|JobConfig|JobDefinition)$', '', job_class)
                        # Mark as externally triggerable
                        jobs.add(f"{job_name}(HTTP-triggered)")

                # EXTERNAL TRIGGERS: Message queue listeners
                if "@KafkaListener" in content or "@RabbitListener" in content or "@JmsListener" in content:
                    class_match = re.search(r'public\s+class\s+(\w+)', content)
                    if class_match:
                        job_class = class_match.group(1)
                        job_name = re.sub(r'(Job|JobConfig|JobDefinition)$', '', job_class)
                        # Mark as queue-triggered
                        jobs.add(f"{job_name}(Queue-triggered)")

            except Exception:
                pass

        return sorted(list(jobs))

    def _find_external_interfaces(self, controllers: list[BackendComponent]) -> list[str]:
        """
        Find external system interfaces (EAI/ETL feeds).

        Detects both directions:
        - PUSH: Outbound calls (RestTemplate, KafkaTemplate, S3, etc.)
        - PULL: Inbound webhooks (@PostMapping receiving external data, @KafkaListener, etc.)

        Looks for references to:
        - RestTemplate/WebClient calls (external REST APIs) - PUSH
        - @FeignClient (external REST APIs) - PUSH
        - Message queue producers (JMS, Kafka, RabbitMQ) - PUSH
        - Message queue listeners (@KafkaListener, @JmsListener) - PULL
        - @PostMapping endpoints (receiving webhooks) - PULL
        - FTP/SFTP clients - PUSH
        - External datasources - PUSH/PULL
        """
        interfaces = set()

        # Get all files referenced in controllers (for tracing)
        all_files = []
        for controller in controllers:
            all_files.append(self.repo_path / controller.file_path)

        # Also search in service/repository files
        all_files.extend(list(self.repo_path.glob("src/**/*Service.java")))
        all_files.extend(list(self.repo_path.glob("src/**/*Repository.java")))
        all_files.extend(list(self.repo_path.glob("src/**/*Listener.java")))

        for file_path in all_files:
            if not file_path.exists():
                continue

            try:
                content = file_path.read_text(errors="ignore")

                # ============ OUTBOUND (PUSH) ============

                # Look for RestTemplate usage (external REST APIs) - PUSH
                rest_template_urls = re.findall(
                    r'restTemplate\.(?:get|post|put|delete|exchange)\s*\(\s*["\']([^"\']+)["\']',
                    content
                )
                for url in rest_template_urls:
                    if url.startswith("http"):
                        interfaces.add(f"REST-PUSH-{url}")

                # Look for @FeignClient (external REST API) - PUSH
                feign_clients = re.findall(r'@FeignClient\s*\(\s*(?:value|name)\s*=\s*["\']([^"\']+)["\']', content)
                for client in feign_clients:
                    interfaces.add(f"FeignClient-PUSH-{client}")

                # Look for WebClient usage - PUSH
                webclient_urls = re.findall(
                    r'webClient\.(?:get|post|put|delete)\s*\(\s*["\']([^"\']+)["\']',
                    content
                )
                for url in webclient_urls:
                    if url.startswith("http"):
                        interfaces.add(f"WebClient-PUSH-{url}")

                # Look for JMS/Kafka/RabbitMQ PRODUCERS - PUSH
                if "JmsTemplate" in content:
                    interfaces.add("JMS-PUSH-MessageQueue")
                if "KafkaTemplate" in content:
                    interfaces.add("Kafka-PUSH-MessageBroker")
                if "RabbitTemplate" in content:
                    interfaces.add("RabbitMQ-PUSH-MessageBroker")

                # Look for FTP/SFTP clients - PUSH
                if "FTPClient" in content or "FTP" in content:
                    interfaces.add("FTP-PUSH-FileTransfer")
                if "SFTPClient" in content or "JSch" in content:
                    interfaces.add("SFTP-PUSH-FileTransfer")

                # Look for external datasources (write operations) - PUSH
                if "DataSource" in content and "@Qualifier" in content:
                    datasources = re.findall(r'@Qualifier\s*\(\s*["\']([^"\']+)["\']', content)
                    for ds in datasources:
                        if "external" in ds.lower() or "remote" in ds.lower():
                            interfaces.add(f"Database-PUSH-{ds}")

                # Look for AWS SDK usage (write operations) - PUSH
                if "AmazonS3" in content or "s3Client.putObject" in content:
                    interfaces.add("AWS-S3-PUSH")
                if "AmazonDynamoDB" in content:
                    interfaces.add("AWS-DynamoDB-PUSH")
                if "AWSCredentials" in content:
                    interfaces.add("AWS-Service-PUSH")

                # Look for custom external service calls - PUSH
                http_calls = re.findall(
                    r'new\s+URL\s*\(\s*["\']([^"\']+)["\']',
                    content
                )
                for url in http_calls:
                    if url.startswith("http"):
                        interfaces.add(f"HttpURL-PUSH-{url}")

                # ============ INBOUND (PULL) ============

                # Look for @PostMapping/@PutMapping endpoints (webhook receivers) - PULL
                if "@PostMapping" in content or "@PutMapping" in content:
                    # This endpoint receives data from external systems
                    mapping_paths = re.findall(r'@(?:PostMapping|PutMapping)\s*\(\s*["\']([^"\']+)["\']', content)
                    for path in mapping_paths:
                        interfaces.add(f"REST-PULL-{path}")

                # Look for JMS/Kafka/RabbitMQ CONSUMERS - PULL
                if "@JmsListener" in content:
                    interfaces.add("JMS-PULL-MessageQueue")
                if "@KafkaListener" in content:
                    interfaces.add("Kafka-PULL-MessageBroker")
                if "@RabbitListener" in content:
                    interfaces.add("RabbitMQ-PULL-MessageBroker")

                # Look for FTP/SFTP file receivers (polling) - PULL
                if "FTP" in content and ("poll" in content.lower() or "schedule" in content.lower()):
                    interfaces.add("FTP-PULL-FileTransfer")
                if "SFTP" in content and ("poll" in content.lower() or "schedule" in content.lower()):
                    interfaces.add("SFTP-PULL-FileTransfer")

                # Look for external datasource (read operations) - PULL
                if "DataSource" in content and "SELECT" in content:
                    interfaces.add("Database-PULL-ExternalSource")

                # Look for webhook validators/handlers - PULL
                if "hmac" in content.lower() or "signature" in content.lower():
                    # Likely receiving authenticated webhooks
                    interfaces.add("Webhook-PULL-WithAuth")

            except Exception:
                pass

        return sorted(list(interfaces))

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
