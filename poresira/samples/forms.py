# SPDX-License-Identifier: AGPL-3.0-or-later
from django import forms
from django.utils.translation import gettext_lazy as _

from poresira.core.forms import BootstrapFormMixin
from poresira.core.models import Project

from .models import (
    CollectionEvent,
    QcMethod,
    QcOutcome,
    Sample,
    SampleKind,
    SampleStatus,
    SequenceRecord,
    StorageLocation,
    StorageLocationKind,
)
from .services import ALLOWED_DERIVATIONS, QC_ATTRIBUTES

PAGE_SIZE = 50
COLLECTION_FIELDS = [
    "collected_on",
    "country",
    "region",
    "province",
    "locality",
    "site_name",
    "latitude",
    "longitude",
    "collector",
    "notes",
]
DESCRIPTIVE_FIELDS = [
    "label",
    "host_taxon",
    "host_tax_id",
    "host_variety",
    "symptom_score",
    "symptom_notes",
    "notes",
]
HOST_AND_SYMPTOM_FIELDS = DESCRIPTIVE_FIELDS[1:6]
SAMPLE_CREATION_FIELDS = [*DESCRIPTIVE_FIELDS, "barcode", "concentration_ng_ul", "volume_ul"]
STORAGE_FIELDS = [
    "name",
    "kind",
    "parent",
    "nominal_temperature_c",
    "grid_rows",
    "grid_columns",
    "notes",
]


def storage_paths() -> dict:
    """Full path of every active storage location, in one query."""
    locations = {location.pk: location for location in StorageLocation.objects.active()}

    def path_of(location) -> str:
        names = []
        while location is not None:
            names.append(location.name)
            location = locations.get(location.parent_id)
        return " > ".join(reversed(names))

    return {pk: path_of(location) for pk, location in locations.items()}


class StorageLocationChoiceField(forms.ModelChoiceField):
    """Active storage locations labelled with their full path, computed once per form."""

    paths = None

    def __init__(self, **kwargs):
        super().__init__(queryset=StorageLocation.objects.active(), **kwargs)

    def label_from_instance(self, location) -> str:
        if self.paths is None:
            self.paths = storage_paths()
        return self.paths.get(location.pk, location.name)


