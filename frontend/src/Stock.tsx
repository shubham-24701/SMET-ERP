import { Lifecycle } from './Installation'
import { useState } from 'react'
import { Link, useNavigate, useParams } from 'react-router'
import { api, download } from './api'
import { useAction, useApi, useCan, useMe, useWhen } from './lib'
import { RaisePR } from './Purchasing'
import {
  Back, Facts, ItemOptions, KIND, LocationOptions, PURPOSE, Status, newKey, num, qty, serialList, useItems, useWarehouses,
  type Page, type Ref, type Row,
} from './ops'
import { Dialog, Field, FormError, PageHead, PageState, Pager, SelectField, TextareaField } from './ui'

type Summary = Ref & {
  kind: string; min_stock: number; suggested: number; on_hand: number; reserved: number; free: number; on_order: number; low: boolean
  available: number; inspection: number; quarantine: number; rejected: number; assembly: number; repair: number; returns: number
}
type Balance = { item: Ref; location_id: string; location: string; location_name: string; purpose: string; lot: string; qty: number }
type Move = { id: number; at: string; item: Ref; qty: number; from: string | null; to: string | null; lot: string; serial: string | null; kind: string; doc_type: string; doc_number: string; user: string; reason: string }
type SerialRow = { id: string; serial: string; item: Ref; supplier_serial: string | null; lot: string; status: string; location: string | null; purpose: string | null; created_at: string }
type SerialRecord = SerialRow & {
  purchase_order: { id: string; number: string } | null; moves: Move[]; built_into: { id: string; serial: string } | null
  components: { item: Ref; serial: string | null; serial_id: string | null; lot: string; qty: number }[]
  inspections: { id: string; at: string; result: string; observations: string; inspector: string
    readings: { name: string; unit: string; value: string | null; min: string | null; max: string | null; limits_confirmed: boolean; within: boolean | null }[]
    checklist: { text: string; required: boolean; done: boolean }[] }[]
}
type Reservation = { id: string; item: Ref; qty: number; qty_used: number; remaining: number; ref_type: string; ref_label: string; status: string; reason: string; created_at: string; version: number }
type AdjLine = { id: string; item: Ref; location: string; lot: string; qty_change: number | null; counted: number | null; serials_in: string[]; serials_out: string[]; serials_found: string[] | null }
type Adjustment = { id: string; number: string; status: string; reason: string; count_id: string | null; requested_by: string; requested_by_id: string; decided_by: string | null; decided_at: string | null; decision_reason: string; created_at: string; version: number; lines?: AdjLine[] }
type Count = { id: string; number: string; location: string; purpose: string; status: string; notes: string; adjustment_id: string | null; created_at: string; version: number
  lines?: { id: string; item: Ref; lot: string; expected: number; counted: number | null; serials_found: string[] | null; serials_expected: string[] | null }[] }

const MOVE_KIND: Record<string, string> = {
  receipt: 'Received', accept: 'Accepted', reject: 'Rejected', hold: 'Held', transfer: 'Transfer', issue: 'Issued to assembly',
  return: 'Returned from assembly', consume: 'Used in a battery', build: 'Battery built', passed: 'Passed inspection',
  on_hold: 'Put on hold', rework: 'Sent to rework', adjust_in: 'Correction +', adjust_out: 'Correction −', supplier_return: 'Returned to supplier',
}

// ---------- stock on hand ----------

function Where({ item }: { item: Summary }) {
  const { data } = useApi<Page<Balance>>(`/stock/balances?item_id=${item.id}`)
  if (!data) return <PageState kind="loading" title="Loading…" />
  if (!data.items.length) return <p className="empty-line">None in stock.</p>
  return (
    <table className="table">
      <thead><tr><th scope="col">Location</th><th scope="col">Status</th><th scope="col">Lot</th><th scope="col" className="num">Quantity</th></tr></thead>
      <tbody>{data.items.map((b) => <tr key={`${b.location_id}${b.lot}`}><td><code>{b.location}</code> <span className="small">{b.location_name}</span></td><td>{PURPOSE[b.purpose]}</td><td>{b.lot || '—'}</td><td className="num">{qty(b.qty, b.item.unit_code)}</td></tr>)}</tbody>
    </table>
  )
}

