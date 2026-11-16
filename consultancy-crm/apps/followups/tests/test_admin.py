from django.contrib.admin.sites import AdminSite
from django.test import RequestFactory, TestCase

from apps.accounts.models import User
from apps.followups.admin import FollowUpAdmin, FollowUpEventAdmin
from apps.followups.models import FollowUp, FollowUpEvent


class FollowUpAdminTests(TestCase):
    def setUp(self):
        self.site = AdminSite()
        self.model_admin = FollowUpAdmin(FollowUp, self.site)
        self.event_admin = FollowUpEventAdmin(FollowUpEvent, self.site)
        self.request = RequestFactory().get("/admin/followups/followup/")
        self.request.user = User.objects.create(
            username="followup_admin_user",
            role=User.Role.ADMIN,
            is_staff=True,
        )

    def test_followup_admin_is_searchable_filterable_and_read_only(self):
        self.assertIn("status", self.model_admin.list_filter)
        self.assertIn("scheduled_at", self.model_admin.list_filter)
        self.assertIn("lead__name", self.model_admin.search_fields)
        self.assertIn("owner_username_snapshot", self.model_admin.search_fields)
        self.assertIn("lead", self.model_admin.get_list_select_related(self.request))
        self.assertFalse(self.model_admin.has_add_permission(self.request))
        self.assertTrue(self.model_admin.has_change_permission(self.request))
        self.assertFalse(self.model_admin.has_delete_permission(self.request))
        self.assertEqual(
            set(self.model_admin.get_readonly_fields(self.request)),
            {field.name for field in FollowUp._meta.fields},
        )
        self.assertNotIn("lead__phone", self.model_admin.search_fields)

    def test_event_admin_is_read_only(self):
        self.assertTrue(self.event_admin.has_view_permission(self.request))
        self.assertFalse(self.event_admin.has_add_permission(self.request))
        self.assertFalse(self.event_admin.has_change_permission(self.request))
        self.assertFalse(self.event_admin.has_delete_permission(self.request))
        self.assertEqual(
            set(self.event_admin.get_readonly_fields(self.request)),
            {field.name for field in FollowUpEvent._meta.fields},
        )
