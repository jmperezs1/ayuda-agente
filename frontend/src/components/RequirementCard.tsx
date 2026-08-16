import { ChevronDown } from 'lucide-react'
import { useState } from 'react'
import { EvidenceCard } from '@/components/EvidenceCard'
import { fetchRequirement } from '@/lib/apiClient'
import { useResource } from '@/lib/useResource'
import { cn } from '@/lib/utils'
import type { Urgency } from '@/types/graph'
import type { PlaceRequirement } from '@/types/place'

/**
 * Cada urgencia en el vocabulario del resto de la interfaz: un punto de color y
 * una palabra, exactamente como la leyenda del mapa, sobre la misma tarjeta
 * plana de línea fina que usan los contactos y las publicaciones.
 *
 * La escala es la del rojo que ya significa algo aquí —el del punto que pide
 * ayuda—, apagándose hasta el gris. Nada de tonos nuevos: en esta paleta el
 * color es la excepción, y una necesidad crítica solo destaca si las demás no
 * compiten. Solo esas dos tiñen además la línea de la tarjeta.
 */
const URGENCY: Record<Urgency, { label: string; card: string; dot: string; text: string }> = {
    critical: { label: 'Crítica', card: 'border-need/40', dot: 'bg-need', text: 'text-need' },
    high: { label: 'Alta', card: 'border-need/20', dot: 'bg-need/70', text: 'text-need/85' },
    medium: { label: 'Media', card: 'border-line', dot: 'bg-fg-faint', text: 'text-fg-subtle' },
    low: { label: 'Baja', card: 'border-line', dot: 'bg-fg-faint/45', text: 'text-fg-faint' }
}

interface RequirementCardProps {
    requirement: PlaceRequirement
    /**
     * Si las publicaciones se ven desde el principio. El panel lo enciende solo
     * en la primera tarjeta —la misma que resume su cabecera—: las
     * publicaciones son la respuesta a "¿y tú cómo sabes eso?", y todas detrás
     * de un clic no las abre nadie.
     *
     * Una por punto y no todas a propósito: seis necesidades abiertas serían
     * seis peticiones al abrir el panel, que es justo lo que evita pedir la
     * evidencia al desplegar.
     */
    defaultOpen?: boolean
}

/**
 * Una necesidad u oferta abierta del punto, con las publicaciones de las que
 * salió.
 *
 * La evidencia se pide al desplegar, no al abrir el panel: un punto con seis
 * necesidades serían seis peticiones para algo que casi nunca se mira entero.
 * Y llega como lista porque lo es: una publicación puede producir varias
 * necesidades y una necesidad puede estar respaldada por varias publicaciones,
 * así que enseñar "la" publicación acierta tan a menudo como falla.
 */
export function RequirementCard({ requirement, defaultOpen = false }: RequirementCardProps) {
    const [isOpen, setIsOpen] = useState(defaultOpen)
    const urgency = URGENCY[requirement.urgency]
    const { data, status } = useResource(fetchRequirement, isOpen ? requirement.id : null)

    return (
        // Misma tarjeta que en el resto del panel; lo único que cambia con la
        // urgencia es el tono de la línea, y solo cuando hay prisa.
        <li className={cn('bg-page rounded-lg border', urgency.card)}>
            <button
                type="button"
                onClick={() => setIsOpen((open) => !open)}
                aria-expanded={isOpen}
                className="w-full p-3.5 text-left"
            >
                <div className="flex items-start justify-between gap-3">
                    <p className="text-fg min-w-0 text-sm font-medium">{requirement.resource}</p>
                    {/* Punto y palabra, como en `HelpLegend`: el color se lee de
                        lejos y el texto lo dice para quien no lo distingue. */}
                    <span
                        className={cn(
                            'flex shrink-0 items-center gap-1.5 pt-0.5 text-[11px] font-medium',
                            urgency.text
                        )}
                    >
                        <span className={cn('size-1.5 shrink-0 rounded-full', urgency.dot)} aria-hidden />
                        {urgency.label}
                    </span>
                </div>
                {requirement.outstanding !== null ? (
                    <p className="text-fg-muted mt-1.5 text-xs">
                        {requirement.kind === 'needed' ? 'Pendiente' : 'Disponible'}:{' '}
                        {requirement.outstanding}
                    </p>
                ) : null}
                {requirement.detail !== '' ? (
                    <p className="text-fg-muted mt-3 text-sm leading-relaxed">{requirement.detail}</p>
                ) : null}
                <span className="text-fg-subtle mt-3 flex items-center gap-1.5 text-xs font-medium">
                    <ChevronDown
                        size={13}
                        strokeWidth={2}
                        aria-hidden
                        className={cn('transition-transform duration-200', isOpen && 'rotate-180')}
                    />
                    {isOpen ? 'Ocultar publicaciones' : 'Ver publicaciones'}
                </span>
            </button>

            {/* Región propia, con su encabezado y separada de la tarjeta: las
                publicaciones son la prueba de lo de arriba, no una nota al pie
                suya. El encabezado repite la palabra del botón a propósito —el
                botón es la acción, esto es la etiqueta de lo que quedó abierto. */}
            {isOpen ? (
                <div className="border-line border-t px-3.5 pt-3 pb-3.5">
                    <h4 className="text-fg-subtle mb-2.5 text-[11px] font-semibold tracking-[0.09em] uppercase">
                        Publicaciones
                    </h4>
                    {status === 'loading' ? (
                        <p className="text-fg-subtle animate-pulse text-xs">Buscando publicaciones…</p>
                    ) : null}
                    {status === 'error' ? (
                        <p className="text-fg-subtle text-xs">No pude cargar las publicaciones.</p>
                    ) : null}
                    {status === 'ready' && data !== null ? (
                        data.evidence.length === 0 ? (
                            <p className="text-fg-subtle text-xs">Sin publicaciones asociadas todavía.</p>
                        ) : (
                            <ul role="list" className="flex flex-col gap-2.5">
                                {data.evidence.map((observation) => (
                                    <EvidenceCard key={observation.id} observation={observation} />
                                ))}
                            </ul>
                        )
                    ) : null}
                </div>
            ) : null}
        </li>
    )
}
