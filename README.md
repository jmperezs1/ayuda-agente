# AyudAgente

**Un agente autónomo para las primeras 72 horas de un desastre.** Vigila las emergencias, decide
dónde mirar, lee lo que la gente publica y lo convierte en un mapa de quién necesita qué y quién lo
ofrece — y después le pasa a un coordinador un enlace para conectar a los dos.

Este repositorio es el punto de entrada a las dos mitades. No guarda su código: las engancha como
submódulos, cada una clavada a un commit de su repositorio de origen.

| Submódulo   | Qué es               | Repositorio                                                                              | Stack                                                                  |
| ----------- | -------------------- | ---------------------------------------------------------------------------------------- | ---------------------------------------------------------------------- |
| `backend/`  | la API y el pipeline | [djimenezm2/back-hackaton-CTW-2026](https://github.com/djimenezm2/back-hackaton-CTW-2026) | Django 5 · PostgreSQL 16 (PostGIS + pgvector) · Celery · OpenAI · Apify |
| `frontend/` | el mapa y el chat    | [juanse-ai/ayuda-agente](https://github.com/juanse-ai/ayuda-agente)                       | React 19 · Vite · TypeScript · Tailwind CSS v4 · react-leaflet          |

Todo lo que se ve en el mapa sale del backend: una sola llamada a `GET /api/events/<id>/graph/` trae
los actores de la emergencia, lo que piden u ofrecen y los emparejamientos entre ellos.

**Demo en vivo: [ayudagente.help](https://ayudagente.help)** — el frontend desplegado en Vercel,
contra el backend de producción. Lo de abajo es para ejecutarlo en local.

---

## Clonar

Al ser submódulos, **un `git clone` a secas deja `backend/` y `frontend/` vacíos**. Hace falta pedir
el contenido explícitamente:

```bash
git clone --recurse-submodules https://github.com/jmperezs1/ayuda-agente.git
```

Si ya lo clonaste sin la bandera, todavía estás a tiempo:

```bash
git submodule update --init
```

Cada submódulo apunta a un commit fijo, no a la rama: es el estado con el que se presentó el
proyecto. Para traer lo último de cada repositorio, `git submodule update --remote`.

---

## Ejecución

Cada mitad se ejecuta por separado y tiene su propio README dentro del submódulo, con el detalle
completo. Lo mínimo para verlo funcionando de punta a punta, en dos terminales:

### 1. Backend — [README](https://github.com/djimenezm2/back-hackaton-CTW-2026#readme)

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
solo hace falta para cosechar publicaciones nuevas. `--limit 25` es lo que hace barato el `pipeline`;
quítalo para leer las 939.

Consultar la API con la clave que minteó `make apikey`:

```bash
curl -H "X-API-Key: <clave>" http://127.0.0.1:8000/api/events/1/requirements/
```

El resto de comandos útiles — `make help` los lista todos:

```bash
make narrate                              # ver el pipeline según ocurre (arráncalo antes que pipeline)
make superuser                            # y el admin de Django en /admin/
make unseed                               # quita las fixtures, deja taxonomía y gazetteer
make check                                # ruff + estilo de comentarios + pyrefly + pytest

make watch                                # sondea USGS: propone eventos en pausa, no gasta nada
make events                               # qué hay esperando y qué puede gastar
make arm ARGS="<id>"                      # autoriza un evento real y encola su primer barrido
make worker                               # y `make beat` en otra terminal
```

**Ojo antes de dejarlo suelto:** el evento piloto viene activo, así que un bucle en marcha lo
cosecharía de verdad. `make unseed` primero.

### 2. Frontend — [README](https://github.com/juanse-ai/ayuda-agente#readme)

**Está desplegado en Vercel: [ayudagente.help](https://ayudagente.help).** Para ver la interfaz no
hace falta levantar nada — ya habla con el backend de producción.

En local hace falta Node 20 o superior:

```bash
cd frontend
npm install
npm run dev                               # http://localhost:5173
```

Sin `.env` apunta al backend desplegado (`https://api.ayudagente.help`) y arranca solo. Para que
hable con el backend que acabas de levantar, escribe `frontend/.env`:

```bash
VITE_API_BASE_URL=http://127.0.0.1:8000
VITE_API_KEY=…                            # la que devolvió `make apikey`
```

Son las dos únicas variables. **Solo se expone al navegador lo que empieza por `VITE_`**, y
`VITE_API_KEY` viaja en la cabecera `X-API-Key` de cada petición: lo que llega al navegador es
público, así que debe ser una clave de solo lectura y revocable. Sin ella, todo `/api/` responde 401.

El resto de comandos:

```bash
npm run build                             # tsc -b && vite build
npm run preview                           # sirve el build de producción
npm run lint                              # oxlint
npm run format                            # prettier --write .
```

---

## Documentación

Las decisiones de arquitectura, la estrategia de búsqueda y los runbooks viven en el backend, y se
leen desde su repositorio o desde `backend/` una vez inicializado el submódulo:

| Documento                                                                                   | Qué cubre                                        |
| ------------------------------------------------------------------------------------------- | ------------------------------------------------ |
| [`CLAUDE.md`](https://github.com/djimenezm2/back-hackaton-CTW-2026/blob/main/CLAUDE.md)       | la arquitectura y las invariantes                |
| [`HANDBOOK.md`](https://github.com/djimenezm2/back-hackaton-CTW-2026/blob/main/HANDBOOK.md)   | la estrategia de búsqueda, con costes medidos    |
| [`docs/api.md`](https://github.com/djimenezm2/back-hackaton-CTW-2026/blob/main/docs/api.md)   | el contrato de lectura contra el que va el front |
| [`docs/agent-api.md`](https://github.com/djimenezm2/back-hackaton-CTW-2026/blob/main/docs/agent-api.md) | los endpoints del agente y su flujo de eventos   |
