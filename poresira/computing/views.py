# SPDX-License-Identifier: AGPL-3.0-or-later
from django.contrib.auth.mixins import LoginRequiredMixin
from django.core.exceptions import PermissionDenied
from django.db.models import Count, Q
from django.http import HttpResponse
from django.shortcuts import get_object_or_404
from django.urls import reverse
from django.utils.translation import gettext as _
from django.utils.translation import gettext_lazy
from django.views import View
from django.views.generic import ListView, TemplateView

from poresira.core.history import HistorySource, build_history
from poresira.core.permissions import can_edit_project
from poresira.samples.views import ServiceFormView
from poresira.sequencing.models import SequencingRun

from . import services
from .forms import PAGE_SIZE, JobFilterForm, NewJobForm, ReasonForm, VoidForm
from .jobtypes import JOB_TYPES
from .models import ComputeJob, ComputeJobEvent, JobKind, JobStatus
from .tasks import cancel_job_task, submit_job_task

JOB_HISTORY_FIELDS = (
    "status",
    "scheduler_job_id",
    "exit_code",
    "notes",
    "voided_at",
    "void_reason",
)


class ComputeServiceFormView(ServiceFormView):
    """Like the samples forms, and ``ComputeServiceError`` also comes back as a form error."""

    def form_valid(self, form):
        try:
            return super().form_valid(form)
        except services.ComputeServiceError as error:
            form.add_error(None, str(error))
            return self.form_invalid(form)


class JobMixin:
    """Loads the job among those the user can see; writing needs edit rights on its project."""

    require_edit = True

    @property
    def job(self) -> ComputeJob:
        if not hasattr(self, "_job"):
            self._job = get_object_or_404(
                ComputeJob.objects.visible_to(self.request.user).select_related(
                    "project", "sequencing_run", "submitted_by"
                ),
                pk=self.kwargs["pk"],
            )
        return self._job

    def dispatch(self, request, *args, **kwargs):
        if request.user.is_authenticated and self.require_edit and not self.can_write():
            raise PermissionDenied
        return super().dispatch(request, *args, **kwargs)

    def can_write(self) -> bool:
        return not self.job.is_voided and can_edit_project(self.request.user, self.job.project)

    def get_success_url(self) -> str:
        return reverse("computing:job-detail", args=[self.job.pk])


class JobListView(LoginRequiredMixin, ListView):
    template_name = "computing/job_list.html"
    paginate_by = PAGE_SIZE
    context_object_name = "jobs"

    def get_filter_form(self) -> JobFilterForm:
        if not hasattr(self, "_filter_form"):
            self._filter_form = JobFilterForm(self.request.GET or None, user=self.request.user)
        return self._filter_form

    def get_queryset(self):
        jobs = (
            ComputeJob.objects.visible_to(self.request.user)
            .select_related("project", "sequencing_run")
            .annotate(output_count=Count("outputs", filter=Q(outputs__voided_at__isnull=True)))
            .order_by("-created_at")
        )
        form = self.get_filter_form()
        if not form.is_valid():
            return jobs.active()
        filters = form.cleaned_data
        for name in ("project", "kind", "status"):
            if filters[name]:
                jobs = jobs.filter(**{name: filters[name]})
        return jobs if filters["include_voided"] else jobs.active()

    def get_context_data(self, **kwargs):
        query = self.request.GET.copy()
        query.pop("page", None)
        return super().get_context_data(
            filter_form=self.get_filter_form(), query_string=query.urlencode(), **kwargs
        )


class JobDetailView(JobMixin, LoginRequiredMixin, TemplateView):
    template_name = "computing/job_detail.html"
    require_edit = False

    def get_context_data(self, **kwargs):
        job = self.job
        history = build_history(
            [
                HistorySource(
                    events=ComputeJobEvent.objects.filter(pgh_obj=job).order_by(
                        "pgh_created_at", "pgh_id"
                    ),
                    field_names=JOB_HISTORY_FIELDS,
                    subject_for=lambda event: _("Job"),
                )
            ]
        )
        editable = self.can_write()
        return super().get_context_data(
            job=job,
            inputs=job.inputs.active().select_related("file__backend").order_by("file__path"),
            outputs=job.outputs.active().select_related("file__backend").order_by("file__path"),
            can_edit=editable,
            can_submit=editable and job.status == JobStatus.DRAFT,
            can_cancel=editable and not job.is_final,
            can_void=editable and job.status not in (JobStatus.SUBMITTED, JobStatus.RUNNING),
            entries=history,
            **kwargs,
        )


