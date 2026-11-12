# SPDX-License-Identifier: AGPL-3.0-or-later
from django.conf import settings


def site_information(request):
    """AGPL section 13: every page links to the source and states the deployed version."""
    return {
        "source_url": settings.PORESIRA_SOURCE_URL,
        "poresira_version": settings.PORESIRA_VERSION,
    }
