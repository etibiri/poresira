# SPDX-License-Identifier: AGPL-3.0-or-later
from django.contrib.auth.mixins import LoginRequiredMixin
from django.core.exceptions import PermissionDenied
from django.db.models import Count, Q
from django.shortcuts import get_object_or_404
from django.urls import reverse
from django.utils.translation import gettext as _
from django.utils.translation import gettext_lazy
from django.views.generic import ListView, TemplateView

from poresira.core.history import HistorySource, build_history
from poresira.core.permissions import can_edit_project
from poresira.pcr.models import PcrRun
from poresira.samples.models import Sample
from poresira.samples.views import ServiceFormView
from poresira.sequencing.models import SequencingRun

from . import services
from .backends import FileNotReadableError
from .forms import (
    PAGE_SIZE,
    FileFilterForm,
    ReasonForm,
    RegisterFileForm,
    TierForm,
    VoidForm,
)
from .models import DataFile, DataFileEvent, StorageBackend
from .tasks import verify_file_task

FILE_HISTORY_FIELDS = ("tier", "notes", "voided_at", "void_reason")
# URL segment -> (model, keyword of ``register_file``, route of the record's page)
LINKS = {
    "sequencing-run": (SequencingRun, "sequencing_run", "sequencing:run-detail"),
    "pcr-run": (PcrRun, "pcr_run", "pcr:run-detail"),
    "sample": (Sample, "sample", "samples:sample-detail"),
}


class StorageServiceFormView(ServiceFormView):
    """Like the samples forms, and storage errors also come back as form errors."""

    def form_valid(self, form):
        try:
            return super().form_valid(form)
        except (services.StorageServiceError, FileNotReadableError) as error:
            form.add_error(None, str(error))
            return self.form_invalid(form)


class FileMixin:
    """Loads the file among those the user can see; writing needs edit rights on its project."""

    require_edit = True

    @property
    def file(self) -> DataFile:
        if not hasattr(self, "_file"):
            self._file = get_object_or_404(
                DataFile.objects.visible_to(self.request.user).select_related(
                    "project", "backend", "sequencing_run", "pcr_run", "sample", "registered_by"
                ),
                pk=self.kwargs["pk"],
            )
        return self._file

    def dispatch(self, request, *args, **kwargs):
        if request.user.is_authenticated and self.require_edit and not self.can_write():
            raise PermissionDenied
        return super().dispatch(request, *args, **kwargs)

    def can_write(self) -> bool:
        return not self.file.is_voided and can_edit_project(self.request.user, self.file.project)

    def get_success_url(self) -> str:
        return reverse("storage:file-detail", args=[self.file.pk])


class FileListView(LoginRequiredMixin, ListView):
    template_name = "storage/file_list.html"
    paginate_by = PAGE_SIZE
    context_object_name = "files"

    def get_filter_form(self) -> FileFilterForm:
        if not hasattr(self, "_filter_form"):
            self._filter_form = FileFilterForm(self.request.GET or None, user=self.request.user)
        return self._filter_form

    def get_queryset(self):
        files = (
            DataFile.objects.visible_to(self.request.user)
            .select_related("project", "backend")
            .order_by("-created_at")
        )
        form = self.get_filter_form()
        if not form.is_valid():
            return files.active()
        filters = form.cleaned_data
        for name in ("project", "backend", "kind", "tier", "integrity"):
            if filters[name]:
                files = files.filter(**{name: filters[name]})
        if filters["q"]:
            files = files.filter(path__icontains=filters["q"])
        return files if filters["include_voided"] else files.active()

    def get_context_data(self, **kwargs):
        query = self.request.GET.copy()
        query.pop("page", None)
        return super().get_context_data(
            filter_form=self.get_filter_form(), query_string=query.urlencode(), **kwargs
        )


