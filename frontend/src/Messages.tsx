import { useEffect, useState } from 'react'
import { Link } from 'react-router'
import { Bell } from 'lucide-react'
import { api, ApiError } from './api'
import { useAction, useApi, useCan, useWhen } from './lib'
import { words, type Page } from './ops'
import { Dialog, Field, FormError, PageHead, PageState, Pager, SelectField, Tag, TextareaField } from './ui'

type Note = { id: string; event: string; title: string; body: string; link: string; read: boolean; created_at: string }
type Inbox = { items: Note[]; total: number; unread: number }
type Prefs = { notify_email: boolean; muted_events: string[]; events: { key: string; label: string }[] }

/** The unread count beside the menu, checked every minute and whenever the inbox changes. */
export function BellLink() {
  const [unread, setUnread] = useState(0)
  useEffect(() => {
    let alive = true
    const check = () => api<Inbox>('/notifications?unread=true&limit=1').then((d) => alive && setUnread(d.unread)).catch(() => {})
    void check()
    const t = window.setInterval(check, 60_000)
    window.addEventListener('notifications-changed', check)
    return () => { alive = false; window.clearInterval(t); window.removeEventListener('notifications-changed', check) }
  }, [])
  return (
    <Link className="bell" to="/notifications" aria-label={unread ? `Notifications, ${unread} unread` : 'Notifications'}>
      <Bell size={16} aria-hidden="true" /> Notifications {unread > 0 && <span className="bell-count">{unread > 99 ? '99+' : unread}</span>}
    </Link>
  )
}

const changed = () => window.dispatchEvent(new Event('notifications-changed'))

export function NotificationsPage() {
  const when = useWhen()
  const [unreadOnly, setUnreadOnly] = useState(false)
  const [offset, setOffset] = useState(0)
  const { data, error, reload } = useApi<Inbox>(`/notifications?limit=30&offset=${offset}${unreadOnly ? '&unread=true' : ''}`)
  const prefs = useApi<Prefs>('/notifications/preferences')
  const [editing, setEditing] = useState(false)
  const action = useAction(() => { reload(); changed() })
  const read = (ids: string[], all = false) => action.run('read', () => api('/notifications/read', { method: 'POST', body: { ids, all } }))
  return (
    <>
      <PageHead title="Notifications" intro={data ? (data.unread ? `${data.unread} unread.` : 'All read.') : undefined}>
        {!!data?.unread && <button className="btn btn-secondary" disabled={!!action.busy} onClick={() => read([], true)}>Mark all as read</button>}
        <button className="btn btn-ghost" onClick={() => setEditing(true)}>Notification settings</button>
      </PageHead>
      <div className="toolbar"><label className="check"><input type="checkbox" checked={unreadOnly} onChange={(e) => { setUnreadOnly(e.target.checked); setOffset(0) }} /> <span>Unread only</span></label></div>
      <FormError message={action.error} />
      {error ? <PageState kind="error" title="Couldn't load notifications" message={error} /> : !data ? <PageState kind="loading" title="Loading…" />
        : data.items.length === 0 ? <p className="empty-line">{unreadOnly ? 'Nothing unread.' : 'Nothing yet. Approvals, late deliveries, visits and service updates that concern you will appear here.'}</p> : (
          <ul className="inbox panel panel-wide">
            {data.items.map((n) => (
              <li key={n.id} data-unread={!n.read || undefined}>
                <span>
                  <strong>{n.link ? <Link to={n.link} onClick={() => !n.read && read([n.id])}>{n.title}</Link> : n.title}</strong>
                  {n.body && <span className="small muted">{n.body}</span>}
                </span>
                <span className="inbox-side">
                  <span className="stamp">{when(n.created_at)}</span>
                  {!n.read && <button className="btn btn-ghost" disabled={!!action.busy} onClick={() => read([n.id])}>Mark read</button>}
                </span>
              </li>
            ))}
          </ul>
        )}
      {data && <Pager offset={offset} limit={30} total={data.total} onChange={setOffset} />}
      {editing && prefs.data && <PrefsDialog prefs={prefs.data} onClose={() => setEditing(false)} onSaved={() => { setEditing(false); prefs.reload() }} />}
    </>
  )
}

