"""Errors with stable reason slugs and exit codes (roadmap item M2.1).

Every user-facing failure is a ``DiscoveryError`` carrying a reason slug from
``REASONS``. The slug decides the exit code (``design/cli-v1.md`` §4) and is
stable for tests, scripts and telemetry.
"""

from __future__ import annotations

# Exit codes (design/cli-v1.md §4)
EXIT_OK = 0
EXIT_FAILURE = 1
EXIT_USAGE = 2
EXIT_BUDGET = 3
EXIT_LOCK = 4
EXIT_NOT_FOUND = 5
EXIT_DRIFT = 6

# Reason slug -> exit code (design/cli-v1.md §5). Add new slugs here only.
REASONS: dict[str, int] = {
    "failure": EXIT_FAILURE,
    "unexpected_error": EXIT_FAILURE,
    "migration_failed": EXIT_FAILURE,
    "fts_unavailable": EXIT_FAILURE,
    "sqlite_vec_unavailable": EXIT_FAILURE,
    "grammar_query_failed": EXIT_FAILURE,
    "provider_unreachable": EXIT_FAILURE,
    "model_not_invocable": EXIT_FAILURE,
    "push_auth_failed": EXIT_FAILURE,
    "init_needs_choice": EXIT_USAGE,
    "config_invalid_key": EXIT_USAGE,
    "config_invalid_value": EXIT_USAGE,
    "provider_not_configured": EXIT_USAGE,
    "cost_gate_no_tty": EXIT_USAGE,
    "cost_gate_declined": EXIT_USAGE,
    "contradictory_flags": EXIT_USAGE,
    "serve_remote_not_allowed": EXIT_USAGE,
    "resume_refused": EXIT_USAGE,
    "budget_exceeded": EXIT_BUDGET,
    "lock_held": EXIT_LOCK,
    "project_not_found": EXIT_NOT_FOUND,
    "not_indexed": EXIT_NOT_FOUND,
    "target_not_found": EXIT_NOT_FOUND,
    "nothing_to_check": EXIT_NOT_FOUND,
    "evidence_format_unknown": EXIT_USAGE,
    "drift_detected": EXIT_DRIFT,
}


class DiscoveryError(Exception):
    """A user-facing failure with a reason slug, optional hint and exit code."""

    def __init__(self, message: str, reason: str = "failure", hint: str | None = None,
                 exit_code: int | None = None):
        if reason not in REASONS:
            raise ValueError(f"unknown reason slug {reason!r}; register it in core/errors.py REASONS")
        super().__init__(message)
        self.message = message
        self.reason = reason
        self.hint = hint
        self.exit_code = REASONS[reason] if exit_code is None else exit_code

    def to_dict(self) -> dict:
        return {"reason": self.reason, "message": self.message, "hint": self.hint,
                "exit_code": self.exit_code}


def as_discovery_error(exc: BaseException) -> DiscoveryError | None:
    """Map known internal exceptions to user-facing errors (None if unknown)."""
    if isinstance(exc, DiscoveryError):
        return exc
    from ..config.loader import ConfigError
    from ..core.lock import LockHeld
    from ..store.migrate import MigrationError

    if isinstance(exc, ConfigError):
        return DiscoveryError(str(exc), "config_invalid_key",
                              hint="fix the key named above, or run `discover doctor`")
    if isinstance(exc, LockHeld):
        return DiscoveryError(str(exc), "lock_held",
                              hint="wait for the other run, or `discover doctor --repair` if it is gone")
    if isinstance(exc, MigrationError):
        return DiscoveryError(str(exc), "migration_failed", hint="run `discover doctor --repair`")
    return None