class SampleFilterForm(BootstrapFormMixin, forms.Form):
    q = forms.CharField(
        required=False,
        label=_("Search"),
        widget=forms.TextInput(attrs={"placeholder": _("Identifier or label")}),
    )
    project = forms.ModelChoiceField(
        queryset=Project.objects.none(), required=False, label=_("Project"), empty_label=_("All")
    )
    kind = forms.ChoiceField(required=False, label=_("Type"))
    status = forms.ChoiceField(required=False, label=_("Status"))
    include_voided = forms.BooleanField(required=False, label=_("Show voided"))

    def __init__(self, *args, user, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["project"].queryset = Project.objects.visible_to(user).active()
        self.fields["kind"].choices = [("", _("All")), *SampleKind.choices]
        self.fields["status"].choices = [("", _("All")), *SampleStatus.choices]


class CollectionEventForm(BootstrapFormMixin, forms.ModelForm):
    project = forms.ModelChoiceField(queryset=Project.objects.none(), label=_("Project"))

    class Meta:
        model = CollectionEvent
        fields = COLLECTION_FIELDS
        widgets = {
            "collected_on": forms.DateInput(attrs={"type": "date"}),
            "notes": forms.Textarea(attrs={"rows": 3}),
        }

    field_order = ["project", *COLLECTION_FIELDS]

    def __init__(self, *args, editable_projects, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["project"].queryset = editable_projects


class FieldSampleForm(BootstrapFormMixin, forms.ModelForm):
    class Meta:
        model = Sample
        fields = SAMPLE_CREATION_FIELDS
        widgets = {"notes": forms.Textarea(attrs={"rows": 3})}


class DerivativeForm(FieldSampleForm):
    kind = forms.ChoiceField(label=_("Type"))

    field_order = ["kind"]

    def __init__(self, *args, parent, **kwargs):
        super().__init__(*args, **kwargs)
        allowed = ALLOWED_DERIVATIONS[parent.kind]
        self.fields["kind"].choices = [(k.value, k.label) for k in SampleKind if k in allowed]
        for name in HOST_AND_SYMPTOM_FIELDS:
            self.fields[name].required = False


class SampleEditForm(BootstrapFormMixin, forms.ModelForm):
    class Meta:
        model = Sample
        fields = DESCRIPTIVE_FIELDS
        widgets = {"notes": forms.Textarea(attrs={"rows": 3})}


class ReasonFormMixin(forms.Form):
    reason = forms.CharField(
        label=_("Reason"),
        help_text=_("Recorded in the audit log."),
        widget=forms.Textarea(attrs={"rows": 2}),
    )

    def clean_reason(self) -> str:
        return self.cleaned_data["reason"].strip()


class MoveForm(BootstrapFormMixin, ReasonFormMixin):
    location = StorageLocationChoiceField(
        required=False,
        label=_("Storage location"),
        empty_label=_("Not in storage"),
    )
    position = forms.CharField(
        required=False,
        label=_("Position"),
        help_text=_("Grid position such as A1, for boxes with a grid."),
    )
    field_order = ["location", "position", "reason"]


class ChangeForm(BootstrapFormMixin, ReasonFormMixin):
    status = forms.ChoiceField(label=_("Status"), choices=SampleStatus.choices)
    barcode = forms.CharField(required=False, label=_("Barcode"))
    concentration_ng_ul = forms.DecimalField(
        required=False,
        min_value=0,
        decimal_places=3,
        max_digits=10,
        label=_("Concentration (ng/µL)"),
    )
    volume_ul = forms.DecimalField(
        required=False, min_value=0, decimal_places=2, max_digits=10, label=_("Volume (µL)")
    )
    field_order = ["status", "barcode", "concentration_ng_ul", "volume_ul", "reason"]


class VoidForm(BootstrapFormMixin, ReasonFormMixin):
    pass


class QcForm(BootstrapFormMixin, forms.Form):
    method = forms.ChoiceField(label=_("Method"), choices=QcMethod.choices)
    concentration_ng_ul = forms.DecimalField(
        required=False,
        min_value=0,
        decimal_places=3,
        max_digits=10,
        label=_("Concentration (ng/µL)"),
    )
    a260_280 = forms.DecimalField(
        required=False, min_value=0, decimal_places=2, max_digits=5, label=_("A260/280")
    )
    a260_230 = forms.DecimalField(
        required=False, min_value=0, decimal_places=2, max_digits=5, label=_("A260/230")
    )
    integrity_number = forms.DecimalField(
        required=False,
        min_value=0,
        max_value=10,
        decimal_places=1,
        max_digits=3,
        label=_("Integrity number"),
    )
    outcome = forms.ChoiceField(
        label=_("Outcome"), choices=QcOutcome.choices, initial=QcOutcome.NOT_ASSESSED
    )
    notes = forms.CharField(
        required=False, label=_("Notes"), widget=forms.Textarea(attrs={"rows": 2})
    )
    apply_to_sample = forms.BooleanField(
        required=False, label=_("Also set this concentration on the sample")
    )

    def values(self) -> dict:
        """Entered values for the QC service, leaving out what was left blank."""
        return {
            name: value
            for name, value in self.cleaned_data.items()
            if name in QC_ATTRIBUTES and value not in (None, "")
        }


class StorageLocationForm(BootstrapFormMixin, forms.ModelForm):
    parent = StorageLocationChoiceField(required=False, label=_("Parent"))
    kind = forms.ChoiceField(label=_("Type"), choices=StorageLocationKind.choices)

    class Meta:
        model = StorageLocation
        fields = STORAGE_FIELDS
        widgets = {"notes": forms.Textarea(attrs={"rows": 2})}


class SequenceRecordForm(BootstrapFormMixin, forms.ModelForm):
    """A sequence that is already public in GenBank (or ENA, DDBJ), made from the sample."""

    class Meta:
        model = SequenceRecord
        fields = [
            "accession",
            "version",
            "organism",
            "isolate",
            "length_bp",
            "topology",
            "assembly_method",
            "biosample_accession",
            "bioproject_accession",
            "notes",
        ]
        widgets = {"notes": forms.Textarea(attrs={"rows": 2})}
