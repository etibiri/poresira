# SPDX-License-Identifier: AGPL-3.0-or-later
import re

from django.conf import settings
from django.utils import translation
from pghistory.middleware import HistoryMiddleware

from .uuid7 import uuid7

REQUEST_ID_HEADER = "X-Request-ID"
REASON_FIELD = "reason"
REASON_HEADER = "X-Audit-Reason"
REASON_MAX_LENGTH = 500
VALID_REQUEST_ID = re.compile(r"^[A-Za-z0-9._-]{8,64}$")


def client_ip(request) -> str | None:
    if settings.PORESIRA_TRUST_X_FORWARDED_FOR:
        forwarded_for = request.META.get("HTTP_X_FORWARDED_FOR", "")
        if forwarded_for:
            return forwarded_for.split(",")[0].strip()
    return request.META.get("REMOTE_ADDR")


def supplied_reason(request) -> str:
    reason = request.POST.get(REASON_FIELD) or request.headers.get(REASON_HEADER) or ""
    return reason.strip()[:REASON_MAX_LENGTH]


class AuditContextMiddleware(HistoryMiddleware):
    """Attach user, URL, IP address, request ID and optional reason to every audit entry.

    Must run after AuthenticationMiddleware. Only state-changing methods carry context.
    """

    def __call__(self, request):
        supplied_id = request.headers.get(REQUEST_ID_HEADER, "")
        request.request_id = supplied_id if VALID_REQUEST_ID.match(supplied_id) else uuid7().hex
        response = super().__call__(request)
        response[REQUEST_ID_HEADER] = request.request_id
        return response

    def get_context(self, request):
        context = super().get_context(request)
        context["ip_address"] = client_ip(request)
        context["request_id"] = request.request_id
        if reason := supplied_reason(request):
            context["reason"] = reason
        return context


class UserLanguageMiddleware:
    """Pick the interface language: user preference, then cookie, then the site default.

    Replaces Django's LocaleMiddleware on purpose: the browser's Accept-Language
    must not override PORESIRA_DEFAULT_LANGUAGE.
    """

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        language = self.chosen_language(request)
        translation.activate(language)
        request.LANGUAGE_CODE = language
        response = self.get_response(request)
        response.headers.setdefault("Content-Language", language)
        return response

    @staticmethod
    def chosen_language(request) -> str:
        available = {code for code, _name in settings.LANGUAGES}
        candidates = (
            getattr(request.user, "language", ""),
            request.COOKIES.get(settings.LANGUAGE_COOKIE_NAME, ""),
            settings.LANGUAGE_CODE,
        )
        return next((code for code in candidates if code in available), "en")
