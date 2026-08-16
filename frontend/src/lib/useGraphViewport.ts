/**
 * La cámara del grafo de Conexiones: un lienzo sin bordes que se arrastra y se
 * acerca.
 *
 * Toda la geometría vive en unidades del viewBox del SVG, no en píxeles: es el
 * mismo espacio en el que están las posiciones de los puntos, así que centrar
 * uno es una resta y no una conversión. Lo que sí llega en píxeles —el puntero,
 * y los márgenes que tapan la leyenda, el chat y el panel— se traduce con la
 * matriz del propio SVG (`getScreenCTM`), que ya incorpora el
 * `preserveAspectRatio`: así el encuadre sale bien con la ventana ancha (donde
 * `slice` recorta arriba y abajo) y también estrecha.
 *
 * La vista es estado de React y no una ref con escrituras a mano: cambia como
 * mucho una vez por frame —los eventos de puntero no llegan más deprisa que el
 * repintado— así que no hace falta salirse de React para que el arrastre vaya
 * fino. Quien la consume solo pinta el `viewStyle` que sale de aquí.
 */
import { useCallback, useEffect, useRef, useState } from 'react'
import type { CSSProperties, PointerEvent as ReactPointerEvent, RefObject } from 'react'
import type { GraphPoint } from '@/types/connection'

/** Dónde está la cámara: desplazamiento y aumento, en unidades del viewBox. */
export interface GraphView {
    x: number
    y: number
    scale: number
}

/** Lo que tapan las capas de encima del grafo, en píxeles de pantalla. */
export interface ViewInsets {
    top: number
    right: number
    bottom: number
    left: number
}

interface Rect {
    x: number
    y: number
    width: number
    height: number
}

const IDENTITY: GraphView = { x: 0, y: 0, scale: 1 }

/**
 * Cuánto se puede alejar y acercar. Alejar más convierte el grafo en una
 * mancha ilegible; acercar más deja un solo punto en pantalla sin nada
 * alrededor con lo que orientarse.
 */
const MIN_SCALE = 0.35
const MAX_SCALE = 6

/** Duración del vuelo guiado (chat, botones, "ver todo"); arrastrar no anima. */
const GLIDE_MS = 600

/** Píxeles a partir de los cuales el gesto deja de ser un clic y es un arrastre. */
const DRAG_SLOP = 4

function clampScale(scale: number): number {
    return Math.min(MAX_SCALE, Math.max(MIN_SCALE, scale))
}

/** De coordenadas de pantalla a unidades del viewBox. */
function toUserSpace(svg: SVGSVGElement, clientX: number, clientY: number): GraphPoint {
    const screenToUser = svg.getScreenCTM()?.inverse()
    if (screenToUser === undefined) {
        return { x: 0, y: 0 }
    }
    const point = new DOMPoint(clientX, clientY).matrixTransform(screenToUser)
    return { x: point.x, y: point.y }
}

/**
 * El trozo del espacio del viewBox que se está viendo de verdad, ya descontado
 * lo que tapan las capas de encima. Con la ventana muy pequeña esos márgenes se
 * comen el hueco entero, y entonces vale más encuadrar contra todo lo visible
 * que contra un rectángulo de área negativa.
 */
function usableRect(svg: SVGSVGElement, insets: ViewInsets): Rect {
    const box = svg.getBoundingClientRect()
    const start = toUserSpace(svg, box.left, box.top)
    const end = toUserSpace(svg, box.right, box.bottom)
    const visible = { x: start.x, y: start.y, width: end.x - start.x, height: end.y - start.y }

    // Los márgenes vienen en píxeles porque en píxeles están medidas la leyenda,
    // el chat y el panel; aquí se pasan a unidades del viewBox.
    const perPixelX = box.width === 0 ? 0 : visible.width / box.width
    const perPixelY = box.height === 0 ? 0 : visible.height / box.height
    const width = visible.width - (insets.left + insets.right) * perPixelX
    const height = visible.height - (insets.top + insets.bottom) * perPixelY
    if (width <= 0 || height <= 0) {
        return visible
    }
    return {
        x: visible.x + insets.left * perPixelX,
        y: visible.y + insets.top * perPixelY,
        width,
        height
    }
}

/** Separación y centro de los dos punteros de un pellizco. */
function measurePinch(pointers: Map<number, GraphPoint>): { span: number; center: GraphPoint } | null {
    if (pointers.size < 2) {
        return null
    }
    const [a, b] = [...pointers.values()]
    return {
        span: Math.hypot(b.x - a.x, b.y - a.y),
        center: { x: (a.x + b.x) / 2, y: (a.y + b.y) / 2 }
    }
}

