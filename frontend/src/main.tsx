import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
import { Analytics } from '@vercel/analytics/react'
import App from './App.tsx'
import { VideoLoop } from './components/VideoLoop.tsx'
import './styles/index.css'

// Rutas que sólo muestran el vídeo en bucle. Un `switch` sobre la ruta basta:
// no hay navegación entre pantallas, así que meter un router sería más
// dependencia que ayuda.
const VIDEO_ROUTES = ['/cami', '/virreyvalley']

const path = window.location.pathname.replace(/\/+$/, '').toLowerCase() || '/'
const isVideoRoute = VIDEO_ROUTES.includes(path)

createRoot(document.getElementById('root')!).render(
    <StrictMode>
        {isVideoRoute ? <VideoLoop /> : <App />}
        <Analytics />
    </StrictMode>
)
