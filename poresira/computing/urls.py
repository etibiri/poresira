# SPDX-License-Identifier: AGPL-3.0-or-later
from django.urls import path

from . import views

app_name = "computing"

urlpatterns = [
    path("compute/", views.JobListView.as_view(), name="job-list"),
    path("compute/jobs/<uuid:pk>/", views.JobDetailView.as_view(), name="job-detail"),
    path("compute/jobs/<uuid:pk>/submit/", views.JobSubmitView.as_view(), name="job-submit"),
    path("compute/jobs/<uuid:pk>/cancel/", views.JobCancelView.as_view(), name="job-cancel"),
    path("compute/jobs/<uuid:pk>/void/", views.JobVoidView.as_view(), name="job-void"),
    path("compute/jobs/<uuid:pk>/script/", views.JobScriptView.as_view(), name="job-script"),
    path("compute/runs/<uuid:pk>/<slug:kind>/new/", views.JobCreateView.as_view(), name="job-new"),
]
