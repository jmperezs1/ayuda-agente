from django.apps import AppConfig


class RadarConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "ayudagente.radar"
    label = "radar"
    verbose_name = "Emergency radar"

    def ready(self):
        from ayudagente.radar import signals  # noqa: F401  (registers graph triggers)