export function Transfer({ item, onDone }: { item?: Ref; onDone: () => void }) {
  const items = useItems()
  const { warehouses } = useWarehouses()
  const [itemId, setItemId] = useState(item?.id ?? '')
  const chosen = items.find((i) => i.id === itemId)
  const [key] = useState(newKey)
  const action = useAction(onDone)
  return (
    <form noValidate onSubmit={(e) => {
      e.preventDefault()
      const fd = new FormData(e.currentTarget)
      const s = (n: string) => String(fd.get(n) ?? '')
      void action.run('go', () => api('/stock/transfers', { method: 'POST', body: { key, item_id: itemId, from_location_id: s('from'), to_location_id: s('to'), qty: num(s('qty')), lot: s('lot'), serials: serialList(s('serials')), reason: s('reason') } }))
    }}>
      <div className="field"><label htmlFor="tr-item">Item</label><select id="tr-item" value={itemId} onChange={(e) => setItemId(e.target.value)} required><option value="">Choose an item</option><ItemOptions items={items} /></select></div>
      <div className="grid-fields">
        <div className="field"><label htmlFor="tr-from">From</label><select id="tr-from" name="from" required><option value="">Choose</option><LocationOptions warehouses={warehouses} /></select></div>
        <div className="field"><label htmlFor="tr-to">To</label><select id="tr-to" name="to" required><option value="">Choose</option><LocationOptions warehouses={warehouses} /></select></div>
        <Field label="Quantity" name="qty" type="number" min={0} step="any" required />
        {chosen?.tracking === 'lot' && <Field label="Lot" name="lot" required />}
      </div>
      {chosen?.tracking === 'serial' && <TextareaField label="Serial numbers" name="serials" rows={2} hint="One per unit moved" />}
      <p className="hint">Transfers stay within one use, for example store to store. Accepting, releasing from hold and passing inspection have their own steps.</p>
      <Field label="Reason" name="reason" maxLength={300} />
      <FormError message={action.error} />
      <button className="btn btn-primary" disabled={!!action.busy}>Move stock</button>
    </form>
  )
}

