import { latLngBounds } from 'leaflet'
import { useEffect, useRef } from 'react'
import { useMap } from 'react-leaflet'
import type { Place } from '@/types/place'

interface MapFitProps {
    places: Place[]
    /** Cambiarlo vuelve a encuadrar: es la emergencia que se está mirando. */
    fitKey: number | null
}

/**
 * Encuadra todos los puntos la primera vez que llegan, y otra vez con cada
 * emergencia nueva.
 *
 * El centro y el zoom iniciales de `MapContainer` se fijan al montar, cuando
 * todavía no hay datos, y la geografía del evento no tiene por qué caer dentro
 * de ese encuadre — ni la del siguiente dentro del anterior. Entre medias manda
 * el usuario: no se reencuadra por cualquier cambio de la lista.
 */
export function MapFit({ places, fitKey }: MapFitProps) {
    const map = useMap()
    const fittedKey = useRef<number | null>(null)

    useEffect(() => {
        if (fittedKey.current === fitKey || places.length === 0) {
            return
        }
        fittedKey.current = fitKey
        map.fitBounds(latLngBounds(places.map((place) => place.position)), { padding: [60, 60] })
    }, [map, places, fitKey])

    return null
}
