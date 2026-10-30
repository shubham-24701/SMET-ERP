import { useState } from 'react'
import { api } from './api'
import { useAction, useApi, useCan } from './lib'
import { KIND, LineRows, PURPOSE, qty, useItems, useWarehouses, type Item, type Loc, type Page, type Row, type Warehouse, newKey, num } from './ops'
import { Dialog, Field, FormError, PageHead, PageState, Pager, SelectField, Tag, TextareaField } from './ui'

type Unit = { code: string; name: string; active: boolean }
type Contact = { name: string; role: string; phone: string; email: string }
type Party = {
  id: string; code: string; name: string; kinds: string[]; contacts: Contact[]; address: string; terms: string
  currency: string; notes: string; active: boolean; version: number
  supplied_items?: { item: Item; supplier_code: string; last_price: number | null; currency: string; lead_days: number | null }[]
}

const fieldsOf = (err: unknown) => (err as { fields?: Record<string, string> }).fields ?? {}
const TRACKING: [string, string][] = [['none', 'Quantity only'], ['lot', 'By lot or batch'], ['serial', 'By serial number, one per unit']]

/** "Name: value" lines ↔ a specification map. */
const specsText = (s: Record<string, string>) => Object.entries(s).map(([k, v]) => `${k}: ${v}`).join('\n')
const parseSpecs = (t: string) => Object.fromEntries(t.split('\n').map((l) => l.split(/:(.*)/s).map((x) => x.trim())).filter(([k, v]) => k && v).map(([k, v]) => [k, v]))

// ---------- items ----------

function ItemForm({ item, units, onDone }: { item: Item | null; units: Unit[]; onDone: () => void }) {
  const [fields, setFields] = useState<Record<string, string>>({})
  const [kind, setKind] = useState(item?.kind ?? 'component')
  const action = useAction(onDone)
  function save(e: React.FormEvent<HTMLFormElement>) {
    e.preventDefault()
    const f = e.currentTarget
    const v = (n: string) => (f.elements.namedItem(n) as HTMLInputElement | null)?.value ?? ''
    const body: Record<string, unknown> = {
      name: v('name'), kind, category: v('category'), unit_code: v('unit_code'), tracking: v('tracking'),
      assembled: kind === 'battery_model' && (f.elements.namedItem('assembled') as HTMLInputElement).checked,
      capacity_ah: num(v('capacity_ah')) ?? null, voltage_v: num(v('voltage_v')) ?? null, chemistry: v('chemistry'),
      min_stock: num(v('min_stock')) ?? 0, notes: v('notes'), specs: parseSpecs(v('specs')),
    }
    if (item) Object.assign(body, { version: item.version, active: (f.elements.namedItem('active') as HTMLInputElement).checked, reason: v('reason') })
    else body.code = v('code')
    setFields({})
    void action.run('save', () => api(item ? `/items/${item.id}` : '/items', { method: item ? 'PATCH' : 'POST', body })
      .catch((err) => { setFields(fieldsOf(err)); throw err }))
  }
  const battery = kind === 'battery_model' || kind === 'battery_unit'
  return (
    <form onSubmit={save} noValidate>
      <div className="grid-fields">
        {item ? <Field label="Code" value={item.code} disabled hint="Codes never change: documents refer to them." /> : <Field label="Code" name="code" required maxLength={40} error={fields.code} hint="Letters, digits, - . / _ " />}
        <Field label="Name" name="name" defaultValue={item?.name} required maxLength={160} error={fields.name} />
        <SelectField label="Kind" name="kind" value={kind} onChange={(e) => setKind(e.target.value)} options={Object.entries(KIND)} error={fields.kind} />
        <Field label="Category" name="category" defaultValue={item?.category} maxLength={60} hint="For example UPS, telecom, solar" />
        <SelectField label="Unit" name="unit_code" defaultValue={item?.unit_code ?? 'PCS'} options={units.filter((u) => u.active).map((u) => [u.code, `${u.name} (${u.code})`])} error={fields.unit_code} />
        <SelectField label="Tracking" name="tracking" defaultValue={item?.tracking ?? 'none'} options={TRACKING} error={fields.tracking} />
        {battery && <>
          <Field label="Capacity (Ah)" name="capacity_ah" type="number" step="any" min={0} defaultValue={item?.capacity_ah ?? ''} error={fields.capacity_ah} />
          <Field label="Voltage (V)" name="voltage_v" type="number" step="any" min={0} defaultValue={item?.voltage_v ?? ''} error={fields.voltage_v} />
          <Field label="Chemistry" name="chemistry" defaultValue={item?.chemistry} maxLength={40} hint="For example LiFePO4, VRLA" />
        </>}
        <Field label="Minimum stock" name="min_stock" type="number" step="any" min={0} defaultValue={item?.min_stock ?? 0} error={fields.min_stock} hint="Below this, the item shows as low and a purchase can be raised. 0 = no alert." />
      </div>
      {kind === 'battery_model' && (
        <label className="check"><input type="checkbox" name="assembled" defaultChecked={item?.assembled} />
          <span>Assembled by SMET<span className="hint">Built from a bill of materials, inspected, serialised. Needs tracking by serial number.</span></span></label>
      )}
      {fields.assembled && <p className="field-error">{fields.assembled}</p>}
      <TextareaField label="Other specifications" name="specs" rows={3} defaultValue={specsText(item?.specs ?? {})} hint="One per line, as Name: value (for example Cycle life: 6000)" />
      <TextareaField label="Notes" name="notes" defaultValue={item?.notes} maxLength={4000} />
      {item && <>
        <label className="check"><input type="checkbox" name="active" defaultChecked={item.active} /> <span>Active<span className="hint">Inactive items can't be used on new documents; their history stays.</span></span></label>
        <TextareaField label="Reason for the change" name="reason" maxLength={500} hint="Kept in the audit log." />
      </>}
      <FormError message={action.error} />
      <button className="btn btn-primary" disabled={!!action.busy}>{action.busy ? 'Saving…' : item ? 'Save item' : 'Add item'}</button>
    </form>
  )
}

