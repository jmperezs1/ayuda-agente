import type { LatLngTuple } from 'leaflet'
import type { Urgency } from '@/types/graph'

/** Si un punto pide ayuda (rojo) o la ofrece (verde). */
export type HelpKind = 'needed' | 'offered'

/**
 * Una necesidad u oferta abierta, ya lista para pintar: cantidades formateadas
 * y sin campos que la interfaz no use. Sale de `GraphRequirement`.
 */
export interface PlaceRequirement {
    id: string
    kind: HelpKind
    /** Nombre del recurso, p. ej. 'Agua potable'. */
    resource: string
    /** Lo que decía el reporte original; cadena vacía si no venía nada. */
    detail: string
    urgency: Urgency
    /** Pendiente ya formateado ('2600 L'), o null si nunca se dijo una cantidad. */
    outstanding: string | null
}

/**
 * Un punto del mapa: un actor del evento con sus necesidades u ofertas abiertas.
 *
 * `city` va denormalizado en vez de un `cityId` a propósito: el panel lateral
 * retiene el último punto durante la animación de salida, y resolver la ciudad
 * aparte obligaría a un segundo latch sincronizado.
 */
export interface Place {
    /** El id del actor en el backend, como cadena. */
    id: string
    /** Nombre del actor, p. ej. 'Consejo Comunitario La Yesquita'. */
    name: string
    /** Unidad administrativa de su ubicación; 'Sin ubicación' si el backend no la trae. */
    city: string
    /** Titular derivado de lo que pide u ofrece. */
    title: string
    /** Necesita ayuda o la ofrece; determina el color del punto en el mapa. */
    kind: HelpKind
    position: LatLngTuple
    requirements: PlaceRequirement[]
}

/** Destino del desplegable del header, derivado de las unidades administrativas. */
export interface City {
    id: string
    name: string
    /** Línea secundaria del menú: cuántos puntos hay en esa ubicación. */
    department: string
    position: LatLngTuple
}
