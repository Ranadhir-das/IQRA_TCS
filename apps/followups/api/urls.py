from django.urls import path

from .views import (
    FollowUpCancelView,
    FollowUpCompleteView,
    FollowUpDetailView,
    FollowUpEventListView,
    FollowUpListCreateView,
    FollowUpRescheduleView,
)


urlpatterns = [
    path("", FollowUpListCreateView.as_view(), name="followup-list-create"),
    path("<int:pk>/", FollowUpDetailView.as_view(), name="followup-detail"),
    path(
        "<int:pk>/events/",
        FollowUpEventListView.as_view(),
        name="followup-events",
    ),
    path(
        "<int:pk>/reschedule/",
        FollowUpRescheduleView.as_view(),
        name="followup-reschedule",
    ),
    path(
        "<int:pk>/complete/",
        FollowUpCompleteView.as_view(),
        name="followup-complete",
    ),
    path(
        "<int:pk>/cancel/",
        FollowUpCancelView.as_view(),
        name="followup-cancel",
    ),
]
