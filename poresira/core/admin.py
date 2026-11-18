# SPDX-License-Identifier: AGPL-3.0-or-later
from django.contrib import admin
from django.contrib.auth.admin import UserAdmin
from django.utils.translation import gettext_lazy as _

from .models import Project, ProjectMembership, User

admin.site.site_title = "PoreSira"
admin.site.site_header = "PoreSira"
admin.site.index_title = _("Administration")


@admin.register(User)
class PoreSiraUserAdmin(UserAdmin):
    fieldsets = (*UserAdmin.fieldsets, ("PoreSira", {"fields": ("role", "language")}))


class ProjectMembershipInline(admin.TabularInline):
    model = ProjectMembership
    extra = 0


@admin.register(Project)
class ProjectAdmin(admin.ModelAdmin):
    list_display = ("code", "name")
    inlines = [ProjectMembershipInline]
