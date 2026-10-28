import { useState } from 'react'
import { api, download } from './api'
import { useAction, useApi, useCan, useMe, useSubmit, useWhen } from './lib'
import { Dialog, Field, FormError, PageHead, PageState, Pager, SelectField, Tag, TextareaField } from './ui'

type RoleRef = { id: string; name: string }
type User = {
  id: string; name: string; email: string; active: boolean; must_change_password: boolean
  last_login_at: string | null; version: number; roles: RoleRef[]
}
type Role = {
  id: string; name: string; description: string; audience: string; template: boolean
  permissions: string[]; users: number; version: number
}
type Perm = { code: string; module: string; description: string }

const AUDIENCE: Record<string, string> = { internal: 'Staff', distributor: 'Distributor portal', customer: 'Customer portal' }

function TemporaryPassword({ name, password }: { name: string; password: string }) {
  return (
    <div className="notice notice-info" role="status">
      <p><strong>Temporary password for {name}</strong></p>
      <p><code className="secret">{password}</code></p>
      <p className="small">Shown only now. Give it to them in person or by phone, not by email. They choose their own password when they first sign in.</p>
    </div>
  )
}

function RolePicker({ roles, selected, disabled }: { roles: Role[]; selected: string[]; disabled?: boolean }) {
  const staff = roles.filter((r) => r.audience === 'internal')
  return (
    <fieldset className="checks" disabled={disabled}>
      <legend>Roles</legend>
      {staff.map((r) => (
        <label key={r.id} className="check">
          <input type="checkbox" name="role_ids" value={r.id} defaultChecked={selected.includes(r.id)} />
          <span>{r.name}<span className="hint">{r.description}</span></span>
        </label>
      ))}
    </fieldset>
  )
}

const checked = (form: HTMLFormElement, name: string) => new FormData(form).getAll(name).map(String)

function AddUser({ roles, onDone }: { roles: Role[]; onDone: () => void }) {
  const can = useCan()
  const [made, setMade] = useState<{ name: string; temporary_password: string } | null>(null)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const [fields, setFields] = useState<Record<string, string>>({})
  async function submit(e: React.FormEvent<HTMLFormElement>) {
    e.preventDefault()
    const f = e.currentTarget
    setBusy(true); setError(''); setFields({})
    try {
      setMade(await api('/users', { method: 'POST', body: { name: f.username.value, email: f.email.value, role_ids: checked(f, 'role_ids') } }))
      onDone()
    } catch (err) {
      const a = err as { message: string; fields?: Record<string, string> }
      setError(a.message); setFields(a.fields ?? {})
    } finally { setBusy(false) }
  }
  if (made) return <TemporaryPassword name={made.name} password={made.temporary_password} />
  return (
    <form onSubmit={submit} noValidate>
      <Field label="Name" name="username" required maxLength={120} error={fields.name} />
      <Field label="Email" name="email" type="email" required error={fields.email} hint="They sign in with this." />
      {can('users.edit') ? <RolePicker roles={roles} selected={[]} /> : <p className="hint">Roles can be given by someone with permission to edit users.</p>}
      {fields.role_ids && <p className="field-error">{fields.role_ids}</p>}
      <FormError message={error} />
      <button className="btn btn-primary" disabled={busy}>{busy ? 'Adding…' : 'Add user'}</button>
    </form>
  )
}

