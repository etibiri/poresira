# SPDX-License-Identifier: AGPL-3.0-or-later
from config.env import env_bool

from .base import *  # noqa: F403

DEBUG = env_bool("DJANGO_DEBUG", True)
SECRET_KEY = SECRET_KEY or "insecure-development-key"  # noqa: F405
STORAGES["staticfiles"] = {  # noqa: F405
    "BACKEND": "django.contrib.staticfiles.storage.StaticFilesStorage"
}
