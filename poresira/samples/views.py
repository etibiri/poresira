# SPDX-License-Identifier: AGPL-3.0-or-later
from django import forms
from django.contrib import messages
from django.contrib.auth.mixins import LoginRequiredMixin
from django.core.exceptions import PermissionDenied, ValidationError
from django.db.models import Count, Q
from django.shortcuts import get_object_or_404, redirect
from django.urls import reverse
from django.utils.translation import gettext as _
from django.views.generic import FormView, ListView, TemplateView

from poresira.core.forms import BootstrapFormMixin
from poresira.core.history import HistorySource, build_history
from poresira.core.models import Project
from poresira.core.permissions import can_edit_project
from poresira.deposits.collect import biological_sample
from poresira.deposits.known import known_for

from . import services
from .forms import (
    PAGE_SIZE,
    ChangeForm,
    CollectionEventForm,
    DerivativeForm,
    FieldSampleForm,
    MoveForm,
    QcForm,
    SampleEditForm,
    SampleFilterForm,
    SequenceRecordForm,
    StorageLocationForm,
    VoidForm,
)
from .lineage import family_tree
from .models import CollectionEvent, QcMeasurement, Sample, SampleEvent, StorageLocation
from .permissions import can_manage_storage

SAMPLE_HISTORY_FIELDS = (
    "label",
    "status",
    "barcode",
    "storage_location",
    "position",
    "concentration_ng_ul",
    "volume_ul",
    "host_taxon",
    "host_variety",
    "symptom_score",
    "symptom_notes",
    "notes",
    "voided_at",
    "void_reason",
)


class ServiceFormView(LoginRequiredMixin, FormView):
    """A form whose valid data goes to a service; rule violations come back as form errors."""

    template_name = "samples/form.html"
    title = ""
    submit_label = _("Save")
    success_message = ""

    def perform(self, cleaned_data):
        raise NotImplementedError

    def get_success_url(self) -> str:
        raise NotImplementedError

    def get_cancel_url(self) -> str:
        return self.get_success_url()

    def get_context_data(self, **kwargs):
        return super().get_context_data(
            title=self.title,
            submit_label=self.submit_label,
            cancel_url=self.get_cancel_url(),
            **kwargs,
        )

    def form_valid(self, form):
        try:
            self.result = self.perform(form.cleaned_data)
        except ValidationError as error:
            for message in error.messages:
                form.add_error(None, message)
            return self.form_invalid(form)
        except (services.SampleServiceError, ValueError) as error:
            form.add_error(None, str(error))
            return self.form_invalid(form)
        messages.success(self.request, self.success_message)
        return redirect(self.get_success_url())


class PickParentView(ServiceFormView):
    """Choose the record a new item hangs on (event, library, run), then go to its own form."""

    submit_label = _("Continue")
    label = ""
    target = ""  # URL name taking the chosen record's pk
    cancel = ""
    choices = None  # callable(user) -> queryset of records the user can see

    def get_form_class(self):
        queryset = self.choices(self.request.user).filter(
            project__in=Project.objects.editable_by(self.request.user)
        )

        class PickForm(BootstrapFormMixin, forms.Form):
            parent = forms.ModelChoiceField(queryset=queryset, label=self.label)

        return PickForm

    def form_valid(self, form):
        return redirect(self.target, form.cleaned_data["parent"].pk)

    def get_cancel_url(self) -> str:
        return reverse(self.cancel)


class SampleMixin:
    """Loads the sample from the URL among those the user can see, and requires write access."""

    require_edit = True

    @property
    def sample(self) -> Sample:
        if not hasattr(self, "_sample"):
            self._sample = get_object_or_404(
                Sample.objects.visible_to(self.request.user).select_related(
                    "project", "collection_event", "storage_location", "parent"
                ),
                pk=self.kwargs["pk"],
            )
        return self._sample

    def dispatch(self, request, *args, **kwargs):
        if request.user.is_authenticated and self.require_edit and not self.can_write():
            raise PermissionDenied
        return super().dispatch(request, *args, **kwargs)

    def can_write(self) -> bool:
        return not self.sample.is_voided and can_edit_project(
            self.request.user, self.sample.project
        )

    def get_success_url(self) -> str:
        return reverse("samples:sample-detail", args=[self.sample.pk])


