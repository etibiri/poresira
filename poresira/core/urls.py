# SPDX-License-Identifier: AGPL-3.0-or-later
from django.contrib.auth import views as auth_views
from django.urls import path

from . import views
from .forms import BootstrapAuthenticationForm

urlpatterns = [
    path("", views.ProjectListView.as_view(), name="project-list"),
    path("projects/<uuid:pk>/history/", views.ProjectHistoryView.as_view(), name="project-history"),
    path(
        "login/",
        auth_views.LoginView.as_view(authentication_form=BootstrapAuthenticationForm),
        name="login",
    ),
    path("logout/", auth_views.LogoutView.as_view(), name="logout"),
    path("demo/", views.DemoDataView.as_view(), name="demo-data"),
    path("language/", views.set_language, name="set-language"),
    path("health/", views.health, name="health"),
]