class JobCreateView(ComputeServiceFormView):
    """Prepare a job of one kind for the sequencing run named in the URL."""

    submit_label = gettext_lazy("Prepare")
    success_message = gettext_lazy("Job prepared. Review it, then submit it.")

    @property
    def run(self) -> SequencingRun:
        if not hasattr(self, "_run"):
            self._run = get_object_or_404(
                SequencingRun.objects.visible_to(self.request.user).select_related(
                    "project", "library__kit"
                ),
                pk=self.kwargs["pk"],
            )
        return self._run

    @property
    def kind(self) -> str:
        if self.kwargs["kind"] not in JobKind.values:
            raise PermissionDenied
        return self.kwargs["kind"]

    def dispatch(self, request, *args, **kwargs):
        if request.user.is_authenticated:
            self.kind  # noqa: B018 - refuses an unknown kind
            if self.run.is_voided or not can_edit_project(request.user, self.run.project):
                raise PermissionDenied
        return super().dispatch(request, *args, **kwargs)

    def get_form_class(self):
        return NewJobForm

    def get_form_kwargs(self):
        return {
            **super().get_form_kwargs(),
            "job_type": JOB_TYPES[self.kind],
            "candidates": services.default_inputs(self.run, self.kind),
            "initial_parameters": services.default_parameters(self.run, self.kind),
        }

    @property
    def title(self) -> str:
        return f"{JobKind(self.kind).label} · {self.run.identifier}"

    def perform(self, cleaned_data):
        inputs = list(cleaned_data.pop("inputs"))
        notes = cleaned_data.pop("notes")
        return services.prepare_job(
            user=self.request.user,
            run=self.run,
            kind=self.kind,
            parameters=cleaned_data,
            inputs=inputs,
            notes=notes,
        )

    def get_success_url(self) -> str:
        return reverse("computing:job-detail", args=[self.result.pk])

    def get_cancel_url(self) -> str:
        return reverse("sequencing:run-detail", args=[self.run.pk])


class ConfirmView(JobMixin, ComputeServiceFormView):
    """A button with a short confirmation page and no field."""

    form_class = ReasonForm

    def get_form(self, form_class=None):
        form = super().get_form(form_class)
        form.fields.pop("reason")
        return form


class JobSubmitView(ConfirmView):
    title = gettext_lazy("Submit the job")
    submit_label = gettext_lazy("Submit")
    success_message = gettext_lazy(
        "Submission requested: the Slurm agent picks it up. Reload this page in a moment."
    )

    def can_write(self) -> bool:
        return super().can_write() and self.job.status == JobStatus.DRAFT

    def perform(self, cleaned_data):
        submit_job_task.delay(str(self.job.pk), str(self.request.user.pk))


class JobCancelView(ConfirmView):
    title = gettext_lazy("Cancel the job")
    submit_label = gettext_lazy("Cancel the job")
    success_message = gettext_lazy("Cancellation requested.")

    def can_write(self) -> bool:
        return super().can_write() and not self.job.is_final

    def perform(self, cleaned_data):
        if self.job.status == JobStatus.DRAFT:
            services.cancel_job(user=self.request.user, job=self.job)
        else:
            cancel_job_task.delay(str(self.job.pk), str(self.request.user.pk))


class JobVoidView(JobMixin, ComputeServiceFormView):
    form_class = VoidForm
    title = gettext_lazy("Void job")
    submit_label = gettext_lazy("Void")
    success_message = gettext_lazy("Job voided.")

    def perform(self, cleaned_data):
        return services.void_record(
            user=self.request.user, job=self.job, reason=cleaned_data["reason"]
        )


class JobScriptView(JobMixin, LoginRequiredMixin, View):
    """The script as submitted, or for a draft as it would be written now."""

    require_edit = False

    def get(self, request, *args, **kwargs):
        job = self.job
        script = job.script or services.render_job_script(job)
        response = HttpResponse(script, content_type="text/x-shellscript; charset=utf-8")
        response["Content-Disposition"] = f'attachment; filename="{job.identifier}.sh"'
        return response
