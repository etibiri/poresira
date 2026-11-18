# SPDX-License-Identifier: AGPL-3.0-or-later
from django.urls import path
from django.utils.translation import gettext_lazy as _

from . import views
from .models import CollectionEvent

app_name = "samples"

urlpatterns = [
    path("samples/", views.SampleListView.as_view(), name="sample-list"),
    path(
        "samples/new/",
        views.PickParentView.as_view(
            title=_("New sample"),
            label=_("Collection event"),
            target="samples:field-sample-create",
            cancel="samples:sample-list",
            choices=lambda user: CollectionEvent.objects.visible_to(user).active(),
        ),
        name="sample-new",
    ),
    path("samples/<uuid:pk>/", views.SampleDetailView.as_view(), name="sample-detail"),
    path("samples/<uuid:pk>/edit/", views.SampleEditView.as_view(), name="sample-edit"),
    path("samples/<uuid:pk>/derive/", views.SampleDeriveView.as_view(), name="sample-derive"),
    path("samples/<uuid:pk>/move/", views.SampleMoveView.as_view(), name="sample-move"),
    path("samples/<uuid:pk>/change/", views.SampleChangeView.as_view(), name="sample-change"),
    path(
        "samples/<uuid:pk>/sequences/new/",
        views.SampleSequenceView.as_view(),
        name="sample-sequence",
    ),
    path("samples/<uuid:pk>/qc/", views.SampleQcView.as_view(), name="sample-qc"),
    path("samples/<uuid:pk>/void/", views.SampleVoidView.as_view(), name="sample-void"),
    path("measurements/<uuid:pk>/void/", views.QcVoidView.as_view(), name="qc-void"),
    path("events/", views.EventListView.as_view(), name="event-list"),
    path("events/new/", views.EventCreateView.as_view(), name="event-create"),
    path("events/<uuid:pk>/", views.EventDetailView.as_view(), name="event-detail"),
    path(
        "events/<uuid:pk>/samples/new/",
        views.FieldSampleCreateView.as_view(),
        name="field-sample-create",
    ),
    path("storage/", views.StorageTreeView.as_view(), name="storage-tree"),
    path("storage/new/", views.StorageCreateView.as_view(), name="storage-create"),
]
