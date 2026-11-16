from django.urls import path

from .views import (
    CallHistoryDetailView,
    CallSubmissionView,
)


urlpatterns = [
    path("", CallSubmissionView.as_view(), name="call-submit"),
    path("<int:pk>/", CallHistoryDetailView.as_view(), name="call-history-detail"),
]
