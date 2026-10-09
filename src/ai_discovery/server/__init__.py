"""Wiki web server (roadmap M4): FastAPI API v1 + bundled SPA."""

from .app import BindRefused, check_bind, create_app

__all__ = ["BindRefused", "check_bind", "create_app"]
