import { MapPin, X } from 'lucide-react'
import { useEffect, useState, type ReactNode } from 'react'
import { ContactList } from '@/components/ContactList'
import { RequirementCard } from '@/components/RequirementCard'
import { fetchActor } from '@/lib/apiClient'
import { useResource } from '@/lib/useResource'
import { cn } from '@/lib/utils'
import type { HelpKind, Place } from '@/types/place'

/**
 * Cómo se presenta cada lado del punto: el que pide y el que da.
 *
 * `label` y el color son literalmente los de `HelpLegend`, a propósito: quien
 * viene de la leyenda del mapa lee aquí la misma etiqueta del mismo color, y no
 * dos maneras distintas de decir lo mismo. Mismo patrón que `URGENCY` en
 * `RequirementCard`: la tabla arriba, el condicional fuera del JSX.
 */
const KIND: Record<HelpKind, { label: string; heading: string; className: string }> = {
    needed: { label: 'Necesita ayuda', heading: 'Necesidades abiertas', className: 'text-need' },
    offered: { label: 'Ofrece ayuda', heading: 'Ofertas abiertas', className: 'text-offer' }
}

interface PanelSectionProps {
    title: string
    /** A la derecha del título, sobre su misma línea base. Sin él, no hay contador. */
    count?: number
    className?: string
    children: ReactNode
}

/**
 * Una región con título del panel.
 *
 * Existe para que todas compartan un único escalón tipográfico —mismo tamaño,
 * mismo peso, mismo espaciado— en vez de repetir la clase del encabezado en
 * cada sección y que se separen en el primer retoque. El contador va suelto a
 * la derecha y no entre paréntesis dentro del título: un número alineado se lee
 * de un vistazo y no alarga la línea del encabezado.
 */
function PanelSection({ title, count, className, children }: PanelSectionProps) {
    return (
        <section className={cn('flex flex-col gap-3', className)}>
            <div className="flex items-baseline justify-between gap-3">
                <h3 className="text-fg-subtle text-[11px] font-semibold tracking-[0.09em] uppercase">
                    {title}
                </h3>
                {count === undefined ? null : (
                    <span className="text-fg-faint text-[11px] font-medium tabular-nums">{count}</span>
                )}
            </div>
            {children}
        </section>
    )
}

interface SidePanelProps {
    place: Place | null
    onClose: () => void
}

/**
 * Overlay drawer. Presentational — it takes the selected place and a way to
 * close, and knows nothing about Leaflet. It sits outside the map container,
 * so clicks inside it never reach the map.
 */
