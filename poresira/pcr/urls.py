# SPDX-License-Identifier: AGPL-3.0-or-later
from django.urls import path
from django.utils.translation import gettext_lazy as _

from . import services, views
from .forms import AssayForm, MasterMixForm, PrimerForm, ProgramForm

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
    path(
        "pcr/assays/new/",
        views.ReferenceCreateView.as_view(
            form_class=AssayForm,
            service=services.create_assay,
            title=_("New assay"),
            list_url="pcr:assay-list",
        ),
        name="assay-new",
    ),
    path(
        "pcr/primers/new/",
        views.ReferenceCreateView.as_view(
            form_class=PrimerForm,
            service=services.create_primer,
            title=_("New primer"),
            list_url="pcr:primer-list",
        ),
        name="primer-new",
    ),
    path(
        "pcr/programs/new/",
        views.ReferenceCreateView.as_view(
            form_class=ProgramForm,
            service=services.create_cycling_program,
            title=_("New cycling program"),
            list_url="pcr:program-list",
        ),
        name="program-new",
    ),
    path("pcr/primers/", views.PrimerListView.as_view(), name="primer-list"),
    path("pcr/mixes/", views.MasterMixListView.as_view(), name="master-mix-list"),
    path(
        "pcr/mixes/new/",
        views.ReferenceCreateView.as_view(
            form_class=MasterMixForm,
            service=services.create_master_mix,
            title=_("New master mix"),
            list_url="pcr:master-mix-list",
        ),
        name="master-mix-new",
    ),
    path("pcr/programs/", views.ProgramListView.as_view(), name="program-list"),
]
