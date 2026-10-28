import { useCallback, useEffect, useState } from 'react'
import { BrowserRouter, Link, Navigate, Route, Routes } from 'react-router'
import { api, ApiError, type Me } from './api'
import { ForcedPasswordPage, LoginPage, SetupPage } from './Auth'
import { AuditPage, CompanyPage, RolesPage, SystemPage, UsersPage } from './Admin'
import { Dashboard } from './pages'
import { Shell } from './Shell'
import { PageState } from './ui'
import { MeContext } from './lib'
import { useTheme } from './theme'

/** A page the user's roles don't open: a plain message, not a broken screen (the server refuses anyway). */
function Allowed({ perm, me, children }: { perm: string; me: Me; children: React.ReactNode }) {
  return me.permissions.includes(perm) ? children
    : <PageState kind="empty" title="You don't have access to this page" message="Ask your system administrator if you need it for your work." action={<Link to="/">Back to overview</Link>} />
}

export default function App() {
  const [me, setMe] = useState<Me | null | undefined>(undefined) // undefined = still loading
  const [needsSetup, setNeedsSetup] = useState(false)
  const [error, setError] = useState('')
  const { mode, setMode } = useTheme()

  const load = useCallback(async () => {
    try {
      const { needs_setup } = await api<{ needs_setup: boolean }>('/auth/setup-status')
      setNeedsSetup(needs_setup)
      setMe(needs_setup ? null : await api<Me>('/auth/me'))
      setError('')
    } catch (e) {
      if (e instanceof ApiError && e.status === 401) setMe(null)
      else setError(e instanceof Error ? e.message : 'Something went wrong.')
    }
  }, [])

  useEffect(() => {
    // oxlint-disable-next-line react/set-state-in-effect -- loading from the API on mount is syncing with an external system
    void load()
    const expired = () => setMe(null)
    window.addEventListener('session-expired', expired)
    return () => window.removeEventListener('session-expired', expired)
  }, [load])

  if (error) return <PageState kind="error" title="Can't reach SMET Operations" message={error} action={<button className="btn btn-primary" onClick={load}>Try again</button>} />
  if (me === undefined) return <PageState kind="loading" title="Loading…" />
  if (me === null) return needsSetup ? <SetupPage onDone={(m) => { setNeedsSetup(false); setMe(m) }} /> : <LoginPage onDone={setMe} />
  if (me.user.must_change_password) return <ForcedPasswordPage onDone={setMe} />

  return (
    <MeContext value={me}>
      <BrowserRouter>
        <Routes>
          <Route element={<Shell me={me} mode={mode} onMode={setMode} onMe={setMe} onSignOut={() => { window.history.replaceState(null, '', '/'); setMe(null) }} />}>
            <Route index element={<Dashboard />} />
            <Route path="admin/users" element={<Allowed perm="users.view" me={me}><UsersPage /></Allowed>} />
            <Route path="admin/roles" element={<Allowed perm="roles.view" me={me}><RolesPage /></Allowed>} />
            <Route path="admin/audit" element={<Allowed perm="audit.view" me={me}><AuditPage /></Allowed>} />
            <Route path="admin/company" element={<Allowed perm="settings.view" me={me}><CompanyPage onChange={load} /></Allowed>} />
            <Route path="admin/system" element={<Allowed perm="system.view" me={me}><SystemPage /></Allowed>} />
            <Route path="admin" element={<Navigate to="/admin/users" replace />} />
            <Route path="*" element={<PageState kind="empty" title="Page not found" message="The address may be mistyped, or the page has moved." action={<Link to="/">Back to overview</Link>} />} />
          </Route>
        </Routes>
      </BrowserRouter>
    </MeContext>
  )
}
