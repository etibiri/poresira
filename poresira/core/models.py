# SPDX-License-Identifier: AGPL-3.0-or-later
from django.conf import settings
from django.contrib.auth.models import AbstractUser
from django.db import models
from django.utils.translation import gettext_lazy as _

from .uuid7 import uuid7


class UUIDPrimaryKeyModel(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid7, editable=False)

    class Meta:
        abstract = True


class GlobalRole(models.TextChoices):
    ADMINISTRATOR = "administrator", _("Administrator")
    PLATFORM_MANAGER = "platform_manager", _("Platform manager")
    TECHNICIAN = "technician", _("Technician")
    BIOINFORMATICIAN = "bioinformatician", _("Bioinformatician")
    PROJECT_LEAD = "project_lead", _("Project lead")
    GUEST = "guest", _("Guest")


class User(UUIDPrimaryKeyModel, AbstractUser):  # noqa: DJ008 (inherits __str__)
    role = models.CharField(max_length=32, choices=GlobalRole.choices, default=GlobalRole.GUEST)
    language = models.CharField(
        max_length=8,
        choices=settings.LANGUAGES,
        blank=True,
        help_text=_("Interface language. Empty means the site default."),
    )

    @property
    def sees_all_projects(self) -> bool:
        return self.is_superuser or self.role == GlobalRole.ADMINISTRATOR


class ProjectQuerySet(models.QuerySet):
    def visible_to(self, user) -> "ProjectQuerySet":
        if not user.is_authenticated:
            return self.none()
        if user.sees_all_projects:
            return self
        return self.filter(memberships__user=user)


class Project(UUIDPrimaryKeyModel):
    code = models.SlugField(max_length=32, unique=True)
    name = models.CharField(max_length=200)
    description = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    objects = ProjectQuerySet.as_manager()

    class Meta:
        ordering = ["code"]

    def __str__(self) -> str:
        return f"{self.code} - {self.name}"


class ProjectRole(models.TextChoices):
    LEAD = "lead", _("Lead")
    CONTRIBUTOR = "contributor", _("Contributor")
    VIEWER = "viewer", _("Viewer")


class ProjectMembership(UUIDPrimaryKeyModel):
    project = models.ForeignKey(Project, on_delete=models.PROTECT, related_name="memberships")
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="project_memberships"
    )
    role = models.CharField(max_length=16, choices=ProjectRole.choices, default=ProjectRole.VIEWER)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["project", "user"], name="unique_membership_per_project"
            ),
        ]

    def __str__(self) -> str:
        return f"{self.user} in {self.project} ({self.role})"
