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
from poresira.core.models import Project
from poresira.core.permissions import can_edit_project
from poresira.deposits.models import Deposit
from poresira.samples.views import ServiceFormView

from . import services
from .forms import (
    PAGE_SIZE,
    CompleteRunForm,
    FlowCellForm,
    LibraryFilterForm,
    LibraryForm,
    MemberForm,
    PlanRunForm,
    ReasonForm,
    ResultForm,
    RunFilterForm,
    StartRunForm,
    VoidForm,
)
from .models import (
    FlowCell,
    FlowCellType,
    Instrument,
    Library,
    LibraryEvent,
    LibraryKit,
    LibraryMember,
    LibraryStatus,
    RunStatus,
    SequencingRun,
    SequencingRunEvent,
)
from .permissions import can_register_flow_cells

LIBRARY_HISTORY_FIELDS = (
    "name",
    "status",
    "prepared_on",
    "prepared_by",
    "notes",
    "voided_at",
    "void_reason",
)
RUN_HISTORY_FIELDS = (
    "status",
    "instrument",
    "pores_at_start",
    "run_id",
    "started_on",
    "performed_by",
    "acquisition_software",
    "basecaller",
    "basecaller_version",
    "basecall_model",
    "total_reads",
    "total_bases",
    "notes",
    "voided_at",
    "void_reason",
)


class SequencingServiceFormView(ServiceFormView):
    """Like the samples forms, and ``SequencingServiceError`` also comes back as a form error."""

    def form_valid(self, form):
        try:
            return super().form_valid(form)
        except services.SequencingServiceError as error:
            form.add_error(None, str(error))
            return self.form_invalid(form)


def _given(cleaned_data: dict) -> dict:
    return {key: value for key, value in cleaned_data.items() if value not in (None, "")}


class _ProjectRecordMixin:
    """Loads a record among those the user can see; writing needs edit rights on its project."""

    require_edit = True
    queryset_name = ""  # "libraries" etc., set by subclasses through ``get_queryset``

    def get_queryset(self):
        raise NotImplementedError

    def project_of(self, record) -> Project:
        return record.project

    def record_of(self, record):
        return record

    @property
    def record(self):
        if not hasattr(self, "_record"):
            self._record = get_object_or_404(
                self.get_queryset().visible_to(self.request.user), pk=self.kwargs["pk"]
            )
        return self._record

    def dispatch(self, request, *args, **kwargs):
        if request.user.is_authenticated and self.require_edit and not self.can_write():
            raise PermissionDenied
        return super().dispatch(request, *args, **kwargs)

    def can_write(self) -> bool:
        return not self.record.is_voided and can_edit_project(
            self.request.user, self.project_of(self.record)
        )


class LibraryMixin(_ProjectRecordMixin):
    def get_queryset(self):
        return Library.objects.select_related("project", "kit", "prepared_by")

    @property
    def library(self) -> Library:
        return self.record

    def get_success_url(self) -> str:
        return reverse("sequencing:library-detail", args=[self.library.pk])


class RunMixin(_ProjectRecordMixin):
    def get_queryset(self):
        return SequencingRun.objects.select_related(
            "project", "library__kit", "flow_cell__type", "instrument", "performed_by"
        )

    @property
    def run(self) -> SequencingRun:
        return self.record

    def get_success_url(self) -> str:
        return reverse("sequencing:run-detail", args=[self.run.pk])


class ConfirmView(SequencingServiceFormView):
    """A button with a short confirmation page, and a reason when ``needs_reason``."""

    form_class = ReasonForm
    needs_reason = False

    def get_form(self, form_class=None):
        form = super().get_form(form_class)
        if not self.needs_reason:
            form.fields.pop("reason")
        return form


# --- Libraries --------------------------------------------------------------------------------


class LibraryListView(LoginRequiredMixin, ListView):
    template_name = "sequencing/library_list.html"
    paginate_by = PAGE_SIZE
    context_object_name = "libraries"

    def get_filter_form(self) -> LibraryFilterForm:
        if not hasattr(self, "_filter_form"):
            self._filter_form = LibraryFilterForm(self.request.GET or None, user=self.request.user)
        return self._filter_form

    def get_queryset(self):
        libraries = (
            Library.objects.visible_to(self.request.user)
            .select_related("project", "kit")
            .annotate(member_count=Count("members", filter=Q(members__voided_at__isnull=True)))
            .order_by("-created_at")
        )
        form = self.get_filter_form()
        if not form.is_valid():
            return libraries.active()
        filters = form.cleaned_data
        for name in ("project", "status"):
            if filters[name]:
                libraries = libraries.filter(**{name: filters[name]})
        return libraries if filters["include_voided"] else libraries.active()

    def get_context_data(self, **kwargs):
        query = self.request.GET.copy()
        query.pop("page", None)
        return super().get_context_data(
            filter_form=self.get_filter_form(),
            query_string=query.urlencode(),
            can_create=Project.objects.editable_by(self.request.user).exists(),
            **kwargs,
        )


