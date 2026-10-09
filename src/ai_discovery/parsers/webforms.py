"""WebForms parser: .aspx/.ascx -> ui_component CodeNode linked to code-behind."""
from __future__ import annotations

from pathlib import Path

from ..graph.domain_classifier import infer_domain
from ..graph.models import CodeNode
from ..webforms_extractor import extract_webforms_page
from .base import LanguageParser
from ..core.encoding import read_source


class WebFormsParser(LanguageParser):
    @property
    def language(self) -> str:
        return "webforms"

    @property
    def extensions(self) -> frozenset[str]:
        return frozenset({".aspx", ".ascx"})

    def parse_file(self, file_path: Path) -> list[CodeNode]:
        page = extract_webforms_page(file_path)
        if page is None:
            return []
        calls = [
            f"{page.code_behind_class}.{ev['handler']}"
            for ev in page.events
            if page.code_behind_class
        ]
        text = ""
        try:
            text = read_source(file_path).text
        except Exception:
            pass
        domain = (
            infer_domain(page.code_behind_class, str(file_path))
            if page.code_behind_class
            else None
        )
        return [
            CodeNode(
                file_path=str(file_path),
                language="webforms",
                node_type="ui_component",
                name=file_path.stem,
                qualified_name=str(file_path),
                source_code=text,
                line_start=1,
                line_end=text.count("\n") + 1,
                calls=calls,
                framework_hints={
                    "framework": "webforms",
                    "code_behind_class": page.code_behind_class,
                    "master_page": page.master_page,
                    "title": page.title,
                    "is_user_control": page.is_user_control,
                    "events": page.events,
                },
                domain=domain,
            )
        ]
