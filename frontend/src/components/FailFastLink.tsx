/**
 * Interruptor de la firma. En `false` la barra queda como antes de que esto
 * existiera: el componente no pinta nada y no ocupa sitio. Ponlo en `true` para
 * enseñarla; no hay que tocar nada más.
 */
const MOSTRAR_FIRMA = false

/**
 * Firma de Fail Fast en el extremo derecho de la barra, cerrándola.
 *
 * Es un enlace y no un `button` porque lleva fuera del sitio: así el navegador
 * da gratis el "abrir en pestaña nueva", el clic con el botón central y la
 * vista previa del destino. `rel` acompaña al `target` para no ceder el
 * `window.opener` al destino.
 *
 * El aviso al pasar el ratón se pinta a mano en vez de con `title` porque el
 * nativo tarda un segundo largo en salir y no se puede peinar. `aria-label`
 * lleva el mismo texto, así que un lector de pantalla lo anuncia igual sin que
 * el globo tenga que existir para él (de ahí el `aria-hidden`).
 */
export function FailFastLink() {
    const tooltip = 'This product was made with love by the Fail Fast team.'

    if (!MOSTRAR_FIRMA) return null

    return (
        // `ml-auto` solo por debajo de `sm`: ahí la barra envuelve y en un
        // teléfono estrecho esta firma cae sola a una segunda fila, donde
        // `justify-between` la dejaría pegada a la izquierda. Empujándola con el
        // margen se queda en su esquina, envuelva o no. De `sm` en adelante
        // manda el `justify-between` de la barra y el margen estorba.
        <div className="group relative ml-auto shrink-0 sm:ml-0">
            <a
                href="https://www.failfast.ai/"
                target="_blank"
                rel="noreferrer noopener"
                aria-label={tooltip}
                className="focus-visible:outline-brand block rounded-sm focus-visible:outline-2 focus-visible:outline-offset-4"
            >
                {/* width/height son el tamaño intrínseco real del archivo:
                    reservan el espacio correcto y evitan el salto de layout. */}
                <img
                    src="/logo-white.png"
                    alt="Fail Fast"
                    width={4636}
                    height={1115}
                    className="h-5 w-auto sm:h-6"
                />
            </a>
            {/* Cuelga del enlace sin ocupar sitio ni robar el puntero: si tuviera
                `pointer-events`, al asomarse bajo el cursor se comería el hover
                del propio enlace y parpadearía.

                Se ancla por la derecha porque el enlace vive en esa esquina de
                la barra: creciendo hacia la izquierda el globo nunca se sale de
                la pantalla. */}
            <span
                role="tooltip"
                aria-hidden
                className="border-line bg-surface text-fg-muted pointer-events-none absolute top-full right-0 z-10 mt-2 rounded-md border px-2.5 py-1.5 text-xs whitespace-nowrap opacity-0 shadow-lg shadow-black/50 transition-opacity duration-200 group-focus-within:opacity-100 group-hover:opacity-100"
            >
                {tooltip}
            </span>
        </div>
    )
}
