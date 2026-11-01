import { Camera, Plus, Trash2 } from 'lucide-react'
import { useEffect, useRef, useState } from 'react'
import { Link, useNavigate, useParams } from 'react-router'
import { api, upload } from './api'
import { useAction, useApi, useCan, useMe, useWhen } from './lib'
import { Back, Facts, Status, day, num, serialList, today, words, type Page, type Ref } from './ops'
import { Dialog, Field, FormError, PageHead, PageState, Pager, SelectField, TextareaField } from './ui'

type Person = { id: string; name: string | null }
type SiteRef = { id: string; name: string; address: string; project: { id: string; number: string; name: string }; customer: { id: string; name: string } }
type Pos = { id?: string; position: string; serial: string; serial_id: string; item: Ref; status?: string; since?: string }
type BankBrief = { id: string; number: string; name: string; configuration: string; status: string; site_id: string; installed_on: string | null
  commissioned_on: string | null; handed_over_on: string | null; batteries: number; version: number; site?: { id: string; name: string; project_id: string } }
type VisitBrief = { id: string; number: string; stage: string; status: string; scheduled_on: string; time_window: string; done_on: string | null
  technicians: Person[]; bank_ids: string[]; open_issues: number; version: number; site?: { id: string; name: string }; project?: { id: string; number: string; name: string } }
type Hist = { position: string; serial: string; serial_id: string; started_at: string; start_reason: string; replaces: string | null; by: string | null
  ended_at: string | null; end_reason: string; ended_by: string | null; note: string }
type Bank = BankBrief & { site: SiteRef; positions: Pos[]; history: Hist[]; visits: VisitBrief[] }
type Check = { id: string; stage: string; kind: 'check' | 'reading'; text: string; unit: string; min_value: number | null; max_value: number | null
  confirmed: boolean; required: boolean; sort: number; active: boolean; version: number }
type Issue = { text: string; action: string; status: 'open' | 'closed'; closed_on?: string; closed_by?: string }
type FileRow = { id: string; kind: string; name: string; content_type: string; size: number; caption: string; uploaded_by: string | null; uploaded_at: string }
type Visit = VisitBrief & {
  site: SiteRef; banks: (BankBrief & { positions: Pos[] })[]; site_ready: boolean | null; access_notes: string
  contacts: { name: string; role: string; phone: string }[]; received_serials: string[]
  checklist: { check_id: string; text: string; required: boolean; done: boolean; note: string }[]
  readings: { check_id: string; text: string; unit: string; value: string | null; min: string | null; max: string | null; confirmed: boolean; within: boolean | null }[]
  observations: string; issues: Issue[]; acknowledgement: { name?: string; role?: string; on?: string | null; note?: string }
  completed_by: string | null; completed_at: string | null; cancel_reason: string; created_by: string | null
  checks: Check[]; photos_needed: number; files: FileRow[]; can_record: boolean; missing: string[]
}

const STAGES: [string, string][] = [['installation', 'Installation'], ['commissioning', 'Commissioning'], ['handover', 'Handover'], ['other', 'Other work']]
// Maintenance (AMC) and service visits are planned from their contract or request, not scheduled here.
const ALL_STAGES: [string, string][] = [...STAGES, ['maintenance', 'Maintenance'], ['service', 'Service']]
const STAGE = Object.fromEntries(ALL_STAGES)
const MOVES_STAGE = ['installation', 'commissioning', 'handover']
const people = (ps: Person[]) => ps.map((t) => t.name).join(', ') || 'Not assigned'
const n = (x: number | string | null) => (x == null ? '…' : String(Number(x)))
const limits = (min: number | string | null, max: number | string | null, unit: string) =>
  min == null && max == null ? 'No limits' : `${n(min)} to ${n(max)} ${unit}`.trim()

// ---------- visits ----------

