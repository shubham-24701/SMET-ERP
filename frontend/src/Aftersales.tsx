import { useState } from 'react'
import { Link, useNavigate, useParams } from 'react-router'
import { api, upload } from './api'
import { useAction, useApi, useCan, useMe, useWhen } from './lib'
import { Back, Facts, ItemOptions, LocationOptions, Status, day, newKey, num, serialList, today, useItems, useWarehouses, words, type Page, type Ref } from './ops'
import { useCustomers } from './Sales'
import { Dialog, Field, FormError, PageHead, PageState, Pager, SelectField, TextareaField } from './ui'

type Person = { id: string; name: string | null }
type Party = { id: string; code: string; name: string }
type FileRow = { id: string; kind: string; name: string; content_type: string; caption: string; uploaded_by: string | null; uploaded_at: string }
type VisitBrief = { id: string; number: string; stage: string; status: string; scheduled_on: string; done_on: string | null; technicians: Person[]; site?: { id: string; name: string | null } }
type Warranty = { state: string; registration?: { id: string; number: string }; start_date?: string; expiry_date?: string }
type AmcCover = { id: string; number: string; starts_on: string; ends_on: string; scope: string; covered: string[]; excluded: string[] }

const WARRANTY: Record<string, string> = { none: 'No warranty', unknown: 'No battery named yet', pending: 'Registration pending', active: 'Active',
  not_started: 'Starts later', expired: 'Expired', ended: 'Ended (replaced)' }
const PRIORITY: [string, string][] = [['urgent', 'Urgent'], ['high', 'High'], ['normal', 'Normal'], ['low', 'Low']]
const people = (ps: Person[]) => ps.map((t) => t.name).join(', ') || 'Not assigned'
const EVENT: [string, string][] = [['sale', 'Sale'], ['installation', 'Installation'], ['commissioning', 'Commissioning'], ['handover', 'Handover']]
const RULE: [string, string][] = [['original_remaining', "The rest of the original's term"], ['new_full', 'A full new term'], ['longer', 'Whichever is longer']]

function Files({ files, path, kinds, canAdd, onDone }: { files: FileRow[]; path: string; kinds: [string, string][]; canAdd: boolean; onDone: () => void }) {
  const action = useAction(onDone)
  if (!files.length && !canAdd) return null
  return (
    <section className="panel panel-wide section">
      <h2>Documents and photographs</h2>
      {files.length > 0 && <ul className="evidence">{files.map((f) => (
        <li key={f.id}>
          {f.content_type.startsWith('image/')
            ? <a href={`/api/v1/files/${f.id}`} target="_blank" rel="noreferrer"><img src={`/api/v1/files/${f.id}`} alt={f.caption || f.name} loading="lazy" /></a>
            : <a className="evidence-doc" href={`/api/v1/files/${f.id}`}>{f.name}</a>}
          <span className="small">{words(f.kind)}{f.caption && `: ${f.caption}`}</span>
          <span className="small muted">{f.uploaded_by}</span>
        </li>
      ))}</ul>}
      {canAdd && (
        <form className="row" noValidate onSubmit={(e) => {
          e.preventDefault()
          const el = e.currentTarget
          void action.run('upload', async () => { await upload(path, new FormData(el)); el.reset() })
        }}>
          <label className="field"><span>File</span><input type="file" name="file" required accept="image/jpeg,image/png,image/webp,application/pdf" /></label>
          <SelectField label="Kind" name="kind" options={kinds} />
          <Field label="Caption" name="caption" maxLength={300} />
          <button className="btn btn-secondary" disabled={!!action.busy}>Add</button>
        </form>
      )}
      <FormError message={action.error} />
    </section>
  )
}

// ======================= service requests =======================

type SrBrief = { id: string; number: string; source: string; status: string; priority: string; serial: { id: string; serial: string } | null
  reported_serial: string; site: { id: string; name: string } | null; customer: Party | null; contact_name: string; issue: string; coordinator: string | null; created_at: string; version: number }
type Sr = SrBrief & {
  contact_phone: string; preferred_visit: string; bank: { id: string; number: string; name: string; status: string } | null
  battery: { status: string; item: Ref; location: string | null; bank: { id: string; number: string } | null } | null
  warranty: Warranty; amc: AmcCover[]; history: { from: string; to: string; by: string; at: string; reason: string }[]
  diagnosis: string; work_done: string; parts: { item: string; name: string; qty: string; unit: string; lot: string; serials: string[]; by: string; on: string }[]
  resolution: string; replacement: { id: string; serial: string; status: string } | null; acknowledgement: { name?: string; on?: string }
  closure_notes: string; visits: VisitBrief[]; alerts: { id: string; number: string; status: string }[]; files: FileRow[]; coordinator_id: string | null
}
const NEXT: Record<string, string[]> = { new: ['under_review', 'scheduled'], under_review: ['scheduled', 'in_progress', 'pending_action'],
  scheduled: ['in_progress', 'pending_action', 'under_review'], in_progress: ['pending_action', 'scheduled'], pending_action: ['under_review', 'scheduled', 'in_progress'] }

export function ServiceRequestsPage() {
  const can = useCan()
  const nav = useNavigate()
  const when = useWhen()
  const [status, setStatus] = useState('new,under_review,scheduled,in_progress,pending_action')
  const [q, setQ] = useState('')
  const [offset, setOffset] = useState(0)
  const [adding, setAdding] = useState(false)
  const { data, error } = useApi<Page<SrBrief>>(`/service-requests?${new URLSearchParams({ q, offset: String(offset), limit: '50', ...(status ? { status } : {}) })}`)
  return (
    <>
      <PageHead title="Service requests" intro="Complaints and maintenance requests from customers, distributors, the public form and SMET's own teams. Urgent ones first.">
        {can('service.create') && <button className="btn btn-primary" onClick={() => setAdding(true)}>New request</button>}
      </PageHead>
      <div className="toolbar" role="search">
        <Field label="Number, serial, contact or issue" type="search" value={q} onChange={(e) => { setQ(e.target.value); setOffset(0) }} />
        <SelectField label="Status" value={status} onChange={(e) => { setStatus(e.target.value); setOffset(0) }} options={[['new,under_review,scheduled,in_progress,pending_action', 'Open'], ['new', 'New'], ['pending_action', 'Pending action'], ['closed', 'Closed'], ['', 'All']]} />
      </div>
      {error ? <PageState kind="error" title="Couldn't load requests" message={error} />
        : !data ? <PageState kind="loading" title="Loading…" />
        : data.items.length === 0 ? <p className="empty-line">No requests here.</p>
        : (
          <div className="table-wrap"><table className="table stack">
            <thead><tr><th scope="col">Request</th><th scope="col">Battery or site</th><th scope="col">Issue</th><th scope="col">Priority</th><th scope="col">Status</th></tr></thead>
            <tbody>{data.items.map((x) => (
              <tr key={x.id}>
                <td className="cell-main"><Link to={`/service/${x.id}`}>{x.number}</Link><span className="small muted">{when(x.created_at)}{x.source === 'public' && ' · public form'}</span></td>
                <td className="cell-sub">{x.serial ? <code>{x.serial.serial}</code> : x.reported_serial ? <span className="muted">{x.reported_serial} (not found)</span> : '—'}{x.site && <span className="small muted"> {x.site.name}</span>}</td>
                <td className="cell-sub clamp">{x.issue}</td>
                <td className="cell-tag"><Status s={x.priority} /></td>
                <td className="cell-tag"><Status s={x.status} /></td>
              </tr>
            ))}</tbody>
          </table></div>
        )}
      {data && <Pager offset={offset} limit={50} total={data.total} onChange={setOffset} />}
      {adding && <Dialog title="New service request" onClose={() => setAdding(false)}><NewSr onDone={(sr) => nav(`/service/${sr.id}`)} /></Dialog>}
    </>
  )
}

function NewSr({ onDone, serial }: { onDone: (sr: Sr) => void; serial?: string }) {
  const action = useAction(() => {})
  return (
    <form noValidate onSubmit={(e) => {
      e.preventDefault()
      const fd = new FormData(e.currentTarget)
      const s = (k: string) => String(fd.get(k) ?? '')
      void action.run('add', async () => onDone(await api<Sr>('/service-requests', { method: 'POST', body: { serial: s('serial').trim() || null, contact_name: s('contact_name'),
        contact_phone: s('contact_phone'), issue: s('issue'), priority: s('priority'), preferred_visit: s('preferred_visit') } })))
    }}>
      <Field label="Battery serial" name="serial" defaultValue={serial} maxLength={60} autoComplete="off" hint="The bank and site are filled in from it" />
      <TextareaField label="What is wrong" name="issue" required minLength={3} maxLength={4000} rows={3} />
      <div className="grid-fields">
        <SelectField label="Priority" name="priority" defaultValue="normal" options={PRIORITY} />
        <Field label="Contact" name="contact_name" maxLength={160} />
        <Field label="Phone" name="contact_phone" type="tel" maxLength={60} />
      </div>
      <Field label="Preferred visit" name="preferred_visit" maxLength={200} />
      <FormError message={action.error} />
      <button className="btn btn-primary" disabled={!!action.busy}>Open request</button>
    </form>
  )
}

