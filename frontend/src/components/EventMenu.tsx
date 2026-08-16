import { Siren } from 'lucide-react'
import { SelectMenu } from '@/components/SelectMenu'
import { formatDate, HAZARD_LABELS } from '@/data/labels'
import type { EventBrief, EventSummary } from '@/types/graph'

interface EventMenuProps {
    events: EventBrief[]
    selectedId: number | null
    /** Recuentos del evento seleccionado; null mientras llegan o si fallaron. */
    summary: EventSummary | null
    onSelect: (eventId: number) => void
}

/**
 * Selector de emergencia. Es el ámbito de todo lo demás —mapa, grafo y los dos
 * chats preguntan sobre un evento— así que se ve en las dos pestañas, a
 * diferencia del menú de ubicaciones.
 *
 * La fila seleccionada enseña sus recuentos en vez de la fecha: al elegir una
 * emergencia lo que se quiere saber es qué hay dentro.
 */
export function EventMenu({ events, selectedId, summary, onSelect }: EventMenuProps) {
    const items = events.map((event) => ({
        id: String(event.id),
        title: event.name,
        subtitle:
            event.id === selectedId && summary !== null
                ? `${summary.needs} necesidades · ${summary.offers} ofertas · ${summary.matches} conexiones`
                : `${HAZARD_LABELS[event.hazard] ?? 'Emergencia'} · ${formatDate(event.occurred_at)}`
    }))

    return (
        <SelectMenu
            label="Emergencias"
            placeholder="Emergencia"
            icon={<Siren size={13} strokeWidth={2} aria-hidden className="shrink-0" />}
            items={items}
            selectedId={selectedId === null ? null : String(selectedId)}
            onSelect={(id) => onSelect(Number(id))}
        />
    )
}
