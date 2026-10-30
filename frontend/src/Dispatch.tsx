import { useState } from 'react'
import { Link, useNavigate, useParams } from 'react-router'
import { api } from './api'
import { useAction, useApi, useCan, useMe, useWhen } from './lib'
import { Back, Facts, LocationOptions, Status, day, newKey, num, qty, serialList, today, useWarehouses, type Page, type Ref } from './ops'
import { Dialog, Field, FormError, Logo, PageHead, PageState, Pager, SelectField, TextareaField } from './ui'

type PlanRow = { order: { id: string; number: string }; customer: { name: string }; delivery_date: string | null; line_id: string; item: Ref; pending: number; planned: number; reserved: number; free: number; ready: number }
type DLine = { id: string; order_line_id: string; item: Ref; qty: number; lot: string; location: string | null; serials: string[]; qty_rejected: number; qty_damaged: number; rejected_serials: string[]; note: string }
type Dispatch = {
  id: string; number: string; status: string; order: { id: string; number: string }; customer: { id: string; name: string; code: string }
  warehouse: { id: string; code: string; name: string }; site: { id: string; name: string; address: string } | null
  planned_date: string | null; destination: string; delivery_contact: string; transporter: string; vehicle: string
  dispatched_at: string | null; received_by: string; delivered_on: string | null; delivery_notes: string; cancel_reason: string
  created_at: string; version: number; lines?: DLine[]
}
type OrderForDispatch = { id: string; number: string; customer: { name: string }; delivery_address: string; project_id: string | null
  lines: { id: string; line_type: string; item: Ref | null; pending: number; reserved: number; reserved_for_project: number; free: number }[] }

