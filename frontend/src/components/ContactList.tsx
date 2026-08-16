import { CONTACT_LABELS } from '@/data/labels'
import type { Contact } from '@/types/graph'

interface ContactListProps {
    contacts: Contact[]
}

/** A dónde lleva un contacto, cuando lleva a algún sitio. */
function contactHref(contact: Contact): string | null {
    switch (contact.kind) {
        case 'whatsapp':
            return `https://wa.me/${contact.value.replace(/\D/g, '')}`
        case 'email':
            return `mailto:${contact.value}`
        case 'phone':
            return `tel:${contact.value}`
        case 'website':
        case 'form':
            return contact.value.startsWith('http') ? contact.value : `https://${contact.value}`
        default:
            return null
    }
}

/**
 * Cómo llegar a un actor, en el orden que manda el backend (`preference_rank`:
 * menos intrusivo y más esperado primero).
 *
 * Los pagos y las direcciones postales se pintan como dato, nunca como acción:
 * son detalles, no canales, y es exactamente lo que significa que el backend
 * los excluya de `can_be_reached`.
 *
 * `times_seen` se enseña porque un número repetido en cinco publicaciones casi
 * seguro es real y uno que apareció una vez puede ser un error de extracción;
 * quien va a escribir merece saber cuál está mirando.
 */
export function ContactList({ contacts }: ContactListProps) {
    if (contacts.length === 0) {
        return <p className="text-fg-subtle text-xs">Sin datos de contacto todavía.</p>
    }

    return (
        <ul role="list" className="flex flex-col gap-2">
            {contacts.map((contact) => {
                const href = contact.reachable ? contactHref(contact) : null
                const label = CONTACT_LABELS[contact.kind] ?? contact.kind
                const value = (
                    <span className="min-w-0">
                        <span className="text-fg-subtle block text-[11px] tracking-wide uppercase">
                            {label}
                            {contact.times_seen > 1 ? ` · visto ${contact.times_seen} veces` : ''}
                        </span>
                        <span className="text-fg block truncate text-sm">{contact.value}</span>
                    </span>
                )

                return (
                    <li key={contact.id} className="border-line bg-page rounded-lg border px-3 py-2.5">
                        {href === null ? (
                            value
                        ) : (
                            <a
                                href={href}
                                target="_blank"
                                rel="noreferrer noopener"
                                className="hover:text-brand block transition-colors duration-200"
                            >
                                {value}
                            </a>
                        )}
                    </li>
                )
            })}
        </ul>
    )
}
