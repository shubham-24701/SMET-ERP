import { useState } from 'react'
import { Link, useNavigate, useParams } from 'react-router'
import { api } from './api'
import { useAction, useApi, useCan, useMe, useWhen } from './lib'
import {
  Back, Facts, LineRows, Status, day, money, newKey, num, qty, serialList, useItems, useRows, useWarehouses,
  type Page, type Ref, type Row,
} from './ops'
import { Dialog, Field, FormError, PageHead, PageState, Pager, SelectField, TextareaField } from './ui'

type PartyRef = { id: string; code: string; name: string }
type PRLine = { id: string; item: Ref; qty: number; notes: string }
type Offer = { quote_id: string; quote: string; supplier: string; currency: string; qty: number; unit_price: number; total: number; delivery_date: string | null; delivery_days: number | null; spec_notes: string; status: string; lowest_price: boolean; earliest: boolean }
type Quote = { id: string; number: string; supplier: PartyRef; supplier_ref: string; valid_until: string | null; currency: string; delivery_days: number | null; status: string; total: number; lines: { item: Ref; qty: number; unit_price: number }[] }
type PR = {
  id: string; number: string; status: string; source: string; ref: string; needed_by: string | null; notes: string
  requested_by: string; created_at: string; version: number; lines: PRLine[]
  quotes?: Quote[]; comparison?: { item: Ref; qty: number; offers: Offer[] }[]; orders?: { id: string; number: string; status: string }[]
}
type POLine = {
  id: string; item: Ref; qty_ordered: number; unit_price: number; line_total: number; qty_received: number; qty_awaiting: number
  qty_accepted: number; qty_rejected: number; qty_held: number; qty_returned: number; qty_outstanding: number
}
type PO = {
  id: string; number: string; status: string; supplier: PartyRef; currency: string; supplier_ref: string; pr_id: string | null
  link_ref: string; expected_ship: string | null; expected_arrival: string | null; terms: string; notes: string
  created_by: string; created_by_id: string; submitted_by: string | null; submitted_by_id: string | null; submitted_at: string | null
  decided_by: string | null; decided_at: string | null; decision_reason: string; total: number; created_at: string; version: number
  lines?: POLine[]; receipts?: { id: string; number: string; delivery_ref: string; created_at: string }[]
  returns?: { id: string; number: string; reason: string; created_at: string; replacement_expected: boolean }[]
}

const SOURCES: [string, string][] = [['shortage', 'Stock below minimum'], ['assembly', 'Assembly need'], ['project', 'Project demand'], ['other', 'Other']]
const fieldsOf = (err: unknown) => (err as { fields?: Record<string, string> }).fields ?? {}

function useSuppliers() {
  return useApi<Page<PartyRef & { currency: string }>>('/parties?kind=supplier&limit=200').data?.items ?? []
}

// ---------- purchase requests ----------

function RaisePR({ onDone, initial }: { onDone: (pr: PR) => void; initial?: { source: string; ref: string; rows: Row[] } }) {
  const items = useItems()
  const [rows, setRows] = useRows(initial?.rows)
  const action = useAction(() => {})
  const [fields, setFields] = useState<Record<string, string>>({})
  return (
    <form noValidate onSubmit={(e) => {
      e.preventDefault()
      const fd = new FormData(e.currentTarget)
      setFields({})
      void action.run('save', () => api<PR>('/purchase-requests', { method: 'POST', body: {
        source: fd.get('source'), ref: fd.get('ref'), needed_by: fd.get('needed_by') || null, notes: fd.get('notes'),
        lines: rows.filter((r) => r.item_id).map((r) => ({ item_id: r.item_id, qty: num(r.qty), notes: r.notes ?? '' })),
      } }).then(onDone).catch((err) => { setFields(fieldsOf(err)); throw err }))
    }}>
      <div className="grid-fields">
        <SelectField label="Why it is needed" name="source" defaultValue={initial?.source ?? 'shortage'} options={SOURCES} />
        <Field label="For" name="ref" defaultValue={initial?.ref} maxLength={120} hint="Project, customer or order it is for" />
        <Field label="Needed by" name="needed_by" type="date" />
      </div>
      <LineRows items={items} rows={rows} setRows={setRows} columns={[{ name: 'qty', label: 'Quantity', width: '120px' }]} />
      {fields.lines && <p className="field-error">{fields.lines}</p>}
      <TextareaField label="Notes" name="notes" maxLength={4000} />
      <FormError message={action.error} />
      <button className="btn btn-primary" disabled={!!action.busy}>{action.busy ? 'Raising…' : 'Raise purchase request'}</button>
    </form>
  )
}

