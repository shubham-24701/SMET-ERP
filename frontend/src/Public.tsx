import { useEffect, useState } from 'react'
import { api, ApiError, upload } from './api'
import { day, today } from './ops'
import { Field, FormError, Logo, TextareaField } from './ui'

// The public portal: no sign-in. It shows only what SMET allows and never anything about the customer.
type Result = { serial: string; product: string; status: string; note: string
  warranty: { state: string; start_date?: string; expiry_date?: string } }

const STATUS: Record<string, string> = { in_service: 'Installed and in service', installed: 'Installed, not yet commissioned',
  removed: 'Taken out of service', not_in_service: 'Not yet installed', withdrawn: 'Withdrawn from use' }
const WARRANTY: Record<string, string> = { none: 'No warranty registered', pending: 'Registration being checked by SMET',
  active: 'Active', not_started: 'Registered, starts later', expired: 'Expired', ended: 'Ended (battery replaced)' }

/** `/v/<token>` from a scanned label, or `/verify` to type a serial number. */
export function PublicPage({ token }: { token?: string }) {
  const [serial, setSerial] = useState(new URLSearchParams(window.location.search).get('serial') ?? '')
  const [result, setResult] = useState<Result | 'missing' | 'error' | null>(null)
  const [busy, setBusy] = useState(false)
  const check = async (path: string) => {
    setBusy(true)
    try { setResult(await api<Result>(path)) } catch (e) { setResult(e instanceof ApiError && e.status === 404 ? 'missing' : 'error') } finally { setBusy(false) }
  }
  // oxlint-disable-next-line react/set-state-in-effect -- loading from the API on mount is syncing with an external system
  useEffect(() => { if (token) void check(`/public/verify/${encodeURIComponent(token)}`) }, [token])
  return (
    <main className="verify">
      <p className="brand"><Logo /> SMET</p>
      {!token && (
        <form className="row" role="search" onSubmit={(e) => { e.preventDefault(); if (serial.trim()) void check(`/public/lookup?${new URLSearchParams({ serial: serial.trim() })}`) }}>
          <Field label="Battery serial number" value={serial} onChange={(e) => setSerial(e.target.value)} autoComplete="off" required maxLength={60} />
          <button className="btn btn-primary" disabled={busy}>Check</button>
        </form>
      )}
      {result === null ? (token ? <p>Checking…</p> : <p className="muted">Type the serial number printed on the battery's label, or scan its QR code with a phone camera.</p>)
        : result === 'missing' ? <><h1>No record found</h1><p>SMET has no battery with that {token ? 'label' : 'serial number'}. Check it and try again, or contact SMET.</p></>
        : result === 'error' ? <><h1>We couldn't check it</h1><p>Please try again in a minute.</p></>
        : <Found r={result} />}
    </main>
  )
}

function Found({ r }: { r: Result }) {
  const [form, setForm] = useState<'register' | 'service' | null>(null)
  const [sent, setSent] = useState('')
  const w = r.warranty
  return (
    <>
      <h1>SMET has a record of this battery</h1>
      <dl className="print-facts">
        <div><dt>Serial number</dt><dd>{r.serial}</dd></div>
        <div><dt>Product</dt><dd>{r.product}</dd></div>
        <div><dt>Status</dt><dd>{STATUS[r.status] ?? r.status}</dd></div>
        <div><dt>Warranty</dt><dd>{WARRANTY[w.state] ?? w.state}{w.expiry_date && `, ${day(w.start_date)} to ${day(w.expiry_date)}`}</dd></div>
      </dl>
      <p className="muted small">{r.note}</p>
      {sent ? <p className="notice notice-success">{sent}</p> : (
        <p className="row">
          {w.state === 'none' && <button className="btn btn-secondary" onClick={() => setForm('register')}>Register the warranty</button>}
          <button className="btn btn-secondary" onClick={() => setForm('service')}>Ask for service</button>
        </p>
      )}
      {!sent && form === 'register' && <RegisterForm serial={r.serial} onSent={setSent} />}
      {!sent && form === 'service' && <ServiceForm serial={r.serial} onSent={setSent} />}
    </>
  )
}

function useSend(onSent: (m: string) => void) {
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const [fields, setFields] = useState<Record<string, string>>({})
  const send = async (fn: () => Promise<{ message: string }>) => {
    setBusy(true); setError(''); setFields({})
    try { onSent((await fn()).message) } catch (e) {
      setError(e instanceof Error ? e.message : 'Something went wrong.')
      if (e instanceof ApiError) setFields(e.fields)
    } finally { setBusy(false) }
  }
  return { busy, error, fields, send }
}

/** Honeypot: hidden from people, filled in by bots; the server quietly drops those. */
const Trap = () => <input name="website" tabIndex={-1} autoComplete="off" aria-hidden="true" className="trap" />

function RegisterForm({ serial, onSent }: { serial: string; onSent: (m: string) => void }) {
  const s = useSend(onSent)
  return (
    <form className="section" noValidate onSubmit={(e) => { e.preventDefault(); const fd = new FormData(e.currentTarget); fd.set('serial', serial); void s.send(() => upload('/public/registrations', fd)) }}>
      <h2>Register the warranty</h2>
      <p className="muted">SMET checks every registration against its warranty policy and contacts you.</p>
      <Field label="Your name or company" name="customer_name" required minLength={2} maxLength={160} error={s.fields.customer_name} />
      <Field label="Phone or email" name="contact" required minLength={5} maxLength={160} error={s.fields.contact} />
      <Field label="Date of purchase" name="purchase_date" type="date" required max={today()} error={s.fields.purchase_date} />
      <Field label="Bought from" name="seller" maxLength={160} hint="The distributor or dealer" />
      <TextareaField label="Where it is installed" name="address" maxLength={2000} rows={2} />
      <label className="field"><span>Invoice or receipt</span><input type="file" name="proof" accept="image/jpeg,image/png,image/webp,application/pdf" /></label>
      <Trap />
      <FormError message={s.error} />
      <button className="btn btn-primary" disabled={s.busy}>Send registration</button>
    </form>
  )
}

function ServiceForm({ serial, onSent }: { serial: string; onSent: (m: string) => void }) {
  const s = useSend(onSent)
  return (
    <form className="section" noValidate onSubmit={(e) => {
      e.preventDefault()
      const fd = new FormData(e.currentTarget)
      void s.send(() => api('/public/service-requests', { method: 'POST', body: { serial, name: fd.get('name'), phone: fd.get('phone'), issue: fd.get('issue'), preferred_visit: fd.get('preferred_visit'), website: fd.get('website') } }))
    }}>
      <h2>Ask for service</h2>
      <Field label="Your name" name="name" required minLength={2} maxLength={160} error={s.fields.name} />
      <Field label="Phone" name="phone" type="tel" required minLength={5} maxLength={60} error={s.fields.phone} />
      <TextareaField label="What is wrong" name="issue" required minLength={10} maxLength={4000} rows={3} error={s.fields.issue} />
      <Field label="When can we visit" name="preferred_visit" maxLength={200} placeholder="e.g. weekday mornings" />
      <Trap />
      <FormError message={s.error} />
      <button className="btn btn-primary" disabled={s.busy}>Send request</button>
    </form>
  )
}
