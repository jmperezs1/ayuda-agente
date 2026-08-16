import { AttributionControl, MapContainer, TileLayer, ZoomControl } from 'react-leaflet'
import { MapFit } from '@/components/MapFit'
import { MapFocus } from '@/components/MapFocus'
import { MapSpotlight } from '@/components/MapSpotlight'
import { PlaceMarker } from '@/components/PlaceMarker'
import { MAP_CENTER, MAP_ZOOM } from '@/data/graphView'
import type { City, Place } from '@/types/place'

const TILE_URL = 'https://{s}.basemaps.cartocdn.com/dark_all/{z}/{x}/{y}{r}.png'
const TILE_ATTRIBUTION =
    '&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> contributors &copy; <a href="https://carto.com/attributions">CARTO</a>'

interface MapStageProps {
    places: Place[]
    /** Emergencia en pantalla: al cambiar, la cámara se reencuadra. */
    eventId: number | null
    selectedId: string | null
    focusedCity: City | null
    /** Puntos de los que habló el agente; ver MapSpotlight sobre su identidad. */
    spotlight?: { places: Place[] } | null
    onSelectPlace: (placeId: string) => void
}

/**
 * The map itself. Reports which place was clicked; knows nothing about what
 * consumes that selection.
 */
export function MapStage({
    places,
    eventId,
    selectedId,
    focusedCity,
    spotlight = null,
    onSelectPlace
}: MapStageProps) {
    // Con varios señalados, el encuadre solo dice "por aquí": el halo dice cuáles.
    const focusedIds = new Set(
        spotlight !== null && spotlight.places.length > 1 ? spotlight.places.map((place) => place.id) : []
    )

    return (
        <MapContainer
            center={MAP_CENTER}
            zoom={MAP_ZOOM}
            zoomControl={false}
            attributionControl={false}
            scrollWheelZoom
            className="h-full w-full"
        >
            <TileLayer url={TILE_URL} attribution={TILE_ATTRIBUTION} subdomains="abcd" maxZoom={20} />
            {/* El zoom va abajo a la derecha; la esquina izquierda es del botón
                de feedback. La atribución se queda a la izquierda: es
                obligatoria y el panel de detalle, que entra por la derecha, no
                puede taparla. */}
            <ZoomControl position="bottomright" />
            <AttributionControl position="bottomleft" prefix={false} />
            <MapFit places={places} fitKey={eventId} />
            <MapFocus city={focusedCity} />
            <MapSpotlight target={spotlight} />
            {places.map((place) => (
                <PlaceMarker
                    key={place.id}
                    place={place}
                    isSelected={place.id === selectedId}
                    isFocused={focusedIds.has(place.id)}
                    onSelect={onSelectPlace}
                />
            ))}
        </MapContainer>
    )
}
