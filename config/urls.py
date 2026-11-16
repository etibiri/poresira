# SPDX-License-Identifier: AGPL-3.0-or-later
from django.contrib import admin
from django.urls import include, path

urlpatterns = [
    path("admin/", admin.site.urls),
    path("", include("poresira.core.urls")),
    path("", include("poresira.samples.urls")),
    path("", include("poresira.pcr.urls")),
    path("", include("poresira.sequencing.urls")),
    path("", include("poresira.storage.urls")),
]