/** Warranty and AMC side by side, each from its own check: one never implies the other. */
function Coverage({ warranty, amc }: { warranty: Warranty; amc: AmcCover[] }) {
  return (
    <div className="grid-2 section">
      <section className="panel">
        <h2>Warranty</h2>
        <p><Status s={warranty.state === 'active' ? 'active' : warranty.state === 'pending' ? 'pending' : warranty.state === 'expired' ? 'expired' : 'closed'} /> {WARRANTY[warranty.state] ?? warranty.state}</p>
        {warranty.expiry_date && <p className="small">{day(warranty.start_date)} to {day(warranty.expiry_date)}</p>}
        {warranty.registration && <p className="small"><Link to={`/warranty/${warranty.registration.id}`}>{warranty.registration.number}</Link></p>}
      </section>
      <section className="panel">
        <h2>Maintenance contract</h2>
        {amc.length === 0 ? <p className="muted">Not covered by any contract today.</p> : amc.map((c) => (
          <div key={c.id}>
            <p><Link to={`/amc/${c.id}`}>{c.number}</Link> to {day(c.ends_on)}</p>
            {c.covered.length > 0 && <p className="small">Covers: {c.covered.join('; ')}</p>}
            {c.excluded.length > 0 && <p className="small muted">Excludes: {c.excluded.join('; ')}</p>}
          </div>
        ))}
      </section>
    </div>
  )
}

type SrDialog = 'move' | 'visit' | 'receive' | 'part' | 'replace' | 'close' | 'details' | 'alert'

