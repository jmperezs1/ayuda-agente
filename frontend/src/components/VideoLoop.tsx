import { Volume2, VolumeX } from 'lucide-react'
import { useCallback, useEffect, useRef, useState } from 'react'

/**
 * Un vídeo vertical para las pantallas verticales y el horizontal para el
 * resto. La forma de la ventana es el criterio, y no si el aparato es un
 * móvil: una ventana estrecha en el escritorio también quiere el vertical, y
 * un móvil girado, el horizontal.
 */
const PORTRAIT_QUERY = '(max-aspect-ratio: 1/1)'

/** Reacciona a girar el móvil o redimensionar la ventana, no sólo al montar. */
function usePortraitViewport() {
    const [portrait, setPortrait] = useState(() => window.matchMedia(PORTRAIT_QUERY).matches)

    useEffect(() => {
        const query = window.matchMedia(PORTRAIT_QUERY)
        const update = () => setPortrait(query.matches)
        query.addEventListener('change', update)
        update()
        return () => query.removeEventListener('change', update)
    }, [])

    return portrait
}

/**
 * Pantalla completa con el vídeo en bucle infinito y la canción sonando
 * encima, también en bucle. Cada uno con su propia duración: el vídeo dura
 * 6 s y la canción 12 s, así que se desfasan a propósito, sin sincronizar.
 *
 * El vídeo va mudo siempre y el sonido de la página es la canción: dejar las
 * dos pistas sonando a la vez sería ruido, no música.
 */