export function PurchaseRequestsPage() {
  const can = useCan()
  const when = useWhen()
  const nav = useNavigate()
  const [status, setStatus] = useState('open')
  const [offset, setOffset] = useState(0)
  const [adding, setAdding] = useState(false)
  const { data, error, reload } = useApi<Page<PR>>(`/purchase-requests?${new URLSearchParams({ offset: String(offset), limit: '50', ...(status ? { status } : {}) })}`)
  return (
    <>
      <PageHead title="Purchase requests" intro="What needs buying and why. Record supplier quotations against a request, compare them, then order.">
        {can('purchasing.create') && <button className="btn btn-primary" onClick={() => setAdding(true)}>Raise request</button>}
      </PageHead>
      <div className="toolbar">
        <SelectField label="Status" value={status} onChange={(e) => { setStatus(e.target.value); setOffset(0) }} options={[['open', 'Open'], ['ordered', 'Ordered'], ['cancelled', 'Cancelled'], ['', 'All']]} />
      </div>
      {error ? <PageState kind="error" title="Couldn't load requests" message={error} />
        : !data ? <PageState kind="loading" title="Loading…" />
        : data.items.length === 0 ? <PageState kind="empty" title="No purchase requests here" message="Raise one, or start from Stock → Below minimum." />
        : (
          <div className="table-wrap">
            <table className="table stack">
              <thead><tr><th scope="col">Request</th><th scope="col">Items</th><th scope="col">Why</th><th scope="col">Needed by</th><th scope="col">Status</th></tr></thead>
              <tbody>
                {data.items.map((r) => (
                  <tr key={r.id}>
                    <td className="cell-main"><Link to={`/purchasing/requests/${r.id}`}>{r.number}</Link><span className="small muted">{r.requested_by}, {when(r.created_at)}</span></td>
                    <td className="cell-sub">{r.lines.map((l) => `${l.item.code} × ${qty(l.qty)}`).join(', ')}</td>
                    <td className="cell-sub">{SOURCES.find(([k]) => k === r.source)?.[1]}{r.ref && `: ${r.ref}`}</td>
                    <td className="cell-sub nowrap">{day(r.needed_by)}</td>
                    <td className="cell-tag"><Status s={r.status} /></td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      {data && <Pager offset={offset} limit={50} total={data.total} onChange={setOffset} />}
      {adding && <Dialog wide title="Raise purchase request" onClose={() => setAdding(false)}><RaisePR onDone={(pr) => nav(`/purchasing/requests/${pr.id}`)} /></Dialog>}
      {error && <button className="btn btn-secondary" onClick={reload}>Retry</button>}
    </>
  )
}

function RecordQuote({ pr, onDone }: { pr: PR; onDone: () => void }) {
  const suppliers = useSuppliers()
  const [rows, setRows] = useState<Row[]>(pr.lines.map((l) => ({ key: newKey(), item_id: l.item.id, qty: String(l.qty), unit_price: '', delivery_date: '' })))
  const items = pr.lines.map((l) => l.item)
  const action = useAction(onDone)
  const [fields, setFields] = useState<Record<string, string>>({})
  return (
    <form noValidate onSubmit={(e) => {
      e.preventDefault()
      const fd = new FormData(e.currentTarget)
      setFields({})
      void action.run('save', () => api('/supplier-quotes', { method: 'POST', body: {
        pr_id: pr.id, supplier_id: fd.get('supplier_id'), supplier_ref: fd.get('supplier_ref'), valid_until: fd.get('valid_until') || null,
        currency: fd.get('currency'), delivery_days: num(String(fd.get('delivery_days') ?? '')) ?? null, notes: fd.get('notes'),
        lines: rows.filter((r) => r.item_id).map((r) => ({ item_id: r.item_id, qty: num(r.qty), unit_price: num(r.unit_price), delivery_date: r.delivery_date || null })),
      } }).catch((err) => { setFields(fieldsOf(err)); throw err }))
    }}>
      <div className="grid-fields">
        <SelectField label="Supplier" name="supplier_id" required options={[['', 'Choose a supplier'], ...suppliers.map((s): [string, string] => [s.id, s.name])]} error={fields.supplier_id} />
        <Field label="Their quotation number" name="supplier_ref" maxLength={60} />
        <Field label="Currency" name="currency" defaultValue="AED" maxLength={3} pattern="[A-Z]{3}" error={fields.currency} />
        <Field label="Valid until" name="valid_until" type="date" />
        <Field label="Delivery (days)" name="delivery_days" type="number" min={0} />
      </div>
      <LineRows items={items} rows={rows} setRows={setRows} columns={[{ name: 'qty', label: 'Quantity', width: '100px' }, { name: 'unit_price', label: 'Unit price', width: '120px' }, { name: 'delivery_date', label: 'Delivery date', type: 'date', width: '150px' }]} />
      <TextareaField label="Notes" name="notes" maxLength={4000} hint="Specifications offered, conditions" />
      <FormError message={action.error} />
      <button className="btn btn-primary" disabled={!!action.busy}>Record quotation</button>
    </form>
  )
}

export function PurchaseRequestPage() {
  const { id } = useParams()
  const can = useCan()
  const when = useWhen()
  const nav = useNavigate()
  const { data: pr, error, reload } = useApi<PR>(`/purchase-requests/${id}`)
  const [quoting, setQuoting] = useState(false)
  const action = useAction(reload)
  if (error) return <PageState kind="error" title="Couldn't load the request" message={error} />
  if (!pr) return <PageState kind="loading" title="Loading…" />
  const open = pr.status === 'open'
  return (
    <>
      <Back to="/purchasing/requests">Purchase requests</Back>
      <PageHead title={pr.number} intro={`${SOURCES.find(([k]) => k === pr.source)?.[1]}${pr.ref ? `: ${pr.ref}` : ''}`}>
        {open && can('purchasing.create') && <>
          <button className="btn btn-secondary" onClick={() => action.run('cancel', () => api(`/purchase-requests/${pr.id}/cancel`, { method: 'POST', body: { version: pr.version } }), `Cancel ${pr.number}?`)}>Cancel request</button>
          <button className="btn btn-primary" onClick={() => setQuoting(true)}>Record quotation</button>
        </>}
      </PageHead>
      <Facts rows={[['Status', <Status s={pr.status} />], ['Requested by', pr.requested_by], ['Raised', when(pr.created_at)], ['Needed by', day(pr.needed_by)]]} />
      <FormError message={action.error} />
      <h2 className="section">Quotations compared</h2>
      {pr.comparison?.every((c) => c.offers.length === 0) ? <p className="empty-line">No quotations yet. Record each supplier's offer to compare them here.</p> : pr.comparison?.map((c) => (
        <div key={c.item.id} className="table-wrap compare">
          <table className="table">
            <caption><code>{c.item.code}</code> {c.item.name}, {qty(c.qty, c.item.unit_code)} requested</caption>
            <thead><tr><th scope="col">Supplier</th><th scope="col" className="num">Quantity</th><th scope="col" className="num">Unit price</th><th scope="col" className="num">Total</th><th scope="col">Delivery</th><th scope="col">Notes</th></tr></thead>
            <tbody>
              {c.offers.map((o) => (
                <tr key={o.quote_id}>
                  <td>{o.supplier}<span className="small muted">{o.quote} · <Status s={o.status} /></span></td>
                  <td className="num">{qty(o.qty)}</td>
                  <td className="num">{money(o.unit_price, o.currency)}{o.lowest_price && <span className="small best">Lowest in {o.currency}</span>}</td>
                  <td className="num">{money(o.total, o.currency)}</td>
                  <td className="nowrap">{o.delivery_date ? day(o.delivery_date) : o.delivery_days != null ? `${o.delivery_days} days` : '—'}{o.earliest && <span className="small best">Earliest</span>}</td>
                  <td className="small">{o.spec_notes}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      ))}
      {!!pr.quotes?.length && (
        <>
          <h2 className="section">Quotations</h2>
          <ul className="feed panel panel-wide">
            {pr.quotes.map((q) => (
              <li key={q.id}>
                <span><strong>{q.supplier.name}</strong><span className="small muted">{q.number}{q.supplier_ref && ` · their ref ${q.supplier_ref}`}{q.valid_until && ` · valid until ${day(q.valid_until)}`}</span></span>
                <span className="row-end">{money(q.total, q.currency)} <Status s={q.status} />
                  {q.status === 'open' && open && can('purchasing.create') && (
                    <button className="btn btn-secondary" onClick={() => action.run(q.id, async () => { const po = await api<PO>(`/supplier-quotes/${q.id}/order`, { method: 'POST' }); nav(`/purchasing/orders/${po.id}`) })}>Order from this</button>
                  )}</span>
              </li>
            ))}
          </ul>
        </>
      )}
      {!!pr.orders?.length && <p>Ordered on {pr.orders.map((o, i) => <span key={o.id}>{i > 0 && ', '}<Link to={`/purchasing/orders/${o.id}`}>{o.number}</Link></span>)}.</p>}
      {quoting && <Dialog wide title={`Quotation for ${pr.number}`} onClose={() => setQuoting(false)}><RecordQuote pr={pr} onDone={() => { setQuoting(false); reload() }} /></Dialog>}
    </>
  )
}

// ---------- purchase orders ----------

function POForm({ po, onDone }: { po: PO | null; onDone: (po: PO) => void }) {
  const suppliers = useSuppliers()
  const items = useItems()
  const [rows, setRows] = useRows(po?.lines?.map((l) => ({ key: newKey(), item_id: l.item.id, qty: String(l.qty_ordered), unit_price: String(l.unit_price) })))
  const action = useAction(() => {})
  const [fields, setFields] = useState<Record<string, string>>({})
  return (
    <form noValidate onSubmit={(e) => {
      e.preventDefault()
      const fd = new FormData(e.currentTarget)
      const body = {
        supplier_id: fd.get('supplier_id'), currency: fd.get('currency'), supplier_ref: fd.get('supplier_ref'), link_ref: fd.get('link_ref'),
        expected_ship: fd.get('expected_ship') || null, expected_arrival: fd.get('expected_arrival') || null, terms: fd.get('terms'), notes: fd.get('notes'),
        pr_id: po?.pr_id ?? null, lines: rows.filter((r) => r.item_id).map((r) => ({ item_id: r.item_id, qty: num(r.qty), unit_price: num(r.unit_price) ?? 0 })),
      }
      setFields({})
      void action.run('save', () => api<PO>(po ? `/purchase-orders/${po.id}` : '/purchase-orders', { method: po ? 'PUT' : 'POST', body: po ? { ...body, version: po.version } : body })
        .then(onDone).catch((err) => { setFields(fieldsOf(err)); throw err }))
    }}>
      <div className="grid-fields">
        <SelectField label="Supplier" name="supplier_id" defaultValue={po?.supplier.id ?? ''} options={[['', 'Choose a supplier'], ...suppliers.map((s): [string, string] => [s.id, s.name])]} error={fields.supplier_id} />
        <Field label="Currency" name="currency" defaultValue={po?.currency ?? 'AED'} maxLength={3} pattern="[A-Z]{3}" error={fields.currency} />
        <Field label="Supplier reference" name="supplier_ref" defaultValue={po?.supplier_ref} maxLength={60} hint="Their quotation or order confirmation number" />
        <Field label="For" name="link_ref" defaultValue={po?.link_ref} maxLength={120} hint="Project, stock need or assembly order" />
        <Field label="Expected shipment" name="expected_ship" type="date" defaultValue={po?.expected_ship ?? ''} />
        <Field label="Expected arrival" name="expected_arrival" type="date" defaultValue={po?.expected_arrival ?? ''} />
      </div>
      <LineRows items={items} rows={rows} setRows={setRows} columns={[{ name: 'qty', label: 'Quantity', width: '110px' }, { name: 'unit_price', label: 'Unit price', width: '130px' }]} />
      {fields.lines && <p className="field-error">{fields.lines}</p>}
      <TextareaField label="Terms" name="terms" defaultValue={po?.terms} maxLength={4000} hint="Leave empty to use the supplier's terms" />
      <TextareaField label="Notes" name="notes" defaultValue={po?.notes} maxLength={4000} />
      <FormError message={action.error} />
      <button className="btn btn-primary" disabled={!!action.busy}>{po ? 'Save changes' : 'Create draft'}</button>
    </form>
  )
}

const PO_CARDS: [string, string][] = [['draft', 'Drafts'], ['submitted', 'Waiting for approval'], ['approved', 'Approved, not delivered'], ['partly_received', 'Partly received'], ['received', 'Received']]

export function PurchaseOrdersPage() {
  const can = useCan()
  const nav = useNavigate()
  const [status, setStatus] = useState('')
  const [q, setQ] = useState('')
  const [offset, setOffset] = useState(0)
  const [adding, setAdding] = useState(false)
  const { data, error } = useApi<Page<PO>>(`/purchase-orders?${new URLSearchParams({ q, offset: String(offset), limit: '50', ...(status ? { status } : {}) })}`)
  return (
    <>
      <PageHead title="Purchase orders" intro="Draft, submit for approval, receive. Quantities ordered, received, accepted, rejected, returned and still outstanding are kept apart.">
        {can('purchasing.create') && <button className="btn btn-primary" onClick={() => setAdding(true)}>New purchase order</button>}
      </PageHead>
      {data?.counts && (
        <div className="counts" role="group" aria-label="Show orders by status">
          {PO_CARDS.map(([s, label]) => (
            <button key={s} className="count" aria-pressed={status === s} onClick={() => { setStatus(status === s ? '' : s); setOffset(0) }}>
              <span>{label}</span><strong>{data.counts?.[s] ?? 0}</strong>
            </button>
          ))}
        </div>
      )}
      <div className="toolbar" role="search"><Field label="Search number, supplier or reference" type="search" value={q} onChange={(e) => { setQ(e.target.value); setOffset(0) }} /></div>
      {error ? <PageState kind="error" title="Couldn't load orders" message={error} />
        : !data ? <PageState kind="loading" title="Loading…" />
        : data.items.length === 0 ? <PageState kind="empty" title="No purchase orders here" message={status || q ? 'Clear the filter to see all orders.' : 'Create one, or order from a supplier quotation.'} />
        : (
          <div className="table-wrap">
            <table className="table stack">
              <thead><tr><th scope="col">Order</th><th scope="col">Supplier</th><th scope="col" className="num">Total</th><th scope="col">Expected</th><th scope="col">Status</th></tr></thead>
              <tbody>
                {data.items.map((o) => (
                  <tr key={o.id}>
                    <td className="cell-main"><Link to={`/purchasing/orders/${o.id}`}>{o.number}</Link>{o.supplier_ref && <span className="small muted">Ref {o.supplier_ref}</span>}</td>
                    <td className="cell-sub">{o.supplier.name}</td>
                    <td className="num cell-sub">{money(o.total, o.currency)}</td>
                    <td className="cell-sub nowrap">{day(o.expected_arrival)}</td>
                    <td className="cell-tag"><Status s={o.status} /></td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      {data && <Pager offset={offset} limit={50} total={data.total} onChange={setOffset} />}
      {adding && <Dialog wide title="New purchase order" onClose={() => setAdding(false)}><POForm po={null} onDone={(po) => nav(`/purchasing/orders/${po.id}`)} /></Dialog>}
    </>
  )
}

function Receive({ po, onDone }: { po: PO; onDone: () => void }) {
  const can = useCan()
  const { warehouses } = useWarehouses()
  const open = po.lines!.filter((l) => l.qty_outstanding > 0)
  const [rows, setRows] = useState(open.map((l) => ({ id: l.id, qty: String(l.qty_outstanding), lot: '', serials: '' })))
  const [acceptAll, setAcceptAll] = useState(false)
  const [key] = useState(newKey)
  const action = useAction(onDone)
  const set = (i: number, k: string, v: string) => setRows(rows.map((r, j) => (j === i ? { ...r, [k]: v } : r)))
  return (
    <form noValidate onSubmit={(e) => {
      e.preventDefault()
      const fd = new FormData(e.currentTarget)
      const lines = rows.map((r, i) => ({ r, l: open[i] })).filter(({ r }) => num(r.qty)).map(({ r, l }) => {
        const serials = serialList(r.serials)
        const line: Record<string, unknown> = { po_line_id: r.id, qty: num(r.qty), lot: r.lot }
        if (l.item.tracking === 'serial') line.units = serials.map((s) => ({ serial: s }))
        if (acceptAll) Object.assign(line, l.item.tracking === 'serial' ? { accept_serials: serials } : { accept: num(r.qty) })
        return line
      })
      void action.run('save', () => api(`/purchase-orders/${po.id}/receipts`, { method: 'POST', body: { key, warehouse_id: fd.get('warehouse_id'), delivery_ref: fd.get('delivery_ref'), notes: fd.get('notes'), lines } }))
    }}>
      <div className="grid-fields">
        <SelectField label="Warehouse" name="warehouse_id" options={warehouses.filter((w) => w.active).map((w) => [w.id, `${w.code} · ${w.name}`])} />
        <Field label="Delivery note number" name="delivery_ref" maxLength={60} />
      </div>
      <div className="table-wrap">
        <table className="table">
          <thead><tr><th scope="col">Item</th><th scope="col" className="num">Outstanding</th><th scope="col">Delivered now</th><th scope="col">Lot / serial numbers</th></tr></thead>
          <tbody>
            {open.map((l, i) => (
              <tr key={l.id}>
                <td><code>{l.item.code}</code><span className="small">{l.item.name}</span></td>
                <td className="num">{qty(l.qty_outstanding, l.item.unit_code)}</td>
                <td><input aria-label={`${l.item.code} delivered`} type="number" min={0} step="any" value={rows[i].qty} onChange={(e) => set(i, 'qty', e.target.value)} /></td>
                <td>
                  {l.item.tracking === 'lot' && <input aria-label={`${l.item.code} lot`} placeholder="Lot number" value={rows[i].lot} onChange={(e) => set(i, 'lot', e.target.value)} />}
                  {l.item.tracking === 'serial' && <textarea aria-label={`${l.item.code} serial numbers`} rows={2} placeholder="Scan or type, one per line" value={rows[i].serials} onChange={(e) => set(i, 'serials', e.target.value)} />}
                  {l.item.tracking === 'none' && <span className="muted">—</span>}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      {can('quality.create')
        ? <label className="check"><input type="checkbox" checked={acceptAll} onChange={(e) => setAcceptAll(e.target.checked)} /> <span>Inspected and accepted on arrival<span className="hint">Otherwise everything waits in receiving inspection for a quality decision.</span></span></label>
        : <p className="hint">Everything goes to receiving inspection; quality decides what is accepted.</p>}
      <TextareaField label="Notes" name="notes" maxLength={4000} />
      <FormError message={action.error} />
      <button className="btn btn-primary" disabled={!!action.busy}>{action.busy ? 'Recording…' : 'Record delivery'}</button>
    </form>
  )
}

function Decide({ po, line, onDone }: { po: PO; line: POLine; onDone: () => void }) {
  const { warehouses } = useWarehouses()
  const [source, setSource] = useState(line.qty_awaiting > 0 ? 'awaiting' : 'held')
  const [key] = useState(newKey)
  const serial = line.item.tracking === 'serial'
  const pool = useApi<Page<{ serial: string; location: string }>>(serial ? `/serials?item_id=${line.item.id}&status=${source === 'awaiting' ? 'awaiting_inspection' : 'on_hold'}&limit=200` : null)
  const action = useAction(onDone)
  return (
    <form noValidate onSubmit={(e) => {
      e.preventDefault()
      const fd = new FormData(e.currentTarget)
      const s = (n: string) => String(fd.get(n) ?? '')
      const body: Record<string, unknown> = { key, warehouse_id: s('warehouse_id'), source, lot: s('lot'), notes: s('notes'), accept_location_id: s('accept_location_id') || null }
      Object.assign(body, serial
        ? { accept_serials: serialList(s('accept')), reject_serials: serialList(s('reject')), hold_serials: source === 'held' ? [] : serialList(s('hold')) }
        : { accept: num(s('accept')) ?? 0, reject: num(s('reject')) ?? 0, hold: source === 'held' ? 0 : num(s('hold')) ?? 0 })
      void action.run('save', () => api(`/purchase-orders/${po.id}/lines/${line.id}/decide`, { method: 'POST', body }))
    }}>
      <p><code>{line.item.code}</code> {line.item.name}: {qty(line.qty_awaiting)} awaiting inspection, {qty(line.qty_held)} on hold.</p>
      <div className="grid-fields">
        <SelectField label="Decide on" value={source} onChange={(e) => setSource(e.target.value)} options={[['awaiting', 'Goods awaiting inspection'], ['held', 'Goods on hold']]} />
        <SelectField label="Warehouse" name="warehouse_id" options={warehouses.filter((w) => w.active).map((w) => [w.id, `${w.code} · ${w.name}`])} />
        {line.item.tracking === 'lot' && <Field label="Lot" name="lot" required />}
        <SelectField label="Accepted goods go to" name="accept_location_id" options={[['', 'Main store'], ...warehouses.flatMap((w) => w.locations.filter((l) => l.purpose === 'available' && l.active).map((l): [string, string] => [l.id, `${l.label} · ${l.name}`]))]} />
      </div>
      {serial && pool.data && <p className="hint">Waiting: {pool.data.items.map((x) => x.serial).join(', ') || 'none'}</p>}
      <div className="grid-fields">
        {serial ? <>
          <TextareaField label="Accepted serial numbers" name="accept" rows={2} />
          <TextareaField label="Rejected serial numbers" name="reject" rows={2} />
          {source === 'awaiting' && <TextareaField label="Held serial numbers" name="hold" rows={2} />}
        </> : <>
          <Field label="Accepted" name="accept" type="number" min={0} step="any" />
          <Field label="Rejected" name="reject" type="number" min={0} step="any" />
          {source === 'awaiting' && <Field label="Held (quarantine)" name="hold" type="number" min={0} step="any" />}
        </>}
      </div>
      <TextareaField label="Inspection notes" name="notes" maxLength={500} />
      <FormError message={action.error} />
      <button className="btn btn-primary" disabled={!!action.busy}>Record decision</button>
    </form>
  )
}

function ReturnGoods({ po, onDone }: { po: PO; onDone: () => void }) {
  const { warehouses } = useWarehouses()
  const lines = po.lines!.filter((l) => l.qty_rejected > l.qty_returned)
  const [rows, setRows] = useState(lines.map((l) => ({ qty: String(l.qty_rejected - l.qty_returned), serials: '' })))
  const [key] = useState(newKey)
  const action = useAction(onDone)
  return (
    <form noValidate onSubmit={(e) => {
      e.preventDefault()
      const fd = new FormData(e.currentTarget)
      void action.run('save', () => api(`/purchase-orders/${po.id}/returns`, { method: 'POST', body: {
        key, warehouse_id: fd.get('warehouse_id'), reason: fd.get('reason'), replacement_expected: fd.get('replacement') === 'on',
        lines: lines.map((l, i) => ({ po_line_id: l.id, qty: num(rows[i].qty), serials: serialList(rows[i].serials) })).filter((x) => x.qty),
      } }))
    }}>
      <SelectField label="From the rejected goods of" name="warehouse_id" options={warehouses.filter((w) => w.active).map((w) => [w.id, `${w.code} · ${w.name}`])} />
      {lines.map((l, i) => (
        <div className="line" key={l.id}>
          <span className="line-item"><code>{l.item.code}</code> {qty(l.qty_rejected - l.qty_returned)} rejected, not yet returned</span>
          <label style={{ inlineSize: 110 }}><span className="line-label">Return</span><input type="number" min={0} step="any" value={rows[i].qty} onChange={(e) => setRows(rows.map((r, j) => (j === i ? { ...r, qty: e.target.value } : r)))} /></label>
          {l.item.tracking === 'serial' && <label className="line-item"><span className="line-label">Serial numbers</span><textarea rows={1} value={rows[i].serials} onChange={(e) => setRows(rows.map((r, j) => (j === i ? { ...r, serials: e.target.value } : r)))} /></label>}
        </div>
      ))}
      <TextareaField label="Reason" name="reason" required minLength={3} maxLength={500} />
      <label className="check"><input type="checkbox" name="replacement" defaultChecked /> <span>The supplier will replace them<span className="hint">They stay outstanding on the order until the replacement arrives.</span></span></label>
      <FormError message={action.error} />
      <button className="btn btn-primary" disabled={!!action.busy}>Record return</button>
    </form>
  )
}

function Reasoned({ label, button, danger, required, onSubmit }: { label: string; button: string; danger?: boolean; required?: boolean; onSubmit: (reason: string) => Promise<unknown> }) {
  const action = useAction(() => {})
  return (
    <form noValidate onSubmit={(e) => { e.preventDefault(); const reason = String(new FormData(e.currentTarget).get('reason') ?? ''); void action.run('go', () => onSubmit(reason)) }}>
      <TextareaField label={label} name="reason" required={required} maxLength={500} hint="Kept in the audit log." />
      <FormError message={action.error} />
      <button className={danger ? 'btn btn-danger' : 'btn btn-primary'} disabled={!!action.busy}>{button}</button>
    </form>
  )
}

export function PurchaseOrderPage() {
  const { id } = useParams()
  const can = useCan()
  const me = useMe()
  const when = useWhen()
  const { data: po, error, reload } = useApi<PO>(`/purchase-orders/${id}`)
  const [dialog, setDialog] = useState<string | POLine | null>(null)
  const action = useAction(reload)
  if (error) return <PageState kind="error" title="Couldn't load the order" message={error} />
  if (!po) return <PageState kind="loading" title="Loading…" />
  const done = () => { setDialog(null); reload() }
  const post = (path: string, body: object = {}) => api(`/purchase-orders/${po.id}/${path}`, { method: 'POST', body: { version: po.version, ...body } }).then(done)
  const mine = [po.created_by_id, po.submitted_by_id].includes(me.user.id)
  const receivable = ['approved', 'partly_received'].includes(po.status)
  const toDecide = po.lines!.filter((l) => l.qty_awaiting > 0 || l.qty_held > 0)
  return (
    <>
      <Back to="/purchasing/orders">Purchase orders</Back>
      <PageHead title={po.number} intro={`${po.supplier.name}${po.link_ref ? ` · for ${po.link_ref}` : ''}`}>
        {can('purchasing.create') && ['draft', 'rejected'].includes(po.status) && <button className="btn btn-secondary" onClick={() => setDialog('edit')}>Edit</button>}
        {can('purchasing.create') && po.status === 'draft' && <button className="btn btn-primary" onClick={() => action.run('submit', () => post('submit'))}>Submit for approval</button>}
        {can('purchasing.approve') && po.status === 'submitted' && !mine && <>
          <button className="btn btn-secondary" onClick={() => setDialog('reject')}>Reject</button>
          <button className="btn btn-primary" onClick={() => setDialog('approve')}>Approve</button>
        </>}
        {can('receipts.create') && receivable && <button className="btn btn-primary" onClick={() => setDialog('receive')}>Record delivery</button>}
      </PageHead>
      {po.status === 'submitted' && mine && can('purchasing.approve') && <p className="notice notice-warning">You raised or submitted this order, so someone else approves it.</p>}
      {po.status === 'rejected' && <p className="notice notice-warning">Rejected by {po.decided_by}: {po.decision_reason}. Edit it and submit again, or cancel it.</p>}
      <Facts rows={[
        ['Status', <Status s={po.status} />], ['Total', money(po.total, po.currency)], ['Expected arrival', day(po.expected_arrival)],
        ['Raised', `${po.created_by}, ${when(po.created_at)}`],
        ['Approval', po.decided_at ? `${po.decided_by ?? 'Automatic'}, ${when(po.decided_at)}` : po.submitted_at ? `Waiting since ${when(po.submitted_at)}` : 'Not submitted'],
      ]} />
      <FormError message={action.error} />
      <div className="table-wrap">
        <table className="table">
          <thead><tr><th scope="col">Item</th><th scope="col" className="num">Ordered</th><th scope="col" className="num">Unit price</th><th scope="col" className="num">Received</th><th scope="col" className="num">Awaiting inspection</th><th scope="col" className="num">Accepted</th><th scope="col" className="num">Rejected</th><th scope="col" className="num">On hold</th><th scope="col" className="num">Returned</th><th scope="col" className="num">Outstanding</th><th scope="col"><span className="sr-only">Actions</span></th></tr></thead>
          <tbody>
            {po.lines!.map((l) => (
              <tr key={l.id}>
                <td><code>{l.item.code}</code><span className="small">{l.item.name}</span></td>
                <td className="num">{qty(l.qty_ordered, l.item.unit_code)}</td>
                <td className="num">{money(l.unit_price, po.currency)}</td>
                <td className="num">{qty(l.qty_received)}</td>
                <td className="num">{qty(l.qty_awaiting)}</td>
                <td className="num">{qty(l.qty_accepted)}</td>
                <td className="num">{qty(l.qty_rejected)}</td>
                <td className="num">{qty(l.qty_held)}</td>
                <td className="num">{qty(l.qty_returned)}</td>
                <td className="num"><strong>{qty(l.qty_outstanding)}</strong></td>
                <td className="actions">{can('quality.create') && (l.qty_awaiting > 0 || l.qty_held > 0) && <button className="link-btn" onClick={() => setDialog(l)}>Decide<span className="sr-only"> on {l.item.code}</span></button>}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      {toDecide.length > 0 && !can('quality.create') && <p className="hint">Goods waiting for a quality decision: {toDecide.map((l) => l.item.code).join(', ')}.</p>}
      <div className="row">
        {can('purchasing.create') && po.lines!.some((l) => l.qty_rejected > l.qty_returned) && <button className="btn btn-secondary" onClick={() => setDialog('return')}>Return rejected goods</button>}
        {can('purchasing.create') && ['draft', 'submitted', 'approved', 'rejected'].includes(po.status) && <button className="btn btn-ghost" onClick={() => setDialog('cancel')}>Cancel order</button>}
        {can('purchasing.create') && po.status === 'partly_received' && <button className="btn btn-ghost" onClick={() => setDialog('close')}>Close short</button>}
      </div>
      {(po.receipts?.length || po.returns?.length) ? (
        <>
          <h2 className="section">Deliveries and returns</h2>
          <ul className="feed panel panel-wide">
            {po.receipts!.map((r) => <li key={r.id}><span><strong>{r.number}</strong><span className="small muted">Delivery {r.delivery_ref || 'without a note number'}</span></span><span className="stamp">{when(r.created_at)}</span></li>)}
            {po.returns!.map((r) => <li key={r.id}><span><strong>{r.number}</strong><span className="small muted">Returned: {r.reason}{r.replacement_expected ? ' · replacement expected' : ''}</span></span><span className="stamp">{when(r.created_at)}</span></li>)}
          </ul>
        </>
      ) : null}
      {(po.terms || po.notes) && <section className="panel panel-wide">{po.terms && <><h2>Terms</h2><p className="prewrap">{po.terms}</p></>}{po.notes && <><h2>Notes</h2><p className="prewrap">{po.notes}</p></>}</section>}
      {dialog === 'edit' && <Dialog wide title={`Edit ${po.number}`} onClose={() => setDialog(null)}><POForm po={po} onDone={done} /></Dialog>}
      {dialog === 'approve' && <Dialog title={`Approve ${po.number}`} onClose={() => setDialog(null)}><Reasoned label="Comment (optional)" button="Approve order" onSubmit={(reason) => post('approve', { reason })} /></Dialog>}
      {dialog === 'reject' && <Dialog title={`Reject ${po.number}`} onClose={() => setDialog(null)}><Reasoned label="Why it is rejected" required danger button="Reject order" onSubmit={(reason) => post('reject', { reason })} /></Dialog>}
      {dialog === 'cancel' && <Dialog title={`Cancel ${po.number}`} onClose={() => setDialog(null)}><Reasoned label="Why it is cancelled" required danger button="Cancel order" onSubmit={(reason) => post('cancel', { reason })} /></Dialog>}
      {dialog === 'close' && <Dialog title={`Close ${po.number} short`} onClose={() => setDialog(null)}><Reasoned label="Why the rest won't come" required button="Close order" onSubmit={(reason) => post('close', { reason })} /></Dialog>}
      {dialog === 'receive' && <Dialog wide title={`Delivery against ${po.number}`} onClose={() => setDialog(null)}><Receive po={po} onDone={done} /></Dialog>}
      {dialog === 'return' && <Dialog wide title={`Return to ${po.supplier.name}`} onClose={() => setDialog(null)}><ReturnGoods po={po} onDone={done} /></Dialog>}
      {dialog && typeof dialog === 'object' && <Dialog wide title={`Inspection decision: ${dialog.item.code}`} onClose={() => setDialog(null)}><Decide po={po} line={dialog} onDone={done} /></Dialog>}
    </>
  )
}

export { RaisePR }
