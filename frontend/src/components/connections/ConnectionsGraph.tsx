import { Maximize, Minus, Plus } from 'lucide-react'
import { useCallback, useEffect, useMemo, useRef } from 'react'
import { GRAPH_VIEWBOX, PANEL_BREAKPOINT, PANEL_WIDTH } from '@/data/graphView'
import { useGraphViewport } from '@/lib/useGraphViewport'
import { cn } from '@/lib/utils'
import type { KeyboardEvent as ReactKeyboardEvent, PointerEvent as ReactPointerEvent } from 'react'
import type { ViewInsets } from '@/lib/useGraphViewport'
import type { Connection, GraphPoint } from '@/types/connection'
import type { Place } from '@/types/place'

interface ConnectionsGraphProps {
    places: Place[]
    connections: Connection[]
    layout: Record<string, GraphPoint>
    highlightedId: string | null
    /**
     * Los puntos de los que habló la última respuesta del agente. Envoltorio nuevo en cada
     * respuesta a propósito, igual que en MapSpotlight: su identidad es lo que dispara el
     * vuelo, así repetir la misma pregunta vuelve a encuadrar aunque los puntos no cambien.
     */
    spotlight: { ids: string[] } | null
    onSelectDot: (placeId: string) => void
    onClearSelection: () => void
}

const SETTLE_MS = 1400
const SPOTLIGHT_SCALE = 1.8

/*
 * Aquí no se pinta el nombre de los puntos. Los nombres reales son largos y los
 * puntos de un mismo barrio caen a decenas de unidades unos de otros, así que
 * las etiquetas se pisan; y acercarse no lo arregla, porque el zoom agranda por
 * igual el texto y la distancia entre puntos. El nombre está donde se puede
 * leer entero: en el panel, a un clic de distancia, y en el `aria-label` del
 * punto para quien navega sin ver.
 */

/** Paso de los botones de zoom y de las teclas + y −. */
const ZOOM_STEP = 1.4

/** Cuánto corre la cámara con cada flecha, en unidades del viewBox. */
const KEY_PAN = 80

/**
 * Malla de fondo. No es decoración: sin una referencia fija detrás, arrastrar
 * una zona vacía del grafo no se ve, y el lienzo parece roto en vez de infinito.
 * La extensión está muy por encima de lo que ocupan los puntos porque el paneo
 * no tiene topes; el navegador solo pinta los mosaicos que caen en pantalla.
 */
const GRID_TILE = 44
const GRID_EXTENT = 8000

/**
 * Lo que tapan las capas de encima, en píxeles: la leyenda arriba y el chat
 * abajo. Encuadrar contra la pantalla entera dejaría puntos justo debajo de
 * ellas, que es como no encuadrarlos.
 */
const CHROME_INSETS: ViewInsets = { top: 96, right: 48, bottom: 184, left: 48 }

function easeOutCubic(t: number) {
    return 1 - Math.pow(1 - t, 3)
}

/**
 * Posición inicial del punto i antes de asentarse: un corrillo determinista
 * alrededor del centro. Nada de Math.random — la entrada se ve igual siempre.
 */
function scatterPoint(index: number): GraphPoint {
    return {
        x: GRAPH_VIEWBOX.width / 2 + Math.cos(index * 2.4) * 60,
        y: GRAPH_VIEWBOX.height / 2 + Math.sin(index * 2.4) * 60
    }
}

/**
 * El grafo en sí: SVG dibujado a mano, sin librería de grafos. Las posiciones
 * de reposo vienen calculadas de fuera (la geografía real proyectada sobre el
 * viewBox), así que aquí no hay layout que resolver: solo pintar, animar y
 * dejarse recorrer.
 *
 * El viewBox no es el límite del mundo, solo el encuadre de partida: la cámara
 * (`useGraphViewport`) arrastra y acerca sin topes, de modo que el grafo se
 * navega como una red que sigue más allá de la pantalla. De ahí el cursor
 * propio: sobre este lienzo el puntero es una red de nodos, no una flecha.
 *
 * Presentational: reporta clics y pinta lo que le digan las props; su único
 * estado es dónde está mirando. La maquinaria es un bucle rAF que escribe
 * transform de puntos y extremos de líneas vía refs — así los hilos quedan
 * pegados a los puntos a la deriva sin re-render por frame. El bucle jamás toca
 * `opacity`: las transiciones CSS de resaltar/atenuar son dueñas de esa
 * propiedad, ni `transform` del grupo: esa es de la cámara.
 */
