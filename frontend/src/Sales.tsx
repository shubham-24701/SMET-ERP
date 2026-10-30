import { Plus, Trash2 } from 'lucide-react'
import { useState } from 'react'
import { Link, useNavigate, useParams } from 'react-router'
import { api } from './api'
import { useAction, useApi, useCan, useMe, useWhen } from './lib'
import { Back, Facts, ItemOptions, Status, day, money, newKey, num, qty, useItems, useWarehouses, type Page, type Ref } from './ops'
import { Dialog, Field, FormError, PageHead, PageState, Pager, SelectField, TextareaField } from './ui'

type PartyRef = { id: string; code: string; name: string }
type Line = { id: string; line_type: string; item: Ref | null; description: string; qty: number; unit_price: number; discount_pct: number; total: number }
type Revision = {
  id: string; rev: number; status: string; currency: string; valid_until: string | null; terms: string; scope_notes: string
  installation_date: string | null; commissioning_date: string | null; handover_date: string | null; editor_ids: string[]
  submitted_by: string | null; submitted_by_id: string | null; decided_by: string | null; decided_at: string | null; decision_reason: string
  customer_answer_by: string; customer_ref: string; customer_answer_at: string | null; total: number; max_discount: number
  version: number; lines: Line[]
}
type Quotation = {
  id: string; number: string; customer: PartyRef; project_id: string | null; rev: number; status: string; currency: string
  valid_until: string | null; total: number; created_at: string; revisions?: Revision[]
  sales_order?: { id: string; number: string } | null; project?: { id: string; number: string; name: string }
}
type Enquiry = {
  id: string; number: string; customer: PartyRef; kind: string; status: string; contact: string; needed_by: string | null
  notes: string; created_at: string; version: number; lines: { item: Ref | null; description: string; qty: string }[]
  quotations: { id: string; number: string }[]
}
type OrderLine = Line & { assembled: boolean; qty_dispatched: number; qty_delivered: number; pending: number; reserved: number; reserved_for_project: number; free: number; short: number }
type Order = {
  id: string; number: string; customer: PartyRef; status: string; currency: string; customer_ref: string; delivery_date: string | null
  delivery_address: string; notes: string; project_id: string | null; created_at: string; version: number; total: number; pending_goods: number
  lines?: OrderLine[]; dispatches?: { id: string; number: string; status: string; planned_date: string | null }[]
  quotation?: { id: string; number: string; rev: number }; project?: { id: string; number: string; name: string }
}

export const LINE_TYPES: [string, string][] = [['battery', 'Battery'], ['accessory', 'Accessory'], ['installation', 'Installation'], ['commissioning', 'Commissioning'], ['amc', 'Maintenance contract (AMC)'], ['other', 'Other']]
const GOODS = ['battery', 'accessory']
const fieldsOf = (err: unknown) => (err as { fields?: Record<string, string> }).fields ?? {}

export function useCustomers() {
  const all = useApi<Page<PartyRef & { kinds: string[]; currency: string; address: string }>>('/parties?limit=200').data?.items ?? []
  return all.filter((p) => p.kinds.some((k) => ['customer', 'distributor', 'dealer'].includes(k)))
}

// ---------- enquiries ----------

