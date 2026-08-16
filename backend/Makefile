COMPOSE ?= docker compose
SERVICE ?= db

UV ?= uv
MANAGE = $(UV) run manage.py

# Extra flags for pytest or manage.py, to narrow a run while working.
ARGS ?=

ENV_FILE ?= .env
ENV_TEMPLATE ?= .env.example

# The deployment keeps its env outside the clone, so `git reset --hard` cannot take it.
PROD_ROOT ?= /opt/ayudagente
PROD_HOST ?= fabcloudlet_n1
PROD_SERVICE ?= web

# Empty when this is already the server; the ssh hop from anywhere else.
REMOTE := $(shell test -f $(PROD_ROOT)/.env || echo "ssh -t $(PROD_HOST)")

DC = $(REMOTE) $(PROD_ROOT)/dc.sh
PROD_MANAGE = $(DC) run --rm web python manage.py

# `init` is what creates the env file, so it runs before the guard below applies.
BOOTSTRAP_GOALS = init help

# Only the local targets read it: a prod target talks to the env under $(PROD_ROOT).
LOCAL_GOALS = $(filter-out $(BOOTSTRAP_GOALS) prod.%,$(or $(MAKECMDGOALS),help))

# Django reads the database credentials from it, so no local target works without it.
$(if $(LOCAL_GOALS),\
	$(if $(wildcard $(ENV_FILE)),,$(error $(ENV_FILE) not found: run make init)))

# Set LIVE to anything to include tests that reach Postgres, Azure or Apify.
LIVE ?=

# `-m ""` clears the `-m "not live"` in `pyproject.toml`, so the whole suite runs.
MARKERS = $(if $(LIVE),-m "",)

.DEFAULT_GOAL := help
.PHONY: help init up down ps logs check lint format comments types test \
        migrate migrations run shell superuser apikey seed unseed \
        taxonomy gazetteer events watch arm harvest pipeline graph media report narrate tick link \
        worker beat \
        prod.deploy prod.seed prod.unseed prod.logs prod.ps prod.shell prod.migrate \
        prod.taxonomy prod.gazetteer \
        prod.events prod.watch prod.arm prod.harvest prod.pipeline prod.graph prod.media \
        prod.link prod.report prod.narrate prod.tick prod.workers prod.workers-down prod.ceiling

help: ## Show available commands
	@awk 'BEGIN {FS = ":.*##"; printf "Local:\n"} /^[a-z0-9_-]+:.*##/ {printf "  make %-14s %s\n", $$1, $$2}' $(MAKEFILE_LIST)
	@awk 'BEGIN {FS = ":.*##"; printf "\nDeployment (api.ayudagente.help):\n"} /^prod\.[a-z-]+:.*##/ {printf "  make %-19s %s\n", $$1, $$2}' $(MAKEFILE_LIST)
	@echo ""; echo "Everything else is a Django command: uv run manage.py help"

# ---------------------------------------------------------------- local stack

init: ## Create .venv, install deps and seed .env from .env.example
	@test -f $(ENV_FILE) || { cp $(ENV_TEMPLATE) $(ENV_FILE) && echo "created $(ENV_FILE): fill in the secrets"; }
	$(UV) sync

up: ## Build and start Postgres and Redis
	$(COMPOSE) up --build -d

down: ## Stop the local stack
	$(COMPOSE) down

ps: ## Show container status
	$(COMPOSE) ps

logs: ## Follow logs (make logs SERVICE=redis)
	$(COMPOSE) logs -f $(SERVICE)

# ---------------------------------------------------------------- quality

check: lint format comments types test ## Lint, format, comments, types and the hermetic suite

lint:
	$(UV) run ruff check .

format:
	$(UV) run ruff format --check .

comments:
	$(UV) run tools/check_comments.py ayudagente agent_tools backend tools

types:
	$(UV) run pyrefly check

test: ## Run the suite (make test LIVE=1 to include tests that need real services)
	$(UV) run pytest $(MARKERS) $(ARGS)

# ---------------------------------------------------------------- database

migrations: ## Generate migrations
	$(MANAGE) makemigrations $(ARGS)

migrate: ## Apply migrations
	$(MANAGE) migrate $(ARGS)

taxonomy: ## Load the resource catalog. Reference data: every environment needs it
	$(MANAGE) load_taxonomy $(ARGS)

gazetteer: ## Load a country's places from GeoNames (make gazetteer ARGS=CO)
	$(MANAGE) load_gazetteer $(ARGS)

seed: ## Load the development fixtures (the pilot corpus). Never in production
	$(MANAGE) seed $(ARGS)

unseed: ## Delete the seed datasets
	$(MANAGE) seed --clear $(ARGS)

# ---------------------------------------------------------------- operating

events: ## List events, whether each may be harvested, and the spend so far
	$(MANAGE) events $(ARGS)

watch: ## Poll USGS and propose new events. Costs nothing and scrapes nothing
	$(MANAGE) watch_events --list $(ARGS)

