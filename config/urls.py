# SPDX-License-Identifier: AGPL-3.0-or-later
from django.contrib import admin
from django.urls import path

urlpatterns = [
    path("admin/", admin.site.urls),
]