export function ConnectionsGraph({
    places,
    connections,
    layout,
    highlightedId,
    spotlight,
    onSelectDot,
    onClearSelection
}: ConnectionsGraphProps) {
    const dotRefs = useRef(new Map<string, SVGGElement>())
    const lineRefs = useRef(new Map<string, SVGLineElement>())
    const svgRef = useRef<SVGSVGElement>(null)

    // La cámara: dónde está mirando el grafo y los gestos que la mueven.
    const {
        viewStyle,
        isPanning,
        hasDragged,
        panBy,
        zoomBy,
        focusOn,
        fitTo,
        ensureVisible,
        gestureHandlers
    } = useGraphViewport(svgRef)

    // Posiciones de arranque por id, para que el primer frame ya pinte el
    // corrillo (sin esto habría un destello de puntos en el origen).
    const scatterById = useMemo(
        () => new Map(places.map((place, index) => [place.id, scatterPoint(index)])),
        [places]
    )

    // Con un punto resaltado, sus emparejados siguen vivos y el resto se atenúa.
    const connectedIds = useMemo(() => {
        if (highlightedId === null) {
            return null
        }
        const ids = new Set([highlightedId])
        for (const connection of connections) {
            if (connection.neededId === highlightedId) {
                ids.add(connection.offeredId)
            }
            if (connection.offeredId === highlightedId) {
                ids.add(connection.neededId)
            }
        }
        return ids
    }, [highlightedId, connections])

    useEffect(() => {
        let frame = 0
        const start = performance.now()

        const tick = (now: number) => {
            const elapsed = now - start
            const settle = easeOutCubic(Math.min(1, elapsed / SETTLE_MS))
            const positions = new Map<string, GraphPoint>()

            places.forEach((place, index) => {
                const target = layout[place.id]
                if (target === undefined) {
                    return
                }
                const scatter = scatterPoint(index)
                // La deriva entra multiplicada por `settle` para no sacudir el
                // corrillo inicial; dos frecuencias distintas evitan que todos
                // los puntos respiren al unísono.
                const x =
                    scatter.x +
                    (target.x - scatter.x) * settle +
                    Math.sin(elapsed / 1700 + index * 1.7) * 6 * settle
                const y =
                    scatter.y +
                    (target.y - scatter.y) * settle +
                    Math.cos(elapsed / 2300 + index * 2.1) * 6 * settle
                positions.set(place.id, { x, y })
                dotRefs.current.get(place.id)?.setAttribute('transform', `translate(${x} ${y})`)
            })

            for (const connection of connections) {
                const a = positions.get(connection.neededId)
                const b = positions.get(connection.offeredId)
                if (a === undefined || b === undefined) {
                    continue
                }
                for (const key of [`${connection.id}:glow`, `${connection.id}:core`]) {
                    const line = lineRefs.current.get(key)
                    if (line === undefined) {
                        continue
                    }
                    line.setAttribute('x1', String(a.x))
                    line.setAttribute('y1', String(a.y))
                    line.setAttribute('x2', String(b.x))
                    line.setAttribute('y2', String(b.y))
                }
            }

            frame = requestAnimationFrame(tick)
        }

        frame = requestAnimationFrame(tick)
        return () => cancelAnimationFrame(frame)
    }, [places, connections, layout])

    const fitAll = useCallback(() => fitTo(Object.values(layout), CHROME_INSETS), [fitTo, layout])

    // El pulso los marca a todos: con varios, el encuadre solo dice "por aquí".
    const spotlightSet = useMemo(() => new Set(spotlight?.ids ?? []), [spotlight])

    // El spotlight es el único encuadre que no pide el usuario con la mano: el agente
    // contestó y la cámara vuela a lo que señaló. Apagarlo (un clic en cualquier sitio) ya
    // no devuelve la cámara a su sitio; deshacer un vuelo que el usuario ya continuó a mano
    // sería quitarle el grafo de debajo.
    //
    // Con un punto se acerca a él; con varios se encuadran todos, porque acercarse a uno de
    // ellos afirmaría que es *el* punto. En los dos casos el vuelo abre el panel lateral a
    // la vez, así que se encuadra en lo que queda a su izquierda y no debajo de él.
    useEffect(() => {
        const points = (spotlight?.ids ?? []).map((id) => layout[id]).filter((point) => point !== undefined)
        if (points.length === 0) {
            return
        }
        const insets = {
            ...CHROME_INSETS,
            right: window.innerWidth >= PANEL_BREAKPOINT ? PANEL_WIDTH : CHROME_INSETS.right
        }
        if (points.length === 1) {
            focusOn(points[0], SPOTLIGHT_SCALE, insets)
            return
        }
        fitTo(points, insets)
    }, [spotlight, layout, focusOn, fitTo])

    /**
     * Qué punto se apretó, y abrirlo al soltar si el gesto no fue un arrastre.
     *
     * La selección no cuelga del `click` de cada punto porque el lienzo captura
     * el puntero para poder arrastrar fuera de la ventana, y con la captura
     * puesta el navegador reetiqueta `mouseup` —y con él el `click`— al SVG: el
     * manejador del punto no llegaría a ejecutarse nunca. El par
     * pointerdown/pointerup sí sabe siempre dónde empezó el gesto.
     *
     * Soltar sobre el fondo despeja la selección, que es lo que hacía antes el
     * clic en el rectángulo de fondo.
     */
    const pressedPlaceId = useRef<string | null>(null)

    const handlePointerDown = (event: ReactPointerEvent<SVGSVGElement>) => {
        const dot = event.target instanceof Element ? event.target.closest('[data-place-id]') : null
        pressedPlaceId.current = dot?.getAttribute('data-place-id') ?? null
        gestureHandlers.onPointerDown(event)
    }

    const handlePointerUp = (event: ReactPointerEvent<SVGSVGElement>) => {
        gestureHandlers.onPointerUp(event)
        const placeId = pressedPlaceId.current
        pressedPlaceId.current = null
        // Recorrer el grafo no debe seleccionar ni despejar nada por el camino.
        if (hasDragged()) {
            return
        }
        if (placeId === null) {
            onClearSelection()
        } else {
            onSelectDot(placeId)
        }
    }

    const handlePointerCancel = (event: ReactPointerEvent<SVGSVGElement>) => {
        // Un gesto cancelado (el sistema se queda el dedo) no es un clic.
        pressedPlaceId.current = null
        gestureHandlers.onPointerCancel(event)
    }

    const handleKeyDown = (event: ReactKeyboardEvent<SVGSVGElement>) => {
        // Enter y espacio no llegan hasta aquí: los consume el punto enfocado.
        // El teclado mueve sin animación a propósito: con la tecla apretada, una
        // transición por pulsación dejaría la cámara siempre por detrás.
        switch (event.key) {
            case 'ArrowLeft':
                panBy(KEY_PAN, 0)
                break
            case 'ArrowRight':
                panBy(-KEY_PAN, 0)
                break
            case 'ArrowUp':
                panBy(0, KEY_PAN)
                break
            case 'ArrowDown':
                panBy(0, -KEY_PAN)
                break
            case '+':
            case '=':
                zoomBy(ZOOM_STEP, false)
                break
            case '-':
            case '_':
                zoomBy(1 / ZOOM_STEP, false)
                break
            case '0':
                fitAll()
                break
            default:
                return
        }
        // Solo si la tecla era de la cámara: si no, se lleva por delante el
        // scroll de la página y el tabulador.
        event.preventDefault()
    }

    // Las dos capas se memorizan porque la cámara vive en estado: sin esto,
    // cada frame de un arrastre volvería a reconciliar los cientos de puntos e
    // hilos que no han cambiado. Con la misma referencia, React se salta el
    // subárbol entero y el arrastre solo cuesta el `transform` del grupo.
    const edges = useMemo(
        () =>
            connections.map((connection) => {
                const isActive =
                    highlightedId === null ||
                    connection.neededId === highlightedId ||
                    connection.offeredId === highlightedId
                const a = scatterById.get(connection.neededId)
                const b = scatterById.get(connection.offeredId)
                const endpoints =
                    a !== undefined && b !== undefined ? { x1: a.x, y1: a.y, x2: b.x, y2: b.y } : undefined
                return (
                    <g
                        key={connection.id}
                        className="transition-opacity duration-300"
                        style={{
                            // Brillo proporcional a la fuerza del enlace;
                            // casi apagado si el resaltado va por otro lado.
                            opacity: (0.25 + connection.strength * 0.55) * (isActive ? 1 : 0.15)
                        }}
                    >
                        <line
                            ref={(el) => {
                                if (el !== null) {
                                    lineRefs.current.set(`${connection.id}:glow`, el)
                                } else {
                                    lineRefs.current.delete(`${connection.id}:glow`)
                                }
                            }}
                            {...endpoints}
                            stroke="var(--color-fg)"
                            strokeWidth={4 + connection.strength * 6}
                            strokeLinecap="round"
                            filter="url(#hilo-glow)"
                        />
                        <line
                            ref={(el) => {
                                if (el !== null) {
                                    lineRefs.current.set(`${connection.id}:core`, el)
                                } else {
                                    lineRefs.current.delete(`${connection.id}:core`)
                                }
                            }}
                            {...endpoints}
                            stroke="var(--color-fg)"
                            strokeWidth={1 + connection.strength * 2.5}
                            strokeLinecap="round"
                        />
                    </g>
                )
            }),
        [connections, highlightedId, scatterById]
    )

    const dots = useMemo(
        () =>
            places.map((place) => {
                const scatter = scatterById.get(place.id)
                // Lo señalado no se atenúa aunque el panel resalte a otro: el agente acaba
                // de decir que habla de todos ellos, y apagarlos lo desmentiría.
                const isDimmed =
                    connectedIds !== null && !connectedIds.has(place.id) && !spotlightSet.has(place.id)
                const isHighlighted = place.id === highlightedId
                const color = place.kind === 'needed' ? 'var(--color-need)' : 'var(--color-offer)'
                return (
                    <g
                        key={place.id}
                        ref={(el) => {
                            if (el !== null) {
                                dotRefs.current.set(place.id, el)
                            } else {
                                dotRefs.current.delete(place.id)
                            }
                        }}
                        transform={scatter !== undefined ? `translate(${scatter.x} ${scatter.y})` : undefined}
                        // Es lo que lee `handlePointerDown` para saber qué punto
                        // se apretó: el `click` de aquí no es de fiar mientras
                        // el lienzo tiene capturado el puntero.
                        data-place-id={place.id}
                        role="button"
                        tabIndex={0}
                        aria-label={`${place.name}, ${place.city} — ${
                            place.kind === 'needed' ? 'necesita ayuda' : 'ofrece ayuda'
                        }`}
                        // Un lector de pantalla activa el punto con un `click`
                        // sintético, sin punteros de por medio: esta es su vía.
                        // Con ratón o dedo manda el pointerup, y repetir la
                        // misma selección no cambia nada.
                        onClick={() => {
                            if (!hasDragged()) {
                                onSelectDot(place.id)
                            }
                        }}
                        // Tabular hasta un punto que la cámara dejó fuera lo
                        // trae a la vista: si no, el foco se iría a la nada.
                        // Solo con el teclado (`:focus-visible`): al hacer clic
                        // el punto ya está donde el usuario apuntó, y mover la
                        // cámara debajo de su mano sería un tirón sin motivo.
                        onFocus={(event) => {
                            const point = layout[place.id]
                            if (point !== undefined && event.currentTarget.matches(':focus-visible')) {
                                ensureVisible(point, CHROME_INSETS)
                            }
                        }}
                        onKeyDown={(event) => {
                            if (event.key === 'Enter' || event.key === ' ') {
                                event.preventDefault()
                                onSelectDot(place.id)
                            }
                        }}
                        className="conexiones-nodo focus-visible:outline-brand transition-opacity duration-300 outline-none focus-visible:outline-2 focus-visible:outline-offset-4"
                        style={{ opacity: isDimmed ? 0.12 : 1 }}
                    >
                        {spotlightSet.has(place.id) ? (
                            <circle
                                r={10}
                                fill="none"
                                stroke={color}
                                strokeWidth={2}
                                className="conexiones-pulse"
                            />
                        ) : null}
                        <circle r={13} fill={color} opacity={0.18} />
                        {/* Resaltado con el mismo lenguaje del marcador
                            seleccionado del mapa: núcleo blanco, aro de color. */}
                        <circle
                            r={7}
                            fill={isHighlighted ? 'var(--color-fg)' : color}
                            stroke={isHighlighted ? color : 'none'}
                            strokeWidth={isHighlighted ? 3 : 0}
                        />
                    </g>
                )
            }),
        [
            places,
            layout,
            scatterById,
            connectedIds,
            highlightedId,
            spotlightSet,
            hasDragged,
            onSelectDot,
            ensureVisible
        ]
    )

    return (
        <>
            <svg
                ref={svgRef}
                viewBox={`0 0 ${GRAPH_VIEWBOX.width} ${GRAPH_VIEWBOX.height}`}
                preserveAspectRatio="xMidYMid slice"
                // Focusable para poder recorrer el grafo sin ratón; el `aria-label`
                // es donde se cuentan los gestos, que de otro modo no se ven.
                tabIndex={0}
                aria-label="Grafo de conexiones. Arrastra para recorrerlo y usa la rueda para acercarte; con el teclado, las flechas mueven, + y − acercan y 0 encuadra todos los puntos."
                onKeyDown={handleKeyDown}
                onPointerDown={handlePointerDown}
                onPointerMove={gestureHandlers.onPointerMove}
                onPointerUp={handlePointerUp}
                onPointerCancel={handlePointerCancel}
                className={cn(
                    'conexiones-lienzo focus-visible:outline-brand h-full w-full touch-none outline-none focus-visible:outline-2 focus-visible:-outline-offset-2',
                    isPanning && 'conexiones-lienzo--agarrado'
                )}
            >
                <defs>
                    {/* Región amplia: sin ella el blur se recorta al bounding box
                        de la línea, que en tramos casi horizontales es finísimo. */}
                    <filter id="hilo-glow" x="-50%" y="-50%" width="200%" height="200%">
                        <feGaussianBlur stdDeviation="4" />
                    </filter>
                    <pattern
                        id="conexiones-malla"
                        width={GRID_TILE}
                        height={GRID_TILE}
                        patternUnits="userSpaceOnUse"
                    >
                        <circle cx={1} cy={1} r={1} fill="var(--color-fg)" opacity={0.09} />
                    </pattern>
                </defs>

                {/* Superficie del gesto: hace que el arrastre y el toque en el
                    vacío lleguen al SVG empiecen donde empiecen. Va fuera del
                    grupo con cámara, así que cubre la pantalla entera por muy
                    lejos que se haya viajado. */}
                <rect
                    width={GRAPH_VIEWBOX.width}
                    height={GRAPH_VIEWBOX.height}
                    fill="none"
                    pointerEvents="all"
                />

                <g style={viewStyle}>
                    {/* Dentro del grupo para que la malla viaje con el grafo: es
                        lo que hace visible el movimiento en las zonas vacías. */}
                    <rect
                        x={-GRID_EXTENT}
                        y={-GRID_EXTENT}
                        width={GRID_EXTENT * 2}
                        height={GRID_EXTENT * 2}
                        fill="url(#conexiones-malla)"
                        pointerEvents="none"
                    />
                    {edges}
                    {dots}
                </g>
            </svg>

            {/* Mismo sitio y mismo lenguaje que el control de zoom del mapa: las
                dos pestañas se manejan igual. Por debajo de 1280 px las dos lo
                suben a la esquina superior derecha, porque abajo la columna del
                chat llega a los bordes y taparía el botón de enviar; arriba a la
                izquierda está la leyenda, así que la derecha queda libre. En
                móvil baja un escalón más: encima está la fila de mandos. */}
            <div className="border-line bg-surface/85 absolute top-16 right-4 z-[1150] flex flex-col overflow-hidden rounded-md border backdrop-blur sm:top-4 xl:top-auto xl:bottom-4">
                <button
                    type="button"
                    onClick={() => zoomBy(ZOOM_STEP)}
                    aria-label="Acercar"
                    className="border-line text-fg-muted hover:bg-surface-muted hover:text-fg focus-visible:outline-brand coarse:size-11 grid size-9 place-items-center border-b transition-colors duration-200 focus-visible:outline-2 focus-visible:-outline-offset-2"
                >
                    <Plus size={16} strokeWidth={2} aria-hidden />
                </button>
                <button
                    type="button"
                    onClick={() => zoomBy(1 / ZOOM_STEP)}
                    aria-label="Alejar"
                    className="border-line text-fg-muted hover:bg-surface-muted hover:text-fg focus-visible:outline-brand coarse:size-11 grid size-9 place-items-center border-b transition-colors duration-200 focus-visible:outline-2 focus-visible:-outline-offset-2"
                >
                    <Minus size={16} strokeWidth={2} aria-hidden />
                </button>
                <button
                    type="button"
                    onClick={fitAll}
                    aria-label="Ver todo el grafo"
                    className="text-fg-muted hover:bg-surface-muted hover:text-fg focus-visible:outline-brand coarse:size-11 grid size-9 place-items-center transition-colors duration-200 focus-visible:outline-2 focus-visible:-outline-offset-2"
                >
                    <Maximize size={16} strokeWidth={2} aria-hidden />
                </button>
            </div>
        </>
    )
}
