"""Runtime-only container startup code (entrypoint, healthcheck).

Kept separate from ``conformance/`` (the application) and ``scripts/`` (CI/
release tooling): this package's modules run *inside* the built container as
PID 1 and the container ``HEALTHCHECK``, so they must work with only the
standard library plus already-installed application dependencies, and
without a shell.
"""
