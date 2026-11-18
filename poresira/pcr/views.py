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
from poresira.samples.views import ServiceFormView

from . import services
from .forms import (
    PAGE_SIZE,
    PerformForm,
    ReactionForm,
    ReasonForm,
    ResultForm,
    RunFilterForm,
    RunForm,
    VoidForm,
)
from .models import (
    Assay,
    Container,
    CyclingProgram,
    MasterMix,
    PcrReaction,
    PcrReactionEvent,
    PcrRun,
    PcrRunEvent,
    Primer,
    Result,
    RunStatus,
)
from .permissions import can_manage_reference_data

RUN_HISTORY_FIELDS = (
    "status",
    "performed_on",
    "performed_by",
    "thermocycler",
    "master_mix_lot",
    "polymerase_lot",
    "notes",
    "voided_at",
    "void_reason",
)
REACTION_HISTORY_FIELDS = (
    "result",
    "ct",
    "band_size_bp",
    "template_volume_ul",
    "notes",
    "voided_at",
    "void_reason",
)


class PcrServiceFormView(ServiceFormView):
    """Like the samples forms, and ``PcrServiceError`` also comes back as a form error."""

    def form_valid(self, form):
        try:
            return super().form_valid(form)
        except services.PcrServiceError as error:
            form.add_error(None, str(error))
            return self.form_invalid(form)


class RunMixin:
    """Loads the run among those the user can see; writing needs edit rights on its project."""

    require_edit = True

    @property
    def run(self) -> PcrRun:
        if not hasattr(self, "_run"):
            self._run = get_object_or_404(
                PcrRun.objects.visible_to(self.request.user).select_related(
                    "project", "assay__forward_primer", "assay__reverse_primer", "performed_by"
                ),
                pk=self.kwargs["pk"],
            )
        return self._run

    def dispatch(self, request, *args, **kwargs):
        if request.user.is_authenticated and self.require_edit and not self.can_write():
            raise PermissionDenied
        return super().dispatch(request, *args, **kwargs)

    def can_write(self) -> bool:
        return not self.run.is_voided and can_edit_project(self.request.user, self.run.project)

    def get_success_url(self) -> str:
        return reverse("pcr:run-detail", args=[self.run.pk])


class RunListView(LoginRequiredMixin, ListView):
    template_name = "pcr/run_list.html"
    paginate_by = PAGE_SIZE
    context_object_name = "runs"

    def get_filter_form(self) -> RunFilterForm:
        if not hasattr(self, "_filter_form"):
            self._filter_form = RunFilterForm(self.request.GET or None, user=self.request.user)
        return self._filter_form

    def get_queryset(self):
        runs = (
            PcrRun.objects.visible_to(self.request.user)
            .select_related("project", "assay")
            .annotate(
                reaction_count=Count("reactions", filter=Q(reactions__voided_at__isnull=True))
            )
            .order_by("-created_at")
        )
        form = self.get_filter_form()
        if not form.is_valid():
            return runs.active()
        filters = form.cleaned_data
        for name in ("project", "assay", "status"):
            if filters[name]:
                runs = runs.filter(**{name: filters[name]})
        return runs if filters["include_voided"] else runs.active()

    def get_context_data(self, **kwargs):
        query = self.request.GET.copy()
        query.pop("page", None)
        return super().get_context_data(
            filter_form=self.get_filter_form(),
            query_string=query.urlencode(),
            can_create=Project.objects.editable_by(self.request.user).exists(),
            **kwargs,
        )


def build_grid(run: PcrRun, reactions) -> dict:
    """Rows of cells for the plate (A-H by 1-12), or one row of tubes; each cell may be empty."""
    by_position = {reaction.position: reaction for reaction in reactions}
    if run.container == Container.TUBES:
        cells = [
            {"position": str(n), "reaction": by_position.get(str(n))}
            for n in range(1, run.tube_count + 1)
        ]
        return {
            "columns": [],
            "rows": [{"label": "", "cells": cells[i : i + 12]} for i in range(0, len(cells), 12)],
        }
    letters = [chr(ord("A") + row) for row in range(8)]
    return {
        "columns": list(range(1, 13)),
        "rows": [
            {
                "label": letter,
                "cells": [
                    {
                        "position": f"{letter}{column}",
                        "reaction": by_position.get(f"{letter}{column}"),
                    }
                    for column in range(1, 13)
                ],
            }
            for letter in letters
        ],
    }


