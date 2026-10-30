import { Plus, Trash2 } from 'lucide-react'
import { useState } from 'react'
import { Link, useNavigate, useParams } from 'react-router'
import { api } from './api'
import { useAction, useApi, useCan, useMe, useWhen } from './lib'
import { Back, Facts, ItemOptions, Status, day, newKey, num, qty, useItems, useWarehouses, type Page, type Ref } from './ops'
import { useCustomers } from './Sales'
import { Dialog, Field, FormError, PageHead, PageState, Pager, SelectField, TextareaField } from './ui'

type Site = { id: string; name: string; address: string; contact: string; survey_date: string | null; surveyed_by: string; survey_notes: string; requirements: Record<string, string>; version: number }
type Calc = { rule: string; expression: string; inputs: Record<string, number>; result: number; result_unit: string; confirmed: boolean; suggested_qty: number; overridden?: boolean }
type Sheet = { id: string; site_id: string | null; version_no: number; status: string; notes: string; prepared_by: string; editor_ids: string[]; decided_by: string | null; decision_reason: string; version: number
  lines: { id: string; item: Ref; configuration: string; qty: number; calculation: Calc | null; notes: string }[] }
type Material = { item: Ref; assembled: boolean; required: number; reserved: number; free: number; short: number }
type Project = {
  id: string; number: string; name: string; customer: { id: string; name: string }; status: string; scope: string; manager: string | null
  start_date: string | null; end_date: string | null; version: number; next_milestone?: { title: string; due_date: string | null } | null
  sites?: Site[]; milestones?: { id: string; kind: string; title: string; due_date: string | null; done_at: string | null; version: number }[]
  tasks?: { id: string; title: string; assignee: string | null; due_date: string | null; status: string; version: number }[]
  changes?: { id: string; kind: string; description: string; status: string; requested_by: string; requested_by_id: string; decided_by: string | null; decision_reason: string; created_at: string; version: number }[]
  notes?: { id: string; text: string; pending_action: boolean; author: string; created_at: string }[]
  sheets?: Sheet[]; materials?: Material[]; quotations?: { id: string; number: string }[]; sales_orders?: { id: string; number: string; status: string }[]
  purchase_requests?: { id: string; number: string; status: string }[]; assembly_orders?: { id: string; number: string; status: string }[]; reservations?: number
}
type Rule = { id: string; name: string; description: string; inputs: { name: string; label: string; unit: string }[]; expression: string; result_unit: string; confirmed: boolean; active: boolean; version: number }

const STATUSES: [string, string][] = [['planning', 'Planning'], ['active', 'Active'], ['on_hold', 'On hold'], ['completed', 'Completed'], ['cancelled', 'Cancelled']]

