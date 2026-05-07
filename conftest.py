import os

# Provide dummy environment variables required by Django settings so that
# pytest and mypy (via django-stubs) can initialise without relying on the
# Makefile or CI env.  These are safe dummy values — never used in production.
os.environ.setdefault("DJANGO_SECRET_KEY", "test-dummy-key-not-for-production")
os.environ.setdefault("DJANGO_ALLOWED_HOSTS", "localhost,127.0.0.1")