export function SidePanel({ place, onClose }: SidePanelProps) {
    const isOpen = place !== null

    // Keep the last place on screen while the panel slides out, so the content
    // does not vanish mid-transition. Adjusted during render, not in an effect.
    // Depende de que `place` sea siempre el MISMO objeto de la lista de puntos:
    // uno compuesto en render dispararía este setState en cada pasada.
    const [lastPlace, setLastPlace] = useState(place)
    if (place !== null && place !== lastPlace) {
        setLastPlace(place)
    }

    // Los contactos se piden al abrir el punto, no con el grafo: doscientos
    // actores con todos sus teléfonos es una carga que nadie mira entera.
    // Van del punto abierto, no del retenido: al cerrar no hay a quién pedirle.
    const { data: actor, status: actorStatus } = useResource(fetchActor, place?.id ?? null)

    // A document listener is the only way to catch Escape while focus is still
    // on the map marker that opened the panel.
    useEffect(() => {
        if (!isOpen) {
            return
        }

        function handleKeyDown(event: KeyboardEvent) {
            if (event.key === 'Escape') {
                onClose()
            }
        }

        document.addEventListener('keydown', handleKeyDown)
        return () => document.removeEventListener('keydown', handleKeyDown)
    }, [isOpen, onClose])

    const kind = lastPlace === null ? null : KIND[lastPlace.kind]

    return (
        <aside
            role="complementary"
            aria-label="Detalle del reporte"
            inert={!isOpen}
            className={cn(
                'border-line bg-surface absolute inset-y-0 right-0 z-[1200] flex w-full flex-col border-l',
                'transition-transform duration-300 ease-out sm:w-[380px]',
                isOpen ? 'translate-x-0' : 'pointer-events-none translate-x-full'
            )}
        >
            {lastPlace !== null && kind !== null ? (
                <>
                    {/* Cabecera fija. `shrink-0` para que no ceda alto cuando la
                        lista de publicaciones crece. */}
                    <header className="border-line flex shrink-0 flex-col gap-3.5 border-b px-5 pt-4 pb-5">
                        <div className="flex items-start justify-between gap-3">
                            {/* Lo primero que hay que saber de un punto es de qué
                                lado está. Va arriba, con el color de su marcador
                                en el mapa, y no compite con el nombre: es una
                                etiqueta pequeña, no un segundo titular. */}
                            <span
                                className={cn(
                                    'inline-flex items-center gap-2 pt-1.5 text-[11px] font-semibold tracking-[0.09em] uppercase',
                                    kind.className
                                )}
                            >
                                <span className="size-1.5 shrink-0 rounded-full bg-current" aria-hidden />
                                {kind.label}
                            </span>
                            <button
                                type="button"
                                onClick={onClose}
                                aria-label="Cerrar panel"
                                className="border-line text-fg-subtle hover:bg-surface-muted hover:text-fg coarse:size-10 grid size-8 shrink-0 place-items-center rounded-md border transition-colors duration-200"
                            >
                                <X size={15} strokeWidth={2} aria-hidden />
                            </button>
                        </div>

                        {/* Identidad, resumen y ubicación, en ese orden y con tres
                            pesos distintos. El titular es el nombre del actor —lo
                            que identifica el punto y lo que se busca al volver a
                            él—; el resumen derivado es descripción y se lee
                            debajo, en prosa y no en versales; la ubicación es un
                            dato de apoyo y baja al último escalón. */}
                        <div className="min-w-0">
                            <h2 className="text-fg text-xl leading-snug font-semibold tracking-tight text-balance">
                                {lastPlace.name}
                            </h2>
                            <p className="text-fg-muted mt-2 text-sm leading-relaxed">{lastPlace.title}</p>
                            <p className="text-fg-faint mt-3 flex items-center gap-1.5 text-xs">
                                <MapPin size={12} strokeWidth={2} aria-hidden className="shrink-0" />
                                <span className="truncate">{lastPlace.city}</span>
                            </p>
                        </div>
                    </header>

                    {/* Única zona con scroll.
                        · `min-h-0` es obligatorio: un hijo flex no encoge por
                          debajo de su contenido sin él, y el panel entero
                          desbordaría en vez de hacer scroll.
                        · `key` remonta el contenedor al cambiar de punto, así el
                          scroll vuelve arriba sin efectos ni refs. */}
                    <div
                        key={lastPlace.id}
                        className="flex min-h-0 flex-1 flex-col gap-6 overflow-y-auto overscroll-contain px-5 py-5"
                    >
                        <PanelSection title={kind.heading} count={lastPlace.requirements.length}>
                            {/* `role="list"` explícito: Preflight aplica
                                `list-style: none` y Safari/VoiceOver pierde la
                                semántica de lista sin él. */}
                            <ul role="list" className="flex flex-col gap-3">
                                {lastPlace.requirements.map((requirement, index) => (
                                    <RequirementCard
                                        key={requirement.id}
                                        requirement={requirement}
                                        // La primera es la que resume la cabecera:
                                        // abrirla deja sus publicaciones a la vista
                                        // nada más abrir el punto, que es lo que
                                        // sostiene todo lo demás. Solo la primera,
                                        // no todas: ver más abajo.
                                        defaultOpen={index === 0}
                                    />
                                ))}
                            </ul>
                        </PanelSection>

                        {/* Separada por una línea: lo de arriba es lo que pasa y
                            de dónde se sabe; esto es lo que se hace después. */}
                        <PanelSection title="Cómo contactarles" className="border-line border-t pt-6">
                            {actorStatus === 'loading' ? (
                                <p className="text-fg-subtle animate-pulse text-xs">Buscando contactos…</p>
                            ) : null}
                            {actorStatus === 'error' ? (
                                <p className="text-fg-subtle text-xs">No pude cargar los contactos.</p>
                            ) : null}
                            {actorStatus === 'ready' && actor !== null ? (
                                <ContactList contacts={actor.contacts} />
                            ) : null}
                            <p className="text-fg-faint text-[11px] leading-relaxed">
                                Nada se envía solo: cada enlace lo abre una persona.
                            </p>
                        </PanelSection>
                    </div>
                </>
            ) : null}
        </aside>
    )
}