class LibraryDetailView(LibraryMixin, LoginRequiredMixin, TemplateView):
    template_name = "sequencing/library_detail.html"
    require_edit = False

    def get_context_data(self, **kwargs):
        library = self.library
        members = list(
            library.members.active().select_related("sample").order_by("barcode", "created_at")
        )
        history = build_history(
            [
                HistorySource(
                    events=LibraryEvent.objects.filter(pgh_obj=library).order_by(
                        "pgh_created_at", "pgh_id"
                    ),
                    field_names=LIBRARY_HISTORY_FIELDS,
                    subject_for=lambda event: _("Library"),
                )
            ]
        )
        editable = self.can_write()
        draft = library.status == LibraryStatus.DRAFT
        return super().get_context_data(
            library=library,
            members=members,
            runs=library.runs.active().select_related("flow_cell"),
            can_edit=editable,
            can_add=editable and draft,
            can_plan=editable and library.status == LibraryStatus.SEALED,
            entries=history,
            **kwargs,
        )


class LibraryCreateView(SequencingServiceFormView):
    form_class = LibraryForm
    title = gettext_lazy("New library")
    submit_label = gettext_lazy("Create")
    success_message = gettext_lazy("Library created.")

    def get_form_kwargs(self):
        editable = Project.objects.editable_by(self.request.user)
        initial = {"project": self.request.GET.get("project")}
        return {**super().get_form_kwargs(), "editable_projects": editable, "initial": initial}

    def perform(self, cleaned_data):
        project, kit = cleaned_data.pop("project"), cleaned_data.pop("kit")
        return services.create_library(
            user=self.request.user, project=project, kit=kit, **_given(cleaned_data)
        )

    def get_success_url(self) -> str:
        return reverse("sequencing:library-detail", args=[self.result.pk])

    def get_cancel_url(self) -> str:
        return reverse("sequencing:library-list")


class MemberAddView(LibraryMixin, SequencingServiceFormView):
    form_class = MemberForm
    title = gettext_lazy("Add a sample")
    submit_label = gettext_lazy("Add")
    success_message = gettext_lazy("Sample added.")

    def get_form_kwargs(self):
        return {**super().get_form_kwargs(), "library": self.library}

    def perform(self, cleaned_data):
        return services.add_member(
            user=self.request.user,
            library=self.library,
            sample=cleaned_data.pop("sample"),
            barcode=cleaned_data.pop("barcode"),
            **_given(cleaned_data),
        )


class LibrarySealView(LibraryMixin, ConfirmView):
    title = gettext_lazy("Seal the library")
    submit_label = gettext_lazy("Seal")
    success_message = gettext_lazy("Library sealed.")

    def perform(self, cleaned_data):
        return services.seal_library(user=self.request.user, library=self.library)


class LibraryVoidView(LibraryMixin, SequencingServiceFormView):
    form_class = VoidForm
    title = gettext_lazy("Void library")
    submit_label = gettext_lazy("Void")
    success_message = gettext_lazy("Library voided.")

    def perform(self, cleaned_data):
        return services.void_record(
            user=self.request.user, record=self.library, reason=cleaned_data["reason"]
        )


class MemberVoidView(SequencingServiceFormView):
    form_class = VoidForm
    title = gettext_lazy("Remove sample")
    submit_label = gettext_lazy("Remove")
    success_message = gettext_lazy("Sample removed from the library.")

    @property
    def member(self) -> LibraryMember:
        if not hasattr(self, "_member"):
            self._member = get_object_or_404(
                LibraryMember.objects.visible_to(self.request.user).select_related(
                    "library__project", "sample"
                ),
                pk=self.kwargs["pk"],
            )
        return self._member

    def dispatch(self, request, *args, **kwargs):
        member = self.member if request.user.is_authenticated else None
        if member and (
            member.is_voided
            or member.library.is_voided
            or not can_edit_project(request.user, member.library.project)
        ):
            raise PermissionDenied
        return super().dispatch(request, *args, **kwargs)

    def perform(self, cleaned_data):
        return services.void_record(
            user=self.request.user, record=self.member, reason=cleaned_data["reason"]
        )

    def get_success_url(self) -> str:
        return reverse("sequencing:library-detail", args=[self.member.library_id])


