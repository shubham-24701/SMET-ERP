import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
import '@fontsource/ibm-plex-sans/400.css'
import '@fontsource/ibm-plex-sans/500.css'
import '@fontsource/ibm-plex-sans/600.css'
import '@fontsource/ibm-plex-mono/400.css'
import './index.css'
import App from './App.tsx'
import { PublicPage } from './Public'

// The public portal, without signing in: /v/<token> from a scanned QR label, /verify to type a serial.
const token = /^\/v\/([^/]+)\/?$/.exec(window.location.pathname)?.[1]
const isPublic = token !== undefined || /^\/verify\/?$/.test(window.location.pathname)

createRoot(document.getElementById('root')!).render(
  <StrictMode>
    {isPublic ? <PublicPage token={token} /> : <App />}
  </StrictMode>,
)
