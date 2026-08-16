/**
 * Cliente HTTP base de la aplicación.
 *
 * Único punto que conoce el origen de la API y la clave con la que se firma cada
 * petición. Todas las llamadas al backend deben hacerse a través de esta
 * instancia (o, para el streaming del agente, con `API_HEADERS`).
 */
import axios from 'axios'
import type { ActorDetail, EventBrief, EventDetail, EventGraph, RequirementDetail } from '@/types/graph'

// Origen del backend desplegado; se sustituye con VITE_API_BASE_URL sin tocar código.
export const API_BASE_URL = import.meta.env.VITE_API_BASE_URL ?? 'https://api.ayudagente.help'

/**
 * Todo `/api/` exige clave. Va en `VITE_API_KEY` porque no hay servidor propio
 * donde esconderla: cualquier clave que llegue al navegador es pública, así que
 * la del frontend debe ser de solo lectura y revocable, nunca la del backoffice.
 */
const API_KEY: string = import.meta.env.VITE_API_KEY ?? ''

/** Cabeceras comunes: sin clave configurada no se manda ninguna y el servidor responde 401. */
export const API_HEADERS: Record<string, string> = API_KEY === '' ? {} : { 'X-API-Key': API_KEY }

export const apiClient = axios.create({
    baseURL: API_BASE_URL,
    timeout: 10000,
    headers: API_HEADERS
})

/**
 * Las emergencias activas, de la más reciente a la más antigua.
 *
 * Es el punto de entrada de todo: sin evento no hay grafo que pedir ni nada que
 * preguntarle al agente. Una lista vacía es un error, no un estado que arrastrar.
 */
export async function fetchEvents(): Promise<EventBrief[]> {
    const { data } = await apiClient.get<{ events: EventBrief[] }>('/api/events/')
    if (data.events.length === 0) {
        throw new Error('No hay ninguna emergencia activa en el servidor.')
    }
    return data.events
}

/** Un evento con sus recuentos: actores, necesidades y ofertas abiertas, emparejamientos. */
export async function fetchEvent(eventId: number): Promise<EventDetail> {
    const { data } = await apiClient.get<EventDetail>(`/api/events/${eventId}/`)
    return data
}

/**
 * El grafo completo de un evento: actores, sus necesidades u ofertas abiertas y
 * los emparejamientos entre ellos. Una sola llamada alimenta el mapa y el grafo.
 *
 * Note: el backend lo recalcula en cada petición, así que no hay caché que invalidar.
 */
export async function fetchEventGraph(eventId: number): Promise<EventGraph> {
    const { data } = await apiClient.get<EventGraph>(`/api/events/${eventId}/graph/`, {
        // El grafo carga de un tirón y crece con el evento; 10 s se quedan cortos.
        timeout: 30000
    })
    return data
}

/**
 * Un actor con sus contactos. Se pide al abrir su punto, no antes: doscientos
 * actores con todos sus teléfonos es una carga que nadie necesita de golpe.
 *
 * Note: si el actor se fusionó en otro, el backend devuelve el superviviente y
 *     un `requested_id` con el que se pidió, en vez de un 404.
 */
export async function fetchActor(actorId: string): Promise<ActorDetail> {
    const { data } = await apiClient.get<ActorDetail>(`/api/actors/${actorId}/`)
    return data
}

/** Una necesidad u oferta con las publicaciones que la respaldan. */
export async function fetchRequirement(requirementId: string): Promise<RequirementDetail> {
    const { data } = await apiClient.get<RequirementDetail>(`/api/requirements/${requirementId}/`)
    return data
}
