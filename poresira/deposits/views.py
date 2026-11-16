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
from .collect import preflight
from .forms import (
    PAGE_SIZE,
    ConfirmForm,
    DepositFilterForm,
    NewDepositForm,
    ProductionForm,
    ReasonForm,
    VoidForm,
)
from .models import Deposit, DepositEvent, DepositItem, DepositStatus, Environment, ItemKind
from .permissions import can_publish
from .rocrate import ro_crate_zip
from .tasks import submit_deposit_task

DEPOSIT_HISTORY_FIELDS = (
    "status",
    "environment",
    "hold_until",
    "submitted_by",
    "notes",
    "voided_at",
    "void_reason",
)


class DepositServiceFormView(ServiceFormView):
    """Like the samples forms, and ``DepositServiceError`` also comes back as a form error."""

    def form_valid(self, form):
        try:
            return super().form_valid(form)
        except services.DepositServiceError as error:
            form.add_error(None, str(error))
            return self.form_invalid(form)


class DepositMixin:
    """Loads the deposit among those the user can see; writing needs edit rights on its project."""

    require_edit = True

    @property
    def deposit(self) -> Deposit:
        if not hasattr(self, "_deposit"):
            self._deposit = get_object_or_404(
                Deposit.objects.visible_to(self.request.user).select_related(
                    "project", "submitted_by"
                ),
                pk=self.kwargs["pk"],
            )
        return self._deposit

    def dispatch(self, request, *args, **kwargs):
        if request.user.is_authenticated and self.require_edit and not self.can_write():
            raise PermissionDenied
        return super().dispatch(request, *args, **kwargs)

    def can_write(self) -> bool:
        return not self.deposit.is_voided and can_edit_project(
            self.request.user, self.deposit.project
        )

    def get_success_url(self) -> str:
        return reverse("deposits:deposit-detail", args=[self.deposit.pk])


class DepositListView(LoginRequiredMixin, ListView):
    template_name = "deposits/deposit_list.html"
    paginate_by = PAGE_SIZE
    context_object_name = "deposits"

    def get_filter_form(self) -> DepositFilterForm:
        if not hasattr(self, "_filter_form"):
            self._filter_form = DepositFilterForm(self.request.GET or None, user=self.request.user)
        return self._filter_form

    def get_queryset(self):
        deposits = (
            Deposit.objects.visible_to(self.request.user)
            .select_related("project")
            .annotate(item_count=Count("items", filter=Q(items__voided_at__isnull=True)))
            .order_by("-created_at")
        )
        form = self.get_filter_form()
        if not form.is_valid():
            return deposits.active()
        filters = form.cleaned_data
        for name in ("project", "environment", "status"):
            if filters[name]:
                deposits = deposits.filter(**{name: filters[name]})
        return deposits if filters["include_voided"] else deposits.active()

    def get_context_data(self, **kwargs):
        query = self.request.GET.copy()
        query.pop("page", None)
        return super().get_context_data(
            filter_form=self.get_filter_form(), query_string=query.urlencode(), **kwargs
        )


class DepositDetailView(DepositMixin, LoginRequiredMixin, TemplateView):
    template_name = "deposits/deposit_detail.html"
    require_edit = False

    def get_context_data(self, **kwargs):
        deposit = self.deposit
        items = list(
            deposit.items.active()
            .select_related("sample", "sequencing_run", "sample_item")
            .annotate(file_count=Count("files", filter=Q(files__voided_at__isnull=True)))
            .order_by("kind", "alias")
        )
        draft = deposit.status == DepositStatus.DRAFT
        editable = self.can_write()
        history = build_history(
            [
                HistorySource(
                    events=DepositEvent.objects.filter(pgh_obj=deposit).order_by(
                        "pgh_created_at", "pgh_id"
                    ),
                    field_names=DEPOSIT_HISTORY_FIELDS,
                    subject_for=lambda event: _("Deposit"),
                )
            ]
        )
        publish = can_publish(self.request.user, deposit.project)
        production = deposit.environment == Environment.PRODUCTION
        return super().get_context_data(
            deposit=deposit,
            study=next((i for i in items if i.kind == ItemKind.STUDY), None),
            samples=[i for i in items if i.kind == ItemKind.SAMPLE],
            reads=[i for i in items if i.kind == ItemKind.READS],
            report=preflight(deposit) if draft and not deposit.is_voided else None,
            can_edit=editable,
            can_submit=editable and draft and (publish or not production),
            can_leave_out=editable and draft,
            can_make_real=(
                publish
                and not deposit.is_voided
                and not production
                and deposit.status == DepositStatus.ACCEPTED
            ),
            entries=history,
            **kwargs,
        )


class DepositCreateView(DepositServiceFormView):
    """Prepare a deposit for the sequencing run named in the URL."""

    form_class = NewDepositForm
    title = gettext_lazy("New deposit")
    submit_label = gettext_lazy("Prepare")
    success_message = gettext_lazy("Deposit prepared for the test service. Review the report.")

    @property
    def run(self) -> SequencingRun:
        if not hasattr(self, "_run"):
            self._run = get_object_or_404(
                SequencingRun.objects.visible_to(self.request.user).select_related("project"),
                pk=self.kwargs["pk"],
            )
        return self._run

    def dispatch(self, request, *args, **kwargs):
        if request.user.is_authenticated and (
            self.run.is_voided or not can_edit_project(request.user, self.run.project)
        ):
            raise PermissionDenied
        return super().dispatch(request, *args, **kwargs)

    def get_form_kwargs(self):
        kwargs = super().get_form_kwargs()
        kwargs.pop("instance", None)
        return kwargs

    def perform(self, cleaned_data):
        return services.prepare_deposit(
            user=self.request.user,
            run=self.run,
            title=cleaned_data.pop("title"),
            description=cleaned_data.pop("description"),
            hold_until=cleaned_data.pop("hold_until"),
            **cleaned_data,
        )

    def get_success_url(self) -> str:
        return reverse("deposits:deposit-detail", args=[self.result.pk])

    def get_cancel_url(self) -> str:
        return reverse("sequencing:run-detail", args=[self.run.pk])


