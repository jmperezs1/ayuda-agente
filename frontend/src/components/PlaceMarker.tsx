import { divIcon, type DivIcon, type LeafletEventHandlerFnMap } from 'leaflet'
import { useMemo } from 'react'
import { Marker } from 'react-leaflet'
import type { HelpKind, Place } from '@/types/place'

const ICON_SIZE = 26

/**
 * Los tres estados de un marcador. `focused` es el resaltado del chat cuando la respuesta
 * habla de varios puntos: el encuadre solo dice "por aquí" y hace falta algo que diga
 * cuáles. `selected` es el único punto abierto en el panel, y manda sobre él.
 */
type MarkerState = 'default' | 'selected' | 'focused'

const MODIFIERS: Record<MarkerState, string> = {
    default: '',
    selected: ' ayuda-marker--selected',
    focused: ' ayuda-marker--focused'
}

function buildIcon(kind: HelpKind, state: MarkerState): DivIcon {
    return divIcon({
        className: `ayuda-marker ayuda-marker--${kind}${MODIFIERS[state]}`,
        html: '<span></span>',
        iconSize: [ICON_SIZE, ICON_SIZE],
        iconAnchor: [ICON_SIZE / 2, ICON_SIZE / 2]
    })
}

/** Hoisted so a marker's icon is never reallocated on render. Styled in styles/index.css. */
const ICONS: Record<HelpKind, Record<MarkerState, DivIcon>> = {
    needed: {
        default: buildIcon('needed', 'default'),
        selected: buildIcon('needed', 'selected'),
        focused: buildIcon('needed', 'focused')
    },
    offered: {
        default: buildIcon('offered', 'default'),
        selected: buildIcon('offered', 'selected'),
        focused: buildIcon('offered', 'focused')
    }
}

interface PlaceMarkerProps {
    place: Place
    isSelected: boolean
    /** Uno de los varios puntos que señaló la última respuesta del agente. */
    isFocused?: boolean
    onSelect: (placeId: string) => void
}

export function PlaceMarker({ place, isSelected, isFocused = false, onSelect }: PlaceMarkerProps) {
    const eventHandlers = useMemo<LeafletEventHandlerFnMap>(
        () => ({
            click: () => onSelect(place.id),
            // Leaflet gives markers tabIndex and role="button" but never wires
            // activation, so Enter/Space have to be handled here.
            keydown: (event) => {
                const { key } = event.originalEvent
                if (key === 'Enter' || key === ' ') {
                    event.originalEvent.preventDefault()
                    onSelect(place.id)
                }
            }
        }),
        [onSelect, place.id]
    )

    return (
        <Marker
            position={place.position}
            icon={ICONS[place.kind][isSelected ? 'selected' : isFocused ? 'focused' : 'default']}
            // `title` names the marker; Leaflet only applies `alt` to <img> icons.
            // Se usa el titular del reporte, que identifica el punto mejor que
            // el nombre del barrio a secas.
            title={place.title}
            eventHandlers={eventHandlers}
        />
    )
}