export function DispatchesPage() {
  const can = useCan()
  const nav = useNavigate()
  const when = useWhen()
  const [status, setStatus] = useState('planned,dispatched')
  const [offset, setOffset] = useState(0)
  const plan = useApi<{ items: PlanRow[] }>('/dispatch/plan')
  const { data, error } = useApi<Page<Dispatch>>(`/dispatches?${new URLSearchParams({ offset: String(offset), limit: '50', ...(status ? { status } : {}) })}`)
  return (
    <>
      <PageHead title="Dispatch" intro="What is still to send, by committed date, and every dispatch. Only available stock can leave: nothing on hold, uninspected or under assembly." />
      <h2 className="section">To send</h2>
      {!plan.data ? <PageState kind="loading" title="Loading…" /> : plan.data.items.length === 0 ? <p className="empty-line">Nothing waiting: every order line has been sent.</p> : (
        <div className="table-wrap">
          <table className="table stack">
            <thead><tr><th scope="col">Order</th><th scope="col">Item</th><th scope="col">Committed</th><th scope="col" className="num">Still to send</th><th scope="col" className="num">Planned</th><th scope="col" className="num">Ready now</th><th scope="col"><span className="sr-only">Actions</span></th></tr></thead>
            <tbody>
              {plan.data.items.map((r) => (
                <tr key={r.line_id}>
                  <td className="cell-main"><Link to={`/sales/orders/${r.order.id}`}>{r.order.number}</Link><span className="small muted">{r.customer.name}</span></td>
                  <td className="cell-sub"><code>{r.item.code}</code></td>
                  <td className="cell-sub nowrap">{day(r.delivery_date)}</td>
                  <td className="num cell-tag">{qty(r.pending, r.item.unit_code)}</td>
                  <td className="num cell-sub">{qty(r.planned)}</td>
                  <td className="num cell-sub"><strong>{qty(r.ready)}</strong></td>
                  <td className="actions cell-action">{can('dispatch.create') && <button className="link-btn" onClick={() => nav(`/dispatch/new?order=${r.order.id}`)}>Plan dispatch</button>}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
      <h2 className="section">Dispatches</h2>
      <div className="toolbar"><SelectField label="Status" value={status} onChange={(e) => { setStatus(e.target.value); setOffset(0) }} options={[['planned,dispatched', 'Planned or on the way'], ['delivered', 'Delivered'], ['cancelled', 'Cancelled'], ['', 'All']]} /></div>
      {error ? <PageState kind="error" title="Couldn't load dispatches" message={error} />
        : !data ? <PageState kind="loading" title="Loading…" />
        : data.items.length === 0 ? <p className="empty-line">No dispatches here.</p>
        : (
          <div className="table-wrap">
            <table className="table stack">
              <thead><tr><th scope="col">Dispatch</th><th scope="col">Order</th><th scope="col">Destination</th><th scope="col">Planned</th><th scope="col">Status</th></tr></thead>
              <tbody>
                {data.items.map((d) => (
                  <tr key={d.id}>
                    <td className="cell-main"><Link to={`/dispatch/${d.id}`}>{d.number}</Link>{d.dispatched_at && <span className="small muted">Left {when(d.dispatched_at)}</span>}</td>
                    <td className="cell-sub">{d.order.number} · {d.customer.name}</td>
                    <td className="cell-sub">{d.site?.name ?? (d.destination || '—')}</td>
                    <td className="cell-sub nowrap">{day(d.planned_date)}</td>
                    <td className="cell-tag"><Status s={d.status} /></td>
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

export function NewDispatchPage() {
  const nav = useNavigate()
  const can = useCan()
  const orderId = new URLSearchParams(window.location.search).get('order') ?? ''
  const { data: so } = useApi<OrderForDispatch>(orderId ? `/sales-orders/${orderId}` : null)
  const { warehouses } = useWarehouses()
  const sites = useApi<{ sites: { id: string; name: string; address: string }[] }>(so?.project_id && can('projects.view') ? `/projects/${so.project_id}` : null).data?.sites ?? []
  const [wh, setWh] = useState('')
  const [rows, setRows] = useState<Record<string, { qty: string; serials: string; lot: string; location_id: string }>>({})
  const action = useAction(() => {})
  if (!orderId) return <PageState kind="empty" title="Choose an order first" message="Plan a dispatch from a sales order or from the dispatch list." action={<Link to="/dispatch">Dispatch</Link>} />
  if (!so) return <PageState kind="loading" title="Loading…" />
  const goods = so.lines.filter((l) => ['battery', 'accessory'].includes(l.line_type) && l.pending > 0)
  const warehouseId = wh || warehouses.find((w) => w.active)?.id || ''
  const row = (id: string, pending: number) => rows[id] ?? { qty: String(pending), serials: '', lot: '', location_id: '' }
  const set = (id: string, pending: number, k: string, v: string) => setRows({ ...rows, [id]: { ...row(id, pending), [k]: v } })
  return (
    <>
      <Back to={`/sales/orders/${so.id}`}>{so.number}</Back>
      <PageHead title="Plan a dispatch" intro={`${so.number} · ${so.customer.name}. Planning moves no stock; sending it does.`} />
      <form noValidate onSubmit={(e) => {
        e.preventDefault()
        const fd = new FormData(e.currentTarget)
        const s = (n: string) => String(fd.get(n) ?? '')
        const lines = goods.map((l) => ({ l, r: row(l.id, l.pending) })).filter(({ r }) => num(r.qty)).map(({ l, r }) => ({
          order_line_id: l.id, qty: num(r.qty), lot: r.lot, serials: serialList(r.serials), location_id: r.location_id || null }))
        void action.run('plan', async () => {
          const d = await api<Dispatch>('/dispatches', { method: 'POST', body: { order_id: so.id, warehouse_id: warehouseId, site_id: s('site_id') || null,
            planned_date: s('planned_date') || null, destination: s('destination'), delivery_contact: s('delivery_contact'), transporter: s('transporter'), vehicle: s('vehicle'), lines } })
          nav(`/dispatch/${d.id}`)
        })
      }}>
        <div className="grid-fields">
          <SelectField label="From warehouse" value={warehouseId} onChange={(e) => setWh(e.target.value)} options={warehouses.filter((w) => w.active).map((w) => [w.id, `${w.code} · ${w.name}`])} />
          {sites.length > 0 && <SelectField label="To site" name="site_id" options={[['', 'No particular site'], ...sites.map((x): [string, string] => [x.id, x.name])]} />}
          <Field label="Planned date" name="planned_date" type="date" />
          <Field label="Delivery contact" name="delivery_contact" maxLength={160} />
          <Field label="Transporter" name="transporter" maxLength={120} />
          <Field label="Vehicle" name="vehicle" maxLength={60} />
        </div>
        <TextareaField label="Destination" name="destination" defaultValue={so.delivery_address} maxLength={2000} hint="Empty: the site's or order's address" />
        <div className="table-wrap">
          <table className="table">
            <thead><tr><th scope="col">Item</th><th scope="col" className="num">Still to send</th><th scope="col" className="num">Reserved / free</th><th scope="col">Send now</th><th scope="col">From</th><th scope="col">Serial numbers / lot</th></tr></thead>
            <tbody>
              {goods.map((l) => {
                const r = row(l.id, l.pending)
                return (
                  <tr key={l.id}>
                    <td><code>{l.item!.code}</code><span className="small">{l.item!.name}</span></td>
                    <td className="num">{qty(l.pending, l.item!.unit_code)}</td>
                    <td className="num">{qty(l.reserved + l.reserved_for_project)} / {qty(l.free)}</td>
                    <td><input aria-label={`${l.item!.code} to send`} type="number" min={0} step="any" value={r.qty} onChange={(e) => set(l.id, l.pending, 'qty', e.target.value)} /></td>
                    <td><select aria-label={`${l.item!.code} from`} value={r.location_id} onChange={(e) => set(l.id, l.pending, 'location_id', e.target.value)}><option value="">Main store</option><LocationOptions warehouses={warehouses.filter((w) => w.id === warehouseId)} purpose="available" /></select></td>
                    <td>
                      {l.item!.tracking === 'serial' && <SerialPicker item={l.item!} value={r.serials} onChange={(v) => set(l.id, l.pending, 'serials', v)} />}
                      {l.item!.tracking === 'lot' && <input aria-label={`${l.item!.code} lot`} placeholder="Lot" value={r.lot} onChange={(e) => set(l.id, l.pending, 'lot', e.target.value)} />}
                    </td>
                  </tr>
                )
              })}
            </tbody>
          </table>
        </div>
        <FormError message={action.error} />
        <button className="btn btn-primary" disabled={!!action.busy || !warehouseId}>Plan dispatch</button>
      </form>
    </>
  )
}

/** Serial numbers to send: typed or scanned; the available ones are listed to pick from. */
function SerialPicker({ item, value, onChange }: { item: Ref; value: string; onChange: (v: string) => void }) {
  const avail = useApi<Page<{ serial: string; location: string }>>(`/serials?item_id=${item.id}&status=available&limit=200`).data?.items ?? []
  const chosen = serialList(value)
  return (
    <div className="serial-picker">
      <textarea aria-label={`${item.code} serial numbers`} rows={2} placeholder="Scan or type, one per unit" value={value} onChange={(e) => onChange(e.target.value)} />
      {avail.length > 0 && <p className="small muted">Available: {avail.map((a) => (
        <button key={a.serial} type="button" className="link-btn small" disabled={chosen.includes(a.serial)} onClick={() => onChange([...chosen, a.serial].join('\n'))}>{a.serial}</button>
      )).reduce<React.ReactNode[]>((acc, x, i) => (i ? [...acc, ', ', x] : [x]), [])}</p>}
    </div>
  )
}

export function DispatchPage() {
  const { id } = useParams()
  const can = useCan()
  const when = useWhen()
  const { data: d, error, reload } = useApi<Dispatch>(`/dispatches/${id}`)
  const [dialog, setDialog] = useState<'deliver' | 'cancel' | null>(null)
  const [key, setKey] = useState(newKey)
  const action = useAction(() => { setKey(newKey()); setDialog(null); reload() })
  const [refused, setRefused] = useState<Record<string, { serials: string; qty_rejected: string; qty_damaged: string; note: string }>>({})
  if (error) return <PageState kind="error" title="Couldn't load the dispatch" message={error} />
  if (!d) return <PageState kind="loading" title="Loading…" />
  const post = (path: string, body: object) => api(`/dispatches/${d.id}/${path}`, { method: 'POST', body: { version: d.version, key, ...body } })
  const rf = (lid: string) => refused[lid] ?? { serials: '', qty_rejected: '', qty_damaged: '', note: '' }
  return (
    <>
      <Back to="/dispatch">Dispatch</Back>
      <PageHead title={d.number} intro={`${d.order.number} · ${d.customer.name}${d.site ? ` · ${d.site.name}` : ''}`}>
        <Link className="btn btn-secondary" to={`/print/dispatch/${d.id}`} target="_blank">Delivery note</Link>
        {can('dispatch.create') && d.status === 'planned' && <button className="btn btn-primary" onClick={() => action.run('send', () => post('send', {}), `Send ${d.number}? Stock leaves the warehouse now.`)}>Send</button>}
        {can('dispatch.create') && d.status === 'dispatched' && <button className="btn btn-primary" onClick={() => setDialog('deliver')}>Record delivery</button>}
      </PageHead>
      {d.status === 'cancelled' && <p className="notice notice-warning">Cancelled: {d.cancel_reason}</p>}
      <Facts rows={[['Status', <Status s={d.status} />], ['From', `${d.warehouse.code} · ${d.warehouse.name}`], ['Planned', day(d.planned_date)],
        ['Transport', [d.transporter, d.vehicle].filter(Boolean).join(', ') || '—'],
        ['Delivered', d.delivered_on ? `${day(d.delivered_on)}, to ${d.received_by}` : d.dispatched_at ? `Left ${when(d.dispatched_at)}` : '—']]} />
      <FormError message={action.error} />
      <div className="table-wrap">
        <table className="table">
          <thead><tr><th scope="col">Item</th><th scope="col" className="num">Quantity</th><th scope="col">Serial numbers / lot</th><th scope="col">From</th><th scope="col">On delivery</th></tr></thead>
          <tbody>
            {d.lines!.map((l) => (
              <tr key={l.id}>
                <td><code>{l.item.code}</code><span className="small">{l.item.name}</span></td>
                <td className="num">{qty(l.qty, l.item.unit_code)}</td>
                <td className="small">{l.serials.join(', ') || (l.lot ? `Lot ${l.lot}` : '—')}</td>
                <td className="small">{l.location ?? 'Main store'}</td>
                <td className="small">{l.qty_rejected || l.qty_damaged ? `${qty(l.qty_rejected)} refused${l.rejected_serials.length ? ` (${l.rejected_serials.join(', ')})` : ''}, ${qty(l.qty_damaged)} damaged${l.note ? `: ${l.note}` : ''}` : d.status === 'delivered' ? 'Accepted' : '—'}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      {d.destination && <p className="prewrap muted">To: {d.destination}{d.delivery_contact && ` · ${d.delivery_contact}`}</p>}
      {can('dispatch.create') && ['planned', 'dispatched'].includes(d.status) && <p><button className="btn btn-ghost" onClick={() => setDialog('cancel')}>Cancel dispatch</button></p>}
      {dialog === 'cancel' && (
        <Dialog title={`Cancel ${d.number}`} onClose={() => setDialog(null)}>
          <form noValidate onSubmit={(e) => { e.preventDefault(); void action.run('cancel', () => post('cancel', { reason: new FormData(e.currentTarget).get('reason') })) }}>
            {d.status === 'dispatched' && <p className="hint">The goods come back to where they were picked from, and the reservations they used are given back. This dispatch stays on record.</p>}
            <TextareaField label="Reason" name="reason" required minLength={3} maxLength={500} />
            <FormError message={action.error} />
            <button className="btn btn-primary" disabled={!!action.busy}>Cancel dispatch</button>
          </form>
        </Dialog>
      )}
      {dialog === 'deliver' && (
        <Dialog wide title={`Delivery of ${d.number}`} onClose={() => setDialog(null)}>
          <form noValidate onSubmit={(e) => {
            e.preventDefault()
            const fd = new FormData(e.currentTarget)
            const lines = d.lines!.map((l) => ({ l, r: rf(l.id) })).filter(({ r }) => r.serials || r.qty_rejected || r.qty_damaged).map(({ l, r }) => ({
              line_id: l.id, rejected_serials: serialList(r.serials), qty_rejected: num(r.qty_rejected) ?? 0, qty_damaged: num(r.qty_damaged) ?? 0, note: r.note }))
            void action.run('deliver', () => post('deliver', { received_by: fd.get('received_by'), delivered_on: fd.get('delivered_on'), notes: fd.get('notes'), lines }))
          }}>
            <div className="grid-fields">
              <Field label="Received by" name="received_by" required maxLength={160} />
              <Field label="On" name="delivered_on" type="date" required defaultValue={today()} />
            </div>
            <p className="hint">Leave a line empty if it arrived complete. Refused goods come back to the customer-returns area and are owed again on the order.</p>
            {d.lines!.map((l) => {
              const r = rf(l.id)
              const set = (k: string, v: string) => setRefused({ ...refused, [l.id]: { ...r, [k]: v } })
              return (
                <div className="line" key={l.id}>
                  <span className="line-item"><code>{l.item.code}</code> × {qty(l.qty)}</span>
                  {l.item.tracking === 'serial'
                    ? <label className="line-item"><span className="line-label">Refused serial numbers</span><input value={r.serials} onChange={(e) => set('serials', e.target.value)} /></label>
                    : <label style={{ inlineSize: 110 }}><span className="line-label">Refused</span><input type="number" min={0} step="any" value={r.qty_rejected} onChange={(e) => set('qty_rejected', e.target.value)} /></label>}
                  <label style={{ inlineSize: 110 }}><span className="line-label">Damaged, kept</span><input type="number" min={0} step="any" value={r.qty_damaged} onChange={(e) => set('qty_damaged', e.target.value)} /></label>
                  <label className="line-item"><span className="line-label">Note</span><input value={r.note} maxLength={300} onChange={(e) => set('note', e.target.value)} /></label>
                </div>
              )
            })}
            <TextareaField label="Notes" name="notes" maxLength={2000} />
            <FormError message={action.error} />
            <button className="btn btn-primary" disabled={!!action.busy}>Record delivery</button>
          </form>
        </Dialog>
      )}
    </>
  )
}

/** The delivery note, laid out for printing (no menu); the browser's Print saves it as PDF. */
export function DeliveryNotePage() {
  const { id } = useParams()
  const { data: d, error } = useApi<Dispatch>(`/dispatches/${id}`)
  const me = useMe()
  if (error) return <PageState kind="error" title="Couldn't load the delivery note" message={error} />
  if (!d) return <PageState kind="loading" title="Loading…" />
  return (
    <article className="print-doc">
      <header className="print-head">
        <span className="brand"><Logo /> {me.company.name}</span>
        <div><h1>Delivery note</h1><p className="muted">{d.number} · {day(d.planned_date) !== '—' ? day(d.planned_date) : day(d.created_at.slice(0, 10))}</p></div>
      </header>
      <dl className="print-facts">
        <div><dt>Customer</dt><dd>{d.customer.name}</dd></div>
        <div><dt>Order</dt><dd>{d.order.number}</dd></div>
        <div><dt>Deliver to</dt><dd className="prewrap">{d.site ? `${d.site.name}\n` : ''}{d.destination || '—'}</dd></div>
        <div><dt>Contact</dt><dd>{d.delivery_contact || '—'}</dd></div>
        <div><dt>Transporter</dt><dd>{[d.transporter, d.vehicle].filter(Boolean).join(', ') || '—'}</dd></div>
      </dl>
      <table className="table">
        <thead><tr><th scope="col">Item</th><th scope="col">Description</th><th scope="col" className="num">Quantity</th><th scope="col">Serial numbers / lot</th></tr></thead>
        <tbody>{d.lines!.map((l) => <tr key={l.id}><td><code>{l.item.code}</code></td><td>{l.item.name}</td><td className="num">{qty(l.qty, l.item.unit_code)}</td><td className="small">{l.serials.join(', ') || (l.lot ? `Lot ${l.lot}` : '')}</td></tr>)}</tbody>
      </table>
      <div className="print-sign">
        <div><p>Received in good condition by</p><span /><p className="small muted">Name, signature, date</p></div>
        <div><p>Delivered by</p><span /><p className="small muted">Name, signature</p></div>
      </div>
      <p className="no-print row"><button className="btn btn-primary" onClick={() => window.print()}>Print</button> <Link to={`/dispatch/${d.id}`}>Back to {d.number}</Link></p>
    </article>
  )
}
