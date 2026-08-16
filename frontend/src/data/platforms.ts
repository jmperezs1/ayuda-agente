import type { Platform } from '@/types/graph'

/**
 * Red social → etiqueta y logo. Único punto que conoce las rutas de
 * `public/logos/`, que son mapas de bits y no se pueden recolorear con CSS.
 */
export const PLATFORMS: Record<Platform, { label: string; logo: string }> = {
    instagram: { label: 'Instagram', logo: '/logos/Instagram.png' },
    x: { label: 'X', logo: '/logos/X.svg.webp' },
    facebook: { label: 'Facebook', logo: '/logos/Facebook.webp' },
    tiktok: { label: 'TikTok', logo: '/logos/TikTok.jpg' }
}
