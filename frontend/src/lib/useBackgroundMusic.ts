import { useCallback, useEffect, useState } from 'react'

const SONG_URL = '/song.m4a'

// Una única instancia de Audio a nivel de módulo: aunque React monte los
// componentes más de una vez (StrictMode, HMR), nunca puede haber dos
// pistas sonando a la vez.
let audio: HTMLAudioElement | null = null
let autoplayAttempted = false

function getAudio(): HTMLAudioElement {
    if (audio === null) {
        audio = new Audio(SONG_URL)
        audio.preload = 'auto'
    }
    return audio
}

function startOnInteraction(event: Event) {
    // Sobre el botón de música manda el botón: si arrancáramos aquí, el click
    // que llega justo después pausaría la canción recién iniciada.
    if (event.target instanceof Element && event.target.closest('[data-music-toggle]')) {
        return
    }
    cancelAutoStart()
    void getAudio().play()
}

function cancelAutoStart() {
    window.removeEventListener('pointerdown', startOnInteraction)
    window.removeEventListener('keydown', startOnInteraction)
}

/**
 * Los navegadores bloquean el audio con sonido hasta la primera interacción
 * del usuario (política de autoplay). Intentamos reproducir de inmediato;
 * si el navegador lo rechaza, arrancamos en la primera interacción real.
 */
function ensureAutoplay() {
    if (autoplayAttempted) {
        return
    }
    autoplayAttempted = true
    getAudio()
        .play()
        .catch(() => {
            window.addEventListener('pointerdown', startOnInteraction)
            window.addEventListener('keydown', startOnInteraction)
        })
}

export function useBackgroundMusic() {
    const [playing, setPlaying] = useState(() => audio !== null && !audio.paused)

    useEffect(() => {
        const element = getAudio()
        const handlePlay = () => setPlaying(true)
        const handlePause = () => setPlaying(false)
        element.addEventListener('play', handlePlay)
        element.addEventListener('pause', handlePause)
        setPlaying(!element.paused)
        ensureAutoplay()
        return () => {
            element.removeEventListener('play', handlePlay)
            element.removeEventListener('pause', handlePause)
        }
    }, [])

    const toggle = useCallback(() => {
        const element = getAudio()
        // Una decisión explícita del usuario anula el autoarranque pendiente.
        cancelAutoStart()
        if (element.paused) {
            // Tras terminar la canción, play() vuelve a empezar desde el inicio.
            void element.play()
        } else {
            element.pause()
        }
    }, [])

    return { playing, toggle }
}
