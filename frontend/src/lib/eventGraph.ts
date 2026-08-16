/**
 * Del payload del backend a lo que pintan las vistas.
 *
 * Funciones puras, sin React: el grafo llega en snake_case, con ids numéricos y
 * cantidades sin formatear, y aquí sale ya como puntos, conexiones, ciudades y
 * posiciones. Los componentes no saben que existe una API.
 */
import type { Connection, GraphPoint } from '@/types/connection'
import type { EventGraph, GraphNode, GraphRequirement } from '@/types/graph'
import type { City, HelpKind, Place, PlaceRequirement } from '@/types/place'

const HELP_KIND: Record<string, HelpKind> = { needs: 'needed', offers: 'offered' }

/** Sin unidad administrativa el punto sigue siendo válido; solo no tiene dónde agruparse. */
const UNKNOWN_CITY = 'Sin ubicación'

/** Zona segura del viewBox: fuera de ella el `slice` del SVG recorta los puntos. */
const LAYOUT_BOX = { minX: 120, maxX: 880, minY: 120, maxY: 520 }

/**
 * Precisiones que se pueden pintar como un punto concreto.
 *
 * Un `country` es el centroide de Colombia y un `admin_1` el de un
 * departamento: clavar ahí un marcador afirma un sitio que nadie reportó. De
 * `admin_2` (municipio) hacia abajo el punto ya significa algo.
 */
const PIN_PRECISIONS = new Set(['admin_2', 'admin_3', 'neighborhood', 'street_address', 'exact_point'])

/** Cantidad pendiente en una línea, o null cuando el reporte nunca dijo cuánto. */
function formatOutstanding(requirement: GraphRequirement): string | null {
    if (requirement.outstanding === null) {
        return null
    }
    const amount = Number.isInteger(requirement.outstanding)
        ? String(requirement.outstanding)
        : requirement.outstanding.toFixed(1)
    return requirement.unit === '' ? amount : `${amount} ${requirement.unit}`
}

function toRequirement(requirement: GraphRequirement): PlaceRequirement {
    return {
        id: String(requirement.id),
        kind: HELP_KIND[requirement.direction] ?? 'needed',
        resource: requirement.resource,
        detail: requirement.free_text,
        urgency: requirement.urgency,
        outstanding: formatOutstanding(requirement)
    }
}

/**
 * Titular del punto. El backend no manda ninguno, así que se arma con lo que
 * el actor pide u ofrece, que es justo lo que distingue un punto de otro.
 */
function buildTitle(name: string, requirements: PlaceRequirement[]): string {
    const first = requirements[0]
    if (first === undefined) {
        return name
    }
    const verb = first.kind === 'needed' ? 'Necesita' : 'Ofrece'
    const amount = first.outstanding === null ? '' : `${first.outstanding} de `
    const rest = requirements.length > 1 ? ` y ${requirements.length - 1} más` : ''
    return `${verb} ${amount}${first.resource.toLowerCase()}${rest}`
}

/**
 * Un actor ubicable y con algo abierto es un punto del mapa.
 *
 * Devuelve null en tres casos. Sin coordenadas no hay marcador que pintar y no
 * hay nada sensato que inventar. Con una ubicación demasiado gruesa el marcador
 * mentiría sobre dónde está el reporte. Y sin necesidades ni ofertas abiertas no
 * hay nada que contar: pintarlo obligaría a elegirle un color —rojo o verde— que
 * afirmaría algo que el actor no está diciendo.
 */
function toPlace(node: GraphNode): Place | null {
    const first = node.requirements[0]
    if (node.location === null || first === undefined) {
        return null
    }
    if (node.precision === null || !PIN_PRECISIONS.has(node.precision)) {
        return null
    }
    const requirements = node.requirements.map(toRequirement)
    // El color lo manda lo primero que reporta: casi siempre solo pide o solo ofrece.
    const kind = HELP_KIND[first.direction] ?? 'needed'
    return {
        id: String(node.id),
        name: node.name,
        city: node.admin_unit ?? UNKNOWN_CITY,
        title: buildTitle(node.name, requirements),
        kind,
        position: [node.location.lat, node.location.lon],
        requirements
    }
}

export function toPlaces(graph: EventGraph): Place[] {
    return graph.nodes.map(toPlace).filter((place): place is Place => place !== null)
}

/**
 * Aristas del backend a hilos del grafo.
 *
 * Se descartan las que apuntan a un actor sin ubicación: el hilo no tendría de
 * dónde salir ni a dónde llegar.
 */
export function toConnections(graph: EventGraph, places: Place[]): Connection[] {
    const known = new Set(places.map((place) => place.id))
    return graph.edges
        .map((edge) => ({
            id: String(edge.id),
            neededId: String(edge.to_actor),
            offeredId: String(edge.from_actor),
            strength: edge.score
        }))
        .filter((connection) => known.has(connection.neededId) && known.has(connection.offeredId))
}

