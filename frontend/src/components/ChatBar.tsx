import { ArrowUp } from 'lucide-react'
import { useEffect, useRef } from 'react'
import { Markdown } from '@/components/Markdown'
import { cn } from '@/lib/utils'
import type { Ref } from 'react'
import type { ChatMessage } from '@/types/connection'

interface ChatBarProps {
    messages: ChatMessage[]
    draft: string
    onDraftChange: (value: string) => void
    onSend: () => void
    /** Un turno abierto: se bloquea el envío hasta que el agente termine. */
    isStreaming?: boolean
    /** Por qué la barra no acepta nada ahora mismo; se enseña en el placeholder. */
    blockedReason?: string
    inputRef?: Ref<HTMLInputElement>
}

/**
 * Qué enseñar mientras la respuesta se está escribiendo: el paso en curso si lo
 * hay, y si no una espera. Medio minuto de silencio con un cursor parado se lee
 * como una pantalla rota; los nombres de las herramientas, no.
 */
function pendingLabel(message: ChatMessage): string | null {
    if (message.step !== undefined) {
        return message.step
    }
    return message.pending === true && message.text === '' ? 'Pensando…' : null
}

/**
 * Barra de chat compartida por Mapa y Conexiones. Presentational: pinta
 * burbujas y reporta el borrador y el envío; la conversación con el agente vive
 * en `useAgentChat`. Nunca llama a ninguna red ni API.
 *
 * Lo que escribe el agente viene en Markdown y se pinta como Markdown (ver
 * `Markdown`); lo que escribe el usuario es texto tal cual.
 *
 * `z-[1100]` porque sobre el mapa debe pintarse encima de los controles de
 * Leaflet (z 1000) y debajo del panel lateral (z 1200); en Conexiones cualquier
 * z bajo serviría, pero uno solo vale para ambos. El degradado de fondo es
 * `pointer-events-none` para no bloquear el mapa (ni el fondo del grafo) fuera
 * de la columna interactiva.
 */