export function VisitsPage() {
  const can = useCan()
  const [mine, setMine] = useState(can('installation.record') && !can('installation.create'))
  const [status, setStatus] = useState('scheduled,started')
  const [stage, setStage] = useState('')
  const [offset, setOffset] = useState(0)
  const qs = new URLSearchParams({ offset: String(offset), limit: '50', ...(status ? { status } : {}), ...(stage ? { stage } : {}), ...(mine ? { mine: 'true' } : {}) })
  const { data, error } = useApi<Page<VisitBrief>>(`/visits?${qs}`)
  return (
    <>
      <PageHead title="Site visits" intro="Installation, commissioning and handover visits by date. Schedule them from a project's sites; the technicians on a visit record its work." />
      <div className="toolbar">
        <SelectField label="Status" value={status} onChange={(e) => { setStatus(e.target.value); setOffset(0) }} options={[['scheduled,started', 'To do'], ['completed', 'Completed'], ['cancelled', 'Cancelled'], ['', 'All']]} />
        <SelectField label="Stage" value={stage} onChange={(e) => { setStage(e.target.value); setOffset(0) }} options={[['', 'Any'], ...ALL_STAGES]} />
        <label className="check toolbar-check"><input type="checkbox" checked={mine} onChange={(e) => { setMine(e.target.checked); setOffset(0) }} /> <span>Only my visits</span></label>
      </div>
      {error ? <PageState kind="error" title="Couldn't load visits" message={error} />
        : !data ? <PageState kind="loading" title="Loading…" />
        : data.items.length === 0 ? <p className="empty-line">No visits here.</p>
        : (
          <div className="table-wrap">
            <table className="table stack">
              <thead><tr><th scope="col">Visit</th><th scope="col">Site</th><th scope="col">Date</th><th scope="col">Technicians</th><th scope="col">Status</th></tr></thead>
              <tbody>
                {data.items.map((v) => (
                  <tr key={v.id}>
                    <td className="cell-main"><Link to={`/installation/visits/${v.id}`}>{v.number}</Link><span className="small muted">{STAGE[v.stage]}</span></td>
                    <td className="cell-sub">{v.site?.name} · {v.project?.number}</td>
                    <td className="cell-sub nowrap">{day(v.scheduled_on)}{v.time_window && `, ${v.time_window}`}</td>
                    <td className="cell-sub">{people(v.technicians)}</td>
                    <td className="cell-tag"><Status s={v.status} />{v.open_issues > 0 && <span className="small muted"> {v.open_issues} open issue{v.open_issues > 1 ? 's' : ''}</span>}</td>
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

export function VisitPage() {
  const { id } = useParams()
  const can = useCan()
  const when = useWhen()
  const { data: v, error, reload } = useApi<Visit>(`/visits/${id}`)
  const [dialog, setDialog] = useState<'plan' | 'cancel' | { place: BankBrief } | { bank: BankBrief; change: Change } | null>(null)
  const action = useAction(() => { setDialog(null); reload() })
  if (error) return <PageState kind="error" title="Couldn't load the visit" message={error} />
  if (!v) return <PageState kind="loading" title="Loading…" />
  const open = ['scheduled', 'started'].includes(v.status)
  const coord = can('installation.create')
  return (
    <>
      <Back to="/installation/visits">Site visits</Back>
      <PageHead title={`${v.number} · ${STAGE[v.stage]}`} intro={`${v.site.name}, ${v.site.project.number} ${v.site.project.name} · ${v.site.customer.name}`}>
        {coord && open && <button className="btn btn-secondary" onClick={() => setDialog('plan')}>Reschedule or reassign</button>}
      </PageHead>
      {v.status === 'cancelled' && <p className="notice notice-warning">Cancelled: {v.cancel_reason}</p>}
      {v.status === 'completed' && <p className="notice notice-success">Completed on {day(v.done_on)} by {v.completed_by} ({when(v.completed_at)}).</p>}
      <Facts rows={[['Status', <Status s={v.status} />], ['Date', `${day(v.scheduled_on)}${v.time_window ? `, ${v.time_window}` : ''}`],
        ['Technicians', people(v.technicians)], ['Site', <span className="prewrap">{v.site.address || v.site.name}</span>],
        ['Site ready', v.site_ready == null ? 'Not checked' : v.site_ready ? 'Yes' : 'No'],
        ['Contacts', v.contacts.length ? v.contacts.map((c) => [c.name, c.role, c.phone].filter(Boolean).join(', ')).join('; ') : '—']]} />
      {v.access_notes && <p className="prewrap muted">Access: {v.access_notes}</p>}
      <FormError message={action.error} />

      {v.banks.length > 0 && <h2 className="section">Battery banks</h2>}
      {v.banks.map((b) => (
        <section key={b.id} className="panel panel-wide">
          <div className="panel-head">
            <h2><Link to={`/installation/banks/${b.id}`}>{b.number}</Link> {b.name} <Status s={b.status} /></h2>
            {v.can_record && b.status === 'planned' && <button className="btn btn-secondary" onClick={() => setDialog({ place: b })}>Place batteries</button>}
          </div>
          {b.configuration && <p className="muted">{b.configuration}</p>}
          {b.positions.length === 0 ? <p className="empty-line">No batteries placed yet.</p> : (
            <div className="table-wrap"><table className="table">
              <thead><tr><th scope="col">Position</th><th scope="col">Serial</th><th scope="col">Item</th>{v.stage === 'installation' && <th scope="col">Receipt confirmed</th>}{v.can_record && <th scope="col"><span className="sr-only">Actions</span></th>}</tr></thead>
              <tbody>{b.positions.map((x) => (
                <tr key={x.serial}><td>{x.position}</td><td><Link to={`/stock/serials/${x.serial_id}`}><code>{x.serial}</code></Link></td><td className="small">{x.item.code}</td>
                  {v.stage === 'installation' && <td>{v.received_serials.includes(x.serial) ? '✓' : <span className="muted">Not yet</span>}</td>}
                  {v.can_record && <td className="actions"><span className="row">{(['replace', 'relocate', 'remove'] as const).map((k) => (
                    <button key={k} className="link-btn" onClick={() => setDialog({ bank: b, change: { kind: k, pos: x } })}>{k === 'relocate' ? 'Move' : words(k)}</button>))}</span></td>}</tr>
              ))}</tbody>
            </table></div>
          )}
        </section>
      ))}

      {v.can_record ? <RecordForm v={v} onSaved={reload} /> : <VisitRecord v={v} />}
      <Evidence v={v} onDone={reload} />
      {v.issues.some((i) => i.status === 'open') && (v.can_record || (!open && (can('installation.create') || can('installation.record')))) && <CloseIssues v={v} onDone={reload} />}
      {coord && open && <p><button className="btn btn-ghost" onClick={() => setDialog('cancel')}>Cancel visit</button></p>}

      {dialog === 'cancel' && (
        <Dialog title={`Cancel ${v.number}`} onClose={() => setDialog(null)}>
          <form noValidate onSubmit={(e) => { e.preventDefault(); void action.run('cancel', () => api(`/visits/${v.id}/cancel`, { method: 'POST', body: { version: v.version, reason: new FormData(e.currentTarget).get('reason') } })) }}>
            <p className="hint">What was recorded on the visit stays. Schedule a new visit to finish the work.</p>
            <TextareaField label="Reason" name="reason" required minLength={3} maxLength={500} />
            <FormError message={action.error} />
            <button className="btn btn-primary" disabled={!!action.busy}>Cancel visit</button>
          </form>
        </Dialog>
      )}
      {dialog === 'plan' && <Dialog wide title={`Plan ${v.number}`} onClose={() => setDialog(null)}><VisitForm site={v.site} visit={v} onDone={() => { setDialog(null); reload() }} /></Dialog>}
      {dialog && typeof dialog === 'object' && 'change' in dialog && <Dialog title={`${dialog.change.kind === 'relocate' ? 'Move' : words(dialog.change.kind)} ${dialog.change.pos.serial}`} onClose={() => setDialog(null)}>
        <ChangeForm bank={dialog.bank} change={dialog.change} banks={v.banks} visitId={v.id} onDone={() => { setDialog(null); reload() }} /></Dialog>}
      {dialog && typeof dialog === 'object' && 'place' in dialog && <Dialog wide title={`Place batteries in ${dialog.place.number}`} onClose={() => setDialog(null)}>
        <PlaceForm bank={dialog.place} siteId={v.site.id} visitId={v.id} taken={v.banks.find((b) => b.id === dialog.place.id)?.positions.length ?? 0} onDone={() => { setDialog(null); reload() }} /></Dialog>}
    </>
  )
}

/** What a technician records: receipt of serials, checklist, readings, issues and, at handover, who accepted it. */
function RecordForm({ v, onSaved }: { v: Visit; onSaved: () => void }) {
  const done = Object.fromEntries(v.checklist.map((c) => [c.check_id, c]))
  const taken = Object.fromEntries(v.readings.map((r) => [r.check_id, r.value ?? '']))
  const [received, setReceived] = useState(v.received_serials.join('\n'))
  const [checks, setChecks] = useState<Record<string, { done: boolean; note: string }>>(
    Object.fromEntries(v.checks.filter((c) => c.kind === 'check').map((c) => [c.id, { done: done[c.id]?.done ?? false, note: done[c.id]?.note ?? '' }])))
  const [readings, setReadings] = useState<Record<string, string>>(Object.fromEntries(v.checks.filter((c) => c.kind === 'reading').map((c) => [c.id, taken[c.id] ?? ''])))
  const [edits, setEdits] = useState<Record<number, { text: string; action: string }>>({})
  const [added, setAdded] = useState<{ text: string; action: string }[]>([])
  const [ready, setReady] = useState(v.site_ready == null ? '' : v.site_ready ? 'yes' : 'no')
  const [doneOn, setDoneOn] = useState(today())
  const action = useAction(onSaved)
  const placed = v.banks.flatMap((b) => b.positions.map((p) => p.serial))
  const form = useRef<HTMLFormElement>(null)
  const lastSaved = useRef(0) // our own save's version, in case the page hasn't reloaded yet
  const body = () => {
    const fd = new FormData(form.current!)
    return {
      version: Math.max(v.version, lastSaved.current), received_serials: serialList(received), observations: fd.get('observations'), access_notes: fd.get('access_notes'),
      site_ready: ready === '' ? null : ready === 'yes',
      checklist: Object.entries(checks).map(([check_id, c]) => ({ check_id, ...c })),
      readings: Object.entries(readings).filter(([, x]) => x !== '').map(([check_id, x]) => ({ check_id, value: num(x) })),
      issues: [...v.issues.map((x, i) => edits[i] ?? { text: x.text, action: x.action }), ...added.filter((i) => i.text.trim())],
      acknowledgement: { name: fd.get('ack_name') ?? '', role: fd.get('ack_role') ?? '', on: fd.get('ack_on') || null, note: fd.get('ack_note') ?? '' },
    }
  }
  const save = async () => {
    const saved = await api<Visit>(`/visits/${v.id}/record`, { method: 'PUT', body: body() })
    lastSaved.current = saved.version
    setEdits({})
    setAdded([])
    return saved
  }
  /** The save stands even if completion is refused, so the page reloads either way (fresh version and list). */
  const saveAndComplete = async () => {
    const saved = await save()
    try {
      await api(`/visits/${v.id}/complete`, { method: 'POST', body: { version: saved.version, done_on: doneOn } })
    } finally {
      onSaved()
    }
  }
  const reading = (c: Check) => {
    const x = num(readings[c.id])
    if (x === undefined || !c.confirmed) return null
    return (c.min_value == null || x >= c.min_value) && (c.max_value == null || x <= c.max_value)
  }
  return (
    <form ref={form} noValidate className="section" onSubmit={(e) => { e.preventDefault(); void action.run('save', save) }}>
      <h2 className="section">Record the work</h2>
      <div className="grid-2">
        <section className="panel">
          <h2>Batteries received at the site</h2>
          <textarea aria-label="Serial numbers received" rows={4} placeholder="Scan or type, one per battery" value={received} onChange={(e) => setReceived(e.target.value)} />
          {placed.length > 0 && <p className="small"><button type="button" className="link-btn" onClick={() => setReceived([...new Set([...serialList(received), ...placed])].join('\n'))}>Add the {placed.length} batteries placed in these banks</button></p>}
          <SelectField label="Site ready for the work" value={ready} onChange={(e) => setReady(e.target.value)} options={[['', 'Not checked'], ['yes', 'Yes'], ['no', 'No']]} />
          <TextareaField label="Access and site notes" name="access_notes" defaultValue={v.access_notes} maxLength={2000} rows={2} />
        </section>
        <section className="panel">
          <h2>{STAGE[v.stage]} checklist</h2>
          {v.checks.filter((c) => c.kind === 'check').length === 0 ? <p className="muted">SMET hasn't set a checklist for this stage yet.</p> : (
            <ul className="check-list">{v.checks.filter((c) => c.kind === 'check').map((c) => (
              <li key={c.id}>
                <label className="check"><input type="checkbox" checked={checks[c.id].done} onChange={(e) => setChecks({ ...checks, [c.id]: { ...checks[c.id], done: e.target.checked } })} />
                  <span>{c.text}{!c.required && <span className="small muted">Optional</span>}</span></label>
                <input aria-label={`Note on ${c.text}`} placeholder="Note" maxLength={300} value={checks[c.id].note} onChange={(e) => setChecks({ ...checks, [c.id]: { ...checks[c.id], note: e.target.value } })} />
              </li>
            ))}</ul>
          )}
        </section>
      </div>
      {v.checks.some((c) => c.kind === 'reading') && (
        <section className="panel panel-wide">
          <h2>Readings</h2>
          <div className="table-wrap"><table className="table">
            <thead><tr><th scope="col">Reading</th><th scope="col">Value</th><th scope="col">Limits</th></tr></thead>
            <tbody>{v.checks.filter((c) => c.kind === 'reading').map((c) => {
              const ok = reading(c)
              return (
                <tr key={c.id}>
                  <td>{c.text}{!c.required && <span className="small muted"> optional</span>}</td>
                  <td><span className="row"><input aria-label={c.text} type="number" step="any" inputMode="decimal" value={readings[c.id]} onChange={(e) => setReadings({ ...readings, [c.id]: e.target.value })} style={{ inlineSize: 120 }} /> {c.unit}</span></td>
                  <td className="small">{limits(c.min_value, c.max_value, c.unit)}{!c.confirmed && (c.min_value != null || c.max_value != null) ? ' · not yet confirmed by SMET' : ''}{ok === false && <strong className="danger-ink"> · outside</strong>}</td>
                </tr>
              )
            })}</tbody>
          </table></div>
        </section>
      )}
      <section className="panel panel-wide">
        <div className="panel-head"><h2>Issues and outstanding work</h2><button type="button" className="btn btn-ghost" onClick={() => setAdded([...added, { text: '', action: '' }])}><Plus size={16} aria-hidden /> Add issue</button></div>
        {v.issues.length + added.length === 0 ? <p className="empty-line">None found.</p> : <>
          {v.issues.map((x, n) => x.status === 'closed'
            ? <p key={n} className="line small"><span className="line-item">{x.text}</span><span className="line-item muted">Closed: {x.action}{x.closed_by && ` (${x.closed_by})`}</span></p>
            : (
              <div className="line" key={n}>
                <label className="line-item"><span className="line-label">Issue</span><input value={(edits[n] ?? x).text} maxLength={500} onChange={(e) => setEdits({ ...edits, [n]: { ...(edits[n] ?? x), text: e.target.value } })} /></label>
                <label className="line-item"><span className="line-label">Corrective action</span><input value={(edits[n] ?? x).action} maxLength={500} onChange={(e) => setEdits({ ...edits, [n]: { ...(edits[n] ?? x), action: e.target.value } })} /></label>
              </div>
            ))}
          {added.map((x, n) => (
            <div className="line" key={`new-${n}`}>
              <label className="line-item"><span className="line-label">New issue</span><input value={x.text} maxLength={500} onChange={(e) => setAdded(added.map((y, k) => (k === n ? { ...y, text: e.target.value } : y)))} /></label>
              <label className="line-item"><span className="line-label">Corrective action</span><input value={x.action} maxLength={500} onChange={(e) => setAdded(added.map((y, k) => (k === n ? { ...y, action: e.target.value } : y)))} /></label>
              <button type="button" className="btn btn-ghost" aria-label={`Remove new issue ${n + 1}`} onClick={() => setAdded(added.filter((_, k) => k !== n))}><Trash2 size={16} aria-hidden /></button>
            </div>
          ))}
          <p className="hint">Issues stay on record. Close one under Outstanding work when it is fixed.</p>
        </>}
        <TextareaField label="Observations" name="observations" defaultValue={v.observations} maxLength={2000} rows={3} />
      </section>
      {v.stage === 'handover' && (
        <section className="panel panel-wide">
          <h2>Customer's acknowledgement</h2>
          <div className="grid-fields">
            <Field label="Accepted by" name="ack_name" defaultValue={v.acknowledgement.name ?? ''} maxLength={160} />
            <Field label="Role" name="ack_role" defaultValue={v.acknowledgement.role ?? ''} maxLength={80} />
            <Field label="On" name="ack_on" type="date" defaultValue={v.acknowledgement.on ?? ''} />
          </div>
          <TextareaField label="Their remarks" name="ack_note" defaultValue={v.acknowledgement.note ?? ''} maxLength={500} rows={2} hint="Add the signed handover form under Evidence." />
        </section>
      )}
      <FormError message={action.error} />
      <div className="row-end">
        <button className="btn btn-secondary" disabled={!!action.busy}>Save</button>
      </div>
      {MOVES_STAGE.includes(v.stage) && (
        <section className="panel panel-wide section">
          <h2>Complete the {v.stage}</h2>
          {v.missing.length > 0
            ? <><p>Still needed, as last saved:</p><ul className="missing">{v.missing.map((m) => <li key={m}>{m}</li>)}</ul></>
            : <p className="muted">Everything SMET requires is recorded. Completing sets the {v.stage} date on {v.banks.map((b) => b.number).join(', ')}.</p>}
          <div className="row">
            <Field label="Done on" type="date" value={doneOn} max={today()} onChange={(e) => setDoneOn(e.target.value)} />
            <button type="button" className="btn btn-primary" disabled={!!action.busy} onClick={() => action.run('complete', () => saveAndComplete(), `Complete the ${v.stage} of ${v.banks.map((b) => b.number).join(', ')}?`)}>Save and complete</button>
          </div>
        </section>
      )}
      {!MOVES_STAGE.includes(v.stage) && <p><button type="button" className="btn btn-primary" disabled={!!action.busy} onClick={() => action.run('complete', () => saveAndComplete())}>Save and mark done</button></p>}
    </form>
  )
}

/** The record as it stands, for people who don't record it. */
function VisitRecord({ v }: { v: Visit }) {
  if (!v.received_serials.length && !v.checklist.length && !v.readings.length && !v.issues.length && !v.observations) return null
  return (
    <>
      <h2 className="section">Record</h2>
      <div className="grid-2">
        <section className="panel">
          <h2>Received</h2>
          <p className="small">{v.received_serials.join(', ') || '—'}</p>
          {v.checklist.length > 0 && <ul className="steps">{v.checklist.map((c) => <li key={c.check_id} className={c.done ? 'done' : ''}>{c.done ? '✓' : '○'} {c.text}{c.note && ` · ${c.note}`}</li>)}</ul>}
        </section>
        <section className="panel">
          <h2>Readings</h2>
          {v.readings.length === 0 ? <p className="muted">None.</p> : <ul className="feed">{v.readings.map((r) => (
            <li key={r.check_id}><span>{r.text}<span className="small muted">{limits(r.min, r.max, r.unit)}{r.confirmed ? '' : ' (not confirmed)'}</span></span><strong className={r.within === false ? 'danger-ink' : ''}>{r.value ?? '—'} {r.unit}</strong></li>
          ))}</ul>}
        </section>
      </div>
      {v.issues.length > 0 && <section className="panel panel-wide"><h2>Issues</h2><ul className="feed">{v.issues.map((i, n) => (
        <li key={n}><span>{i.text}<span className="small muted">{i.action || 'No action yet'}{i.closed_by && ` · closed by ${i.closed_by} on ${day(i.closed_on)}`}</span></span><Status s={i.status} /></li>
      ))}</ul></section>}
      {v.observations && <p className="prewrap">{v.observations}</p>}
      {v.acknowledgement.name && <p>Accepted by {v.acknowledgement.name}{v.acknowledgement.role && `, ${v.acknowledgement.role}`}, on {day(v.acknowledgement.on)}{v.acknowledgement.note && `: ${v.acknowledgement.note}`}</p>}
    </>
  )
}

function CloseIssues({ v, onDone }: { v: Visit; onDone: () => void }) {
  const [closing, setClosing] = useState<number | null>(null)
  const action = useAction(() => { setClosing(null); onDone() })
  return (
    <section className="panel panel-wide">
      <h2>Outstanding work</h2>
      <ul className="feed">{v.issues.map((i, n) => i.status === 'open' && (
        <li key={n}><span>{i.text}<span className="small muted">{i.action || 'No action yet'}</span></span><button className="link-btn" onClick={() => setClosing(n)}>Close</button></li>
      ))}</ul>
      {closing !== null && (
        <Dialog title="Close the issue" onClose={() => setClosing(null)}>
          <form noValidate onSubmit={(e) => { e.preventDefault(); void action.run('close', () => api(`/visits/${v.id}/issues/${closing}/close`, { method: 'POST', body: { version: v.version, action: new FormData(e.currentTarget).get('action') } })) }}>
            <p>{v.issues[closing].text}</p>
            <TextareaField label="What was done" name="action" required maxLength={500} />
            <FormError message={action.error} />
            <button className="btn btn-primary" disabled={!!action.busy}>Close issue</button>
          </form>
        </Dialog>
      )}
    </section>
  )
}

function Evidence({ v, onDone }: { v: Visit; onDone: () => void }) {
  const action = useAction(onDone)
  const can = v.can_record || (v.status === 'completed' && v.can_record)
  const photos = v.files.filter((f) => f.kind === 'photo').length
  if (!v.files.length && !v.can_record) return null
  return (
    <section className="panel panel-wide section">
      <div className="panel-head"><h2>Evidence</h2>{v.photos_needed > 0 && <span className="stamp">{photos} of {v.photos_needed} photograph{v.photos_needed > 1 ? 's' : ''} required</span>}</div>
      {v.files.length > 0 && (
        <ul className="evidence">{v.files.map((f) => (
          <li key={f.id}>
            {f.content_type.startsWith('image/')
              ? <a href={`/api/v1/files/${f.id}`} target="_blank" rel="noreferrer"><img src={`/api/v1/files/${f.id}`} alt={f.caption || f.name} loading="lazy" /></a>
              : <a className="evidence-doc" href={`/api/v1/files/${f.id}`}>{f.name}</a>}
            <span className="small">{words(f.kind)}{f.caption && `: ${f.caption}`}</span>
            <span className="small muted">{f.uploaded_by}</span>
          </li>
        ))}</ul>
      )}
      {can && (
        <form className="row" noValidate onSubmit={(e) => {
          e.preventDefault()
          const formEl = e.currentTarget
          void action.run('upload', async () => { await upload(`/visits/${v.id}/files`, new FormData(formEl)); formEl.reset() })
        }}>
          <label className="field"><span>File</span><input type="file" name="file" required accept="image/jpeg,image/png,image/webp,application/pdf" capture="environment" /></label>
          <SelectField label="Kind" name="kind" options={[['photo', 'Photograph'], ['test_report', 'Test report'], ['acknowledgement', 'Signed acknowledgement'], ['other', 'Other']]} />
          <Field label="Caption" name="caption" maxLength={300} />
          <button className="btn btn-secondary" disabled={!!action.busy}>Add</button>
        </form>
      )}
      <FormError message={action.error} />
    </section>
  )
}

function PlaceForm({ bank, siteId, visitId, taken, onDone }: { bank: BankBrief; siteId: string; visitId?: string; taken: number; onDone: () => void }) {
  const { data } = useApi<{ items: { id: string; serial: string; item: Ref }[] }>(`/sites/${siteId}/batteries`)
  const [rows, setRows] = useState<Record<string, string>>({})
  const action = useAction(onDone)
  if (!data) return <PageState kind="loading" title="Loading…" />
  if (!data.items.length) return <p className="empty-line">Every battery delivered to this site is already in a bank. Deliver more first.</p>
  const chosen = Object.entries(rows).filter(([, p]) => p.trim())
  return (
    <form noValidate onSubmit={(e) => {
      e.preventDefault()
      void action.run('place', () => api(`/banks/${bank.id}/batteries`, { method: 'POST', body: { version: bank.version, visit_id: visitId ?? null,
        batteries: chosen.map(([serial, position]) => ({ serial, position: position.trim() })) } }))
    }}>
      <p className="hint">Give each battery its position in {bank.name}. Only batteries delivered to this site and in no bank are listed.</p>
      <p><button type="button" className="link-btn" onClick={() => setRows(Object.fromEntries(data.items.map((b, i) => [b.serial, String(taken + i + 1)])))}>Number them in order from {taken + 1}</button></p>
      <div className="table-wrap"><table className="table">
        <thead><tr><th scope="col">Serial</th><th scope="col">Item</th><th scope="col">Position</th></tr></thead>
        <tbody>{data.items.map((b) => (
          <tr key={b.id}><td><code>{b.serial}</code></td><td className="small">{b.item.code}</td>
            <td><input aria-label={`Position of ${b.serial}`} maxLength={40} value={rows[b.serial] ?? ''} onChange={(e) => setRows({ ...rows, [b.serial]: e.target.value })} style={{ inlineSize: 110 }} /></td></tr>
        ))}</tbody>
      </table></div>
      <FormError message={action.error} />
      <button className="btn btn-primary" disabled={!!action.busy || !chosen.length}>Place {chosen.length || ''} batter{chosen.length === 1 ? 'y' : 'ies'}</button>
    </form>
  )
}

function VisitForm({ site, sites, visit, onDone }: { site?: { id: string }; sites?: { id: string; name: string }[]; visit?: Visit; onDone: (v: Visit) => void }) {
  const techs = useApi<{ items: Person[] }>('/installation/technicians').data?.items
  const [siteId, setSiteId] = useState(site?.id ?? sites?.[0]?.id ?? '')
  const banks = useApi<{ items: BankBrief[] }>(siteId ? `/sites/${siteId}/banks` : null).data?.items
  const [stage, setStage] = useState(visit?.stage ?? 'installation')
  const [chosen, setChosen] = useState<string[]>(visit?.bank_ids ?? [])
  const [team, setTeam] = useState<string[]>(visit?.technicians.map((t) => t.id) ?? [])
  const action = useAction(() => {})
  const toggle = (list: string[], set: (x: string[]) => void, id: string) => set(list.includes(id) ? list.filter((x) => x !== id) : [...list, id])
  const next: Record<string, string> = { installation: 'planned', commissioning: 'installed', handover: 'commissioned' }
  return (
    <form noValidate onSubmit={(e) => {
      e.preventDefault()
      const fd = new FormData(e.currentTarget)
      const c = { name: String(fd.get('c_name') ?? '').trim(), role: String(fd.get('c_role') ?? ''), phone: String(fd.get('c_phone') ?? '') }
      const plan = { bank_ids: chosen, technician_ids: team, scheduled_on: fd.get('scheduled_on'), time_window: fd.get('time_window'),
        access_notes: fd.get('access_notes'), contacts: c.name ? [c] : [] }
      void action.run('save', async () => onDone(visit
        ? await api<Visit>(`/visits/${visit.id}`, { method: 'PATCH', body: { version: visit.version, ...plan } })
        : await api<Visit>('/visits', { method: 'POST', body: { site_id: siteId, stage, ...plan } })))
    }}>
      <div className="grid-fields">
        {sites && !visit && <SelectField label="Site" value={siteId} onChange={(e) => { setSiteId(e.target.value); setChosen([]) }} options={sites.map((x) => [x.id, x.name])} />}
        {!visit && <SelectField label="Stage" value={stage} onChange={(e) => setStage(e.target.value)} options={STAGES} />}
        <Field label="Date" name="scheduled_on" type="date" required defaultValue={visit?.scheduled_on ?? today()} />
        <Field label="Time" name="time_window" maxLength={60} placeholder="e.g. 08:00–12:00" defaultValue={visit?.time_window ?? ''} />
      </div>
      {stage !== 'other' && (
        <fieldset className="plain"><legend>Battery banks</legend>
          {!banks ? <p className="muted">Loading…</p> : banks.length === 0 ? <p className="muted">This site has no battery banks yet. Add one first.</p>
            : banks.map((b) => <label key={b.id} className="check"><input type="checkbox" checked={chosen.includes(b.id)} onChange={() => toggle(chosen, setChosen, b.id)} />
              <span>{b.number} {b.name} <span className="small muted">{words(b.status)}, {b.batteries} batteries{next[stage] && b.status !== next[stage] ? ` · needs to be ${words(next[stage]).toLowerCase()} for this stage` : ''}</span></span></label>)}
        </fieldset>
      )}
      <fieldset className="plain"><legend>Technicians</legend>
        {!techs ? <p className="muted">Loading…</p> : techs.length === 0 ? <p className="muted">Nobody holds “Record work on visits” yet. Give someone the Installation Technician role.</p>
          : techs.map((t) => <label key={t.id} className="check"><input type="checkbox" checked={team.includes(t.id)} onChange={() => toggle(team, setTeam, t.id)} /> <span>{t.name}</span></label>)}
      </fieldset>
      <div className="grid-fields">
        <Field label="Site contact" name="c_name" maxLength={160} defaultValue={visit?.contacts[0]?.name ?? ''} />
        <Field label="Contact's role" name="c_role" maxLength={80} defaultValue={visit?.contacts[0]?.role ?? ''} />
        <Field label="Phone" name="c_phone" type="tel" maxLength={40} defaultValue={visit?.contacts[0]?.phone ?? ''} />
      </div>
      <TextareaField label="Access requirements" name="access_notes" maxLength={2000} rows={2} defaultValue={visit?.access_notes ?? ''} hint="Permits, gate passes, working hours, who holds the keys." />
      <FormError message={action.error} />
      <button className="btn btn-primary" disabled={!!action.busy}>{visit ? 'Save plan' : 'Schedule visit'}</button>
    </form>
  )
}

// ---------- the project's installation panel ----------

type ProjectInstallation = { banks: BankBrief[]; visits: VisitBrief[]; outstanding: { visit_id: string; visit: string; index: number; text: string; action: string }[] }

export function InstallationPanel({ projectId, sites }: { projectId: string; sites: { id: string; name: string }[] }) {
  const can = useCan()
  const nav = useNavigate()
  const { data, reload } = useApi<ProjectInstallation>(`/projects/${projectId}/installation`)
  const [dialog, setDialog] = useState<'bank' | 'visit' | null>(null)
  const action = useAction(() => { setDialog(null); reload() })
  const coord = can('installation.create')
  if (!data) return null
  const siteName = Object.fromEntries(sites.map((s) => [s.id, s.name]))
  return (
    <>
      <div className="panel-head section"><h2>Installation</h2>
        {coord && sites.length > 0 && <span className="row"><button className="btn btn-secondary" onClick={() => setDialog('bank')}>Add battery bank</button><button className="btn btn-secondary" onClick={() => setDialog('visit')}>Schedule visit</button></span>}
      </div>
      {data.banks.length === 0 ? <p className="empty-line">{sites.length ? 'No battery banks yet. Add one for each system at a site.' : 'Add a site first.'}</p> : (
        <div className="table-wrap"><table className="table stack">
          <thead><tr><th scope="col">Bank</th><th scope="col">Site</th><th scope="col" className="num">Batteries</th><th scope="col">Installed</th><th scope="col">Commissioned</th><th scope="col">Handed over</th><th scope="col">Stage</th></tr></thead>
          <tbody>{data.banks.map((b) => (
            <tr key={b.id}><td className="cell-main"><Link to={`/installation/banks/${b.id}`}>{b.number}</Link><span className="small muted">{b.name}</span></td>
              <td className="cell-sub">{siteName[b.site_id]}</td><td className="num cell-sub">{b.batteries}</td>
              <td className="cell-sub nowrap">{day(b.installed_on)}</td><td className="cell-sub nowrap">{day(b.commissioned_on)}</td><td className="cell-sub nowrap">{day(b.handed_over_on)}</td>
              <td className="cell-tag"><Status s={b.status} /></td></tr>
          ))}</tbody>
        </table></div>
      )}
      {data.visits.length > 0 && (
        <ul className="feed panel panel-wide">{data.visits.map((v) => (
          <li key={v.id}><span><Link to={`/installation/visits/${v.id}`}>{v.number} · {STAGE[v.stage]}</Link><span className="small muted">{day(v.scheduled_on)} · {people(v.technicians)}</span></span><Status s={v.status} /></li>
        ))}</ul>
      )}
      {data.outstanding.length > 0 && (
        <section className="panel panel-wide"><h2>Outstanding work</h2><ul className="feed">{data.outstanding.map((o) => (
          <li key={`${o.visit_id}-${o.index}`}><span>{o.text}<span className="small muted">{o.action || 'No action yet'}</span></span><Link to={`/installation/visits/${o.visit_id}`}>{o.visit}</Link></li>
        ))}</ul></section>
      )}
      {dialog === 'bank' && (
        <Dialog title="Add a battery bank" onClose={() => setDialog(null)}>
          <form noValidate onSubmit={(e) => {
            e.preventDefault()
            const fd = new FormData(e.currentTarget)
            void action.run('bank', () => api(`/sites/${fd.get('site_id')}/banks`, { method: 'POST', body: { name: fd.get('name'), configuration: fd.get('configuration') } }))
          }}>
            <SelectField label="Site" name="site_id" options={sites.map((s) => [s.id, s.name])} />
            <Field label="Name" name="name" required maxLength={120} placeholder="e.g. Bank 1, UPS room" />
            <Field label="Configuration" name="configuration" maxLength={300} placeholder="e.g. 24 × 2 V in series, 48 V" />
            <FormError message={action.error} />
            <button className="btn btn-primary" disabled={!!action.busy}>Add bank</button>
          </form>
        </Dialog>
      )}
      {dialog === 'visit' && <Dialog wide title="Schedule a visit" onClose={() => setDialog(null)}><VisitForm sites={sites} onDone={(v) => nav(`/installation/visits/${v.id}`)} /></Dialog>}
    </>
  )
}

// ---------- banks ----------

export function BanksPage() {
  const [status, setStatus] = useState('')
  const [q, setQ] = useState('')
  const [offset, setOffset] = useState(0)
  const { data, error } = useApi<Page<BankBrief>>(`/banks?${new URLSearchParams({ q, offset: String(offset), limit: '50', ...(status ? { status } : {}) })}`)
  return (
    <>
      <PageHead title="Battery banks" intro="Every battery system at a customer's site and the stage it has reached. Banks are added from a project's sites." />
      <div className="toolbar" role="search">
        <Field label="Bank or site" type="search" value={q} onChange={(e) => { setQ(e.target.value); setOffset(0) }} />
        <SelectField label="Stage" value={status} onChange={(e) => { setStatus(e.target.value); setOffset(0) }} options={[['', 'Any'], ['planned', 'Planned'], ['installed', 'Installed, to commission'], ['commissioned', 'Commissioned, to hand over'], ['handed_over', 'Handed over']]} />
      </div>
      {error ? <PageState kind="error" title="Couldn't load banks" message={error} />
        : !data ? <PageState kind="loading" title="Loading…" />
        : data.items.length === 0 ? <p className="empty-line">No battery banks here.</p>
        : (
          <div className="table-wrap"><table className="table stack">
            <thead><tr><th scope="col">Bank</th><th scope="col">Site</th><th scope="col" className="num">Batteries</th><th scope="col">Last stage</th><th scope="col">Stage</th></tr></thead>
            <tbody>{data.items.map((b) => (
              <tr key={b.id}><td className="cell-main"><Link to={`/installation/banks/${b.id}`}>{b.number}</Link><span className="small muted">{b.name}</span></td>
                <td className="cell-sub"><Link to={`/projects/${b.site!.project_id}`}>{b.site!.name}</Link></td><td className="num cell-sub">{b.batteries}</td>
                <td className="cell-sub nowrap">{day(b.handed_over_on ?? b.commissioned_on ?? b.installed_on)}</td><td className="cell-tag"><Status s={b.status} /></td></tr>
            ))}</tbody>
          </table></div>
        )}
      {data && <Pager offset={offset} limit={50} total={data.total} onChange={setOffset} />}
    </>
  )
}

type Change = { kind: 'remove' | 'replace' | 'relocate'; pos: Pos }

export function BankPage() {
  const { id } = useParams()
  const can = useCan()
  const when = useWhen()
  const { data: b, error, reload } = useApi<Bank>(`/banks/${id}`)
  const [dialog, setDialog] = useState<'add' | 'edit' | Change | null>(null)
  const action = useAction(() => { setDialog(null); reload() })
  const others = useApi<ProjectInstallation>(b && dialog && typeof dialog === 'object' && dialog.kind === 'relocate' ? `/projects/${b.site.project.id}/installation` : null)
  if (error) return <PageState kind="error" title="Couldn't load the bank" message={error} />
  if (!b) return <PageState kind="loading" title="Loading…" />
  const coord = can('installation.create')
  const change = dialog && typeof dialog === 'object' ? dialog : null
  return (
    <>
      <Back to={`/projects/${b.site.project.id}`}>{b.site.project.number} {b.site.project.name}</Back>
      <PageHead title={`${b.number} · ${b.name}`} intro={`${b.site.name} · ${b.site.customer.name}${b.configuration ? ` · ${b.configuration}` : ''}`}>
        {b.positions.length > 0 && <Link className="btn btn-secondary" target="_blank" to={`/print/labels?ids=${b.positions.map((p) => p.serial_id).join(',')}`}>Print labels</Link>}
        {coord && <button className="btn btn-secondary" onClick={() => setDialog('edit')}>Edit</button>}
        {coord && b.status === 'planned' && <button className="btn btn-primary" onClick={() => setDialog('add')}>Add batteries</button>}
      </PageHead>
      <Facts rows={[['Stage', <Status s={b.status} />], ['Installed', day(b.installed_on)], ['Commissioned', day(b.commissioned_on)], ['Handed over', day(b.handed_over_on)], ['Batteries', String(b.positions.length)]]} />
      <FormError message={action.error} />
      <h2 className="section">Batteries</h2>
      {b.positions.length === 0 ? <p className="empty-line">No batteries in this bank.</p> : (
        <div className="table-wrap"><table className="table stack">
          <thead><tr><th scope="col">Position</th><th scope="col">Serial</th><th scope="col">Item</th><th scope="col">Since</th><th scope="col"><span className="sr-only">Actions</span></th></tr></thead>
          <tbody>{b.positions.map((p) => (
            <tr key={p.serial}><td className="cell-tag">{p.position}</td><td className="cell-main"><Link to={`/stock/serials/${p.serial_id}`}><code>{p.serial}</code></Link></td>
              <td className="cell-sub">{p.item.code}</td><td className="cell-sub">{when(p.since)}</td>
              <td className="actions cell-action">{coord && <span className="row"><button className="link-btn" onClick={() => setDialog({ kind: 'replace', pos: p })}>Replace</button><button className="link-btn" onClick={() => setDialog({ kind: 'relocate', pos: p })}>Move</button><button className="link-btn" onClick={() => setDialog({ kind: 'remove', pos: p })}>Remove</button></span>}</td></tr>
          ))}</tbody>
        </table></div>
      )}
      {b.visits.length > 0 && <><h2 className="section">Visits</h2><ul className="feed panel panel-wide">{b.visits.map((v) => (
        <li key={v.id}><span><Link to={`/installation/visits/${v.id}`}>{v.number} · {STAGE[v.stage]}</Link><span className="small muted">{day(v.done_on ?? v.scheduled_on)}</span></span><Status s={v.status} /></li>
      ))}</ul></>}
      <h2 className="section">Placement history</h2>
      {b.history.length === 0 ? <p className="empty-line">Nothing yet.</p> : (
        <div className="table-wrap"><table className="table stack">
          <thead><tr><th scope="col">Serial</th><th scope="col">Position</th><th scope="col">In</th><th scope="col">Out</th></tr></thead>
          <tbody>{b.history.map((h, i) => (
            <tr key={i}><td className="cell-main"><Link to={`/stock/serials/${h.serial_id}`}><code>{h.serial}</code></Link></td><td className="cell-tag">{h.position}</td>
              <td className="cell-sub">{when(h.started_at)} · {h.start_reason === 'replacement' ? `replaced ${h.replaces}` : words(h.start_reason)} · {h.by}</td>
              <td className="cell-sub">{h.ended_at ? `${when(h.ended_at)} · ${words(h.end_reason)} · ${h.ended_by}${h.note ? ` · ${h.note}` : ''}` : 'Still here'}</td></tr>
          ))}</tbody>
        </table></div>
      )}
      {dialog === 'add' && <Dialog wide title={`Add batteries to ${b.number}`} onClose={() => setDialog(null)}><PlaceForm bank={b} siteId={b.site.id} taken={b.positions.length} onDone={() => { setDialog(null); reload() }} /></Dialog>}
      {dialog === 'edit' && (
        <Dialog title={`Edit ${b.number}`} onClose={() => setDialog(null)}>
          <form noValidate onSubmit={(e) => { e.preventDefault(); const fd = new FormData(e.currentTarget); void action.run('edit', () => api(`/banks/${b.id}`, { method: 'PUT', body: { version: b.version, name: fd.get('name'), configuration: fd.get('configuration') } })) }}>
            <Field label="Name" name="name" required maxLength={120} defaultValue={b.name} />
            <Field label="Configuration" name="configuration" maxLength={300} defaultValue={b.configuration} />
            <FormError message={action.error} />
            <button className="btn btn-primary" disabled={!!action.busy}>Save</button>
          </form>
        </Dialog>
      )}
      {change && <Dialog title={`${words(change.kind === 'relocate' ? 'move' : change.kind)} ${change.pos.serial}`} onClose={() => setDialog(null)}>
        <ChangeForm bank={b} change={change} banks={others.data?.banks ?? [b]} onDone={() => { setDialog(null); reload() }} /></Dialog>}
    </>
  )
}

/** Remove, replace or move one battery; on a visit, the visit is recorded with it. */
function ChangeForm({ bank, change, banks, visitId, onDone }: { bank: BankBrief; change: Change; banks: BankBrief[]; visitId?: string; onDone: () => void }) {
  const action = useAction(onDone)
  return (
    <form noValidate onSubmit={(e) => {
      e.preventDefault()
      const fd = new FormData(e.currentTarget)
      const body: Record<string, unknown> = { version: bank.version, visit_id: visitId ?? null, serial: change.pos.serial, reason: fd.get('reason') }
      if (change.kind === 'replace') body.new_serial = String(fd.get('new_serial') ?? '').trim().toUpperCase()
      if (change.kind === 'relocate') Object.assign(body, { to_bank_id: fd.get('to_bank_id'), position: fd.get('position') })
      void action.run(change.kind, () => api(`/banks/${bank.id}/${change.kind}`, { method: 'POST', body }))
    }}>
      <p className="hint">{change.kind === 'remove' ? 'The battery stays at the site, out of the bank, until it is returned or placed again.'
        : change.kind === 'replace' ? `The new battery takes position ${change.pos.position}. It must have been delivered to this site.${bank.status !== 'planned' ? ' This bank is in use, so record the replacement on a visit with its readings and photographs.' : ''}`
        : 'To another position here, or to another bank of the same customer at the same stage.'}</p>
      {change.kind === 'replace' && <Field label="New battery's serial" name="new_serial" required maxLength={60} autoComplete="off" />}
      {change.kind === 'relocate' && <>
        <SelectField label="To bank" name="to_bank_id" defaultValue={bank.id} options={banks.map((x) => [x.id, `${x.number} ${x.name}`])} />
        <Field label="Position" name="position" required maxLength={40} />
      </>}
      <TextareaField label="Reason" name="reason" required minLength={3} maxLength={500} />
      <FormError message={action.error} />
      <button className="btn btn-primary" disabled={!!action.busy}>{words(change.kind === 'relocate' ? 'move' : change.kind)}</button>
    </form>
  )
}

// ---------- SMET's stage checks ----------

export function StageChecksPage() {
  const can = useCan()
  const edit = can('installation.configure')
  const { data, error, reload } = useApi<{ items: Check[] }>('/stage-checks')
  const [dialog, setDialog] = useState<Check | string | null>(null)
  if (error) return <PageState kind="error" title="Couldn't load the checks" message={error} />
  if (!data) return <PageState kind="loading" title="Loading…" />
  return (
    <>
      <PageHead title="Site checks" intro="What a technician must tick off and measure at each stage. A reading is judged against its limits only once SMET marks them confirmed; until then it is recorded, not judged." />
      {[...STAGES.slice(0, 3), ...ALL_STAGES.slice(4)].map(([stage, label]) => {
        const rows = data.items.filter((c) => c.stage === stage)
        return (
          <section key={stage} className="panel panel-wide">
            <div className="panel-head"><h2>{label}</h2>{edit && <button className="btn btn-secondary" onClick={() => setDialog(stage)}>Add</button>}</div>
            {rows.length === 0 ? <p className="empty-line">Nothing set for this stage.</p> : (
              <ul className="feed">{rows.map((c) => (
                <li key={c.id} className={c.active ? '' : 'muted'}>
                  <span>{c.text}<span className="small muted">{c.kind === 'reading' ? `Reading · ${limits(c.min_value, c.max_value, c.unit)}${c.confirmed ? ' · confirmed' : ' · limits not confirmed'}` : 'Checklist'}{c.required ? '' : ' · optional'}{c.active ? '' : ' · switched off'}</span></span>
                  {edit && <button className="link-btn" onClick={() => setDialog(c)}>Edit</button>}
                </li>
              ))}</ul>
            )}
          </section>
        )
      })}
      {dialog && <Dialog title={typeof dialog === 'string' ? `Add to ${STAGE[dialog]}` : 'Edit check'} onClose={() => setDialog(null)}>
        <CheckForm check={typeof dialog === 'string' ? undefined : dialog} stage={typeof dialog === 'string' ? dialog : dialog.stage} onDone={() => { setDialog(null); reload() }} /></Dialog>}
    </>
  )
}

function CheckForm({ check, stage, onDone }: { check?: Check; stage: string; onDone: () => void }) {
  const [kind, setKind] = useState(check?.kind ?? 'check')
  const action = useAction(onDone)
  return (
    <form noValidate onSubmit={(e) => {
      e.preventDefault()
      const fd = new FormData(e.currentTarget)
      const reading = kind === 'reading'
      const body = { stage, kind, text: fd.get('text'), unit: reading ? fd.get('unit') : '', min_value: reading ? num(String(fd.get('min') ?? '')) ?? null : null,
        max_value: reading ? num(String(fd.get('max') ?? '')) ?? null : null, confirmed: reading && fd.get('confirmed') === 'on',
        required: fd.get('required') === 'on', sort: num(String(fd.get('sort') ?? '')) ?? 0, active: check ? fd.get('active') === 'on' : true }
      void action.run('save', () => api(check ? `/stage-checks/${check.id}` : '/stage-checks', { method: check ? 'PUT' : 'POST', body: check ? { ...body, version: check.version } : body }))
    }}>
      <SelectField label="Kind" value={kind} onChange={(e) => setKind(e.target.value as Check['kind'])} options={[['check', 'Checklist line'], ['reading', 'Reading']]} />
      <Field label={kind === 'reading' ? 'What is measured' : 'What is checked'} name="text" required maxLength={200} defaultValue={check?.text ?? ''} />
      {kind === 'reading' && <div className="grid-fields">
        <Field label="Unit" name="unit" maxLength={20} defaultValue={check?.unit ?? ''} />
        <Field label="Lowest" name="min" type="number" step="any" defaultValue={check?.min_value ?? ''} />
        <Field label="Highest" name="max" type="number" step="any" defaultValue={check?.max_value ?? ''} />
      </div>}
      {kind === 'reading' && <label className="check"><input type="checkbox" name="confirmed" defaultChecked={check?.confirmed ?? false} /> <span>SMET has confirmed these limits<span className="small muted">Only confirmed limits can stop a commissioning.</span></span></label>}
      <label className="check"><input type="checkbox" name="required" defaultChecked={check?.required ?? true} /> <span>Required</span></label>
      {check && <label className="check"><input type="checkbox" name="active" defaultChecked={check.active} /> <span>In use</span></label>}
      <Field label="Order" name="sort" type="number" min={0} max={9999} defaultValue={check?.sort ?? 0} />
      <FormError message={action.error} />
      <button className="btn btn-primary" disabled={!!action.busy}>Save</button>
    </form>
  )
}

// ---------- find by scan; labels; the public check ----------

type Detector = { detect: (v: HTMLVideoElement) => Promise<{ rawValue: string }[]> }

export function TracePage() {
  const nav = useNavigate()
  const [code, setCode] = useState('')
  const [error, setError] = useState('')
  const [camera, setCamera] = useState(false)
  const find = async (c: string) => {
    setError('')
    try {
      const r = await api<{ id: string }>(`/trace/scan?${new URLSearchParams({ code: c.trim() })}`)
      nav(`/stock/serials/${r.id}`)
    } catch (e) { setError(e instanceof Error ? e.message : 'Not found') }
  }
  const hasCamera = 'BarcodeDetector' in window && !!navigator.mediaDevices
  return (
    <>
      <PageHead title="Find a battery" intro="Scan its QR label, or type its serial number or the supplier's. A handheld scanner works too: it types into the box." />
      <form className="toolbar" role="search" onSubmit={(e) => { e.preventDefault(); if (code.trim()) void find(code) }}>
        <Field label="Label, serial or supplier serial" type="search" value={code} onChange={(e) => setCode(e.target.value)} autoFocus autoComplete="off" />
        <button className="btn btn-primary">Find</button>
        {hasCamera && <button type="button" className="btn btn-secondary" onClick={() => setCamera(!camera)}><Camera size={16} aria-hidden /> {camera ? 'Stop camera' : 'Use camera'}</button>}
      </form>
      <FormError message={error} />
      {camera && <Scanner onCode={(c) => { setCamera(false); setCode(c); void find(c) }} />}
      {!hasCamera && <p className="muted small">This browser can't read QR codes from the camera; use a phone's camera app to open the label, or a handheld scanner.</p>}
    </>
  )
}

function Scanner({ onCode }: { onCode: (code: string) => void }) {
  const video = useRef<HTMLVideoElement>(null)
  const [error, setError] = useState('')
  useEffect(() => {
    let stream: MediaStream | null = null
    let timer = 0
    const Ctor = (window as unknown as { BarcodeDetector: new (o: object) => Detector }).BarcodeDetector
    const detector = new Ctor({ formats: ['qr_code'] })
    navigator.mediaDevices.getUserMedia({ video: { facingMode: 'environment' } }).then((s) => {
      stream = s
      if (!video.current) return
      video.current.srcObject = s
      void video.current.play()
      timer = window.setInterval(async () => {
        const found = video.current && (await detector.detect(video.current).catch(() => []))
        if (found && found.length) { window.clearInterval(timer); onCode(found[0].rawValue) }
      }, 300)
    }).catch(() => setError('The camera is not available. Allow camera access, or type the serial number.'))
    return () => { window.clearInterval(timer); stream?.getTracks().forEach((t) => t.stop()) }
  }, [onCode])
  return error ? <FormError message={error} /> : <video ref={video} className="scanner" muted playsInline aria-label="Camera view: point it at the QR label" />
}

type Label = { id: string; serial: string; item: Ref; supplier_serial: string | null; url: string; qr: string }

/** Labels laid out for printing; the browser's Print sends them to a label or office printer. */
export function LabelsPage() {
  const ids = new URLSearchParams(window.location.search).get('ids') ?? ''
  const me = useMe()
  const { data, error } = useApi<{ items: Label[]; public_base_url: string }>(ids ? `/serial-labels?ids=${ids}` : null)
  if (!ids) return <PageState kind="empty" title="No batteries chosen" message="Print labels from a battery or a battery bank." />
  if (error) return <PageState kind="error" title="Couldn't make the labels" message={error} />
  if (!data) return <PageState kind="loading" title="Loading…" />
  const local = /localhost|127\.0\.0\.1/.test(data.public_base_url)
  return (
    <div className="labels-page">
      <div className="no-print">
        {local && <p className="notice notice-warning">These labels point to {data.public_base_url}, which only works on this computer. Set PUBLIC_BASE_URL to SMET's public address before printing labels for batteries that leave the building.</p>}
        <p className="row"><button className="btn btn-primary" onClick={() => window.print()}>Print {data.items.length} label{data.items.length > 1 ? 's' : ''}</button></p>
      </div>
      <div className="labels">{data.items.map((l) => (
        <article key={l.id} className="label">
          <img src={l.qr} alt={`QR code for ${l.serial}`} />
          <div><p className="label-brand">{me.company.name}</p><p className="label-serial">{l.serial}</p><p className="small">{l.item.name}</p>{l.supplier_serial && <p className="small">Cell {l.supplier_serial}</p>}<p className="small">Scan to check this battery</p></div>
        </article>
      ))}</div>
    </div>
  )
}

// ---------- a battery's whole life ----------

type Event = { at: string; kind: string; title: string; detail?: string; by?: string | null; doc?: { type: string; id: string; number: string }; link?: { type: string; id: string } }
type Timeline = { now: { status: string; customer: { name: string } | null; bank?: { id: string; number: string; name: string }; position?: string; site?: SiteRef; location?: string
  warranty?: { state: string; registration?: { id: string; number: string }; expiry_date?: string }; amc?: { id: string; number: string; ends_on: string }[] }; events: Event[] }
const DOC_PATH: Record<string, string> = { dispatch: '/dispatch/', visit: '/installation/visits/', assembly_order: '/assembly/orders/', purchase_order: '/purchasing/orders/',
  warranty: '/warranty/', service: '/service/', alert: '/service/alerts/' }

export function Lifecycle({ id }: { id: string }) {
  const when = useWhen()
  const { data } = useApi<Timeline>(`/serials/${id}/timeline`)
  if (!data) return null
  const n = data.now
  return (
    <>
      <h2 className="section">Where it is now</h2>
      <Facts rows={[['Status', <Status s={n.status} />], ['Customer', n.customer?.name ?? '—'],
        ['Site', n.site ? <Link to={`/projects/${n.site.project.id}`}>{n.site.name} · {n.site.project.number}</Link> : '—'],
        ['Bank', n.bank ? <Link to={`/installation/banks/${n.bank.id}`}>{n.bank.number} {n.bank.name}, position {n.position}</Link> : '—'],
        ['In stock at', n.location ?? '—'],
        ...(n.warranty ? [['Warranty', n.warranty.registration ? <Link to={`/warranty/${n.warranty.registration.id}`}>{words(n.warranty.state)}{n.warranty.expiry_date ? ` to ${day(n.warranty.expiry_date)}` : ''}</Link> : 'None'] as [string, React.ReactNode]] : []),
        ...(n.amc ? [['Maintenance contract', n.amc.length ? n.amc.map((c) => <Link key={c.id} to={`/amc/${c.id}`}>{c.number} to {day(c.ends_on)}</Link>) : 'None'] as [string, React.ReactNode]] : [])]} />
      <h2 className="section">Lifecycle</h2>
      <ol className="timeline panel panel-wide">{data.events.map((e, i) => (
        <li key={i} data-kind={e.kind}>
          <span><strong>{e.title}</strong>
            <span className="small muted">{[e.detail, e.doc?.number && (DOC_PATH[e.doc.type] ? <Link key="d" to={`${DOC_PATH[e.doc.type]}${e.doc.id}`}>{e.doc.number}</Link> : e.doc.number),
              e.link?.type === 'serial' && <Link key="l" to={`/stock/serials/${e.link.id}`}>Open</Link>, e.link?.type === 'bank' && <Link key="b" to={`/installation/banks/${e.link.id}`}>Bank</Link>, e.by]
              .filter(Boolean).reduce<React.ReactNode[]>((acc, x, k) => (k ? [...acc, ' · ', x] : [x]), [])}</span></span>
          <span className="stamp">{e.at.length > 10 ? when(e.at) : day(e.at)}</span>
        </li>
      ))}</ol>
    </>
  )
}
