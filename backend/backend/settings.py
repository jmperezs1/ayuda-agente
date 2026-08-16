import os
from pathlib import Path

from corsheaders.defaults import default_headers
from dotenv import load_dotenv

load_dotenv()

BASE_DIR = Path(__file__).resolve().parent.parent

SECRET_KEY = os.environ.get("SECRET_KEY", "insecure-dev-key-change-me")

DEBUG = os.environ.get("DEBUG", "True") == "True"

ALLOWED_HOSTS = os.environ.get("ALLOWED_HOSTS", "*").split(",")

INSTALLED_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "django.contrib.gis",
    "corsheaders",
    # Registers the `unaccent` and trigram lookups; the extensions themselves are migrated
    "django.contrib.postgres",
    "ayudagente.radar",
]

MIDDLEWARE = [
    "corsheaders.middleware.CorsMiddleware",
    "django.middleware.security.SecurityMiddleware",
    # Above the session and auth middleware: a rejected client never costs a session lookup
    "ayudagente.radar.middleware.ApiKeyMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
]

ROOT_URLCONF = "backend.urls"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
            ],
        },
    },
]

WSGI_APPLICATION = "backend.wsgi.application"

DATABASES = {
    "default": {
        "ENGINE": "django.contrib.gis.db.backends.postgis",
        "NAME": os.environ.get("DB_NAME", "hackaton"),
        "USER": os.environ.get("DB_USER", "hackaton"),
        "PASSWORD": os.environ.get("DB_PASSWORD", "hackaton"),
        "HOST": os.environ.get("DB_HOST", "localhost"),
        "PORT": os.environ.get("DB_PORT", "5432"),
    }
}

AUTH_PASSWORD_VALIDATORS = [
    {"NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator"},
    {"NAME": "django.contrib.auth.password_validation.MinimumLengthValidator"},
    {"NAME": "django.contrib.auth.password_validation.CommonPasswordValidator"},
    {"NAME": "django.contrib.auth.password_validation.NumericPasswordValidator"},
]

LANGUAGE_CODE = "en-us"
TIME_ZONE = "UTC"
USE_I18N = True
USE_TZ = True

# Harvested images live on disk, not in Postgres: bytes would bloat every backup
MEDIA_ROOT = Path(os.environ.get("MEDIA_ROOT", BASE_DIR / "media"))
MEDIA_URL = "/media/"  # rooted, or a cross-origin frontend resolves it against its own host

STATIC_URL = "static/"
STATIC_ROOT = BASE_DIR / "staticfiles"

DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

# Django's stock config only logs to console while DEBUG, so a deployment 500 leaves no trace
LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "formatters": {
        "standard": {"format": "{asctime} {levelname} {name} {message}", "style": "{"},
    },
    "handlers": {
        "console": {"class": "logging.StreamHandler", "formatter": "standard"},
    },
    "root": {"handlers": ["console"], "level": "WARNING"},
    "loggers": {
        "ayudagente": {"level": os.environ.get("LOG_LEVEL", "INFO")},
        "django.request": {"handlers": ["console"], "level": "ERROR", "propagate": False},
    },
}

# Frontend runs on its own origin during the hackathon; open CORS only in DEBUG.
CORS_ALLOW_ALL_ORIGINS = DEBUG
CORS_ALLOWED_ORIGINS = [
    origin for origin in os.environ.get("CORS_ALLOWED_ORIGINS", "").split(",") if origin
]
CORS_ALLOW_HEADERS = (*default_headers, "x-api-key")  # or the preflight drops the key header

# The API authenticates the client, not a user: one shared key per consumer
API_KEYS = [key.strip() for key in os.environ.get("API_KEYS", "").split(",") if key.strip()]
API_KEY_PROTECTED_PREFIXES = ["/api/"]

# Pin the working libraries when a host carries a second, incompatible GDAL/GEOS build
if os.environ.get("GDAL_LIBRARY_PATH"):
    GDAL_LIBRARY_PATH = os.environ["GDAL_LIBRARY_PATH"]
if os.environ.get("GEOS_LIBRARY_PATH"):
    GEOS_LIBRARY_PATH = os.environ["GEOS_LIBRARY_PATH"]

# Celery. No result backend: a task's output is the rows it wrote
CELERY_BROKER_URL = os.environ.get("CELERY_BROKER_URL", "redis://localhost:6379/0")
CELERY_TASK_ACKS_LATE = True
CELERY_WORKER_PREFETCH_MULTIPLIER = 1
CELERY_TASK_TIME_LIMIT = 600

# A scrape holds a slot ~40s and a read ~3s, so they get separate pools
CELERY_TASK_ROUTES = {"ayudagente.radar.tasks.harvest": {"queue": "harvest"}}

# The pool size is the real governor; this only stops a burst from tripping the model's quota
EXTRACTION_RATE_LIMIT = os.environ.get("EXTRACTION_RATE_LIMIT", "240/m")

# One beat drives the whole perpetual loop; the pacing rules decide what it actually does
CELERY_BEAT_SCHEDULE = {
    "radar-tick": {
        "task": "ayudagente.radar.tasks.tick",
        "schedule": float(os.environ.get("TICK_SECONDS", 300)),
    },
    # Free and unattended by design: it proposes paused events and nothing else
    "radar-watch": {
        "task": "ayudagente.radar.tasks.watch_for_events",
        "schedule": float(os.environ.get("WATCH_SECONDS", 900)),
    },
}

# OpenAI. A model per role, mapped onto the GPT-5.6 tiers
OPENAI_API_KEY = os.environ.get("OPENAI_API_KEY", "")
OPENAI_MODELS = {
    "reasoning": os.environ.get("OPENAI_MODEL_REASONING", "gpt-5.6-sol"),
    "extraction": os.environ.get("OPENAI_MODEL_EXTRACTION", "gpt-5.6-sol"),
    "triage": os.environ.get("OPENAI_MODEL_TRIAGE", "gpt-5.6-luna"),
    "embedding": os.environ.get("OPENAI_MODEL_EMBEDDING", "text-embedding-3-small"),
}

GOOGLE_GEOCODING_API_KEY = os.environ.get("GOOGLE_GEOCODING_API_KEY", "")

# Apify. Without it the frontier can decide where to look but nothing ever fetches a post
APIFY_TOKEN = os.environ.get("APIFY_TOKEN", "")

# Circuit breaker, not a budget: past this an event is paused. Zero disables it
HARVEST_SPEND_CEILING_USD = float(os.environ.get("HARVEST_SPEND_CEILING_USD", 25))

# The same breaker across every event, refused at the gate so raising it resumes on the spot
HARVEST_SPEND_TOTAL_CEILING_USD = float(os.environ.get("HARVEST_SPEND_TOTAL_CEILING_USD", 25))
