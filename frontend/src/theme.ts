import { useCallback, useEffect, useState } from 'react'

export type Mode = 'light' | 'dark' | 'auto'
export const MODES: [Mode, string][] = [['light', 'Light'], ['dark', 'Dark'], ['auto', 'Match system']]
const KEY = 'smet.theme'

/** Saved per browser only (a convenience, not account data); storage can be blocked, so never required. */
function load(): Mode {
  try {
    const v = localStorage.getItem(KEY)
    return MODES.some(([m]) => m === v) ? (v as Mode) : 'auto'
  } catch {
    return 'auto'
  }
}

const systemDark = () => window.matchMedia?.('(prefers-color-scheme: dark)').matches ?? false
const apply = (m: Mode) => { document.documentElement.dataset.theme = m === 'auto' ? (systemDark() ? 'dark' : 'light') : m }

export function useTheme() {
  const [mode, setModeState] = useState(load)
  const setMode = useCallback((m: Mode) => {
    setModeState(m)
    apply(m)
    try { localStorage.setItem(KEY, m) } catch { /* private window: lasts this visit only */ }
  }, [])
  useEffect(() => {
    apply(mode)
    if (mode !== 'auto') return
    const media = window.matchMedia?.('(prefers-color-scheme: dark)')
    const follow = () => apply(mode)
    media?.addEventListener('change', follow)
    return () => media?.removeEventListener('change', follow)
  }, [mode])
  return { mode, setMode }
}