export function VideoLoop() {
    const audioRef = useRef<HTMLAudioElement>(null)
    const videoRef = useRef<HTMLVideoElement>(null)
    const portrait = usePortraitViewport()
    const [playing, setPlaying] = useState(false)
    // Una pausa pedida por el usuario se respeta; las que impone el navegador no.
    const userPaused = useRef(false)

    /**
     * Los navegadores bloquean el audio con sonido hasta la primera
     * interacción del usuario (política de autoplay). Intentamos arrancar de
     * inmediato; si el navegador lo rechaza, la canción entra en el primer
     * clic o tecla que ocurra en la página.
     */
    useEffect(() => {
        const audio = audioRef.current
        if (!audio) return

        const start = () => {
            void audio.play().catch(() => {})
        }

        const startOnInteraction = (event: Event) => {
            // Sobre el botón de sonido manda el botón: si arrancáramos aquí, el
            // click que llega justo después pausaría la canción recién iniciada.
            if (event.target instanceof Element && event.target.closest('[data-sound-toggle]')) {
                return
            }
            start()
        }

        // Chrome suspende el audio de las pestañas que están en segundo plano,
        // así que abrir el enlace en una pestaña sin mirar deja la canción
        // parada. Al volver a mirarla se reanuda, salvo que la pausa la haya
        // pedido el usuario: sólo así el bucle es de verdad infinito.
        const resumeWhenVisible = () => {
            if (!document.hidden && !userPaused.current) start()
        }

        const cancelAutoStart = () => {
            window.removeEventListener('pointerdown', startOnInteraction)
            window.removeEventListener('keydown', startOnInteraction)
        }

        // Los oyentes se arman antes de intentar reproducir, y es el propio
        // evento `play` quien los retira: si el navegador deja la promesa de
        // `play()` pendiente en lugar de rechazarla (pestaña en segundo plano),
        // armarlos en el `catch` los dejaría sin armar para siempre y el primer
        // clic del usuario no haría nada.
        window.addEventListener('pointerdown', startOnInteraction)
        window.addEventListener('keydown', startOnInteraction)
        document.addEventListener('visibilitychange', resumeWhenVisible)
        audio.addEventListener('play', cancelAutoStart)
        start()

        return () => {
            cancelAutoStart()
            document.removeEventListener('visibilitychange', resumeWhenVisible)
            audio.removeEventListener('play', cancelAutoStart)
        }
    }, [])

    /**
     * `loop` solo no basta en el móvil. Con `loop` puesto el navegador nunca
     * dispara `ended` —reinicia por dentro—, así que cuando algo corta la
     * reproducción el bucle se queda parado y no hay evento de final donde
     * engancharse. Lo que sí llega siempre es `pause`, y en el móvil llega
     * mucho: iOS pausa el vídeo al bloquear la pantalla, al cambiar de app, al
     * entrar en modo de bajo consumo y cuando el decodificador se queda sin
     * datos. Reanudar en `pause` es lo que hace el bucle infinito de verdad.
     *
     * El `ended` se conserva por si algún navegador ignora el `loop`.
     */
    useEffect(() => {
        const video = videoRef.current
        if (!video) return

        // Como propiedad y no sólo como atributo: es lo que mira iOS para
        // decidir si permite el autoarranque, y React no siempre lo refleja.
        video.muted = true

        const play = () => {
            void video.play().catch(() => {})
        }

        // Con la pestaña oculta el navegador vuelve a pausar en cuanto
        // arranque: insistir ahí sería pelearse con él y gastar batería.
        const resume = () => {
            if (!document.hidden) play()
        }

        const restart = () => {
            video.currentTime = 0
            play()
        }

        video.addEventListener('pause', resume)
        video.addEventListener('ended', restart)
        document.addEventListener('visibilitychange', resume)
        play()

        return () => {
            video.removeEventListener('pause', resume)
            video.removeEventListener('ended', restart)
            document.removeEventListener('visibilitychange', resume)
        }
    }, [portrait])

    const toggleSound = useCallback(() => {
        const audio = audioRef.current
        if (!audio) return
        if (audio.paused) {
            userPaused.current = false
            void audio.play().catch(() => {})
        } else {
            userPaused.current = true
            audio.pause()
        }
    }, [])

    const label = playing ? 'Silenciar la canción' : 'Activar la canción'

    return (
        <div className="relative h-dvh w-full overflow-hidden bg-black">
            {/* Cambiar los `<source>` de sitio no recarga el vídeo —haría falta
                un `load()` a mano—, así que la `key` fuerza un elemento nuevo al
                girar el móvil y el navegador vuelve a elegir fuente. */}
            <video
                key={portrait ? 'portrait' : 'landscape'}
                ref={videoRef}
                autoPlay
                loop
                muted
                playsInline
                preload="auto"
                className="h-full w-full object-contain"
            >
                {portrait ? (
                    /* Una sola fuente para el móvil, y en H.264: el vertical
                       original es HEVC, que Chrome de Android no decodifica, y
                       pesa 21 MB con la tabla de índices al final del fichero,
                       así que el navegador tenía que descargarlo entero antes
                       de empezar. Por datos móviles eso es lo que rompía el
                       bucle: sonaba una vez a duras penas y ya no volvía. */
                    <source src="/video-mobile-h264.mp4" type='video/mp4; codecs="avc1.640028"' />
                ) : (
                    <>
                        {/* El original es HEVC 4K y sólo Safari lo decodifica:
                            en Chrome y Firefox la página quedaba en negro. El
                            navegador se queda con la primera fuente que dice
                            poder reproducir, así que Safari se lleva el 4K y el
                            resto el H.264 de 1080p. */}
                        <source src="/video.mp4" type='video/mp4; codecs="hvc1"' />
                        <source src="/video-h264.mp4" type='video/mp4; codecs="avc1.640028"' />
                    </>
                )}
            </video>
            {/* El estado del botón sigue a la pista, no al revés: así también
                refleja las pausas que no vienen de un click (autoarranque
                aceptado, sistema que corta el audio). */}
            <audio
                ref={audioRef}
                src="/cami.WAV"
                autoPlay
                loop
                preload="auto"
                onPlay={() => setPlaying(true)}
                onPause={() => setPlaying(false)}
            />
            <button
                type="button"
                data-sound-toggle
                onClick={toggleSound}
                aria-pressed={playing}
                aria-label={label}
                title={label}
                className="absolute right-4 bottom-4 flex size-10 items-center justify-center rounded-full border border-white/20 bg-black/50 text-white/80 backdrop-blur transition-colors duration-200 hover:bg-black/70 hover:text-white"
            >
                {playing ? (
                    <Volume2 size={16} strokeWidth={2} aria-hidden />
                ) : (
                    <VolumeX size={16} strokeWidth={2} aria-hidden />
                )}
            </button>
        </div>
    )
}
