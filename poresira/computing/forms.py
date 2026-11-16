# SPDX-License-Identifier: AGPL-3.0-or-later
from django import forms
from django.utils.translation import gettext_lazy as _

from poresira.core.forms import BootstrapFormMixin
from poresira.core.models import Project
from poresira.samples.forms import PAGE_SIZE, ReasonFormMixin, VoidForm
from poresira.storage.models import DataFile

from .jobtypes import JobType
from .models import JobKind, JobStatus

__all__ = ["PAGE_SIZE", "JobFilterForm", "NewJobForm", "ReasonForm", "VoidForm"]


class ReasonForm(BootstrapFormMixin, ReasonFormMixin):
    pass


class JobFilterForm(BootstrapFormMixin, forms.Form):
    project = forms.ModelChoiceField(
        queryset=Project.objects.none(), required=False, label=_("Project"), empty_label=_("All")
    )
    kind = forms.ChoiceField(required=False, label=_("Kind"))
    status = forms.ChoiceField(required=False, label=_("Status"))
    include_voided = forms.BooleanField(required=False, label=_("Show voided"))

    def __init__(self, *args, user, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["project"].queryset = Project.objects.visible_to(user).active()
        self.fields["kind"].choices = [("", _("All")), *JobKind.choices]
        self.fields["status"].choices = [("", _("All")), *JobStatus.choices]


class _FileChoice(forms.ModelMultipleChoiceField):
    def label_from_instance(self, obj):
        return f"{obj.path} ({obj.size_bytes:,} bytes)"


class NewJobForm(BootstrapFormMixin, forms.Form):
    """The parameters of one kind of job, and the input files among those it would read."""

    inputs = _FileChoice(
        queryset=DataFile.objects.none(),
        widget=forms.CheckboxSelectMultiple,
        label=_("Input files"),
    )
    notes = forms.CharField(
        required=False, label=_("Notes"), widget=forms.Textarea(attrs={"rows": 2})
    )

    def __init__(self, *args, job_type: JobType, candidates, initial_parameters: dict, **kwargs):
        super().__init__(*args, **kwargs)
        self.job_type = job_type
        self.fields["inputs"].queryset = DataFile.objects.filter(
            pk__in=[file.pk for file in candidates]
        ).order_by("path")
        self.fields["inputs"].initial = [file.pk for file in candidates]
        parameter_fields = {}
        for parameter in job_type.parameters:
            field_class = forms.IntegerField if parameter.integer else forms.CharField
            parameter_fields[parameter.name] = field_class(
                required=parameter.required,
                label=parameter.label,
                help_text=parameter.help_text,
                initial=initial_parameters.get(parameter.name, parameter.default),
            )
        self.fields = {**parameter_fields, **self.fields}  # parameters first
        for field in self.fields.values():
            if hasattr(field.widget, "attrs") and not isinstance(
                field.widget, forms.CheckboxSelectMultiple
            ):
                field.widget.attrs.setdefault("class", "form-control")
