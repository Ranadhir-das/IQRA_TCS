"""
IQRA TCS Calling CRM — Management Command: create_admin

Creates the initial IQRA TCS ADMIN user interactively.

Usage:
    python manage.py create_admin --username admin --email admin@iqracentral.com

Security notes:
- Password is prompted securely via getpass (never echoed, never stored in shell history).
- The created user has role=ADMIN and is_staff=True.
- is_superuser is NOT set by default; use Django's createsuperuser for emergency access.
- Must be run from a trusted terminal session on a secured machine.
"""

import getpass
import sys

from django.core.management.base import BaseCommand, CommandError

from apps.accounts.models import User


class Command(BaseCommand):
    help = "Create the initial IQRA TCS ADMIN account."

    def add_arguments(self, parser):
        parser.add_argument(
            "--username",
            required=True,
            help="Login username for the admin account.",
        )
        parser.add_argument(
            "--email",
            required=True,
            help="Email address for the admin account.",
        )
        parser.add_argument(
            "--first-name",
            default="",
            dest="first_name",
            help="First name (optional).",
        )
        parser.add_argument(
            "--last-name",
            default="",
            dest="last_name",
            help="Last name (optional).",
        )

    def handle(self, *args, **options):
        username = options["username"].strip()
        email = options["email"].strip()
        first_name = options["first_name"].strip()
        last_name = options["last_name"].strip()

        # Guard: prevent overwriting an existing account
        if User.objects.filter(username__iexact=username).exists():
            raise CommandError(
                f"A user with username '{username}' already exists. "
                "Aborting to prevent overwrite."
            )

        if User.objects.filter(email__iexact=email).exists():
            raise CommandError(
                f"A user with email '{email}' already exists. "
                "Aborting to prevent overwrite."
            )

        self.stdout.write(self.style.WARNING(
            "\nCreating IQRA TCS ADMIN account."
            "\nPassword will not be echoed to the terminal.\n"
        ))

        # Secure password prompt — does not appear in shell history
        try:
            password = getpass.getpass("Password: ")
            confirm = getpass.getpass("Confirm password: ")
        except (KeyboardInterrupt, EOFError):
            self.stderr.write("\nAborted.")
            sys.exit(1)

        if not password:
            raise CommandError("Password cannot be empty.")

        if password != confirm:
            raise CommandError("Passwords do not match. No account was created.")

        # Validate password strength using Django's validators
        from django.contrib.auth.password_validation import validate_password
        from django.core.exceptions import ValidationError

        try:
            validate_password(password)
        except ValidationError as exc:
            raise CommandError(
                "Password validation failed:\n  " + "\n  ".join(exc.messages)
            ) from exc

        user = User.objects.create_user(
            username=username,
            email=email,
            password=password,
            first_name=first_name,
            last_name=last_name,
            role=User.Role.ADMIN,
            is_active=True,
            is_staff=True,          # Allow Django admin login for maintenance
            is_superuser=False,     # Not a Django superuser; use createsuperuser for that
        )

        self.stdout.write(self.style.SUCCESS(
            f"\n✓ ADMIN account created:"
            f"\n  Username  : {user.username}"
            f"\n  Email     : {user.email}"
            f"\n  Role      : {user.role}"
            f"\n  is_staff  : {user.is_staff}"
            f"\n  PK        : {user.pk}"
            f"\n\nLog in at /api/v1/auth/login/"
        ))
