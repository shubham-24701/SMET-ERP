import { useState } from 'react'
import { Link, useNavigate, useParams } from 'react-router'
import { api } from './api'
import { useAction, useApi, useCan, useWhen } from './lib'
import {
  Back, Facts, ItemOptions, LineRows, LocationOptions, Status, day, newKey, num, qty, serialList, useItems, useRows,
  useWarehouses, type Page, type Ref, type Row,
} from './ops'
import { Dialog, Field, FormError, PageHead, PageState, Pager, SelectField, TextareaField } from './ui'

type Material = { item: Ref; per_unit: number; needed: number; reserved_left: number; issued: number; consumed: number; returned: number; on_floor: number; on_floor_lots: { lot: string; qty: number }[]; free: number }
type Order = {
  id: string; number: string; item: Ref; bom: { id: string; version: number }; warehouse: { id: string; code: string }
  qty_planned: number; qty_built: number; qty_passed: number; units_by_status: Record<string, number>; status: string
  source: string; source_ref: string; responsible: string | null; target_date: string | null; notes: string; created_by: string
  created_at: string; version: number; materials?: Material[]; units?: { id: string; serial: string; status: string; created_at: string }[]
  serials_on_floor?: { serial: string; item_id: string }[]; assembly_location?: string
}
type Bom = { id: string; item: Ref; version: number; status: string; notes: string; created_at: string; activated_at: string | null; line_count?: number; lines?: { id: string; component: Ref; qty: number; notes: string }[] }
type Quality = { parameters: { id: string; name: string; unit: string; min_value: number | null; max_value: number | null; required: boolean; confirmed: boolean; active: boolean }[]; checklist: { id: string; text: string; required: boolean; active: boolean }[] }
type Queued = { id: string; serial: string; item: Ref; status: string; location: string | null; assembly_order_id: string | null; created_at: string }

const SOURCE: [string, string][] = [['replenishment', 'Stock replenishment'], ['sales_order', 'Sales order'], ['project', 'Project']]

// ---------- orders ----------

function PlanOrder({ onDone }: { onDone: (o: Order) => void }) {
  const models = useItems('battery_model').filter((i) => i.assembled)
  const { warehouses } = useWarehouses()
  const people = useApi<{ items: { id: string; name: string }[] }>('/assembly-orders/people').data?.items ?? []
  const action = useAction(() => {})
  const [fields, setFields] = useState<Record<string, string>>({})
  return (
    <form noValidate onSubmit={(e) => {
      e.preventDefault()
      const fd = new FormData(e.currentTarget)
      const s = (n: string) => String(fd.get(n) ?? '')
      setFields({})
      void action.run('go', () => api<Order>('/assembly-orders', { method: 'POST', body: {
        item_id: s('item_id'), qty_planned: num(s('qty')), warehouse_id: s('warehouse_id'), source: s('source'), source_ref: s('source_ref'),
        responsible_id: s('responsible_id') || null, target_date: s('target_date') || null, notes: s('notes') } })
        .then(onDone).catch((err) => { setFields(err.fields ?? {}); throw err }))
    }}>
      <div className="grid-fields">
        <SelectField label="Battery model" name="item_id" options={[['', 'Choose a model'], ...models.map((m): [string, string] => [m.id, `${m.code} · ${m.name}`])]} error={fields.item_id} hint="Built to its active bill of materials" />
        <Field label="Batteries to build" name="qty" type="number" min={1} step={1} required error={fields.qty_planned} />
        <SelectField label="Warehouse" name="warehouse_id" options={warehouses.filter((w) => w.active).map((w) => [w.id, `${w.code} · ${w.name}`])} error={fields.warehouse_id} />
        <SelectField label="Why" name="source" options={SOURCE} />
        <Field label="For" name="source_ref" maxLength={120} hint="Sales order or project, if any" />
        <SelectField label="Responsible" name="responsible_id" options={[['', 'Not assigned yet'], ...people.map((p): [string, string] => [p.id, p.name])]} />
        <Field label="Target date" name="target_date" type="date" />
      </div>
      <TextareaField label="Notes" name="notes" maxLength={2000} />
      <FormError message={action.error} />
      <button className="btn btn-primary" disabled={!!action.busy}>Plan order</button>
    </form>
  )
}

