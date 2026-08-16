import { useEffect, useState } from 'react'

type Status = 'idle' | 'loading' | 'ready' | 'error'

/**
 * Un dato que se pide al abrirlo, no al arrancar.
 *
 * Lo usan el panel (los contactos de un actor) y las tarjetas (las
 * publicaciones de una necesidad): el mismo efecto con su aborto y su estado,
 * escrito una vez. Con `id` en null no pide nada — así el que llama expresa
 * "todavía no" sin romper el orden de los hooks.
 */
export function useResource<T>(load: (id: string) => Promise<T>, id: string | null) {
    const [data, setData] = useState<T | null>(null)
    const [status, setStatus] = useState<Status>('idle')

    useEffect(() => {
        if (id === null) {
            setData(null)
            setStatus('idle')
            return
        }

        const controller = new AbortController()
        setStatus('loading')
        // Nada de conservar lo anterior: sería el detalle de otro id en pantalla.
        setData(null)

        load(id)
            .then((loaded) => {
                if (!controller.signal.aborted) {
                    setData(loaded)
                    setStatus('ready')
                }
            })
            .catch(() => {
                if (!controller.signal.aborted) {
                    setStatus('error')
                }
            })

        return () => controller.abort()
        // `load` se asume estable (una función de módulo), como las de apiClient.
    }, [id, load])

    return { data, status }
}