class SampleListView(LoginRequiredMixin, ListView):
    paginate_by = PAGE_SIZE
    context_object_name = "samples"

    def get_filter_form(self) -> SampleFilterForm:
        if not hasattr(self, "_filter_form"):
            self._filter_form = SampleFilterForm(self.request.GET or None, user=self.request.user)
        return self._filter_form

    def get_queryset(self):
        samples = Sample.objects.visible_to(self.request.user).select_related(
            "project", "storage_location"
        )
        form = self.get_filter_form()
        if not form.is_valid():
            return samples.active()
        filters = form.cleaned_data
        if filters["q"]:
            samples = samples.filter(
                Q(identifier__icontains=filters["q"]) | Q(label__icontains=filters["q"])
            )
        for name in ("project", "kind", "status"):
            if filters[name]:
                samples = samples.filter(**{name: filters[name]})
        return samples if filters["include_voided"] else samples.active()

    def get_template_names(self):
        if self.request.headers.get("HX-Request"):
            return ["samples/_sample_results.html"]
        return ["samples/sample_list.html"]

    def get_context_data(self, **kwargs):
        query = self.request.GET.copy()
        query.pop("page", None)
        return super().get_context_data(
            filter_form=self.get_filter_form(),
            query_string=query.urlencode(),
            can_create=Project.objects.editable_by(self.request.user).exists(),
            **kwargs,
        )


class SampleDetailView(SampleMixin, LoginRequiredMixin, TemplateView):
    template_name = "samples/sample_detail.html"
    require_edit = False

    def get_context_data(self, **kwargs):
        sample = self.sample
        history = build_history(
            [
                HistorySource(
                    events=SampleEvent.objects.filter(pgh_obj=sample).order_by(
                        "pgh_created_at", "pgh_id"
                    ),
                    field_names=SAMPLE_HISTORY_FIELDS,
                    subject_for=lambda event: _("Sample"),
                )
            ]
        )
        return super().get_context_data(
            sample=sample,
            can_edit=not sample.is_voided and can_edit_project(self.request.user, sample.project),
            family=family_tree(sample),
            measurements=sample.qc_measurements.select_related("measured_by"),
            sequences=sample.sequence_records.active(),
            pcr_reactions=sample.pcr_reactions.active()
            .filter(run__voided_at__isnull=True)
            .select_related("run__assay")
            .order_by("run__identifier", "position"),
            public=known_for(biological_sample(sample)),
            files=sample.files.active().select_related("backend"),
            register_url=(
                reverse("storage:file-register", args=["sample", sample.pk])
                if not sample.is_voided and can_edit_project(self.request.user, sample.project)
                else ""
            ),
            library_memberships=sample.library_memberships.active()
            .filter(library__voided_at__isnull=True)
            .select_related("library")
            .prefetch_related("library__runs__flow_cell"),
            entries=history,
            **kwargs,
        )


class SampleEditView(SampleMixin, ServiceFormView):
    form_class = SampleEditForm
    title = _("Edit description")
    success_message = _("Sample updated.")

    def get_form_kwargs(self):
        return {**super().get_form_kwargs(), "instance": self.sample}

    def perform(self, cleaned_data):
        return services.edit_sample(user=self.request.user, sample=self.sample, **cleaned_data)


class SampleDeriveView(SampleMixin, ServiceFormView):
    form_class = DerivativeForm
    title = _("Derive a sample")
    submit_label = _("Create")
    success_message = _("Derived sample created.")

    def get_form_kwargs(self):
        return {**super().get_form_kwargs(), "parent": self.sample}

    def perform(self, cleaned_data):
        attributes = {
            name: value for name, value in cleaned_data.items() if value not in (None, "")
        }
        kind = attributes.pop("kind")
        return services.derive_sample(
            user=self.request.user, parent=self.sample, kind=kind, **attributes
        )

    def get_success_url(self) -> str:
        return reverse("samples:sample-detail", args=[self.result.pk])

    def get_cancel_url(self) -> str:
        return reverse("samples:sample-detail", args=[self.sample.pk])


