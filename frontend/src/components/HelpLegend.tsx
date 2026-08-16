interface HelpLegendProps {
    /** La fila de "Conexión" solo tiene sentido donde hay hilos: el grafo. */
    showConnection?: boolean
}

/**
 * Leyenda de colores compartida por Mapa y Conexiones: rojo necesita ayuda,
 * verde la ofrece. `z-[1100]` por la misma razón que la ChatBar: sobre el mapa
 * debe pintarse encima de los panes y controles de Leaflet; en Conexiones
 * cualquier z bajo serviría, pero uno solo vale para ambos.
 */
export function HelpLegend({ showConnection = false }: HelpLegendProps) {
    return (
        // `top-16` en móvil deja pasar la fila de mandos que ahí flota debajo de
        // la cabecera. El ancho máximo solo muerde en pantallas estrechas y solo
        // en el grafo: la fila de "Conexión" es larga y sin él llegaría hasta el
        // control de zoom, que ahí se pinta arriba a la derecha; acotada, envuelve.
        <div className="border-line bg-surface/85 absolute top-16 left-4 z-[1100] flex max-w-[16rem] flex-col gap-2 rounded-xl border px-4 py-3 backdrop-blur sm:top-4 sm:max-w-none">
            <div className="flex items-center gap-2.5">
                <span className="bg-need size-2.5 shrink-0 rounded-full" aria-hidden />
                <span className="text-fg-muted text-xs">Necesita ayuda</span>
            </div>
            <div className="flex items-center gap-2.5">
                <span className="bg-offer size-2.5 shrink-0 rounded-full" aria-hidden />
                <span className="text-fg-muted text-xs">Ofrece ayuda</span>
            </div>
            {showConnection ? (
                <div className="flex items-center gap-2.5">
                    <span
                        className="bg-fg h-0.5 w-4 shrink-0 rounded-full shadow-[0_0_6px_var(--color-fg)]"
                        aria-hidden
                    />
                    <span className="text-fg-muted text-xs">Conexión — más brillo, mejor coincidencia</span>
                </div>
            ) : null}
        </div>
    )
}
