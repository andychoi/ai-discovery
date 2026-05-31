"""Framework-specific pattern detection for multiple languages/platforms."""

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Optional


@dataclass
class FrameworkPattern:
    """Framework-specific detection pattern."""

    language: str  # "java", "csharp", "javascript", etc.
    framework: str  # "spring", "aspnet", "express", etc.
    controller_patterns: list[str]  # Regex patterns for controller detection
    service_patterns: list[str]  # Service/dependency injection patterns
    entity_patterns: list[str]  # Database entity/model patterns
    api_call_patterns: list[str]  # External API call patterns
    batch_job_patterns: list[str]  # Batch/job patterns


class FrameworkDetector:
    """Detects and provides patterns for different frameworks."""

    JAVA_SPRING = FrameworkPattern(
        language="java",
        framework="spring",
        controller_patterns=[
            r'@(?:RestController|Controller)',
            r'@RequestMapping',
        ],
        service_patterns=[
            r'@Service',
            r'@Component',
            r'@(?:Autowired|Inject)',
        ],
        entity_patterns=[
            r'@Entity',
            r'@Table',
        ],
        api_call_patterns=[
            r'RestTemplate',
            r'WebClient',
            r'@FeignClient',
            r'HttpClient',
            r'HttpURLConnection',
        ],
        batch_job_patterns=[
            r'@EnableBatchProcessing',
            r'@Scheduled',
            r'JobBuilderFactory',
        ],
    )

    DOTNET_ASPNET = FrameworkPattern(
        language="csharp",
        framework="aspnet",
        controller_patterns=[
            r'\[ApiController\]',
            r'\[Controller\]',
            r'\[Route\(',
        ],
        service_patterns=[
            r'\[Service\]',
            r'\[Inject\]',
            r'private readonly',
            r'public.*\(.*Service',
        ],
        entity_patterns=[
            r'\[Table\(',
            r'\[Entity\]',
            r'DbSet<',
        ],
        api_call_patterns=[
            r'HttpClient',
            r'RestClient',
            r'using.*Http',
            r'WebClient',
        ],
        batch_job_patterns=[
            r'IHostedService',
            r'BackgroundService',
            r'\[Scheduled\]',
            r'ScheduledJob',
        ],
    )

    NODE_EXPRESS = FrameworkPattern(
        language="javascript",
        framework="express",
        controller_patterns=[
            r'router\.(get|post|put|delete)',
            r'app\.(get|post|put|delete)',
            r'export.*controller',
        ],
        service_patterns=[
            r'module\.exports',
            r'export\s+(const|class|function)',
            r'require\(',
        ],
        entity_patterns=[
            r'schema\s*=',
            r'new\s+Schema',
            r'mongoose\.model',
        ],
        api_call_patterns=[
            r'axios\.',
            r'fetch\(',
            r'request\(',
            r'http\.get',
            r'http\.post',
        ],
        batch_job_patterns=[
            r'schedule\.',
            r'Bull\(',
            r'agenda\.',
            r'setTimeout',
        ],
    )

    @staticmethod
    def detect_framework(repo_path: Path) -> Optional[FrameworkPattern]:
        """
        Detect which framework is used in the repository.

        Returns the framework pattern if detected, None otherwise.
        """
        # Check for Java/Spring
        pom_file = repo_path / "pom.xml"
        gradle_file = repo_path / "build.gradle"
        if pom_file.exists():
            content = pom_file.read_text(errors="ignore")
            if "spring" in content.lower():
                return FrameworkDetector.JAVA_SPRING
        if gradle_file.exists():
            content = gradle_file.read_text(errors="ignore")
            if "spring" in content.lower():
                return FrameworkDetector.JAVA_SPRING

        # Check for .NET/ASP.NET
        csproj_files = list(repo_path.glob("**/*.csproj"))
        if csproj_files:
            content = csproj_files[0].read_text(errors="ignore")
            if "Microsoft" in content or "AspNetCore" in content:
                return FrameworkDetector.DOTNET_ASPNET

        # Check for Node.js/Express
        package_json = repo_path / "package.json"
        if package_json.exists():
            content = package_json.read_text(errors="ignore")
            if "express" in content.lower():
                return FrameworkDetector.NODE_EXPRESS

        return None

    @staticmethod
    def detect_controller_files(repo_path: Path, pattern: FrameworkPattern) -> list[Path]:
        """
        Find controller files based on framework patterns.

        Returns list of detected controller file paths.
        """
        controllers = []

        # Framework-specific file searches
        if pattern.framework == "spring":
            controllers.extend(repo_path.glob("**/*Controller.java"))
        elif pattern.framework == "aspnet":
            controllers.extend(repo_path.glob("**/*Controller.cs"))
        elif pattern.framework == "express":
            controllers.extend(repo_path.glob("**/routes/*.js"))
            controllers.extend(repo_path.glob("**/controllers/*.js"))

        return controllers

    @staticmethod
    def detect_entities(repo_path: Path, pattern: FrameworkPattern) -> list[Path]:
        """
        Find entity/model files based on framework patterns.

        Returns list of detected entity file paths.
        """
        entities = []

        # Framework-specific file searches
        if pattern.framework == "spring":
            entities.extend(repo_path.glob("**/entity/*.java"))
            entities.extend(repo_path.glob("**/model/*.java"))
            entities.extend(repo_path.glob("**/*Entity.java"))
        elif pattern.framework == "aspnet":
            entities.extend(repo_path.glob("**/Models/*.cs"))
            entities.extend(repo_path.glob("**/Entities/*.cs"))
        elif pattern.framework == "express":
            entities.extend(repo_path.glob("**/models/*.js"))

        return entities

    @staticmethod
    def detect_services(repo_path: Path, pattern: FrameworkPattern) -> list[Path]:
        """
        Find service files based on framework patterns.

        Returns list of detected service file paths.
        """
        services = []

        # Framework-specific file searches
        if pattern.framework == "spring":
            services.extend(repo_path.glob("**/*Service.java"))
            services.extend(repo_path.glob("**/service/*.java"))
        elif pattern.framework == "aspnet":
            services.extend(repo_path.glob("**/Services/*.cs"))
            services.extend(repo_path.glob("**/*Service.cs"))
        elif pattern.framework == "express":
            services.extend(repo_path.glob("**/services/*.js"))

        return services

    @staticmethod
    def extract_controller_metadata(file_path: Path, pattern: FrameworkPattern) -> dict:
        """
        Extract controller metadata (class name, endpoints, etc.) based on framework.

        Returns dict with metadata.
        """
        content = file_path.read_text(errors="ignore")
        metadata = {
            "file_path": str(file_path),
            "class_name": None,
            "endpoints": [],
            "methods": [],
        }

        # Framework-specific extraction
        if pattern.framework == "spring":
            # Extract class name
            class_match = re.search(r'public\s+class\s+(\w+)', content)
            if class_match:
                metadata["class_name"] = class_match.group(1)

            # Extract endpoints
            endpoint_patterns = [
                r'@(?:GetMapping|PostMapping|PutMapping|DeleteMapping|PatchMapping)\s*(?:\(\s*["\']([^"\']*)["\']?\s*\))?',
                r'@RequestMapping\s*(?:\(\s*["\']([^"\']*)["\']?\s*\))?',
            ]
            for pattern_str in endpoint_patterns:
                for match in re.finditer(pattern_str, content):
                    endpoint = match.group(1) if match.group(1) else ""
                    if endpoint not in metadata["endpoints"]:
                        metadata["endpoints"].append(endpoint)

        elif pattern.framework == "aspnet":
            # Extract class name
            class_match = re.search(r'public\s+class\s+(\w+)', content)
            if class_match:
                metadata["class_name"] = class_match.group(1)

            # Extract routes
            route_patterns = [
                r'\[HttpGet\s*(?:\(\s*["\']([^"\']*)["\']?\s*\))?',
                r'\[HttpPost\s*(?:\(\s*["\']([^"\']*)["\']?\s*\))?',
                r'\[Route\s*\(\s*["\']([^"\']*)["\']',
            ]
            for pattern_str in route_patterns:
                for match in re.finditer(pattern_str, content):
                    route = match.group(1) if match.group(1) else ""
                    if route and route not in metadata["endpoints"]:
                        metadata["endpoints"].append(route)

        elif pattern.framework == "express":
            # Extract route methods
            route_patterns = [
                r'router\.(get|post|put|delete|patch)\s*\(\s*["\']([^"\']*)["\']',
                r'app\.(get|post|put|delete|patch)\s*\(\s*["\']([^"\']*)["\']',
            ]
            for pattern_str in route_patterns:
                for match in re.finditer(pattern_str, content):
                    method = match.group(1).upper() if match.group(1) else "GET"
                    path = match.group(2) if match.group(2) else ""
                    metadata["methods"].append({"method": method, "path": path})

        return metadata
