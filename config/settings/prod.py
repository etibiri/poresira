# SPDX-License-Identifier: AGPL-3.0-or-later
from django.core.exceptions import ImproperlyConfigured

from config.env import env_bool

from .base import *  # noqa: F403

if not SECRET_KEY:  # noqa: F405
    raise ImproperlyConfigured("DJANGO_SECRET_KEY must be set in production.")

# Lab deployments may run over plain HTTP on a local network.
PORESIRA_HTTPS = env_bool("PORESIRA_HTTPS", True)
SESSION_COOKIE_SECURE = PORESIRA_HTTPS
CSRF_COOKIE_SECURE = PORESIRA_HTTPS
SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https") if PORESIRA_HTTPS else None
