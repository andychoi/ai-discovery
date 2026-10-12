"""Pipeline stages (ADR-0003, roadmap M1.6–M1.8).

Each stage module moves one block of ``pipeline.run_pipeline`` **verbatim**:
``run(ctx)`` does the work, ``rehydrate(ctx)`` replaces the old ``else:``
resume branch (reload outputs without recomputing or calling an LLM), and
``execute(ctx)`` picks one of the two from the phase gate. Front half (M1.6):
``resolve``, ``walk``, ``parse``, ``domains`` + ``graph``, ``slices``,
``sql``, ``entities``. The back half moves in M1.7; ``StageRunner`` and
fingerprints arrive in M1.8.
"""
