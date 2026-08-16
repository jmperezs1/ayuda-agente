/* ==========================================================================
   Slug del backend → texto en español.

   La API manda slugs estables a propósito y deja las etiquetas al frontend:
   son decisiones de producto y las lee gente en Colombia. Este archivo es el
   único sitio donde se escriben.
   ========================================================================== */
import type { ContactKind, Hazard } from '@/types/graph'

export const HAZARD_LABELS: Record<Hazard, string> = {
    earthquake: 'Sismo',
    flood: 'Inundación',
    landslide: 'Deslizamiento',
    cyclone: 'Ciclón',
    wildfire: 'Incendio forestal',
    windstorm: 'Vendaval',
    other: 'Emergencia'
}

export const CONTACT_LABELS: Record<ContactKind, string> = {
    handle: 'Perfil',
    phone: 'Teléfono',
    whatsapp: 'WhatsApp',
    email: 'Correo',
    website: 'Sitio web',
    form: 'Formulario',
    payment: 'Cuenta de pago',
    street_address: 'Dirección'
}

/** Fecha corta para una fila de menú: '14 ago 2026'. */
export function formatDate(isoDate: string): string {
    return new Date(isoDate).toLocaleDateString('es-CO', {
        day: 'numeric',
        month: 'short',
        year: 'numeric'
    })
}

/** Antigüedad de una publicación: 'hace 2 h', 'hace 3 d'. */
export function formatAge(isoDate: string): string {
    const minutes = Math.round((Date.now() - new Date(isoDate).getTime()) / 60000)
    if (minutes < 60) {
        return `hace ${Math.max(1, minutes)} min`
    }
    if (minutes < 60 * 24) {
        return `hace ${Math.round(minutes / 60)} h`
    }
    return `hace ${Math.round(minutes / (60 * 24))} d`
}