class RunDetailView(RunMixin, LoginRequiredMixin, TemplateView):
    template_name = "pcr/run_detail.html"
    require_edit = False

    def get_context_data(self, **kwargs):
        run = self.run
        reactions = list(run.reactions.active().select_related("sample").order_by("created_at"))
        validity = services.assess_run(run)
        history = build_history(
            [
                HistorySource(
                    events=PcrRunEvent.objects.filter(pgh_obj=run).order_by(
                        "pgh_created_at", "pgh_id"
                    ),
                    field_names=RUN_HISTORY_FIELDS,
                    subject_for=lambda event: _("Run"),
                ),
                HistorySource(
                    events=PcrReactionEvent.objects.filter(run=run).order_by(
                        "pgh_created_at", "pgh_id"
                    ),
                    field_names=REACTION_HISTORY_FIELDS,
                    subject_for=lambda event: (
                        _("Reaction %(position)s") % {"position": event.position}
                    ),
                ),
            ]
        )
        editable = self.can_write()
        return super().get_context_data(
            run=run,
            reactions=reactions,
            grid=build_grid(run, reactions),
            validity=validity,
            can_edit=editable,
            can_add=editable and run.status == RunStatus.PLANNED,
            can_enter_results=editable and run.status in (RunStatus.PERFORMED, RunStatus.CLOSED),
            positives=sum(r.result == Result.POSITIVE for r in reactions),
            files=run.files.active().select_related("backend"),
            register_url=(
                reverse("storage:file-register", args=["pcr-run", run.pk]) if editable else ""
            ),
            entries=history,
            **kwargs,
        )


class RunCreateView(PcrServiceFormView):
    form_class = RunForm
    title = gettext_lazy("New PCR run")
    submit_label = gettext_lazy("Create")
    success_message = gettext_lazy("PCR run planned.")

    def get_form_kwargs(self):
        editable = Project.objects.editable_by(self.request.user)
        initial = {"project": self.request.GET.get("project")}
        return {**super().get_form_kwargs(), "editable_projects": editable, "initial": initial}

    def perform(self, cleaned_data):
        project, assay = cleaned_data.pop("project"), cleaned_data.pop("assay")
        container = cleaned_data.pop("container")
        tube_count = cleaned_data.pop("tube_count")
        return services.plan_run(
            user=self.request.user,
            project=project,
            assay=assay,
            container=container,
            tube_count=tube_count,
            **cleaned_data,
        )

    def get_success_url(self) -> str:
        return reverse("pcr:run-detail", args=[self.result.pk])

    def get_cancel_url(self) -> str:
        return reverse("pcr:run-list")


class ReactionAddView(RunMixin, PcrServiceFormView):
    form_class = ReactionForm
    title = gettext_lazy("Add a reaction")
    submit_label = gettext_lazy("Add")
    success_message = gettext_lazy("Reaction added.")

    def get_form_kwargs(self):
        return {**super().get_form_kwargs(), "run": self.run}

    def get_initial(self):
        return {"position": self.request.GET.get("position", "")}

    def perform(self, cleaned_data):
        values = {k: v for k, v in cleaned_data.items() if v not in (None, "")}
        return services.add_reaction(
            user=self.request.user,
            run=self.run,
            role=values.pop("role"),
            sample=values.pop("sample", None),
            position=values.pop("position", ""),
            **values,
        )


class RunPerformView(RunMixin, PcrServiceFormView):
    form_class = PerformForm
    title = gettext_lazy("Mark the run as performed")
    submit_label = gettext_lazy("Mark as performed")
    success_message = gettext_lazy("Run marked as performed.")

    def get_form_kwargs(self):
        return {**super().get_form_kwargs(), "instance": PcrRun.objects.get(pk=self.run.pk)}

    def perform(self, cleaned_data):
        values = {k: v for k, v in cleaned_data.items() if v not in (None, "")}
        return services.mark_performed(user=self.request.user, run=self.run, **values)


class ConfirmView(RunMixin, PcrServiceFormView):
    """A button with a short confirmation page and no field."""

    form_class = ReasonForm

    def get_form(self, form_class=None):
        form = super().get_form(form_class)
        if not self.needs_reason:
            form.fields.pop("reason")
        return form

    needs_reason = False


class RunCloseView(ConfirmView):
    title = gettext_lazy("Close the run")
    submit_label = gettext_lazy("Close")
    success_message = gettext_lazy("Run closed.")

    def perform(self, cleaned_data):
        return services.close_run(user=self.request.user, run=self.run)