export function ProjectsPage() {
  const can = useCan()
  const nav = useNavigate()
  const customers = useCustomers()
  const [status, setStatus] = useState('')
  const [q, setQ] = useState('')
  const [offset, setOffset] = useState(0)
  const [adding, setAdding] = useState(false)
  const { data, error } = useApi<Page<Project>>(`/projects?${new URLSearchParams({ q, offset: String(offset), limit: '50', ...(status ? { status } : {}) })}`)
  const action = useAction(() => {})
  return (
    <>
      <PageHead title="Projects" intro="Project supply: sites and surveys, battery requirements approved by an engineer, then stock, purchases, assembly and delivery.">
        {can('projects.create') && <button className="btn btn-primary" onClick={() => setAdding(true)}>New project</button>}
      </PageHead>
      {data?.counts && <div className="counts" role="group" aria-label="Show projects by status">{STATUSES.slice(0, 4).map(([s, l]) => <button key={s} className="count" aria-pressed={status === s} onClick={() => { setStatus(status === s ? '' : s); setOffset(0) }}><span>{l}</span><strong>{data.counts?.[s] ?? 0}</strong></button>)}</div>}
      <div className="toolbar" role="search"><Field label="Search number, name or customer" type="search" value={q} onChange={(e) => { setQ(e.target.value); setOffset(0) }} /></div>
      {error ? <PageState kind="error" title="Couldn't load projects" message={error} />
        : !data ? <PageState kind="loading" title="Loading…" />
        : data.items.length === 0 ? <PageState kind="empty" title="No projects here" />
        : (
          <div className="table-wrap">
            <table className="table stack">
              <thead><tr><th scope="col">Project</th><th scope="col">Customer</th><th scope="col">Next milestone</th><th scope="col">Status</th></tr></thead>
              <tbody>
                {data.items.map((p) => (
                  <tr key={p.id}>
                    <td className="cell-main"><Link to={`/projects/${p.id}`}>{p.name}</Link><span className="small muted">{p.number}{p.manager && ` · ${p.manager}`}</span></td>
                    <td className="cell-sub">{p.customer.name}</td>
                    <td className="cell-sub">{p.next_milestone ? `${p.next_milestone.title}, ${day(p.next_milestone.due_date)}` : '—'}</td>
                    <td className="cell-tag"><Status s={p.status} /></td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      {data && <Pager offset={offset} limit={50} total={data.total} onChange={setOffset} />}
      {adding && (
        <Dialog title="New project" onClose={() => setAdding(false)}>
          <form noValidate onSubmit={(e) => {
            e.preventDefault()
            const fd = new FormData(e.currentTarget)
            void action.run('add', async () => { const p = await api<Project>('/projects', { method: 'POST', body: { name: fd.get('name'), customer_id: fd.get('customer_id'),
              scope: fd.get('scope'), start_date: fd.get('start_date') || null, end_date: fd.get('end_date') || null } }); nav(`/projects/${p.id}`) })
          }}>
            <Field label="Project name" name="name" required maxLength={160} />
            <SelectField label="Customer" name="customer_id" options={[['', 'Choose a customer'], ...customers.map((c): [string, string] => [c.id, c.name])]} />
            <div className="grid-fields"><Field label="Start" name="start_date" type="date" /><Field label="End" name="end_date" type="date" /></div>
            <TextareaField label="Scope" name="scope" maxLength={4000} />
            <p className="hint">Created with milestones for material readiness, delivery, installation, commissioning and handover.</p>
            <FormError message={action.error} />
            <button className="btn btn-primary" disabled={!!action.busy}>Create project</button>
          </form>
        </Dialog>
      )}
    </>
  )
}

function SiteForm({ projectId, site, onDone }: { projectId: string; site: Site | null; onDone: () => void }) {
  const [reqs, setReqs] = useState<[string, string][]>(Object.entries(site?.requirements ?? {}))
  const action = useAction(onDone)
  return (
    <form noValidate onSubmit={(e) => {
      e.preventDefault()
      const fd = new FormData(e.currentTarget)
      const body = { name: fd.get('name'), address: fd.get('address'), contact: fd.get('contact'), survey_date: fd.get('survey_date') || null,
        surveyed_by: fd.get('surveyed_by'), survey_notes: fd.get('survey_notes'), requirements: Object.fromEntries(reqs.filter(([k, v]) => k.trim() && v.trim())) }
      void action.run('save', () => api(site ? `/sites/${site.id}` : `/projects/${projectId}/sites`, { method: site ? 'PUT' : 'POST', body: site ? { ...body, version: site.version } : body }))
    }}>
      <div className="grid-fields">
        <Field label="Site name" name="name" required maxLength={160} defaultValue={site?.name} />
        <Field label="Site contact" name="contact" maxLength={160} defaultValue={site?.contact} />
        <Field label="Survey date" name="survey_date" type="date" defaultValue={site?.survey_date ?? ''} />
        <Field label="Surveyed by" name="surveyed_by" maxLength={120} defaultValue={site?.surveyed_by} />
      </div>
      <TextareaField label="Address" name="address" maxLength={4000} defaultValue={site?.address} />
      <TextareaField label="Survey findings" name="survey_notes" maxLength={4000} defaultValue={site?.survey_notes} />
      <fieldset className="lines"><legend>Technical requirements found</legend>
        {reqs.map(([k, v], i) => (
          <div className="line" key={i}>
            <label className="line-item"><span className="line-label">What</span><input value={k} maxLength={60} placeholder="e.g. Load" onChange={(e) => setReqs(reqs.map((r, j) => (j === i ? [e.target.value, r[1]] : r)))} /></label>
            <label className="line-item"><span className="line-label">Value</span><input value={v} maxLength={300} placeholder="e.g. 2.4 kW" onChange={(e) => setReqs(reqs.map((r, j) => (j === i ? [r[0], e.target.value] : r)))} /></label>
            <button type="button" className="btn btn-ghost" aria-label={`Remove requirement ${i + 1}`} onClick={() => setReqs(reqs.filter((_, j) => j !== i))}><Trash2 size={16} aria-hidden="true" /></button>
          </div>
        ))}
        <button type="button" className="btn btn-secondary" onClick={() => setReqs([...reqs, ['', '']])}><Plus size={16} aria-hidden="true" /> Add requirement</button>
      </fieldset>
      <FormError message={action.error} />
      <button className="btn btn-primary" disabled={!!action.busy}>{site ? 'Save site' : 'Add site'}</button>
    </form>
  )
}

type SheetRow = { key: string; item_id: string; configuration: string; qty: string; rule_id: string; inputs: Record<string, string>; notes: string }

function SheetForm({ project, sheet, onDone }: { project: Project; sheet: Sheet | null; onDone: () => void }) {
  const items = useItems()
  const rules = useApi<{ items: Rule[] }>('/sizing-rules').data?.items.filter((r) => r.active) ?? []
  const [rows, setRows] = useState<SheetRow[]>(sheet?.lines.map((l) => ({ key: newKey(), item_id: l.item.id, configuration: l.configuration, qty: String(l.qty),
    rule_id: l.calculation ? (rules.find((r) => r.name === l.calculation!.rule)?.id ?? '') : '', inputs: Object.fromEntries(Object.entries(l.calculation?.inputs ?? {}).map(([k, v]) => [k, String(v)])), notes: l.notes }))
    ?? [{ key: newKey(), item_id: '', configuration: '', qty: '', rule_id: '', inputs: {}, notes: '' }])
  const [preview, setPreview] = useState<Record<string, string>>({})
  const action = useAction(onDone)
  const set = (i: number, patch: Partial<SheetRow>) => setRows(rows.map((r, j) => (j === i ? { ...r, ...patch } : r)))
  const tryRule = async (r: SheetRow) => {
    try {
      const out = await api<Calc>(`/sizing-rules/${r.rule_id}/evaluate`, { method: 'POST', body: { inputs: Object.fromEntries(Object.entries(r.inputs).map(([k, v]) => [k, Number(v)])) } })
      setPreview({ ...preview, [r.key]: `${out.expression} = ${out.result} ${out.result_unit} → suggested ${out.suggested_qty}${out.confirmed ? '' : ' (rule not confirmed by SMET)'}` })
    } catch (e) { setPreview({ ...preview, [r.key]: (e as Error).message }) }
  }
  return (
    <form noValidate onSubmit={(e) => {
      e.preventDefault()
      const fd = new FormData(e.currentTarget)
      const body = { site_id: fd.get('site_id') || null, notes: fd.get('notes'), lines: rows.filter((r) => r.item_id).map((r) => ({
        item_id: r.item_id, configuration: r.configuration, qty: num(r.qty) ?? null, rule_id: r.rule_id || null, notes: r.notes,
        inputs: r.rule_id ? Object.fromEntries(Object.entries(r.inputs).map(([k, v]) => [k, Number(v)])) : {} })) }
      void action.run('save', () => api(sheet ? `/sheets/${sheet.id}` : `/projects/${project.id}/sheets`, { method: sheet ? 'PUT' : 'POST', body: sheet ? { ...body, version: sheet.version } : body }))
    }}>
      <SelectField label="For" name="site_id" defaultValue={sheet?.site_id ?? ''} options={[['', 'The whole project'], ...project.sites!.map((s): [string, string] => [s.id, s.name])]} />
      <p className="hint">No sizing formula is built in. Type the quantity, or pick a rule SMET has entered (Projects → Sizing rules) and its inputs; the engineer sees the inputs and the result before approving.</p>
      {rows.map((r, i) => {
        const rule = rules.find((x) => x.id === r.rule_id)
        return (
          <fieldset key={r.key} className="lines">
            <legend>Requirement {i + 1}</legend>
            <div className="line">
              <label className="line-item"><span className="line-label">Item</span><select value={r.item_id} onChange={(e) => set(i, { item_id: e.target.value })}><option value="">Choose an item</option><ItemOptions items={items} /></select></label>
              <label className="line-item"><span className="line-label">Configuration</span><input value={r.configuration} maxLength={300} placeholder="e.g. 24 × 2 V in series" onChange={(e) => set(i, { configuration: e.target.value })} /></label>
              <label style={{ inlineSize: 100 }}><span className="line-label">Quantity</span><input type="number" min={0} step="any" value={r.qty} placeholder={rule ? 'from rule' : ''} onChange={(e) => set(i, { qty: e.target.value })} /></label>
              <button type="button" className="btn btn-ghost" aria-label={`Remove requirement ${i + 1}`} onClick={() => setRows(rows.filter((_, j) => j !== i))}><Trash2 size={16} aria-hidden="true" /></button>
            </div>
            {rules.length > 0 && (
              <div className="line">
                <label className="line-item"><span className="line-label">Sizing rule (optional)</span><select value={r.rule_id} onChange={(e) => set(i, { rule_id: e.target.value, inputs: {} })}><option value="">None: quantity typed in</option>{rules.map((x) => <option key={x.id} value={x.id}>{x.name}{x.confirmed ? '' : ' (not confirmed)'}</option>)}</select></label>
                {rule?.inputs.map((inp) => <label key={inp.name} style={{ inlineSize: 140 }}><span className="line-label">{inp.label}{inp.unit && ` (${inp.unit})`}</span><input type="number" step="any" value={r.inputs[inp.name] ?? ''} onChange={(e) => set(i, { inputs: { ...r.inputs, [inp.name]: e.target.value } })} /></label>)}
                {rule && <button type="button" className="btn btn-secondary" onClick={() => tryRule(r)}>Calculate</button>}
              </div>
            )}
            {preview[r.key] && <p className="small muted">{preview[r.key]}</p>}
          </fieldset>
        )
      })}
      <button type="button" className="btn btn-secondary" onClick={() => setRows([...rows, { key: newKey(), item_id: '', configuration: '', qty: '', rule_id: '', inputs: {}, notes: '' }])}><Plus size={16} aria-hidden="true" /> Add requirement</button>
      <TextareaField label="Notes" name="notes" defaultValue={sheet?.notes} maxLength={4000} />
      <FormError message={action.error} />
      <button className="btn btn-primary" disabled={!!action.busy}>{sheet ? 'Save draft' : 'Create draft'}</button>
    </form>
  )
}

export function ProjectPage() {
  const { id } = useParams()
  const can = useCan()
  const me = useMe()
  const nav = useNavigate()
  const when = useWhen()
  const { warehouses } = useWarehouses(can('warehouses.view'))
  const { data: p, error, reload } = useApi<Project>(`/projects/${id}`)
  const people = useApi<{ items: { id: string; name: string }[] }>('/projects/people').data?.items ?? []
  const [dialog, setDialog] = useState<null | 'site' | 'sheet' | 'task' | 'change' | { site: Site } | { sheet: Sheet } | { decide: Sheet } | { change: NonNullable<Project['changes']>[number]; verdict: 'approve' | 'reject' }>(null)
  const [key, setKey] = useState(newKey)
  const action = useAction(() => { setKey(newKey()); setDialog(null); reload() })
  if (error) return <PageState kind="error" title="Couldn't load the project" message={error} />
  if (!p) return <PageState kind="loading" title="Loading…" />
  const edit = can('projects.create') && !['completed', 'cancelled'].includes(p.status)
  const go = (path: string, body: object = {}, method = 'POST') => api(path, { method, body })
  return (
    <>
      <Back to="/projects">Projects</Back>
      <PageHead title={p.name} intro={`${p.number} · ${p.customer.name}${p.manager ? ` · managed by ${p.manager}` : ''}`}>
        {edit && <SelectField label="Status" value={p.status} onChange={(e) => action.run('status', () => go(`/projects/${p.id}`, { version: p.version, status: e.target.value }, 'PATCH'))} options={STATUSES} />}
      </PageHead>
      <Facts rows={[['Status', <Status s={p.status} />], ['Sites', String(p.sites!.length)], ['Dates', p.start_date || p.end_date ? `${day(p.start_date)} – ${day(p.end_date)}` : 'Not set'],
        ['Approved sheets', String(p.sheets!.filter((s) => s.status === 'approved').length)], ['Reservations', String(p.reservations ?? 0)]]} />
      <FormError message={action.error} />
      {p.scope && <p className="prewrap">{p.scope}</p>}

      <div className="panel-head section"><h2>Sites</h2>{edit && <button className="btn btn-secondary" onClick={() => setDialog('site')}>Add site</button>}</div>
      {p.sites!.length === 0 ? <p className="empty-line">No sites yet.</p> : (
        <ul className="feed panel panel-wide">{p.sites!.map((s) => (
          <li key={s.id}><span><strong>{s.name}</strong><span className="small muted">{s.address || 'No address'} · {s.survey_date ? `surveyed ${day(s.survey_date)} by ${s.surveyed_by || '—'}` : 'not surveyed yet'}{Object.keys(s.requirements).length > 0 && ` · ${Object.entries(s.requirements).map(([k, v]) => `${k}: ${v}`).join(', ')}`}</span></span>
            {edit && <button className="link-btn" onClick={() => setDialog({ site: s })}>Edit<span className="sr-only"> {s.name}</span></button>}</li>
        ))}</ul>
      )}

      <div className="panel-head section"><h2>Battery requirements</h2>{edit && p.sites && <button className="btn btn-secondary" onClick={() => setDialog('sheet')}>New requirement sheet</button>}</div>
      {p.sheets!.length === 0 ? <p className="empty-line">No requirement sheet yet. An engineer approves each before stock, purchases or assembly follow.</p> : p.sheets!.map((s) => {
        const mine = [s.prepared_by, ...s.editor_ids].includes(me.user.id) || s.editor_ids.includes(me.user.id)
        return (
          <section key={s.id} className="panel panel-wide">
            <div className="panel-head"><h2>Version {s.version_no}{s.site_id && ` · ${p.sites!.find((x) => x.id === s.site_id)?.name}`} <Status s={s.status} /></h2>
              <span className="row-end">
                {edit && ['draft', 'rejected'].includes(s.status) && <button className="link-btn" onClick={() => setDialog({ sheet: s })}>Edit</button>}
                {edit && s.status === 'draft' && <button className="link-btn" onClick={() => action.run(s.id, () => go(`/sheets/${s.id}/submit`, { version: s.version }))}>Submit for engineering review</button>}
                {can('projects.approve') && s.status === 'submitted' && !s.editor_ids.includes(me.user.id) && <button className="link-btn" onClick={() => setDialog({ decide: s })}>Review</button>}
              </span></div>
            {s.status === 'submitted' && mine && can('projects.approve') && <p className="hint">You prepared this sheet, so another engineer reviews it.</p>}
            {s.decision_reason && <p className="small muted">{s.decided_by}: {s.decision_reason}</p>}
            <div className="table-wrap"><table className="table"><thead><tr><th scope="col">Item</th><th scope="col">Configuration</th><th scope="col" className="num">Quantity</th><th scope="col">How it was worked out</th></tr></thead>
              <tbody>{s.lines.map((l) => <tr key={l.id}><td><code>{l.item.code}</code></td><td>{l.configuration || '—'}</td><td className="num">{qty(l.qty, l.item.unit_code)}</td>
                <td className="small">{l.calculation ? `${l.calculation.rule}: ${l.calculation.expression} with ${Object.entries(l.calculation.inputs).map(([k, v]) => `${k} = ${v}`).join(', ')} → ${l.calculation.result} ${l.calculation.result_unit}${l.calculation.overridden ? ' (quantity typed differently)' : ''}${l.calculation.confirmed ? '' : ' · rule not confirmed by SMET'}` : 'Typed in'}</td></tr>)}</tbody></table></div>
          </section>
        )
      })}

      {p.materials!.length > 0 && (
        <>
          <div className="panel-head section"><h2>Material list</h2>
            <span className="row-end">
              {can('stock.create') && edit && <button className="btn btn-secondary" onClick={() => action.run('reserve', () => go(`/projects/${p.id}/reserve`, { key }))}>Reserve free stock</button>}
              {edit && p.materials!.some((m) => m.short > 0 && !m.assembled) && <button className="btn btn-secondary" onClick={() => action.run('pr', async () => { const r = await go(`/projects/${p.id}/purchase-request`) as { id: string }; nav(`/purchasing/requests/${r.id}`) })}>Request purchase of what is short</button>}
              {can('sales.create') && <button className="btn btn-secondary" onClick={() => action.run('qt', async () => { const q = await go(`/projects/${p.id}/quotation`) as { id: string }; nav(`/sales/quotations/${q.id}`) })}>Prepare quotation</button>}
            </span></div>
          <div className="table-wrap"><table className="table">
            <thead><tr><th scope="col">Item</th><th scope="col" className="num">Required</th><th scope="col" className="num">Reserved</th><th scope="col" className="num">Free</th><th scope="col" className="num">Short</th><th scope="col"><span className="sr-only">Actions</span></th></tr></thead>
            <tbody>{p.materials!.map((m) => <tr key={m.item.id}><td><code>{m.item.code}</code><span className="small">{m.item.name}</span></td><td className="num">{qty(m.required, m.item.unit_code)}</td><td className="num">{qty(m.reserved)}</td><td className="num">{qty(m.free)}</td><td className="num">{m.short ? <strong className="low">{qty(m.short)}</strong> : '—'}</td>
              <td className="actions">{m.assembled && m.short > 0 && can('assembly.create') && warehouses[0] && <button className="link-btn" onClick={() => action.run(m.item.id, async () => { const ao = await go(`/projects/${p.id}/assembly-order`, { item_id: m.item.id, warehouse_id: warehouses[0].id, qty: Math.ceil(m.short) }) as { id: string }; nav(`/assembly/orders/${ao.id}`) })}>Plan assembly</button>}</td></tr>)}</tbody>
          </table></div>
        </>
      )}

      <div className="grid-2 section">
        <section className="panel">
          <h2>Milestones</h2>
          <ul className="steps">{p.milestones!.map((m) => (
            <li key={m.id} className={m.done_at ? 'done' : ''}>
              {edit ? <input type="checkbox" aria-label={`${m.title} done`} checked={!!m.done_at} onChange={(e) => action.run(m.id, () => go(`/milestones/${m.id}`, { version: m.version, done: e.target.checked }, 'PATCH'))} /> : <span className="todo" />}
              <span>{m.title}</span>
              {edit ? <input type="date" aria-label={`${m.title} due`} className="inline-date" value={m.due_date ?? ''} onChange={(e) => action.run(m.id, () => go(`/milestones/${m.id}`, { version: m.version, due_date: e.target.value || null }, 'PATCH'))} /> : <span className="stamp">{day(m.due_date)}</span>}
            </li>
          ))}</ul>
        </section>
        <section className="panel">
          <div className="panel-head"><h2>Tasks</h2>{edit && <button className="btn btn-secondary" onClick={() => setDialog('task')}>Add task</button>}</div>
          {p.tasks!.length === 0 ? <p className="empty-line">No tasks.</p> : <ul className="steps">{p.tasks!.map((t) => (
            <li key={t.id} className={t.status === 'done' ? 'done' : ''}>
              <input type="checkbox" aria-label={`${t.title} done`} disabled={!edit} checked={t.status === 'done'} onChange={(e) => action.run(t.id, () => go(`/tasks/${t.id}`, { version: t.version, status: e.target.checked ? 'done' : 'open' }, 'PATCH'))} />
              <span>{t.title}<span className="small muted"> · {t.assignee ?? 'unassigned'} · {day(t.due_date)}</span></span>
            </li>
          ))}</ul>}
        </section>
      </div>

      <div className="grid-2 section">
        <section className="panel">
          <div className="panel-head"><h2>Changes</h2>{edit && <button className="btn btn-secondary" onClick={() => setDialog('change')}>Ask for a change</button>}</div>
          {p.changes!.length === 0 ? <p className="empty-line">No changes requested.</p> : <ul className="feed">{p.changes!.map((c) => (
            <li key={c.id}><span>{c.description}<span className="small muted">{c.kind} · {c.requested_by}{c.decision_reason && ` · ${c.decided_by}: ${c.decision_reason}`}</span></span>
              <span className="row-end"><Status s={c.status} />{can('projects.approve') && c.status === 'pending' && c.requested_by_id !== me.user.id && <>
                <button className="link-btn" onClick={() => setDialog({ change: c, verdict: 'approve' })}>Approve</button>
                <button className="link-btn" onClick={() => setDialog({ change: c, verdict: 'reject' })}>Reject</button></>}</span></li>
          ))}</ul>}
        </section>
        <section className="panel">
          <h2>Progress notes</h2>
          {edit && <form className="row" onSubmit={(e) => { e.preventDefault(); const f = e.currentTarget; const fd = new FormData(f); void action.run('note', () => go(`/projects/${p.id}/notes`, { text: fd.get('text'), pending_action: fd.get('pending') === 'on' }).then(() => f.reset())) }}>
            <label className="line-item"><span className="sr-only">Note</span><input name="text" required maxLength={4000} placeholder="What happened, or what is pending" /></label>
            <label className="check inline"><input type="checkbox" name="pending" /> <span>Pending action</span></label>
            <button className="btn btn-secondary">Add</button></form>}
          <ul className="feed">{p.notes!.map((n) => <li key={n.id}><span>{n.pending_action && <strong>Pending: </strong>}{n.text}<span className="small muted">{n.author}</span></span><span className="stamp">{when(n.created_at)}</span></li>)}</ul>
        </section>
      </div>

      {(p.quotations!.length + p.sales_orders!.length + p.purchase_requests!.length + p.assembly_orders!.length) > 0 && (
        <section className="panel panel-wide">
          <h2>Linked documents</h2>
          <p>{[...p.quotations!.map((x) => <Link key={x.id} to={`/sales/quotations/${x.id}`}>{x.number}</Link>),
            ...p.sales_orders!.map((x) => <Link key={x.id} to={`/sales/orders/${x.id}`}>{x.number}</Link>),
            ...p.purchase_requests!.map((x) => <Link key={x.id} to={`/purchasing/requests/${x.id}`}>{x.number}</Link>),
            ...p.assembly_orders!.map((x) => <Link key={x.id} to={`/assembly/orders/${x.id}`}>{x.number}</Link>)].reduce<React.ReactNode[]>((a, x, i) => (i ? [...a, ' · ', x] : [x]), [])}</p>
        </section>
      )}

      {dialog === 'site' && <Dialog wide title="Add site" onClose={() => setDialog(null)}><SiteForm projectId={p.id} site={null} onDone={() => { setDialog(null); reload() }} /></Dialog>}
      {dialog && typeof dialog === 'object' && 'site' in dialog && <Dialog wide title={`Site: ${dialog.site.name}`} onClose={() => setDialog(null)}><SiteForm projectId={p.id} site={dialog.site} onDone={() => { setDialog(null); reload() }} /></Dialog>}
      {dialog === 'sheet' && <Dialog wide title="New requirement sheet" onClose={() => setDialog(null)}><SheetForm project={p} sheet={null} onDone={() => { setDialog(null); reload() }} /></Dialog>}
      {dialog && typeof dialog === 'object' && 'sheet' in dialog && <Dialog wide title={`Requirement sheet v${dialog.sheet.version_no}`} onClose={() => setDialog(null)}><SheetForm project={p} sheet={dialog.sheet} onDone={() => { setDialog(null); reload() }} /></Dialog>}
      {dialog && typeof dialog === 'object' && 'decide' in dialog && (
        <Dialog title={`Engineering review: version ${dialog.decide.version_no}`} onClose={() => setDialog(null)}>
          <form noValidate onSubmit={(e) => {
            e.preventDefault()
            const fd = new FormData(e.currentTarget)
            const verdict = (e.nativeEvent as SubmitEvent).submitter?.getAttribute('value') ?? 'approve'
            void action.run('decide', () => go(`/sheets/${dialog.decide.id}/${verdict}`, { version: dialog.decide.version, reason: fd.get('reason') }))
          }}>
            <p className="hint">Check each quantity against the site's requirements and how it was worked out. Approved sheets never change; a change is a new version.</p>
            <TextareaField label="Comment" name="reason" maxLength={500} hint="Needed to send it back" />
            <FormError message={action.error} />
            <div className="row"><button className="btn btn-primary" value="approve" disabled={!!action.busy}>Approve quantities</button><button className="btn btn-secondary" value="reject" disabled={!!action.busy}>Send back</button></div>
          </form>
        </Dialog>
      )}
      {dialog && typeof dialog === 'object' && 'change' in dialog && (
        <Dialog title={dialog.verdict === 'approve' ? 'Approve the change' : 'Reject the change'} onClose={() => setDialog(null)}>
          <form noValidate onSubmit={(e) => { e.preventDefault(); void action.run('decide-change', () => go(`/changes/${dialog.change.id}/${dialog.verdict}`, { version: dialog.change.version, reason: new FormData(e.currentTarget).get('reason') })) }}>
            <p>{dialog.change.description}</p>
            <TextareaField label={dialog.verdict === 'approve' ? 'Comment (optional)' : 'Why it is rejected'} name="reason" required={dialog.verdict === 'reject'} maxLength={500} />
            <FormError message={action.error} />
            <button className="btn btn-primary" disabled={!!action.busy}>{dialog.verdict === 'approve' ? 'Approve' : 'Reject'}</button>
          </form>
        </Dialog>
      )}
      {dialog === 'task' && (
        <Dialog title="Add task" onClose={() => setDialog(null)}>
          <form noValidate onSubmit={(e) => { e.preventDefault(); const fd = new FormData(e.currentTarget); void action.run('task', () => go(`/projects/${p.id}/tasks`, { title: fd.get('title'), assignee_id: fd.get('assignee_id') || null, due_date: fd.get('due_date') || null })) }}>
            <Field label="Task" name="title" required maxLength={200} />
            <SelectField label="Who" name="assignee_id" options={[['', 'Unassigned'], ...people.map((x): [string, string] => [x.id, x.name])]} />
            <Field label="Due" name="due_date" type="date" />
            <FormError message={action.error} />
            <button className="btn btn-primary" disabled={!!action.busy}>Add task</button>
          </form>
        </Dialog>
      )}
      {dialog === 'change' && (
        <Dialog title="Ask for a change" onClose={() => setDialog(null)}>
          <form noValidate onSubmit={(e) => { e.preventDefault(); const fd = new FormData(e.currentTarget); void action.run('change', () => go(`/projects/${p.id}/changes`, { kind: fd.get('kind'), description: fd.get('description') })) }}>
            <SelectField label="Change to" name="kind" options={[['scope', 'Scope'], ['quantity', 'Quantities'], ['schedule', 'Schedule']]} />
            <TextareaField label="What changes and why" name="description" required minLength={3} maxLength={4000} />
            <p className="hint">Someone with project approval decides on it.</p>
            <FormError message={action.error} />
            <button className="btn btn-primary" disabled={!!action.busy}>Ask for approval</button>
          </form>
        </Dialog>
      )}
    </>
  )
}

export function SizingRulesPage() {
  const can = useCan()
  const { data, reload } = useApi<{ items: Rule[] }>('/sizing-rules')
  const [dialog, setDialog] = useState<Rule | 'add' | null>(null)
  return (
    <>
      <PageHead title="Sizing rules" intro="Battery-sizing formulas SMET supplies. None is built in. A rule's result is shown with its inputs on each requirement, and an engineer approves the quantity either way.">
        {can('projects.configure') && <button className="btn btn-primary" onClick={() => setDialog('add')}>Add rule</button>}
      </PageHead>
      {!data ? <PageState kind="loading" title="Loading…" /> : data.items.length === 0 ? <PageState kind="empty" title="No sizing rules yet" message="Waiting for SMET's engineering rules (open question 15). Until then quantities are typed in and approved." /> : (
        <ul className="feed panel panel-wide">{data.items.map((r) => (
          <li key={r.id}><span><strong>{r.name}</strong><span className="small muted"><code>{r.expression}</code> → {r.result_unit} · inputs {r.inputs.map((i) => `${i.name} (${i.label}${i.unit ? `, ${i.unit}` : ''})`).join(', ')}</span></span>
            <span className="row-end">{r.confirmed ? <Status s="approved" /> : <Status s="pending" />}{!r.active && <Status s="retired" />}{can('projects.configure') && <button className="link-btn" onClick={() => setDialog(r)}>Edit</button>}</span></li>
        ))}</ul>
      )}
      {dialog && <Dialog wide title={dialog === 'add' ? 'Add sizing rule' : `Edit ${dialog.name}`} onClose={() => setDialog(null)}><RuleForm rule={dialog === 'add' ? null : dialog} onDone={() => { setDialog(null); reload() }} /></Dialog>}
    </>
  )
}

function RuleForm({ rule, onDone }: { rule: Rule | null; onDone: () => void }) {
  const [inputs, setInputs] = useState(rule?.inputs ?? [{ name: '', label: '', unit: '' }])
  const [fields, setFields] = useState<Record<string, string>>({})
  const action = useAction(onDone)
  return (
    <form noValidate onSubmit={(e) => {
      e.preventDefault()
      const fd = new FormData(e.currentTarget)
      const body = { name: fd.get('name'), description: fd.get('description'), expression: fd.get('expression'), result_unit: fd.get('result_unit'),
        confirmed: fd.get('confirmed') === 'on', active: fd.get('active') !== null ? fd.get('active') === 'on' : true, inputs: inputs.filter((i) => i.name) }
      setFields({})
      void action.run('save', () => api(rule ? `/sizing-rules/${rule.id}` : '/sizing-rules', { method: rule ? 'PUT' : 'POST', body: rule ? { ...body, version: rule.version, reason: fd.get('reason') } : body })
        .catch((err) => { setFields((err as { fields?: Record<string, string> }).fields ?? {}); throw err }))
    }}>
      <Field label="Name" name="name" required maxLength={120} defaultValue={rule?.name} error={fields.name} />
      <TextareaField label="What it is for" name="description" maxLength={4000} defaultValue={rule?.description} />
      <fieldset className="lines"><legend>Inputs</legend>
        {inputs.map((inp, i) => (
          <div className="line" key={i}>
            <label style={{ inlineSize: 150 }}><span className="line-label">Name in formula</span><input value={inp.name} pattern="[a-z_][a-z0-9_]*" placeholder="load_w" onChange={(e) => setInputs(inputs.map((x, j) => (j === i ? { ...x, name: e.target.value } : x)))} /></label>
            <label className="line-item"><span className="line-label">Label</span><input value={inp.label} placeholder="Load" onChange={(e) => setInputs(inputs.map((x, j) => (j === i ? { ...x, label: e.target.value } : x)))} /></label>
            <label style={{ inlineSize: 90 }}><span className="line-label">Unit</span><input value={inp.unit} placeholder="W" onChange={(e) => setInputs(inputs.map((x, j) => (j === i ? { ...x, unit: e.target.value } : x)))} /></label>
            <button type="button" className="btn btn-ghost" aria-label={`Remove input ${i + 1}`} onClick={() => setInputs(inputs.filter((_, j) => j !== i))}><Trash2 size={16} aria-hidden="true" /></button>
          </div>
        ))}
        <button type="button" className="btn btn-secondary" onClick={() => setInputs([...inputs, { name: '', label: '', unit: '' }])}><Plus size={16} aria-hidden="true" /> Add input</button>
      </fieldset>
      {fields.inputs && <p className="field-error">{fields.inputs}</p>}
      <Field label="Formula" name="expression" required maxLength={500} defaultValue={rule?.expression} error={fields.expression} hint="Inputs, numbers, + − * / and brackets, ceil, floor, round, max, min. For example ceil(load_w * hours / cell_wh)" />
      <Field label="Result unit" name="result_unit" maxLength={20} defaultValue={rule?.result_unit ?? 'batteries'} />
      <label className="check"><input type="checkbox" name="confirmed" defaultChecked={rule?.confirmed} /> <span>Confirmed by SMET's engineering<span className="hint">Shown on each requirement; approval is still needed.</span></span></label>
      {rule && <><label className="check"><input type="checkbox" name="active" defaultChecked={rule.active} /> <span>In use</span></label><TextareaField label="Reason for the change" name="reason" maxLength={500} /></>}
      <FormError message={action.error} />
      <button className="btn btn-primary" disabled={!!action.busy}>{rule ? 'Save rule' : 'Add rule'}</button>
    </form>
  )
}