type Param = { id?: string; name: string; unit: string; min_value: number | null; max_value: number | null; required: boolean; confirmed: boolean; active: boolean }
type Check = { id?: string; text: string; required: boolean; active: boolean }

/** Final inspection set-up for an assembled model: readings with limits SMET confirms, and a checklist. */
export function QualitySetup({ item, onDone }: { item: Item; onDone: () => void }) {
  const can = useCan()
  const { data, error } = useApi<{ parameters: Param[]; checklist: Check[] }>(`/items/${item.id}/quality`)
  if (error) return <PageState kind="error" title="Couldn't load the inspection set-up" message={error} />
  if (!data) return <PageState kind="loading" title="Loading…" />
  return <QualityForm key={item.id} item={item} initial={data} readOnly={!can('quality.configure')} onDone={onDone} />
}

function QualityForm({ item, initial, readOnly, onDone }: { item: Item; initial: { parameters: Param[]; checklist: Check[] }; readOnly: boolean; onDone: () => void }) {
  const [params, setParams] = useState<Param[]>(initial.parameters.filter((p) => p.active))
  const [checks, setChecks] = useState<Check[]>(initial.checklist.filter((c) => c.active))
  const [reason, setReason] = useState('')
  const action = useAction(onDone)
  const setP = (i: number, patch: Partial<Param>) => setParams(params.map((p, j) => (j === i ? { ...p, ...patch } : p)))
  const setC = (i: number, patch: Partial<Check>) => setChecks(checks.map((c, j) => (j === i ? { ...c, ...patch } : c)))
  return (
    <form onSubmit={(e) => { e.preventDefault(); void action.run('save', () => api(`/items/${item.id}/quality`, { method: 'PUT', body: { parameters: params, checklist: checks, reason } })) }}>
      <p className="muted">A battery passes only when every required check is done, every required reading is taken, and each reading is within limits that SMET has confirmed. A limit not yet confirmed is shown to the inspector but not applied.</p>
      <fieldset className="plain" disabled={readOnly}>
        <h3 className="sub">Readings</h3>
        {params.length === 0 && <p className="empty-line">No readings yet.</p>}
        {params.map((p, i) => (
          <div className="line" key={p.id ?? i}>
            <label className="line-item"><span className="line-label">Reading</span><input value={p.name} onChange={(e) => setP(i, { name: e.target.value })} required maxLength={80} /></label>
            <label style={{ inlineSize: 80 }}><span className="line-label">Unit</span><input value={p.unit} onChange={(e) => setP(i, { unit: e.target.value })} maxLength={20} /></label>
            <label style={{ inlineSize: 100 }}><span className="line-label">Lowest</span><input type="number" step="any" value={p.min_value ?? ''} onChange={(e) => setP(i, { min_value: num(e.target.value) ?? null })} /></label>
            <label style={{ inlineSize: 100 }}><span className="line-label">Highest</span><input type="number" step="any" value={p.max_value ?? ''} onChange={(e) => setP(i, { max_value: num(e.target.value) ?? null })} /></label>
            <label className="check inline"><input type="checkbox" checked={p.confirmed} onChange={(e) => setP(i, { confirmed: e.target.checked })} /> <span>Limits confirmed by SMET</span></label>
            <label className="check inline"><input type="checkbox" checked={p.required} onChange={(e) => setP(i, { required: e.target.checked })} /> <span>Required</span></label>
            <button type="button" className="btn btn-ghost" onClick={() => setParams(params.filter((_, j) => j !== i))}>Remove</button>
          </div>
        ))}
        {!readOnly && <button type="button" className="btn btn-secondary" onClick={() => setParams([...params, { name: '', unit: '', min_value: null, max_value: null, required: true, confirmed: false, active: true }])}>Add reading</button>}
        <h3 className="sub">Checklist</h3>
        {checks.length === 0 && <p className="empty-line">No checks yet.</p>}
        {checks.map((c, i) => (
          <div className="line" key={c.id ?? i}>
            <label className="line-item"><span className="line-label">Check</span><input value={c.text} onChange={(e) => setC(i, { text: e.target.value })} required maxLength={200} /></label>
            <label className="check inline"><input type="checkbox" checked={c.required} onChange={(e) => setC(i, { required: e.target.checked })} /> <span>Required</span></label>
            <button type="button" className="btn btn-ghost" onClick={() => setChecks(checks.filter((_, j) => j !== i))}>Remove</button>
          </div>
        ))}
        {!readOnly && <button type="button" className="btn btn-secondary" onClick={() => setChecks([...checks, { text: '', required: true, active: true }])}>Add check</button>}
        {!readOnly && <TextareaField label="Reason for the change" value={reason} onChange={(e) => setReason(e.target.value)} maxLength={500} hint="Kept in the audit log with the old and new limits." />}
      </fieldset>
      <FormError message={action.error} />
      {!readOnly && <button className="btn btn-primary" disabled={!!action.busy}>{action.busy ? 'Saving…' : 'Save inspection set-up'}</button>}
    </form>
  )
}