export function ChatBar({
    messages,
    draft,
    onDraftChange,
    onSend,
    isStreaming,
    blockedReason,
    inputRef
}: ChatBarProps) {
    // Un solo motivo por el que la barra no acepta nada, y su texto. Escribir en
    // un campo que no reacciona se lee como una pantalla rota.
    const blockedBy = blockedReason ?? (isStreaming === true ? 'El agente está respondiendo…' : null)

    // Centinela al final de la lista: al llegar una burbuja nueva, el scroll
    // baja solo. `block: 'nearest'` evita que arrastre el resto de la página.
    const endRef = useRef<HTMLDivElement>(null)
    useEffect(() => {
        endRef.current?.scrollIntoView({ behavior: 'smooth', block: 'nearest' })
    }, [messages])

    // El input se deshabilita mientras el agente responde, y deshabilitarlo le
    // quita el foco: sin devolverlo, lo que el usuario escriba después de la
    // respuesta se pierde. Por eso hace falta una ref propia además de la de fuera.
    const localRef = useRef<HTMLInputElement>(null)
    const wasStreaming = useRef(false)
    useEffect(() => {
        if (wasStreaming.current && isStreaming !== true) {
            localRef.current?.focus()
        }
        wasStreaming.current = isStreaming === true
    }, [isStreaming])

    const attachInput = (node: HTMLInputElement | null) => {
        localRef.current = node
        if (typeof inputRef === 'function') {
            inputRef(node)
        } else if (inputRef !== undefined && inputRef !== null) {
            inputRef.current = node
        }
    }

    return (
        <div className="from-page via-page/80 pointer-events-none absolute inset-x-0 bottom-0 z-[1100] bg-gradient-to-t to-transparent px-4 pt-10 pb-4">
            <div className="pointer-events-auto mx-auto flex w-full max-w-3xl flex-col gap-3">
                <div
                    role="log"
                    aria-label="Conversación"
                    // `mb-12` reserva la banda a la que sube el botón de feedback
                    // cuando esta columna llega a las esquinas (ver FeedbackButton):
                    // sin ella las burbujas quedarían debajo del botón.
                    // `chat-scroll` da la barra de scroll fina del compositor
                    // (ver index.css); va aquí y no global para no tocar la de
                    // Leaflet ni la del panel lateral.
                    className="chat-scroll mb-12 flex max-h-[40dvh] flex-col gap-2 overflow-y-auto overscroll-contain pr-1 xl:mb-0"
                >
                    {messages.map((message) => {
                        const label = pendingLabel(message)
                        return (
                            <div
                                key={message.id}
                                className={cn(
                                    'flex',
                                    message.from === 'user' ? 'justify-end' : 'justify-start'
                                )}
                            >
                                <div
                                    className={cn(
                                        // `break-words` para que un enlace largo
                                        // sin espacios no estire la burbuja fuera
                                        // de la columna.
                                        'max-w-[85%] rounded-2xl border px-3.5 py-2.5 text-sm leading-relaxed break-words',
                                        message.from === 'user'
                                            ? // Lo que escribe el usuario es texto tal cual: sus
                                              // saltos de línea se respetan y sus asteriscos no
                                              // son formato.
                                              'border-[#7b9cff]/40 bg-[#7b9cff]/20 whitespace-pre-wrap text-gray-100'
                                            : 'border-[#444444] bg-[#1F2023] text-gray-100'
                                    )}
                                >
                                    {label !== null ? (
                                        <span className="mb-1 block animate-pulse text-xs text-gray-400">
                                            {label}
                                        </span>
                                    ) : null}
                                    {message.from === 'user' ? (
                                        message.text
                                    ) : (
                                        <Markdown text={message.text} />
                                    )}
                                </div>
                            </div>
                        )
                    })}
                    <div ref={endRef} aria-hidden />
                </div>

                <form
                    onSubmit={(event) => {
                        event.preventDefault()
                        onSend()
                    }}
                    // El anillo va en `focus-within` y no en `focus`: el cursor
                    // cae en el campo de dentro, pero lo que el usuario ve
                    // enfocado es la tarjeta entera.
                    //
                    // El anillo del turno en curso existe porque enviar
                    // deshabilita el campo, y eso saca a la tarjeta de
                    // `:focus-within`: sin él el borde volvería al gris plano en
                    // el mismo instante del envío. El anillo escribe
                    // `--tw-ring-shadow` y la sombra `--tw-shadow`, así que
                    // Tailwind compone las dos. Se ata a `isStreaming` y no a
                    // `blockedBy`: el acento dice "hay un turno corriendo", no
                    // "la barra está cerrada por otro motivo".
                    className={cn(
                        'rounded-3xl border border-[#444444] bg-[#1F2023] p-2 shadow-[0_8px_30px_rgba(0,0,0,0.24)] transition-all duration-300 ease-in-out',
                        'focus-within:border-blue-400 focus-within:ring-2 focus-within:ring-blue-400/20',
                        isStreaming === true && 'border-[#7b9cff] ring-2 ring-[#7b9cff]/20'
                    )}
                >
                    <input
                        ref={attachInput}
                        type="text"
                        value={draft}
                        onChange={(event) => onDraftChange(event.target.value)}
                        // Un turno a la vez por conversación: mandar otro mensaje
                        // mientras el agente responde entrelaza las dos escrituras.
                        disabled={blockedBy !== null}
                        placeholder={blockedBy ?? '¿Cómo quieres ayudar o recibir ayuda?'}
                        aria-label="Mensaje"
                        // `text-base` son los 16 px que Safari en iOS necesita
                        // para no hacer zoom al enfocar el campo y descuadrar la
                        // pantalla entera: no bajarlo a `text-sm`.
                        className="min-h-[44px] w-full bg-transparent px-3 py-2.5 text-base text-gray-100 outline-none placeholder:text-gray-400 focus-visible:ring-0 focus-visible:outline-none disabled:cursor-not-allowed disabled:opacity-50"
                    />
                    <div className="flex items-center justify-end gap-2 p-0 pt-2">
                        <button
                            type="submit"
                            disabled={draft.trim() === '' || blockedBy !== null}
                            aria-label="Enviar"
                            className={cn(
                                'inline-flex size-8 shrink-0 items-center justify-center rounded-full font-medium transition-all duration-200 focus-visible:outline-none disabled:pointer-events-none disabled:opacity-50',
                                // 44 px de objetivo táctil con el dedo; el diseño de escritorio son 32.
                                'coarse:size-11',
                                draft.trim() !== ''
                                    ? 'bg-white text-[#1F2023] hover:bg-white/80'
                                    : 'bg-transparent text-[#9CA3AF] hover:bg-gray-600/30 hover:text-[#D1D5DB]'
                            )}
                        >
                            <ArrowUp
                                className={cn(
                                    'h-4 w-4',
                                    draft.trim() !== '' ? 'text-[#1F2023]' : 'text-inherit'
                                )}
                                aria-hidden
                            />
                        </button>
                    </div>
                </form>
            </div>
        </div>
    )
}
