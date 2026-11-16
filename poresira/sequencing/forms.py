# SPDX-License-Identifier: AGPL-3.0-or-later
from django import forms
from django.utils.translation import gettext_lazy as _

from poresira.core.forms import BootstrapFormMixin
from poresira.core.models import Project
from poresira.samples.forms import PAGE_SIZE, ReasonFormMixin, VoidForm
from poresira.samples.models import Sample

from .models import (
    FlowCell,
    FlowCellType,
    Instrument,
    Library,
    LibraryKit,
    LibraryStatus,
    RunStatus,
    SequencingRun,
)

__all__ = [
    "PAGE_SIZE",
    "CompleteRunForm",
    "FlowCellForm",
    "LibraryFilterForm",
    "LibraryForm",
    "MemberForm",
    "PlanRunForm",
    "ReasonForm",
    "ResultForm",
    "RunFilterForm",
    "StartRunForm",
    "VoidForm",
]


class ReasonForm(BootstrapFormMixin, ReasonFormMixin):
    pass


class _FilterForm(BootstrapFormMixin, forms.Form):
    project = forms.ModelChoiceField(
        queryset=Project.objects.none(), required=False, label=_("Project"), empty_label=_("All")
    )
    status = forms.ChoiceField(required=False, label=_("Status"))
    include_voided = forms.BooleanField(required=False, label=_("Show voided"))
    status_choices: list = []

    def __init__(self, *args, user, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["project"].queryset = Project.objects.visible_to(user).active()
        self.fields["status"].choices = [("", _("All")), *self.status_choices]


class LibraryFilterForm(_FilterForm):
    status_choices = LibraryStatus.choices


class RunFilterForm(_FilterForm):
    status_choices = RunStatus.choices


class LibraryForm(BootstrapFormMixin, forms.ModelForm):
    project = forms.ModelChoiceField(queryset=Project.objects.none(), label=_("Project"))
    kit = forms.ModelChoiceField(queryset=LibraryKit.objects.active(), label=_("Kit"))

    class Meta:
        model = Library
        fields = ["project", "kit", "name", "prepared_on", "reconstructed", "notes"]
        widgets = {
            "prepared_on": forms.DateInput(attrs={"type": "date"}),
            "notes": forms.Textarea(attrs={"rows": 3}),
        }

    def __init__(self, *args, editable_projects, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["project"].queryset = editable_projects


class MemberForm(BootstrapFormMixin, forms.Form):
    sample = forms.CharField(
        label=_("Sample"), help_text=_("Identifier or label of the DNA extract or RCA product.")
    )
    barcode = forms.CharField(
        required=False,
        label=_("Barcode"),
        help_text=_("A number, NB07 or barcode07; empty only for a kit without barcodes."),
    )
    input_ng = forms.DecimalField(
        required=False, min_value=0, decimal_places=2, max_digits=8, label=_("Input (ng)")
    )
    notes = forms.CharField(
        required=False, label=_("Notes"), widget=forms.Textarea(attrs={"rows": 2})
    )

    def __init__(self, *args, library, **kwargs):
        super().__init__(*args, **kwargs)
        self.library = library

    def clean_sample(self):
        text = self.cleaned_data["sample"].strip()
        samples = Sample.objects.active().filter(project=self.library.project)
        found = samples.filter(identifier=text) or samples.filter(label=text)
        if len(found) != 1:
            raise forms.ValidationError(
                _("No single sample of this project matches “%(text)s”.") % {"text": text}
            )
        return found[0]


class _FlowCellChoice(forms.ModelChoiceField):
    def label_from_instance(self, obj):
        return f"{obj.flow_cell_id} ({obj.type.code})"


class PlanRunForm(BootstrapFormMixin, forms.ModelForm):
    flow_cell = _FlowCellChoice(
        queryset=FlowCell.objects.active().select_related("type"), label=_("Flow cell")
    )

    class Meta:
        model = SequencingRun
        fields = ["flow_cell", "instrument", "pores_at_start", "notes"]
        widgets = {"notes": forms.Textarea(attrs={"rows": 3})}

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["instrument"].queryset = Instrument.objects.active()


class StartRunForm(BootstrapFormMixin, forms.ModelForm):
    class Meta:
        model = SequencingRun
        fields = ["started_on", "run_id", "acquisition_software", "pores_at_start"]
        widgets = {"started_on": forms.DateInput(attrs={"type": "date"})}


class ResultForm(BootstrapFormMixin, forms.Form):
    reads = forms.IntegerField(min_value=0, label=_("Reads"))
    bases = forms.IntegerField(required=False, min_value=0, label=_("Bases"))
    n50 = forms.IntegerField(required=False, min_value=1, label=_("Read N50 (bp)"))
    mean_quality = forms.DecimalField(
        required=False, min_value=0, decimal_places=1, max_digits=4, label=_("Mean quality (Q)")
    )
    notes = forms.CharField(
        required=False, label=_("Notes"), widget=forms.Textarea(attrs={"rows": 2})
    )
    reason = forms.CharField(
        required=False,
        label=_("Reason"),
        help_text=_("Required once the run is completed; recorded in the audit log."),
        widget=forms.Textarea(attrs={"rows": 2}),
    )


class CompleteRunForm(BootstrapFormMixin, forms.ModelForm):
    class Meta:
        model = SequencingRun
        fields = [
            "basecaller",
            "basecaller_version",
            "basecall_model",
            "total_reads",
            "total_bases",
        ]


class FlowCellForm(BootstrapFormMixin, forms.ModelForm):
    type = forms.ModelChoiceField(queryset=FlowCellType.objects.active(), label=_("Type"))

    class Meta:
        model = FlowCell
        fields = ["flow_cell_id", "type", "pores_at_qc", "notes"]
        widgets = {"notes": forms.Textarea(attrs={"rows": 2})}
