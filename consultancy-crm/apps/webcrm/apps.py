from django.apps import AppConfig
from django.db.models.signals import post_migrate


class WebcrmConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.webcrm"
    verbose_name = "IQRA TCS Admin Web CRM"

    def ready(self):
        from .rate_limit import (
            ensure_shared_cache_table,
            validate_login_rate_limit_settings,
        )

        validate_login_rate_limit_settings()
        post_migrate.connect(
            ensure_shared_cache_table,
            dispatch_uid="webcrm.ensure_shared_cache_table",
        )