class DepositSubmitView(DepositMixin, DepositServiceFormView):
    """The confirmation before anything leaves: for the real archive the project code is typed."""

    form_class = ConfirmForm
    submit_label = gettext_lazy("Submit")
    success_message = gettext_lazy(
        "Submission requested: the deposit agent picks it up. Reload this page in a moment."
    )

    @property
    def title(self) -> str:
        if self.deposit.environment == Environment.PRODUCTION:
            return _("Submit for real (public and permanent)")
        return _("Submit to the test service")

    def can_write(self) -> bool:
        production = self.deposit.environment == Environment.PRODUCTION
        return (
            super().can_write()
            and self.deposit.status == DepositStatus.DRAFT
            and (can_publish(self.request.user, self.deposit.project) or not production)
        )

    def get_form_kwargs(self):
        production = self.deposit.environment == Environment.PRODUCTION
        code = self.deposit.project.code if production else None
        return {**super().get_form_kwargs(), "project_code": code}

    def perform(self, cleaned_data):
        confirm = cleaned_data.get("confirm", "")
        services.ready_to_send(self.request.user, self.deposit, confirm)  # tell the person now
        submit_deposit_task.delay(str(self.deposit.pk), str(self.request.user.pk), confirm)


class DepositProductionView(DepositMixin, DepositServiceFormView):
    """The real deposit, prepared from a test deposit the archive accepted."""

    form_class = ProductionForm
    title = gettext_lazy("Prepare the real deposit")
    submit_label = gettext_lazy("Prepare")
    success_message = gettext_lazy("Deposit prepared. Check it, then submit it for real.")

    def can_write(self) -> bool:
        return super().can_write() and can_publish(self.request.user, self.deposit.project)

    def perform(self, cleaned_data):
        return services.copy_for_production(
            user=self.request.user,
            source=self.deposit,
            hold_until=cleaned_data["hold_until"],
            permits_confirmed=cleaned_data["permits_confirmed"],
        )

    def get_success_url(self) -> str:
        return reverse("deposits:deposit-detail", args=[self.result.pk])

    def get_cancel_url(self) -> str:
        return reverse("deposits:deposit-detail", args=[self.deposit.pk])


class DepositVoidView(DepositMixin, DepositServiceFormView):
    form_class = VoidForm
    title = gettext_lazy("Void deposit")
    submit_label = gettext_lazy("Void")
    success_message = gettext_lazy("Deposit voided.")

    def can_write(self) -> bool:
        return super().can_write() and self.deposit.status != DepositStatus.SUBMITTING

    def perform(self, cleaned_data):
        return services.void_record(
            user=self.request.user, deposit=self.deposit, reason=cleaned_data["reason"]
        )


class ItemLeaveOutView(DepositServiceFormView):
    form_class = ReasonForm
    title = gettext_lazy("Leave out of the deposit")
    submit_label = gettext_lazy("Leave out")
    success_message = gettext_lazy("Left out of the deposit.")

    @property
    def item(self) -> DepositItem:
        if not hasattr(self, "_item"):
            self._item = get_object_or_404(
                DepositItem.objects.visible_to(self.request.user).select_related(
                    "deposit__project"
                ),
                pk=self.kwargs["pk"],
            )
        return self._item

    def dispatch(self, request, *args, **kwargs):
        if request.user.is_authenticated:
            deposit = self.item.deposit
            if (
                self.item.is_voided
                or deposit.is_voided
                or deposit.status != DepositStatus.DRAFT
                or not can_edit_project(request.user, deposit.project)
            ):
                raise PermissionDenied
        return super().dispatch(request, *args, **kwargs)

    def perform(self, cleaned_data):
        return services.void_item(
            user=self.request.user, item=self.item, reason=cleaned_data["reason"]
        )

    def get_success_url(self) -> str:
        return reverse("deposits:deposit-detail", args=[self.item.deposit_id])


class _DownloadView(DepositMixin, LoginRequiredMixin, View):
    require_edit = False
    filename = ""
    content_type = "application/zip"

    def build(self, deposit: Deposit) -> bytes:
        raise NotImplementedError

    def get(self, request, *args, **kwargs):
        deposit = self.deposit
        response = HttpResponse(self.build(deposit), content_type=self.content_type)
        response["Content-Disposition"] = (
            f'attachment; filename="{deposit.identifier}{self.filename}"'
        )
        return response


class RoCrateView(_DownloadView):
    filename = "-ro-crate.zip"

    def build(self, deposit: Deposit) -> bytes:
        return ro_crate_zip(deposit)


class SubmissionFilesView(_DownloadView):
    filename = "-submission-files.zip"

    def build(self, deposit: Deposit) -> bytes:
        return services.submission_zip(deposit)