# --- Runs -------------------------------------------------------------------------------------


class RunListView(LoginRequiredMixin, ListView):
    template_name = "sequencing/run_list.html"
    paginate_by = PAGE_SIZE
    context_object_name = "runs"

    def get_filter_form(self) -> RunFilterForm:
        if not hasattr(self, "_filter_form"):
            self._filter_form = RunFilterForm(self.request.GET or None, user=self.request.user)
        return self._filter_form

    def get_queryset(self):
        runs = (
            SequencingRun.objects.visible_to(self.request.user)
            .select_related("project", "library", "flow_cell__type")
            .order_by("-created_at")
        )
        form = self.get_filter_form()
        if not form.is_valid():
            return runs.active()
        filters = form.cleaned_data
        for name in ("project", "status"):
            if filters[name]:
                runs = runs.filter(**{name: filters[name]})
        return runs if filters["include_voided"] else runs.active()

    def get_context_data(self, **kwargs):
        query = self.request.GET.copy()
        query.pop("page", None)
        return super().get_context_data(
            filter_form=self.get_filter_form(), query_string=query.urlencode(), **kwargs
        )


class RunDetailView(RunMixin, LoginRequiredMixin, TemplateView):
    template_name = "sequencing/run_detail.html"
    require_edit = False

    def get_context_data(self, **kwargs):
        run = self.run
        results = {r.member_id: r for r in run.results.active()}
        rows = [
            {"member": member, "result": results.get(member.pk)}
            for member in run.library.members.active()
            .select_related("sample")
            .order_by("barcode", "created_at")
        ]
        history = build_history(
            [
                HistorySource(
                    events=SequencingRunEvent.objects.filter(pgh_obj=run).order_by(
                        "pgh_created_at", "pgh_id"
                    ),
                    field_names=RUN_HISTORY_FIELDS,
                    subject_for=lambda event: _("Run"),
                )
            ]
        )
        editable = self.can_write()
        return super().get_context_data(
            run=run,
            rows=rows,
            reads_in_results=sum(r["result"].reads for r in rows if r["result"]),
            other_runs=run.flow_cell.runs.active().exclude(pk=run.pk).order_by("started_on"),
            run_deposits=Deposit.objects.visible_to(self.request.user)
            .active()
            .filter(items__sequencing_run=run, items__voided_at__isnull=True)
            .distinct()
            .order_by("-created_at"),
            can_deposit=editable and not run.is_voided,
            compute_jobs=run.compute_jobs.active().order_by("-created_at"),
            can_compute=editable and not run.is_voided,
            files=run.files.active().select_related("backend"),
            register_url=(
                reverse("storage:file-register", args=["sequencing-run", run.pk])
                if editable
                else ""
            ),
            can_edit=editable,
            can_enter_results=editable and run.status in (RunStatus.RUNNING, RunStatus.COMPLETED),
            entries=history,
            **kwargs,
        )


class RunPlanView(LibraryMixin, SequencingServiceFormView):
    form_class = PlanRunForm
    title = gettext_lazy("Plan a run")
    submit_label = gettext_lazy("Plan")
    success_message = gettext_lazy("Run planned.")

    def perform(self, cleaned_data):
        flow_cell = cleaned_data.pop("flow_cell")
        return services.plan_run(
            user=self.request.user,
            library=self.library,
            flow_cell=flow_cell,
            **_given(cleaned_data),
        )

    def get_success_url(self) -> str:
        return reverse("sequencing:run-detail", args=[self.result.pk])

    def get_cancel_url(self) -> str:
        return reverse("sequencing:library-detail", args=[self.library.pk])


class RunStartView(RunMixin, SequencingServiceFormView):
    form_class = StartRunForm
    title = gettext_lazy("Start the run")
    submit_label = gettext_lazy("Start")
    success_message = gettext_lazy("Run started.")

    def get_form_kwargs(self):
        return {
            **super().get_form_kwargs(),
            "instance": SequencingRun.objects.get(pk=self.run.pk),
        }

    def perform(self, cleaned_data):
        return services.start_run(user=self.request.user, run=self.run, **_given(cleaned_data))


class RunCompleteView(RunMixin, SequencingServiceFormView):
    form_class = CompleteRunForm
    title = gettext_lazy("Complete the run")
    submit_label = gettext_lazy("Complete")
    success_message = gettext_lazy("Run completed.")

    def get_form_kwargs(self):
        return {
            **super().get_form_kwargs(),
            "instance": SequencingRun.objects.get(pk=self.run.pk),
        }

    def perform(self, cleaned_data):
        return services.complete_run(user=self.request.user, run=self.run, **_given(cleaned_data))


