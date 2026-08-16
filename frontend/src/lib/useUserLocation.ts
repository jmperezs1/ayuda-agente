/**
 * Dónde está quien pregunta.
 *
 * El agente ordena por cercanía cuando le das un punto, así que "¿qué falta
 * cerca de mí?" solo tiene respuesta si el navegador dice dónde es "mí". Es
 * contexto, nunca un requisito: sin permiso el chat funciona igual.
 *
 * `watchPosition` y no una lectura única porque un coordinador se mueve durante
 * la emergencia, y la posición tomada al abrir la pestaña deja de describirle a
 * los diez minutos.
 */
import { useEffect, useState } from 'react'
import type { GeoPoint } from '@/types/graph'

/**
 * Precisión de calle: basta para ordenar por cercanía y no enciende el GPS.
 * `maximumAge` permite reutilizar una lectura reciente en vez de volver a medir.
 */
const WATCH_OPTIONS: PositionOptions = {
    enableHighAccuracy: false,
    timeout: 10000,
    maximumAge: 60000
}

/**
 * La última posición conocida del navegador, o null mientras no haya ninguna.
 *
 * Note: nunca lanza ni bloquea. Permiso denegado, navegador sin la API o fallo
 *     de señal son todos "no lo sé", que es un estado válido y el habitual.
 */
export function useUserLocation(): GeoPoint | null {
    const [location, setLocation] = useState<GeoPoint | null>(null)

    useEffect(() => {
        // Sin HTTPS —y en algunos navegadores embebidos— la API no existe
        if (navigator.geolocation === undefined) {
            return
        }

        const watchId = navigator.geolocation.watchPosition(
            ({ coords }) => {
                // Una coordenada no finita sería un 400 del backend, no una posición
                if (Number.isFinite(coords.latitude) && Number.isFinite(coords.longitude)) {
                    setLocation({ lat: coords.latitude, lon: coords.longitude })
                }
            },
            (error) => {
                // Retirar el permiso retira el consentimiento: se deja de enviar.
                // Un fallo de señal es pasajero y no invalida la última posición.
                if (error.code === error.PERMISSION_DENIED) {
                    setLocation(null)
                }
            },
            WATCH_OPTIONS
        )

        return () => navigator.geolocation.clearWatch(watchId)
    }, [])

    return location
}
