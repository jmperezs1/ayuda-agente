/**
 * Emparejamiento entre un punto que necesita ayuda y uno que la ofrece.
 * `strength` (0..1) es el `score` que calculó el backend.
 */
export interface Connection {
    id: string
    neededId: string
    offeredId: string
    strength: number
}

/** Coordenadas dentro del viewBox del grafo de conexiones. */
export interface GraphPoint {
    x: number
    y: number
}

/** Burbuja del chat. */
export interface ChatMessage {
    id: string
    from: 'user' | 'agent'
    text: string
    /** Herramienta que el agente está usando ahora mismo, ya traducida a una frase. */
    step?: string
    /** True mientras llegan tokens: la burbuja aún no está completa. */
    pending?: boolean
}
