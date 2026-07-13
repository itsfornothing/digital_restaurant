"""
authentication/urls.py

URL routing for the authentication subsystem.

All paths are mounted under /api/v1/ by config/urls.py, so the effective
endpoints are:

  POST  /api/v1/auth/login/
  POST  /api/v1/auth/logout/
  GET   /api/v1/auth/session/
  POST  /api/v1/auth/password-reset/
  POST  /api/v1/auth/password-reset/confirm/
  POST  /api/v1/auth/2fa/setup/
  POST  /api/v1/auth/2fa/verify/
  POST  /api/v1/auth/2fa/disable/
  POST  /api/v1/auth/2fa/challenge/
  POST  /api/v1/auth/2fa/login/
"""

from django.urls import path

from .views import (
    LoginView,
    LogoutView,
    PasswordResetConfirmView,
    PasswordResetRequestView,
    SessionView,
    SignupView,
    TwoFactorChallengeView,
    TwoFactorDisableView,
    TwoFactorLoginView,
    TwoFactorSetupView,
    TwoFactorVerifyView,
    UserViewSet,
)

app_name = "authentication"

urlpatterns = [
    # Registration
    path("auth/register/", SignupView.as_view(), name="register"),
    # Session management
    path("auth/login/", LoginView.as_view(), name="login"),
    path("auth/logout/", LogoutView.as_view(), name="logout"),
    path("auth/session/", SessionView.as_view(), name="session"),
    # Password reset
    path("auth/password-reset/", PasswordResetRequestView.as_view(), name="password-reset"),
    path("auth/password-reset/confirm/", PasswordResetConfirmView.as_view(), name="password-reset-confirm"),
    # Two-factor authentication
    path("auth/2fa/setup/", TwoFactorSetupView.as_view(), name="2fa-setup"),
    path("auth/2fa/verify/", TwoFactorVerifyView.as_view(), name="2fa-verify"),
    path("auth/2fa/disable/", TwoFactorDisableView.as_view(), name="2fa-disable"),
    path("auth/2fa/challenge/", TwoFactorChallengeView.as_view(), name="2fa-challenge"),
    path("auth/2fa/login/", TwoFactorLoginView.as_view(), name="2fa-login"),
    # Staff management
    path("auth/users/", UserViewSet.as_view({"get": "list", "post": "create"}), name="users-list"),
    path("auth/users/<uuid:pk>/", UserViewSet.as_view({"patch": "partial_update"}), name="users-detail"),
    path("auth/users/<uuid:pk>/deactivate/", UserViewSet.as_view({"post": "deactivate"}), name="users-deactivate"),
    path("auth/users/<uuid:pk>/reassign/", UserViewSet.as_view({"post": "reassign"}), name="users-reassign"),
]
