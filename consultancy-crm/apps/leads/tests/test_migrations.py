from django.conf import settings
from django.db import connection
from django.db.migrations.executor import MigrationExecutor
from django.test import TransactionTestCase


class AssignmentIdentitySnapshotMigrationTests(TransactionTestCase):
    migrate_from = ("leads", "0003_leadassignmenthistory")
    migrate_to = ("leads", "0004_leadassignmenthistory_changed_by_name_and_more")

    def setUp(self):
        super().setUp()
        executor = MigrationExecutor(connection)
        executor.migrate([self.migrate_from])
        old_apps = executor.loader.project_state([self.migrate_from]).apps

        user_app, user_model = settings.AUTH_USER_MODEL.split(".")
        User = old_apps.get_model(user_app, user_model)
        Lead = old_apps.get_model("leads", "Lead")
        History = old_apps.get_model("leads", "LeadAssignmentHistory")

        employee = User.objects.create(
            username="snapshot_migration_employee",
            password="not-a-real-login",
            first_name="Morgan",
            last_name="Student",
            role="EMPLOYEE",
        )
        admin = User.objects.create(
            username="snapshot_migration_admin",
            password="not-a-real-login",
            first_name="Riley",
            last_name="Admin",
            role="ADMIN",
        )
        lead = Lead.objects.create(name="Migration lead", phone="9876500091")
        self.history_id = History.objects.create(
            lead=lead,
            previous_employee=employee,
            new_employee=employee,
            changed_by=admin,
            reason="Legacy event",
        ).pk
        self.history_with_missing_employees_id = History.objects.create(
            lead=lead,
            previous_employee=None,
            new_employee=None,
            changed_by=admin,
            reason="Legacy event without employee references",
        ).pk

        executor = MigrationExecutor(connection)
        executor.migrate([self.migrate_to])
        self.new_apps = executor.loader.project_state([self.migrate_to]).apps

    def tearDown(self):
        executor = MigrationExecutor(connection)
        executor.migrate(executor.loader.graph.leaf_nodes())
        super().tearDown()

    def test_existing_related_identities_are_backfilled_without_inventing_missing_users(self):
        History = self.new_apps.get_model("leads", "LeadAssignmentHistory")
        history = History.objects.get(pk=self.history_id)
        self.assertEqual(
            history.previous_employee_username, "snapshot_migration_employee"
        )
        self.assertEqual(history.previous_employee_name, "Morgan Student")
        self.assertEqual(history.new_employee_username, "snapshot_migration_employee")
        self.assertEqual(history.new_employee_name, "Morgan Student")
        self.assertEqual(history.changed_by_username, "snapshot_migration_admin")
        self.assertEqual(history.changed_by_name, "Riley Admin")

        missing = History.objects.get(pk=self.history_with_missing_employees_id)
        self.assertIsNone(missing.previous_employee_username)
        self.assertIsNone(missing.previous_employee_name)
        self.assertIsNone(missing.new_employee_username)
        self.assertIsNone(missing.new_employee_name)
        self.assertEqual(missing.changed_by_username, "snapshot_migration_admin")
