"""Numbered schema migrations: ``m<NNNN>_<name>.py`` modules with ``up(conn)``.

Rules (M1.4):
* Never edit a migration that has shipped; add a new file.
* Numbers are contiguous from 0001; reserve the next number in
  docs/roadmap/PROGRESS.md before creating the file.
* ``up(conn)`` must not commit — the runner wraps it in a transaction and
  records it in ``schema_migrations``.
"""
