import { Check, ChevronDown } from 'lucide-react'
import { useCallback, useEffect, useRef, useState } from 'react'
import { cn } from '@/lib/utils'
import type { ReactNode } from 'react'

export interface SelectMenuItem {
    id: string
    title: string
    /** Segunda línea de la fila; opcional porque no todo dato tiene subtítulo. */
    subtitle?: string
}

interface SelectMenuProps {
    /** Etiqueta accesible del menú, p. ej. 'Emergencias'. */
    label: string
    /** Texto del botón cuando no hay nada seleccionado. */
    placeholder: string
    icon: ReactNode
    items: SelectMenuItem[]
    selectedId: string | null
    onSelect: (id: string) => void
}

/**
 * Desplegable de selección única, compartido por el menú de emergencias y el de
 * ubicaciones. Presentational: pinta filas y reporta la elegida.
 *
 * Un listener en `document` es la única forma de enterarse de un clic que no
 * pasa por este subárbol. El de teclado va en fase de captura y detiene la
 * propagación para que un Escape cierre solo lo que está más arriba —este
 * menú— y no también el panel lateral, que escucha Escape en `document`.
 */
export function SelectMenu({ label, placeholder, icon, items, selectedId, onSelect }: SelectMenuProps) {
    const [isOpen, setIsOpen] = useState(false)
    const containerRef = useRef<HTMLDivElement>(null)

    const selected = items.find((item) => item.id === selectedId) ?? null

    useEffect(() => {
        if (!isOpen) {
            return
        }

        function handlePointerDown(event: PointerEvent) {
            if (!containerRef.current?.contains(event.target as Node)) {
                setIsOpen(false)
            }
        }

        function handleKeyDown(event: KeyboardEvent) {
            if (event.key === 'Escape') {
                event.stopPropagation()
                setIsOpen(false)
            }
        }

        document.addEventListener('pointerdown', handlePointerDown)
        document.addEventListener('keydown', handleKeyDown, true)
        return () => {
            document.removeEventListener('pointerdown', handlePointerDown)
            document.removeEventListener('keydown', handleKeyDown, true)
        }
    }, [isOpen])

    const handleToggle = useCallback(() => setIsOpen((open) => !open), [])

    const handleSelect = useCallback(
        (id: string) => {
            onSelect(id)
            setIsOpen(false)
        },
        [onSelect]
    )

    return (
        // `static` en móvil a propósito: así el desplegable de abajo no se ancla
        // a este botón —que ahí ocupa media fila y dejaría medio menú fuera de
        // pantalla— sino al ancestro posicionado más cercano, que es la fila de
        // mandos de AppHeader; el menú cae a su mismo ancho. La contención en el
        // DOM no cambia, así que el clic fuera sigue midiéndose con `containerRef`.
        <div ref={containerRef} className="static sm:relative">
            <button
                type="button"
                onClick={handleToggle}
                aria-haspopup="menu"
                aria-expanded={isOpen}
                aria-label={label}
                // `w-full` para que el botón siga el ancho que le den y no el de
                // su texto: un <button> encoge a su contenido aunque sea `flex`,
                // y sin esto el título largo desbordaría en vez de recortarse.
                // El máximo lo sigue poniendo `max-w-[16rem]`.
                className="border-line text-fg-muted hover:bg-surface hover:text-fg coarse:py-2.5 flex w-full max-w-[16rem] items-center gap-2 rounded-full border px-3 py-1.5 text-xs font-medium transition-colors duration-200"
            >
                {icon}
                <span className="truncate">{selected === null ? placeholder : selected.title}</span>
                <ChevronDown
                    size={13}
                    strokeWidth={2}
                    aria-hidden
                    className={cn('shrink-0 transition-transform duration-200', isOpen ? 'rotate-180' : '')}
                />
            </button>

            {isOpen ? (
                <div
                    role="menu"
                    aria-label={label}
                    // En móvil cuelga a ancho completo de la fila de mandos (ver
                    // el `static` de arriba); desde `sm` vuelve a colgar del botón.
                    className="border-line bg-surface absolute top-full right-0 left-0 mt-2 max-h-[60dvh] w-auto overflow-y-auto overscroll-contain rounded-xl border shadow-2xl shadow-black/50 sm:left-auto sm:w-80"
                >
                    {items.map((item) => (
                        <button
                            key={item.id}
                            type="button"
                            role="menuitemradio"
                            aria-checked={item.id === selectedId}
                            onClick={() => handleSelect(item.id)}
                            className="hover:bg-surface-muted flex w-full items-center justify-between gap-3 px-4 py-2.5 text-left transition-colors duration-200"
                        >
                            <span className="min-w-0">
                                <span className="text-fg block truncate text-sm font-medium">
                                    {item.title}
                                </span>
                                {item.subtitle !== undefined ? (
                                    <span className="text-fg-subtle block truncate text-xs">
                                        {item.subtitle}
                                    </span>
                                ) : null}
                            </span>
                            {item.id === selectedId ? (
                                <Check
                                    size={15}
                                    strokeWidth={2}
                                    aria-hidden
                                    className="text-brand shrink-0"
                                />
                            ) : null}
                        </button>
                    ))}
                </div>
            ) : null}
        </div>
    )
}