class RunAbortView(RunMixin, ConfirmView):
    title = gettext_lazy("Abort the run")
    submit_label = gettext_lazy("Abort")
    success_message = gettext_lazy("Run aborted.")
    needs_reason = True

    def perform(self, cleaned_data):
        return services.abort_run(
            user=self.request.user, run=self.run, reason=cleaned_data["reason"]
        )


class RunReopenView(RunMixin, ConfirmView):
    title = gettext_lazy("Reopen the run")
    submit_label = gettext_lazy("Reopen")
    success_message = gettext_lazy("Run reopened.")
    needs_reason = True

    def perform(self, cleaned_data):
        return services.reopen_run(
            user=self.request.user, run=self.run, reason=cleaned_data["reason"]
        )


class RunVoidView(RunMixin, SequencingServiceFormView):
    form_class = VoidForm
    title = gettext_lazy("Void run")
    submit_label = gettext_lazy("Void")
    success_message = gettext_lazy("Run voided.")

    def perform(self, cleaned_data):
        return services.void_record(
            user=self.request.user, record=self.run, reason=cleaned_data["reason"]
        )


class ResultView(RunMixin, SequencingServiceFormView):
    """Reads of one library member in this run (the member's pk is in the URL)."""

    form_class = ResultForm
    title = gettext_lazy("Enter the reads")
    success_message = gettext_lazy("Reads saved.")

    @property
    def member(self) -> LibraryMember:
        if not hasattr(self, "_member"):
            self._member = get_object_or_404(
                self.run.library.members.active(), pk=self.kwargs["member_pk"]
            )
        return self._member

    def can_write(self) -> bool:
        return super().can_write() and self.run.status in (
            RunStatus.RUNNING,
            RunStatus.COMPLETED,
        )

    def get_initial(self):
        existing = self.run.results.active().filter(member=self.member).first()
        if existing is None:
            return {}
        return {
            "reads": existing.reads,
            "bases": existing.bases,
            "n50": existing.n50,
            "mean_quality": existing.mean_quality,
            "notes": existing.notes,
        }

    def perform(self, cleaned_data):
        reason = cleaned_data.pop("reason")
        reads = cleaned_data.pop("reads")
        return services.record_result(
            user=self.request.user,
            run=self.run,
            member=self.member,
            reads=reads,
            reason=reason,
            **_given(cleaned_data),
        )


# --- Flow cells and reference data ------------------------------------------------------------


class FlowCellListView(LoginRequiredMixin, ListView):
    template_name = "sequencing/flow_cell_list.html"
    context_object_name = "flow_cells"

    def get_queryset(self):
        flow_cells = list(
            FlowCell.objects.active()
            .select_related("type")
            .annotate(run_count=Count("runs", filter=Q(runs__voided_at__isnull=True)))
        )
        last_run = {}
        runs = SequencingRun.objects.visible_to(self.request.user).active()
        for run in runs.order_by("started_on", "created_at"):
            last_run[run.flow_cell_id] = run
        for cell in flow_cells:
            cell.last_run = last_run.get(cell.pk)
        return flow_cells

    def get_context_data(self, **kwargs):
        return super().get_context_data(
            can_register=can_register_flow_cells(self.request.user), **kwargs
        )


class FlowCellRegisterView(SequencingServiceFormView):
    form_class = FlowCellForm
    title = gettext_lazy("Register a flow cell")
    submit_label = gettext_lazy("Register")
    success_message = gettext_lazy("Flow cell registered.")

    def dispatch(self, request, *args, **kwargs):
        if request.user.is_authenticated and not can_register_flow_cells(request.user):
            raise PermissionDenied
        return super().dispatch(request, *args, **kwargs)

    def perform(self, cleaned_data):
        return services.register_flow_cell(
            user=self.request.user,
            flow_cell_id=cleaned_data["flow_cell_id"],
            type=cleaned_data["type"],
            pores_at_qc=cleaned_data["pores_at_qc"],
            notes=cleaned_data["notes"],
        )

    def get_success_url(self) -> str:
        return reverse("sequencing:flow-cell-list")


class ReferenceView(LoginRequiredMixin, TemplateView):
    template_name = "sequencing/reference.html"

    def get_context_data(self, **kwargs):
        return super().get_context_data(
            kits=LibraryKit.objects.active().order_by("code"),
            flow_cell_types=FlowCellType.objects.active().order_by("code"),
            instruments=Instrument.objects.active().order_by("name"),
            **kwargs,
        )