arm: ## Let an event be harvested. ARGS="<event_id> --hashtags sismo,chocó"
	$(MANAGE) arm_event $(ARGS)

harvest: ## Run the jobs an armed event queued. Spends Apify. ARGS="--limit 3"
	$(MANAGE) harvest $(ARGS)

pipeline: ## Read an event's posts into requirements. Spends OpenAI. ARGS="--limit 20"
	$(MANAGE) run_pipeline $(ARGS)

link: ## Attach stored locations to their municipality. ARGS="--dry-run"
	$(MANAGE) link_locations $(ARGS)

graph: ## Recompute matches and the stored graph. ARGS="--event 1 --force"
	$(MANAGE) build_graph $(ARGS)

media: ## Download harvested images before their signed URLs expire
	$(MANAGE) download_media $(ARGS)

report: ## Compare what each harvesting route produced. ARGS="--event 1"
	$(MANAGE) harvest_report $(ARGS)

narrate: ## Follow the loop in prose. ARGS="--once" for a snapshot
	$(MANAGE) narrate $(ARGS)

tick: ## Beat the loop once, now, without waiting for TICK_SECONDS
	$(MANAGE) tick

worker: ## Run a Celery worker
	$(UV) run celery -A backend worker -l info

beat: ## Run the scheduler that drives the perpetual loop
	$(UV) run celery -A backend beat -l info

shell: ## Django shell
	$(MANAGE) shell

superuser: ## Create an admin user
	$(MANAGE) createsuperuser

apikey: ## Mint an API key into .env. ARGS="--replace" drops the existing ones
	$(MANAGE) apikey $(ARGS)

run: ## Development server
	$(MANAGE) runserver $(ARGS)

# ---------------------------------------------------------------- deployment

prod.deploy: ## Redeploy from origin/main. Creates no rows. ARGS="--reset" wipes the volume
	$(REMOTE) $(PROD_ROOT)/deploy.sh $(ARGS)

prod.taxonomy: ## Load the resource catalog. Reference data, needed before anything is armed
	$(PROD_MANAGE) load_taxonomy

prod.gazetteer: ## Load a country's places. arm_event refuses without it. ARGS=CO
	$(PROD_MANAGE) load_gazetteer $(ARGS)

prod.ps: ## Container status on the server
	$(DC) ps

prod.logs: ## Follow the API log (make prod.logs PROD_SERVICE=db)
	$(DC) logs -f $(PROD_SERVICE)

prod.shell: ## Django shell on the server
	$(PROD_MANAGE) shell

prod.migrate: ## Apply migrations on the server
	$(PROD_MANAGE) migrate

prod.seed: ## Load the fixtures into the deployment
	$(PROD_MANAGE) seed $(ARGS)

prod.unseed: ## Delete the seed datasets from the deployment
	$(PROD_MANAGE) seed --clear $(ARGS)

prod.events: ## Events, harvest permission and spend against the global ceiling
	$(PROD_MANAGE) events $(ARGS)

prod.watch: ## Poll USGS on the server. Costs nothing and scrapes nothing
	$(PROD_MANAGE) watch_events --list $(ARGS)

prod.arm: ## Authorise an event to be harvested. ARGS="<event_id>"
	$(PROD_MANAGE) arm_event $(ARGS)

prod.harvest: ## Run one round of pending jobs. Spends Apify. ARGS="--limit 3 --yes"
	$(PROD_MANAGE) harvest $(ARGS)

prod.pipeline: ## Read pending posts. Spends OpenAI. ARGS="1 --limit 50 --yes"
	$(PROD_MANAGE) run_pipeline $(ARGS)

prod.link: ## Attach stored locations to their municipality. ARGS="--dry-run"
	$(PROD_MANAGE) link_locations $(ARGS)

prod.graph: ## Recompute matches and the stored graph. ARGS="--event 1 --force"
	$(PROD_MANAGE) build_graph $(ARGS)

prod.media: ## Download harvested images on the server
	$(PROD_MANAGE) download_media $(ARGS)

prod.report: ## Compare what each harvesting route produced. ARGS="--event 1"
	$(PROD_MANAGE) harvest_report $(ARGS)

prod.narrate: ## Follow the deployment in prose, for the demo screen
	$(PROD_MANAGE) narrate $(ARGS)

prod.tick: ## Beat the loop once, now. Beat waits a full TICK_SECONDS before its first
	$(PROD_MANAGE) tick

prod.workers: ## Start the perpetual loop. Bounded by the global spend ceiling
	$(DC) --profile workers up -d worker harvest-worker beat

prod.workers-down: ## Stop the perpetual loop
	$(DC) --profile workers stop worker harvest-worker beat

prod.ceiling: ## Set the global Apify ceiling and restart the workers. USD=10
	$(REMOTE) $(PROD_ROOT)/ceiling.sh $(USD)
