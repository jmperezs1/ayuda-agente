import { MapPin } from 'lucide-react'
import { SelectMenu } from '@/components/SelectMenu'
import type { City } from '@/types/place'

interface LocationMenuProps {
    cities: City[]
    focusedId: string | null
    onFocusCity: (cityId: string) => void
}

/** Ubicaciones del evento, derivadas de las unidades administrativas de sus puntos. */
export function LocationMenu({ cities, focusedId, onFocusCity }: LocationMenuProps) {
    const items = cities.map((city) => ({
        id: city.id,
        title: city.name,
        subtitle: city.department
    }))

    return (
        <SelectMenu
            label="Ubicaciones afectadas"
            placeholder="Ubicaciones afectadas"
            icon={<MapPin size={13} strokeWidth={2} aria-hidden className="shrink-0" />}
            items={items}
            selectedId={focusedId}
            onSelect={onFocusCity}
        />
    )
}
