# SPDX-License-Identifier: AGPL-3.0-or-later
from decimal import Decimal

from django import forms
from django.utils.translation import gettext_lazy as _

from poresira.core.forms import BootstrapFormMixin
from poresira.core.models import Project
from poresira.samples.forms import PAGE_SIZE, ReasonFormMixin, VoidForm
from poresira.samples.models import Sample

from .models import (
    Assay,
    Container,
    MasterMix,
    PcrReaction,
    PcrRun,
    Primer,
    PrimerDirection,
    Result,
    Role,
    StepStage,
)

__all__ = [
    "PAGE_SIZE",
    "AssayForm",
    "PerformForm",
    "PrimerForm",
    "ProgramForm",
    "ReactionForm",
    "ReasonForm",
    "ResultForm",
    "RunFilterForm",
    "RunForm",
    "VoidForm",
]


class ReasonForm(BootstrapFormMixin, ReasonFormMixin):
    pass


class RunFilterForm(BootstrapFormMixin, forms.Form):
    project = forms.ModelChoiceField(
        queryset=Project.objects.none(), required=False, label=_("Project"), empty_label=_("All")
    )
    assay = forms.ModelChoiceField(
        queryset=Assay.objects.active(), required=False, label=_("Assay"), empty_label=_("All")
    )
    status = forms.ChoiceField(required=False, label=_("Status"))
    include_voided = forms.BooleanField(required=False, label=_("Show voided"))

    def __init__(self, *args, user, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["project"].queryset = Project.objects.visible_to(user).active()
        self.fields["status"].choices = [("", _("All")), *PcrRun._meta.get_field("status").choices]


class RunForm(BootstrapFormMixin, forms.ModelForm):
    project = forms.ModelChoiceField(queryset=Project.objects.none(), label=_("Project"))
    assay = forms.ModelChoiceField(queryset=Assay.objects.active(), label=_("Assay"))

    class Meta:
        model = PcrRun
        fields = ["project", "assay", "container", "tube_count", "reconstructed", "notes"]
        widgets = {"notes": forms.Textarea(attrs={"rows": 3})}
        help_texts = {"tube_count": _("Only for PCR tubes: from 1 to 96.")}

    def __init__(self, *args, editable_projects, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["project"].queryset = editable_projects
        self.fields["tube_count"].required = False

    def clean(self):
        cleaned = super().clean()
        if cleaned.get("container") == Container.PLATE_96:
            cleaned["tube_count"] = None
        elif cleaned.get("container") and not cleaned.get("tube_count"):
            self.add_error("tube_count", _("Give the number of tubes."))
        return cleaned


class ReactionForm(BootstrapFormMixin, forms.Form):
    role = forms.ChoiceField(label=_("Role"), choices=Role.choices, initial=Role.SAMPLE)
    sample = forms.CharField(
        required=False,
        label=_("Template"),
        help_text=_("Identifier or label of the sample; empty for a control."),
    )
    position = forms.CharField(
        required=False,
        label=_("Position"),
        help_text=_("Well such as A1, or tube number; empty takes the next free one."),
    )
    template_volume_ul = forms.DecimalField(
        required=False, min_value=0, decimal_places=2, max_digits=6, label=_("Template (µL)")
    )
    notes = forms.CharField(
        required=False, label=_("Notes"), widget=forms.Textarea(attrs={"rows": 2})
    )

    def __init__(self, *args, run, **kwargs):
        super().__init__(*args, **kwargs)
        self.run = run

    def clean_sample(self):
        text = self.cleaned_data["sample"].strip()
        if not text:
            return None
        samples = Sample.objects.active().filter(project=self.run.project)
        found = samples.filter(identifier=text) or samples.filter(label=text)
        if len(found) != 1:
            raise forms.ValidationError(
                _("No single sample of this project matches “%(text)s”.") % {"text": text}
            )
        return found[0]

    def clean(self):
        cleaned = super().clean()
        if cleaned.get("role") == Role.SAMPLE and not cleaned.get("sample"):
            self.add_error("sample", _("A sample reaction needs a template."))
        if cleaned.get("role") != Role.SAMPLE and cleaned.get("sample"):
            self.add_error("sample", _("A control has no template sample."))
        return cleaned


class PerformForm(BootstrapFormMixin, forms.ModelForm):
    class Meta:
        model = PcrRun
        fields = ["performed_on", "thermocycler", "master_mix_lot", "polymerase_lot"]
        widgets = {"performed_on": forms.DateInput(attrs={"type": "date"})}


class ResultForm(BootstrapFormMixin, forms.ModelForm):
    reason = forms.CharField(
        required=False,
        label=_("Reason"),
        help_text=_("Required once the run is closed; recorded in the audit log."),
        widget=forms.Textarea(attrs={"rows": 2}),
    )

    class Meta:
        model = PcrReaction
        fields = ["result", "ct", "band_size_bp", "notes"]
        widgets = {"notes": forms.Textarea(attrs={"rows": 2})}

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["result"].choices = [c for c in Result.choices if c[0] != Result.PENDING]


class PrimerForm(BootstrapFormMixin, forms.ModelForm):
    class Meta:
        model = Primer
        fields = ["name", "sequence", "direction", "target", "reference", "notes"]
        widgets = {"notes": forms.Textarea(attrs={"rows": 2})}


class ProgramForm(BootstrapFormMixin, forms.Form):
    name = forms.CharField(max_length=100, label=_("Name"))
    cycles = forms.IntegerField(min_value=1, label=_("Cycles"))
    steps = forms.CharField(
        label=_("Steps"),
        widget=forms.Textarea(attrs={"rows": 6}),
        help_text=_("One step per line: stage;°C;seconds;label, e.g. cycle;55;30;Annealing"),
    )
    reference = forms.CharField(required=False, label=_("Reference"))
    notes = forms.CharField(
        required=False, label=_("Notes"), widget=forms.Textarea(attrs={"rows": 2})
    )

    def clean_steps(self) -> list[dict]:
        steps = []
        for number, line in enumerate(self.cleaned_data["steps"].splitlines(), start=1):
            if not line.strip():
                continue
            try:
                stage, temperature, seconds, *label = [part.strip() for part in line.split(";")]
                if stage not in StepStage.values:
                    raise ValueError
                steps.append(
                    {
                        "stage": stage,
                        "temperature_c": Decimal(temperature),
                        "duration_seconds": int(seconds),
                        "label": ";".join(label),
                    }
                )
            except (ValueError, ArithmeticError):
                raise forms.ValidationError(
                    _("Line %(number)s is not stage;temperature;duration;label."),
                    params={"number": number},
                ) from None
        return steps


class AssayForm(BootstrapFormMixin, forms.ModelForm):
    class Meta:
        model = Assay
        fields = [
            "name",
            "target",
            "forward_primer",
            "reverse_primer",
            "cycling_program",
            "master_mix",
            "reverse_transcription",
            "expected_amplicon_bp",
            "reference",
            "notes",
        ]
        widgets = {"notes": forms.Textarea(attrs={"rows": 2})}

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        primers = Primer.objects.active()
        self.fields["forward_primer"].queryset = primers.filter(direction=PrimerDirection.FORWARD)
        self.fields["reverse_primer"].queryset = primers.filter(direction=PrimerDirection.REVERSE)
        self.fields["master_mix"].queryset = MasterMix.objects.active()
