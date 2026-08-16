"""
The tool surface: the deterministic services, wrapped so an agent can call them.

One package per tool, named after it. A wrapper translates types and serializes for a
model, and nothing else — every domain rule lives in `ayudagente.<app>.services`, so the
same function behaves identically whether a Celery beat schedule or an agent calls it.

Note:
    Importing anything here imports Django models. A process that is not `manage.py` — a
    Celery worker, a LangGraph server, a notebook — calls `django.setup()` first.
"""
