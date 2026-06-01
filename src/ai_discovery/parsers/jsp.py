"""JSP parser: .jsp/.jspx/.tag/.tagx -> ui_component CodeNode linked to useBean class."""
from __future__ import annotations

from pathlib import Path

from ..graph.domain_classifier import infer_domain
from ..graph.models import CodeNode
from ..jsp_extractor import extract_jsp_page
from .base import LanguageParser


class JspParser(LanguageParser):
    @property
    def language(self) -> str:
        return "jsp"

    @property
    def extensions(self) -> frozenset[str]:
        return frozenset({".jsp", ".jspx", ".tag", ".tagx"})

    def parse_file(self, file_path: Path) -> list[CodeNode]:
        page = extract_jsp_page(file_path)
        if page is None:
            return []
        text = ""
        try:
            text = file_path.read_text(encoding="utf-8", errors="ignore")
        except Exception:
            pass
        domain = infer_domain(page.bean_classes[0], str(file_path)) if page.bean_classes else None
        return [
            CodeNode(
                file_path=str(file_path),
                language="jsp",
                node_type="ui_component",
                name=file_path.stem,
                qualified_name=str(file_path),
                source_code=text,
                line_start=1,
                line_end=text.count("\n") + 1,
                calls=list(page.bean_classes),
                domain=domain,
                framework_hints={
                    "framework": "jsp",
                    "bean_classes": page.bean_classes,
                    "form_actions": page.form_actions,
                    "includes": page.includes,
                    "title": page.title,
                    "is_tag_file": page.is_tag_file,
                },
            )
        ]
