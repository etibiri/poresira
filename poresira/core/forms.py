# SPDX-License-Identifier: AGPL-3.0-or-later
from django import forms
from django.contrib.auth.forms import AuthenticationForm


class BootstrapAuthenticationForm(AuthenticationForm):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for field in self.fields.values():
            field.widget.attrs["class"] = "form-control"


class BootstrapFormMixin:
    """Give every widget of a form its Bootstrap 5 class."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for form_field in self.fields.values():
            widget = form_field.widget
            if isinstance(widget, forms.CheckboxInput):
                css_class = "form-check-input"
            elif isinstance(widget, forms.Select):
                css_class = "form-select"
            else:
                css_class = "form-control"
            widget.attrs["class"] = f"{widget.attrs.get('class', '')} {css_class}".strip()
