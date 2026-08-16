import { useCallback, useEffect, useState } from 'react'
import { fetchEvent, fetchEvents } from '@/lib/apiClient'
import type { EventBrief, EventSummary } from '@/types/graph'

/**
 * Las emergencias activas y cuál se está mirando.
 *
 * La lista se pide una vez; los recuentos, solo del evento seleccionado. Pedir
 * el detalle de todos para pintar el desplegable serían N peticiones para un
 * dato que solo interesa del que está abierto.
 */
export function useEvents() {
    const [events, setEvents] = useState<EventBrief[]>([])
    const [selectedId, setSelectedId] = useState<number | null>(null)
    const [summary, setSummary] = useState<EventSummary | null>(null)
    const [error, setError] = useState('')

    // `attempt` dispara la carga: subirlo es reintentar.
    const [attempt, setAttempt] = useState(0)
    const reload = useCallback(() => setAttempt((previous) => previous + 1), [])

    useEffect(() => {
        const controller = new AbortController()

        async function load() {
            try {
                const loaded = await fetchEvents()
                if (controller.signal.aborted) {
                    return
                }
                setError('')
                setEvents(loaded)
                // El más reciente: el backend ya los ordena por fecha.
                setSelectedId(loaded[0].id)
            } catch (failure) {
                if (!controller.signal.aborted) {
                    setError(failure instanceof Error ? failure.message : 'Error desconocido')
                }
            }
        }

        void load()
        return () => controller.abort()
    }, [attempt])

    useEffect(() => {
        if (selectedId === null) {
            return
        }
        const controller = new AbortController()
        setSummary(null)

        // Los recuentos son un adorno del desplegable: si fallan, el resto de la
        // app sigue funcionando y no hay nada que anunciar.
        fetchEvent(selectedId)
            .then((event) => {
                if (!controller.signal.aborted) {
                    setSummary(event.summary)
                }
            })
            .catch(() => setSummary(null))

        return () => controller.abort()
    }, [selectedId])

    const select = useCallback((eventId: number) => setSelectedId(eventId), [])

    const selected = events.find((event) => event.id === selectedId) ?? null

    return { events, selected, selectedId, select, summary, error, reload }
}
