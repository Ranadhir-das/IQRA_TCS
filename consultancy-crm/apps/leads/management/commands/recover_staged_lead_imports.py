from django.core.management.base import BaseCommand

from apps.leads.import_services import recover_abandoned_imports


class Command(BaseCommand):
    help = "Fail abandoned PROCESSING lead imports and erase their staged rows."

    def handle(self, *args, **options):
        count = recover_abandoned_imports()
        self.stdout.write(f"Abandoned staged imports recovered: {count}")