function PrefsDialog({ prefs, onClose, onSaved }: { prefs: Prefs; onClose: () => void; onSaved: () => void }) {
  const action = useAction(onSaved)
  return (
    <Dialog title="Notification settings" onClose={onClose}>
      <form noValidate onSubmit={(e) => {
        e.preventDefault()
        const fd = new FormData(e.currentTarget)
        const muted = prefs.events.map((x) => x.key).filter((k) => fd.get(k) !== 'on')
        void action.run('save', () => api('/notifications/preferences', { method: 'PUT', body: { notify_email: fd.get('email') === 'on', muted_events: muted } }))
      }}>
        <label className="check"><input type="checkbox" name="email" defaultChecked={prefs.notify_email} /> <span>Also send them to my email address</span></label>
        <p className="hint">Email goes out only once SMET has set up an email provider and switched it on.</p>
        <fieldset className="plain">
          <legend>Tell me about</legend>
          {prefs.events.map((x) => (
            <label key={x.key} className="check"><input type="checkbox" name={x.key} defaultChecked={!prefs.muted_events.includes(x.key)} /> <span>{x.label}</span></label>
          ))}
        </fieldset>
        <FormError message={action.error} />
        <div className="dialog-actions"><button className="btn btn-primary" disabled={!!action.busy}>Save settings</button></div>
      </form>
    </Dialog>
  )
}

// ---------- administration ----------

type Provider = {
  channel: 'email' | 'sms' | 'whatsapp'
  providers: { key: string; label: string; secret_label: string; fields: { name: string; label: string; required: boolean }[] }[]
  provider: string; mode: string; settings: Record<string, string>; secret_set: boolean; test_recipient: string
  last_test: { at: string; ok: boolean; message: string } | null; blocked: string; version: number
}
type Template = { event: string; label: string; channel: string; audience: string; fields: string[]; subject: string; body: string
  provider_template: string; language: string; active: boolean; customised: boolean; version: number }
type Delivery = { id: string; label: string; channel: string; mode: string; to: string; sent_to: string; subject: string; status: string
  attempts: number; error: string; created_at: string }
type OptOut = { channel: string; address: string; reason: string; created_at: string }

const CHANNEL: Record<string, string> = { in_app: 'In the app', email: 'Email', sms: 'SMS', whatsapp: 'WhatsApp' }
const MODE: Record<string, string> = { off: 'Off', mock: 'Mock: recorded here, nothing sent', sandbox: 'Sandbox: test recipient only', production: 'Production: real recipients' }
const SEND_TONE: Record<string, 'success' | 'warning' | 'danger' | 'neutral' | 'info'> = {
  delivered: 'success', sent: 'info', queued: 'neutral', sending: 'neutral', failed: 'danger', unknown: 'warning', blocked: 'warning', skipped: 'neutral' }

function fieldsOf(e: unknown) { return e instanceof ApiError ? e.fields : {} }

export function MessagesAdminPage() {
  const can = useCan()
  const tabs = [...(can('notifications.configure') ? [['channels', 'Channels'], ['wording', 'Wording']] : []),
    ['sent', 'Sent messages'], ...(can('notifications.configure') ? [['optouts', 'Opt-outs']] : [])] as [string, string][]
  const [tab, setTab] = useState(tabs[0][0])
  return (
    <>
      <PageHead title="Messages" intro="How SMET tells people what happened: in the app, and by email, SMS or WhatsApp once a provider is set up and allowed." />
      <div className="tabs" role="tablist" aria-label="Messages">
        {tabs.map(([k, label]) => <button key={k} role="tab" aria-selected={tab === k} className={tab === k ? 'tab active' : 'tab'} onClick={() => setTab(k)}>{label}</button>)}
      </div>
      {tab === 'channels' && <Channels />}
      {tab === 'wording' && <Wording />}
      {tab === 'sent' && <Sent />}
      {tab === 'optouts' && <OptOuts />}
    </>
  )
}

