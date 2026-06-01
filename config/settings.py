"""Django settings for the local conformance suite runtime."""

import os
import sys
from pathlib import Path

# Build paths inside the project like this: BASE_DIR / 'subdir'.
BASE_DIR = Path(__file__).resolve().parent.parent


# Environment-specific settings
# See https://docs.djangoproject.com/en/6.0/howto/deployment/checklist/


def _get_allowed_hosts() -> list[str]:
    """Parse DJANGO_ALLOWED_HOSTS into a clean list, stripping whitespace and empty entries.

    Returns:
        List of non-empty, whitespace-stripped hostnames from the environment variable.
    """
    raw = os.environ.get("DJANGO_ALLOWED_HOSTS", "")
    return [host.strip() for host in raw.split(",") if host.strip()]


SECRET_KEY = os.environ.get("DJANGO_SECRET_KEY", "django-insecure-dev-tooling-fallback")

DEBUG = os.environ.get("DJANGO_DEBUG", "false").lower() == "true"

ALLOWED_HOSTS = _get_allowed_hosts()

# Conformance REST API: defence-in-depth loopback guard. When False
# (the default), API views reject any request whose REMOTE_ADDR is not
# a loopback address (127.0.0.0/8 or ::1). Set
# CONFORMANCE_API_ALLOW_NON_LOCAL=true to disable the guard — only do
# this when the API is fronted by an authenticated reverse proxy that
# you control. The primary control remains binding the published
# Docker port to 127.0.0.1; this setting backstops misconfiguration.
API_ALLOW_NON_LOCAL = os.environ.get("CONFORMANCE_API_ALLOW_NON_LOCAL", "false").lower() == "true"

# Reserved hostname sent by the container HEALTHCHECK (see Dockerfile).
# The healthcheck runs inside the container against ``http://localhost:8000/``
# but sends an explicit ``Host: healthcheck.local`` header so the probe does
# not depend on operators including ``localhost`` in ``DJANGO_ALLOWED_HOSTS``.
# This token is reserved for the in-container probe and is unconditionally
# trusted; it is not routable from outside the container.
HEALTHCHECK_HOST = "healthcheck.local"
if HEALTHCHECK_HOST not in ALLOWED_HOSTS:
    ALLOWED_HOSTS.append(HEALTHCHECK_HOST)

# Production safety: enforce production-grade configuration when the
# user has explicitly set DJANGO_SECRET_KEY and disabled DEBUG. Skipped
# during tooling runs (mypy, pytest, ruff, etc.) because those import
# this module to introspect types/symbols and have no business being
# blocked by deployment guards — the guard's job is to refuse to *serve*
# an insecure production process, not to refuse to type-check one.
_TOOLING_ENTRYPOINTS = ("mypy", "pytest", "ruff", "interrogate", "pydoclint", "coverage")
_is_tooling_run = any(tool in Path(sys.argv[0] if sys.argv else "").name for tool in _TOOLING_ENTRYPOINTS)
_explicitly_configured = "DJANGO_SECRET_KEY" in os.environ
if _explicitly_configured and not DEBUG and not _is_tooling_run:
    if not SECRET_KEY.strip():
        raise ValueError("DJANGO_SECRET_KEY must not be empty when DEBUG is disabled")
    if SECRET_KEY.startswith("django-insecure-"):
        raise ValueError("DJANGO_SECRET_KEY must be set to a secure value when DEBUG is disabled")
    if not ALLOWED_HOSTS:
        raise ValueError("DJANGO_ALLOWED_HOSTS must be set when DEBUG is disabled")


# Application definition

INSTALLED_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
]

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
]

ROOT_URLCONF = "config.urls"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
            ],
        },
    },
]

WSGI_APPLICATION = "config.wsgi.application"


# Database
# https://docs.djangoproject.com/en/6.0/ref/settings/#databases

DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.sqlite3",
        "NAME": BASE_DIR / "db.sqlite3",
    }
}


# Password validation
# https://docs.djangoproject.com/en/6.0/ref/settings/#auth-password-validators

AUTH_PASSWORD_VALIDATORS = [
    {
        "NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator",
    },
    {
        "NAME": "django.contrib.auth.password_validation.MinimumLengthValidator",
    },
    {
        "NAME": "django.contrib.auth.password_validation.CommonPasswordValidator",
    },
    {
        "NAME": "django.contrib.auth.password_validation.NumericPasswordValidator",
    },
]


# Internationalization
# https://docs.djangoproject.com/en/6.0/topics/i18n/

LANGUAGE_CODE = "en-us"

TIME_ZONE = "UTC"

USE_I18N = True

USE_TZ = True


# Static files (CSS, JavaScript, Images)
# https://docs.djangoproject.com/en/6.0/howto/static-files/

STATIC_URL = "static/"

# Default primary key field type
# https://docs.djangoproject.com/en/6.0/ref/settings/#default-auto-field
DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "formatters": {
        "verbose": {
            "format": "{levelname} {asctime} {name} {message}",
            "style": "{",
        },
    },
    "handlers": {
        "console": {
            "class": "logging.StreamHandler",
            "formatter": "verbose",
        },
    },
    "root": {
        "handlers": ["console"],
        "level": "INFO",
    },
}
