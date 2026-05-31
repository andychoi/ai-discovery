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