function Channels() {
  const { data, error, reload } = useApi<{ items: Provider[]; external_sends: string }>('/messages/providers')
  const [open, setOpen] = useState<Provider | null>(null)
  const action = useAction(reload)
  const when = useWhen()
  if (error) return <PageState kind="error" title="Couldn't load channels" message={error} />
  if (!data) return <PageState kind="loading" title="Loading…" />
  return (
    <>
      <p className={data.external_sends === 'off' ? 'notice' : 'notice notice-warning'}>
        {data.external_sends === 'off' ? 'This server sends nothing outside: every channel can be tried in mock mode, and messages in sandbox or production mode are recorded as blocked. Whoever runs the server switches this on (EXTERNAL_SENDS).'
          : data.external_sends === 'sandbox' ? 'This server allows sandbox sends only: messages go to each channel\'s test recipient.' : 'This server allows real sends on channels in production mode.'}
      </p>
      <FormError message={action.error} />
      <div className="grid-3 section">
        {data.items.map((p) => (
          <section key={p.channel} className="panel">
            <header className="panel-head"><h2>{CHANNEL[p.channel]}</h2><Tag tone={p.mode === 'off' ? 'neutral' : p.blocked ? 'warning' : 'success'}>{p.mode === 'off' ? 'Off' : words(p.mode)}</Tag></header>
            <p className="small">{p.providers.find((x) => x.key === p.provider)?.label}{p.secret_set ? ' · credentials stored' : ''}</p>
            {p.blocked && <p className="small muted">{p.blocked}.</p>}
            {p.last_test && <p className="small">{p.last_test.ok ? 'Connection test passed' : 'Connection test failed'}, {when(p.last_test.at)}: {p.last_test.message}</p>}
            <div className="row">
              <button className="btn btn-secondary" onClick={() => setOpen(p)}>Set up</button>
              {p.mode !== 'off' && <button className="btn btn-ghost" disabled={!!action.busy} onClick={() => action.run(p.channel, () => api(`/messages/providers/${p.channel}/test`, { method: 'POST' }))}>Test connection</button>}
            </div>
          </section>
        ))}
      </div>
      {open && <ChannelForm p={open} onClose={() => setOpen(null)} onSaved={() => { setOpen(null); reload() }} />}
    </>
  )
}

function ChannelForm({ p, onClose, onSaved }: { p: Provider; onClose: () => void; onSaved: () => void }) {
  const [provider, setProvider] = useState(p.provider)
  const [mode, setMode] = useState(p.mode)
  const [fields, setFields] = useState<Record<string, string>>({})
  const action = useAction(onSaved)
  const kind = p.providers.find((x) => x.key === provider)!
  return (
    <Dialog title={`${CHANNEL[p.channel]} messages`} onClose={onClose}>
      <form noValidate onSubmit={(e) => {
        e.preventDefault()
        const fd = new FormData(e.currentTarget)
        const settings = Object.fromEntries(kind.fields.map((f) => [f.name, String(fd.get(`s_${f.name}`) ?? '')]))
        setFields({})
        void action.run('save', () => api(`/messages/providers/${p.channel}`, { method: 'PUT', body: { version: p.version, provider, mode, settings,
          secret: fd.get('secret') ?? '', test_recipient: fd.get('test_recipient') ?? '', reason: fd.get('reason') ?? '' } })
          .catch((err) => { setFields(fieldsOf(err)); throw err }))
      }}>
        {p.providers.length > 1 && <SelectField label="Provider" value={provider} onChange={(e) => setProvider(e.target.value)} options={p.providers.map((x) => [x.key, x.label])} />}
        <SelectField label="Mode" value={mode} onChange={(e) => setMode(e.target.value)} options={Object.entries(MODE)} error={fields.mode} />
        {kind.fields.map((f) => <Field key={f.name} label={f.label} name={`s_${f.name}`} defaultValue={p.settings[f.name] ?? ''} error={fields[`settings.${f.name}`]} required={f.required && ['sandbox', 'production'].includes(mode)} />)}
        <Field label={kind.secret_label} name="secret" type="password" autoComplete="new-password" error={fields.secret}
          hint={p.secret_set ? 'Stored encrypted. Leave empty to keep it; it is never shown again.' : 'Stored encrypted; never shown again.'} />
        <Field label="Test recipient" name="test_recipient" defaultValue={p.test_recipient} error={fields.test_recipient}
          hint="In sandbox mode every message goes here instead of to the real person." />
        {p.channel === 'whatsapp' && <p className="hint">WhatsApp only sends templates approved by Meta: give each message its template name under Wording.</p>}
        <TextareaField label="Reason for the change" name="reason" maxLength={500} />
        <FormError message={action.error} />
        <div className="dialog-actions"><button className="btn btn-primary" disabled={!!action.busy}>Save channel</button></div>
      </form>
    </Dialog>
  )
}

