import { useEffect } from 'react'
import { useMap } from 'react-leaflet'
import { CITY_ZOOM } from '@/data/graphView'
import type { City } from '@/types/place'

interface MapFocusProps {
    /**
     * Debe ser siempre un elemento de la lista de ciudades (memoizada en
     * `useEventGraph`), nunca un objeto compuesto en render: el efecto depende
     * de su identidad y una referencia nueva en cada pasada reiniciaría el
     * vuelo continuamente.
     */
    city: City | null
}

/**
 * Mueve la cámara a la ciudad enfocada.
 *
 * `MapContainer` congela `center` y `zoom` después del montaje, así que la
 * única forma de mover el mapa es a través de la instancia de Leaflet, y
 * `useMap()` solo existe dentro del contenedor. De ahí este componente sin
 * render propio.
 */
export function MapFocus({ city }: MapFocusProps) {
    const map = useMap()

    useEffect(() => {
        if (city === null) {
            return
        }
        map.flyTo(city.position, CITY_ZOOM, { duration: 1.2 })
    }, [map, city])

    return null
}
