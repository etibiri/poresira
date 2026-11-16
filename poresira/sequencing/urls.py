# SPDX-License-Identifier: AGPL-3.0-or-later
from django.urls import path

from . import views

app_name = "sequencing"

urlpatterns = [
    path("sequencing/", views.RunListView.as_view(), name="run-list"),
    path("sequencing/runs/<uuid:pk>/", views.RunDetailView.as_view(), name="run-detail"),
    path("sequencing/runs/<uuid:pk>/start/", views.RunStartView.as_view(), name="run-start"),
    path(
        "sequencing/runs/<uuid:pk>/complete/", views.RunCompleteView.as_view(), name="run-complete"
    ),
    path("sequencing/runs/<uuid:pk>/abort/", views.RunAbortView.as_view(), name="run-abort"),
    path("sequencing/runs/<uuid:pk>/reopen/", views.RunReopenView.as_view(), name="run-reopen"),
    path("sequencing/runs/<uuid:pk>/void/", views.RunVoidView.as_view(), name="run-void"),
    path(
        "sequencing/runs/<uuid:pk>/members/<uuid:member_pk>/result/",
        views.ResultView.as_view(),
        name="result",
    ),
    path("sequencing/libraries/", views.LibraryListView.as_view(), name="library-list"),
    path("sequencing/libraries/new/", views.LibraryCreateView.as_view(), name="library-create"),
    path(
        "sequencing/libraries/<uuid:pk>/",
        views.LibraryDetailView.as_view(),
        name="library-detail",
    ),
    path(
        "sequencing/libraries/<uuid:pk>/members/new/",
        views.MemberAddView.as_view(),
        name="member-add",
    ),
    path(
        "sequencing/libraries/<uuid:pk>/seal/", views.LibrarySealView.as_view(), name="library-seal"
    ),
    path("sequencing/libraries/<uuid:pk>/runs/new/", views.RunPlanView.as_view(), name="run-plan"),
    path(
        "sequencing/libraries/<uuid:pk>/void/", views.LibraryVoidView.as_view(), name="library-void"
    ),
    path("sequencing/members/<uuid:pk>/void/", views.MemberVoidView.as_view(), name="member-void"),
    path("sequencing/flow-cells/", views.FlowCellListView.as_view(), name="flow-cell-list"),
    path(
        "sequencing/flow-cells/new/",
        views.FlowCellRegisterView.as_view(),
        name="flow-cell-register",
    ),
    path("sequencing/reference/", views.ReferenceView.as_view(), name="reference"),
]
