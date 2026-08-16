import { useCallback, useEffect, useMemo, useState } from 'react'
import { fetchEventGraph } from '@/lib/apiClient'
import { toCities, toConnections, toGraphLayout, toPlaces } from '@/lib/eventGraph'
import type { EventGraph } from '@/types/graph'

type Status = 'loading' | 'ready' | 'error'

/**
 * Carga el grafo de una emergencia y lo traduce a lo que pintan las vistas.
 *
 * Una sola carga alimenta el mapa, el grafo y el panel: dos peticiones darían
 * dos verdades sobre lo mismo. Cambiar de evento vuelve a pedirlo entero, que
 * es exactamente lo que significa cambiar de emergencia.
 *
 * Note: el backend lo recalcula en cada petición, así que refrescar es volver a
 *     llamar; `reload` existe para reintentar cuando la red falla.
 */
export function useEventGraph(eventId: number | null) {
    const [graph, setGraph] = useState<EventGraph | null>(null)
    const [status, setStatus] = useState<Status>('loading')
    const [error, setError] = useState('')

    // `attempt` es lo que dispara la carga: subirlo es reintentar.
    const [attempt, setAttempt] = useState(0)
    const reload = useCallback(() => setAttempt((previous) => previous + 1), [])

    useEffect(() => {
        if (eventId === null) {
            return
        }

        const controller = new AbortController()
        setStatus('loading')

        async function load(id: number) {
            try {
                const loaded = await fetchEventGraph(id)
                if (controller.signal.aborted) {
                    return
                }
                setGraph(loaded)
                setStatus('ready')
            } catch (failure) {
                if (controller.signal.aborted) {
                    return
                }
                setError(failure instanceof Error ? failure.message : 'Error desconocido')
                setStatus('error')
            }
        }

        void load(eventId)
        return () => controller.abort()
    }, [eventId, attempt])

    // Derivados del payload: se recalculan solo cuando llega uno nuevo.
    const places = useMemo(() => (graph === null ? [] : toPlaces(graph)), [graph])
    const connections = useMemo(() => (graph === null ? [] : toConnections(graph, places)), [graph, places])
    const cities = useMemo(() => toCities(places), [places])
    const layout = useMemo(() => toGraphLayout(places), [places])

    return { places, connections, cities, layout, status, error, reload }
}
