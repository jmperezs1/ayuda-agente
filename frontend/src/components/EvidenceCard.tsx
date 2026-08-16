import { ExternalLink } from 'lucide-react'
import { formatAge } from '@/data/labels'
import { PLATFORMS } from '@/data/platforms'
import type { ObservationBrief } from '@/types/graph'

interface EvidenceCardProps {
    observation: ObservationBrief
}

/**
 * La publicación de la que salió una necesidad: la respuesta a "¿y tú cómo
 * sabes eso?", que es la primera pregunta que se le hace a un sistema que lee
 * redes sociales durante una emergencia.
 *
 * La imagen sale de `url` (nuestra copia) y solo si no hay, de `source_url`:
 * las del origen van firmadas y caducan en horas, así que en una publicación de
 * ayer se verían rotas. Hoy `url` siempre es null, así que casi siempre se
 * acaba en el marcador de texto.
 */
export function EvidenceCard({ observation }: EvidenceCardProps) {
    const platform = PLATFORMS[observation.platform]
    const image = observation.media.find((item) => item.url !== null || item.source_url !== null)
    const imageUrl = image?.url ?? image?.source_url ?? null

    return (
        <li className="border-line bg-page rounded-lg border p-3">
            <div className="flex items-center gap-2.5">
                {/* Marco común para los logos: el de X es un disco negro que,
                    suelto, se pierde sobre el fondo oscuro. */}
                <span className="border-line bg-surface-muted grid size-7 shrink-0 place-items-center rounded-md border">
                    <img
                        src={platform?.logo}
                        alt=""
                        width={18}
                        height={18}
                        loading="lazy"
                        decoding="async"
                        className="size-[18px] object-contain"
                    />
                </span>
                <div className="min-w-0 flex-1">
                    <p className="text-fg truncate text-sm font-medium">
                        {observation.author.handle === ''
                            ? observation.author.name
                            : observation.author.handle}
                    </p>
                    <p className="text-fg-subtle truncate text-xs">
                        {platform?.label ?? observation.platform} · {formatAge(observation.posted_at)}
                    </p>
                </div>
            </div>

            {observation.text !== '' ? (
                <p className="text-fg-muted mt-2.5 text-sm leading-relaxed">{observation.text}</p>
            ) : null}

            {imageUrl !== null ? (
                <img
                    src={imageUrl}
                    alt={image?.alt_text ?? ''}
                    loading="lazy"
                    decoding="async"
                    className="border-line mt-2.5 max-h-48 w-full rounded-md border object-cover"
                />
            ) : null}

            {observation.permalink !== '' ? (
                <a
                    href={observation.permalink}
                    target="_blank"
                    rel="noreferrer noopener"
                    className="text-brand mt-2.5 inline-flex items-center gap-1.5 text-xs font-medium"
                >
                    Ver publicación
                    <ExternalLink size={12} strokeWidth={2} aria-hidden />
                </a>
            ) : null}
        </li>
    )
}
