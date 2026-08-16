import { latLngBounds, point } from 'leaflet'
import { useEffect } from 'react'
import { useMap } from 'react-leaflet'
import { PANEL_BREAKPOINT, PANEL_WIDTH, PLACE_ZOOM } from '@/data/graphView'
import type { Place } from '@/types/place'

/** Duración del vuelo, la misma se acerque a un punto o encuadre varios. */
const FLIGHT_SECONDS = 1.2

/** Aire alrededor del encuadre, el mismo que usa MapFit al llegar los puntos. */
const PADDING = 60

interface MapSpotlightProps {
    /**
     * Envoltorio creado NUEVO en cada respuesta del chat, a propósito: el
     * efecto depende de su identidad, así que repetir la misma petición vuelve
     * a volar aunque los puntos sean los mismos (el usuario pudo haber movido el
     * mapa entre medias). Los `places` de dentro sí deben salir de la lista de puntos.
     */
    target: { places: Place[] } | null
}

/**
 * Mueve la cámara a lo que el agente acabó señalando. Mismo patrón que
 * MapFocus: `MapContainer` congela `center` y `zoom` tras el montaje y la
 * única forma de mover el mapa es la instancia de Leaflet vía `useMap()`, que
 * solo existe dentro del contenedor. De ahí este componente sin render propio.
 *
 * Un punto se mira de cerca; varios se encuadran juntos. Acercarse a uno de los cinco que
 * la respuesta menciona afirmaría que es *el* sitio, y el encuadre dice lo que de verdad
 * dijo el agente: están todos ahí. En los dos casos se abre el panel del primero, así que
 * el encuadre esquiva el ancho que ocupa.
 */
export function MapSpotlight({ target }: MapSpotlightProps) {
    const map = useMap()

    useEffect(() => {
        const places = target?.places ?? []
        if (places.length === 0) {
            return
        }
        if (places.length === 1) {
            map.flyTo(places[0].position, PLACE_ZOOM, { duration: FLIGHT_SECONDS })
            return
        }
        const panel = window.innerWidth >= PANEL_BREAKPOINT ? PANEL_WIDTH : 0
        map.flyToBounds(latLngBounds(places.map((place) => place.position)), {
            paddingTopLeft: point(PADDING, PADDING),
            paddingBottomRight: point(PADDING + panel, PADDING),
            duration: FLIGHT_SECONDS
        })
    }, [map, target])

    return null
}