export function ServiceRequestPage() {
  const { id } = useParams()
  const can = useCan()
  const when = useWhen()
  const nav = useNavigate()
  const { data: sr, error, reload } = useApi<Sr>(`/service-requests/${id}`)
  const [dialog, setDialog] = useState<SrDialog | null>(null)
  const [key, setKey] = useState(newKey)
  const action = useAction(() => { setDialog(null); setKey(newKey()); reload() })
  const ppl = useApi<{ coordinators: Person[]; technicians: Person[] }>(dialog === 'visit' || dialog === 'details' ? '/service/people' : null).data
  const { warehouses } = useWarehouses(dialog === 'receive' || dialog === 'part' || dialog === 'replace')
  const items = useItems()
  if (error) return <PageState kind="error" title="Couldn't load the request" message={error} />
  if (!sr) return <PageState kind="loading" title="Loading…" />
  const open = sr.status !== 'closed'
  const edit = can('service.create') && open
  const stock = edit && can('stock.create')
  const post = (path: string, body: object) => api(`/service-requests/${sr.id}/${path}`, { method: 'POST', body: { version: sr.version, ...body } })
  const form = (fn: (fd: FormData) => Promise<unknown>) => (e: React.FormEvent<HTMLFormElement>) => { e.preventDefault(); void action.run('form', () => fn(new FormData(e.currentTarget))) }
  return (
    <>
      <Back to="/service">Service requests</Back>
      <PageHead title={sr.number} intro={`${sr.customer?.name ?? 'Customer not known yet'}${sr.site ? ` · ${sr.site.name}` : ''}`}>
        {edit && <button className="btn btn-secondary" onClick={() => setDialog('details')}>Assign and record</button>}
        {edit && NEXT[sr.status] && <button className="btn btn-secondary" onClick={() => setDialog('move')}>Change status</button>}
        {edit && sr.site && can('installation.create') && <button className="btn btn-secondary" onClick={() => setDialog('visit')}>Schedule visit</button>}
        {edit && <button className="btn btn-primary" onClick={() => setDialog('close')}>Close request</button>}
      </PageHead>
      <Facts rows={[['Status', <Status s={sr.status} />], ['Priority', <Status s={sr.priority} />], ['Battery', sr.serial ? <Link to={`/stock/serials/${sr.serial.id}`}>{sr.serial.serial}</Link> : sr.reported_serial ? `${sr.reported_serial} (not found)` : '—'],
        ['Bank', sr.bank ? <Link to={`/installation/banks/${sr.bank.id}`}>{sr.bank.number} {sr.bank.name}</Link> : '—'], ['Contact', [sr.contact_name, sr.contact_phone].filter(Boolean).join(', ') || '—'],
        ['Coordinator', sr.coordinator ?? 'Not assigned']]} />
      <p className="prewrap">{sr.issue}</p>
      {sr.preferred_visit && <p className="muted">Preferred visit: {sr.preferred_visit}</p>}
      {sr.status === 'closed' && <p className="notice notice-success">Closed: {words(sr.resolution)}. {sr.closure_notes}{sr.acknowledgement.name && ` Accepted by ${sr.acknowledgement.name} on ${day(sr.acknowledgement.on)}.`}</p>}
      <FormError message={action.error} />
      <Coverage warranty={sr.warranty} amc={sr.amc} />
      {sr.battery && (
        <section className="panel panel-wide">
          <div className="panel-head"><h2>The battery</h2>
            <span className="row">
              {stock && sr.battery.status !== 'in_repair' && !sr.battery.bank && <button className="btn btn-secondary" onClick={() => setDialog('receive')}>Receive at workshop</button>}
              {stock && sr.battery.status === 'in_repair' && <button className="btn btn-secondary" onClick={() => action.run('return', () => post('return-battery', { key }), 'Send it back to the customer?')}>Return to customer</button>}
              {stock && !sr.replacement && <button className="btn btn-secondary" onClick={() => setDialog('replace')}>Send a replacement</button>}
              {stock && <button className="btn btn-secondary" onClick={() => setDialog('part')}>Use a part</button>}
            </span>
          </div>
          <p><Status s={sr.battery.status} /> {sr.battery.item.code} · {sr.battery.item.name}{sr.battery.location && ` · at ${sr.battery.location}`}{sr.battery.bank && ` · in ${sr.battery.bank.number}`}</p>
          {sr.replacement && <p>Replacement: <Link to={`/stock/serials/${sr.replacement.id}`}>{sr.replacement.serial}</Link> <Status s={sr.replacement.status} />. A technician puts it in the original's place on the service visit; its warranty is set when this request closes.</p>}
          {sr.battery.bank && open && <p className="hint">To take it out of its bank, replace or remove it on the service visit.</p>}
        </section>
      )}
      {(sr.diagnosis || sr.work_done) && <section className="panel panel-wide"><h2>Diagnosis and work</h2>
        {sr.diagnosis && <p className="prewrap"><strong>Diagnosis.</strong> {sr.diagnosis}</p>}{sr.work_done && <p className="prewrap"><strong>Work done.</strong> {sr.work_done}</p>}</section>}
      {sr.parts.length > 0 && <section className="panel panel-wide"><h2>Parts used</h2><ul className="feed">{sr.parts.map((x, i) => (
        <li key={i}><span>{x.item} · {x.name}<span className="small muted">{[x.lot && `lot ${x.lot}`, x.serials.join(', '), x.by].filter(Boolean).join(' · ')}</span></span><strong>{x.qty} {x.unit}</strong></li>))}</ul></section>}
      <div className="grid-2 section">
        <section className="panel">
          <h2>Visits</h2>
          {sr.visits.length === 0 ? <p className="muted">None yet.</p> : <ul className="feed">{sr.visits.map((v) => (
            <li key={v.id}><span><Link to={`/installation/visits/${v.id}`}>{v.number}</Link><span className="small muted">{day(v.done_on ?? v.scheduled_on)} · {people(v.technicians)}</span></span><Status s={v.status} /></li>))}</ul>}
        </section>
        <section className="panel">
          <div className="panel-head"><h2>Maintenance alerts</h2>{can('codes.create') && open && <button className="btn btn-ghost" onClick={() => setDialog('alert')}>Register alert</button>}</div>
          {sr.alerts.length === 0 ? <p className="muted">None.</p> : <ul className="feed">{sr.alerts.map((a) => (
            <li key={a.id}><Link to={`/service/alerts/${a.id}`}>{a.number}</Link><Status s={a.status} /></li>))}</ul>}
        </section>
      </div>
      <Files files={sr.files} path={`/service-requests/${sr.id}/files`} kinds={[['photo', 'Photograph'], ['test_report', 'Test report'], ['acknowledgement', 'Signed acknowledgement'], ['other', 'Other']]} canAdd={can('service.create')} onDone={reload} />
      <h2 className="section">History</h2>
      <ol className="timeline panel panel-wide">
        <li><span><strong>Opened</strong><span className="small muted">{sr.source === 'public' ? 'Public form' : 'By SMET staff'}</span></span><span className="stamp">{when(sr.created_at)}</span></li>
        {sr.history.map((h, i) => <li key={i}><span><strong>{words(h.to)}</strong><span className="small muted">{[h.by, h.reason].filter(Boolean).join(' · ')}</span></span><span className="stamp">{when(h.at)}</span></li>)}
      </ol>

      {dialog === 'move' && <Dialog title="Change status" onClose={() => setDialog(null)}>
        <form noValidate onSubmit={form((fd) => post('status', { to: fd.get('to'), reason: fd.get('reason') }))}>
          <SelectField label="To" name="to" options={NEXT[sr.status].map((s): [string, string] => [s, words(s)])} />
          <TextareaField label="Reason" name="reason" maxLength={500} rows={2} hint="Needed for pending action: say what it is waiting for" />
          <FormError message={action.error} /><button className="btn btn-primary" disabled={!!action.busy}>Change status</button>
        </form></Dialog>}
      {dialog === 'details' && <Dialog title="Assign and record" onClose={() => setDialog(null)}>
        <form noValidate onSubmit={form((fd) => api(`/service-requests/${sr.id}`, { method: 'PATCH', body: { version: sr.version, coordinator_id: fd.get('coordinator_id') || null,
          priority: fd.get('priority'), diagnosis: fd.get('diagnosis'), work_done: fd.get('work_done'), ...(fd.get('serial') ? { serial: fd.get('serial') } : {}) } }))}>
          {!sr.serial && <Field label="Battery serial" name="serial" defaultValue={sr.reported_serial} maxLength={60} hint="Fills in the bank, site and customer" />}
          <SelectField label="Coordinator" name="coordinator_id" defaultValue={sr.coordinator_id ?? ''} options={[['', 'Not assigned'], ...(ppl?.coordinators ?? []).map((p): [string, string] => [p.id, p.name ?? ''])]} />
          <SelectField label="Priority" name="priority" defaultValue={sr.priority} options={PRIORITY} />
          <TextareaField label="Diagnosis and inspection results" name="diagnosis" defaultValue={sr.diagnosis} maxLength={4000} rows={3} />
          <TextareaField label="Work done" name="work_done" defaultValue={sr.work_done} maxLength={4000} rows={3} />
          <FormError message={action.error} /><button className="btn btn-primary" disabled={!!action.busy}>Save</button>
        </form></Dialog>}
      {dialog === 'visit' && <Dialog title="Schedule a service visit" onClose={() => setDialog(null)}>
        <form noValidate onSubmit={form((fd) => post('visit', { scheduled_on: fd.get('scheduled_on'), time_window: fd.get('time_window'), technician_ids: fd.getAll('tech') }))}>
          <div className="grid-fields"><Field label="Date" name="scheduled_on" type="date" required defaultValue={today()} /><Field label="Time" name="time_window" maxLength={60} /></div>
          <fieldset className="plain"><legend>Technicians</legend>{(ppl?.technicians ?? []).map((t) => <label key={t.id} className="check"><input type="checkbox" name="tech" value={t.id} /> <span>{t.name}</span></label>)}</fieldset>
          <FormError message={action.error} /><button className="btn btn-primary" disabled={!!action.busy}>Schedule visit</button>
        </form></Dialog>}
      {dialog === 'receive' && <Dialog title={`Receive ${sr.serial?.serial} at the workshop`} onClose={() => setDialog(null)}>
        <form noValidate onSubmit={form((fd) => post('receive', { key, location_id: fd.get('location_id') }))}>
          <label className="field"><span>Into</span><select name="location_id" required><LocationOptions warehouses={warehouses} purpose="repair" /></select></label>
          <FormError message={action.error} /><button className="btn btn-primary" disabled={!!action.busy}>Receive</button>
        </form></Dialog>}
      {dialog === 'replace' && <Dialog title="Send a replacement battery" onClose={() => setDialog(null)}>
        <form noValidate onSubmit={form((fd) => post('send-replacement', { key, serial: String(fd.get('serial') ?? '').trim(), location_id: fd.get('location_id') }))}>
          <p className="hint">It leaves available stock for {sr.site?.name ?? "the customer's site"}. A technician then replaces {sr.serial?.serial} with it on the service visit.</p>
          <Field label="Replacement's serial" name="serial" required maxLength={60} autoComplete="off" />
          <label className="field"><span>From</span><select name="location_id" required><LocationOptions warehouses={warehouses} purpose="available" /></select></label>
          <FormError message={action.error} /><button className="btn btn-primary" disabled={!!action.busy}>Send replacement</button>
        </form></Dialog>}
      {dialog === 'part' && <Dialog title="Use a part" onClose={() => setDialog(null)}>
        <form noValidate onSubmit={form((fd) => post('parts', { key, item_id: fd.get('item_id'), qty: num(String(fd.get('qty'))), location_id: fd.get('location_id'), lot: fd.get('lot') ?? '', serials: serialList(String(fd.get('serials') ?? '')) }))}>
          <label className="field"><span>Part</span><select name="item_id" required><ItemOptions items={items} /></select></label>
          <div className="grid-fields"><Field label="Quantity" name="qty" type="number" min={0} step="any" required defaultValue="1" /><Field label="Lot" name="lot" maxLength={60} /></div>
          <TextareaField label="Serial numbers (if tracked)" name="serials" rows={2} />
          <label className="field"><span>From</span><select name="location_id" required><LocationOptions warehouses={warehouses} purpose="available" /></select></label>
          <FormError message={action.error} /><button className="btn btn-primary" disabled={!!action.busy}>Use part</button>
        </form></Dialog>}
      {dialog === 'alert' && <Dialog title="Register a maintenance alert" onClose={() => setDialog(null)}>
        <form noValidate onSubmit={(e) => { e.preventDefault(); const fd = new FormData(e.currentTarget)
          void action.run('alert', async () => { const a = await api<{ id: string }>('/maintenance-alerts', { method: 'POST', body: { service_request_id: sr.id, serial: fd.get('serial'), description: fd.get('description') } }); nav(`/service/alerts/${a.id}`) }) }}>
          <Field label="Battery showing the alert" name="serial" defaultValue={sr.serial?.serial ?? ''} maxLength={60} />
          <TextareaField label="What the alert says" name="description" required minLength={3} maxLength={2000} rows={3} />
          <FormError message={action.error} /><button className="btn btn-primary" disabled={!!action.busy}>Register alert</button>
        </form></Dialog>}
      {dialog === 'close' && <Dialog title={`Close ${sr.number}`} onClose={() => setDialog(null)}>
        <form noValidate onSubmit={form((fd) => post('close', { resolution: fd.get('resolution'), closure_notes: fd.get('closure_notes'), acknowledgement: { name: fd.get('ack_name') ?? '', on: fd.get('ack_on') || null } }))}>
          <SelectField label="Outcome" name="resolution" options={[['repaired', 'Repaired'], ['replaced', 'Replaced'], ['returned', 'Returned'], ['no_fault', 'No fault found'], ['other', 'Other']]} />
          <p className="hint">Replaced: the replacement takes a warranty under the original battery's policy, and the original's ends.</p>
          <TextareaField label="Closure notes" name="closure_notes" required minLength={3} maxLength={4000} rows={3} />
          <div className="grid-fields"><Field label="Accepted by" name="ack_name" maxLength={160} /><Field label="On" name="ack_on" type="date" max={today()} /></div>
          <FormError message={action.error} /><button className="btn btn-primary" disabled={!!action.busy}>Close request</button>
        </form></Dialog>}
    </>
  )
}

// ======================= maintenance alerts and codes =======================

type Alert = { id: string; number: string; status: string; serial: { id: string; serial: string }; service_request: { id: string; number: string; status: string }
  description: string; has_code: boolean; created_at: string; version: number; item?: Ref; verification?: { checked?: Record<string, string | null>; note?: string }
  verified_by?: string | null; verified_at?: string | null; recipient?: string; supplier_reference?: string; issued_on?: string | null; expires_on?: string | null
  issued_by?: string | null; cleared?: boolean | null; cleared_note?: string; closed_at?: string | null; purged_at?: string | null
  facts?: { customer: string | null; site: string | null; address: string | null; bank: string | null; status: string }; evidence?: { id: string; number: string; done_on: string }[] }

export function AlertsPage() {
  const when = useWhen()
  const [status, setStatus] = useState('open,verified,code_recorded')
  const { data, error } = useApi<Page<Alert>>(`/maintenance-alerts?limit=100${status ? `&status=${status}` : ''}`)
  return (
    <>
      <PageHead title="Maintenance alerts" intro="Batteries asking for a maintenance reset code. A code comes from the battery supplier and is typed in by an authorised person; it is stored encrypted and never shown in lists." />
      <div className="toolbar"><SelectField label="Status" value={status} onChange={(e) => setStatus(e.target.value)} options={[['open,verified,code_recorded', 'Open'], ['closed', 'Closed'], ['', 'All']]} /></div>
      {error ? <PageState kind="error" title="Couldn't load alerts" message={error} /> : !data ? <PageState kind="loading" title="Loading…" />
        : data.items.length === 0 ? <p className="empty-line">No alerts here. Alerts are registered from a service request.</p> : (
          <div className="table-wrap"><table className="table stack">
            <thead><tr><th scope="col">Alert</th><th scope="col">Battery</th><th scope="col">Service request</th><th scope="col">Status</th></tr></thead>
            <tbody>{data.items.map((a) => (
              <tr key={a.id}><td className="cell-main"><Link to={`/service/alerts/${a.id}`}>{a.number}</Link><span className="small muted">{when(a.created_at)}</span></td>
                <td className="cell-sub"><code>{a.serial.serial}</code></td><td className="cell-sub">{a.service_request.number}</td><td className="cell-tag"><Status s={a.status} /></td></tr>
            ))}</tbody>
          </table></div>
        )}
    </>
  )
}

export function AlertPage() {
  const { id } = useParams()
  const can = useCan()
  const when = useWhen()
  const { data: a, error, reload } = useApi<Alert>(`/maintenance-alerts/${id}`)
  const [dialog, setDialog] = useState<'verify' | 'code' | 'reveal' | 'close' | null>(null)
  const [code, setCode] = useState('')
  const action = useAction(() => { setDialog(null); reload() })
  const reveal = useAction(() => {}) // keeps the dialog open to show the code
  if (error) return <PageState kind="error" title="Couldn't load the alert" message={error} />
  if (!a) return <PageState kind="loading" title="Loading…" />
  const post = (path: string, body: object) => api(`/maintenance-alerts/${a.id}/${path}`, { method: 'POST', body: { version: a.version, ...body } })
  const f = a.facts!
  return (
    <>
      <Back to="/service/alerts">Maintenance alerts</Back>
      <PageHead title={a.number} intro={`${a.serial.serial} · ${a.item?.name ?? ''}`}>
        {can('codes.create') && a.status === 'open' && <button className="btn btn-primary" onClick={() => setDialog('verify')}>Verify the battery</button>}
        {can('codes.reveal') && a.status === 'verified' && <button className="btn btn-primary" onClick={() => setDialog('code')}>Record the code</button>}
        {can('codes.reveal') && a.has_code && <button className="btn btn-secondary" onClick={() => { setCode(''); setDialog('reveal') }}>Show the code</button>}
        {can('codes.create') && a.status !== 'closed' && <button className="btn btn-secondary" onClick={() => setDialog('close')}>Close alert</button>}
      </PageHead>
      <Facts rows={[['Status', <Status s={a.status} />], ['Service request', <Link to={`/service/${a.service_request.id}`}>{a.service_request.number}</Link>],
        ['Customer', f.customer ?? '—'], ['Installed at', [f.site, f.bank].filter(Boolean).join(', ') || '—'], ['Code', a.has_code ? `Recorded ${day(a.issued_on)}, given to ${a.recipient}` : a.purged_at ? 'Erased after the retention period' : 'Not recorded']]} />
      <p className="prewrap">{a.description}</p>
      <FormError message={action.error} />
      <div className="grid-2 section">
        <section className="panel"><h2>Verification</h2>
          {a.verified_at ? <p>Battery, customer and location confirmed by {a.verified_by}, {when(a.verified_at)}.{a.verification?.note && ` ${a.verification.note}`}</p> : <p className="muted">Not verified yet.</p>}
        </section>
        <section className="panel"><h2>Inspection done</h2>
          {a.evidence!.length ? <ul className="feed">{a.evidence!.map((v) => <li key={v.id}><Link to={`/installation/visits/${v.id}`}>{v.number}</Link><span className="stamp">{day(v.done_on)}</span></li>)}</ul>
            : <p className="muted">No completed visit on {a.service_request.number} yet. A code is recorded only after the inspection.</p>}
        </section>
      </div>
      {a.status === 'closed' && <p className="notice">Closed {when(a.closed_at)}: alert {a.cleared === true ? 'cleared' : a.cleared === false ? 'not cleared' : 'clearing not confirmed'}. {a.cleared_note}</p>}

      {dialog === 'verify' && <Dialog title="Verify before any code" onClose={() => setDialog(null)}>
        <form noValidate onSubmit={(e) => { e.preventDefault(); const fd = new FormData(e.currentTarget)
          void action.run('verify', () => post('verify', { serial_confirmed: fd.get('s') === 'on', customer_confirmed: fd.get('c') === 'on', location_confirmed: fd.get('l') === 'on', note: fd.get('note') })) }}>
          <p className="hint">Compare what the caller or technician reports with SMET's record.</p>
          <label className="check"><input type="checkbox" name="s" /> <span>The serial on the battery is {a.serial.serial}</span></label>
          <label className="check"><input type="checkbox" name="c" /> <span>The customer is {f.customer ?? '(none on record)'}</span></label>
          <label className="check"><input type="checkbox" name="l" /> <span>It is installed at {[f.site, f.address, f.bank].filter(Boolean).join(', ') || '(no location on record)'}</span></label>
          <TextareaField label="Note" name="note" maxLength={500} rows={2} />
          <FormError message={action.error} /><button className="btn btn-primary" disabled={!!action.busy}>Confirm verification</button>
        </form></Dialog>}
      {dialog === 'code' && <Dialog title="Record the supplier's code" onClose={() => setDialog(null)}>
        <form noValidate autoComplete="off" onSubmit={(e) => { e.preventDefault(); const fd = new FormData(e.currentTarget)
          void action.run('code', () => post('code', { code: fd.get('code'), recipient: fd.get('recipient'), issued_on: fd.get('issued_on'), supplier_reference: fd.get('supplier_reference'), expires_on: fd.get('expires_on') || null })) }}>
          <p className="hint">Stored encrypted and recorded once. It is never shown in lists or written to any log.</p>
          <Field label="Code" name="code" required maxLength={200} autoComplete="off" spellCheck={false} />
          <Field label="Given to" name="recipient" required maxLength={160} />
          <div className="grid-fields"><Field label="Issued on" name="issued_on" type="date" required defaultValue={today()} max={today()} /><Field label="Expires on" name="expires_on" type="date" hint="Only if the supplier states it" /></div>
          <Field label="Supplier's reference" name="supplier_reference" maxLength={120} />
          <FormError message={action.error} /><button className="btn btn-primary" disabled={!!action.busy}>Record code</button>
        </form></Dialog>}
      {dialog === 'reveal' && <Dialog title="Show the code" onClose={() => { setCode(''); setDialog(null) }}>
        {code ? <><p className="code-box" aria-live="polite">{code}</p><p className="hint">Your view has been recorded in the audit log.</p></> : (
          <form noValidate onSubmit={(e) => { e.preventDefault(); const reason = new FormData(e.currentTarget).get('reason')
            void reveal.run('reveal', async () => setCode((await api<{ code: string }>(`/maintenance-alerts/${a.id}/reveal`, { method: 'POST', body: { reason } })).code)) }}>
            <TextareaField label="Why do you need it?" name="reason" required minLength={3} maxLength={500} rows={2} hint="Kept in the audit log with your name" />
            <FormError message={reveal.error} /><button className="btn btn-primary" disabled={!!reveal.busy}>Show code</button>
          </form>
        )}</Dialog>}
      {dialog === 'close' && <Dialog title={`Close ${a.number}`} onClose={() => setDialog(null)}>
        <form noValidate onSubmit={(e) => { e.preventDefault(); const fd = new FormData(e.currentTarget); const c = String(fd.get('cleared'))
          void action.run('close', () => post('close', { cleared: c === 'yes' ? true : c === 'no' ? false : null, note: fd.get('note') })) }}>
          <SelectField label="Was the alert cleared on the battery?" name="cleared" options={[['yes', 'Yes, seen cleared'], ['no', 'No'], ['unknown', 'Not confirmed']]} />
          <TextareaField label="Note" name="note" required minLength={3} maxLength={500} rows={2} />
          <FormError message={action.error} /><button className="btn btn-primary" disabled={!!action.busy}>Close alert</button>
        </form></Dialog>}
    </>
  )
}

// ======================= maintenance contracts =======================

type AmcBrief = { id: string; number: string; customer: Party; scope: string; reference: string; starts_on: string; ends_on: string; status: string; visit_every_months: number; version: number }
type Amc = AmcBrief & { site: { id: string; name: string } | null; banks: { id: string; number: string; name: string }[]; service_scope: string; covered: string[]; excluded: string[]
  cancel_reason: string; batteries: { id: string; serial: string; item: Ref; status: string; bank: { id: string; number: string } | null }[]
  visits: VisitBrief[]; renewed_from: { id: string; number: string } | null; renewed_by: { id: string; number: string } | null }
type Reminders = { upcoming: VisitBrief[]; overdue: VisitBrief[]; expiring: AmcBrief[]; renewal_notice_days: number }
const SCOPE: Record<string, string> = { customer: 'All their batteries', site: 'A site', bank: 'Battery banks', batteries: 'Chosen batteries' }

export function AmcListPage() {
  const can = useCan()
  const nav = useNavigate()
  const [status, setStatus] = useState('active,draft')
  const [offset, setOffset] = useState(0)
  const [adding, setAdding] = useState(false)
  const { data, error } = useApi<Page<AmcBrief>>(`/amc?${new URLSearchParams({ offset: String(offset), limit: '50', ...(status ? { status } : {}) })}`)
  const rem = useApi<Reminders>('/amc/reminders').data
  return (
    <>
      <PageHead title="Maintenance contracts" intro="Annual maintenance contracts (AMC): what they cover and the planned visits. Coverage is separate from warranty.">
        {can('amc.create') && <button className="btn btn-primary" onClick={() => setAdding(true)}>New contract</button>}
      </PageHead>
      {rem && (rem.overdue.length + rem.upcoming.length + rem.expiring.length > 0) && (
        <div className="grid-2 section">
          <section className="panel"><h2>Maintenance visits</h2><ul className="feed">
            {rem.overdue.map((v) => <li key={v.id}><span><Link to={`/installation/visits/${v.id}`}>{v.number}</Link><span className="small muted">{people(v.technicians)}</span></span><span className="danger-ink small">Overdue since {day(v.scheduled_on)}</span></li>)}
            {rem.upcoming.map((v) => <li key={v.id}><span><Link to={`/installation/visits/${v.id}`}>{v.number}</Link><span className="small muted">{people(v.technicians)}</span></span><span className="stamp">{day(v.scheduled_on)}</span></li>)}
            {!rem.overdue.length && !rem.upcoming.length && <li className="muted">None in the next 14 days.</li>}
          </ul></section>
          <section className="panel"><h2>To renew</h2><ul className="feed">
            {rem.expiring.map((c) => <li key={c.id}><span><Link to={`/amc/${c.id}`}>{c.number}</Link><span className="small muted">{c.customer.name}</span></span><span className="stamp">Ends {day(c.ends_on)}</span></li>)}
            {!rem.expiring.length && <li className="muted">Nothing ends in the next {rem.renewal_notice_days} days.</li>}
          </ul></section>
        </div>
      )}
      <div className="toolbar"><SelectField label="Status" value={status} onChange={(e) => { setStatus(e.target.value); setOffset(0) }} options={[['active,draft', 'Active or draft'], ['renewed', 'Renewed'], ['cancelled', 'Cancelled'], ['', 'All']]} /></div>
      {error ? <PageState kind="error" title="Couldn't load contracts" message={error} /> : !data ? <PageState kind="loading" title="Loading…" />
        : data.items.length === 0 ? <p className="empty-line">No contracts here.</p> : (
          <div className="table-wrap"><table className="table stack">
            <thead><tr><th scope="col">Contract</th><th scope="col">Customer</th><th scope="col">Covers</th><th scope="col">Period</th><th scope="col">Status</th></tr></thead>
            <tbody>{data.items.map((c) => (
              <tr key={c.id}><td className="cell-main"><Link to={`/amc/${c.id}`}>{c.number}</Link>{c.reference && <span className="small muted">{c.reference}</span>}</td>
                <td className="cell-sub">{c.customer.name}</td><td className="cell-sub">{SCOPE[c.scope]}, every {c.visit_every_months} months</td>
                <td className="cell-sub nowrap">{day(c.starts_on)} – {day(c.ends_on)}</td><td className="cell-tag"><Status s={c.status} /></td></tr>
            ))}</tbody>
          </table></div>
        )}
      {data && <Pager offset={offset} limit={50} total={data.total} onChange={setOffset} />}
      {adding && <Dialog wide title="New maintenance contract" onClose={() => setAdding(false)}><AmcForm onDone={(c) => nav(`/amc/${c.id}`)} /></Dialog>}
    </>
  )
}

function AmcForm({ onDone }: { onDone: (c: Amc) => void }) {
  const customers = useCustomers().filter((p) => p.kinds.includes('customer'))
  const [customer, setCustomer] = useState('')
  const [scope, setScope] = useState('bank')
  const cust = customer || customers[0]?.id || ''
  const banks = useApi<Page<{ id: string; number: string; name: string; site: { id: string; name: string } }>>(cust ? `/banks?customer_id=${cust}&limit=200` : null).data?.items ?? []
  const sites = [...new Map(banks.map((b) => [b.site.id, b.site])).values()]
  const action = useAction(() => {})
  const lines = (v: FormDataEntryValue | null) => String(v ?? '').split('\n').map((x) => x.trim()).filter(Boolean)
  return (
    <form noValidate onSubmit={(e) => {
      e.preventDefault()
      const fd = new FormData(e.currentTarget)
      void action.run('add', async () => onDone(await api<Amc>('/amc', { method: 'POST', body: { customer_id: cust, scope, site_id: scope === 'site' ? fd.get('site_id') : null,
        bank_ids: scope === 'bank' ? fd.getAll('bank') : [], serials: scope === 'batteries' ? serialList(String(fd.get('serials') ?? '')) : [], reference: fd.get('reference'),
        starts_on: fd.get('starts_on'), ends_on: fd.get('ends_on'), visit_every_months: num(String(fd.get('every'))), service_scope: fd.get('service_scope'),
        covered: lines(fd.get('covered')), excluded: lines(fd.get('excluded')) } })))
    }}>
      <div className="grid-fields">
        <SelectField label="Customer" value={cust} onChange={(e) => setCustomer(e.target.value)} options={customers.map((c) => [c.id, c.name])} />
        <SelectField label="Covers" value={scope} onChange={(e) => setScope(e.target.value)} options={Object.entries(SCOPE)} />
        <Field label="Contract reference" name="reference" maxLength={80} />
      </div>
      {scope === 'site' && <SelectField label="Site" name="site_id" options={sites.map((s) => [s.id, s.name])} hint={sites.length ? undefined : 'This customer has no site with a battery bank yet'} />}
      {scope === 'bank' && <fieldset className="plain"><legend>Battery banks</legend>{banks.length === 0 ? <p className="muted">This customer has no battery banks yet.</p>
        : banks.map((b) => <label key={b.id} className="check"><input type="checkbox" name="bank" value={b.id} /> <span>{b.number} {b.name} <span className="small muted">{b.site.name}</span></span></label>)}</fieldset>}
      {scope === 'batteries' && <TextareaField label="Serial numbers" name="serials" rows={3} />}
      <div className="grid-fields">
        <Field label="Starts" name="starts_on" type="date" required defaultValue={today()} />
        <Field label="Ends" name="ends_on" type="date" required />
        <Field label="Visit every (months)" name="every" type="number" min={1} max={24} required defaultValue="3" />
      </div>
      <TextareaField label="Service scope" name="service_scope" maxLength={2000} rows={2} />
      <div className="grid-2">
        <TextareaField label="Covered services, one per line" name="covered" rows={3} />
        <TextareaField label="Excluded, one per line" name="excluded" rows={3} />
      </div>
      <FormError message={action.error} />
      <button className="btn btn-primary" disabled={!!action.busy || !cust}>Create draft</button>
    </form>
  )
}

export function AmcPage() {
  const { id } = useParams()
  const can = useCan()
  const nav = useNavigate()
  const { data: c, error, reload } = useApi<Amc>(`/amc/${id}`)
  const [dialog, setDialog] = useState<'renew' | 'cancel' | null>(null)
  const action = useAction(() => { setDialog(null); reload() })
  if (error) return <PageState kind="error" title="Couldn't load the contract" message={error} />
  if (!c) return <PageState kind="loading" title="Loading…" />
  const edit = can('amc.create')
  const post = (path: string, body: object = {}) => api<Amc>(`/amc/${c.id}/${path}`, { method: 'POST', body: { version: c.version, ...body } })
  return (
    <>
      <Back to="/amc">Maintenance contracts</Back>
      <PageHead title={c.number} intro={`${c.customer.name}${c.reference ? ` · ${c.reference}` : ''}`}>
        {edit && c.status === 'draft' && <button className="btn btn-primary" onClick={() => action.run('activate', () => post('activate'), 'Activate it and plan its maintenance visits?')}>Activate</button>}
        {edit && c.status === 'active' && <button className="btn btn-primary" onClick={() => setDialog('renew')}>Renew</button>}
        {edit && ['draft', 'active'].includes(c.status) && <button className="btn btn-ghost" onClick={() => setDialog('cancel')}>Cancel contract</button>}
      </PageHead>
      <Facts rows={[['Status', <Status s={c.status} />], ['Period', `${day(c.starts_on)} – ${day(c.ends_on)}`], ['Visits', `Every ${c.visit_every_months} months`],
        ['Covers', `${SCOPE[c.scope]}${c.site ? `: ${c.site.name}` : ''}${c.banks.length ? `: ${c.banks.map((b) => b.number).join(', ')}` : ''}`],
        ['Renewal', c.renewed_by ? <Link to={`/amc/${c.renewed_by.id}`}>Renewed as {c.renewed_by.number}</Link> : c.renewed_from ? <Link to={`/amc/${c.renewed_from.id}`}>Renews {c.renewed_from.number}</Link> : '—']]} />
      {c.status === 'cancelled' && <p className="notice notice-warning">Cancelled: {c.cancel_reason}</p>}
      <FormError message={action.error} />
      {c.service_scope && <p className="prewrap">{c.service_scope}</p>}
      <div className="grid-2 section">
        <section className="panel"><h2>Covered</h2>{c.covered.length ? <ul>{c.covered.map((x) => <li key={x}>{x}</li>)}</ul> : <p className="muted">Not listed.</p>}</section>
        <section className="panel"><h2>Excluded</h2>{c.excluded.length ? <ul>{c.excluded.map((x) => <li key={x}>{x}</li>)}</ul> : <p className="muted">Not listed.</p>}</section>
      </div>
      <h2 className="section">Batteries covered now</h2>
      {c.batteries.length === 0 ? <p className="empty-line">None yet.</p> : (
        <div className="table-wrap"><table className="table stack"><thead><tr><th scope="col">Serial</th><th scope="col">Item</th><th scope="col">Bank</th><th scope="col">Status</th></tr></thead>
          <tbody>{c.batteries.map((b) => <tr key={b.id}><td className="cell-main"><Link to={`/stock/serials/${b.id}`}><code>{b.serial}</code></Link></td><td className="cell-sub">{b.item.code}</td>
            <td className="cell-sub">{b.bank ? <Link to={`/installation/banks/${b.bank.id}`}>{b.bank.number}</Link> : '—'}</td><td className="cell-tag"><Status s={b.status} /></td></tr>)}</tbody></table></div>
      )}
      <h2 className="section">Planned visits</h2>
      {c.visits.length === 0 ? <p className="empty-line">{c.status === 'draft' ? 'Planned when the contract is activated.' : 'None.'}</p> : (
        <ul className="feed panel panel-wide">{c.visits.map((v) => (
          <li key={v.id}><span><Link to={`/installation/visits/${v.id}`}>{v.number}</Link><span className="small muted">{v.site?.name} · {people(v.technicians)}</span></span><span className="row-end"><span className="stamp">{day(v.done_on ?? v.scheduled_on)}</span><Status s={v.status} /></span></li>
        ))}</ul>
      )}
      {dialog === 'renew' && <Dialog title={`Renew ${c.number}`} onClose={() => setDialog(null)}>
        <form noValidate onSubmit={(e) => { e.preventDefault(); const fd = new FormData(e.currentTarget)
          void action.run('renew', async () => { const n = await post('renew', { starts_on: fd.get('starts_on'), ends_on: fd.get('ends_on'), reference: fd.get('reference') }); nav(`/amc/${n.id}`) }) }}>
          <p className="hint">A new draft with the same coverage and terms. This contract keeps its visits and covers until {day(c.ends_on)}.</p>
          <div className="grid-fields"><Field label="Starts" name="starts_on" type="date" required /><Field label="Ends" name="ends_on" type="date" required /></div>
          <Field label="Contract reference" name="reference" maxLength={80} defaultValue={c.reference} />
          <FormError message={action.error} /><button className="btn btn-primary" disabled={!!action.busy}>Create renewal</button>
        </form></Dialog>}
      {dialog === 'cancel' && <Dialog title={`Cancel ${c.number}`} onClose={() => setDialog(null)}>
        <form noValidate onSubmit={(e) => { e.preventDefault(); void action.run('cancel', () => post('cancel', { reason: new FormData(e.currentTarget).get('reason') })) }}>
          <p className="hint">Visits still to come are cancelled; completed ones stay.</p>
          <TextareaField label="Reason" name="reason" required minLength={3} maxLength={500} />
          <FormError message={action.error} /><button className="btn btn-primary" disabled={!!action.busy}>Cancel contract</button>
        </form></Dialog>}
    </>
  )
}

// ======================= warranty =======================

type RegBrief = { id: string; number: string; serial: { id: string; serial: string }; item: Ref; route: string; status: string; expired: boolean; customer_name: string
  start_date: string | null; expiry_date: string | null; created_at: string; version: number }
type Policy = { id: string; item: Ref; months: number; start_event: string; auto_approve: boolean; requires_proof: boolean; replacement_rule: string
  effective_from: string; notes: string; active: boolean; used: number; version: number }
type Reg = RegBrief & { customer: Party | null; customer_contact: string; address: string; distributor: Party | null; distributor_name: string
  sale_date: string | null; installation_date: string | null; commissioning_date: string | null; handover_date: string | null; notes: string; policy: Policy | null
  would_start: string | null; would_expire: string | null; missing: string[]; ended_on: string | null; end_reason: string; replaces: { id: string; number: string } | null
  submitted_by: string; submitted_by_id: string | null; decided_by: string | null; decided_at: string | null; decision_reason: string; files: FileRow[]
  history: { at: string; who: string; action: string; summary: string; reason: string; changes: Record<string, [string, string]> }[] }
const ROUTE: Record<string, string> = { smet_installation: 'SMET installation', distributor: 'Distributor sale', direct: 'Direct sale', public: 'Public form', replacement: 'Replacement' }

export function WarrantyPage() {
  const can = useCan()
  const nav = useNavigate()
  const [status, setStatus] = useState('pending,correction_required')
  const [q, setQ] = useState('')
  const [offset, setOffset] = useState(0)
  const [adding, setAdding] = useState(false)
  const { data, error } = useApi<Page<RegBrief>>(`/warranty-registrations?${new URLSearchParams({ q, offset: String(offset), limit: '50', ...(status ? { status } : {}) })}`)
  return (
    <>
      <PageHead title="Warranty registrations" intro="Every battery's warranty, checked against SMET's policy for its model. SMET's own handovers open them; distributors' and customers' sales are registered here or on the public form.">
        {can('warranty.create') && <button className="btn btn-primary" onClick={() => setAdding(true)}>Register a battery</button>}
      </PageHead>
      <div className="toolbar" role="search">
        <Field label="Number, serial or customer" type="search" value={q} onChange={(e) => { setQ(e.target.value); setOffset(0) }} />
        <SelectField label="Status" value={status} onChange={(e) => { setStatus(e.target.value); setOffset(0) }} options={[['pending,correction_required', 'To decide'], ['approved', 'Approved'], ['rejected', 'Rejected'], ['ended', 'Ended'], ['', 'All']]} />
      </div>
      {error ? <PageState kind="error" title="Couldn't load registrations" message={error} /> : !data ? <PageState kind="loading" title="Loading…" />
        : data.items.length === 0 ? <p className="empty-line">None here.</p> : (
          <div className="table-wrap"><table className="table stack">
            <thead><tr><th scope="col">Registration</th><th scope="col">Battery</th><th scope="col">Customer</th><th scope="col">Warranty</th><th scope="col">Status</th></tr></thead>
            <tbody>{data.items.map((r) => (
              <tr key={r.id}><td className="cell-main"><Link to={`/warranty/${r.id}`}>{r.number}</Link><span className="small muted">{ROUTE[r.route]}</span></td>
                <td className="cell-sub"><code>{r.serial.serial}</code> {r.item.code}</td><td className="cell-sub">{r.customer_name || '—'}</td>
                <td className="cell-sub nowrap">{r.start_date ? `${day(r.start_date)} – ${day(r.expiry_date)}` : '—'}</td>
                <td className="cell-tag"><Status s={r.expired ? 'expired' : r.status} /></td></tr>
            ))}</tbody>
          </table></div>
        )}
      {data && <Pager offset={offset} limit={50} total={data.total} onChange={setOffset} />}
      {adding && <Dialog wide title="Register a battery for warranty" onClose={() => setAdding(false)}><RegForm onDone={(r) => nav(`/warranty/${r.id}`)} /></Dialog>}
    </>
  )
}

function usePartners() {
  const all = useApi<Page<Party & { kinds: string[] }>>('/parties?limit=200').data?.items ?? []
  return { customers: all.filter((p) => p.kinds.includes('customer')), sellers: all.filter((p) => p.kinds.some((k) => ['distributor', 'dealer'].includes(k))) }
}

function RegForm({ reg, onDone }: { reg?: Reg; onDone: (r: Reg) => void }) {
  const { customers, sellers } = usePartners()
  const action = useAction(() => {})
  return (
    <form noValidate onSubmit={(e) => {
      e.preventDefault()
      const fd = new FormData(e.currentTarget)
      const s = (k: string) => String(fd.get(k) ?? '')
      const d = (k: string) => s(k) || null
      const body = { customer_id: d('customer_id'), customer_name: s('customer_name'), customer_contact: s('customer_contact'), address: s('address'),
        distributor_id: d('distributor_id'), distributor_name: s('distributor_name'), sale_date: d('sale_date'), installation_date: d('installation_date'),
        commissioning_date: d('commissioning_date'), handover_date: d('handover_date'), notes: s('notes') }
      void action.run('save', async () => onDone(reg ? await api<Reg>(`/warranty-registrations/${reg.id}`, { method: 'PUT', body: { ...body, version: reg.version } })
        : await api<Reg>('/warranty-registrations', { method: 'POST', body: { ...body, serial: s('serial'), route: s('route') } })))
    }}>
      {!reg && <div className="grid-fields">
        <Field label="Battery serial" name="serial" required maxLength={60} autoComplete="off" />
        <SelectField label="Sold" name="route" options={[['distributor', 'Through a distributor or dealer'], ['direct', 'Directly by SMET']]} />
      </div>}
      <div className="grid-fields">
        <SelectField label="Customer on record" name="customer_id" defaultValue={reg?.customer?.id ?? ''} options={[['', 'Not a customer on record'], ...customers.map((c): [string, string] => [c.id, c.name])]} />
        <Field label="End customer's name" name="customer_name" maxLength={160} defaultValue={reg?.customer_name ?? ''} />
        <Field label="Phone or email" name="customer_contact" maxLength={160} defaultValue={reg?.customer_contact ?? ''} />
      </div>
      <TextareaField label="Installation address" name="address" maxLength={2000} rows={2} defaultValue={reg?.address ?? ''} />
      <div className="grid-fields">
        <SelectField label="Distributor or dealer" name="distributor_id" defaultValue={reg?.distributor?.id ?? ''} options={[['', 'None on record'], ...sellers.map((c): [string, string] => [c.id, c.name])]} />
        <Field label="Or their name" name="distributor_name" maxLength={160} defaultValue={reg?.distributor_name ?? ''} />
      </div>
      <div className="grid-fields">
        <Field label="Sale date" name="sale_date" type="date" max={today()} defaultValue={reg?.sale_date ?? ''} />
        <Field label="Installation date" name="installation_date" type="date" max={today()} defaultValue={reg?.installation_date ?? ''} />
        <Field label="Commissioning date" name="commissioning_date" type="date" max={today()} defaultValue={reg?.commissioning_date ?? ''} />
        <Field label="Handover date" name="handover_date" type="date" max={today()} defaultValue={reg?.handover_date ?? ''} />
      </div>
      <TextareaField label="Notes" name="notes" maxLength={2000} rows={2} defaultValue={reg?.notes ?? ''} />
      <p className="hint">Which date starts the warranty depends on the model's policy. Attach the proof of purchase next.</p>
      <FormError message={action.error} />
      <button className="btn btn-primary" disabled={!!action.busy}>{reg ? 'Save and check again' : 'Register'}</button>
    </form>
  )
}

export function RegistrationPage() {
  const { id } = useParams()
  const can = useCan()
  const me = useMe()
  const when = useWhen()
  const { data: r, error, reload } = useApi<Reg>(`/warranty-registrations/${id}`)
  const [dialog, setDialog] = useState<'edit' | 'decide' | 'correct' | null>(null)
  const action = useAction(() => { setDialog(null); reload() })
  if (error) return <PageState kind="error" title="Couldn't load the registration" message={error} />
  if (!r) return <PageState kind="loading" title="Loading…" />
  const deciding = ['pending', 'correction_required'].includes(r.status)
  const mine = r.submitted_by_id === me.user.id
  const post = (path: string, body: object) => api(`/warranty-registrations/${r.id}/${path}`, { method: 'POST', body: { version: r.version, ...body } })
  return (
    <>
      <Back to="/warranty">Warranty registrations</Back>
      <PageHead title={r.number} intro={`${r.serial.serial} · ${r.item.code} · ${ROUTE[r.route]}`}>
        {can('warranty.create') && deciding && <button className="btn btn-secondary" onClick={() => setDialog('edit')}>Complete details</button>}
        {can('warranty.approve') && deciding && !mine && <button className="btn btn-primary" onClick={() => setDialog('decide')}>Decide</button>}
        {can('warranty.correct') && r.status === 'approved' && <button className="btn btn-secondary" onClick={() => setDialog('correct')}>Correct</button>}
      </PageHead>
      {deciding && mine && can('warranty.approve') && <p className="hint">You submitted this registration, so someone else decides it.</p>}
      {r.status === 'correction_required' && <p className="notice notice-warning">Correction asked for: {r.decision_reason}</p>}
      {r.status === 'rejected' && <p className="notice notice-warning">Rejected by {r.decided_by}: {r.decision_reason}</p>}
      {r.status === 'ended' && <p className="notice">Ended {day(r.ended_on)}: {r.end_reason}</p>}
      <Facts rows={[['Status', <Status s={r.expired ? 'expired' : r.status} />], ['Warranty', r.start_date ? `${day(r.start_date)} – ${day(r.expiry_date)}` : r.would_start ? `Would run ${day(r.would_start)} – ${day(r.would_expire)}` : '—'],
        ['Customer', [r.customer?.name ?? r.customer_name, r.customer_contact].filter(Boolean).join(', ') || '—'], ['Sold by', r.distributor?.name ?? (r.distributor_name || '—')],
        ['Battery', <Link to={`/stock/serials/${r.serial.id}`}>{r.serial.serial}</Link>], ['Replaces', r.replaces ? <Link to={`/warranty/${r.replaces.id}`}>{r.replaces.number}</Link> : '—']]} />
      <FormError message={action.error} />
      {r.missing.length > 0 && <section className="panel panel-wide"><h2>Still needed before approval</h2><ul className="missing">{r.missing.map((m) => <li key={m}>{m}</li>)}</ul></section>}
      <div className="grid-2 section">
        <section className="panel"><h2>Dates</h2><ul className="feed">
          {([['Sale', r.sale_date], ['Installation', r.installation_date], ['Commissioning', r.commissioning_date], ['Handover', r.handover_date]] as const).map(([k, v]) => (
            <li key={k}><span>{k}{r.policy?.start_event === k.toLowerCase() && <span className="small muted">starts the warranty</span>}</span><span>{day(v)}</span></li>))}
        </ul>{r.address && <p className="prewrap small">{r.address}</p>}</section>
        <section className="panel"><h2>Policy</h2>{r.policy ? <>
          <p>{r.policy.months} months from {r.policy.start_event}, from {day(r.policy.effective_from)}</p>
          <p className="small muted">{r.policy.auto_approve ? 'Approved automatically when complete' : 'Decided by a person'} · {r.policy.requires_proof ? 'proof of purchase needed' : 'no proof needed'} · replacement: {RULE.find((x) => x[0] === r.policy!.replacement_rule)?.[1]}</p>
        </> : <p className="muted">No policy applies yet.</p>}</section>
      </div>
      <Files files={r.files} path={`/warranty-registrations/${r.id}/files`} kinds={[['proof_of_purchase', 'Proof of purchase'], ['installation_photo', 'Installation photograph'], ['other', 'Other']]}
        canAdd={can('warranty.create') && !['rejected', 'ended'].includes(r.status)} onDone={reload} />
      <h2 className="section">History</h2>
      <ol className="timeline panel panel-wide">{r.history.map((h, i) => (
        <li key={i}><span><strong>{h.summary}</strong><span className="small muted">{[h.who, h.reason, ...Object.entries(h.changes ?? {}).map(([k, [a, b]]) => `${words(k)}: ${a} → ${b}`)].filter(Boolean).join(' · ')}</span></span><span className="stamp">{when(h.at)}</span></li>
      ))}</ol>

      {dialog === 'edit' && <Dialog wide title={`Complete ${r.number}`} onClose={() => setDialog(null)}><RegForm reg={r} onDone={() => { setDialog(null); reload() }} /></Dialog>}
      {dialog === 'decide' && <Dialog title={`Decide ${r.number}`} onClose={() => setDialog(null)}>
        <form noValidate onSubmit={(e) => { e.preventDefault(); const fd = new FormData(e.currentTarget); const how = String(fd.get('how'))
          void action.run('decide', () => post(how, { reason: fd.get('reason') })) }}>
          <SelectField label="Decision" name="how" options={[['approve', 'Approve'], ['request-correction', 'Ask for a correction'], ['reject', 'Reject']]} />
          <TextareaField label="Reason" name="reason" maxLength={500} rows={2} hint="Needed to reject or ask for a correction" />
          <FormError message={action.error} /><button className="btn btn-primary" disabled={!!action.busy}>Record decision</button>
        </form></Dialog>}
      {dialog === 'correct' && <Dialog title={`Correct ${r.number}`} onClose={() => setDialog(null)}>
        <form noValidate onSubmit={(e) => { e.preventDefault(); const fd = new FormData(e.currentTarget)
          const body: Record<string, unknown> = { reason: fd.get('reason') }
          for (const k of ['customer_name', 'customer_contact', 'address', 'sale_date', 'installation_date', 'commissioning_date', 'handover_date']) {
            const v = String(fd.get(k) ?? ''); const was = String((r as unknown as Record<string, string | null>)[k] ?? '')
            if (v !== was && v !== '') body[k] = v
          }
          void action.run('correct', () => post('correct', body)) }}>
          <p className="hint">The warranty dates are worked out again from the policy. Every change is kept with your reason.</p>
          <div className="grid-fields"><Field label="Customer's name" name="customer_name" maxLength={160} defaultValue={r.customer_name} /><Field label="Phone or email" name="customer_contact" maxLength={160} defaultValue={r.customer_contact} /></div>
          <div className="grid-fields">
            <Field label="Sale" name="sale_date" type="date" max={today()} defaultValue={r.sale_date ?? ''} /><Field label="Installation" name="installation_date" type="date" max={today()} defaultValue={r.installation_date ?? ''} />
            <Field label="Commissioning" name="commissioning_date" type="date" max={today()} defaultValue={r.commissioning_date ?? ''} /><Field label="Handover" name="handover_date" type="date" max={today()} defaultValue={r.handover_date ?? ''} />
          </div>
          <TextareaField label="Address" name="address" maxLength={2000} rows={2} defaultValue={r.address} />
          <TextareaField label="Reason for the correction" name="reason" required minLength={3} maxLength={500} rows={2} />
          <FormError message={action.error} /><button className="btn btn-primary" disabled={!!action.busy}>Correct</button>
        </form></Dialog>}
    </>
  )
}

export function PoliciesPage() {
  const can = useCan()
  const edit = can('warranty.configure')
  const { data, error, reload } = useApi<{ items: Policy[] }>('/warranty-policies')
  const [dialog, setDialog] = useState<Policy | 'add' | null>(null)
  if (error) return <PageState kind="error" title="Couldn't load policies" message={error} />
  if (!data) return <PageState kind="loading" title="Loading…" />
  return (
    <>
      <PageHead title="Warranty policies" intro="SMET's warranty terms for each battery model. None is built in: a model with no policy can't have a warranty approved. Terms already in use don't change; add a new policy from the date new terms apply.">
        {edit && <button className="btn btn-primary" onClick={() => setDialog('add')}>Add policy</button>}
      </PageHead>
      {data.items.length === 0 ? <p className="empty-line">No policies yet (open question 17).</p> : (
        <div className="table-wrap"><table className="table stack">
          <thead><tr><th scope="col">Model</th><th scope="col">Term</th><th scope="col">Approval</th><th scope="col">Replacement</th><th scope="col">From</th><th scope="col"><span className="sr-only">Actions</span></th></tr></thead>
          <tbody>{data.items.map((x) => (
            <tr key={x.id} className={x.active ? '' : 'muted'}><td className="cell-main"><code>{x.item.code}</code><span className="small muted">{x.item.name}</span></td>
              <td className="cell-sub">{x.months} months from {x.start_event}</td><td className="cell-sub">{x.auto_approve ? 'Automatic when complete' : 'A person decides'}{x.requires_proof ? ', proof needed' : ''}</td>
              <td className="cell-sub">{RULE.find((r) => r[0] === x.replacement_rule)?.[1]}</td><td className="cell-sub nowrap">{day(x.effective_from)}{!x.active && ' (off)'}</td>
              <td className="actions cell-action">{edit && <button className="link-btn" onClick={() => setDialog(x)}>Edit</button>}<span className="small muted"> {x.used} in use</span></td></tr>
          ))}</tbody>
        </table></div>
      )}
      {dialog && <Dialog title={dialog === 'add' ? 'Add a warranty policy' : 'Edit policy'} onClose={() => setDialog(null)}>
        <PolicyForm policy={dialog === 'add' ? undefined : dialog} onDone={() => { setDialog(null); reload() }} /></Dialog>}
    </>
  )
}

function PolicyForm({ policy, onDone }: { policy?: Policy; onDone: () => void }) {
  const items = useItems().filter((i) => ['battery_model', 'battery_unit'].includes(i.kind))
  const action = useAction(onDone)
  return (
    <form noValidate onSubmit={(e) => {
      e.preventDefault()
      const fd = new FormData(e.currentTarget)
      const body = { item_id: fd.get('item_id'), months: num(String(fd.get('months'))), start_event: fd.get('start_event'), auto_approve: fd.get('auto') === 'on',
        requires_proof: fd.get('proof') === 'on', replacement_rule: fd.get('rule'), effective_from: fd.get('effective_from'), notes: fd.get('notes'), active: policy ? fd.get('active') === 'on' : true }
      void action.run('save', () => api(policy ? `/warranty-policies/${policy.id}` : '/warranty-policies', { method: policy ? 'PUT' : 'POST', body: policy ? { ...body, version: policy.version } : body }))
    }}>
      {policy?.used ? <p className="hint">{policy.used} warranties use this policy: only notes and switching it off can change.</p> : null}
      <label className="field"><span>Battery model</span><select name="item_id" required defaultValue={policy?.item.id}><ItemOptions items={items} /></select></label>
      <div className="grid-fields">
        <Field label="Months" name="months" type="number" min={1} max={600} required defaultValue={policy?.months ?? ''} />
        <SelectField label="Starts at" name="start_event" defaultValue={policy?.start_event ?? 'commissioning'} options={EVENT} />
        <Field label="In force from" name="effective_from" type="date" required defaultValue={policy?.effective_from ?? today()} />
      </div>
      <SelectField label="A replacement battery gets" name="rule" defaultValue={policy?.replacement_rule ?? 'original_remaining'} options={RULE} />
      <label className="check"><input type="checkbox" name="proof" defaultChecked={policy?.requires_proof ?? true} /> <span>Proof of purchase needed (distributor and public registrations)</span></label>
      <label className="check"><input type="checkbox" name="auto" defaultChecked={policy?.auto_approve ?? false} /> <span>Approve automatically when nothing is missing<span className="small muted">Otherwise someone other than the submitter decides</span></span></label>
      {policy && <label className="check"><input type="checkbox" name="active" defaultChecked={policy.active} /> <span>In use</span></label>}
      <TextareaField label="Notes" name="notes" maxLength={2000} rows={2} defaultValue={policy?.notes ?? ''} />
      <FormError message={action.error} />
      <button className="btn btn-primary" disabled={!!action.busy}>Save</button>
    </form>
  )
}