function Wording() {
  const { data, error, reload } = useApi<{ items: Template[] }>('/messages/templates')
  const [channel, setChannel] = useState('in_app')
  const [open, setOpen] = useState<Template | null>(null)
  if (error) return <PageState kind="error" title="Couldn't load the wording" message={error} />
  if (!data) return <PageState kind="loading" title="Loading…" />
  const shown = data.items.filter((t) => t.channel === channel)
  return (
    <>
      <div className="toolbar"><SelectField label="Channel" value={channel} onChange={(e) => setChannel(e.target.value)} options={Object.entries(CHANNEL)} /></div>
      <div className="table-wrap"><table className="table stack">
        <thead><tr><th scope="col">Message</th><th scope="col">For</th><th scope="col">Wording</th><th scope="col">State</th></tr></thead>
        <tbody>{shown.map((t) => (
          <tr key={t.event}>
            <td className="cell-main"><button className="link-btn" onClick={() => setOpen(t)}>{t.label}</button></td>
            <td className="cell-sub">{t.audience === 'customer' ? 'Customer' : 'Staff'}</td>
            <td className="cell-sub clamp">{t.subject ? <strong>{t.subject} </strong> : null}{t.body}</td>
            <td className="cell-tag">{!t.active ? <Tag>Off</Tag> : t.customised ? <Tag tone="info">Changed</Tag> : <Tag>Standard</Tag>}</td>
          </tr>
        ))}</tbody>
      </table></div>
      {open && <TemplateForm t={open} onClose={() => setOpen(null)} onSaved={() => { setOpen(null); reload() }} />}
    </>
  )
}