export function useGraphViewport(svgRef: RefObject<SVGSVGElement | null>) {
    const [view, setView] = useState<GraphView>(IDENTITY)
    const [isPanning, setIsPanning] = useState(false)
    // Solo anima lo que decide el código. Un arrastre tiene que ir pegado al
    // puntero, y una transición lo dejaría siempre medio segundo por detrás.
    const [glide, setGlide] = useState(false)

    // Punteros vivos, en unidades del viewBox: uno arrastra, dos pellizcan.
    const pointers = useRef(new Map<number, GraphPoint>())
    const pinch = useRef<{ span: number; center: GraphPoint } | null>(null)
    // Dónde se apretó (en píxeles) y si ya se pasó del umbral: un arrastre no
    // debe acabar además abriendo un punto ni despejando la selección.
    const pressedAt = useRef<{ x: number; y: number } | null>(null)
    const dragged = useRef(false)

    const panBy = useCallback((dx: number, dy: number) => {
        setGlide(false)
        setView((previous) => ({ ...previous, x: previous.x + dx, y: previous.y + dy }))
    }, [])

    /** Acerca o aleja dejando quieto el punto `anchor`: el cursor, o el centro. */
    const zoomAt = useCallback((anchor: GraphPoint, factor: number, animated: boolean) => {
        setGlide(animated)
        setView((previous) => {
            const scale = clampScale(previous.scale * factor)
            const ratio = scale / previous.scale
            return {
                scale,
                x: anchor.x - (anchor.x - previous.x) * ratio,
                y: anchor.y - (anchor.y - previous.y) * ratio
            }
        })
    }, [])

    /** Zoom sin puntero (botones y teclado): el ancla es el centro de la pantalla. */
    const zoomBy = useCallback(
        (factor: number, animated = true) => {
            const svg = svgRef.current
            if (svg === null) {
                return
            }
            const rect = usableRect(svg, { top: 0, right: 0, bottom: 0, left: 0 })
            zoomAt({ x: rect.x + rect.width / 2, y: rect.y + rect.height / 2 }, factor, animated)
        },
        [svgRef, zoomAt]
    )

    /** Lleva un punto del grafo al centro del hueco libre. Es el vuelo del chat. */
    const focusOn = useCallback(
        (point: GraphPoint, scale: number, insets: ViewInsets) => {
            const svg = svgRef.current
            if (svg === null) {
                return
            }
            const rect = usableRect(svg, insets)
            const next = clampScale(scale)
            setGlide(true)
            setView({
                scale: next,
                x: rect.x + rect.width / 2 - next * point.x,
                y: rect.y + rect.height / 2 - next * point.y
            })
        },
        [svgRef]
    )

    /**
     * Encuadra todos los puntos a la vez: es la vuelta a casa después de
     * recorrer el grafo. Con un solo punto (o con todos en el mismo sitio) no
     * hay extensión que ajustar, así que se centra al máximo aumento.
     */
    const fitTo = useCallback(
        (points: GraphPoint[], insets: ViewInsets) => {
            const svg = svgRef.current
            if (svg === null || points.length === 0) {
                return
            }
            const rect = usableRect(svg, insets)
            const xs = points.map((point) => point.x)
            const ys = points.map((point) => point.y)
            const minX = Math.min(...xs)
            const maxX = Math.max(...xs)
            const minY = Math.min(...ys)
            const maxY = Math.max(...ys)
            const scale = clampScale(
                Math.min(rect.width / Math.max(maxX - minX, 1), rect.height / Math.max(maxY - minY, 1))
            )
            setGlide(true)
            setView({
                scale,
                x: rect.x + rect.width / 2 - (scale * (minX + maxX)) / 2,
                y: rect.y + rect.height / 2 - (scale * (minY + maxY)) / 2
            })
        },
        [svgRef]
    )

    /**
     * Trae un punto a la vista solo si quedó fuera. Es lo que mantiene
     * utilizable el tabulador: los puntos siguen siendo focusables aunque la
     * cámara esté en la otra punta del grafo, y sin esto el foco se iría a
     * sitios que no se ven.
     */
    const ensureVisible = useCallback(
        (point: GraphPoint, insets: ViewInsets) => {
            const svg = svgRef.current
            if (svg === null) {
                return
            }
            const rect = usableRect(svg, insets)
            setGlide(true)
            setView((previous) => {
                const at = {
                    x: previous.x + previous.scale * point.x,
                    y: previous.y + previous.scale * point.y
                }
                const isVisible =
                    at.x >= rect.x &&
                    at.x <= rect.x + rect.width &&
                    at.y >= rect.y &&
                    at.y <= rect.y + rect.height
                if (isVisible) {
                    return previous
                }
                return {
                    scale: previous.scale,
                    x: rect.x + rect.width / 2 - previous.scale * point.x,
                    y: rect.y + rect.height / 2 - previous.scale * point.y
                }
            })
        },
        [svgRef]
    )

    const handlePointerDown = useCallback(
        (event: ReactPointerEvent<SVGSVGElement>) => {
            // Solo el botón principal: el secundario abre el menú contextual.
            if (event.pointerType === 'mouse' && event.button !== 0) {
                return
            }
            const svg = svgRef.current
            if (svg === null) {
                return
            }
            // Un puntero primario es siempre el principio de un gesto: lo que
            // quede vivo de uno anterior (un pointerup que nunca llegó porque
            // el sistema se quedó el dedo, o un cambio de ventana a media
            // pulsación) es basura. Sin esta limpieza el gesto siguiente se
            // creería un pellizco y el lienzo dejaría de responder a los clics.
            if (event.isPrimary) {
                pointers.current.clear()
                pinch.current = null
            }
            event.currentTarget.setPointerCapture(event.pointerId)
            pointers.current.set(event.pointerId, toUserSpace(svg, event.clientX, event.clientY))
            if (pointers.current.size === 1) {
                pressedAt.current = { x: event.clientX, y: event.clientY }
                dragged.current = false
                setGlide(false)
                setIsPanning(true)
            }
            pinch.current = measurePinch(pointers.current)
        },
        [svgRef]
    )

    const handlePointerMove = useCallback(
        (event: ReactPointerEvent<SVGSVGElement>) => {
            const svg = svgRef.current
            const previousPoint = pointers.current.get(event.pointerId)
            if (svg === null || previousPoint === undefined) {
                return
            }
            const point = toUserSpace(svg, event.clientX, event.clientY)
            pointers.current.set(event.pointerId, point)

            const pressed = pressedAt.current
            if (
                pressed !== null &&
                Math.hypot(event.clientX - pressed.x, event.clientY - pressed.y) > DRAG_SLOP
            ) {
                dragged.current = true
            }

            if (pointers.current.size === 1) {
                panBy(point.x - previousPoint.x, point.y - previousPoint.y)
                return
            }

            const next = measurePinch(pointers.current)
            const last = pinch.current
            if (next === null || last === null) {
                return
            }
            pinch.current = next
            // Pellizco: el centro de los dos dedos arrastra y su separación
            // manda el aumento. Van en un mismo cambio de vista porque el ancla
            // del zoom es el centro ya desplazado; en dos pasos el contenido se
            // escaparía entre uno y otro.
            const factor = last.span === 0 ? 1 : next.span / last.span
            setGlide(false)
            setView((previous) => {
                const moved = {
                    x: previous.x + next.center.x - last.center.x,
                    y: previous.y + next.center.y - last.center.y
                }
                const scale = clampScale(previous.scale * factor)
                const ratio = scale / previous.scale
                return {
                    scale,
                    x: next.center.x - (next.center.x - moved.x) * ratio,
                    y: next.center.y - (next.center.y - moved.y) * ratio
                }
            })
        },
        [svgRef, panBy]
    )

    const hasDragged = useCallback(() => dragged.current, [])

    const handlePointerUp = useCallback((event: ReactPointerEvent<SVGSVGElement>) => {
        pointers.current.delete(event.pointerId)
        if (event.currentTarget.hasPointerCapture(event.pointerId)) {
            event.currentTarget.releasePointerCapture(event.pointerId)
        }
        // Levantar un dedo de un pellizco deja al otro arrastrando: se vuelve a
        // medir para que el que queda no herede el centro de los dos.
        pinch.current = measurePinch(pointers.current)
        if (pointers.current.size === 0) {
            pressedAt.current = null
            setIsPanning(false)
        }
    }, [])

    // La rueda se escucha a mano y no con `onWheel`: React registra `wheel` en
    // la raíz como pasivo, y ahí `preventDefault` no hace nada — el navegador
    // seguiría haciendo scroll de la página (o su propio zoom) encima del grafo.
    useEffect(() => {
        const svg = svgRef.current
        if (svg === null) {
            return
        }

        const handleWheel = (event: WheelEvent) => {
            event.preventDefault()
            // `deltaMode` 1 son líneas, no píxeles: Firefox manda unas tres por
            // muesca de rueda y sin convertirlas el zoom sería imperceptible.
            const steps = event.deltaMode === 1 ? event.deltaY * 16 : event.deltaY
            // El pellizco del trackpad llega como rueda con ctrl y pasos
            // pequeños; se le da más recorrido para que responda igual de vivo.
            const factor = Math.exp(-steps * (event.ctrlKey ? 0.012 : 0.0022))
            zoomAt(toUserSpace(svg, event.clientX, event.clientY), factor, false)
        }

        svg.addEventListener('wheel', handleWheel, { passive: false })
        return () => svg.removeEventListener('wheel', handleWheel)
    }, [svgRef, zoomAt])

    const viewStyle: CSSProperties = {
        transform: `translate(${view.x}px, ${view.y}px) scale(${view.scale})`,
        // El origen de una transformación CSS sobre un elemento SVG es, por
        // defecto, el centro de su viewBox; toda la cámara de aquí cuenta desde
        // (0, 0), que es donde empieza el sistema de coordenadas del grafo.
        transformOrigin: '0 0',
        transition: glide ? `transform ${GLIDE_MS}ms ease` : 'none'
    }

    return {
        viewStyle,
        /** True mientras hay un dedo o un botón apretado sobre el lienzo. */
        isPanning,
        /** Si el gesto que acaba de terminar fue un arrastre y no un clic. */
        hasDragged,
        panBy,
        zoomBy,
        focusOn,
        fitTo,
        ensureVisible,
        gestureHandlers: {
            onPointerDown: handlePointerDown,
            onPointerMove: handlePointerMove,
            onPointerUp: handlePointerUp,
            onPointerCancel: handlePointerUp
        }
    }
}
