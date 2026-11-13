# SPDX-License-Identifier: AGPL-3.0-or-later
from django.urls import path

from . import views

app_name = "pcr"

urlpatterns = [
    path("pcr/", views.RunListView.as_view(), name="run-list"),
    path("pcr/new/", views.RunCreateView.as_view(), name="run-create"),
    path("pcr/<uuid:pk>/", views.RunDetailView.as_view(), name="run-detail"),
    path("pcr/<uuid:pk>/reactions/new/", views.ReactionAddView.as_view(), name="reaction-add"),
    path("pcr/<uuid:pk>/perform/", views.RunPerformView.as_view(), name="run-perform"),
    path("pcr/<uuid:pk>/close/", views.RunCloseView.as_view(), name="run-close"),
    path("pcr/<uuid:pk>/reopen/", views.RunReopenView.as_view(), name="run-reopen"),
    path("pcr/<uuid:pk>/void/", views.RunVoidView.as_view(), name="run-void"),
    path("pcr/reactions/<uuid:pk>/result/", views.ResultView.as_view(), name="reaction-result"),
    path("pcr/reactions/<uuid:pk>/void/", views.ReactionVoidView.as_view(), name="reaction-void"),
    path("pcr/assays/", views.AssayListView.as_view(), name="assay-list"),
    path("pcr/primers/", views.PrimerListView.as_view(), name="primer-list"),
    path("pcr/programs/", views.ProgramListView.as_view(), name="program-list"),
]