function TemplateForm({ t, onClose, onSaved }: { t: Template; onClose: () => void; onSaved: () => void }) {
  const [subject, setSubject] = useState(t.subject)
  const [body, setBody] = useState(t.body)
  const [preview, setPreview] = useState<{ subject: string; body: string; errors: Record<string, string>; chars: number; segments: number } | null>(null)
  const [fields, setFields] = useState<Record<string, string>>({})
  const action = useAction(onSaved)
  useEffect(() => {
    const id = window.setTimeout(() => {
      api<typeof preview>('/messages/templates/preview', { method: 'POST', body: { event: t.event, channel: t.channel, subject, body } }).then(setPreview).catch(() => {})
    }, 300)
    return () => window.clearTimeout(id)
  }, [subject, body, t.event, t.channel])
  const text = t.channel === 'sms' || t.channel === 'whatsapp'
  return (
    <Dialog title={`${t.label}: ${CHANNEL[t.channel]}`} onClose={onClose} wide>
      <form noValidate onSubmit={(e) => {
        e.preventDefault()
        const fd = new FormData(e.currentTarget)
        setFields({})
        void action.run('save', () => api(`/messages/templates/${t.event}/${t.channel}`, { method: 'PUT', body: { version: t.version, subject, body,
          provider_template: fd.get('provider_template') ?? '', language: fd.get('language') || 'en', active: fd.get('active') === 'on' } })
          .catch((err) => { setFields(fieldsOf(err)); throw err }))
      }}>
        <p className="hint">Fields you can use: {t.fields.map((f) => <code key={f}>{`{${f}}`}</code>).reduce<React.ReactNode[]>((a, x, i) => (i ? [...a, ' ', x] : [x]), [])}</p>
        {!text && <Field label="Title" value={subject} onChange={(e) => setSubject(e.target.value)} maxLength={200} error={fields.subject ?? preview?.errors.subject} />}
        <TextareaField label="Text" value={body} onChange={(e) => setBody(e.target.value)} rows={4} maxLength={2000} error={fields.body ?? preview?.errors.body} />
        {t.channel === 'whatsapp' && (
          <div className="grid-fields">
            <Field label="Template name approved by Meta" name="provider_template" defaultValue={t.provider_template} error={fields.provider_template} hint="The fields above become its parameters, in order." />
            <Field label="Template language" name="language" defaultValue={t.language} error={fields.language} />
          </div>
        )}
        <label className="check"><input type="checkbox" name="active" defaultChecked={t.active} /> <span>Send this message</span></label>
        {preview && (
          <section className="template-preview" aria-label="Preview with sample values">
            <p className="small muted">Preview with sample values{text ? ` · ${preview.chars} characters${t.channel === 'sms' ? `, ${preview.segments} SMS` : ''}` : ''}</p>
            {preview.subject && <p><strong>{preview.subject}</strong></p>}
            <p className="prewrap">{preview.body}</p>
          </section>
        )}
        <FormError message={action.error} />
        <div className="dialog-actions"><button className="btn btn-primary" disabled={!!action.busy}>Save wording</button></div>
      </form>
    </Dialog>
  )
}

function Sent() {
  const can = useCan()
  const when = useWhen()
  const [status, setStatus] = useState('')
  const [offset, setOffset] = useState(0)
  const { data, error, reload } = useApi<Page<Delivery>>(`/messages/deliveries?limit=50&offset=${offset}${status ? `&status=${status}` : ''}`)
  const action = useAction(reload)
  return (
    <>
      <div className="toolbar"><SelectField label="Status" value={status} onChange={(e) => { setStatus(e.target.value); setOffset(0) }}
        options={[['', 'All'], ['failed,unknown,blocked', 'Needs attention'], ['queued,sending', 'Waiting'], ['sent,delivered', 'Sent'], ['skipped', 'Skipped (opted out)']]} /></div>
      <FormError message={action.error} />
      {error ? <PageState kind="error" title="Couldn't load messages" message={error} /> : !data ? <PageState kind="loading" title="Loading…" />
        : data.items.length === 0 ? <p className="empty-line">No messages here. Messages appear once a channel is switched on.</p> : (
          <div className="table-wrap"><table className="table stack">
            <thead><tr><th scope="col">Message</th><th scope="col">To</th><th scope="col">Channel</th><th scope="col">Status</th><th scope="col"><span className="sr-only">Actions</span></th></tr></thead>
            <tbody>{data.items.map((d) => (
              <tr key={d.id}>
                <td className="cell-main">{d.label}<span className="small muted">{d.subject ? `${d.subject} · ` : ''}{when(d.created_at)}</span></td>
                <td className="cell-sub"><code>{d.to}</code>{d.sent_to && d.sent_to !== d.to && <span className="small muted"> sandbox: sent to {d.sent_to}</span>}</td>
                <td className="cell-sub">{CHANNEL[d.channel]} · {words(d.mode)}</td>
                <td className="cell-tag">{d.mode === 'mock' && d.status === 'delivered' ? <Tag>Recorded, not sent</Tag> : <Tag tone={SEND_TONE[d.status]}>{words(d.status)}</Tag>}{d.error && <span className="small muted">{d.error}</span>}</td>
                <td className="cell-actions">{can('notifications.configure') && ['failed', 'blocked', 'unknown'].includes(d.status) &&
                  <button className="btn btn-ghost" disabled={!!action.busy} onClick={() => action.run(d.id, () => api(`/messages/deliveries/${d.id}/retry`, { method: 'POST' }),
                    d.status === 'unknown' ? 'This message may already have arrived. Send it again anyway?' : undefined)}>Send again</button>}</td>
              </tr>
            ))}</tbody>
          </table></div>
        )}
      {data && <Pager offset={offset} limit={50} total={data.total} onChange={setOffset} />}
    </>
  )
}

