import { useCallback, useMemo, useState } from 'react'
import { ChatBar } from '@/components/ChatBar'
import { ConnectionsGraph } from '@/components/connections/ConnectionsGraph'
import { HelpLegend } from '@/components/HelpLegend'
import { SidePanel } from '@/components/SidePanel'
import { findPlacesForAnswer } from '@/lib/eventGraph'
import { useAgentChat } from '@/lib/useAgentChat'
import type { AgentAnswer } from '@/lib/useAgentChat'
import type { Connection, GraphPoint } from '@/types/connection'
import type { Place } from '@/types/place'

/**
 * Un solo clic resalta el punto y sus emparejados y abre su panel a la vez.
 * `highlighted` existe para después de cerrar el panel: el resaltado se
 * conserva hasta que un clic en el fondo lo despeja. La unión discriminada
 * hace imposible "panel abierto sin punto": no hay estados a medias.
 */
type Selection =
    { status: 'idle' } | { status: 'highlighted'; placeId: string } | { status: 'panel'; placeId: string }

const OFFLINE_REPLY = 'Aun así intento señalarte en el grafo el punto que encaja con lo que escribiste.'

/**
 * Hueco que necesita un punto para leerse como un punto, en unidades del
 * viewBox: el doble de lo que mide su halo (r = 13). Con vecinos más cerca que
 * esto los círculos se funden en una mancha y los hilos, que salen por debajo
 * de ellos, quedan escondidos.
 */
const NODE_SPACING = 52

/**
 * Tope de la separación. No es estético: la cámara solo se aleja hasta 0.35, y
 * estirando más de esto el botón "ver todo el grafo" ya no cabría el dibujo
 * entero en pantalla.
 */
const MAX_SPREAD = 2.5

/**
 * Separación típica del grafo: la mediana de lo que dista cada punto de su
 * vecino más cercano.
 *
 * Mediana y no mínimo ni media porque el dibujo llega en cúmulos —los puntos de
 * una misma ciudad caen juntos— y lo que hay que medir es cómo de apretado está
 * el caso corriente, no el par más pegado ni el satélite suelto que inflaría el
 * promedio.
 */
function typicalGap(points: GraphPoint[]): number {
    const gaps = points.map((point) => {
        let nearest = Infinity
        for (const other of points) {
            if (other !== point) {
                nearest = Math.min(nearest, Math.hypot(other.x - point.x, other.y - point.y))
            }
        }
        return nearest
    })
    return gaps.sort((a, b) => a - b)[Math.floor(gaps.length / 2)]
}

/**
 * Estira las posiciones de reposo hasta que cada punto tenga sitio propio.
 *
 * Las posiciones llegan proyectadas sobre un rectángulo fijo del viewBox, así
 * que cuantos más puntos trae el evento, más juntos caen: pasado cierto número
 * el grafo es una masa de círculos pisados sin hilos visibles entre ellos. Aquí
 * se escala ese mismo dibujo alrededor de su centro —misma forma, mismos
 * cúmulos, las mismas conexiones entre los mismos puntos— hasta que la
 * separación típica llega a NODE_SPACING. Al no tocar el tamaño del punto,
 * estirar es exactamente lo mismo que encogerlo: lo que cambia es cuánto ocupa
 * cada uno del dibujo, y entre uno y otro aparece el hilo que los une.
 *
 * El factor nunca encoge (un evento de cuatro puntos ya se ve bien) y el lienzo
 * no tiene topes de paneo: el grafo simplemente sigue más allá del primer
 * encuadre, que es como está pensado recorrerlo.
 */
function spaceOut(layout: Record<string, GraphPoint>): Record<string, GraphPoint> {
    const points = Object.values(layout)
    if (points.length < 2) {
        return layout
    }
    const gap = typicalGap(points)
    // Con todos los puntos en la misma coordenada no hay nada que estirar:
    // multiplicar una distancia de cero la deja en cero.
    if (gap === 0 || gap >= NODE_SPACING) {
        return layout
    }

    const factor = Math.min(MAX_SPREAD, NODE_SPACING / gap)
    const xs = points.map((point) => point.x)
    const ys = points.map((point) => point.y)
    const centerX = (Math.min(...xs) + Math.max(...xs)) / 2
    const centerY = (Math.min(...ys) + Math.max(...ys)) / 2

    return Object.fromEntries(
        Object.entries(layout).map(([id, point]) => [
            id,
            {
                x: centerX + (point.x - centerX) * factor,
                y: centerY + (point.y - centerY) * factor
            }
        ])
    )
}

