import { useEffect, useRef, useState } from 'react'
import { NavLink, Outlet, useLocation } from 'react-router'
import { KeyRound, LogOut, Menu, X } from 'lucide-react'
import { api, type Me } from './api'
import { NAV } from './nav'
import { MODES, type Mode } from './theme'
import { Dialog, Logo } from './ui'
import { ChangePasswordForm } from './Auth'

// A path that another menu entry extends ("/stock" vs "/stock/moves") lights up only on itself.
const PARENTS = new Set(NAV.flatMap((g) => g.items).map((i) => i.path).filter((p) => NAV.some((g) => g.items.some((i) => i.path.startsWith(`${p}/`)))))

function UserMenu({ me, mode, onMode, onSignOut, onPassword }: {
  me: Me; mode: Mode; onMode: (m: Mode) => void; onSignOut: () => void; onPassword: () => void
}) {
  const [open, setOpen] = useState(false)
  const box = useRef<HTMLDivElement>(null)
  useEffect(() => {
    if (!open) return
    const close = (e: MouseEvent | KeyboardEvent) => {
      if (e instanceof KeyboardEvent ? e.key === 'Escape' : !box.current?.contains(e.target as Node)) setOpen(false)
    }
    document.addEventListener('mousedown', close)
    document.addEventListener('keydown', close)
    return () => { document.removeEventListener('mousedown', close); document.removeEventListener('keydown', close) }
  }, [open])
  const initials = me.user.name.split(/\s+/).map((w) => w[0]).join('').slice(0, 2).toUpperCase()
  // A stable hue per person, from their id: the same tint wherever they appear.
  const hue = [...me.user.id].reduce((h, c) => (h * 31 + c.charCodeAt(0)) % 360, 7)
  return (
    <div className="user-menu" ref={box}>
      <button className="user-row" aria-expanded={open} aria-haspopup="true" onClick={() => setOpen(!open)}>
        <span className="avatar" aria-hidden="true" style={{ '--hue': hue } as React.CSSProperties}>{initials}</span>
        <span className="user-text">{me.user.name}<span className="muted small">{me.roles[0] ?? 'No roles yet'}{me.roles.length > 1 ? ` +${me.roles.length - 1}` : ''}</span></span>
      </button>
      {open && (
        <div className="menu" role="group" aria-label="Your account">
          <div className="menu-section">
            <p className="menu-name">{me.user.name}</p>
            <p className="muted small">{me.user.email}</p>
            <p className="muted small">{me.roles.join(', ') || 'No roles yet'}</p>
          </div>
          <fieldset className="menu-section">
            <legend>Appearance</legend>
            {MODES.map(([m, label]) => (
              <label key={m} className="radio">
                <input type="radio" name="theme" checked={mode === m} onChange={() => onMode(m)} /> {label}
              </label>
            ))}
          </fieldset>
          <button className="menu-item" onClick={() => { setOpen(false); onPassword() }}><KeyRound size={16} aria-hidden="true" /> Change password</button>
          <button className="menu-item" onClick={onSignOut}><LogOut size={16} aria-hidden="true" /> Sign out</button>
        </div>
      )}
    </div>
  )
}

export function Shell({ me, mode, onMode, onMe, onSignOut }: {
  me: Me; mode: Mode; onMode: (m: Mode) => void; onMe: (me: Me) => void; onSignOut: () => void
}) {
  const [navOpen, setNavOpen] = useState(false)
  const [password, setPassword] = useState(false)
  const [error, setError] = useState('')
  const location = useLocation()
  // oxlint-disable-next-line react/set-state-in-effect -- the mobile menu closes when the page changes
  useEffect(() => setNavOpen(false), [location.pathname])
  async function signOut() {
    try {
      await api('/auth/logout', { method: 'POST' })
      onSignOut()
    } catch (e) {
      // Leaving the app here would hide a session that is still valid on the server.
      setError(e instanceof Error ? e.message : 'Sign-out failed.')
    }
  }
  const groups = NAV.map((g) => ({ ...g, items: g.items.filter((i) => !i.perm || i.perm.split('|').some((x) => me.permissions.includes(x))) }))
    .filter((g) => g.items.length)
  return (
    <div className="shell">
      <a className="skip-link" href="#main">Skip to content</a>
      {/* Phone and tablet: a 48px bar with the menu; the sidebar opens as a full-screen sheet. */}
      <header className="mobile-bar">
        <span className="brand"><Logo /> SMET Operations</span>
        <button className="btn btn-ghost" aria-expanded={navOpen} aria-controls="sidenav" onClick={() => setNavOpen(!navOpen)}>
          {navOpen ? <X size={20} aria-hidden="true" /> : <Menu size={20} aria-hidden="true" />}
          <span>{navOpen ? 'Close' : 'Menu'}</span>
        </button>
      </header>
      <aside id="sidenav" className={navOpen ? 'sidebar open' : 'sidebar'}>
        <div className="sidebar-head">
          <span className="brand"><Logo /> SMET Operations</span>
          <span className="scope">{me.company.name}</span>
        </div>
        <nav aria-label="Main">
          {groups.map((g) => (
            <div key={g.group} className="nav-group">
              <p className="nav-group-label">{g.group}</p>
              <ul>
                {g.items.map(({ path, label, icon: Icon }) => (
                  <li key={path}><NavLink to={path} end={path === '/' || PARENTS.has(path)}><Icon size={16} aria-hidden="true" />{label}</NavLink></li>
                ))}
              </ul>
            </div>
          ))}
        </nav>
        <UserMenu me={me} mode={mode} onMode={onMode} onSignOut={signOut} onPassword={() => setPassword(true)} />
      </aside>
      <main id="main" className="sheet" tabIndex={-1}>
        {error && <p className="form-error" role="alert">{error}</p>}
        <Outlet />
      </main>
      {password && (
        <Dialog title="Change password" onClose={() => setPassword(false)}>
          <ChangePasswordForm onDone={(m) => { onMe(m); setPassword(false) }} />
        </Dialog>
      )}
    </div>
  )
}
