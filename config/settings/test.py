# SPDX-License-Identifier: AGPL-3.0-or-later
from .base import *  # noqa: F403

SECRET_KEY = "insecure-test-key"  # noqa: S105
PASSWORD_HASHERS = ["django.contrib.auth.hashers.MD5PasswordHasher"]
CELERY_TASK_ALWAYS_EAGER = True
STORAGES["staticfiles"] = {  # noqa: F405
    "BACKEND": "django.contrib.staticfiles.storage.StaticFilesStorage"
}
