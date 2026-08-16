import { Volume2, VolumeX } from 'lucide-react'
import { useBackgroundMusic } from '@/lib/useBackgroundMusic'

export function MusicToggle() {
    const { playing, toggle } = useBackgroundMusic()
    const label = playing ? 'Pausar la música' : 'Reproducir la música'

    return (
        // `data-music-toggle` lo usa el autoarranque global para ceder el
        // control al botón cuando la interacción ocurre sobre él.
        <button
            type="button"
            data-music-toggle
            onClick={toggle}
            aria-pressed={playing}
            aria-label={label}
            title={label}
            className="border-line text-fg-muted hover:bg-surface hover:text-fg coarse:size-10 flex size-8 shrink-0 items-center justify-center rounded-full border transition-colors duration-200"
        >
            {playing ? (
                <Volume2 size={14} strokeWidth={2} aria-hidden />
            ) : (
                <VolumeX size={14} strokeWidth={2} aria-hidden />
            )}
        </button>
    )
}