class SampleMoveView(SampleMixin, ServiceFormView):
    form_class = MoveForm
    title = _("Move sample")
    success_message = _("Sample moved.")

    def get_initial(self):
        return {"location": self.sample.storage_location, "position": self.sample.position}

    def perform(self, cleaned_data):
        return services.move_sample(
            user=self.request.user,
            sample=self.sample,
            location=cleaned_data["location"],
            position=cleaned_data["position"],
            reason=cleaned_data["reason"],
        )


class SampleChangeView(SampleMixin, ServiceFormView):
    form_class = ChangeForm
    title = _("Change status, barcode or quantities")
    success_message = _("Sample updated.")

    def get_initial(self):
        sample = self.sample
        return {
            "status": sample.status,
            "barcode": sample.barcode,
            "concentration_ng_ul": sample.concentration_ng_ul,
            "volume_ul": sample.volume_ul,
        }

    def perform(self, cleaned_data):
        reason = cleaned_data.pop("reason")
        return services.change_sample(
            user=self.request.user, sample=self.sample, reason=reason, **cleaned_data
        )


class SampleQcView(SampleMixin, ServiceFormView):
    form_class = QcForm
    title = _("Record a measurement")
    success_message = _("Measurement recorded.")

    def perform(self, cleaned_data):
        form_values = {**cleaned_data}
        apply_to_sample = form_values.pop("apply_to_sample")
        method = form_values.pop("method")
        values = {k: v for k, v in form_values.items() if v not in (None, "")}
        return services.record_qc(
            user=self.request.user,
            sample=self.sample,
            method=method,
            apply_to_sample=apply_to_sample,
            **values,
        )


class SampleSequenceView(SampleMixin, ServiceFormView):
    form_class = SequenceRecordForm
    title = _("Record a GenBank sequence")
    submit_label = _("Record")
    success_message = _("Sequence recorded.")

    def perform(self, cleaned_data):
        data = {k: v for k, v in cleaned_data.items() if v not in (None, "")}
        return services.record_sequence(user=self.request.user, sample=self.sample, **data)


class SampleVoidView(SampleMixin, ServiceFormView):
    form_class = VoidForm
    title = _("Void sample")
    submit_label = _("Void")
    success_message = _("Sample voided.")

    def perform(self, cleaned_data):
        return services.void_record(
            user=self.request.user, record=self.sample, reason=cleaned_data["reason"]
        )

    def get_cancel_url(self) -> str:
        return reverse("samples:sample-detail", args=[self.sample.pk])


class QcVoidView(ServiceFormView):
    form_class = VoidForm
    title = _("Void measurement")
    submit_label = _("Void")
    success_message = _("Measurement voided.")

    @property
    def measurement(self) -> QcMeasurement:
        if not hasattr(self, "_measurement"):
            self._measurement = get_object_or_404(
                QcMeasurement.objects.visible_to(self.request.user).select_related(
                    "sample__project"
                ),
                pk=self.kwargs["pk"],
            )
        return self._measurement

    def perform(self, cleaned_data):
        return services.void_record(
            user=self.request.user, record=self.measurement, reason=cleaned_data["reason"]
        )

    def get_success_url(self) -> str:
        return reverse("samples:sample-detail", args=[self.measurement.sample_id])


class EventListView(LoginRequiredMixin, ListView):
    template_name = "samples/event_list.html"
    context_object_name = "events"
    paginate_by = PAGE_SIZE

    def get_queryset(self):
        return (
            CollectionEvent.objects.visible_to(self.request.user)
            .active()
            .select_related("project")
            .annotate(sample_count=Count("samples", filter=Q(samples__voided_at__isnull=True)))
            .order_by("-collected_on", "identifier")
        )


