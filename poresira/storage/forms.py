# SPDX-License-Identifier: AGPL-3.0-or-later
from django import forms
from django.utils.translation import gettext_lazy as _

from poresira.core.forms import BootstrapFormMixin
from poresira.core.models import Project
from poresira.samples.forms import PAGE_SIZE, ReasonFormMixin, VoidForm

from .models import FileKind, Integrity, StorageBackend, Tier

__all__ = [
    "PAGE_SIZE",
    "BackendForm",
    "FileFilterForm",
    "ReasonForm",
    "RegisterFileForm",
    "TierForm",
    "VoidForm",
]


class BackendForm(BootstrapFormMixin, forms.ModelForm):
    class Meta:
        model = StorageBackend
        fields = ["name", "kind", "root", "notes"]
        widgets = {"notes": forms.Textarea(attrs={"rows": 2})}


class ReasonForm(BootstrapFormMixin, ReasonFormMixin):
    pass


class FileFilterForm(BootstrapFormMixin, forms.Form):
    q = forms.CharField(required=False, label=_("Path contains"))
    project = forms.ModelChoiceField(
        queryset=Project.objects.none(), required=False, label=_("Project"), empty_label=_("All")
    )
    backend = forms.ModelChoiceField(
        queryset=StorageBackend.objects.active(),
        required=False,
        label=_("Backend"),
        empty_label=_("All"),
    )
    kind = forms.ChoiceField(required=False, label=_("Kind"))
    tier = forms.ChoiceField(required=False, label=_("Tier"))
    integrity = forms.ChoiceField(required=False, label=_("Integrity"))
    include_voided = forms.BooleanField(required=False, label=_("Show voided"))

    def __init__(self, *args, user, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["project"].queryset = Project.objects.visible_to(user).active()
        for name, choices in (
            ("kind", FileKind.choices),
            ("tier", Tier.choices),
            ("integrity", Integrity.choices),
        ):
            self.fields[name].choices = [("", _("All")), *choices]


class RegisterFileForm(BootstrapFormMixin, forms.Form):
    backend = forms.ModelChoiceField(queryset=StorageBackend.objects.active(), label=_("Backend"))
    path = forms.CharField(
        label=_("Path"),
        help_text=_("Relative to the root of the backend, such as runs/run1/reads.fastq.gz."),
    )
    kind = forms.ChoiceField(
        required=False,
        label=_("Kind"),
        choices=[("", _("Detect from the name")), *FileKind.choices],
    )
    notes = forms.CharField(
        required=False, label=_("Notes"), widget=forms.Textarea(attrs={"rows": 2})
    )
    size_bytes = forms.IntegerField(
        required=False,
        min_value=0,
        label=_("Size (bytes)"),
        help_text=_("Only for an offline medium, which cannot be read here."),
    )
    sha256 = forms.CharField(required=False, label=_("SHA-256"))
    md5 = forms.CharField(required=False, label=_("MD5"))


class TierForm(BootstrapFormMixin, ReasonFormMixin):
    tier = forms.ChoiceField(label=_("Tier"), choices=Tier.choices)
