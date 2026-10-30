import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
import '@fontsource/ibm-plex-sans/400.css'
import '@fontsource/ibm-plex-sans/500.css'
import '@fontsource/ibm-plex-sans/600.css'
import '@fontsource/ibm-plex-mono/400.css'
import './index.css'
import App from './App.tsx'
import { VerifyPage } from './Installation'

// A scanned QR label opens /v/<token>: the public check, without signing in.
const token = /^\/v\/([0-9a-f]{32})\/?$/.exec(window.location.pathname)?.[1]

createRoot(document.getElementById('root')!).render(
  <StrictMode>
    {token ? <VerifyPage token={token} /> : <App />}
  </StrictMode>,
)
