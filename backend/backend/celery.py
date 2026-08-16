"""
The Celery application.

Work is queued rather than done inline because the pipeline is a few hundred model calls per
harvest, each of which can be rate limited. A queue turns that into retries that cost nothing;
doing it in a request turns it into a timeout.
"""

import os

from celery import Celery

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "backend.settings")

app = Celery("ayudagente")
app.config_from_object("django.conf:settings", namespace="CELERY")
app.autodiscover_tasks()