/**
 * Ciudades del menú del header: una por unidad administrativa, en su centroide
 * y ordenadas por cuántos puntos agrupan. Es el orden útil — arriba queda donde
 * está pasando algo.
 */
export function toCities(places: Place[]): City[] {
    const groups = new Map<string, Place[]>()
    for (const place of places) {
        if (place.city === UNKNOWN_CITY) {
            continue
        }
        const group = groups.get(place.city)
        if (group === undefined) {
            groups.set(place.city, [place])
        } else {
            group.push(place)
        }
    }

    return [...groups.entries()]
        .sort(([nameA, a], [nameB, b]) => b.length - a.length || nameA.localeCompare(nameB))
        .map(([name, group]) => ({
            id: name.toLowerCase().replace(/\s+/g, '-'),
            name,
            department: `${group.length} ${group.length === 1 ? 'punto' : 'puntos'}`,
            position: [
                group.reduce((sum, place) => sum + place.position[0], 0) / group.length,
                group.reduce((sum, place) => sum + place.position[1], 0) / group.length
            ] as [number, number]
        }))
}

/**
 * Posición de reposo de cada punto en el grafo de Conexiones.
 *
 * Se proyecta la geografía sobre el viewBox en vez de repartir los puntos en
 * círculo: así los cúmulos del grafo son los cúmulos del mapa y las dos
 * pestañas cuentan la misma historia. Con un solo punto (o todos en la misma
 * coordenada) el rango es cero, y entonces va al centro.
 */
export function toGraphLayout(places: Place[]): Record<string, GraphPoint> {
    if (places.length === 0) {
        return {}
    }
    const lats = places.map((place) => place.position[0])
    const lons = places.map((place) => place.position[1])
    const minLat = Math.min(...lats)
    const maxLat = Math.max(...lats)
    const minLon = Math.min(...lons)
    const maxLon = Math.max(...lons)

    const project = (value: number, min: number, max: number, from: number, to: number) =>
        max === min ? (from + to) / 2 : from + ((value - min) / (max - min)) * (to - from)

    const layout: Record<string, GraphPoint> = {}
    for (const place of places) {
        layout[place.id] = {
            x: project(place.position[1], minLon, maxLon, LAYOUT_BOX.minX, LAYOUT_BOX.maxX),
            // El eje y del SVG crece hacia abajo y la latitud hacia arriba: se invierte.
            y: project(place.position[0], maxLat, minLat, LAYOUT_BOX.minY, LAYOUT_BOX.maxY)
        }
    }
    return spreadCollisions(layout)
}

/** Radio del corrillo con el que se separan los puntos que caen en la misma celda. */
const CLUSTER_RADIUS = 30

/**
 * Separa los puntos que la proyección deja unos encima de otros.
 *
 * Dos actores del mismo barrio distan metros, y a escala de un país eso es el
 * mismo píxel: sin esto un cúmulo urbano es un solo punto con varias etiquetas
 * pisadas. Se reparten en corrillo alrededor de donde caían, con ángulo áureo
 * para que ni siquiera con muchos queden alineados. Determinista: la misma
 * entrada da siempre el mismo dibujo.
 */
function spreadCollisions(layout: Record<string, GraphPoint>): Record<string, GraphPoint> {
    const cells = new Map<string, string[]>()
    for (const [id, point] of Object.entries(layout)) {
        const key = `${Math.round(point.x / CLUSTER_RADIUS)}:${Math.round(point.y / CLUSTER_RADIUS)}`
        cells.set(key, [...(cells.get(key) ?? []), id])
    }

    const spread: Record<string, GraphPoint> = {}
    for (const ids of cells.values()) {
        const first = layout[ids[0]]
        ids.forEach((id, index) => {
            if (ids.length === 1) {
                spread[id] = layout[id]
                return
            }
            const angle = index * 2.39996
            const radius = CLUSTER_RADIUS * (0.6 + index / ids.length)
            spread[id] = {
                x: first.x + Math.cos(angle) * radius,
                y: first.y + Math.sin(angle) * radius
            }
        })
    }
    return spread
}

/** Minúsculas y sin tildes: 'Quibdó' y 'quibdo' tienen que coincidir. */
function normalize(text: string): string {
    return text
        .toLowerCase()
        .normalize('NFD')
        .replace(/\p{Diacritic}/gu, '')
}

/** Palabras que identifican un punto sin ambigüedad: recurso, ubicación y nombre. */
function keywords(place: Place): string[] {
    return splitWords([
        place.city,
        place.name,
        ...place.requirements.map((requirement) => requirement.resource)
    ])
}

/** Si un texto ya normalizado contiene alguna de esas palabras. */
function mentions(haystack: string, words: string[]): boolean {
    return words.some((word) => haystack.includes(word))
}

