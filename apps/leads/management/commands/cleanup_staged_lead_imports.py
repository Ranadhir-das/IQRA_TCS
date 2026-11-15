from django.core.management.base import BaseCommand

from apps.leads.import_services import cleanup_expired_imports


class Command(BaseCommand):
    help = "Erase staged lead-import row data after its expiry time."

    def handle(self, *args, **options):
        count = cleanup_expired_imports()
        self.stdout.write(f"Expired staged imports cleaned: {count}")