export function EnquiriesPage() {
  const can = useCan()
  const nav = useNavigate()
  const when = useWhen()
  const items = useItems()
  const customers = useCustomers()
  const [status, setStatus] = useState('open')
  const [offset, setOffset] = useState(0)
  const [adding, setAdding] = useState(false)
  const [rows, setRows] = useState([{ key: newKey(), item_id: '', description: '', qty: '1' }])
  const { data, error, reload } = useApi<Page<Enquiry>>(`/enquiries?${new URLSearchParams({ offset: String(offset), limit: '50', ...(status ? { status } : {}) })}`)
  const action = useAction(() => { setAdding(false); reload() })
  return (
    <>
      <PageHead title="Enquiries" intro="What customers ask for, before a price. Prepare a quotation from an enquiry in one step.">
        {can('sales.create') && <button className="btn btn-primary" onClick={() => setAdding(true)}>Record enquiry</button>}
      </PageHead>
      <div className="toolbar"><SelectField label="Status" value={status} onChange={(e) => { setStatus(e.target.value); setOffset(0) }} options={[['open', 'Open'], ['quoted', 'Quoted'], ['lost', 'Lost'], ['closed', 'Closed'], ['', 'All']]} /></div>
      <FormError message={action.error} />
      {error ? <PageState kind="error" title="Couldn't load enquiries" message={error} />
        : !data ? <PageState kind="loading" title="Loading…" />
        : data.items.length === 0 ? <PageState kind="empty" title="No enquiries here" />
        : (
          <div className="table-wrap">
            <table className="table stack">
              <thead><tr><th scope="col">Enquiry</th><th scope="col">Customer</th><th scope="col">Asked for</th><th scope="col">Needed by</th><th scope="col">Status</th><th scope="col"><span className="sr-only">Actions</span></th></tr></thead>
              <tbody>
                {data.items.map((e) => (
                  <tr key={e.id}>
                    <td className="cell-main">{e.number}<span className="small muted">{e.kind === 'project' ? 'Project supply' : 'Products'} · {when(e.created_at)}</span></td>
                    <td className="cell-sub">{e.customer.name}{e.contact && <span className="small muted">{e.contact}</span>}</td>
                    <td className="cell-sub">{e.lines.map((l) => `${l.item?.code ?? l.description} × ${qty(Number(l.qty))}`).join(', ')}</td>
                    <td className="cell-sub nowrap">{day(e.needed_by)}</td>
                    <td className="cell-tag"><Status s={e.status} /></td>
                    <td className="actions cell-action">
                      {e.quotations.map((q) => <Link key={q.id} to={`/sales/quotations/${q.id}`}>{q.number}</Link>)}
                      {can('sales.create') && e.status === 'open' && <button className="link-btn" onClick={() => action.run(e.id, async () => { const q = await api<Quotation>(`/enquiries/${e.id}/quote`, { method: 'POST' }); nav(`/sales/quotations/${q.id}`) })}>Prepare quotation</button>}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      {data && <Pager offset={offset} limit={50} total={data.total} onChange={setOffset} />}
      {adding && (
        <Dialog wide title="Record enquiry" onClose={() => setAdding(false)}>
          <form noValidate onSubmit={(e) => {
            e.preventDefault()
            const fd = new FormData(e.currentTarget)
            void action.run('add', () => api('/enquiries', { method: 'POST', body: {
              customer_id: fd.get('customer_id'), kind: fd.get('kind'), contact: fd.get('contact'), needed_by: fd.get('needed_by') || null, notes: fd.get('notes'),
              lines: rows.filter((r) => r.item_id || r.description).map((r) => ({ item_id: r.item_id || null, description: r.description, qty: num(r.qty) })) } }))
          }}>
            <div className="grid-fields">
              <SelectField label="Customer" name="customer_id" options={[['', 'Choose a customer'], ...customers.map((c): [string, string] => [c.id, c.name])]} />
              <SelectField label="For" name="kind" options={[['product', 'Products only'], ['project', 'Project supply']]} />
              <Field label="Contact" name="contact" maxLength={160} />
              <Field label="Needed by" name="needed_by" type="date" />
            </div>
            <fieldset className="lines"><legend>Asked for</legend>
              {rows.map((r, i) => (
                <div className="line" key={r.key}>
                  <label className="line-item"><span className="line-label">Item (if known)</span><select value={r.item_id} onChange={(e) => setRows(rows.map((x, j) => (j === i ? { ...x, item_id: e.target.value } : x)))}><option value="">—</option><ItemOptions items={items} /></select></label>
                  <label className="line-item"><span className="line-label">Or describe it</span><input value={r.description} maxLength={300} onChange={(e) => setRows(rows.map((x, j) => (j === i ? { ...x, description: e.target.value } : x)))} /></label>
                  <label style={{ inlineSize: 100 }}><span className="line-label">Quantity</span><input type="number" min={0} step="any" value={r.qty} onChange={(e) => setRows(rows.map((x, j) => (j === i ? { ...x, qty: e.target.value } : x)))} /></label>
                  <button type="button" className="btn btn-ghost" aria-label={`Remove line ${i + 1}`} onClick={() => setRows(rows.filter((_, j) => j !== i))}><Trash2 size={16} aria-hidden="true" /></button>
                </div>
              ))}
              <button type="button" className="btn btn-secondary" onClick={() => setRows([...rows, { key: newKey(), item_id: '', description: '', qty: '1' }])}><Plus size={16} aria-hidden="true" /> Add line</button>
            </fieldset>
            <TextareaField label="Notes" name="notes" maxLength={4000} />
            <FormError message={action.error} />
            <button className="btn btn-primary" disabled={!!action.busy}>Record enquiry</button>
          </form>
        </Dialog>
      )}
    </>
  )
}

// ---------- quotations ----------

type EditLine = { key: string; line_type: string; item_id: string; description: string; qty: string; unit_price: string; discount_pct: string }
const toEdit = (l: Line): EditLine => ({ key: newKey(), line_type: l.line_type, item_id: l.item?.id ?? '', description: l.description, qty: String(l.qty), unit_price: String(l.unit_price), discount_pct: String(l.discount_pct) })
const blank = (): EditLine => ({ key: newKey(), line_type: 'battery', item_id: '', description: '', qty: '1', unit_price: '0', discount_pct: '0' })

function QuoteLines({ rows, setRows, currency }: { rows: EditLine[]; setRows: (r: EditLine[]) => void; currency: string }) {
  const items = useItems()
  const set = (i: number, k: keyof EditLine, v: string) => setRows(rows.map((r, j) => (j === i ? { ...r, [k]: v } : r)))
  const total = rows.reduce((s, r) => s + (num(r.qty) ?? 0) * (num(r.unit_price) ?? 0) * (100 - (num(r.discount_pct) ?? 0)) / 100, 0)
  return (
    <fieldset className="lines">
      <legend>Lines</legend>
      {rows.map((r, i) => (
        <div className="line" key={r.key}>
          <label style={{ inlineSize: 170 }}><span className="line-label">Type</span><select value={r.line_type} onChange={(e) => set(i, 'line_type', e.target.value)}>{LINE_TYPES.map(([v, l]) => <option key={v} value={v}>{l}</option>)}</select></label>
          {GOODS.includes(r.line_type)
            ? <label className="line-item"><span className="line-label">Item</span><select value={r.item_id} onChange={(e) => set(i, 'item_id', e.target.value)}><option value="">Choose an item</option><ItemOptions items={items} /></select></label>
            : <label className="line-item"><span className="line-label">Description</span><input value={r.description} maxLength={300} onChange={(e) => set(i, 'description', e.target.value)} /></label>}
          <label style={{ inlineSize: 90 }}><span className="line-label">Quantity</span><input type="number" min={0} step="any" value={r.qty} onChange={(e) => set(i, 'qty', e.target.value)} /></label>
          <label style={{ inlineSize: 120 }}><span className="line-label">Unit price</span><input type="number" min={0} step="any" value={r.unit_price} onChange={(e) => set(i, 'unit_price', e.target.value)} /></label>
          <label style={{ inlineSize: 90 }}><span className="line-label">Discount %</span><input type="number" min={0} max={100} step="any" value={r.discount_pct} onChange={(e) => set(i, 'discount_pct', e.target.value)} /></label>
          <button type="button" className="btn btn-ghost" aria-label={`Remove line ${i + 1}`} onClick={() => setRows(rows.filter((_, j) => j !== i))}><Trash2 size={16} aria-hidden="true" /></button>
        </div>
      ))}
      <div className="row"><button type="button" className="btn btn-secondary" onClick={() => setRows([...rows, blank()])}><Plus size={16} aria-hidden="true" /> Add line</button>
        <span className="muted">Total {money(total, currency)}</span></div>
    </fieldset>
  )
}

const lineBody = (rows: EditLine[]) => rows.filter((r) => r.item_id || r.description).map((r) => ({
  line_type: r.line_type, item_id: GOODS.includes(r.line_type) ? r.item_id || null : null, description: GOODS.includes(r.line_type) ? '' : r.description,
  qty: num(r.qty), unit_price: num(r.unit_price) ?? 0, discount_pct: num(r.discount_pct) ?? 0,
}))

function RevisionForm({ quotation, rev, onDone }: { quotation: Quotation | null; rev: Revision | null; onDone: (q: Quotation) => void }) {
  const customers = useCustomers()
  const [rows, setRows] = useState<EditLine[]>(rev?.lines.map(toEdit) ?? [blank()])
  const [currency, setCurrency] = useState(rev?.currency ?? 'AED')
  const [fields, setFields] = useState<Record<string, string>>({})
  const action = useAction(() => {})
  return (
    <form noValidate onSubmit={(e) => {
      e.preventDefault()
      const fd = new FormData(e.currentTarget)
      const s = (n: string) => String(fd.get(n) ?? '')
      const body = { currency, valid_until: s('valid_until') || null, terms: s('terms'), scope_notes: s('scope_notes'),
        installation_date: s('installation_date') || null, commissioning_date: s('commissioning_date') || null, handover_date: s('handover_date') || null, lines: lineBody(rows) }
      setFields({})
      void action.run('save', () => api<Quotation>(quotation ? `/quotations/${quotation.id}/draft` : '/quotations', {
        method: quotation ? 'PUT' : 'POST', body: quotation ? { ...body, version: rev!.version } : { ...body, customer_id: s('customer_id') } })
        .then(onDone).catch((err) => { setFields(fieldsOf(err)); throw err }))
    }}>
      <div className="grid-fields">
        {!quotation && <SelectField label="Customer" name="customer_id" options={[['', 'Choose a customer'], ...customers.map((c): [string, string] => [c.id, c.name])]} error={fields.customer_id} />}
        <Field label="Currency" value={currency} onChange={(e) => setCurrency(e.target.value.toUpperCase())} maxLength={3} error={fields.currency} />
        <Field label="Valid until" name="valid_until" type="date" defaultValue={rev?.valid_until ?? ''} error={fields.valid_until} hint="Empty: the default validity in Rules & numbering" />
        <Field label="Installation" name="installation_date" type="date" defaultValue={rev?.installation_date ?? ''} />
        <Field label="Commissioning" name="commissioning_date" type="date" defaultValue={rev?.commissioning_date ?? ''} />
        <Field label="Handover" name="handover_date" type="date" defaultValue={rev?.handover_date ?? ''} />
      </div>
      <QuoteLines rows={rows} setRows={setRows} currency={currency} />
      {fields.lines && <p className="field-error">{fields.lines}</p>}
      <TextareaField label="Scope notes and specifications" name="scope_notes" defaultValue={rev?.scope_notes} maxLength={4000} />
      <TextareaField label="Commercial terms" name="terms" defaultValue={rev?.terms} maxLength={4000} />
      <FormError message={action.error} />
      <button className="btn btn-primary" disabled={!!action.busy}>{quotation ? 'Save draft' : 'Create draft'}</button>
    </form>
  )
}

export function QuotationsPage() {
  const can = useCan()
  const nav = useNavigate()
  const [q, setQ] = useState('')
  const [offset, setOffset] = useState(0)
  const [adding, setAdding] = useState(false)
  const { data, error } = useApi<Page<Quotation>>(`/quotations?${new URLSearchParams({ q, offset: String(offset), limit: '50' })}`)
  return (
    <>
      <PageHead title="Quotations" intro="Each change after submission is a new revision; earlier revisions stay as they were. An accepted revision becomes one sales order.">
        {can('sales.create') && <button className="btn btn-primary" onClick={() => setAdding(true)}>New quotation</button>}
      </PageHead>
      <div className="toolbar" role="search"><Field label="Search number or customer" type="search" value={q} onChange={(e) => { setQ(e.target.value); setOffset(0) }} /></div>
      {error ? <PageState kind="error" title="Couldn't load quotations" message={error} />
        : !data ? <PageState kind="loading" title="Loading…" />
        : data.items.length === 0 ? <PageState kind="empty" title="No quotations yet" message="Start from an enquiry or a new quotation." />
        : (
          <div className="table-wrap">
            <table className="table stack">
              <thead><tr><th scope="col">Quotation</th><th scope="col">Customer</th><th scope="col" className="num">Total</th><th scope="col">Valid until</th><th scope="col">Status</th></tr></thead>
              <tbody>
                {data.items.map((x) => (
                  <tr key={x.id}>
                    <td className="cell-main"><Link to={`/sales/quotations/${x.id}`}>{x.number}</Link><span className="small muted">Revision {x.rev}</span></td>
                    <td className="cell-sub">{x.customer.name}</td>
                    <td className="num cell-sub">{money(x.total, x.currency)}</td>
                    <td className="cell-sub nowrap">{day(x.valid_until)}</td>
                    <td className="cell-tag"><Status s={x.status} /></td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      {data && <Pager offset={offset} limit={50} total={data.total} onChange={setOffset} />}
      {adding && <Dialog wide title="New quotation" onClose={() => setAdding(false)}><RevisionForm quotation={null} rev={null} onDone={(x) => nav(`/sales/quotations/${x.id}`)} /></Dialog>}
    </>
  )
}

function Reasoned({ label, button, required, onSubmit }: { label: string; button: string; required?: boolean; onSubmit: (reason: string) => Promise<unknown> }) {
  const action = useAction(() => {})
  return (
    <form noValidate onSubmit={(e) => { e.preventDefault(); void action.run('go', () => onSubmit(String(new FormData(e.currentTarget).get('reason') ?? ''))) }}>
      <TextareaField label={label} name="reason" required={required} maxLength={500} hint="Kept in the audit log." />
      <FormError message={action.error} />
      <button className="btn btn-primary" disabled={!!action.busy}>{button}</button>
    </form>
  )
}

function LinesTable({ rev }: { rev: Revision }) {
  return (
    <div className="table-wrap">
      <table className="table">
        <thead><tr><th scope="col">Line</th><th scope="col" className="num">Quantity</th><th scope="col" className="num">Unit price</th><th scope="col" className="num">Discount</th><th scope="col" className="num">Total</th></tr></thead>
        <tbody>
          {rev.lines.map((l) => (
            <tr key={l.id}>
              <td>{l.item ? <><code>{l.item.code}</code><span className="small">{l.item.name}</span></> : l.description}<span className="small muted">{LINE_TYPES.find(([v]) => v === l.line_type)?.[1]}</span></td>
              <td className="num">{qty(l.qty, l.item?.unit_code)}</td>
              <td className="num">{money(l.unit_price, rev.currency)}</td>
              <td className="num">{l.discount_pct ? `${l.discount_pct}%` : '—'}</td>
              <td className="num">{money(l.total, rev.currency)}</td>
            </tr>
          ))}
          <tr><td colSpan={4} className="num"><strong>Total</strong></td><td className="num"><strong>{money(rev.total, rev.currency)}</strong></td></tr>
        </tbody>
      </table>
    </div>
  )
}

export function QuotationPage() {
  const { id } = useParams()
  const can = useCan()
  const me = useMe()
  const nav = useNavigate()
  const when = useWhen()
  const { data: qt, error, reload } = useApi<Quotation>(`/quotations/${id}`)
  const [dialog, setDialog] = useState<'edit' | 'approve' | 'reject' | 'answer' | 'convert' | null>(null)
  const action = useAction(reload)
  if (error) return <PageState kind="error" title="Couldn't load the quotation" message={error} />
  if (!qt) return <PageState kind="loading" title="Loading…" />
  const r = qt.revisions![0]
  const done = () => { setDialog(null); reload() }
  const post = (path: string, body: object = {}) => api(`/quotations/${qt.id}/${path}`, { method: 'POST', body: { version: r.version, ...body } })
  const mine = [r.submitted_by_id, ...r.editor_ids].includes(me.user.id)
  return (
    <>
      <Back to="/sales/quotations">Quotations</Back>
      <PageHead title={`${qt.number} · R${r.rev}`} intro={`${qt.customer.name}${qt.project ? ` · project ${qt.project.number}` : ''}`}>
        {can('sales.create') && r.status === 'draft' && <>
          <button className="btn btn-secondary" onClick={() => setDialog('edit')}>Edit draft</button>
          <button className="btn btn-primary" onClick={() => action.run('submit', () => post('submit'))}>Submit for approval</button>
        </>}
        {can('sales.approve') && r.status === 'submitted' && !mine && <>
          <button className="btn btn-secondary" onClick={() => setDialog('reject')}>Reject</button>
          <button className="btn btn-primary" onClick={() => setDialog('approve')}>Approve</button>
        </>}
        {can('sales.create') && r.status === 'approved' && <button className="btn btn-primary" onClick={() => setDialog('answer')}>Record customer's answer</button>}
        {can('sales.create') && r.status === 'accepted' && !qt.sales_order && <button className="btn btn-primary" onClick={() => setDialog('convert')}>Create sales order</button>}
        {can('sales.create') && ['submitted', 'approved', 'rejected', 'declined', 'expired'].includes(r.status) && <button className="btn btn-secondary" onClick={() => action.run('revise', () => post('revise'), `Draft revision ${r.rev + 1}? Revision ${r.rev} is kept as it is.`)}>Revise</button>}
      </PageHead>
      {r.status === 'submitted' && mine && can('sales.approve') && <p className="notice notice-warning">You prepared or submitted this revision, so someone else approves it.</p>}
      {r.status === 'rejected' && <p className="notice notice-warning">Rejected by {r.decided_by}: {r.decision_reason}. Revise it to change it.</p>}
      {r.status === 'expired' && <p className="notice notice-warning">Valid until {day(r.valid_until)} has passed. Revise it to send a new one.</p>}
      {qt.sales_order && <p className="notice notice-success">Converted to <Link to={`/sales/orders/${qt.sales_order.id}`}>{qt.sales_order.number}</Link>.</p>}
      <Facts rows={[['Status', <Status s={r.status} />], ['Total', money(r.total, r.currency)], ['Valid until', day(r.valid_until)],
        ['Approval', r.decided_at ? `${r.decided_by ?? 'Automatic'}, ${when(r.decided_at)}` : r.submitted_by ? `Submitted by ${r.submitted_by}` : 'Not submitted'],
        ['Customer', r.customer_answer_at ? `${r.customer_answer_by}, ${day(r.customer_answer_at)}${r.customer_ref ? ` (ref ${r.customer_ref})` : ''}` : '—']]} />
      <FormError message={action.error} />
      <LinesTable rev={r} />
      {(r.installation_date || r.commissioning_date || r.handover_date) && <p className="muted">Installation {day(r.installation_date)} · commissioning {day(r.commissioning_date)} · handover {day(r.handover_date)}</p>}
      {(r.scope_notes || r.terms) && <section className="panel panel-wide">{r.scope_notes && <><h2>Scope</h2><p className="prewrap">{r.scope_notes}</p></>}{r.terms && <><h2>Terms</h2><p className="prewrap">{r.terms}</p></>}</section>}
      {qt.revisions!.length > 1 && (
        <>
          <h2 className="section">Earlier revisions</h2>
          <ul className="feed panel panel-wide">
            {qt.revisions!.slice(1).map((x) => <li key={x.id}><span>R{x.rev} · {money(x.total, x.currency)}{x.decision_reason && <span className="small muted">{x.decision_reason}</span>}</span><Status s={x.status} /></li>)}
          </ul>
        </>
      )}
      {dialog === 'edit' && <Dialog wide title={`Edit ${qt.number} R${r.rev}`} onClose={() => setDialog(null)}><RevisionForm quotation={qt} rev={r} onDone={done} /></Dialog>}
      {dialog === 'approve' && <Dialog title={`Approve ${qt.number}`} onClose={() => setDialog(null)}><Reasoned label="Comment (optional)" button="Approve" onSubmit={(reason) => post('approve', { reason }).then(done)} /></Dialog>}
      {dialog === 'reject' && <Dialog title={`Reject ${qt.number}`} onClose={() => setDialog(null)}><Reasoned label="What to change" required button="Reject" onSubmit={(reason) => post('reject', { reason }).then(done)} /></Dialog>}
      {dialog === 'answer' && (
        <Dialog title="The customer's answer" onClose={() => setDialog(null)}>
          <form noValidate onSubmit={(e) => {
            e.preventDefault()
            const fd = new FormData(e.currentTarget)
            void action.run('answer', () => post('answer', { accepted: fd.get('accepted') === 'yes', by: fd.get('by'), ref: fd.get('ref'), on: fd.get('on'), reason: fd.get('reason') }).then(done))
          }}>
            <fieldset className="checks row-checks"><legend>The customer</legend>
              <label className="radio"><input type="radio" name="accepted" value="yes" defaultChecked /> accepted</label>
              <label className="radio"><input type="radio" name="accepted" value="no" /> declined</label>
            </fieldset>
            <Field label="Who answered" name="by" required maxLength={160} />
            <Field label="Their reference (e.g. purchase order)" name="ref" maxLength={60} />
            <Field label="On" name="on" type="date" required defaultValue={new Date().toISOString().slice(0, 10)} />
            <TextareaField label="Note" name="reason" maxLength={500} />
            <FormError message={action.error} />
            <button className="btn btn-primary" disabled={!!action.busy}>Record answer</button>
          </form>
        </Dialog>
      )}
      {dialog === 'convert' && (
        <Dialog title="Create sales order" onClose={() => setDialog(null)}>
          <form noValidate onSubmit={(e) => {
            e.preventDefault()
            const fd = new FormData(e.currentTarget)
            void action.run('convert', async () => { const so = await post('convert', { delivery_date: fd.get('delivery_date') || null, delivery_address: fd.get('delivery_address'), notes: fd.get('notes') }) as Order; nav(`/sales/orders/${so.id}`) })
          }}>
            <Field label="Committed delivery date" name="delivery_date" type="date" />
            <TextareaField label="Delivery address" name="delivery_address" maxLength={4000} />
            <TextareaField label="Notes" name="notes" maxLength={4000} />
            <FormError message={action.error} />
            <button className="btn btn-primary" disabled={!!action.busy}>Create sales order</button>
          </form>
        </Dialog>
      )}
    </>
  )
}

// ---------- sales orders ----------

const SO_CARDS: [string, string][] = [['open', 'Open'], ['partly_dispatched', 'Partly dispatched'], ['dispatched', 'Dispatched'], ['closed', 'Closed']]

export function SalesOrdersPage() {
  const [status, setStatus] = useState('')
  const [q, setQ] = useState('')
  const [offset, setOffset] = useState(0)
  const { data, error } = useApi<Page<Order>>(`/sales-orders?${new URLSearchParams({ q, offset: String(offset), limit: '50', ...(status ? { status } : {}) })}`)
  return (
    <>
      <PageHead title="Sales orders" intro="Created from accepted quotations. Reserve stock, request what is short, and dispatch, in part or in full." />
      {data?.counts && <div className="counts" role="group" aria-label="Show orders by status">{SO_CARDS.map(([s, l]) => <button key={s} className="count" aria-pressed={status === s} onClick={() => { setStatus(status === s ? '' : s); setOffset(0) }}><span>{l}</span><strong>{data.counts?.[s] ?? 0}</strong></button>)}</div>}
      <div className="toolbar" role="search"><Field label="Search number, customer or their reference" type="search" value={q} onChange={(e) => { setQ(e.target.value); setOffset(0) }} /></div>
      {error ? <PageState kind="error" title="Couldn't load orders" message={error} />
        : !data ? <PageState kind="loading" title="Loading…" />
        : data.items.length === 0 ? <PageState kind="empty" title="No sales orders here" message="Accepted quotations become sales orders." />
        : (
          <div className="table-wrap">
            <table className="table stack">
              <thead><tr><th scope="col">Order</th><th scope="col">Customer</th><th scope="col" className="num">Total</th><th scope="col" className="num">Still to send</th><th scope="col">Delivery</th><th scope="col">Status</th></tr></thead>
              <tbody>
                {data.items.map((o) => (
                  <tr key={o.id}>
                    <td className="cell-main"><Link to={`/sales/orders/${o.id}`}>{o.number}</Link>{o.customer_ref && <span className="small muted">Their ref {o.customer_ref}</span>}</td>
                    <td className="cell-sub">{o.customer.name}</td>
                    <td className="num cell-sub">{money(o.total, o.currency)}</td>
                    <td className="num cell-sub">{qty(o.pending_goods)}</td>
                    <td className="cell-sub nowrap">{day(o.delivery_date)}</td>
                    <td className="cell-tag"><Status s={o.status} /></td>
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

export function SalesOrderPage() {
  const { id } = useParams()
  const can = useCan()
  const nav = useNavigate()
  const { warehouses } = useWarehouses(can('warehouses.view'))
  const { data: so, error, reload } = useApi<Order>(`/sales-orders/${id}`)
  const [closing, setClosing] = useState(false)
  const [key, setKey] = useState(newKey)
  const action = useAction(() => { setKey(newKey()); reload() })
  if (error) return <PageState kind="error" title="Couldn't load the order" message={error} />
  if (!so) return <PageState kind="loading" title="Loading…" />
  const open = ['open', 'partly_dispatched'].includes(so.status)
  const shortBought = so.lines!.some((l) => l.short > 0 && !l.assembled)
  return (
    <>
      <Back to="/sales/orders">Sales orders</Back>
      <PageHead title={so.number} intro={`${so.customer.name}${so.quotation ? ` · from ${so.quotation.number} R${so.quotation.rev}` : ''}${so.project ? ` · project ${so.project.number}` : ''}`}>
        {open && can('sales.create') && <button className="btn btn-secondary" onClick={() => action.run('reserve', () => api(`/sales-orders/${so.id}/reserve`, { method: 'POST', body: { key } }))}>Reserve free stock</button>}
        {open && can('dispatch.create') && <button className="btn btn-primary" onClick={() => nav(`/dispatch/new?order=${so.id}`)}>Plan a dispatch</button>}
      </PageHead>
      <Facts rows={[['Status', <Status s={so.status} />], ['Total', money(so.total, so.currency)], ['Committed delivery', day(so.delivery_date)],
        ['Their reference', so.customer_ref || '—'], ['Still to send', qty(so.pending_goods)]]} />
      <FormError message={action.error} />
      <div className="table-wrap">
        <table className="table">
          <thead><tr><th scope="col">Line</th><th scope="col" className="num">Ordered</th><th scope="col" className="num">Sent</th><th scope="col" className="num">Delivered</th><th scope="col" className="num">Still to send</th><th scope="col" className="num">Reserved</th><th scope="col" className="num">Free</th><th scope="col" className="num">Short</th><th scope="col"><span className="sr-only">Actions</span></th></tr></thead>
          <tbody>
            {so.lines!.map((l) => {
              const goods = GOODS.includes(l.line_type)
              return (
                <tr key={l.id}>
                  <td>{l.item ? <><code>{l.item.code}</code><span className="small">{l.item.name}</span></> : l.description}<span className="small muted">{LINE_TYPES.find(([v]) => v === l.line_type)?.[1]}</span></td>
                  <td className="num">{qty(l.qty, l.item?.unit_code)}</td>
                  <td className="num">{goods ? qty(l.qty_dispatched) : '—'}</td>
                  <td className="num">{goods ? qty(l.qty_delivered) : '—'}</td>
                  <td className="num"><strong>{goods ? qty(l.pending) : 'Service'}</strong></td>
                  <td className="num">{goods ? qty(l.reserved + l.reserved_for_project) : '—'}</td>
                  <td className="num">{goods ? qty(l.free) : '—'}</td>
                  <td className="num">{l.short ? <strong className="low">{qty(l.short)}</strong> : '—'}</td>
                  <td className="actions">{open && l.short > 0 && l.assembled && can('assembly.create') && warehouses[0] &&
                    <button className="link-btn" onClick={() => action.run(l.id, async () => { const ao = await api<{ id: string }>(`/sales-orders/${so.id}/assembly-order`, { method: 'POST', body: { line_id: l.id, warehouse_id: warehouses[0].id } }); nav(`/assembly/orders/${ao.id}`) })}>Plan assembly</button>}</td>
                </tr>
              )
            })}
          </tbody>
        </table>
      </div>
      <div className="row">
        {open && shortBought && can('sales.create') && <button className="btn btn-secondary" onClick={() => action.run('pr', async () => { const pr = await api<{ id: string }>(`/sales-orders/${so.id}/purchase-request`, { method: 'POST' }); nav(`/purchasing/requests/${pr.id}`) })}>Request purchase of what is short</button>}
        {open && can('sales.create') && <button className="btn btn-ghost" onClick={() => setClosing(true)}>Close order</button>}
      </div>
      {so.dispatches!.length > 0 && (
        <>
          <h2 className="section">Dispatches</h2>
          <ul className="feed panel panel-wide">{so.dispatches!.map((d) => <li key={d.id}><span><Link to={`/dispatch/${d.id}`}>{d.number}</Link><span className="small muted">Planned {day(d.planned_date)}</span></span><Status s={d.status} /></li>)}</ul>
        </>
      )}
      {closing && <Dialog title={`Close ${so.number}`} onClose={() => setClosing(false)}><Reasoned label="Why (nothing sent yet: the order is cancelled)" required button="Close order" onSubmit={(reason) => api(`/sales-orders/${so.id}/close`, { method: 'POST', body: { version: so.version, reason } }).then(() => { setClosing(false); reload() })} /></Dialog>}
    </>
  )
}
