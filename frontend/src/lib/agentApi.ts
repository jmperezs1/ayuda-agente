/**
 * El protocolo del agente: una petición POST que se lee como un flujo de
 * eventos. Sin React y sin estado — quien llama decide qué pintar con cada
 * evento.
 *
 * `EventSource` no sabe hacer POST, así que se usa `fetch` y se lee el cuerpo a
 * mano. Tampoco se usa `apiClient`: axios en el navegador acumula la respuesta
 * entera antes de entregarla (adiós al streaming) y su timeout de 10 s cortaría
 * un turno que legítimamente tarda 40. Del cliente base solo se toma el origen,
 * que sigue viviendo en un único sitio.
 */
import { API_BASE_URL, API_HEADERS } from '@/lib/apiClient'
import type { GeoPoint } from '@/types/graph'

/** Los dos agentes que expone el backend. */
export type AgentName = 'coordination' | 'frontier'

/** Resumen de lo que devolvió una herramienta: nunca las filas, solo el recuento. */
interface ToolResult {
    ok: boolean
    count?: number
    truncated?: boolean
    [key: string]: unknown
}

/** Un evento del flujo. `type` discrimina: cada variante trae lo suyo y nada más. */
export type AgentEvent =
    | { type: 'start'; thread_id: string }
    | { type: 'tool_start'; name: string; args: Record<string, unknown>; node: string }
    | { type: 'tool_end'; name: string; result: ToolResult }
    /**
     * Las filas que tocó una herramienta, con los mismos ids que dibuja
     * `GET /events/<id>/graph/`: `actors` contra los nodos y `requirements` contra sus
     * necesidades. Es lo que el agente *miró*, no lo que concluyó — cuál de ellos es el
     * de la frase lo decide quien lo consume.
     */
    | { type: 'focus'; name: string; actors: number[]; requirements: number[] }
    | { type: 'token'; text: string }
    | { type: 'done'; thread_id: string }
    | { type: 'error'; error: string }

/**
 * Nombre de herramienta → frase que se le enseña al usuario. Los `args` nunca
 * se pintan: son ids y slugs internos.
 */
const TOOL_LABELS: Record<string, string> = {
    match_resource: 'Buscando el otro lado del recurso',
    check_coverage: 'Comprobando si ya va ayuda en camino',
    find_gaps: 'Buscando lo que nadie está cubriendo',
    get_balance: 'Comparando oferta y demanda',
    get_actor_contacts: 'Buscando cómo contactarles',
    road_distance: 'Midiendo el trayecto real',
    propose_match: 'Proponiendo un emparejamiento',
    draft_outreach: 'Redactando un mensaje',
    get_frontier: 'Revisando dónde mirar',
    create_harvest_job: 'Encolando una búsqueda'
}

/** Frase del paso en curso; el nombre crudo si la herramienta es nueva. */
export function toolLabel(name: string): string {
    return TOOL_LABELS[name] ?? name
}

interface AskAgentOptions {
    agent: AgentName
    eventId: number
    message: string
    /** Omitir en el primer turno: el evento `start` trae el que hay que reutilizar. */
    threadId: string | null
    /**
     * Dónde está quien pregunta, si el navegador lo dio. Null se omite del
     * cuerpo: el backend distingue "no la compartió" de una posición inválida.
     */
    location?: GeoPoint | null
    onEvent: (event: AgentEvent) => void
    signal?: AbortSignal
}

/** Mensaje de error de una respuesta que falló antes de abrir el flujo. */
async function readErrorMessage(response: Response): Promise<string> {
    // Un 404 devuelve la página de Django, no JSON: parsear a ciegas escondería el fallo real.
    try {
        const body: unknown = await response.json()
        if (typeof body === 'object' && body !== null && 'error' in body) {
            return String((body as { error: unknown }).error)
        }
    } catch {
        /* cuerpo no-JSON */
    }
    return `El servidor respondió ${response.status}.`
}

/**
 * Ejecuta un turno de conversación y entrega sus eventos según llegan.
 *
 * Resuelve cuando el flujo termina. Todo fallo posterior a la apertura del
 * flujo llega como un evento `error`, no como una excepción: el estado 200 ya
 * se envió y los tokens recibidos hasta ahí siguen siendo válidos.
 *
 * @throws Error Si la petición falla antes de abrir el flujo (400, 404, 405, 503).
 */
export async function askAgent({
    agent,
    eventId,
    message,
    threadId,
    location,
    onEvent,
    signal
}: AskAgentOptions): Promise<void> {
    const response = await fetch(`${API_BASE_URL}/api/agent/${agent}/`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json', ...API_HEADERS },
        // `JSON.stringify` descarta las claves `undefined`, así que un turno sin
        // hilo o sin ubicación manda el cuerpo sin ellas en vez de con nulls.
        body: JSON.stringify({
            event_id: eventId,
            message,
            thread_id: threadId ?? undefined,
            location: location ?? undefined
        }),
        signal
    })

    if (!response.ok || response.body === null) {
        throw new Error(await readErrorMessage(response))
    }

    const reader = response.body.getReader()
    const decoder = new TextDecoder()
    // Un trozo de red puede partir un evento por la mitad: se acumula y solo se
    // parsea lo que ya está cerrado por una línea en blanco.
    let buffer = ''

    while (true) {
        const { value, done } = await reader.read()
        if (done) {
            break
        }
        buffer += decoder.decode(value, { stream: true })

        const parts = buffer.split('\n\n')
        buffer = parts.pop() ?? ''
        for (const part of parts) {
            const line = part.trim()
            if (!line.startsWith('data: ')) {
                continue
            }
            const event = JSON.parse(line.slice(6)) as AgentEvent
            onEvent(event)
            // `done` y `error` son terminales: el turno acabó, no hay nada más que
            // esperar. Sin esto una conexión que el servidor no cierra —o que un
            // proxy mantiene viva— deja la barra bloqueada para siempre.
            if (event.type === 'done' || event.type === 'error') {
                await reader.cancel()
                return
            }
        }
    }
}