const ORDER_CARDS: [string, string][] = [['draft', 'Planned'], ['released', 'Released'], ['in_progress', 'In progress'], ['completed', 'Completed']]

export function AssemblyOrdersPage() {
  const can = useCan()
  const nav = useNavigate()
  const [status, setStatus] = useState('')
  const [offset, setOffset] = useState(0)
  const [adding, setAdding] = useState(false)
  const { data, error } = useApi<Page<Order>>(`/assembly-orders?${new URLSearchParams({ offset: String(offset), limit: '50', ...(status ? { status } : {}) })}`)
  return (
    <>
      <PageHead title="Assembly orders" intro="Plan, reserve components, issue them to the floor, build and serialise each battery, then inspect it.">
        {can('assembly.create') && <button className="btn btn-primary" onClick={() => setAdding(true)}>Plan an order</button>}
      </PageHead>
      {data?.counts && (
        <div className="counts" role="group" aria-label="Show orders by status">
          {ORDER_CARDS.map(([s, l]) => <button key={s} className="count" aria-pressed={status === s} onClick={() => { setStatus(status === s ? '' : s); setOffset(0) }}><span>{l}</span><strong>{data.counts?.[s] ?? 0}</strong></button>)}
        </div>
      )}
      {error ? <PageState kind="error" title="Couldn't load orders" message={error} />
        : !data ? <PageState kind="loading" title="Loading…" />
        : data.items.length === 0 ? <PageState kind="empty" title="No assembly orders here" message="A battery model needs an active bill of materials before it can be planned." />
        : (
          <div className="table-wrap">
            <table className="table stack">
              <thead><tr><th scope="col">Order</th><th scope="col">Battery</th><th scope="col">Progress</th><th scope="col">Target</th><th scope="col">Status</th></tr></thead>
              <tbody>
                {data.items.map((o) => (
                  <tr key={o.id}>
                    <td className="cell-main"><Link to={`/assembly/orders/${o.id}`}>{o.number}</Link><span className="small muted">{o.responsible ?? 'Not assigned'}</span></td>
                    <td className="cell-sub"><code>{o.item.code}</code> · BOM v{o.bom.version}</td>
                    <td className="cell-sub">{o.qty_built} of {o.qty_planned} built, {o.qty_passed} passed</td>
                    <td className="cell-sub nowrap">{day(o.target_date)}</td>
                    <td className="cell-tag"><Status s={o.status} /></td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      {data && <Pager offset={offset} limit={50} total={data.total} onChange={setOffset} />}
      {adding && <Dialog wide title="Plan an assembly order" onClose={() => setAdding(false)}><PlanOrder onDone={(o) => nav(`/assembly/orders/${o.id}`)} /></Dialog>}
    </>
  )
}

function Materials({ order, mode, onDone }: { order: Order; mode: 'issue' | 'return'; onDone: () => void }) {
  const { warehouses } = useWarehouses()
  const [key] = useState(newKey)
  const mats = order.materials!.filter((m) => (mode === 'issue' ? true : m.on_floor > 0))
  const floorSerials = (id: string) => order.serials_on_floor!.filter((s) => s.item_id === id).map((s) => s.serial)
  const [rows, setRows] = useState(mats.map((m) => ({
    qty: String(mode === 'issue' ? Math.max(m.needed - m.issued + m.returned, 0) : m.on_floor),
    lot: mode === 'return' ? m.on_floor_lots[0]?.lot ?? '' : '', serials: mode === 'return' ? floorSerials(m.item.id).join('\n') : '', location_id: '',
  })))
  const action = useAction(onDone)
  const set = (i: number, k: string, v: string) => setRows(rows.map((r, j) => (j === i ? { ...r, [k]: v } : r)))
  return (
    <form noValidate onSubmit={(e) => {
      e.preventDefault()
      const lines = mats.map((m, i) => ({ item_id: m.item.id, qty: num(rows[i].qty), lot: rows[i].lot, serials: serialList(rows[i].serials), location_id: rows[i].location_id || null })).filter((l) => l.qty)
      void action.run('go', () => api(`/assembly-orders/${order.id}/${mode}`, { method: 'POST', body: { key, lines } }))
    }}>
      <p className="hint">{mode === 'issue' ? `From available stock to ${order.assembly_location}. Reserved quantities are used first.` : `From ${order.assembly_location} back to available stock.`}</p>
      <div className="table-wrap">
        <table className="table">
          <thead><tr><th scope="col">Component</th><th scope="col" className="num">{mode === 'issue' ? 'Needed / issued' : 'On the floor'}</th><th scope="col">Quantity</th><th scope="col">{mode === 'issue' ? 'From' : 'To'}</th><th scope="col">Lot / serial numbers</th></tr></thead>
          <tbody>
            {mats.map((m, i) => (
              <tr key={m.item.id}>
                <td><code>{m.item.code}</code><span className="small">{m.item.name}</span></td>
                <td className="num">{mode === 'issue' ? `${qty(m.needed)} / ${qty(m.issued - m.returned)}` : qty(m.on_floor, m.item.unit_code)}</td>
                <td><input aria-label={`${m.item.code} quantity`} type="number" min={0} step="any" value={rows[i].qty} onChange={(e) => set(i, 'qty', e.target.value)} /></td>
                <td><select aria-label={`${m.item.code} location`} value={rows[i].location_id} onChange={(e) => set(i, 'location_id', e.target.value)}><option value="">Main store</option><LocationOptions warehouses={warehouses.filter((w) => w.id === order.warehouse.id)} purpose="available" /></select></td>
                <td>
                  {m.item.tracking === 'lot' && <input aria-label={`${m.item.code} lot`} placeholder="Lot" value={rows[i].lot} onChange={(e) => set(i, 'lot', e.target.value)} />}
                  {m.item.tracking === 'serial' && <textarea aria-label={`${m.item.code} serial numbers`} rows={2} placeholder="One per unit" value={rows[i].serials} onChange={(e) => set(i, 'serials', e.target.value)} />}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <FormError message={action.error} />
      <button className="btn btn-primary" disabled={!!action.busy}>{mode === 'issue' ? 'Issue materials' : 'Return to stock'}</button>
    </form>
  )
}

function BuildBattery({ order, onDone }: { order: Order; onDone: (serial: string) => void }) {
  const [key] = useState(newKey)
  const [serial, setSerial] = useState('')
  const mats = order.materials!
  const floor = (id: string) => order.serials_on_floor!.filter((s) => s.item_id === id).map((s) => s.serial)
  const [rows, setRows] = useState(mats.map((m) => ({
    qty: String(m.per_unit), lot: m.on_floor_lots[0]?.lot ?? '', serials: m.item.tracking === 'serial' ? floor(m.item.id).slice(0, m.per_unit).join('\n') : '',
  })))
  const action = useAction(() => {})
  const set = (i: number, k: string, v: string) => setRows(rows.map((r, j) => (j === i ? { ...r, [k]: v } : r)))
  return (
    <form noValidate onSubmit={(e) => {
      e.preventDefault()
      const parts = mats.map((m, i) => ({ item_id: m.item.id, qty: num(rows[i].qty), lot: rows[i].lot, serials: serialList(rows[i].serials) }))
      void action.run('go', () => api<{ serial: string }>(`/assembly-orders/${order.id}/units`, { method: 'POST', body: { key, serial: serial.trim() || null, parts } }).then((r) => onDone(r.serial)))
    }}>
      <Field label="Serial number" value={serial} onChange={(e) => setSerial(e.target.value.toUpperCase())} maxLength={60} hint="Leave empty for the next SMET number" />
      <div className="table-wrap">
        <table className="table">
          <thead><tr><th scope="col">Component</th><th scope="col" className="num">BOM</th><th scope="col">Used</th><th scope="col">Lot / serial numbers</th></tr></thead>
          <tbody>
            {mats.map((m, i) => (
              <tr key={m.item.id}>
                <td><code>{m.item.code}</code><span className="small">{qty(m.on_floor, m.item.unit_code)} on the floor</span></td>
                <td className="num">{qty(m.per_unit)}</td>
                <td><input aria-label={`${m.item.code} used`} type="number" min={0} step="any" value={rows[i].qty} onChange={(e) => set(i, 'qty', e.target.value)} /></td>
                <td>
                  {m.item.tracking === 'lot' && <select aria-label={`${m.item.code} lot`} value={rows[i].lot} onChange={(e) => set(i, 'lot', e.target.value)}>{m.on_floor_lots.map((l) => <option key={l.lot} value={l.lot}>Lot {l.lot} ({qty(l.qty)})</option>)}</select>}
                  {m.item.tracking === 'serial' && <textarea aria-label={`${m.item.code} serial numbers`} rows={2} value={rows[i].serials} onChange={(e) => set(i, 'serials', e.target.value)} />}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <p className="hint">Record what was actually used; it may differ from the bill of materials. Each component serial is linked to this battery.</p>
      <FormError message={action.error} />
      <button className="btn btn-primary" disabled={!!action.busy}>Record battery</button>
    </form>
  )
}

export function AssemblyOrderPage() {
  const { id } = useParams()
  const can = useCan()
  const nav = useNavigate()
  const when = useWhen()
  const { data: o, error, reload } = useApi<Order>(`/assembly-orders/${id}`)
  const [dialog, setDialog] = useState<'issue' | 'return' | 'build' | 'complete' | 'cancel' | { inspect: Queued } | null>(null)
  const [built, setBuilt] = useState('')
  const action = useAction(reload)
  if (error) return <PageState kind="error" title="Couldn't load the order" message={error} />
  if (!o) return <PageState kind="loading" title="Loading…" />
  const done = () => { setDialog(null); reload() }
  const working = ['released', 'in_progress'].includes(o.status)
  const short = o.materials!.filter((m) => m.free < m.needed)
  return (
    <>
      <Back to="/assembly/orders">Assembly orders</Back>
      <PageHead title={o.number} intro={`${o.qty_planned} × ${o.item.code} · ${o.item.name} · bill of materials v${o.bom.version}`}>
        {o.status === 'draft' && can('assembly.create') && <button className="btn btn-primary" onClick={() => action.run('release', () => api(`/assembly-orders/${o.id}/release`, { method: 'POST', body: { version: o.version } }))}>Release and reserve</button>}
        {working && can('assembly.edit') && <button className="btn btn-secondary" onClick={() => setDialog('issue')}>Issue materials</button>}
        {o.status === 'in_progress' && can('assembly.edit') && o.qty_built < o.qty_planned && <button className="btn btn-primary" onClick={() => setDialog('build')}>Record a battery</button>}
      </PageHead>
      {built && <p className="notice notice-success" role="status">Battery {built} recorded. It waits for inspection.</p>}
      <Facts rows={[['Status', <Status s={o.status} />], ['Built', `${o.qty_built} of ${o.qty_planned}`], ['Passed', String(o.qty_passed)],
        ['Responsible', o.responsible ?? 'Not assigned'], ['Target', day(o.target_date)]]} />
      <FormError message={action.error} />
      {o.status === 'draft' && short.length > 0 && (
        <div className="notice notice-warning">
          <p>Short to release: {short.map((m) => `${m.item.code} (${qty(m.needed - Math.max(m.free, 0))})`).join(', ')}.</p>
          {can('purchasing.create') && <button className="btn btn-secondary" onClick={() => action.run('pr', async () => { const pr = await api<{ id: string }>(`/assembly-orders/${o.id}/shortage-request`, { method: 'POST' }); nav(`/purchasing/requests/${pr.id}`) })}>Request purchase of what is short</button>}
        </div>
      )}
      <h2 className="section">Materials</h2>
      <div className="table-wrap">
        <table className="table">
          <thead><tr><th scope="col">Component</th><th scope="col" className="num">Per battery</th><th scope="col" className="num">Needed</th><th scope="col" className="num">Still reserved</th><th scope="col" className="num">Issued</th><th scope="col" className="num">Used</th><th scope="col" className="num">Returned</th><th scope="col" className="num">On the floor</th></tr></thead>
          <tbody>
            {o.materials!.map((m) => (
              <tr key={m.item.id}>
                <td><code>{m.item.code}</code><span className="small">{m.item.name}</span></td>
                <td className="num">{qty(m.per_unit, m.item.unit_code)}</td><td className="num">{qty(m.needed)}</td><td className="num">{qty(m.reserved_left)}</td>
                <td className="num">{qty(m.issued)}</td><td className="num">{qty(m.consumed)}</td><td className="num">{qty(m.returned)}</td><td className="num"><strong>{qty(m.on_floor)}</strong></td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <h2 className="section">Batteries</h2>
      {o.units!.length === 0 ? <p className="empty-line">None built yet.</p> : (
        <ul className="feed panel panel-wide">
          {o.units!.map((u) => (
            <li key={u.id}>
              <span><Link to={`/stock/serials/${u.id}`}><code>{u.serial}</code></Link><span className="small muted">{when(u.created_at)}</span></span>
              <span className="row-end"><Status s={u.status} />
                {can('quality.create') && ['under_assembly', 'rework', 'on_hold'].includes(u.status) && <button className="link-btn" onClick={() => setDialog({ inspect: { ...u, item: o.item, location: null, assembly_order_id: o.id } })}>Inspect<span className="sr-only"> {u.serial}</span></button>}
              </span>
            </li>
          ))}
        </ul>
      )}
      {working && (
        <div className="row">
          {can('assembly.edit') && o.materials!.some((m) => m.on_floor > 0) && <button className="btn btn-secondary" onClick={() => setDialog('return')}>Return unused materials</button>}
          {can('assembly.create') && <button className="btn btn-secondary" onClick={() => setDialog('complete')}>Complete order</button>}
        </div>
      )}
      {['draft', 'released', 'in_progress'].includes(o.status) && can('assembly.create') && o.qty_built === 0 && <p><button className="btn btn-ghost" onClick={() => setDialog('cancel')}>Cancel order</button></p>}
      {(dialog === 'issue' || dialog === 'return') && <Dialog wide title={dialog === 'issue' ? 'Issue materials' : 'Return unused materials'} onClose={() => setDialog(null)}><Materials order={o} mode={dialog} onDone={done} /></Dialog>}
      {dialog === 'build' && <Dialog wide title="Record a battery" onClose={() => setDialog(null)}><BuildBattery order={o} onDone={(s) => { setBuilt(s); done() }} /></Dialog>}
      {(dialog === 'complete' || dialog === 'cancel') && (
        <Dialog title={dialog === 'complete' ? `Complete ${o.number}` : `Cancel ${o.number}`} onClose={() => setDialog(null)}>
          <form noValidate onSubmit={(e) => { e.preventDefault(); void action.run('close', () => api(`/assembly-orders/${o.id}/${dialog}`, { method: 'POST', body: { version: o.version, reason: new FormData(e.currentTarget).get('reason') } }).then(done)) }}>
            <p className="hint">{dialog === 'complete' ? 'Materials left on the floor must be returned first; remaining reservations are released.' : 'Reservations are released. Return any issued materials first.'}</p>
            <TextareaField label="Reason" name="reason" maxLength={500} hint={dialog === 'complete' ? 'Needed if fewer batteries were built than planned' : 'Needed'} />
            <FormError message={action.error} />
            <button className="btn btn-primary" disabled={!!action.busy}>{dialog === 'complete' ? 'Complete order' : 'Cancel order'}</button>
          </form>
        </Dialog>
      )}
      {dialog && typeof dialog === 'object' && <Dialog wide title={`Inspect ${dialog.inspect.serial}`} onClose={() => setDialog(null)}><Inspect unit={dialog.inspect} onDone={done} /></Dialog>}
    </>
  )
}

// ---------- inspection ----------

export function Inspect({ unit, onDone }: { unit: Queued; onDone: () => void }) {
  const { data: q } = useApi<Quality>(`/items/${unit.item.id}/quality`)
  const { warehouses } = useWarehouses()
  const [key] = useState(newKey)
  const [result, setResult] = useState('passed')
  const [values, setValues] = useState<Record<string, string>>({})
  const [done, setDone] = useState<Record<string, boolean>>({})
  const action = useAction(onDone)
  if (!q) return <PageState kind="loading" title="Loading the checklist…" />
  const params = q.parameters.filter((p) => p.active)
  const checks = q.checklist.filter((c) => c.active)
  const outside = (p: Quality['parameters'][0]) => {
    const v = num(values[p.id])
    return p.confirmed && v !== undefined && ((p.min_value != null && v < p.min_value) || (p.max_value != null && v > p.max_value))
  }
  return (
    <form noValidate onSubmit={(e) => {
      e.preventDefault()
      const fd = new FormData(e.currentTarget)
      void action.run('go', () => api(`/serials/${unit.id}/inspect`, { method: 'POST', body: {
        key, result, observations: fd.get('observations'), location_id: fd.get('location_id') || null,
        readings: params.filter((p) => values[p.id]).map((p) => ({ parameter_id: p.id, value: num(values[p.id]) })),
        checklist: checks.map((c) => ({ item_id: c.id, done: !!done[c.id] })) } }))
    }}>
      {params.length === 0 && checks.length === 0 && <p className="notice notice-warning">No checklist or readings are set up for {unit.item.code}. A battery can't pass until Quality sets them up (Items → Inspection).</p>}
      {params.length > 0 && (
        <fieldset className="lines">
          <legend>Readings</legend>
          {params.map((p) => (
            <div className="line" key={p.id}>
              <label className="line-item"><span className="line-label">{p.name}{p.required ? '' : ' (optional)'}</span>
                <input type="number" step="any" value={values[p.id] ?? ''} onChange={(e) => setValues({ ...values, [p.id]: e.target.value })} aria-invalid={outside(p) || undefined} /></label>
              <span className="small muted limits">{p.unit} · {p.min_value ?? '…'} to {p.max_value ?? '…'}{p.confirmed ? '' : ' · limits not yet confirmed by SMET, not applied'}{outside(p) && <strong className="low"> Outside the limits</strong>}</span>
            </div>
          ))}
        </fieldset>
      )}
      {checks.length > 0 && (
        <fieldset className="checks">
          <legend>Checklist</legend>
          {checks.map((c) => <label key={c.id} className="check"><input type="checkbox" checked={!!done[c.id]} onChange={(e) => setDone({ ...done, [c.id]: e.target.checked })} /> <span>{c.text}{!c.required && <span className="hint">Optional</span>}</span></label>)}
        </fieldset>
      )}
      <fieldset className="checks row-checks">
        <legend>Decision</legend>
        {[['passed', 'Pass: to available stock'], ['on_hold', 'Hold: to quarantine'], ['rework', 'Rework: back to the assembly floor']].map(([v, l]) => (
          <label key={v} className="radio"><input type="radio" name="result" checked={result === v} onChange={() => setResult(v)} /> {l}</label>
        ))}
      </fieldset>
      {result === 'passed' && <div className="field"><label htmlFor="in-loc">Store it in</label><select id="in-loc" name="location_id"><option value="">Main store</option><LocationOptions warehouses={warehouses} purpose="available" /></select></div>}
      <TextareaField label="Observations" name="observations" maxLength={4000} />
      <FormError message={action.error} />
      <button className="btn btn-primary" disabled={!!action.busy}>Record inspection</button>
    </form>
  )
}

export function InspectionPage() {
  const when = useWhen()
  const can = useCan()
  const { data, error, reload } = useApi<Page<Queued>>('/inspections/queue')
  const [unit, setUnit] = useState<Queued | null>(null)
  return (
    <>
      <PageHead title="Final inspection" intro="Batteries built, in rework or on hold. Only a passing inspection puts a battery into available stock." />
      {error ? <PageState kind="error" title="Couldn't load the queue" message={error} />
        : !data ? <PageState kind="loading" title="Loading…" />
        : data.items.length === 0 ? <PageState kind="empty" title="Nothing waiting for inspection" message="Batteries appear here as they are built." />
        : (
          <div className="table-wrap">
            <table className="table stack">
              <thead><tr><th scope="col">Battery</th><th scope="col">Model</th><th scope="col">Where</th><th scope="col">Status</th><th scope="col"><span className="sr-only">Actions</span></th></tr></thead>
              <tbody>
                {data.items.map((u) => (
                  <tr key={u.id}>
                    <td className="cell-main"><Link to={`/stock/serials/${u.id}`}><code>{u.serial}</code></Link><span className="small muted">Built {when(u.created_at)}</span></td>
                    <td className="cell-sub"><code>{u.item.code}</code></td>
                    <td className="cell-sub">{u.location}</td>
                    <td className="cell-tag"><Status s={u.status} /></td>
                    <td className="actions cell-action">{can('quality.create') && <button className="link-btn" onClick={() => setUnit(u)}>Inspect<span className="sr-only"> {u.serial}</span></button>}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      {unit && <Dialog wide title={`Inspect ${unit.serial}`} onClose={() => setUnit(null)}><Inspect unit={unit} onDone={() => { setUnit(null); reload() }} /></Dialog>}
    </>
  )
}

// ---------- bills of materials ----------

function BomForm({ bom, onDone }: { bom: Bom | null; onDone: () => void }) {
  const models = useItems('battery_model').filter((i) => i.assembled)
  const items = useItems()
  const [model, setModel] = useState(bom?.item.id ?? '')
  const [rows, setRows] = useRows(bom?.lines?.map((l): Row => ({ key: newKey(), item_id: l.component.id, qty: String(l.qty) })))
  const action = useAction(onDone)
  const [fields, setFields] = useState<Record<string, string>>({})
  const editing = bom?.status === 'draft'
  return (
    <form noValidate onSubmit={(e) => {
      e.preventDefault()
      const notes = new FormData(e.currentTarget).get('notes')
      const lines = rows.filter((r) => r.item_id).map((r) => ({ component_id: r.item_id, qty: num(r.qty) }))
      setFields({})
      void action.run('go', () => api(editing ? `/boms/${bom!.id}` : '/boms', { method: editing ? 'PUT' : 'POST', body: editing ? { notes, lines } : { item_id: model, notes, lines } })
        .catch((err) => { setFields(err.fields ?? {}); throw err }))
    }}>
      {bom && !editing && <p className="notice">Version {bom.version} is {bom.status}; saving creates a new draft version from it.</p>}
      <div className="field"><label htmlFor="bm-model">Battery model</label>
        <select id="bm-model" value={model} onChange={(e) => setModel(e.target.value)} disabled={!!bom}><option value="">Choose a model</option><ItemOptions items={models} /></select>
        {fields.item_id && <p className="field-error">{fields.item_id}</p>}</div>
      <LineRows label="Components per battery" items={items.filter((i) => i.id !== model)} rows={rows} setRows={setRows} columns={[{ name: 'qty', label: 'Quantity', width: '120px' }]} />
      {fields.lines && <p className="field-error">{fields.lines}</p>}
      <TextareaField label="Notes" name="notes" defaultValue={bom?.notes} maxLength={2000} />
      <FormError message={action.error} />
      <button className="btn btn-primary" disabled={!!action.busy}>{editing ? 'Save draft' : 'Save as new draft version'}</button>
    </form>
  )
}

export function BomsPage() {
  const can = useCan()
  const [dialog, setDialog] = useState<'add' | Bom | null>(null)
  const { data, error, reload } = useApi<Page<Bom>>('/boms?limit=200')
  const [open, setOpen] = useState<string | null>(null)
  const detail = useApi<Bom>(open ? `/boms/${open}` : null)
  const action = useAction(() => { reload(); detail.reload() })
  return (
    <>
      <PageHead title="Bills of materials" intro="What goes into each battery SMET assembles. An active version never changes: changes are a new version, so built batteries keep their recipe.">
        {can('bom.configure') && <button className="btn btn-primary" onClick={() => setDialog('add')}>New bill of materials</button>}
      </PageHead>
      <FormError message={action.error} />
      {error ? <PageState kind="error" title="Couldn't load" message={error} />
        : !data ? <PageState kind="loading" title="Loading…" />
        : data.items.length === 0 ? <PageState kind="empty" title="No bills of materials yet" message="Add a battery model marked “Assembled by SMET” first, then its components." />
        : (
          <div className="table-wrap">
            <table className="table stack">
              <thead><tr><th scope="col">Battery model</th><th scope="col">Version</th><th scope="col">Components</th><th scope="col">Status</th><th scope="col"><span className="sr-only">Actions</span></th></tr></thead>
              <tbody>
                {data.items.map((b) => (
                  <tr key={b.id}>
                    <td className="cell-main"><button className="link-btn" onClick={() => setOpen(open === b.id ? null : b.id)}><code>{b.item.code}</code></button><span className="small">{b.item.name}</span></td>
                    <td className="cell-sub">v{b.version}</td>
                    <td className="cell-sub">{b.line_count}</td>
                    <td className="cell-tag"><Status s={b.status} /></td>
                    <td className="actions cell-action">{can('bom.configure') && <>
                      {b.status === 'draft' && <button className="link-btn" onClick={() => action.run(b.id, () => api(`/boms/${b.id}/activate`, { method: 'POST' }), `Make version ${b.version} of ${b.item.code} active? New assembly orders will use it.`)}>Make active</button>}{' '}
                      <button className="link-btn" onClick={async () => setDialog(await api<Bom>(`/boms/${b.id}`))}>{b.status === 'draft' ? 'Edit' : 'New version'}</button>
                    </>}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      {open && detail.data && (
        <section className="panel panel-wide">
          <h2>{detail.data.item.code} v{detail.data.version}</h2>
          <ul className="feed">{detail.data.lines!.map((l) => <li key={l.id}><span><code>{l.component.code}</code> {l.component.name}</span><span>{qty(l.qty, l.component.unit_code)}</span></li>)}</ul>
        </section>
      )}
      {dialog === 'add' && <Dialog wide title="New bill of materials" onClose={() => setDialog(null)}><BomForm bom={null} onDone={() => { setDialog(null); reload() }} /></Dialog>}
      {dialog && typeof dialog === 'object' && <Dialog wide title={`${dialog.item.code} v${dialog.version}`} onClose={() => setDialog(null)}>
        <BomForm bom={dialog.status === 'draft' ? dialog : { ...dialog, status: dialog.status }} onDone={() => { setDialog(null); reload() }} /></Dialog>}
    </>
  )
}
