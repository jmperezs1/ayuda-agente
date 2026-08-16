/**
 * La API tal y como responde: eventos, grafo, actores y necesidades.
 *
 * Copia fiel de los payloads, en snake_case y con los slugs del backend: es el
 * contrato, no el modelo de la interfaz. Las etiquetas en español viven en
 * `data/labels.ts` y la traducción a lo que pintan los componentes en
 * `lib/eventGraph.ts`.
 */

export interface GeoPoint {
    lat: number
    lon: number
}

export type Hazard = 'earthquake' | 'flood' | 'landslide' | 'cyclone' | 'wildfire' | 'windstorm' | 'other'

/** Una emergencia. Todo lo demás se pregunta sobre una de estas. */
export interface EventBrief {
    id: number
    name: string
    hazard: Hazard
    status: string
    occurred_at: string
    magnitude: number | null
    epicenter: GeoPoint | null
}

/**
 * Recuentos del evento. `needs` y `offers` cuentan lo *abierto* —lo que sigue
 * pendiente, no lo que se ha visto alguna vez— y `unread_observations` dice si
 * el pipeline va con retraso.
 */
export interface EventSummary {
    actors: number
    needs: number
    offers: number
    requirements: number
    matches: number
    observations: number
    unread_observations: number
    outreach_drafts: number
}

export interface EventDetail extends EventBrief {
    depth_km: number | null
    country_code: string
    languages: string[]
    detection_source: string
    summary: EventSummary
}

/** Un actor pide (`needs`) o entrega (`offers`). */
export type Direction = 'needs' | 'offers'

export type Urgency = 'critical' | 'high' | 'medium' | 'low'

/** Una necesidad u oferta abierta; el backend solo manda las abiertas o parciales. */
export interface GraphRequirement {
    id: number
    direction: Direction
    resource: string
    resource_key: string
    free_text: string
    urgency: Urgency
    status: string
    quantity: number | null
    covered_quantity: number | null
    /** Lo que sigue sin cubrir; null cuando nunca se dijo una cantidad. */
    outstanding: number | null
    unit: string
    destination: GeoPoint | null
    confidence: number | null
}

/** Un actor del evento: el nodo del grafo y el punto del mapa. */
export interface GraphNode {
    id: number
    name: string
    kind: string
    credibility: number | null
    verified: boolean
    location: GeoPoint | null
    precision: string | null
    admin_unit: string | null
    requirements: GraphRequirement[]
}

/** Un emparejamiento propuesto o ya en curso: la arista. */
export interface GraphEdge {
    id: number
    /** Quien ofrece. */
    from_actor: number
    /** Quien necesita. */
    to_actor: number
    resource: string
    status: string
    /** [0, 1]; lo calcula `score_pair` en el backend. */
    score: number
    distance_km: number | null
    committed_quantity: number | null
    via_transport_actor: number | null
    rationale: string
}

export interface EventGraph {
    event: { id: number; name: string; epicenter: GeoPoint | null }
    nodes: GraphNode[]
    edges: GraphEdge[]
}

export type ContactKind =
    'handle' | 'phone' | 'whatsapp' | 'email' | 'website' | 'form' | 'payment' | 'street_address'

/**
 * Una forma de llegar a un actor.
 *
 * `times_seen` es la señal de confianza que merece verse: un teléfono repetido
 * en cinco publicaciones casi seguro es real; uno que apareció una vez puede
 * ser un error de extracción, y quien va a llamar merece saber cuál mira.
 */
export interface Contact {
    id: number
    kind: ContactKind
    value: string
    raw_value: string
    platform: string
    payment_network: string
    times_seen: number
    confidence: number
    verified: boolean
    /** Si el canal sirve para algo; los pagos y las direcciones no. */
    reachable: boolean
    can_carry_a_message: boolean
    /** Menos intrusivo primero. El backend ya los manda ordenados. */
    preference_rank: number
    attempts: number
}

/** Un actor con todo lo suyo, incluido cómo contactarle. */
export interface ActorDetail {
    id: number
    name: string
    kind: string
    is_organization: boolean
    credibility: number | null
    verified: boolean
    alternate_names: string[]
    max_followers: number | null
    first_seen_at: string
    last_seen_at: string
    location: RequirementLocation | null
    contacts: Contact[]
    requirements: GraphRequirement[]
    /** Solo si el id pedido se había fusionado en otro: el que se pidió. */
    requested_id?: number
}

export interface RequirementLocation {
    point: GeoPoint | null
    precision: string | null
    text: string
    admin_unit: string | null
}

export interface Media {
    id: number
    kind: string
    /** Nuestra copia. Preferirla: las del origen caducan en horas. */
    url: string | null
    source_url: string | null
    alt_text: string
    position: number
}

export type Platform = 'x' | 'instagram' | 'facebook' | 'tiktok'

/** Una publicación como evidencia: qué dijo, quién lo dijo y dónde leerlo. */
export interface ObservationBrief {
    id: number
    platform: Platform
    permalink: string
    posted_at: string
    text: string
    transcript: string
    language: string
    author: {
        handle: string
        name: string
        avatar_url: string
        followers: number | null
        verified: boolean
    }
    metrics: { likes?: number; shares?: number; comments?: number }
    is_reply: boolean
    media: Media[]
}

/**
 * Una necesidad u oferta con las publicaciones de donde salió.
 *
 * `evidence` es una lista a propósito: una publicación puede producir varias
 * necesidades y una necesidad puede estar respaldada por varias publicaciones.
 */
export interface RequirementDetail extends GraphRequirement {
    actor: { id: number; name: string; kind: string; verified: boolean }
    location: RequirementLocation | null
    is_saturated: boolean
    last_seen_at: string
    evidence: ObservationBrief[]
    matches: unknown[]
}
