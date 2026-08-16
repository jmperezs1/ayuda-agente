import { useCallback, useEffect, useRef, useState } from 'react'
import { askAgent, toolLabel } from '@/lib/agentApi'
import { useUserLocation } from '@/lib/useUserLocation'
import type { AgentName } from '@/lib/agentApi'
import type { ChatMessage } from '@/types/connection'

/**
 * El turno terminado: qué contestó el agente y a qué apuntó por el camino.
 *
 * Los ids llegan en el orden en que los devolvieron las herramientas —lo más cerca o lo más
 * urgente primero— y ya como texto: las vistas no ven números del backend.
 */
export interface AgentAnswer {
    /** Lo que se preguntó. Es el último recurso cuando la respuesta no señala nada. */
    question: string
    /** La respuesta entera, ya concatenada. Vacía si el turno se cayó antes del primer token. */
    text: string
    actorIds: string[]
    requirementIds: string[]
}

interface UseAgentChatOptions {
    /** Emergencia a la que se acota todo; null mientras el grafo aún carga. */
    eventId: number | null
    /** Primera burbuja, la que ya está en pantalla antes de escribir nada; sin ella la conversación arranca vacía. */
    greeting?: ChatMessage
    agent?: AgentName
    /** Se llama una vez al acabar el turno: la vista mueve mapa o grafo hacia lo que se dijo. */
    onAnswer?: (answer: AgentAnswer) => void
    /** Qué añadir cuando no hay backend, para que la demo siga diciendo algo útil. */
    offlineReply?: string
}

/** Motivo del fallo en una frase. Un servidor caído llega como un inglés sin contexto. */
function describeFailure(error: unknown): string {
    if (error instanceof TypeError || (error instanceof Error && error.message === 'Network Error')) {
        return 'no pude conectar con el servidor'
    }
    return error instanceof Error ? error.message : 'error desconocido'
}

/**
 * La conversación con el agente: burbujas, borrador e hilo.
 *
 * Vive aquí y no en las vistas porque Mapa y Conexiones mantienen cada una su
 * conversación pero necesitan exactamente el mismo comportamiento de red.
 *
 * El `thread_id` que devuelve el servidor se reenvía en cada turno siguiente,
 * que es lo que hace que "¿y quién puede surtirlo?" se entienda sin repetir qué
 * era "lo". Un solo turno a la vez por hilo: mandar otro mensaje mientras el
 * primero responde entrelazaría dos escrituras sobre la misma conversación.
 *
 * La ubicación se pide aquí y no en las vistas por lo mismo: es parte de la
 * petición, y las dos pestañas la mandarían igual. Se adjunta la última conocida
 * en cada turno, así que "cerca de mí" sigue al coordinador si se mueve.
 *
 * Lo que el turno señaló se acumula aquí y se entrega de una vez en `onAnswer`, al final.
 * Los ids van llegando a mitad de turno, pero el agente todavía puede llamar a otra
 * herramienta y cambiar de asunto: una vista que se moviera con cada `focus` seguiría su
 * razonamiento en vez de su respuesta.
 */