function EditUser({ user, roles, onDone }: { user: User; roles: Role[]; onDone: () => void }) {
  const me = useMe()
  const self = user.id === me.user.id
  const [reset, setReset] = useState('')
  const [fields, setFields] = useState<Record<string, string>>({})
  const action = useAction(() => {})
  function save(e: React.FormEvent<HTMLFormElement>) {
    e.preventDefault()
    const f = e.currentTarget
    const body: Record<string, unknown> = { version: user.version, name: f.username.value, reason: f.reason.value }
    if (!self) { body.role_ids = checked(f, 'role_ids'); body.active = f.active.checked }
    setFields({})
    void action.run('save', () => api(`/users/${user.id}`, { method: 'PATCH', body }).then(onDone)
      .catch((err) => { setFields(err.fields ?? {}); throw err }))
  }
  return (
    <>
      <form onSubmit={save} noValidate>
        <Field label="Name" name="username" defaultValue={user.name} required maxLength={120} error={fields.name} />
        <p className="muted small">{user.email}</p>
        {self && <p className="notice notice-warning">You can't change your own roles or switch off your own account. Ask another administrator.</p>}
        <RolePicker roles={roles} selected={user.roles.map((r) => r.id)} disabled={self} />
        {fields.role_ids && <p className="field-error">{fields.role_ids}</p>}
        <label className="check"><input type="checkbox" name="active" defaultChecked={user.active} disabled={self} /> <span>Account active<span className="hint">Switching it off signs them out at once. Their history stays.</span></span></label>
        <TextareaField label="Reason for the change" name="reason" maxLength={500} hint="Kept in the audit log." />
        <FormError message={action.error} />
        <div className="row">
          <button className="btn btn-primary" disabled={!!action.busy}>{action.busy === 'save' ? 'Saving…' : 'Save changes'}</button>
          {!self && (
            <button type="button" className="btn btn-secondary" disabled={!!action.busy}
              onClick={() => action.run('reset', async () => setReset((await api<{ temporary_password: string }>(`/users/${user.id}/reset-password`, { method: 'POST' })).temporary_password),
                `Issue a new temporary password for ${user.name}? They are signed out everywhere.`)}>
              Reset password
            </button>
          )}
        </div>
      </form>
      {reset && <TemporaryPassword name={user.name} password={reset} />}
    </>
  )
}

type UserStatus = 'active' | 'password_pending' | 'no_roles' | 'deactivated'
const STATUS_CARDS: [UserStatus, string][] = [
  ['active', 'Active'], ['password_pending', 'Waiting for first sign-in'], ['no_roles', 'Active with no roles'], ['deactivated', 'Deactivated'],
]

export function UsersPage() {
  const can = useCan()
  const when = useWhen()
  const [q, setQ] = useState('')
  const [status, setStatus] = useState<UserStatus | ''>('')
  const [offset, setOffset] = useState(0)
  const [dialog, setDialog] = useState<'add' | User | null>(null)
  const params = new URLSearchParams({ q, offset: String(offset), limit: '50', ...(status ? { status } : {}) })
  const { data, error, reload } = useApi<{ items: User[]; total: number; counts: Record<UserStatus, number> }>(`/users?${params}`)
  const roles = useApi<{ items: Role[] }>(can('roles.view') ? '/roles' : null)
  const roleList = roles.data?.items ?? []
  const pick = (s: UserStatus) => { setStatus(status === s ? '' : s); setOffset(0) }
  return (
    <>
      <PageHead title="Users" intro={data ? `${data.counts.active} active ${data.counts.active === 1 ? 'person' : 'people'}. Roles decide what each person can open and do.` : undefined}>
        {can('users.create') && <button className="btn btn-primary" onClick={() => setDialog('add')}>Add user</button>}
      </PageHead>
      {data && (
        <div className="counts" role="group" aria-label="Show users by status">
          {STATUS_CARDS.map(([s, label]) => (
            <button key={s} className="count" aria-pressed={status === s} onClick={() => pick(s)}>
              <span>{label}</span><strong>{data.counts[s]}</strong>
            </button>
          ))}
        </div>
      )}
      <div className="toolbar" role="search">
        <Field label="Search by name or email" type="search" value={q} onChange={(e) => { setQ(e.target.value); setOffset(0) }} />
      </div>
      {error ? <PageState kind="error" title="Couldn't load users" message={error} action={<button className="btn btn-secondary" onClick={reload}>Retry</button>} />
        : !data ? <PageState kind="loading" title="Loading users…" />
        : data.items.length === 0 ? <PageState kind="empty" title="Nobody matches" message={status || q ? 'Clear the search or the status to see everyone.' : 'Add the first person.'} />
        : (
          <div className="table-wrap">
            <table className="table stack">
              <thead><tr><th scope="col">Name</th><th scope="col">Roles</th><th scope="col">Status</th><th scope="col">Last sign-in</th><th scope="col"><span className="sr-only">Actions</span></th></tr></thead>
              <tbody>
                {data.items.map((u) => (
                  <tr key={u.id}>
                    <td className="cell-main">{u.name}<span className="muted small">{u.email}</span></td>
                    <td className="cell-sub">{u.roles.map((r) => r.name).join(', ') || <span className="muted">No roles</span>}</td>
                    <td className="cell-tag">{!u.active ? <Tag>Deactivated</Tag> : u.must_change_password ? <Tag tone="info">Waiting for first sign-in</Tag> : <Tag tone="success">Active</Tag>}</td>
                    <td className="cell-sub nowrap">{u.last_login_at ? when(u.last_login_at) : <span className="muted">Never signed in</span>}</td>
                    <td className="actions cell-action">{can('users.edit') && <button className="link-btn" onClick={() => setDialog(u)}>Edit<span className="sr-only"> {u.name}</span></button>}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      {data && <Pager offset={offset} limit={50} total={data.total} onChange={setOffset} />}
      {dialog === 'add' && <Dialog title="Add user" onClose={() => setDialog(null)}><AddUser roles={roleList} onDone={reload} /></Dialog>}
      {dialog && dialog !== 'add' && (
        <Dialog title={`Edit ${dialog.name}`} onClose={() => setDialog(null)}>
          <EditUser user={dialog} roles={roleList} onDone={() => { reload(); setDialog(null) }} />
        </Dialog>
      )}
    </>
  )
}

function RoleEditor({ role, catalogue, onDone }: { role: Role | null; catalogue: Perm[]; onDone: () => void }) {
  const me = useMe()
  const can = useCan()
  const held = role ? me.roles.includes(role.name) : false
  const readOnly = !can('roles.configure') || held
  const action = useAction(onDone)
  const [fields, setFields] = useState<Record<string, string>>({})
  const modules = [...new Set(catalogue.map((p) => p.module))]
  function save(e: React.FormEvent<HTMLFormElement>) {
    e.preventDefault()
    const f = e.currentTarget
    const body = { name: f.rolename.value, description: f.description.value, permissions: checked(f, 'permissions') }
    setFields({})
    void action.run('save', () => (role
      ? api(`/roles/${role.id}`, { method: 'PUT', body: { ...body, version: role.version, reason: f.reason.value } })
      : api('/roles', { method: 'POST', body: { ...body, audience: f.audience.value } })
    ).catch((err) => { setFields(err.fields ?? {}); throw err }))
  }
  return (
    <form onSubmit={save} noValidate>
      {held && <p className="notice notice-warning">You hold this role, so you can't change it. Another administrator can.</p>}
      <fieldset disabled={readOnly} className="plain">
        <Field label="Role name" name="rolename" defaultValue={role?.name} required maxLength={80} error={fields.name} />
        <TextareaField label="What this role is for" name="description" defaultValue={role?.description} maxLength={500} />
        {!role && <SelectField label="Who it is for" name="audience" options={Object.entries(AUDIENCE)} />}
        {role && <p className="muted small">{AUDIENCE[role.audience]} · {role.users === 1 ? '1 user holds' : `${role.users} users hold`} this role. Changes apply on their next click.</p>}
        <div className="perm-groups">
          {modules.map((m) => (
            <fieldset key={m} className="checks">
              <legend>{m}</legend>
              {catalogue.filter((p) => p.module === m).map((p) => (
                <label key={p.code} className="check">
                  <input type="checkbox" name="permissions" value={p.code} defaultChecked={role?.permissions.includes(p.code)} />
                  <span>{p.description}<code className="hint code">{p.code}</code></span>
                </label>
              ))}
            </fieldset>
          ))}
        </div>
        {fields.permissions && <p className="field-error">{fields.permissions}</p>}
        {role && <TextareaField label="Reason for the change" name="reason" maxLength={500} hint="Kept in the audit log with the permissions added and removed." />}
      </fieldset>
      <FormError message={action.error} />
      {!readOnly && (
        <div className="row">
          <button className="btn btn-primary" disabled={!!action.busy}>{action.busy === 'save' ? 'Saving…' : role ? 'Save role' : 'Create role'}</button>
          {role && !role.template && (
            <button type="button" className="btn btn-danger" disabled={!!action.busy}
              onClick={() => action.run('delete', () => api(`/roles/${role.id}`, { method: 'DELETE' }), `Delete the role “${role.name}”?`)}>Delete role</button>
          )}
        </div>
      )}
    </form>
  )
}

export function RolesPage() {
  const can = useCan()
  const { data, error, reload } = useApi<{ items: Role[] }>('/roles')
  const catalogue = useApi<{ items: Perm[] }>('/permissions')
  const [editing, setEditing] = useState<Role | 'new' | null>(null)
  if (error || catalogue.error) return <PageState kind="error" title="Couldn't load roles" message={error || catalogue.error} />
  if (!data || !catalogue.data) return <PageState kind="loading" title="Loading roles…" />
  const modules = Object.fromEntries(catalogue.data.items.map((p) => [p.code, p.module]))
  const opens = (r: Role) => [...new Set(r.permissions.filter((x) => x.endsWith('.view')).map((x) => modules[x]))].join(', ')
  return (
    <>
      <PageHead title="Roles & permissions" intro="People can hold several roles and may do whatever any of them allows. The 17 shipped roles follow the proposal; each gains its permissions as its module is delivered.">
        {can('roles.configure') && <button className="btn btn-primary" onClick={() => setEditing('new')}>New role</button>}
      </PageHead>
      <div className="table-wrap">
        <table className="table">
          <thead><tr><th scope="col">Role</th><th scope="col">Can open</th><th scope="col">For</th><th scope="col" className="num">Users</th><th scope="col"><span className="sr-only">Actions</span></th></tr></thead>
          <tbody>
            {data.items.map((r) => (
              <tr key={r.id}>
                <td>{r.name} {!r.template && <Tag tone="info">Custom</Tag>}<span className="muted small">{r.description}</span></td>
                <td>{opens(r) || <span className="muted">Nothing yet</span>}</td>
                <td className="nowrap">{AUDIENCE[r.audience]}</td>
                <td className="num">{r.users}</td>
                <td className="actions"><button className="link-btn" onClick={() => setEditing(r)}>{can('roles.configure') ? 'Edit' : 'View'}<span className="sr-only"> {r.name}</span></button></td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      {editing && (
        <Dialog wide title={editing === 'new' ? 'New role' : editing.name} onClose={() => setEditing(null)}>
          <RoleEditor role={editing === 'new' ? null : editing} catalogue={catalogue.data.items} onDone={() => { reload(); setEditing(null) }} />
        </Dialog>
      )}
    </>
  )
}

type Event = {
  id: number; at: string; actor: string; action: string; entity_type: string; entity_id: string
  summary: string; changes: Record<string, unknown>; reason: string; ip: string
}

function Changes({ changes }: { changes: Record<string, unknown> }) {
  const keys = Object.keys(changes)
  if (!keys.length) return null
  return (
    <details>
      <summary>What changed</summary>
      <dl className="changes">
        {keys.map((k) => <div key={k}><dt>{k}</dt><dd><code>{JSON.stringify(changes[k])}</code></dd></div>)}
      </dl>
    </details>
  )
}

export function AuditPage() {
  const can = useCan()
  const when = useWhen()
  const [filters, setFilters] = useState<Record<string, string>>({})
  const [offset, setOffset] = useState(0)
  const [error, setError] = useState('')
  const params = new URLSearchParams(Object.entries(filters).filter(([, v]) => v))
  const { data, error: loadError, reload } = useApi<{ items: Event[]; total: number }>(`/audit?${params}&offset=${offset}&limit=50`)
  function apply(e: React.FormEvent<HTMLFormElement>) {
    e.preventDefault()
    const f = Object.fromEntries([...new FormData(e.currentTarget)].map(([k, v]) => [k, String(v)]))
    // Dates are picked as whole days in the browser's time zone and sent as full timestamps.
    if (f.since) f.since = new Date(`${f.since}T00:00`).toISOString()
    if (f.until) f.until = new Date(new Date(`${f.until}T00:00`).getTime() + 86_400_000).toISOString()
    setFilters(f)
    setOffset(0)
  }
  return (
    <>
      <PageHead title="Audit log" intro="Every sign-in, change and export, with who did it and why. Entries can't be edited or deleted, by anyone.">
        {can('audit.export') && <button className="btn btn-secondary" onClick={() => download(`/audit/export?${params}`, 'audit-log.csv').catch((e) => setError(e.message))}>Export CSV</button>}
      </PageHead>
      <form className="toolbar" onSubmit={apply} role="search">
        <Field label="Action starts with" name="action" placeholder="e.g. role." maxLength={64} />
        <Field label="Person" name="actor" maxLength={120} />
        <Field label="Text" name="q" maxLength={100} />
        <Field label="From" name="since" type="date" />
        <Field label="To" name="until" type="date" />
        <button className="btn btn-secondary">Filter</button>
      </form>
      <FormError message={error} />
      {loadError ? <PageState kind="error" title="Couldn't load the audit log" message={loadError} action={<button className="btn btn-secondary" onClick={reload}>Retry</button>} />
        : !data ? <PageState kind="loading" title="Loading…" />
        : data.items.length === 0 ? <PageState kind="empty" title="Nothing matches these filters" />
        : (
          <div className="table-wrap">
            <table className="table">
              <thead><tr><th scope="col">When</th><th scope="col">Who</th><th scope="col">Action</th><th scope="col">Details</th></tr></thead>
              <tbody>
                {data.items.map((e) => (
                  <tr key={e.id}>
                    <td className="nowrap">{when(e.at)}</td>
                    <td>{e.actor}<div className="muted small">{e.ip}</div></td>
                    <td><code>{e.action}</code></td>
                    <td>{e.summary}{e.reason && <div className="small">Reason: {e.reason}</div>}<Changes changes={e.changes} /></td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      {data && <Pager offset={offset} limit={50} total={data.total} onChange={setOffset} />}
    </>
  )
}

type Company = { name: string; timezone: string; currency: string; version: number; timezones: string[] }

/** One settings section: what it is on the left, its fields on the right. */
function SettingsRow({ title, text, children }: { title: string; text: string; children: React.ReactNode }) {
  return (
    <section className="settings-row">
      <div><h2>{title}</h2><p className="muted">{text}</p></div>
      <div className="settings-panel">{children}</div>
    </section>
  )
}

export function CompanyPage({ onChange }: { onChange: () => void }) {
  const can = useCan()
  const { data, error, reload } = useApi<Company>('/company')
  const [saved, setSaved] = useState(false)
  const [tz, setTz] = useState('')
  const [currency, setCurrency] = useState('')
  const submit = useSubmit('/company', () => { setSaved(true); reload(); onChange() }, { method: 'PATCH', extra: { version: data?.version } })
  if (error) return <PageState kind="error" title="Couldn't load company settings" message={error} action={<button className="btn btn-secondary" onClick={reload}>Retry</button>} />
  if (!data) return <PageState kind="loading" title="Loading…" />
  const zone = tz || data.timezone
  const cur = /^[A-Z]{3}$/.test(currency) ? currency : data.currency
  let now = ''
  let amount = ''
  try {
    now = new Date().toLocaleString('en-GB', { timeZone: zone, dateStyle: 'medium', timeStyle: 'short' })
    amount = new Intl.NumberFormat('en-GB', { style: 'currency', currency: cur }).format(18450.5)
  } catch { /* an unknown code while typing: the preview waits */ }
  const editable = can('settings.configure')
  return (
    <>
      <PageHead title="Company settings" intro={editable ? undefined : 'Read only: changing these needs the settings permission.'} />
      <form onSubmit={(e) => { setSaved(false); void submit.submit(e) }} noValidate key={data.version}>
        <fieldset disabled={!editable} className="plain">
          <SettingsRow title="Company" text="The name on screens, exports and, later, on quotations and delivery notes.">
            <Field label="Company name" name="name" defaultValue={data.name} required maxLength={160} error={submit.fields.name} />
          </SettingsRow>
          <SettingsRow title="Dates and times" text="Every date and time in the system is stored in UTC and shown in this time zone.">
            <SelectField label="Time zone" name="timezone" defaultValue={data.timezone} onChange={(e) => setTz(e.target.value)}
              options={data.timezones.map((t) => [t, t.replaceAll('_', ' ')])} error={submit.fields.timezone} />
            <p className="preview">Now in {zone.replaceAll('_', ' ')}: <strong>{now || '—'}</strong></p>
          </SettingsRow>
          <SettingsRow title="Money" text="The default currency for purchase, quotation and order values. A document can state another.">
            <Field label="Currency code" name="currency" defaultValue={data.currency} pattern="[A-Z]{3}" maxLength={3}
              onChange={(e) => setCurrency(e.target.value.toUpperCase())} hint="Three capital letters, e.g. AED or USD." error={submit.fields.currency} />
            <p className="preview">An amount will look like <strong>{amount || '—'}</strong></p>
          </SettingsRow>
          <div className="settings-foot">
            <FormError message={submit.error} />
            {saved && <p className="notice notice-success" role="status">Settings saved.</p>}
            {editable && <button className="btn btn-primary" disabled={submit.busy}>{submit.busy ? 'Saving…' : 'Save settings'}</button>}
          </div>
        </fieldset>
      </form>
    </>
  )
}

type Status = {
  version: string; database_mb: number; database_version: string; worker_in_api: boolean
  jobs: { pending: number; running: number; done: number; dead: number; dead_jobs: { id: string; kind: string; attempts: number; last_error: string }[] }
}

export function SystemPage() {
  const { data, error, reload } = useApi<Status>('/system/status')
  const [checked, setChecked] = useState(() => new Date())
  if (error) return <PageState kind="error" title="Couldn't load system status" message={error} action={<button className="btn btn-secondary" onClick={reload}>Retry</button>} />
  if (!data) return <PageState kind="loading" title="Loading…" />
  const j = data.jobs
  return (
    <>
      <PageHead title="System health" intro={j.dead ? `${j.dead} background ${j.dead === 1 ? 'job has' : 'jobs have'} failed and need a look.` : 'Everything is running. No background job has failed.'}>
        <span className="stamp">Checked {checked.toLocaleTimeString('en-GB', { timeStyle: 'short' })}</span>
        <button className="btn btn-secondary" onClick={() => { reload(); setChecked(new Date()) }}>Refresh</button>
      </PageHead>
      <dl className="facts">
        <div><dt>App version</dt><dd>{data.version}</dd></div>
        <div><dt>Database</dt><dd>PostgreSQL {data.database_version}</dd></div>
        <div><dt>Database size</dt><dd>{data.database_mb.toLocaleString('en-GB', { minimumFractionDigits: 1 })} <span className="unit">MB</span></dd></div>
        <div><dt>Background worker</dt><dd>{data.worker_in_api ? 'Inside the API' : 'Separate process'}</dd></div>
      </dl>
      <section className="panel panel-wide" aria-labelledby="jobs-title">
        <h2 id="jobs-title">Background jobs</h2>
        <p className="muted">Reminders, notifications and clean-ups run here. A job that fails is retried with growing waits, up to five times, then kept below.</p>
        <dl className="facts facts-inner">
          <div><dt>Waiting</dt><dd>{j.pending}</dd></div>
          <div><dt>Running</dt><dd>{j.running}</dd></div>
          <div><dt>Finished this week</dt><dd>{j.done}</dd></div>
          <div><dt>Failed</dt><dd>{j.dead}</dd></div>
        </dl>
        {j.dead_jobs.length === 0 ? <p className="empty-line">No failed jobs.</p> : (
          <div className="table-wrap"><table className="table">
            <thead><tr><th scope="col">Job</th><th scope="col" className="num">Attempts</th><th scope="col">Last error</th></tr></thead>
            <tbody>{j.dead_jobs.map((x) => <tr key={x.id}><td><code>{x.kind}</code></td><td className="num">{x.attempts}</td><td>{x.last_error}</td></tr>)}</tbody>
          </table></div>
        )}
      </section>
    </>
  )
}
