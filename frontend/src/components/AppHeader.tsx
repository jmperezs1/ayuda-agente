import { EventMenu } from '@/components/EventMenu'
import { FailFastLink } from '@/components/FailFastLink'
import { MusicToggle } from '@/components/MusicToggle'
import { cn } from '@/lib/utils'
import type { EventBrief, EventSummary } from '@/types/graph'

/** Vistas de nivel superior de la app. Exactamente estas doDDDs. */
export type AppTab = 'mapa' | 'conexiones'

const TABS: { id: AppTab; label: string }[] = [
    { id: 'mapa', label: 'Mapa' },
    { id: 'conexiones', label: 'Conexiones' }
]

interface AppHeaderProps {
    events: EventBrief[]
    selectedEventId: number | null
    eventSummary: EventSummary | null
    activeTab: AppTab
    onSelectEvent: (eventId: number) => void
    onSelectTab: (tab: AppTab) => void
}

export function AppHeader({
    events,
    selectedEventId,
    eventSummary,
    activeTab,
    onSelectEvent,
    onSelectTab
}: AppHeaderProps) {
    return (
        // `relative` ancla el grupo de mandos, que en móvil sale de la barra y
        // flota sobre el mapa (ver más abajo), y con él los desplegables.
        //
        // El z sube a 1300 desde `sm` para que el desplegable se pinte por encima
        // del mapa y del panel lateral, que vienen después en el DOM. En móvil se
        // queda en 1175 a propósito: ahí lo que sobrevuela el mapa es el grupo de
        // mandos, que debe taparle el zoom y la atribución (1150) pero quedarse
        // por debajo del panel de detalle (1200). La barra en sí no compite con
        // nada: ocupa su propia fila de la rejilla.
        //
        // `min-w-0` es obligatorio: como hijo de esa rejilla, su ancho mínimo
        // automático es su contenido, así que sin él la columna entera se
        // estiraría hasta caber la barra y sería la página la que desbordaría.
        // `flex-wrap` es el seguro: en una pantalla aún más estrecha el peor caso
        // es una fila de más, nunca una barra desbordada con scroll horizontal.
        <header className="border-line bg-page relative z-[1175] flex min-w-0 shrink-0 flex-wrap items-center justify-between gap-x-3 gap-y-2 border-b px-4 py-3 sm:z-[1300] sm:h-16 sm:flex-nowrap sm:gap-4 sm:px-6 sm:py-0">
            {/* width/height son el tamaño intrínseco real del archivo: reservan
                el espacio correcto y evitan el salto de layout al cargar. */}
            <img
                src="/logo.png"
                alt="Ayuda Agente"
                width={939}
                height={200}
                className="h-8 w-auto shrink-0 sm:h-10"
            />
            <div
                role="tablist"
                aria-label="Vistas"
                className="border-line bg-surface flex shrink-0 gap-1 rounded-full border p-1"
            >
                {TABS.map((tab) => (
                    <button
                        key={tab.id}
                        type="button"
                        role="tab"
                        aria-selected={activeTab === tab.id}
                        onClick={() => onSelectTab(tab.id)}
                        className={cn(
                            // `px-3` en móvil para que logo y pestañas quepan en
                            // la primera fila; `coarse:py-2` sube el objetivo
                            // táctil sin tocar el ratón.
                            'rounded-full px-3 py-1.5 text-sm font-medium transition-colors duration-200',
                            'coarse:py-2 sm:px-4',
                            activeTab === tab.id ? 'bg-surface-muted text-fg' : 'text-fg-subtle hover:text-fg'
                        )}
                    >
                        {tab.label}
                    </button>
                ))}
            </div>
            {/* Los mandos del evento. En un teléfono no caben en la barra —logo,
                pestañas y el menú pasan de 500 px—, así que por debajo de `sm`
                salen de ella y flotan justo debajo, sobre el mapa, a ancho
                completo. Siguen viviendo aquí y
                no en App para no duplicar el grupo ni sus props: basta con
                sacarlos del flujo. `pointer-events` sigue el patrón de ChatBar —
                la franja no debe robarle al mapa el arrastre entre pastilla y
                pastilla. */}
            <div className="pointer-events-none absolute inset-x-4 top-full mt-3 flex min-w-0 items-center gap-2 sm:pointer-events-auto sm:static sm:mt-0 sm:w-auto">
                {/* La emergencia es el ámbito de todo —mapa, grafo y los dos
                    chats—, así que su selector no se esconde en ninguna pestaña. */}
                <div className="pointer-events-auto min-w-0 flex-1 sm:flex-initial">
                    <EventMenu
                        events={events}
                        selectedId={selectedEventId}
                        summary={eventSummary}
                        onSelect={onSelectEvent}
                    />
                </div>
                {/* La música es global: el botón sigue visible en todas las pestañas. */}
                <div className="pointer-events-auto">
                    <MusicToggle />
                </div>
            </div>
            {/* La firma de quién hizo esto cierra la barra por la derecha. Va
                fuera del grupo de mandos a propósito: no es un mando del evento
                y en móvil no debe irse con ellos a flotar sobre el mapa. */}
            <FailFastLink />
        </header>
    )
}
