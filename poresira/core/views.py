# SPDX-License-Identifier: AGPL-3.0-or-later
import redis
from django.conf import settings
from django.contrib import messages
from django.contrib.auth.mixins import LoginRequiredMixin
from django.db import connection
from django.db.models import Q
from django.http import HttpResponseForbidden, JsonResponse
from django.shortcuts import get_object_or_404, redirect
from django.utils.http import url_has_allowed_host_and_scheme
from django.utils.translation import gettext as _
from django.views.decorators.http import require_GET, require_POST
from django.views.generic import ListView, TemplateView

from .history import project_history
from .models import Project

LANGUAGE_COOKIE_MAX_AGE = 365 * 24 * 3600
HEALTH_CHECK_TIMEOUT_SECONDS = 2


class ProjectListView(LoginRequiredMixin, ListView):
    context_object_name = "projects"

    def get_queryset(self):
        projects = Project.objects.visible_to(self.request.user).active()
        search_text = self.request.GET.get("q", "").strip()
        if search_text:
            projects = projects.filter(
                Q(code__icontains=search_text) | Q(name__icontains=search_text)
            )
        return projects

    def get_template_names(self):
        if self.request.headers.get("HX-Request"):
            return ["core/_project_rows.html"]
        return ["core/project_list.html"]

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["search_text"] = self.request.GET.get("q", "")
        context["can_load_demo"] = self.request.user.sees_all_projects
        return context


class DemoDataView(LoginRequiredMixin, TemplateView):
    """Let an administrator load the demonstration data without a shell on the server."""

    template_name = "core/demo_data.html"

    def dispatch(self, request, *args, **kwargs):
        if request.user.is_authenticated and not request.user.sees_all_projects:
            return HttpResponseForbidden()
        return super().dispatch(request, *args, **kwargs)

    def post(self, request):
        from poresira.samples.demo.loader import load_demo_data  # heavy: only when asked

        summary = load_demo_data()
        text = _("Demo data already present.") if summary.already_loaded else _("Demo data loaded.")
        messages.success(request, text)
        return redirect("project-list")


class ProjectHistoryView(LoginRequiredMixin, TemplateView):
    template_name = "core/project_history.html"

    def get_context_data(self, **kwargs):
        project = get_object_or_404(Project.objects.visible_to(self.request.user), pk=kwargs["pk"])
        return {
            **super().get_context_data(**kwargs),
            "project": project,
            "entries": project_history(project),
        }


@require_POST
def set_language(request):
    """Store the chosen language on the user profile (if signed in) and in a cookie."""
    language = request.POST.get("language", "")
    next_url = request.POST.get("next", "")
    if not url_has_allowed_host_and_scheme(next_url, allowed_hosts={request.get_host()}):
        next_url = "/"
    response = redirect(next_url)
    if language in dict(settings.LANGUAGES):
        if request.user.is_authenticated:
            request.user.language = language
            request.user.save(update_fields=["language"])
        response.set_cookie(
            settings.LANGUAGE_COOKIE_NAME, language, max_age=LANGUAGE_COOKIE_MAX_AGE, samesite="Lax"
        )
    return response


def _database_is_reachable() -> bool:
    try:
        with connection.cursor() as cursor:
            cursor.execute("SELECT 1")
    except Exception:
        return False
    return True


def _redis_is_reachable() -> bool:
    try:
        client = redis.Redis.from_url(
            settings.REDIS_URL, socket_connect_timeout=HEALTH_CHECK_TIMEOUT_SECONDS
        )
        return bool(client.ping())
    except Exception:
        return False


@require_GET
def health(request):
    checks = {"database": _database_is_reachable(), "redis": _redis_is_reachable()}
    body = {name: "ok" if reachable else "error" for name, reachable in checks.items()}
    body["status"] = "ok" if all(checks.values()) else "error"
    return JsonResponse(body, status=200 if all(checks.values()) else 503)
