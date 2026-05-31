"""Generic entity-service resolver for tracing service methods that handle data entities."""

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from .framework_detector import FrameworkPattern


@dataclass
class Entity:
    """Represents a data entity (table, class, schema)."""

    entity_type: str  # "class", "table", "schema"
    class_name: str
    file_path: str
    fields: list[str] = field(default_factory=list)


@dataclass
class Repository:
    """Represents a repository/data access layer."""

    repo_type: str  # "interface", "class"
    class_name: str
    file_path: str
    entity_type: str  # What entity does it manage?
    confidence: float = 0.8


@dataclass
class Service:
    """Represents a service class."""

    service_type: str  # "class", "interface"
    class_name: str
    file_path: str
    repositories: list[str] = field(default_factory=list)  # Injected repos
    confidence: float = 0.8


@dataclass
class DependencyGraph:
    """Complete entity-repository-service-controller dependency graph."""

    entities: dict[str, Entity]
    repositories: dict[str, Repository]
    services: dict[str, Service]
    relationships: dict[str, list[str]]  # entity_name -> [service_names]


class EntityServiceResolver:
    """Resolves entity-service relationships across frameworks."""

    def __init__(self, repo_path: Path, pattern: FrameworkPattern):
        self.repo_path = repo_path
        self.pattern = pattern

    def find_services_for_entity(self, entity: Entity) -> list[Service]:
        """Find services that depend on this entity."""
        if self.pattern.framework == "spring":
            return self._find_services_for_entity_java(entity)
        elif self.pattern.framework == "aspnet":
            return self._find_services_for_entity_dotnet(entity)
        elif self.pattern.framework == "express":
            return self._find_services_for_entity_node(entity)
        return []

    def find_entities_for_service(self, service: Service) -> list[Entity]:
        """Find entities that a service depends on."""
        if self.pattern.framework == "spring":
            return self._find_entities_for_service_java(service)
        elif self.pattern.framework == "aspnet":
            return self._find_entities_for_service_dotnet(service)
        elif self.pattern.framework == "express":
            return self._find_entities_for_service_node(service)
        return []

    def _find_services_for_entity_java(self, entity: Entity) -> list[Service]:
        """Find Java @Service classes that use this entity."""
        services = []

        # Step 1: Find repositories that use this entity
        repositories = self._find_repositories_for_entity_java(entity)

        # Step 2: For each repository, find services that inject it
        for repo in repositories:
            services.extend(self._find_services_for_repository_java(repo))

        return services

    def _find_repositories_for_entity_java(self, entity: Entity) -> list[Repository]:
        """Find Java @Repository interfaces that handle this entity."""
        repositories = []
        repo_files = list(self.repo_path.glob("**/*Repository.java"))

        for repo_file in repo_files:
            try:
                content = repo_file.read_text(errors="ignore")
            except Exception:
                continue

            # Look for "extends JpaRepository<OrderEntity>" pattern
            if f"<{entity.class_name}" in content or f"<{entity.class_name}," in content:
                class_match = re.search(r'(?:interface|class)\s+(\w+)', content)
                if class_match:
                    repositories.append(Repository(
                        repo_type="interface",
                        class_name=class_match.group(1),
                        file_path=str(repo_file.relative_to(self.repo_path)),
                        entity_type=entity.class_name,
                        confidence=0.8
                    ))

        return repositories

    def _find_services_for_repository_java(self, repository: Repository) -> list[Service]:
        """Find Java @Service classes that inject this repository."""
        services = []
        service_files = list(self.repo_path.glob("**/*Service.java"))

        for service_file in service_files:
            try:
                content = service_file.read_text(errors="ignore")
            except Exception:
                continue

            # Look for "@Autowired private RepositoryName repo;" pattern
            pattern = rf'@(?:Autowired|Inject)\s+(?:private\s+)?{repository.class_name}\s+\w+(?:\s*;|,)'
            if re.search(pattern, content):
                class_match = re.search(r'public\s+class\s+(\w+)', content)
                if class_match:
                    services.append(Service(
                        service_type="class",
                        class_name=class_match.group(1),
                        file_path=str(service_file.relative_to(self.repo_path)),
                        repositories=[repository.class_name],
                        confidence=0.85
                    ))

        return services

    def _find_entities_for_service_java(self, service: Service) -> list[Entity]:
        """Find entities that a Java service uses."""
        entities = []

        try:
            service_file = self.repo_path / service.file_path
            content = service_file.read_text(errors="ignore")
        except Exception:
            return entities

        # Find all repository fields in service
        for repo_name in service.repositories:
            # Look for EntityType in repository generics
            # Pattern: private RepositoryName repo; where RepositoryName = <EntityType>Repository
            entity_match = re.search(rf'{repo_name}.*', content)
            if entity_match:
                # Try to find the entity type by checking repository definitions
                repo_files = list(self.repo_path.glob("**/*Repository.java"))
                for repo_file in repo_files:
                    try:
                        repo_content = repo_file.read_text(errors="ignore")
                    except Exception:
                        continue

                    if repo_name in repo_content:
                        # Extract entity type from <EntityType> in repository
                        entity_match = re.search(rf'JpaRepository<(\w+)[>,]', repo_content)
                        if entity_match:
                            entity_name = entity_match.group(1)
                            entities.append(Entity(
                                entity_type="class",
                                class_name=entity_name,
                                file_path=str(repo_file)
                            ))

        return entities

    def _find_services_for_entity_dotnet(self, entity: Entity) -> list[Service]:
        """Find .NET services that use this entity."""
        services = []
        service_files = list(self.repo_path.glob("**/*Service.cs"))

        for service_file in service_files:
            try:
                content = service_file.read_text(errors="ignore")
            except Exception:
                continue

            # Look for IRepository<Entity>, DbSet<Entity>, or direct type usage
            entity_patterns = [
                f"IRepository<{entity.class_name}>",
                f"DbSet<{entity.class_name}>",
                f": {entity.class_name}",
                f"({entity.class_name}",
                f"<{entity.class_name}>"
            ]

            if any(pattern in content for pattern in entity_patterns):
                class_match = re.search(r'public\s+class\s+(\w+)', content)
                if class_match:
                    services.append(Service(
                        service_type="class",
                        class_name=class_match.group(1),
                        file_path=str(service_file.relative_to(self.repo_path)),
                        confidence=0.8
                    ))

        return services

    def _find_entities_for_service_dotnet(self, service: Service) -> list[Entity]:
        """Find entities that a .NET service uses."""
        entities = []

        try:
            service_file = self.repo_path / service.file_path
            content = service_file.read_text(errors="ignore")
        except Exception:
            return entities

        # Look for type references in method signatures and statements
        patterns = [
            r'(?:IRepository|DbSet)<(\w+)>',
            r'public\s+(\w+)\s+\w+\(',  # public Type MethodName(
            r'(?:return|new)\s+(\w+)\s*[\(\{]',  # return Type( or return Type{
            r'private\s+(?:readonly\s+)?(\w+)\s+\w+[;,]',  # private Type field;
        ]

        seen = set()
        for pattern in patterns:
            for match in re.finditer(pattern, content):
                entity_name = match.group(1)
                if entity_name and entity_name not in seen and entity_name[0].isupper():
                    # Skip common C# keywords and types
                    if entity_name not in ['Task', 'ActionResult', 'IOrderRepository', 'System']:
                        seen.add(entity_name)
                        entities.append(Entity(
                            entity_type="class",
                            class_name=entity_name,
                            file_path=str(service_file)
                        ))

        return entities

    def _find_services_for_entity_node(self, entity: Entity) -> list[Service]:
        """Find Node.js services that use this entity."""
        services = []
        service_files = list(self.repo_path.glob("**/services/*.js"))
        service_files.extend(self.repo_path.glob("**/*Service.js"))

        for service_file in service_files:
            try:
                content = service_file.read_text(errors="ignore")
            except Exception:
                continue

            # Look for mongoose.model or require patterns
            if entity.class_name in content or entity.class_name.lower() in content.lower():
                # Check if this service imports/uses this model
                if f"require('{entity.class_name}')" in content or f'require("{entity.class_name}")' in content:
                    class_match = re.search(r'class\s+(\w+)', content)
                    if class_match:
                        services.append(Service(
                            service_type="class",
                            class_name=class_match.group(1),
                            file_path=str(service_file.relative_to(self.repo_path)),
                            confidence=0.75
                        ))

        return services

    def _find_entities_for_service_node(self, service: Service) -> list[Entity]:
        """Find entities that a Node.js service uses."""
        entities = []

        try:
            service_file = self.repo_path / service.file_path
            content = service_file.read_text(errors="ignore")
        except Exception:
            return entities

        # Look for require('./models/...') or require('../models/...') patterns
        patterns = [
            r"require\(['\"](?:\.+/)?models/(\w+)['\"]",
            r"const\s+(\w+)\s*=\s*require\(['\"][^'\"]*['\"]",
        ]

        seen = set()
        for pattern in patterns:
            for match in re.finditer(pattern, content):
                entity_name = match.group(1)
                if entity_name and entity_name not in seen:
                    seen.add(entity_name)
                    entities.append(Entity(
                        entity_type="class",
                        class_name=entity_name,
                        file_path=f"models/{entity_name}.js"
                    ))

        return entities

    def build_dependency_graph(self) -> DependencyGraph:
        """Build complete entity-repository-service dependency graph."""
        entities = {}
        services = {}
        repositories = {}
        relationships = {}

        # Find all entities
        entity_files = []
        if self.pattern.framework == "spring":
            entity_files.extend(self.repo_path.glob("**/entity/*.java"))
            entity_files.extend(self.repo_path.glob("**/*Entity.java"))
        elif self.pattern.framework == "aspnet":
            entity_files.extend(self.repo_path.glob("**/Models/*.cs"))
            entity_files.extend(self.repo_path.glob("**/Entities/*.cs"))
        elif self.pattern.framework == "express":
            entity_files.extend(self.repo_path.glob("**/models/*.js"))

        for entity_file in entity_files:
            try:
                content = entity_file.read_text(errors="ignore")
                class_match = re.search(r'(?:class|const)\s+(\w+)', content)
                if class_match:
                    entity_name = class_match.group(1)
                    entity = Entity(
                        entity_type="class",
                        class_name=entity_name,
                        file_path=str(entity_file.relative_to(self.repo_path))
                    )
                    entities[entity_name] = entity
            except Exception:
                continue

        # Find all services
        service_files = []
        if self.pattern.framework == "spring":
            service_files.extend(self.repo_path.glob("**/*Service.java"))
        elif self.pattern.framework == "aspnet":
            service_files.extend(self.repo_path.glob("**/*Service.cs"))
        elif self.pattern.framework == "express":
            service_files.extend(self.repo_path.glob("**/services/*.js"))

        for service_file in service_files:
            try:
                content = service_file.read_text(errors="ignore")
                class_match = re.search(r'(?:class|const|function)\s+(\w+)', content)
                if class_match:
                    service_name = class_match.group(1)
                    service = Service(
                        service_type="class",
                        class_name=service_name,
                        file_path=str(service_file.relative_to(self.repo_path))
                    )
                    services[service_name] = service
            except Exception:
                continue

        # Build relationships
        for entity_name, entity in entities.items():
            services_for_entity = self.find_services_for_entity(entity)
            relationships[entity_name] = [s.class_name for s in services_for_entity]

        return DependencyGraph(
            entities=entities,
            repositories=repositories,
            services=services,
            relationships=relationships
        )
