from django.urls import path

from .views import CallOutcomeDetailView, CallOutcomeListView


urlpatterns = [
    path("", CallOutcomeListView.as_view(), name="call-outcome-list"),
    path("<int:pk>/", CallOutcomeDetailView.as_view(), name="call-outcome-detail"),
]
