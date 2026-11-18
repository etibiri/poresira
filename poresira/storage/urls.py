# SPDX-License-Identifier: AGPL-3.0-or-later
from django.urls import path

from . import views

app_name = "storage"

urlpatterns = [
    path("files/", views.FileListView.as_view(), name="file-list"),
    path("files/<uuid:pk>/", views.FileDetailView.as_view(), name="file-detail"),
    path("files/<uuid:pk>/check/", views.FileVerifyView.as_view(), name="file-check"),
    path("files/<uuid:pk>/tier/", views.FileTierView.as_view(), name="file-tier"),
    path("files/<uuid:pk>/void/", views.FileVoidView.as_view(), name="file-void"),
    path(
        "files/register/<slug:link>/<uuid:pk>/",
        views.FileRegisterView.as_view(),
        name="file-register",
    ),
    path("files/backends/", views.BackendListView.as_view(), name="backend-list"),
    path("files/backends/new/", views.BackendCreateView.as_view(), name="backend-new"),
]
