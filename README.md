# AyudAgente

**Un agente autónomo para las primeras 72 horas de un desastre.** Vigila las emergencias, decide
dónde mirar, lee lo que la gente publica y lo convierte en un mapa de quién necesita qué y quién lo
ofrece — y después le pasa a un coordinador un enlace para conectar a los dos.

Este monorepo tiene las dos mitades:

| Carpeta                            | Qué es                     | Stack                                                    |
| ---------------------------------- | -------------------------- | -------------------------------------------------------- |
| [`backend/`](backend/README.md)   | la API y el pipeline       | Django 5 · PostgreSQL 16 (PostGIS + pgvector) · Celery · OpenAI · Apify |
| [`frontend/`](frontend/README.md) | el mapa y el chat          | React 19 · Vite · TypeScript · Tailwind CSS v4 · react-leaflet |

Todo lo que se ve en el mapa sale del backend: una sola llamada a `GET /api/events/<id>/graph/` trae
los actores de la emergencia, lo que piden u ofrecen y los emparejamientos entre ellos.

---

## Ejecución

Cada proyecto se ejecuta por separado, con su propio README como referencia completa. Lo mínimo para
verlo funcionando de punta a punta, en dos terminales:

### 1. Backend — [`backend/README.md`](backend/README.md)

Hace falta [uv](https://docs.astral.sh/uv/), Docker y las librerías GEOS/GDAL/PROJ contra las que
enlaza `django.contrib.gis` (`sudo dnf install gdal geos proj`, o el equivalente de tu sistema).

```bash
cd backend
make init                                 # .venv, dependencias, .env a partir de .env.example
make up                                   # Postgres con PostGIS + pgvector, y Redis
make migrate

make taxonomy                             # el catálogo de recursos
make gazetteer ARGS=CO                    # los lugares de Colombia, desde GeoNames
make seed                                 # las 939 publicaciones del terremoto del Chocó

make pipeline ARGS="1 --limit 25 --yes"   # léelas: una llamada multimodal por publicación (gasta OpenAI)
make graph ARGS="--event 1"               # empareja necesidades con ofertas y redibuja el mapa

make apikey                               # mintea la clave que usará el frontend
make run                                  # http://127.0.0.1:8000
```

`OPENAI_API_KEY` va en `backend/.env` — el pipeline lee las publicaciones con ella. `APIFY_TOKEN`
solo hace falta para cosechar publicaciones nuevas. `make help` lista todos los comandos.

### 2. Frontend — [`frontend/README.md`](frontend/README.md)

Hace falta Node 20 o superior.

```bash
cd frontend
npm install
```

Escribe `frontend/.env` con el backend local y la clave que devolvió `make apikey`:

```bash
VITE_API_BASE_URL=http://127.0.0.1:8000
VITE_API_KEY=…
```

```bash
npm run dev                               # http://localhost:5173
```

Sin `.env`, el frontend apunta al backend desplegado (`https://api.ayudagente.help`) y arranca solo:
si únicamente quieres ver la interfaz, este es el camino corto.

---

## Procedencia

Las dos mitades se desarrollaron por separado durante el hackathon y se reunieron aquí. Su historial
completo, commit a commit, sigue en sus repositorios de origen:

- `backend/` — [djimenezm2/back-hackaton-CTW-2026](https://github.com/djimenezm2/back-hackaton-CTW-2026)
- `frontend/` — [juanse-ai/ayuda-agente](https://github.com/juanse-ai/ayuda-agente)

Este monorepo es la copia que se lee y se ejecuta; los cambios de aquí en adelante van aquí.

## Documentación

La decisiones de arquitectura, la estrategia de búsqueda y los runbooks viven en el backend:
[`backend/CLAUDE.md`](backend/CLAUDE.md), [`backend/HANDBOOK.md`](backend/HANDBOOK.md) y
[`backend/docs/`](backend/docs/) — con el contrato de la API en
[`backend/docs/api.md`](backend/docs/api.md) y los endpoints del agente en
[`backend/docs/agent-api.md`](backend/docs/agent-api.md).