interface ConnectionsViewProps {
    eventId: number | null
    /** Por qué el chat no puede enviar (grafo cargando o caído); undefined si todo va bien. */
    blockedReason?: string
    places: Place[]
    connections: Connection[]
    layout: Record<string, GraphPoint>
}

/**
 * La vista Conexiones completa: grafo + leyenda + chat + panel lateral.
 * hola
 * Su estado (selección, spotlight, conversación) vive aquí, no en App: la vista
 * se monta al activar la pestaña y se desmonta al salir, así que arranca de cero
 * cada visita (la animación de entrada del grafo se repite a propósito). Los
 * datos, en cambio, bajan de App: son los mismos que pinta el mapa.
 */
export function ConnectionsView({
    eventId,
    blockedReason,
    places,
    connections,
    layout
}: ConnectionsViewProps) {
    const [selection, setSelection] = useState<Selection>({ status: 'idle' })
    // El spotlight (el vuelo de cámara hacia lo señalado) solo lo activa el chat;
    // cualquier clic manual lo apaga: la manipulación directa manda sobre la
    // guiada. Apagarlo no devuelve la cámara a donde estaba — el grafo se
    // recorre a mano, y deshacer ese recorrido sería quitárselo de debajo.
    // Envoltorio nuevo por respuesta: ver ConnectionsGraph sobre su identidad.
    const [spotlight, setSpotlight] = useState<{ ids: string[] } | null>(null)

    // El encuadre del grafo es cosa de esta vista, no de los datos: el mapa
    // quiere la geografía tal cual y aquí hace falta aire entre los puntos para
    // que se distingan y se vean los hilos. Se separa una sola vez y baja ya
    // separado, que es lo que también usan el vuelo del chat y el encuadre.
    const spacedLayout = useMemo(() => spaceOut(layout), [layout])

    // Derivados en render, como en App. OJO: siempre el objeto de `places`, nunca
    // uno compuesto — el latch interno de SidePanel depende de esa identidad.
    const selectedPlace =
        selection.status === 'panel' ? (places.find((place) => place.id === selection.placeId) ?? null) : null
    const highlightedId = selection.status === 'idle' ? null : selection.placeId

    // Igual que en el mapa: se mira a dónde apuntó la respuesta, no la pregunta. Encuadre
    // sobre todo lo señalado y panel del primero, que es el que la respuesta pone por
    // delante; es un solo gesto guiado.
    const spotlightAnswer = useCallback(
        (answer: AgentAnswer) => {
            const matched = findPlacesForAnswer({ ...answer, places })
            if (matched.length === 0) {
                return
            }
            setSpotlight({ ids: matched.map((place) => place.id) })
            setSelection({ status: 'panel', placeId: matched[0].id })
        },
        [places]
    )

    const { messages, draft, setDraft, send, isStreaming } = useAgentChat({
        eventId,
        onAnswer: spotlightAnswer,
        offlineReply: OFFLINE_REPLY
    })

    const handleDotClick = (placeId: string) => {
        setSpotlight(null)
        setSelection((prev) =>
            prev.status === 'panel' && prev.placeId === placeId ? prev : { status: 'panel', placeId }
        )
    }

    const handleClearSelection = () => {
        setSpotlight(null)
        setSelection({ status: 'idle' })
    }

    // Cerrar el panel conserva el resaltado: el usuario suele querer seguir
    // mirando ese cúmulo; otro clic en el fondo ya despeja todo.
    const handleClosePanel = () => {
        setSpotlight(null)
        setSelection((prev) =>
            prev.status === 'panel' ? { status: 'highlighted', placeId: prev.placeId } : prev
        )
    }

    return (
        // Sin `isolate` a propósito: los mandos del evento viven en el header y
        // en móvil flotan sobre esta vista, así que tienen que quedar por encima
        // del grafo y por debajo del panel de detalle. Aislar esta sección los
        // dejaría a todos de un mismo lado. Los z de dentro ya cuentan con eso:
        // grafo y chat abajo (1100-1150), panel arriba (1200). Con el mapa no
        // hay conflicto: mientras esta vista está montada, aquel es `invisible`.
        <section aria-label="Conexiones" className="bg-page absolute inset-0 overflow-hidden">
            <ConnectionsGraph
                places={places}
                connections={connections}
                layout={spacedLayout}
                highlightedId={highlightedId}
                spotlight={spotlight}
                onSelectDot={handleDotClick}
                onClearSelection={handleClearSelection}
            />
            <HelpLegend showConnection />
            <ChatBar
                messages={messages}
                draft={draft}
                onDraftChange={setDraft}
                onSend={() => void send()}
                isStreaming={isStreaming}
                blockedReason={blockedReason}
            />
            <SidePanel place={selectedPlace} onClose={handleClosePanel} />
        </section>
    )
}