function UnitsDialog({ onClose }: { onClose: () => void }) {
  const { data, reload } = useApi<{ items: Unit[] }>('/units')
  const [fields, setFields] = useState<Record<string, string>>({})
  const action = useAction(reload)
  return (
    <Dialog title="Units of measure" onClose={onClose}>
      <ul className="feed">{data?.items.map((u) => <li key={u.code}><span>{u.name}</span><code>{u.code}</code></li>)}</ul>
      <form className="row" onSubmit={(e) => {
        e.preventDefault()
        const f = e.currentTarget
        setFields({})
        void action.run('add', () => api('/units', { method: 'POST', body: { code: f.code.value, name: f.unitname.value } }).then(() => f.reset())
          .catch((err) => { setFields(fieldsOf(err)); throw err }))
      }}>
        <Field label="Code" name="code" maxLength={12} required error={fields.code} />
        <Field label="Name" name="unitname" maxLength={40} required error={fields.name} />
        <button className="btn btn-secondary">Add unit</button>
      </form>
      <FormError message={action.error} />
    </Dialog>
  )
}

export function ItemsPage() {
  const can = useCan()
  const [q, setQ] = useState('')
  const [kind, setKind] = useState('')
  const [active, setActive] = useState('true')
  const [offset, setOffset] = useState(0)
  const [dialog, setDialog] = useState<'add' | 'units' | { edit: Item } | { quality: Item } | null>(null)
  const params = new URLSearchParams({ q, offset: String(offset), limit: '50', ...(kind ? { kind } : {}), ...(active ? { active } : {}) })
  const { data, error, reload } = useApi<Page<Item>>(`/items?${params}`)
  const units = useApi<{ items: Unit[] }>('/units').data?.items ?? []
  const close = () => { setDialog(null); reload() }
  return (
    <>
      <PageHead title="Items" intro="Battery models, purchased battery units, components, accessories and assembly materials.">
        {can('items.configure') && <button className="btn btn-secondary" onClick={() => setDialog('units')}>Units</button>}
        {can('items.edit') && <button className="btn btn-primary" onClick={() => setDialog('add')}>Add item</button>}
      </PageHead>
      <div className="toolbar" role="search">
        <Field label="Search code, name or category" type="search" value={q} onChange={(e) => { setQ(e.target.value); setOffset(0) }} />
        <SelectField label="Kind" value={kind} onChange={(e) => { setKind(e.target.value); setOffset(0) }} options={[['', 'All kinds'], ...Object.entries(KIND)]} />
        <SelectField label="Status" value={active} onChange={(e) => { setActive(e.target.value); setOffset(0) }} options={[['true', 'Active'], ['false', 'Inactive'], ['', 'All']]} />
      </div>
      {error ? <PageState kind="error" title="Couldn't load items" message={error} action={<button className="btn btn-secondary" onClick={reload}>Retry</button>} />
        : !data ? <PageState kind="loading" title="Loading items…" />
        : data.items.length === 0 ? <PageState kind="empty" title={q || kind ? 'Nothing matches' : 'No items yet'} message={q || kind ? 'Clear the search to see all items.' : 'Add the battery models and components SMET stocks.'} />
        : (
          <div className="table-wrap">
            <table className="table stack">
              <thead><tr><th scope="col">Item</th><th scope="col">Kind</th><th scope="col">Specification</th><th scope="col">Tracking</th><th scope="col" className="num">Minimum</th><th scope="col"><span className="sr-only">Actions</span></th></tr></thead>
              <tbody>
                {data.items.map((i) => (
                  <tr key={i.id}>
                    <td className="cell-main"><code>{i.code}</code><span className="small">{i.name}</span></td>
                    <td className="cell-tag">{KIND[i.kind]}{i.assembled && <span className="muted small">Assembled by SMET</span>}</td>
                    <td className="cell-sub">{[i.capacity_ah && `${i.capacity_ah} Ah`, i.voltage_v && `${i.voltage_v} V`, i.chemistry, i.category].filter(Boolean).join(', ') || <span className="muted">—</span>}</td>
                    <td className="cell-sub">{TRACKING.find(([k]) => k === i.tracking)?.[1]} · {i.unit_code}</td>
                    <td className="num cell-sub">{i.min_stock ? qty(i.min_stock) : '—'}</td>
                    <td className="actions cell-action">
                      {i.assembled && <button className="link-btn" onClick={() => setDialog({ quality: i })}>Inspection<span className="sr-only"> set-up for {i.code}</span></button>}{' '}
                      {can('items.edit') && <button className="link-btn" onClick={() => setDialog({ edit: i })}>Edit<span className="sr-only"> {i.code}</span></button>}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      {data && <Pager offset={offset} limit={50} total={data.total} onChange={setOffset} />}
      {dialog === 'add' && <Dialog wide title="Add item" onClose={() => setDialog(null)}><ItemForm item={null} units={units} onDone={close} /></Dialog>}
      {dialog === 'units' && <UnitsDialog onClose={() => setDialog(null)} />}
      {dialog && typeof dialog === 'object' && 'edit' in dialog && <Dialog wide title={`Edit ${dialog.edit.code}`} onClose={() => setDialog(null)}><ItemForm item={dialog.edit} units={units} onDone={close} /></Dialog>}
      {dialog && typeof dialog === 'object' && 'quality' in dialog && <Dialog wide title={`Final inspection: ${dialog.quality.code}`} onClose={() => setDialog(null)}><QualitySetup item={dialog.quality} onDone={close} /></Dialog>}
    </>
  )
}

// ---------- suppliers and customers ----------

const PARTY_KINDS: [string, string][] = [['supplier', 'Supplier'], ['customer', 'Customer'], ['distributor', 'Distributor'], ['dealer', 'Dealer']]

function PartyForm({ party, onDone }: { party: Party | null; onDone: () => void }) {
  const [fields, setFields] = useState<Record<string, string>>({})
  const [contacts, setContacts] = useState<Contact[]>(party?.contacts ?? [])
  const action = useAction(onDone)
  function save(e: React.FormEvent<HTMLFormElement>) {
    e.preventDefault()
    const f = e.currentTarget
    const fd = new FormData(f)
    const body: Record<string, unknown> = {
      name: fd.get('name'), kinds: fd.getAll('kinds'), currency: fd.get('currency'), address: fd.get('address'),
      terms: fd.get('terms'), notes: fd.get('notes'), contacts: contacts.filter((c) => c.name.trim()),
    }
    if (party) Object.assign(body, { version: party.version, active: fd.get('active') === 'on', reason: fd.get('reason') })
    else body.code = fd.get('code')
    setFields({})
    void action.run('save', () => api(party ? `/parties/${party.id}` : '/parties', { method: party ? 'PATCH' : 'POST', body })
      .catch((err) => { setFields(fieldsOf(err)); throw err }))
  }
  const setC = (i: number, k: keyof Contact, v: string) => setContacts(contacts.map((c, j) => (j === i ? { ...c, [k]: v } : c)))
  return (
    <form onSubmit={save} noValidate>
      <div className="grid-fields">
        {party ? <Field label="Code" value={party.code} disabled /> : <Field label="Code" name="code" required maxLength={20} error={fields.code} />}
        <Field label="Name" name="name" defaultValue={party?.name} required maxLength={160} error={fields.name} />
        <Field label="Currency" name="currency" defaultValue={party?.currency ?? 'AED'} pattern="[A-Z]{3}" maxLength={3} error={fields.currency} hint="Used on their documents, e.g. AED, USD, CNY" />
      </div>
      <fieldset className="checks row-checks">
        <legend>This company is a</legend>
        {PARTY_KINDS.map(([k, l]) => <label key={k} className="check"><input type="checkbox" name="kinds" value={k} defaultChecked={party?.kinds.includes(k)} /> <span>{l}</span></label>)}
      </fieldset>
      {fields.kinds && <p className="field-error">Choose at least one</p>}
      <TextareaField label="Address" name="address" defaultValue={party?.address} maxLength={4000} />
      <TextareaField label="Terms" name="terms" defaultValue={party?.terms} maxLength={4000} hint="Purchase or commercial terms; copied onto new purchase orders" />
      <fieldset className="lines">
        <legend>Contacts</legend>
        {contacts.map((c, i) => (
          <div className="line" key={i}>
            {(['name', 'role', 'phone', 'email'] as const).map((k) => (
              <label key={k} className={k === 'name' ? 'line-item' : undefined}><span className="line-label">{k[0].toUpperCase() + k.slice(1)}</span>
                <input value={c[k]} type={k === 'email' ? 'email' : 'text'} onChange={(e) => setC(i, k, e.target.value)} /></label>
            ))}
            <button type="button" className="btn btn-ghost" onClick={() => setContacts(contacts.filter((_, j) => j !== i))}>Remove</button>
          </div>
        ))}
        <button type="button" className="btn btn-secondary" onClick={() => setContacts([...contacts, { name: '', role: '', phone: '', email: '' }])}>Add contact</button>
      </fieldset>
      {fields.contacts && <p className="field-error">{fields.contacts}</p>}
      <TextareaField label="Notes" name="notes" defaultValue={party?.notes} maxLength={4000} />
      {party && <>
        <label className="check"><input type="checkbox" name="active" defaultChecked={party.active} /> <span>Active</span></label>
        <TextareaField label="Reason for the change" name="reason" maxLength={500} />
      </>}
      <FormError message={action.error} />
      <button className="btn btn-primary" disabled={!!action.busy}>{action.busy ? 'Saving…' : party ? 'Save' : 'Add'}</button>
    </form>
  )
}

function SuppliedItems({ party, onDone }: { party: Party; onDone: () => void }) {
  const can = useCan()
  const items = useItems()
  const { data } = useApi<Party>(`/parties/${party.id}`)
  const [rows, setRows] = useState<Row[] | null>(null)
  const action = useAction(onDone)
  const current = rows ?? data?.supplied_items?.map((s) => ({ key: newKey(), item_id: s.item.id, supplier_code: s.supplier_code, last_price: String(s.last_price ?? ''), lead_days: String(s.lead_days ?? '') })) ?? []
  if (!data) return <PageState kind="loading" title="Loading…" />
  return (
    <form onSubmit={(e) => {
      e.preventDefault()
      void action.run('save', () => api(`/parties/${party.id}/items`, { method: 'PUT', body: current.filter((r) => r.item_id).map((r) => ({
        item_id: r.item_id, supplier_code: r.supplier_code ?? '', last_price: num(r.last_price) ?? null, currency: party.currency, lead_days: num(r.lead_days) ?? null })) }))
    }}>
      <fieldset className="plain" disabled={!can('parties.edit')}>
        <LineRows label="Items this supplier supplies" items={items} rows={current} setRows={setRows}
          columns={[{ name: 'supplier_code', label: 'Their code', type: 'text', width: '140px' }, { name: 'last_price', label: `Last price (${party.currency})`, width: '130px' }, { name: 'lead_days', label: 'Lead time (days)', step: '1', width: '120px' }]} />
      </fieldset>
      <FormError message={action.error} />
      {can('parties.edit') && <button className="btn btn-primary" disabled={!!action.busy}>Save supplied items</button>}
    </form>
  )
}

export function PartiesPage() {
  const can = useCan()
  const [q, setQ] = useState('')
  const [kind, setKind] = useState('')
  const [offset, setOffset] = useState(0)
  const [dialog, setDialog] = useState<'add' | { edit: Party } | { supplied: Party } | null>(null)
  const params = new URLSearchParams({ q, offset: String(offset), limit: '50', ...(kind ? { kind } : {}) })
  const { data, error, reload } = useApi<Page<Party>>(`/parties?${params}`)
  const close = () => { setDialog(null); reload() }
  return (
    <>
      <PageHead title="Suppliers & customers" intro="Suppliers, customers, distributors and dealers, with their contacts and terms.">
        {can('parties.edit') && <button className="btn btn-primary" onClick={() => setDialog('add')}>Add company</button>}
      </PageHead>
      <div className="toolbar" role="search">
        <Field label="Search code or name" type="search" value={q} onChange={(e) => { setQ(e.target.value); setOffset(0) }} />
        <SelectField label="Show" value={kind} onChange={(e) => { setKind(e.target.value); setOffset(0) }} options={[['', 'Everyone'], ...PARTY_KINDS.map(([k, l]): [string, string] => [k, `${l}s`])]} />
      </div>
      {error ? <PageState kind="error" title="Couldn't load" message={error} action={<button className="btn btn-secondary" onClick={reload}>Retry</button>} />
        : !data ? <PageState kind="loading" title="Loading…" />
        : data.items.length === 0 ? <PageState kind="empty" title={q || kind ? 'Nothing matches' : 'No companies yet'} message={q || kind ? 'Clear the search.' : 'Add your suppliers first: purchase orders need them.'} />
        : (
          <div className="table-wrap">
            <table className="table stack">
              <thead><tr><th scope="col">Company</th><th scope="col">Is a</th><th scope="col">Main contact</th><th scope="col">Currency</th><th scope="col"><span className="sr-only">Actions</span></th></tr></thead>
              <tbody>
                {data.items.map((x) => (
                  <tr key={x.id}>
                    <td className="cell-main">{x.name}<span className="small muted"><code>{x.code}</code>{!x.active && ' · inactive'}</span></td>
                    <td className="cell-tag">{x.kinds.map((k) => <Tag key={k}>{PARTY_KINDS.find(([v]) => v === k)?.[1]}</Tag>)}</td>
                    <td className="cell-sub">{x.contacts[0] ? `${x.contacts[0].name}${x.contacts[0].phone ? `, ${x.contacts[0].phone}` : ''}` : <span className="muted">—</span>}</td>
                    <td className="cell-sub">{x.currency}</td>
                    <td className="actions cell-action">
                      {x.kinds.includes('supplier') && <button className="link-btn" onClick={() => setDialog({ supplied: x })}>Supplied items<span className="sr-only"> of {x.name}</span></button>}{' '}
                      {can('parties.edit') && <button className="link-btn" onClick={() => setDialog({ edit: x })}>Edit<span className="sr-only"> {x.name}</span></button>}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      {data && <Pager offset={offset} limit={50} total={data.total} onChange={setOffset} />}
      {dialog === 'add' && <Dialog wide title="Add company" onClose={() => setDialog(null)}><PartyForm party={null} onDone={close} /></Dialog>}
      {dialog && typeof dialog === 'object' && 'edit' in dialog && <Dialog wide title={`Edit ${dialog.edit.name}`} onClose={() => setDialog(null)}><PartyForm party={dialog.edit} onDone={close} /></Dialog>}
      {dialog && typeof dialog === 'object' && 'supplied' in dialog && <Dialog wide title={`${dialog.supplied.name}: supplied items`} onClose={() => setDialog(null)}><SuppliedItems party={dialog.supplied} onDone={close} /></Dialog>}
    </>
  )
}

// ---------- warehouses ----------

function LocationForm({ warehouse, loc, onDone }: { warehouse: Warehouse; loc: Loc | null; onDone: () => void }) {
  const [fields, setFields] = useState<Record<string, string>>({})
  const action = useAction(onDone)
  return (
    <form noValidate onSubmit={(e) => {
      e.preventDefault()
      const fd = new FormData(e.currentTarget)
      const body = loc ? { version: loc.version, name: fd.get('name'), purpose: fd.get('purpose'), active: fd.get('active') === 'on' }
        : { code: fd.get('code'), name: fd.get('name'), purpose: fd.get('purpose') }
      setFields({})
      void action.run('save', () => api(loc ? `/locations/${loc.id}` : `/warehouses/${warehouse.id}/locations`, { method: loc ? 'PATCH' : 'POST', body })
        .catch((err) => { setFields(fieldsOf(err)); throw err }))
    }}>
      {loc ? <Field label="Code" value={loc.code} disabled /> : <Field label="Code" name="code" required maxLength={20} error={fields.code} hint="For example RACK-A1" />}
      <Field label="Name" name="name" defaultValue={loc?.name} required maxLength={120} error={fields.name} />
      <SelectField label="Use" name="purpose" defaultValue={loc?.purpose ?? 'available'} options={Object.entries(PURPOSE)} hint="What sits here counts with this status. Only available stock can be reserved, issued or sold." />
      {loc && <label className="check"><input type="checkbox" name="active" defaultChecked={loc.active} /> <span>Active</span></label>}
      <FormError message={action.error} />
      <button className="btn btn-primary" disabled={!!action.busy}>{loc ? 'Save location' : 'Add location'}</button>
    </form>
  )
}

export function WarehousesPage() {
  const can = useCan()
  const { warehouses, loaded, reload } = useWarehouses()
  const [dialog, setDialog] = useState<'add' | { loc: Loc | null; wh: Warehouse } | null>(null)
  const [fields, setFields] = useState<Record<string, string>>({})
  const action = useAction(() => { setDialog(null); reload() })
  const close = () => { setDialog(null); reload() }
  return (
    <>
      <PageHead title="Warehouses" intro="Each warehouse has areas for each stock status. Add racks and bays as available-stock locations.">
        {can('warehouses.configure') && <button className="btn btn-primary" onClick={() => setDialog('add')}>Add warehouse</button>}
      </PageHead>
      {!loaded ? <PageState kind="loading" title="Loading…" />
        : warehouses.length === 0 ? <PageState kind="empty" title="No warehouses yet" message="Add the first warehouse; its standard areas are created with it." />
        : warehouses.map((w) => (
          <section key={w.id} className="panel panel-wide">
            <div className="panel-head">
              <h2>{w.name} <code className="muted">{w.code}</code></h2>
              {can('warehouses.configure') && <button className="btn btn-secondary" onClick={() => setDialog({ loc: null, wh: w })}>Add location</button>}
            </div>
            <table className="table stack">
              <thead><tr><th scope="col">Location</th><th scope="col">Use</th><th scope="col"><span className="sr-only">Actions</span></th></tr></thead>
              <tbody>
                {w.locations.map((l) => (
                  <tr key={l.id}>
                    <td className="cell-main"><code>{l.code}</code> <span className="small">{l.name}{!l.active && ' · inactive'}</span></td>
                    <td className="cell-sub">{PURPOSE[l.purpose]}</td>
                    <td className="actions cell-action">{can('warehouses.configure') && <button className="link-btn" onClick={() => setDialog({ loc: l, wh: w })}>Edit<span className="sr-only"> {l.code}</span></button>}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </section>
        ))}
      {dialog === 'add' && (
        <Dialog title="Add warehouse" onClose={() => setDialog(null)}>
          <form noValidate onSubmit={(e) => {
            e.preventDefault()
            const fd = new FormData(e.currentTarget)
            setFields({})
            void action.run('add', () => api('/warehouses', { method: 'POST', body: { code: fd.get('code'), name: fd.get('name'), address: fd.get('address') } })
              .catch((err) => { setFields(fieldsOf(err)); throw err }))
          }}>
            <Field label="Code" name="code" required maxLength={20} error={fields.code} hint="For example DXB-MAIN" />
            <Field label="Name" name="name" required maxLength={120} error={fields.name} />
            <TextareaField label="Address" name="address" maxLength={4000} />
            <p className="hint">Created with a main store, receiving inspection, quarantine, rejected goods and an assembly floor.</p>
            <FormError message={action.error} />
            <button className="btn btn-primary" disabled={!!action.busy}>Add warehouse</button>
          </form>
        </Dialog>
      )}
      {dialog && dialog !== 'add' && <Dialog title={dialog.loc ? `Edit ${dialog.wh.code}/${dialog.loc.code}` : `Add a location to ${dialog.wh.code}`} onClose={() => setDialog(null)}><LocationForm warehouse={dialog.wh} loc={dialog.loc} onDone={close} /></Dialog>}
    </>
  )
}

// ---------- business rules and document numbering ----------

type Rules = { po_auto_approve_up_to: string | number | null; po_self_approval: boolean; adjustment_self_approval: boolean
  install_photos_min: number; commission_photos_min: number; handover_photos_min: number; handover_with_open_issues: boolean
  maintenance_photos_min: number; service_photos_min: number; warranty_self_approval: boolean; public_warranty_dates: boolean
  amc_renewal_notice_days: number; code_retention_days: number }
type Seq = { doc_type: string; prefix: string; padding: number; yearly: boolean; next: string }
const DOCS: Record<string, string> = {
  purchase_request: 'Purchase requests', supplier_quote: 'Supplier quotations', purchase_order: 'Purchase orders',
  goods_receipt: 'Goods receipts', supplier_return: 'Returns to suppliers', adjustment: 'Stock corrections',
  stock_count: 'Stock counts', assembly_order: 'Assembly orders', serial: 'Battery serial numbers',
  enquiry: 'Enquiries', quotation: 'Quotations', sales_order: 'Sales orders', project: 'Projects', dispatch: 'Dispatches',
  battery_bank: 'Battery banks', site_visit: 'Site visits', warranty: 'Warranty registrations', amc: 'Maintenance contracts',
  service_request: 'Service requests', maintenance_alert: 'Maintenance alerts',
}

export function RulesPage() {
  const can = useCan()
  const edit = can('settings.configure')
  const rules = useApi<{ rules: Rules; version: number }>('/rules')
  const seqs = useApi<{ items: Seq[] }>('/numbering')
  const action = useAction(() => { rules.reload(); seqs.reload() })
  const [fields, setFields] = useState<Record<string, string>>({})
  if (!rules.data || !seqs.data) return <PageState kind="loading" title="Loading…" />
  const r = rules.data.rules
  return (
    <>
      <PageHead title="Rules & numbering" intro="Business rules SMET can change without a software change. Each change is audited." />
      <form className="settings-row" noValidate onSubmit={(e) => {
        e.preventDefault()
        const fd = new FormData(e.currentTarget)
        const limit = String(fd.get('limit') ?? '')
        setFields({})
        void action.run('rules', () => api('/rules', { method: 'PATCH', body: { version: rules.data!.version, po_auto_approve_up_to: limit === '' ? null : Number(limit),
          po_self_approval: fd.get('po_self') === 'on', adjustment_self_approval: fd.get('adj_self') === 'on', reason: fd.get('reason') } })
          .catch((err) => { setFields(fieldsOf(err)); throw err }))
      }}>
        <div><h2>Approvals</h2><p className="muted">Who must approve what. The defaults are the cautious choice until SMET decides (open questions 6 and 9).</p></div>
        <fieldset className="settings-panel plain" disabled={!edit}>
          <Field label="Approve purchase orders automatically up to" name="limit" type="number" min={0} step="0.01" defaultValue={r.po_auto_approve_up_to ?? ''} error={fields.po_auto_approve_up_to}
            hint="In the company currency. Empty: every order needs an approver. Orders in other currencies always need one." />
          <label className="check"><input type="checkbox" name="po_self" defaultChecked={r.po_self_approval} /> <span>The person who raised a purchase order may approve it</span></label>
          <label className="check"><input type="checkbox" name="adj_self" defaultChecked={r.adjustment_self_approval} /> <span>The person who asked for a stock correction may approve it</span></label>
          <TextareaField label="Reason for the change" name="reason" maxLength={500} />
          <FormError message={action.busy === 'rules' ? '' : action.error} />
          {edit && <button className="btn btn-primary" disabled={!!action.busy}>Save rules</button>}
        </fieldset>
      </form>
      <form className="settings-row" noValidate onSubmit={(e) => {
        e.preventDefault()
        const fd = new FormData(e.currentTarget)
        const n = (k: string) => Number(fd.get(k) ?? 0)
        setFields({})
        void action.run('evidence', () => api('/rules', { method: 'PATCH', body: { version: rules.data!.version, install_photos_min: n('install'), commission_photos_min: n('commission'),
          handover_photos_min: n('handover'), handover_with_open_issues: fd.get('open_issues') === 'on', reason: fd.get('reason') } })
          .catch((err) => { setFields(fieldsOf(err)); throw err }))
      }}>
        <div><h2>Installation evidence</h2><p className="muted">What a visit needs before it can be completed, besides SMET's site checks (open questions 16 and 31).</p></div>
        <fieldset className="settings-panel plain" disabled={!edit}>
          <div className="grid-fields">
            <Field label="Photographs at installation" name="install" type="number" min={0} max={20} defaultValue={r.install_photos_min} error={fields.install_photos_min} />
            <Field label="At commissioning" name="commission" type="number" min={0} max={20} defaultValue={r.commission_photos_min} error={fields.commission_photos_min} />
            <Field label="At handover" name="handover" type="number" min={0} max={20} defaultValue={r.handover_photos_min} error={fields.handover_photos_min} />
          </div>
          <label className="check"><input type="checkbox" name="open_issues" defaultChecked={r.handover_with_open_issues} /> <span>A handover may complete with issues still open on its visit</span></label>
          <TextareaField label="Reason for the change" name="reason" maxLength={500} />
          {edit && <button className="btn btn-primary" disabled={!!action.busy}>Save evidence rules</button>}
        </fieldset>
      </form>
      <form className="settings-row" noValidate onSubmit={(e) => {
        e.preventDefault()
        const fd = new FormData(e.currentTarget)
        const n = (k: string) => Number(fd.get(k) ?? 0)
        setFields({})
        void action.run('aftersales', () => api('/rules', { method: 'PATCH', body: { version: rules.data!.version, warranty_self_approval: fd.get('self') === 'on',
          public_warranty_dates: fd.get('dates') === 'on', amc_renewal_notice_days: n('notice'), code_retention_days: n('retention'),
          maintenance_photos_min: n('mphotos'), service_photos_min: n('sphotos'), reason: fd.get('reason') } })
          .catch((err) => { setFields(fieldsOf(err)); throw err }))
      }}>
        <div><h2>After-sales</h2><p className="muted">Warranty decisions, what the public check shows, renewal reminders and how long reset codes are kept (open questions 17–22).</p></div>
        <fieldset className="settings-panel plain" disabled={!edit}>
          <label className="check"><input type="checkbox" name="self" defaultChecked={r.warranty_self_approval} /> <span>Whoever submitted a warranty registration may approve it</span></label>
          <label className="check"><input type="checkbox" name="dates" defaultChecked={r.public_warranty_dates} /> <span>The public check shows warranty start and end dates</span></label>
          <div className="grid-fields">
            <Field label="Renewal reminder (days before an AMC ends)" name="notice" type="number" min={1} max={365} defaultValue={r.amc_renewal_notice_days} error={fields.amc_renewal_notice_days} />
            <Field label="Keep reset codes for (days after the alert closes)" name="retention" type="number" min={1} max={3650} defaultValue={r.code_retention_days} error={fields.code_retention_days} />
            <Field label="Photographs at a maintenance visit" name="mphotos" type="number" min={0} max={20} defaultValue={r.maintenance_photos_min} />
            <Field label="At a service visit" name="sphotos" type="number" min={0} max={20} defaultValue={r.service_photos_min} />
          </div>
          <TextareaField label="Reason for the change" name="reason" maxLength={500} />
          {edit && <button className="btn btn-primary" disabled={!!action.busy}>Save after-sales rules</button>}
        </fieldset>
      </form>
      <section className="settings-row">
        <div><h2>Document numbers</h2><p className="muted">Numbers already issued never change; a new format applies to new documents (open question 11).</p></div>
        <div className="table-wrap">
          <table className="table stack">
            <thead><tr><th scope="col">Document</th><th scope="col">Prefix</th><th scope="col">Digits</th><th scope="col">Year in number</th><th scope="col">Next</th><th scope="col"><span className="sr-only">Save</span></th></tr></thead>
            <tbody>
              {seqs.data.items.map((s) => (
                <tr key={s.doc_type}>
                  <td className="cell-main">{DOCS[s.doc_type]}</td>
                  <td colSpan={4} className="cell-sub">
                    <form id={`seq-${s.doc_type}`} className="seq-form" onSubmit={(e) => {
                      e.preventDefault()
                      const fd = new FormData(e.currentTarget)
                      void action.run(s.doc_type, () => api(`/numbering/${s.doc_type}`, { method: 'PUT', body: { prefix: fd.get('prefix'), padding: Number(fd.get('padding')), yearly: fd.get('yearly') === 'on' } }))
                    }}>
                      <fieldset className="plain seq-fields" disabled={!edit}>
                        <input name="prefix" defaultValue={s.prefix} maxLength={12} aria-label={`${DOCS[s.doc_type]} prefix`} />
                        <input name="padding" type="number" min={3} max={9} defaultValue={s.padding} aria-label={`${DOCS[s.doc_type]} digits`} />
                        <label className="check"><input type="checkbox" name="yearly" defaultChecked={s.yearly} /><span className="sr-only">Year in {DOCS[s.doc_type]} numbers</span></label>
                        <code>{s.next}</code>
                      </fieldset>
                    </form>
                  </td>
                  <td className="actions cell-action">{edit && <button className="link-btn" form={`seq-${s.doc_type}`}>Save<span className="sr-only"> {DOCS[s.doc_type]}</span></button>}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </section>
      <FormError message={action.error} />
    </>
  )
}
