"""
Observability URL routes — /health endpoint.
Full implementation in Task 9.
"""

from django.urls import path

from . import views

urlpatterns = [
    path("", views.health_check, name="health-check"),
]
