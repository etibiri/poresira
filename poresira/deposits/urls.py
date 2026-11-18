# SPDX-License-Identifier: AGPL-3.0-or-later
from django.urls import path
from django.utils.translation import gettext_lazy as _

from poresira.samples.views import PickParentView
from poresira.sequencing.models import SequencingRun

from . import views

app_name = "deposits"

urlpatterns = [
    path("deposits/", views.DepositListView.as_view(), name="deposit-list"),
    path(
        "deposits/new/",
        PickParentView.as_view(
            title=_("New deposit"),
            label=_("Sequencing run"),
            target="deposits:deposit-new",
            cancel="deposits:deposit-list",
            choices=lambda user: SequencingRun.objects.visible_to(user).active(),
        ),
        name="deposit-pick",
    ),
    path(
        "deposits/accessions/new/",
        views.PublicAccessionCreateView.as_view(),
        name="accession-new",
    ),
    path("deposits/<uuid:pk>/", views.DepositDetailView.as_view(), name="deposit-detail"),
    path("deposits/<uuid:pk>/submit/", views.DepositSubmitView.as_view(), name="deposit-submit"),
    path(
        "deposits/<uuid:pk>/production/",
        views.DepositProductionView.as_view(),
        name="deposit-production",
    ),
    path("deposits/<uuid:pk>/void/", views.DepositVoidView.as_view(), name="deposit-void"),
    path("deposits/<uuid:pk>/ro-crate/", views.RoCrateView.as_view(), name="deposit-ro-crate"),
    path(
        "deposits/<uuid:pk>/submission-files/",
        views.SubmissionFilesView.as_view(),
        name="deposit-files",
    ),
    path(
        "deposits/items/<uuid:pk>/leave-out/",
        views.ItemLeaveOutView.as_view(),
        name="item-leave-out",
    ),
    path("deposits/runs/<uuid:pk>/new/", views.DepositCreateView.as_view(), name="deposit-new"),
]