function OptOuts() {
  const when = useWhen()
  const { data, error, reload } = useApi<Page<OptOut>>('/messages/opt-outs?limit=200')
  const [fields, setFields] = useState<Record<string, string>>({})
  const [allow, setAllow] = useState<OptOut | null>(null)
  const action = useAction(() => { setAllow(null); reload() })
  return (
    <>
      <form className="toolbar" noValidate onSubmit={(e) => {
        e.preventDefault()
        const form = e.currentTarget
        const fd = new FormData(form)
        setFields({})
        void action.run('add', () => api('/messages/opt-outs', { method: 'POST', body: { channel: fd.get('channel'), address: fd.get('address'), reason: fd.get('reason') } })
          .then(() => form.reset()).catch((err) => { setFields(fieldsOf(err)); throw err }))
      }}>
        <SelectField label="Channel" name="channel" options={[['sms', 'SMS'], ['whatsapp', 'WhatsApp'], ['email', 'Email']]} />
        <Field label="Phone number or email" name="address" required error={fields.address} />
        <Field label="Reason" name="reason" maxLength={300} />
        <button className="btn btn-secondary" disabled={!!action.busy}>Stop messages</button>
      </form>
      <FormError message={allow ? '' : action.error} />
      {error ? <PageState kind="error" title="Couldn't load opt-outs" message={error} /> : !data ? <PageState kind="loading" title="Loading…" />
        : data.items.length === 0 ? <p className="empty-line">Nobody has opted out.</p> : (
          <div className="table-wrap"><table className="table stack">
            <thead><tr><th scope="col">Address</th><th scope="col">Channel</th><th scope="col">Reason</th><th scope="col"><span className="sr-only">Actions</span></th></tr></thead>
            <tbody>{data.items.map((o) => (
              <tr key={`${o.channel}${o.address}`}>
                <td className="cell-main"><code>{o.address}</code><span className="small muted">{when(o.created_at)}</span></td>
                <td className="cell-sub">{CHANNEL[o.channel]}</td><td className="cell-sub">{o.reason}</td>
                <td className="cell-actions"><button className="btn btn-ghost" onClick={() => setAllow(o)}>Allow again</button></td>
              </tr>
            ))}</tbody>
          </table></div>
        )}
      {allow && (
        <Dialog title={`Allow ${CHANNEL[allow.channel]} to ${allow.address} again`} onClose={() => setAllow(null)}>
          <form noValidate onSubmit={(e) => {
            e.preventDefault()
            const reason = String(new FormData(e.currentTarget).get('reason') ?? '').trim()
            void action.run('allow', () => api(`/messages/opt-outs/${allow.channel}/${encodeURIComponent(allow.address)}?reason=${encodeURIComponent(reason)}`, { method: 'DELETE' }))
          }}>
            <TextareaField label="Why may this address receive messages again?" name="reason" required minLength={3} maxLength={300} />
            <FormError message={action.error} />
            <div className="dialog-actions"><button className="btn btn-primary" disabled={!!action.busy}>Allow messages</button></div>
          </form>
        </Dialog>
      )}
    </>
  )
}
