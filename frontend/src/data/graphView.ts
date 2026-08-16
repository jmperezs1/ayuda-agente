import type { LatLngTuple } from 'leaflet'

/* ==========================================================================
   Constantes de encuadre. Los datos que se pintan vienen del backend
   (`GET /api/events/<id>/graph/`); aquí solo vive cómo se mira.
   ========================================================================== */

/** Encuadre inicial mientras carga el grafo; después se ajusta a los puntos reales. */
export const MAP_CENTER: LatLngTuple = [4.57, -76.17]
export const MAP_ZOOM = 6

/**
 * Zoom al enfocar una ubicación desde el menú del header.
 *
 * A zoom bajo los puntos de una misma ciudad se solapan y se leen como una
 * mancha de actividad; a 12 quedan claramente separados. No hay un zoom único
 * que cumpla las dos cosas cuando las ciudades están a cientos de km.
 */
export const CITY_ZOOM = 12

/** Zoom al volar hacia un punto concreto (lo usa el chat del mapa). */
export const PLACE_ZOOM = 15

/**
 * El panel de detalle, que el chat abre a la vez que vuela: hay que encuadrar en lo que
 * queda a su izquierda, no debajo de él. Por debajo de `sm` ocupa todo el ancho y entonces
 * no hay hueco que respetar.
 */
export const PANEL_WIDTH = 380
export const PANEL_BREAKPOINT = 640

/**
 * Tamaño del viewBox del grafo de Conexiones. El SVG usa `preserveAspectRatio`
 * en modo `slice`, así que en pantallas muy anchas o muy altas se recortan los
 * bordes: por eso las posiciones se proyectan sobre una zona segura interior.
 */
export const GRAPH_VIEWBOX = { width: 1000, height: 620 }