export function useAgentChat({
    eventId,
    greeting,
    agent = 'coordination',
    onAnswer,
    offlineReply
}: UseAgentChatOptions) {
    const [messages, setMessages] = useState<ChatMessage[]>(greeting === undefined ? [] : [greeting])
    const [draft, setDraft] = useState('')
    const [isStreaming, setIsStreaming] = useState(false)

    // Null mientras el navegador no la haya dado; el turno se manda igual.
    const location = useUserLocation()

    const threadIdRef = useRef<string | null>(null)
    const abortRef = useRef<AbortController | null>(null)
    const messageIdRef = useRef(0)
    const nextMessageId = () => `mensaje-${messageIdRef.current++}`

    // Lo que va señalando el turno en curso. En refs y no en estado: no se pinta nada con
    // ello, y guardarlo en estado repintaría el chat por cada evento del flujo.
    const answerRef = useRef('')
    const focusRef = useRef<{ actors: string[]; requirements: string[] }>({
        actors: [],
        requirements: []
    })

    /** Añade ids nuevos conservando el orden de llegada; el mismo actor puede salir en varias herramientas. */
    const rememberFocus = (into: string[], ids: number[]) => {
        for (const id of ids.map(String)) {
            if (!into.includes(id)) {
                into.push(id)
            }
        }
    }

    // Salir de la vista corta la petición en curso.
    useEffect(() => () => abortRef.current?.abort(), [])

    // Actualiza la burbuja del agente en curso, siempre la última de la lista.
    const updateLast = useCallback((change: (message: ChatMessage) => ChatMessage) => {
        setMessages((prev) => [...prev.slice(0, -1), change(prev[prev.length - 1])])
    }, [])

    /** Añade burbujas ya escritas, sin red: el hook sigue siendo el dueño de los ids. */
    const appendMessages = useCallback((...added: Omit<ChatMessage, 'id'>[]) => {
        setMessages((prev) => [...prev, ...added.map((message) => ({ ...message, id: nextMessageId() }))])
    }, [])

    const send = useCallback(async () => {
        const text = draft.trim()
        if (text === '' || isStreaming || eventId === null) {
            return
        }

        // La burbuja del agente entra vacía: es la que `updateLast` va llenando.
        setMessages((prev) => [
            ...prev,
            { id: nextMessageId(), from: 'user', text },
            { id: nextMessageId(), from: 'agent', text: '', pending: true }
        ])
        setDraft('')
        setIsStreaming(true)
        // Lo del turno anterior no dice nada de este.
        answerRef.current = ''
        focusRef.current = { actors: [], requirements: [] }

        const controller = new AbortController()
        abortRef.current = controller

        try {
            await askAgent({
                agent,
                eventId,
                message: text,
                threadId: threadIdRef.current,
                location,
                signal: controller.signal,
                onEvent: (event) => {
                    switch (event.type) {
                        case 'start':
                        case 'done':
                            threadIdRef.current = event.thread_id
                            break
                        // Los tokens son fragmentos, no frases: se concatenan tal cual.
                        case 'token':
                            answerRef.current += event.text
                            updateLast((message) => ({ ...message, text: message.text + event.text }))
                            break
                        // No se pinta: es a qué apuntar cuando acabe el turno.
                        case 'focus':
                            rememberFocus(focusRef.current.actors, event.actors)
                            rememberFocus(focusRef.current.requirements, event.requirements)
                            break
                        case 'tool_start':
                            updateLast((message) => ({ ...message, step: toolLabel(event.name) }))
                            break
                        // `ok: false` no es un fallo: el agente se corrige en la llamada siguiente.
                        case 'tool_end':
                            updateLast((message) => ({ ...message, step: undefined }))
                            break
                        // Se conserva lo que ya había llegado: media respuesta
                        // vale más que un mensaje de error a secas.
                        case 'error':
                            updateLast((message) => ({
                                ...message,
                                text:
                                    message.text === ''
                                        ? `El agente se detuvo: ${event.error}`
                                        : `${message.text}\n\nEl agente se detuvo: ${event.error}`
                            }))
                            break
                    }
                }
            })
        } catch (error) {
            // Solo llega aquí lo que falla antes de abrir el flujo; un fallo
            // posterior es un evento `error` y conserva los tokens recibidos.
            if (!controller.signal.aborted) {
                const excuse = offlineReply === undefined ? '' : ` ${offlineReply}`
                updateLast((message) => ({
                    ...message,
                    text: `No pude hablar con el agente (${describeFailure(error)}).${excuse}`
                }))
            }
        } finally {
            abortRef.current = null
            // Un turno abortado no informa a nadie: quien lo cortó ya no está mirando.
            if (!controller.signal.aborted) {
                updateLast((message) => ({ ...message, step: undefined, pending: false }))
                setIsStreaming(false)
                // También tras un fallo: media respuesta, o la propia pregunta, todavía
                // pueden señalar un punto, y moverse hacia él sigue informando.
                onAnswer?.({
                    question: text,
                    text: answerRef.current,
                    actorIds: focusRef.current.actors,
                    requirementIds: focusRef.current.requirements
                })
            }
        }
    }, [agent, draft, eventId, isStreaming, location, offlineReply, onAnswer, updateLast])

    return { messages, appendMessages, draft, setDraft, send, isStreaming }
}