export function StockPage() {
  const can = useCan()
  const nav = useNavigate()
  const [q, setQ] = useState('')
  const [kind, setKind] = useState('')
  const [low, setLow] = useState(false)
  const [offset, setOffset] = useState(0)
  const [dialog, setDialog] = useState<'transfer' | 'pr' | Summary | null>(null)
  const params = new URLSearchParams({ q, offset: String(offset), limit: '50', ...(kind ? { kind } : {}), ...(low ? { low: 'true' } : {}) })
  const { data, error, reload } = useApi<Page<Summary>>(`/stock/summary?${params}`)
  const lowRows: Row[] = (data?.items ?? []).filter((r) => r.low && r.suggested > 0).map((r) => ({ key: newKey(), item_id: r.id, qty: String(r.suggested) }))
  return (
    <>
      <PageHead title="Stock" intro="Available means in an available-stock location; free is what is left after reservations. Awaiting inspection, on hold and under assembly are not available.">
        {can('stock.create') && <button className="btn btn-secondary" onClick={() => setDialog('transfer')}>Move stock</button>}
        {low && can('purchasing.create') && lowRows.length > 0 && <button className="btn btn-primary" onClick={() => setDialog('pr')}>Request purchase of these</button>}
      </PageHead>
      <div className="toolbar" role="search">
        <Field label="Search code or name" type="search" value={q} onChange={(e) => { setQ(e.target.value); setOffset(0) }} />
        <SelectField label="Kind" value={kind} onChange={(e) => { setKind(e.target.value); setOffset(0) }} options={[['', 'All kinds'], ...Object.entries(KIND)]} />
        <label className="check toolbar-check"><input type="checkbox" checked={low} onChange={(e) => { setLow(e.target.checked); setOffset(0) }} /> <span>Below minimum only</span></label>
      </div>
      {error ? <PageState kind="error" title="Couldn't load stock" message={error} action={<button className="btn btn-secondary" onClick={reload}>Retry</button>} />
        : !data ? <PageState kind="loading" title="Loading stock…" />
        : data.items.length === 0 ? <PageState kind="empty" title={low ? 'Nothing is below its minimum' : 'No items yet'} message={low ? 'Items with a minimum stock show here when free stock plus what is on order falls below it.' : 'Add items under Master data → Items.'} />
        : (
          <div className="table-wrap">
            <table className="table">
              <thead><tr><th scope="col">Item</th><th scope="col" className="num">Available</th><th scope="col" className="num">Reserved</th><th scope="col" className="num">Free</th><th scope="col" className="num">Awaiting inspection</th><th scope="col" className="num">On hold</th><th scope="col" className="num">Under assembly</th><th scope="col" className="num">On order</th><th scope="col" className="num">Minimum</th></tr></thead>
              <tbody>
                {data.items.map((s) => (
                  <tr key={s.id}>
                    <td><button className="link-btn" onClick={() => setDialog(s)}><code>{s.code}</code></button><span className="small">{s.name}</span></td>
                    <td className="num">{qty(s.available, s.unit_code)}</td>
                    <td className="num">{qty(s.reserved)}</td>
                    <td className="num"><strong>{qty(s.free)}</strong></td>
                    <td className="num">{qty(s.inspection)}</td>
                    <td className="num">{qty(s.quarantine)}</td>
                    <td className="num">{qty(s.assembly)}</td>
                    <td className="num">{qty(s.on_order)}</td>
                    <td className="num">{s.min_stock ? qty(s.min_stock) : '—'}{s.low && <span className="small low">Low: order {qty(s.suggested)}</span>}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      {data && <Pager offset={offset} limit={50} total={data.total} onChange={setOffset} />}
      {dialog === 'transfer' && <Dialog title="Move stock" onClose={() => setDialog(null)}><Transfer onDone={() => { setDialog(null); reload() }} /></Dialog>}
      {dialog === 'pr' && <Dialog wide title="Request purchase of low stock" onClose={() => setDialog(null)}><RaisePR initial={{ source: 'shortage', ref: 'Below minimum stock', rows: lowRows }} onDone={(pr) => nav(`/purchasing/requests/${pr.id}`)} /></Dialog>}
      {dialog && typeof dialog === 'object' && <Dialog wide title={`${dialog.code}: where it is`} onClose={() => setDialog(null)}><Where item={dialog} /><p className="row"><Link to={`/stock/moves?item=${dialog.id}`}>Movements of {dialog.code}</Link></p></Dialog>}
    </>
  )
}

// ---------- movements ----------

export function MovesPage() {
  const can = useCan()
  const when = useWhen()
  const items = useItems()
  const [item, setItem] = useState(new URLSearchParams(window.location.search).get('item') ?? '')
  const [kind, setKind] = useState('')
  const [offset, setOffset] = useState(0)
  const [err, setErr] = useState('')
  const params = new URLSearchParams({ offset: String(offset), limit: '50', ...(item ? { item_id: item } : {}), ...(kind ? { kind } : {}) })
  const { data, error } = useApi<Page<Move>>(`/stock/moves?${params}`)
  return (
    <>
      <PageHead title="Stock movements" intro="Every change of stock, newest first. Nothing here is ever edited: a correction is a new movement.">
        {can('stock.export') && <button className="btn btn-secondary" onClick={() => download(`/stock/moves/export${item ? `?item_id=${item}` : ''}`, 'stock-movements.csv').catch((e) => setErr(e.message))}>Download CSV</button>}
      </PageHead>
      <FormError message={err} />
      <div className="toolbar">
        <div className="field"><label htmlFor="mv-item">Item</label><select id="mv-item" value={item} onChange={(e) => { setItem(e.target.value); setOffset(0) }}><option value="">All items</option><ItemOptions items={items} /></select></div>
        <SelectField label="Movement" value={kind} onChange={(e) => { setKind(e.target.value); setOffset(0) }} options={[['', 'All'], ...Object.entries(MOVE_KIND)]} />
      </div>
      {error ? <PageState kind="error" title="Couldn't load movements" message={error} />
        : !data ? <PageState kind="loading" title="Loading…" />
        : data.items.length === 0 ? <PageState kind="empty" title="No movements" message="Receipts, transfers, issues and corrections appear here." />
        : (
          <div className="table-wrap">
            <table className="table stack">
              <thead><tr><th scope="col">When</th><th scope="col">Item</th><th scope="col" className="num">Quantity</th><th scope="col">From → to</th><th scope="col">What</th><th scope="col">By</th></tr></thead>
              <tbody>
                {data.items.map((m) => (
                  <tr key={m.id}>
                    <td className="cell-sub nowrap">{when(m.at)}</td>
                    <td className="cell-main"><code>{m.item.code}</code>{(m.serial || m.lot) && <span className="small muted">{m.serial ?? `Lot ${m.lot}`}</span>}</td>
                    <td className="num cell-tag">{qty(m.qty, m.item.unit_code)}</td>
                    <td className="cell-sub">{m.from ?? 'In'} → {m.to ?? 'Out'}</td>
                    <td className="cell-sub">{MOVE_KIND[m.kind] ?? m.kind}{m.doc_number && <span className="small muted">{m.doc_number}</span>}{m.reason && <span className="small muted">{m.reason}</span>}</td>
                    <td className="cell-sub">{m.user}</td>
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

// ---------- serial numbers ----------

const SERIAL_STATUSES = ['available', 'awaiting_inspection', 'on_hold', 'under_assembly', 'rework', 'issued', 'consumed', 'rejected', 'returned_to_supplier', 'written_off']

export function SerialsPage() {
  const [q, setQ] = useState('')
  const [status, setStatus] = useState('')
  const [offset, setOffset] = useState(0)
  const { data, error } = useApi<Page<SerialRow>>(`/serials?${new URLSearchParams({ q, offset: String(offset), limit: '50', ...(status ? { status } : {}) })}`)
  return (
    <>
      <PageHead title="Serial numbers" intro="Every individually tracked unit: purchased battery units, serialised components and the batteries SMET builds." />
      <div className="toolbar" role="search">
        <Field label="Serial or supplier serial" type="search" value={q} onChange={(e) => { setQ(e.target.value.toUpperCase()); setOffset(0) }} />
        <SelectField label="Status" value={status} onChange={(e) => { setStatus(e.target.value); setOffset(0) }} options={[['', 'Any'], ...SERIAL_STATUSES.map((s): [string, string] => [s, s.replaceAll('_', ' ')])]} />
      </div>
      {error ? <PageState kind="error" title="Couldn't load" message={error} />
        : !data ? <PageState kind="loading" title="Loading…" />
        : data.items.length === 0 ? <PageState kind="empty" title="No serial numbers match" />
        : (
          <div className="table-wrap">
            <table className="table stack">
              <thead><tr><th scope="col">Serial</th><th scope="col">Item</th><th scope="col">Where</th><th scope="col">Status</th></tr></thead>
              <tbody>
                {data.items.map((s) => (
                  <tr key={s.id}>
                    <td className="cell-main"><Link to={`/stock/serials/${s.id}`}><code>{s.serial}</code></Link>{s.supplier_serial && <span className="small muted">Supplier {s.supplier_serial}</span>}</td>
                    <td className="cell-sub"><code>{s.item.code}</code> {s.item.name}</td>
                    <td className="cell-sub">{s.location ?? <span className="muted">Not in stock</span>}</td>
                    <td className="cell-tag"><Status s={s.status} /></td>
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

export function SerialPage() {
  const { id } = useParams()
  const when = useWhen()
  const { data: s, error } = useApi<SerialRecord>(`/serials/${id}`)
  if (error) return <PageState kind="error" title="Couldn't load the serial number" message={error} />
  if (!s) return <PageState kind="loading" title="Loading…" />
  return (
    <>
      <Back to="/stock/serials">Serial numbers</Back>
      <PageHead title={s.serial} intro={`${s.item.code} · ${s.item.name}`}>
        <Link className="btn btn-secondary" target="_blank" to={`/print/labels?ids=${s.id}`}>Print label</Link>
      </PageHead>
      <Facts rows={[['Status', <Status s={s.status} />], ['Where', s.location ?? 'Not in stock'], ['Supplier serial', s.supplier_serial ?? '—'],
        ['Bought on', s.purchase_order ? <Link to={`/purchasing/orders/${s.purchase_order.id}`}>{s.purchase_order.number}</Link> : '—'],
        ['Built into', s.built_into ? <Link to={`/stock/serials/${s.built_into.id}`}>{s.built_into.serial}</Link> : '—']]} />
      {s.components.length > 0 && (
        <>
          <h2 className="section">What went into it</h2>
          <div className="table-wrap"><table className="table"><thead><tr><th scope="col">Component</th><th scope="col">Serial or lot</th><th scope="col" className="num">Quantity</th></tr></thead>
            <tbody>{s.components.map((c, i) => <tr key={i}><td><code>{c.item.code}</code> <span className="small">{c.item.name}</span></td><td>{c.serial_id ? <Link to={`/stock/serials/${c.serial_id}`}>{c.serial}</Link> : c.lot ? `Lot ${c.lot}` : '—'}</td><td className="num">{qty(c.qty, c.item.unit_code)}</td></tr>)}</tbody></table></div>
        </>
      )}
      {s.inspections.length > 0 && (
        <>
          <h2 className="section">Inspections</h2>
          {s.inspections.map((x) => (
            <section key={x.id} className="panel panel-wide">
              <div className="panel-head"><h2><Status s={x.result} /> {x.inspector}</h2><span className="stamp">{when(x.at)}</span></div>
              {x.readings.length > 0 && <table className="table"><thead><tr><th scope="col">Reading</th><th scope="col" className="num">Value</th><th scope="col">Limits</th></tr></thead>
                <tbody>{x.readings.map((r) => <tr key={r.name}><td>{r.name}</td><td className="num">{r.value ?? '—'} {r.unit}</td><td>{r.min ?? '…'} to {r.max ?? '…'} {r.limits_confirmed ? (r.within === false ? '· outside' : '') : '· not yet confirmed by SMET'}</td></tr>)}</tbody></table>}
              {x.checklist.length > 0 && <ul className="steps">{x.checklist.map((c) => <li key={c.text} className={c.done ? 'done' : ''}>{c.done ? '✓' : '○'} {c.text}</li>)}</ul>}
              {x.observations && <p className="prewrap">{x.observations}</p>}
            </section>
          ))}
        </>
      )}
      <Lifecycle id={s.id} />
    </>
  )
}

// ---------- reservations ----------

export function ReservationsPage() {
  const can = useCan()
  const items = useItems()
  const [status, setStatus] = useState('active')
  const [dialog, setDialog] = useState<'add' | Reservation | null>(null)
  const [key, setKey] = useState(newKey)
  const { data, error, reload } = useApi<Page<Reservation>>(`/reservations?limit=200${status ? `&status=${status}` : ''}`)
  const action = useAction(() => { setDialog(null); setKey(newKey()); reload() })
  return (
    <>
      <PageHead title="Reservations" intro="Stock promised to an assembly order, a sales order or a project. Reserved stock can't be issued or corrected away.">
        {can('stock.create') && <button className="btn btn-primary" onClick={() => setDialog('add')}>Reserve stock</button>}
      </PageHead>
      <div className="toolbar"><SelectField label="Status" value={status} onChange={(e) => setStatus(e.target.value)} options={[['active', 'Active'], ['used', 'Used'], ['released', 'Released'], ['', 'All']]} /></div>
      {error ? <PageState kind="error" title="Couldn't load" message={error} />
        : !data ? <PageState kind="loading" title="Loading…" />
        : data.items.length === 0 ? <PageState kind="empty" title="No reservations" />
        : (
          <div className="table-wrap">
            <table className="table stack">
              <thead><tr><th scope="col">For</th><th scope="col">Item</th><th scope="col" className="num">Reserved</th><th scope="col" className="num">Used</th><th scope="col">Status</th><th scope="col"><span className="sr-only">Actions</span></th></tr></thead>
              <tbody>
                {data.items.map((r) => (
                  <tr key={r.id}>
                    <td className="cell-main">{r.ref_label}<span className="small muted">{r.ref_type.replaceAll('_', ' ')}</span></td>
                    <td className="cell-sub"><code>{r.item.code}</code></td>
                    <td className="num cell-sub">{qty(r.qty, r.item.unit_code)}</td>
                    <td className="num cell-sub">{qty(r.qty_used)}</td>
                    <td className="cell-tag"><Status s={r.status} /></td>
                    <td className="actions cell-action">{can('stock.create') && r.status === 'active' && r.ref_type !== 'assembly_order' && <button className="link-btn" onClick={() => setDialog(r)}>Release</button>}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      {dialog === 'add' && (
        <Dialog title="Reserve stock" onClose={() => setDialog(null)}>
          <form noValidate onSubmit={(e) => {
            e.preventDefault()
            const fd = new FormData(e.currentTarget)
            void action.run('add', () => api('/reservations', { method: 'POST', body: { key, item_id: fd.get('item_id'), qty: num(String(fd.get('qty'))), ref_type: fd.get('ref_type'), ref_label: fd.get('ref_label'), reason: fd.get('reason') } }))
          }}>
            <div className="field"><label htmlFor="rs-item">Item</label><select id="rs-item" name="item_id" required><option value="">Choose an item</option><ItemOptions items={items} /></select></div>
            <Field label="Quantity" name="qty" type="number" min={0} step="any" required />
            <SelectField label="For a" name="ref_type" options={[['project', 'Project'], ['sales_order', 'Sales order'], ['other', 'Other']]} />
            <Field label="Which one" name="ref_label" required maxLength={120} hint="Sales orders and projects get their own records in Phase 4" />
            <Field label="Note" name="reason" maxLength={300} />
            <FormError message={action.error} />
            <button className="btn btn-primary" disabled={!!action.busy}>Reserve</button>
          </form>
        </Dialog>
      )}
      {dialog && typeof dialog === 'object' && (
        <Dialog title={`Release ${dialog.item.code} for ${dialog.ref_label}`} onClose={() => setDialog(null)}>
          <form noValidate onSubmit={(e) => { e.preventDefault(); void action.run('release', () => api(`/reservations/${dialog.id}/release`, { method: 'POST', body: { version: dialog.version, reason: new FormData(e.currentTarget).get('reason') } })) }}>
            <TextareaField label="Reason" name="reason" required minLength={3} maxLength={500} />
            <FormError message={action.error} />
            <button className="btn btn-primary" disabled={!!action.busy}>Release reservation</button>
          </form>
        </Dialog>
      )}
    </>
  )
}

// ---------- corrections and counts ----------

function RequestCorrection({ onDone }: { onDone: () => void }) {
  const items = useItems()
  const { warehouses } = useWarehouses()
  const [rows, setRows] = useState<Row[]>([{ key: newKey(), item_id: '', location_id: '', qty_change: '', serials_in: '', serials_out: '', lot: '' }])
  const [key] = useState(newKey)
  const action = useAction(onDone)
  const set = (i: number, k: string, v: string) => setRows(rows.map((r, j) => (j === i ? { ...r, [k]: v } : r)))
  return (
    <form noValidate onSubmit={(e) => {
      e.preventDefault()
      const reason = new FormData(e.currentTarget).get('reason')
      void action.run('go', () => api('/adjustments', { method: 'POST', body: { key, reason, lines: rows.filter((r) => r.item_id).map((r) => ({
        item_id: r.item_id, location_id: r.location_id, lot: r.lot, qty_change: num(r.qty_change) ?? null, serials_in: serialList(r.serials_in), serials_out: serialList(r.serials_out) })) } }))
    }}>
      {rows.map((r, i) => {
        const it = items.find((x) => x.id === r.item_id)
        return (
          <fieldset key={r.key} className="lines">
            <legend>Line {i + 1}</legend>
            <div className="grid-fields">
              <div className="field"><label htmlFor={`aj-i${i}`}>Item</label><select id={`aj-i${i}`} value={r.item_id} onChange={(e) => set(i, 'item_id', e.target.value)}><option value="">Choose an item</option><ItemOptions items={items} /></select></div>
              <div className="field"><label htmlFor={`aj-l${i}`}>Location</label><select id={`aj-l${i}`} value={r.location_id} onChange={(e) => set(i, 'location_id', e.target.value)}><option value="">Choose</option><LocationOptions warehouses={warehouses} /></select></div>
              {it?.tracking === 'lot' && <Field label="Lot" value={r.lot} onChange={(e) => set(i, 'lot', e.target.value)} />}
              {it?.tracking !== 'serial' && <Field label="Change" type="number" step="any" value={r.qty_change} onChange={(e) => set(i, 'qty_change', e.target.value)} hint="−2 takes two away, 5 adds five" />}
            </div>
            {it?.tracking === 'serial' && <div className="grid-fields">
              <TextareaField label="Serial numbers found (add)" rows={2} value={r.serials_in} onChange={(e) => set(i, 'serials_in', e.target.value)} />
              <TextareaField label="Serial numbers missing (remove)" rows={2} value={r.serials_out} onChange={(e) => set(i, 'serials_out', e.target.value)} />
            </div>}
          </fieldset>
        )
      })}
      <button type="button" className="btn btn-secondary" onClick={() => setRows([...rows, { key: newKey(), item_id: '', location_id: '', qty_change: '', serials_in: '', serials_out: '', lot: '' }])}>Add line</button>
      <TextareaField label="Reason" name="reason" required minLength={3} maxLength={500} hint="Why stock differs: damaged, lost, found, miscounted…" />
      <p className="hint">Stock changes only when someone else approves the correction.</p>
      <FormError message={action.error} />
      <button className="btn btn-primary" disabled={!!action.busy}>Ask for approval</button>
    </form>
  )
}

function CorrectionDetail({ id, onDone }: { id: string; onDone: () => void }) {
  const can = useCan()
  const me = useMe()
  const { data: a } = useApi<Adjustment>(`/adjustments/${id}`)
  const action = useAction(onDone)
  const [reason, setReason] = useState('')
  if (!a) return <PageState kind="loading" title="Loading…" />
  const decide = (verb: string) => action.run(verb, () => api(`/adjustments/${a.id}/${verb}`, { method: 'POST', body: { version: a.version, reason } }))
  return (
    <>
      <p>{a.reason}</p>
      <p className="muted small">Asked by {a.requested_by}{a.decided_by && ` · ${a.status} by ${a.decided_by}${a.decision_reason ? `: ${a.decision_reason}` : ''}`}</p>
      <table className="table">
        <thead><tr><th scope="col">Item</th><th scope="col">Location</th><th scope="col">Change</th></tr></thead>
        <tbody>{a.lines!.map((l) => (
          <tr key={l.id}><td><code>{l.item.code}</code>{l.lot && <span className="small">Lot {l.lot}</span>}</td><td><code>{l.location}</code></td>
            <td>{l.counted != null ? `Counted ${qty(l.counted)} (the difference is worked out at approval)` : l.serials_found ? `Found: ${l.serials_found.join(', ') || 'none'}` : l.qty_change != null && Number(l.qty_change) !== 0 ? (l.qty_change > 0 ? `+${qty(l.qty_change)}` : qty(l.qty_change)) : `+ ${l.serials_in.join(', ') || '—'} / − ${l.serials_out.join(', ') || '—'}`}</td></tr>
        ))}</tbody>
      </table>
      {a.status === 'pending' && can('adjustments.approve') && (a.requested_by_id === me.user.id
        ? <p className="notice notice-warning">You asked for this correction, so someone else decides on it.</p>
        : <>
          <TextareaField label="Comment" value={reason} onChange={(e) => setReason(e.target.value)} maxLength={500} hint="Needed to reject." />
          <FormError message={action.error} />
          <div className="row"><button className="btn btn-primary" disabled={!!action.busy} onClick={() => decide('approve')}>Approve and change stock</button>
            <button className="btn btn-secondary" disabled={!!action.busy} onClick={() => decide('reject')}>Reject</button></div>
        </>)}
    </>
  )
}

export function CorrectionsPage() {
  const can = useCan()
  const when = useWhen()
  const nav = useNavigate()
  const { warehouses } = useWarehouses()
  const [status, setStatus] = useState('pending')
  const [dialog, setDialog] = useState<'request' | 'count' | string | null>(null)
  const adj = useApi<Page<Adjustment>>(`/adjustments?limit=100${status ? `&status=${status}` : ''}`)
  const counts = useApi<Page<Count>>('/counts?limit=20')
  const action = useAction(() => {})
  const done = () => { setDialog(null); adj.reload(); counts.reload() }
  return (
    <>
      <PageHead title="Counts & corrections" intro="Count a location against the books; differences, and any other correction, wait for a second person's approval.">
        {can('stock.create') && <button className="btn btn-secondary" onClick={() => setDialog('count')}>Start a count</button>}
        {can('adjustments.create') && <button className="btn btn-primary" onClick={() => setDialog('request')}>Request a correction</button>}
      </PageHead>
      <h2 className="section">Corrections</h2>
      <div className="toolbar"><SelectField label="Status" value={status} onChange={(e) => setStatus(e.target.value)} options={[['pending', 'Waiting for approval'], ['approved', 'Approved'], ['rejected', 'Rejected'], ['', 'All']]} /></div>
      {!adj.data ? <PageState kind="loading" title="Loading…" /> : adj.data.items.length === 0 ? <p className="empty-line">No corrections here.</p> : (
        <div className="table-wrap">
          <table className="table stack">
            <thead><tr><th scope="col">Correction</th><th scope="col">Reason</th><th scope="col">Asked by</th><th scope="col">Status</th></tr></thead>
            <tbody>{adj.data.items.map((a) => (
              <tr key={a.id}><td className="cell-main"><button className="link-btn" onClick={() => setDialog(a.id)}>{a.number}</button><span className="small muted">{when(a.created_at)}</span></td>
                <td className="cell-sub">{a.reason}</td><td className="cell-sub">{a.requested_by}</td><td className="cell-tag"><Status s={a.status} /></td></tr>
            ))}</tbody>
          </table>
        </div>
      )}
      <h2 className="section">Recent counts</h2>
      {!counts.data ? null : counts.data.items.length === 0 ? <p className="empty-line">No counts yet.</p> : (
        <ul className="feed panel panel-wide">
          {counts.data.items.map((c) => <li key={c.id}><span><Link to={`/stock/counts/${c.id}`}>{c.number}</Link><span className="small muted">{c.location}</span></span><span className="row-end"><Status s={c.status} /><span className="stamp">{when(c.created_at)}</span></span></li>)}
        </ul>
      )}
      {dialog === 'request' && <Dialog wide title="Request a stock correction" onClose={() => setDialog(null)}><RequestCorrection onDone={done} /></Dialog>}
      {dialog === 'count' && (
        <Dialog title="Start a count" onClose={() => setDialog(null)}>
          <form onSubmit={(e) => { e.preventDefault(); const fd = new FormData(e.currentTarget); void action.run('go', async () => { const c = await api<Count>('/counts', { method: 'POST', body: { location_id: fd.get('location_id'), notes: fd.get('notes') } }); nav(`/stock/counts/${c.id}`) }) }}>
            <div className="field"><label htmlFor="ct-loc">Location to count</label><select id="ct-loc" name="location_id" required><option value="">Choose</option><LocationOptions warehouses={warehouses} /></select></div>
            <TextareaField label="Notes" name="notes" maxLength={2000} />
            <p className="hint">The count lists what the books show now. Counters enter what they find.</p>
            <FormError message={action.error} />
            <button className="btn btn-primary" disabled={!!action.busy}>Start count</button>
          </form>
        </Dialog>
      )}
      {dialog && !['request', 'count'].includes(dialog) && <Dialog wide title="Stock correction" onClose={() => setDialog(null)}><CorrectionDetail id={dialog} onDone={done} /></Dialog>}
    </>
  )
}

export function CountPage() {
  const { id } = useParams()
  const can = useCan()
  const items = useItems()
  const { data: c, error, reload } = useApi<Count>(`/counts/${id}`)
  const [edits, setEdits] = useState<Record<string, string>>({})
  const [extra, setExtra] = useState('')
  const action = useAction(() => { setEdits({}); reload() })
  if (error) return <PageState kind="error" title="Couldn't load the count" message={error} />
  if (!c) return <PageState kind="loading" title="Loading…" />
  const open = c.status === 'open' && can('stock.create')
  const lineKey = (itemId: string, lot: string) => `${itemId}|${lot}`
  const save = () => api<Count>(`/counts/${c.id}/lines`, { method: 'PUT', body: { version: c.version, lines: Object.entries(edits).map(([k, v]) => {
    const [item_id, lot] = k.split('|')
    const it = c.lines!.find((l) => l.item.id === item_id)?.item ?? items.find((i) => i.id === item_id)
    return it?.tracking === 'serial' ? { item_id, lot, serials_found: serialList(v) } : { item_id, lot, counted: num(v) ?? null }
  }) } })
  return (
    <>
      <Back to="/stock/corrections">Counts & corrections</Back>
      <PageHead title={c.number} intro={`${c.location} · ${PURPOSE[c.purpose]}`}>
        {open && <>
          <button className="btn btn-secondary" disabled={!!action.busy} onClick={() => action.run('save', save)}>Save counts</button>
          <button className="btn btn-primary" disabled={!!action.busy} onClick={() => action.run('submit', async () => { const fresh = Object.keys(edits).length ? await save() : c; await api(`/counts/${c.id}/submit`, { method: 'POST', body: { version: fresh.version } }) }, 'Submit the count? Differences become a correction for approval.')}>Submit count</button>
        </>}
      </PageHead>
      <Facts rows={[['Status', <Status s={c.status} />], ['Lines', String(c.lines!.length)], ['Correction', c.adjustment_id ? 'Requested, waiting for approval' : c.status === 'submitted' ? 'None needed' : '—']]} />
      <FormError message={action.error} />
      <div className="table-wrap">
        <table className="table">
          <thead><tr><th scope="col">Item</th><th scope="col" className="num">On the books</th><th scope="col">Counted</th></tr></thead>
          <tbody>
            {c.lines!.map((l) => {
              const k = lineKey(l.item.id, l.lot)
              const serial = l.item.tracking === 'serial'
              const value = edits[k] ?? (serial ? (l.serials_found ?? []).join('\n') : l.counted == null ? '' : String(l.counted))
              return (
                <tr key={l.id}>
                  <td><code>{l.item.code}</code><span className="small">{l.item.name}{l.lot && ` · lot ${l.lot}`}</span></td>
                  <td className="num">{qty(l.expected, l.item.unit_code)}{serial && l.serials_expected && <span className="small muted">{l.serials_expected.join(', ')}</span>}</td>
                  <td>{serial
                    ? <textarea aria-label={`${l.item.code} serial numbers found`} rows={2} disabled={!open} value={value} onChange={(e) => setEdits({ ...edits, [k]: e.target.value })} placeholder="Scan each unit found" />
                    : <input aria-label={`${l.item.code} counted`} type="number" min={0} step="any" disabled={!open} value={value} onChange={(e) => setEdits({ ...edits, [k]: e.target.value })} />}</td>
                </tr>
              )
            })}
          </tbody>
        </table>
      </div>
      {open && (
        <div className="row">
          <label className="field inline-field"><span>Found something not listed</span>
            <select value={extra} onChange={(e) => setExtra(e.target.value)}><option value="">Choose an item</option><ItemOptions items={items} /></select></label>
          <button className="btn btn-secondary" disabled={!extra} onClick={() => action.run('add', async () => { await api(`/counts/${c.id}/lines`, { method: 'PUT', body: { version: c.version, lines: [{ item_id: extra, counted: 0, ...(items.find((i) => i.id === extra)?.tracking === 'serial' ? { counted: undefined, serials_found: [] } : {}) }] } }); setExtra('') })}>Add to count</button>
        </div>
      )}
    </>
  )
}
