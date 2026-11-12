# SPDX-License-Identifier: AGPL-3.0-or-later
from pathlib import Path

from config.env import env_bool, env_list, env_str

BASE_DIR = Path(__file__).resolve().parent.parent.parent

SECRET_KEY = env_str("DJANGO_SECRET_KEY")
DEBUG = False
ALLOWED_HOSTS = env_list("DJANGO_ALLOWED_HOSTS", "localhost,127.0.0.1")

INSTALLED_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "rest_framework",
    "pgtrigger",
    "pghistory",
    "poresira.core",
    "poresira.samples",
    "poresira.pcr",
    "poresira.sequencing",
    "poresira.storage",
    "poresira.computing",
    "poresira.deposits",
]

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "whitenoise.middleware.WhiteNoiseMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.locale.LocaleMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "poresira.core.middleware.AuditContextMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
]

AUTH_USER_MODEL = "core.User"

ROOT_URLCONF = "config.urls"
WSGI_APPLICATION = "config.wsgi.application"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [BASE_DIR / "templates"],
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

DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.postgresql",
        "NAME": env_str("POSTGRES_DB", "poresira"),
        "HOST": env_str("POSTGRES_HOST", "localhost"),
        "PORT": env_str("POSTGRES_PORT", "5432"),
        "USER": env_str("POSTGRES_USER", "poresira_app"),
        "PASSWORD": env_str("POSTGRES_PASSWORD"),
    },
}

AUTH_PASSWORD_VALIDATORS = [
    {"NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator"},
    {"NAME": "django.contrib.auth.password_validation.MinimumLengthValidator"},
    {"NAME": "django.contrib.auth.password_validation.CommonPasswordValidator"},
    {"NAME": "django.contrib.auth.password_validation.NumericPasswordValidator"},
]

LANGUAGE_CODE = env_str("PORESIRA_DEFAULT_LANGUAGE", "en")
LANGUAGES = [("en", "English"), ("fr", "Français")]
LOCALE_PATHS = [BASE_DIR / "locale"]
TIME_ZONE = env_str("PORESIRA_TIME_ZONE", "Africa/Ouagadougou")
USE_I18N = True
USE_TZ = True

STATIC_URL = "static/"
STATIC_ROOT = BASE_DIR / "staticfiles"
STATICFILES_DIRS = [BASE_DIR / "static"]
STORAGES = {
    "default": {"BACKEND": "django.core.files.storage.FileSystemStorage"},
    "staticfiles": {"BACKEND": "whitenoise.storage.CompressedManifestStaticFilesStorage"},
}

CELERY_BROKER_URL = env_str("REDIS_URL", "redis://localhost:6379/0")
CELERY_RESULT_BACKEND = CELERY_BROKER_URL
CELERY_TASK_ALWAYS_EAGER = env_bool("CELERY_TASK_ALWAYS_EAGER", False)

PORESIRA_SITE_PREFIX = env_str("PORESIRA_SITE_PREFIX", "WBF")
PORESIRA_SOURCE_URL = env_str("PORESIRA_SOURCE_URL", "https://github.com/etibiri/poresira")
PORESIRA_VERSION = env_str("PORESIRA_VERSION", "dev")

# Role the application connects as; migrations strip its write access to audit tables.
DATABASE_APP_ROLE = env_str("POSTGRES_APP_USER", "poresira_app")
# Only enable behind a reverse proxy that overwrites X-Forwarded-For.
PORESIRA_TRUST_X_FORWARDED_FOR = env_bool("PORESIRA_TRUST_X_FORWARDED_FOR", False)
