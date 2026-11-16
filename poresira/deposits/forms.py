# SPDX-License-Identifier: AGPL-3.0-or-later
from django import forms
from django.utils.translation import gettext_lazy as _

from poresira.core.forms import BootstrapFormMixin
from poresira.core.models import Project
from poresira.samples.forms import PAGE_SIZE, ReasonFormMixin, VoidForm

from .models import Deposit, DepositStatus, Environment

__all__ = [
    "PAGE_SIZE",
    "ConfirmForm",
    "DepositFilterForm",
    "NewDepositForm",
    "ProductionForm",
    "ReasonForm",
    "VoidForm",
]


class ReasonForm(BootstrapFormMixin, ReasonFormMixin):
    pass


class DepositFilterForm(BootstrapFormMixin, forms.Form):
    project = forms.ModelChoiceField(
        queryset=Project.objects.none(), required=False, label=_("Project"), empty_label=_("All")
    )
    environment = forms.ChoiceField(required=False, label=_("Environment"))
    status = forms.ChoiceField(required=False, label=_("Status"))
    include_voided = forms.BooleanField(required=False, label=_("Show voided"))

    def __init__(self, *args, user, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["project"].queryset = Project.objects.visible_to(user).active()
        self.fields["environment"].choices = [("", _("All")), *Environment.choices]
        self.fields["status"].choices = [("", _("All")), *DepositStatus.choices]


class NewDepositForm(BootstrapFormMixin, forms.ModelForm):
    """A deposit for the test service of ENA, or the files for the NCBI Submission Portal. The
    real ENA deposit is made from a test that worked."""

    class Meta:
        model = Deposit
        fields = [
            "archive",
            "title",
            "description",
            "hold_until",
            "sample_checklist",
            "library_source",
            "library_selection",
            "library_strategy",
        ]
        widgets = {
            "description": forms.Textarea(attrs={"rows": 3}),
            "hold_until": forms.DateInput(attrs={"type": "date"}),
        }


class ConfirmForm(BootstrapFormMixin, forms.Form):
    """Sending to the real archive is final: the project code is typed to confirm."""

    confirm = forms.CharField(required=False, label=_("Project code"))

    def __init__(self, *args, project_code: str | None, **kwargs):
        super().__init__(*args, **kwargs)
        if project_code is None:
            del self.fields["confirm"]
        else:
            self.fields["confirm"].required = True
            self.fields["confirm"].help_text = _(
                "Type %(code)s to confirm that the data become public and permanent."
            ) % {"code": project_code}


class ProductionForm(BootstrapFormMixin, forms.Form):
    hold_until = forms.DateField(
        label=_("Release date"),
        widget=forms.DateInput(attrs={"type": "date"}),
        help_text=_("The data stay private until this date."),
    )
    permits_confirmed = forms.BooleanField(
        label=_("Sampling and benefit-sharing permits were checked"),
        help_text=_("Required before anything is made public."),
    )
