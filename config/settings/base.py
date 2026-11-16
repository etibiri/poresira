# SPDX-License-Identifier: AGPL-3.0-or-later
from pathlib import Path

from django.contrib.messages import constants as message_levels

from config.env import env_bool, env_int, env_list, env_str

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
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "poresira.core.middleware.AuditContextMiddleware",
    "poresira.core.middleware.UserLanguageMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
]

AUTH_USER_MODEL = "core.User"
LOGIN_URL = "login"
LOGIN_REDIRECT_URL = "project-list"
LOGOUT_REDIRECT_URL = "login"

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
                "poresira.core.context_processors.site_information",
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

REDIS_URL = env_str("REDIS_URL", "redis://localhost:6379/0")
CELERY_BROKER_URL = REDIS_URL
CELERY_RESULT_BACKEND = CELERY_BROKER_URL
CELERY_TASK_ALWAYS_EAGER = env_bool("CELERY_TASK_ALWAYS_EAGER", False)
# Compute jobs are handled by the Slurm agent, a worker on the "slurm" queue.
CELERY_TASK_ROUTES = {
    "poresira.computing.tasks.*": {"queue": "slurm"},
    "poresira.deposits.tasks.*": {"queue": "deposits"},  # the only worker that reaches the archive
}

PORESIRA_SITE_PREFIX = env_str("PORESIRA_SITE_PREFIX", "WBF")
PORESIRA_SOURCE_URL = env_str("PORESIRA_SOURCE_URL", "https://github.com/etibiri/poresira")
PORESIRA_VERSION = env_str("PORESIRA_VERSION", "dev")

# Role the application connects as; migrations strip its write access to audit tables.
DATABASE_APP_ROLE = env_str("POSTGRES_APP_USER", "poresira_app")
# Only enable behind a reverse proxy that overwrites X-Forwarded-For.
PORESIRA_TRUST_X_FORWARDED_FOR = env_bool("PORESIRA_TRUST_X_FORWARDED_FOR", False)

# Compute jobs (see docs/computing.md). The commands and setup lines are written by the
# administrator and trusted; job parameters never reach a shell unquoted.
PORESIRA_DATA_DIR = env_str("PORESIRA_DATA_DIR", "/srv/poresira-data")
COMPUTING = {
    "OUTPUT_DIR": env_str("PORESIRA_COMPUTE_OUTPUT_DIR", f"{PORESIRA_DATA_DIR}/computing"),
    "SETUP": env_str("PORESIRA_COMPUTE_SETUP"),  # for example: module load dorado/0.9.0
    "DORADO": env_str("PORESIRA_DORADO_COMMAND", "dorado"),
    "DORADO_VERSION": env_str("PORESIRA_DORADO_VERSION"),
    "DORADO_MODELS_DIR": env_str("PORESIRA_DORADO_MODELS_DIR"),
    "POD5": env_str("PORESIRA_POD5_COMMAND", "pod5"),
    "POD5_VERSION": env_str("PORESIRA_POD5_VERSION"),
    "SLURM_BIN_DIR": env_str("PORESIRA_SLURM_BIN_DIR"),  # folder of sbatch; empty: use PATH
    "POLL_SECONDS": env_int("PORESIRA_COMPUTE_POLL_SECONDS", 60),
    "SLURM_PARTITION": env_str("PORESIRA_SLURM_PARTITION"),
    "SLURM_ACCOUNT": env_str("PORESIRA_SLURM_ACCOUNT"),
    "GPUS": env_int("PORESIRA_SLURM_GPUS", 1),
    "CPUS": env_int("PORESIRA_SLURM_CPUS", 8),
    "MEM_GB": env_int("PORESIRA_SLURM_MEM_GB", 64),
    "TIME": env_str("PORESIRA_SLURM_TIME", "24:00:00"),
}

# Deposits to the European Nucleotide Archive (see docs/deposits.md). The Webin account name
# and password are read from the environment of the agent only, never stored in the database.
ENA = {
    "WEBIN_USER": env_str("ENA_WEBIN_USER"),
    "WEBIN_PASSWORD": env_str("ENA_WEBIN_PASSWORD"),
    "TEST_URL": env_str("ENA_TEST_URL", "https://wwwdev.ebi.ac.uk/ena/submit/drop-box/submit"),
    "PRODUCTION_URL": env_str(
        "ENA_PRODUCTION_URL", "https://www.ebi.ac.uk/ena/submit/drop-box/submit"
    ),
    "WEBIN_CLI": env_str("ENA_WEBIN_CLI", "webin-cli"),  # may be several words, e.g. java -jar
    "WORK_DIR": env_str("ENA_WORK_DIR", f"{PORESIRA_DATA_DIR}/deposits"),
    "TIMEOUT_SECONDS": env_int("ENA_TIMEOUT_SECONDS", 120),
    "CLI_TIMEOUT_SECONDS": env_int("ENA_CLI_TIMEOUT_SECONDS", 6 * 3600),
}

# NCBI Submission Portal tables (see docs/deposits.md). The package asks for a metagenome organism.
NCBI = {"ORGANISM": env_str("PORESIRA_NCBI_ORGANISM", "plant metagenome")}

MESSAGE_TAGS = {message_levels.ERROR: "danger"}
