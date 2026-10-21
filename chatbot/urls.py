from django.urls import path

from . import views

urlpatterns = [
    path("health/live", views.health_live, name="health-live"),
    path("health/ready", views.health_ready, name="health-ready"),
    path("v1/sessions", views.sessions, name="sessions"),
    path("v1/sessions/<uuid:session_id>", views.session_detail, name="session-detail"),
    path("v1/sessions/<uuid:session_id>/messages", views.session_messages, name="session-messages"),
    path("v1/catalog", views.catalog, name="catalog"),
]
