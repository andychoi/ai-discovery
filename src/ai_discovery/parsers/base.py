from __future__ import annotations

from abc import ABC, abstractmethod
from pathlib import Path

from ..graph.models import CodeNode


class LanguageParser(ABC):
    """Abstract base class that all language parsers must implement."""

    @property
    @abstractmethod
    def language(self) -> str:
        ...

    @property
    @abstractmethod
    def extensions(self) -> frozenset[str]:
        ...

    @abstractmethod
    def parse_file(self, file_path: Path) -> list[CodeNode]:
        ...

    def can_parse(self, file_path: Path) -> bool:
        return file_path.suffix.lower() in self.extensions
