import { useCallback } from 'react'
import { ChatBar } from '@/components/ChatBar'
import { findPlacesForAnswer } from '@/lib/eventGraph'
import { useAgentChat } from '@/lib/useAgentChat'
import type { AgentAnswer } from '@/lib/useAgentChat'
import type { Place } from '@/types/place'

const OFFLINE_REPLY = 'Aun así intento llevarte al punto del mapa que encaja con lo que escribiste.'

interface MapAssistantProps {
    eventId: number | null
    /** Por qué el chat no puede enviar (grafo cargando o caído); undefined si todo va bien. */
    blockedReason?: string
    places: Place[]
    /** Reporta los puntos señalados; App decide volar hacia ellos y abrir el detalle. */
    onMatch: (places: Place[]) => void
}

/**
 * El chat de la pestaña Mapa. La respuesta la escribe el agente de coordinación del backend
 * y el encuadre sale de esa misma respuesta: los eventos `focus` del flujo traen los ids de
 * los actores que sus herramientas tocaron, y `findPlacesForAnswer` decide de cuáles está
 * hablando. Sigue la regla del README: reporta los puntos encontrados pero no los almacena
 * ni conoce Leaflet — App conecta ambos.
 */
export function MapAssistant({ eventId, blockedReason, places, onMatch }: MapAssistantProps) {
    // Se vuela al terminar el turno, no al enviar: hasta que el agente no contesta no se
    // sabe de qué punto habla, y moverse antes es adivinar. La espera la narran los pasos
    // de herramienta que ya enseña el ChatBar.
    const flyToAnswer = useCallback(
        (answer: AgentAnswer) => onMatch(findPlacesForAnswer({ ...answer, places })),
        [onMatch, places]
    )

    const { messages, draft, setDraft, send, isStreaming } = useAgentChat({
        eventId,
        onAnswer: flyToAnswer,
        offlineReply: OFFLINE_REPLY
    })

    return (
        <ChatBar
            messages={messages}
            draft={draft}
            onDraftChange={setDraft}
            onSend={() => void send()}
            isStreaming={isStreaming}
            blockedReason={blockedReason}
        />
    )
}