/** Palabras del reporte original: menos precisas, pero es como habla la gente. */
function detailWords(place: Place): string[] {
    return splitWords(place.requirements.map((requirement) => requirement.detail))
}

function splitWords(terms: string[]): string[] {
    return terms
        .flatMap((term) => normalize(term).split(/[^\p{Letter}\p{Number}]+/u))
        .filter((word) => word.length > 3)
}

/**
 * A qué punto llevar la cámara con un texto suelto, sin más pistas.
 *
 * Es el último recurso de `findPlacesForAnswer`: se usa cuando el turno no señaló ningún id
 * —el agente contestó de memoria, o el servidor no llegó a responder—. Se compara palabra a
 * palabra, no la frase entera: "puedo llevar agua" tiene que reconocer un recurso que en el
 * backend se llama "Agua potable". Ante un empate gana quien necesita ayuda — quien ofrece
 * quiere ver dónde falta, no quién más está ofreciendo.
 */
export function findPlaceForText(text: string, places: Place[]): Place | null {
    const haystack = normalize(text)
    const pick = (candidates: Place[]) =>
        candidates.find((place) => place.kind === 'needed') ?? candidates[0] ?? null

    const byKeyword = places.filter((place) => mentions(haystack, keywords(place)))
    if (byKeyword.length > 0) {
        return pick(byKeyword)
    }
    // Segundo intento sobre el texto del reporte: "palas" o "incendio" salen ahí,
    // no en el nombre del recurso. Menos preciso, pero mover el mapa a un punto
    // parecido informa más que no moverlo.
    return pick(places.filter((place) => mentions(haystack, detailWords(place))))
}

/**
 * Tope de puntos que se encuadran a la vez. Pasado eso el encuadre es el país entero, que no
 * señala nada; se conservan los primeros, que es como los ordenó la herramienta.
 */
const MAX_FOCUS_PLACES = 8

interface AnswerFocus {
    /** Ids de actor que tocaron las herramientas, en orden de relevancia. */
    actorIds: string[]
    /** Ids de necesidad u oferta; llegan de `propose_match` y `check_coverage`. */
    requirementIds: string[]
    /** La respuesta del agente, para decidir de cuál de los candidatos está hablando. */
    text: string
    /** Lo que se preguntó; solo se mira si no queda nada más. */
    question: string
    places: Place[]
}

/** Los puntos que nombran esos ids, sin repetir y respetando el orden de llegada. */
function resolveIds(actorIds: string[], requirementIds: string[], places: Place[]): Place[] {
    const byActor = new Map(places.map((place) => [place.id, place]))
    const byRequirement = new Map<string, Place>()
    for (const place of places) {
        for (const requirement of place.requirements) {
            byRequirement.set(requirement.id, place)
        }
    }

    const found: Place[] = []
    const ids = [
        ...actorIds.map((id) => byActor.get(id)),
        ...requirementIds.map((id) => byRequirement.get(id))
    ]
    for (const place of ids) {
        // Un id sin punto es lo normal: el actor puede no tener coordenadas, tenerlas
        // demasiado gruesas o no tener nada abierto, y entonces el mapa no lo dibuja.
        if (place !== undefined && !found.includes(place)) {
            found.push(place)
        }
    }
    return found
}

/**
 * Qué puntos encuadrar cuando el agente termina de contestar.
 *
 * Los ids del flujo dicen qué miró el agente, que no es lo mismo que de qué está hablando:
 * una consulta sobre doce candidatos devuelve doce y luego escribe una frase sobre dos. Así
 * que primero se resuelven los ids —eso ya acota a lo que existe en el mapa— y después se
 * mira la prosa para quedarse con los que nombra, primero por nombre propio y, si ninguno
 * aparece, por ciudad o recurso. Si la respuesta no nombra a ninguno vale el conjunto
 * entero: resumió sin señalar, y enseñar todo lo que miró es fiel a eso.
 *
 * Sin ids resueltos queda el respaldo por texto, que es lo que hacía esto antes de que el
 * backend los mandara. Devolver una lista vacía significa no mover la cámara: inventarse un
 * destino es peor que quedarse quieto.
 */
export function findPlacesForAnswer({
    actorIds,
    requirementIds,
    text,
    question,
    places
}: AnswerFocus): Place[] {
    const found = resolveIds(actorIds, requirementIds, places)
    if (found.length === 0) {
        const guess = findPlaceForText(text, places) ?? findPlaceForText(question, places)
        return guess === null ? [] : [guess]
    }
    if (found.length === 1) {
        return found
    }

    const haystack = normalize(text)
    const byName = found.filter((place) => mentions(haystack, splitWords([place.name])))
    if (byName.length > 0) {
        return byName.slice(0, MAX_FOCUS_PLACES)
    }
    const byKeyword = found.filter((place) => mentions(haystack, keywords(place)))
    return (byKeyword.length > 0 ? byKeyword : found).slice(0, MAX_FOCUS_PLACES)
}
