import { useCallback, useState } from 'react'
import { AppHeader, type AppTab } from '@/components/AppHeader'
import { ConnectionsView } from '@/components/connections/ConnectionsView'
import { FeedbackButton } from '@/components/FeedbackButton'
import { HelpLegend } from '@/components/HelpLegend'
import { MapAssistant } from '@/components/MapAssistant'
import { MapStage } from '@/components/MapStage'
import { SidePanel } from '@/components/SidePanel'
import { useEventGraph } from '@/lib/useEventGraph'
import { useEvents } from '@/lib/useEvents'
import { cn } from '@/lib/utils'
import type { Place } from '@/types/place'

export default function App() {
    // Qué emergencia se está mirando; todo lo demás cuelga de ella.
    const {
        events,
        selectedId: eventId,
        select,
        summary,
        error: eventsError,
        reload: reloadEvents
    } = useEvents()
    // Una sola carga alimenta las dos pestañas y el chat: mismos puntos, misma emergencia.
    const { places, connections, cities, layout, status, error, reload } = useEventGraph(eventId)

    // Dos cargas encadenadas (la lista y el grafo) con un solo estado en pantalla:
    // al usuario le da igual cuál de las dos falló, y el fallo manda sobre la espera.
    const loadError = eventsError !== '' ? eventsError : status === 'error' ? error : null
    const isLoading = loadError === null && (eventId === null || status === 'loading')

    // Sin emergencia resuelta no hay a qué preguntarle al agente, y el chat lo dice.
    const chatBlockedReason =
        loadError !== null
            ? 'Sin conexión con el servidor'
            : isLoading
              ? 'Cargando la emergencia…'
              : undefined

    const [activeTab, setActiveTab] = useState<AppTab>('mapa')
    // Dos ejes independientes: qué ciudad encuadra el mapa y qué punto está abierto.
    const [focusedCityId, setFocusedCityId] = useState<string | null>(null)
    const [selectedId, setSelectedId] = useState<string | null>(null)
    // Objetivo del chat del mapa. Envoltorio nuevo en cada respuesta a propósito:
    // su identidad es lo que dispara el vuelo en MapSpotlight, así repetir la
    // misma petición vuelve a volar aunque los puntos no cambien.
    const [spotlight, setSpotlight] = useState<{ places: Place[] } | null>(null)

    // Derivados en render — no hace falta ningún efecto para mantenerlos al día.
    const focusedCity = cities.find((city) => city.id === focusedCityId) ?? null
    const selectedPlace = places.find((place) => place.id === selectedId) ?? null

    const handleSelectPlace = useCallback((placeId: string) => setSelectedId(placeId), [])
    const handleClose = useCallback(() => setSelectedId(null), [])

    // Lo que señaló la respuesta del agente: volar hacia ello y abrir el detalle del
    // primero. Con varios, el encuadre enseña el conjunto y el panel el que la respuesta
    // pone por delante — llegan ordenados por relevancia, y dejar el panel cerrado
    // obligaría a buscar a mano de cuál de todos se estaba hablando.
    const handleChatMatch = useCallback((matched: Place[]) => {
        if (matched.length === 0) {
            return
        }
        setSpotlight({ places: matched })
        setSelectedId(matched[0].id)
    }, [])

    // Cambiar de pestaña también lo cierra, por la misma razón: al volver, un
    // panel abierto sobre un contexto viejo se lee como un error.
    const handleSelectTab = useCallback((tab: AppTab) => {
        setActiveTab(tab)
        setSelectedId(null)
    }, [])

    // Cambiar de emergencia lo reinicia todo: la nueva tiene otras ubicaciones y
    // otros puntos, y nada de lo que hubiera en pantalla se refiere a ella.
    const handleSelectEvent = useCallback(
        (nextEventId: number) => {
            select(nextEventId)
            setFocusedCityId(null)
            setSelectedId(null)
            setSpotlight(null)
        },
        [select]
    )

    const isMapa = activeTab === 'mapa'

    return (
        <div className="bg-page text-fg grid h-dvh grid-rows-[auto_1fr] font-sans">
            <AppHeader
                events={events}
                selectedEventId={eventId}
                eventSummary={summary}
                activeTab={activeTab}
                onSelectEvent={handleSelectEvent}
                onSelectTab={handleSelectTab}
            />
            <main className="relative overflow-hidden">
                {/* El mapa queda montado siempre y se oculta con visibility:
                    Leaflet mide su contenedor al montar, y desmontarlo (o un
                    display:none, que lo deja sin tamaño) perdería la cámara. */}
                <div className={cn('absolute inset-0', !isMapa && 'invisible')} inert={!isMapa}>
                    <MapStage
                        places={places}
                        eventId={eventId}
                        selectedId={selectedId}
                        focusedCity={focusedCity}
                        spotlight={spotlight}
                        onSelectPlace={handleSelectPlace}
                    />
                    {/* Sin la fila de "Conexión": en el mapa no hay hilos. */}
                    <HelpLegend />
                    {/* `key` remonta el chat al cambiar de emergencia: un
                        `thread_id` pertenece al agente de un evento, y arrastrarlo
                        al siguiente mezclaría dos conversaciones. */}
                    <MapAssistant
                        key={eventId}
                        eventId={eventId}
                        blockedReason={chatBlockedReason}
                        places={places}
                        onMatch={handleChatMatch}
                    />
                    <SidePanel place={selectedPlace} onClose={handleClose} />
                </div>
                {/* Conexiones sí se monta al activarse: su animación de entrada
                    se repite y su estado (chat, selección) arranca de cero. */}
                {activeTab === 'conexiones' ? (
                    <ConnectionsView
                        key={eventId}
                        eventId={eventId}
                        blockedReason={chatBlockedReason}
                        places={places}
                        connections={connections}
                        layout={layout}
                    />
                ) : null}
                {/* Fuera del contenedor del mapa: la llamada al feedback vale
                    para las dos pestañas y no debe quedarse `inert` con él. */}
                <FeedbackButton />
                {/* Aviso de carga o de fallo, por encima de las dos pestañas: sin
                    datos ambas están vacías y el silencio se lee como una app rota.
                    Si falló, el propio aviso es el botón de reintentar. */}
                {isLoading ? (
                    <div className="border-line bg-surface text-fg-muted pointer-events-none absolute top-4 left-1/2 z-[1250] max-w-[calc(100%-2rem)] -translate-x-1/2 rounded-full border px-4 py-2 text-center text-sm shadow-2xl shadow-black/50">
                        Cargando la emergencia…
                    </div>
                ) : null}
                {loadError !== null ? (
                    <button
                        type="button"
                        // Reintenta lo que falló: la lista de emergencias o su grafo.
                        onClick={eventsError !== '' ? reloadEvents : reload}
                        // El motivo del fallo lo escribe el servidor y puede ser
                        // largo: acotado al ancho de la pantalla envuelve en vez
                        // de desbordarla.
                        className="border-line bg-surface text-fg-muted hover:text-fg hover:border-brand/60 absolute top-4 left-1/2 z-[1250] max-w-[calc(100%-2rem)] -translate-x-1/2 rounded-full border px-4 py-2 text-center text-sm shadow-2xl shadow-black/50 transition-colors duration-200"
                    >
                        No pude cargar la emergencia: {loadError}. Reintentar →
                    </button>
                ) : null}
            </main>
        </div>
    )
}