class FileDetailView(FileMixin, LoginRequiredMixin, TemplateView):
    template_name = "storage/file_detail.html"
    require_edit = False

    def get_context_data(self, **kwargs):
        file = self.file
        history = build_history(
            [
                HistorySource(
                    events=DataFileEvent.objects.filter(pgh_obj=file).order_by(
                        "pgh_created_at", "pgh_id"
                    ),
                    field_names=FILE_HISTORY_FIELDS,
                    subject_for=lambda event: _("File"),
                )
            ]
        )
        route, record = next(
            (route, getattr(file, name))
            for _model, name, route in LINKS.values()
            if getattr(file, name) is not None
        )
        return super().get_context_data(
            file=file,
            checks=file.checks.active().select_related("checked_by")[:50],
            link_url=reverse(route, args=[record.pk]),
            link_label=getattr(record, "identifier", str(record)),
            produced_by=file.job_outputs.active().select_related("job").first(),
            can_edit=self.can_write(),
            can_check=self.can_write() and file.backend.is_readable,
            entries=history,
            **kwargs,
        )


class FileRegisterView(StorageServiceFormView):
    """Register a file for the sequencing run, PCR run or sample named in the URL."""

    form_class = RegisterFileForm
    title = gettext_lazy("Register a file")
    submit_label = gettext_lazy("Register")
    success_message = gettext_lazy("File registered.")

    @property
    def record(self):
        if not hasattr(self, "_record"):
            model, _name, _route = LINKS[self.kwargs["link"]]
            self._record = get_object_or_404(
                model.objects.visible_to(self.request.user), pk=self.kwargs["pk"]
            )
        return self._record

    def dispatch(self, request, *args, **kwargs):
        if request.user.is_authenticated:
            record = self.record
            if record.is_voided or not can_edit_project(request.user, record.project):
                raise PermissionDenied
        return super().dispatch(request, *args, **kwargs)

    def perform(self, cleaned_data):
        _model, name, _route = LINKS[self.kwargs["link"]]
        values = {key: value for key, value in cleaned_data.items() if value not in (None, "")}
        return services.register_file(
            user=self.request.user,
            project=self.record.project,
            backend=values.pop("backend"),
            path=values.pop("path"),
            **{name: self.record},
            **values,
        )

    def get_success_url(self) -> str:
        return reverse("storage:file-detail", args=[self.result.pk])

    def get_cancel_url(self) -> str:
        return reverse(LINKS[self.kwargs["link"]][2], args=[self.record.pk])


class FileVerifyView(FileMixin, StorageServiceFormView):
    """A button with a confirmation page: the check runs in the background."""

    form_class = ReasonForm
    title = gettext_lazy("Check the file")
    submit_label = gettext_lazy("Check")
    success_message = gettext_lazy("Check started: reload this page in a moment.")

    def get_form(self, form_class=None):
        form = super().get_form(form_class)
        form.fields.pop("reason")
        return form

    def perform(self, cleaned_data):
        if not self.file.backend.is_readable:
            raise FileNotReadableError(_("A file on an offline medium cannot be checked here."))
        verify_file_task.delay(str(self.file.pk), str(self.request.user.pk))


class FileTierView(FileMixin, StorageServiceFormView):
    form_class = TierForm
    title = gettext_lazy("Change the tier")
    success_message = gettext_lazy("Tier changed.")

    def get_initial(self):
        return {"tier": self.file.tier}

    def perform(self, cleaned_data):
        return services.change_tier(
            user=self.request.user,
            file=self.file,
            tier=cleaned_data["tier"],
            reason=cleaned_data["reason"],
        )


class FileVoidView(FileMixin, StorageServiceFormView):
    form_class = VoidForm
    title = gettext_lazy("Void file")
    submit_label = gettext_lazy("Void")
    success_message = gettext_lazy("File voided.")

    def perform(self, cleaned_data):
        return services.void_record(
            user=self.request.user, record=self.file, reason=cleaned_data["reason"]
        )


class BackendListView(LoginRequiredMixin, ListView):
    template_name = "storage/backend_list.html"
    context_object_name = "backends"

    def get_queryset(self):
        return (
            StorageBackend.objects.active()
            .annotate(file_count=Count("files", filter=Q(files__voided_at__isnull=True)))
            .order_by("name")
        )
