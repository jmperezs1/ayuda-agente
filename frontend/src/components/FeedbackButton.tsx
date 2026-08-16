import { Baby } from 'lucide-react'

/**
 * Llamada al feedback, anclada abajo a la izquierda sobre las dos pestañas.
 *
 * Es un enlace y no un `button` porque lleva a otra URL: así el navegador da
 * gratis el "abrir en pestaña nueva", el clic con el botón central y la vista
 * previa del destino. `z-[1150]` lo deja sobre el degradado del chat (1100) y
 * por debajo del panel de detalle (1200), que sí debe taparlo.
 *
 * Por debajo de 1280 px la columna del chat (max-w-3xl centrada) llega a esta
 * esquina, así que el botón sube justo por encima del formulario —y por encima
 * de la atribución del mapa, que se queda abajo del todo—. El hueco que deja
 * libre para él la conversación lo pone el `mb-12` de ChatBar.
 *
 * Los 144 px de `bottom-36` son la altura del compositor de ChatBar con el dedo
 * (8 + 44 del campo + 8 + 44 del botón + 8) más los 16 del `pb-4` de su envoltura,
 * y 16 de aire. Si el compositor cambia de alto, este número cambia con él: por
 * debajo se le mete dentro del campo y tapa lo que se escribe.
 */
export function FeedbackButton() {
    return (
        <a
            href="/cami"
            className="border-line bg-surface text-fg-muted hover:text-fg hover:border-brand/60 absolute bottom-36 left-4 z-[1150] flex items-center gap-2 rounded-full border px-4 py-2 text-sm shadow-2xl shadow-black/50 transition-colors duration-200 xl:bottom-4"
        >
            <Baby size={14} strokeWidth={2} aria-hidden />
            Cami
        </a>
    )
}