class EventDetailView(LoginRequiredMixin, TemplateView):
    template_name = "samples/event_detail.html"

    def get_context_data(self, **kwargs):
        event = get_object_or_404(
            CollectionEvent.objects.visible_to(self.request.user).select_related("project"),
            pk=kwargs["pk"],
        )
        return super().get_context_data(
            event=event,
            can_edit=not event.is_voided and can_edit_project(self.request.user, event.project),
            samples=event.samples.active().select_related("storage_location"),
            **kwargs,
        )


class EventCreateView(ServiceFormView):
    form_class = CollectionEventForm
    title = _("New collection event")
    submit_label = _("Create")
    success_message = _("Collection event created.")

    def get_form_kwargs(self):
        editable = Project.objects.editable_by(self.request.user)
        initial = {"project": self.request.GET.get("project")}
        return {**super().get_form_kwargs(), "editable_projects": editable, "initial": initial}

    def perform(self, cleaned_data):
        project = cleaned_data.pop("project")
        attributes = {k: v for k, v in cleaned_data.items() if v not in (None, "")}
        return services.create_collection_event(
            user=self.request.user, project=project, **attributes
        )

    def get_success_url(self) -> str:
        return reverse("samples:event-detail", args=[self.result.pk])

    def get_cancel_url(self) -> str:
        return reverse("samples:event-list")


class FieldSampleCreateView(ServiceFormView):
    form_class = FieldSampleForm
    title = _("Register a field sample")
    submit_label = _("Create")
    success_message = _("Field sample registered.")

    @property
    def event(self) -> CollectionEvent:
        if not hasattr(self, "_event"):
            self._event = get_object_or_404(
                CollectionEvent.objects.visible_to(self.request.user).select_related("project"),
                pk=self.kwargs["pk"],
            )
        return self._event

    def dispatch(self, request, *args, **kwargs):
        if request.user.is_authenticated and (
            self.event.is_voided or not can_edit_project(request.user, self.event.project)
        ):
            raise PermissionDenied
        return super().dispatch(request, *args, **kwargs)

    def perform(self, cleaned_data):
        attributes = {k: v for k, v in cleaned_data.items() if v not in (None, "")}
        return services.register_field_sample(
            user=self.request.user, collection_event=self.event, **attributes
        )

    def get_success_url(self) -> str:
        return reverse("samples:sample-detail", args=[self.result.pk])

    def get_cancel_url(self) -> str:
        return reverse("samples:event-detail", args=[self.event.pk])


class StorageTreeView(LoginRequiredMixin, TemplateView):
    template_name = "samples/storage_tree.html"

    def get_context_data(self, **kwargs):
        locations = list(
            StorageLocation.objects.active().annotate(
                sample_count=Count("samples", filter=Q(samples__voided_at__isnull=True))
            )
        )
        children = {}
        for location in locations:
            children.setdefault(location.parent_id, []).append(location)

        def walk(parent_id, depth):
            for location in sorted(children.get(parent_id, []), key=lambda node: node.name):
                yield location, depth
                yield from walk(location.pk, depth + 1)

        return super().get_context_data(
            nodes=list(walk(None, 0)), can_manage=can_manage_storage(self.request.user), **kwargs
        )


class StorageCreateView(ServiceFormView):
    form_class = StorageLocationForm
    title = _("New storage location")
    submit_label = _("Create")
    success_message = _("Storage location created.")

    def dispatch(self, request, *args, **kwargs):
        if request.user.is_authenticated and not can_manage_storage(request.user):
            raise PermissionDenied
        return super().dispatch(request, *args, **kwargs)

    def perform(self, cleaned_data):
        name, kind = cleaned_data.pop("name"), cleaned_data.pop("kind")
        parent = cleaned_data.pop("parent")
        attributes = {k: v for k, v in cleaned_data.items() if v not in (None, "")}
        return services.create_storage_location(
            user=self.request.user, name=name, kind=kind, parent=parent, **attributes
        )

    def get_success_url(self) -> str:
        return reverse("samples:storage-tree")