class RunReopenView(ConfirmView):
    title = gettext_lazy("Reopen the run")
    submit_label = gettext_lazy("Reopen")
    success_message = gettext_lazy("Run reopened.")
    needs_reason = True

    def perform(self, cleaned_data):
        return services.reopen_run(
            user=self.request.user, run=self.run, reason=cleaned_data["reason"]
        )


class RunVoidView(RunMixin, PcrServiceFormView):
    form_class = VoidForm
    title = gettext_lazy("Void run")
    submit_label = gettext_lazy("Void")
    success_message = gettext_lazy("Run voided.")

    def perform(self, cleaned_data):
        return services.void_record(
            user=self.request.user, record=self.run, reason=cleaned_data["reason"]
        )


class ReactionMixin:
    @property
    def reaction(self) -> PcrReaction:
        if not hasattr(self, "_reaction"):
            self._reaction = get_object_or_404(
                PcrReaction.objects.visible_to(self.request.user).select_related(
                    "run__project", "sample"
                ),
                pk=self.kwargs["pk"],
            )
        return self._reaction

    def dispatch(self, request, *args, **kwargs):
        if request.user.is_authenticated and (
            self.reaction.is_voided
            or self.reaction.run.is_voided
            or not can_edit_project(request.user, self.reaction.run.project)
        ):
            raise PermissionDenied
        return super().dispatch(request, *args, **kwargs)

    def get_success_url(self) -> str:
        return reverse("pcr:run-detail", args=[self.reaction.run_id])


class ResultView(ReactionMixin, PcrServiceFormView):
    form_class = ResultForm
    title = gettext_lazy("Enter a result")
    success_message = gettext_lazy("Result saved.")

    def get_form_kwargs(self):
        initial = {} if self.reaction.result == Result.PENDING else {"result": self.reaction.result}
        return {**super().get_form_kwargs(), "instance": self.reaction, "initial": initial}

    def perform(self, cleaned_data):
        reason = cleaned_data.pop("reason")
        result = cleaned_data.pop("result")
        values = {k: v for k, v in cleaned_data.items() if v not in (None, "")}
        return services.record_result(
            user=self.request.user,
            reaction=PcrReaction.objects.get(pk=self.reaction.pk),
            result=result,
            reason=reason,
            **values,
        )


class ReactionVoidView(ReactionMixin, PcrServiceFormView):
    form_class = VoidForm
    title = gettext_lazy("Void reaction")
    submit_label = gettext_lazy("Void")
    success_message = gettext_lazy("Reaction voided.")

    def perform(self, cleaned_data):
        return services.void_record(
            user=self.request.user, record=self.reaction, reason=cleaned_data["reason"]
        )


class ManageContextMixin:
    def get_context_data(self, **kwargs):
        return super().get_context_data(
            can_manage=can_manage_reference_data(self.request.user), **kwargs
        )


class AssayListView(ManageContextMixin, LoginRequiredMixin, ListView):
    template_name = "pcr/assay_list.html"
    context_object_name = "assays"

    def get_queryset(self):
        return (
            Assay.objects.active()
            .select_related("forward_primer", "reverse_primer", "cycling_program", "master_mix")
            .order_by("name")
        )


class PrimerListView(ManageContextMixin, LoginRequiredMixin, ListView):
    template_name = "pcr/primer_list.html"
    context_object_name = "primers"

    def get_queryset(self):
        return Primer.objects.active().order_by("name")


class ProgramListView(ManageContextMixin, LoginRequiredMixin, ListView):
    template_name = "pcr/program_list.html"
    context_object_name = "programs"

    def get_queryset(self):
        return CyclingProgram.objects.active().prefetch_related("steps").order_by("name")


class MasterMixListView(ManageContextMixin, LoginRequiredMixin, ListView):
    template_name = "pcr/master_mix_list.html"
    context_object_name = "mixes"

    def get_queryset(self):
        return MasterMix.objects.active().prefetch_related("components").order_by("name")


class ReferenceCreateView(PcrServiceFormView):
    """Add a primer, a cycling program or an assay; ``service`` records it."""

    submit_label = gettext_lazy("Add")
    success_message = gettext_lazy("Added.")
    service = None
    list_url = ""

    def dispatch(self, request, *args, **kwargs):
        if request.user.is_authenticated and not can_manage_reference_data(request.user):
            raise PermissionDenied
        return super().dispatch(request, *args, **kwargs)

    def perform(self, cleaned_data):
        data = {k: v for k, v in cleaned_data.items() if v not in (None, "")}
        return self.service(user=self.request.user, **data)

    def get_success_url(self) -> str:
        return reverse(self.list_url)
