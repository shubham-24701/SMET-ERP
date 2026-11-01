import { useState } from 'react'
import { Link, useNavigate, useParams, useSearchParams } from 'react-router'
import { download } from './api'
import { useAction, useApi, useCan } from './lib'
import { Back, day, ItemOptions, useItems, useWarehouses, words, type Page } from './ops'
import { Field, FormError, PageHead, PageState, Pager } from './ui'

type Tile = { label: string; report: string; filters: Record<string, string>; count: number }
type Area = { area: string; label: string; tiles: Tile[] }
type Col = { key: string; label: string }
type ReportInfo = { name: string; title: string; area: string; columns: Col[]; filters: string[]; statuses: string[]; flags: Record<string, string>; date_label: string; link: string }
type Rows = ReportInfo & { items: Record<string, unknown>[]; total: number }
type PartyOpt = { id: string; name: string; kinds: string[] }

const reportLink = (t: { report: string; filters: Record<string, string> }) => {
  const q = new URLSearchParams(t.filters).toString()
  return `/reports/${t.report}${q ? `?${q}` : ''}`
}

/** Area by area, each figure the person may see; a figure opens the rows it counts. */
export function ReportsPage() {
  const { data, error } = useApi<{ areas: Area[] }>('/dashboards')
  const list = useApi<{ items: ReportInfo[]; areas: Record<string, string> }>('/reports')
  if (error) return <PageState kind="error" title="Couldn't load the dashboards" message={error} />
  if (!data || !list.data) return <PageState kind="loading" title="Loading…" />
  const exceptions = data.areas.find((a) => a.area === 'exceptions')
  const late = exceptions?.tiles.filter((t) => t.count > 0) ?? []
  return (
    <>
      <PageHead title="Reports" intro={exceptions ? (late.length ? `${late.length} kinds of exception need attention.` : 'No exceptions right now.') : 'Figures from the records you may see.'} />
      <div className="grid-3">
        {data.areas.map((a) => (
          <section key={a.area} className={a.area === 'exceptions' ? 'panel panel-wide' : 'panel'} aria-labelledby={`area-${a.area}`}>
            <h2 id={`area-${a.area}`}>{a.label}</h2>
            <ul className="feed">
              {a.tiles.map((t) => (
                <li key={t.label}><span>{t.count ? <Link to={reportLink(t)}>{t.label}</Link> : <span className="muted">{t.label}</span>}</span><strong className="num">{t.count}</strong></li>
              ))}
            </ul>
          </section>
        ))}
      </div>
      <h2 className="section">All reports</h2>
      <div className="grid-3">
        {Object.entries(list.data.areas).filter(([a]) => list.data!.items.some((r) => r.area === a)).map(([a, label]) => (
          <section key={a} className="panel"><h3>{label}</h3>
            <ul className="link-list">{list.data!.items.filter((r) => r.area === a).map((r) => <li key={r.name}><Link to={`/reports/${r.name}`}>{r.title}</Link></li>)}</ul>
          </section>
        ))}
      </div>
    </>
  )
}

function useParties(enabled: boolean) {
  return useApi<Page<PartyOpt>>(enabled ? '/parties?limit=200' : null).data?.items ?? []
}

const show = (v: unknown, key: string) => {
  if (v === null || v === undefined || v === '') return '—'
  if (typeof v === 'number') return v.toLocaleString('en-GB', { maximumFractionDigits: 3 })
  if (typeof v === 'string' && /^\d{4}-\d{2}-\d{2}$/.test(v)) return day(v)
  if (typeof v === 'string' && /^\d{4}-\d{2}-\d{2}T/.test(v)) return new Date(v).toLocaleString('en-GB', { dateStyle: 'medium', timeStyle: 'short' })
  if (key === 'status' || key === 'purpose' || key === 'stage' || key === 'warranty' || key === 'resolution' || key === 'route' || key === 'source' || key === 'scope') return words(String(v))
  return String(v)
}

export function ReportPage() {
  const { name } = useParams()
  const can = useCan()
  const [params, setParams] = useSearchParams()
  const navigate = useNavigate()
  const [offset, setOffset] = useState(0)
  const query = new URLSearchParams(params)
  query.set('limit', '50')
  query.set('offset', String(offset))
  const { data, error } = useApi<Rows>(`/reports/${name}?${query}`)
  const info = useApi<{ items: ReportInfo[]; can_export: boolean }>('/reports')
  const r = info.data?.items.find((x) => x.name === name)
  const has = (f: string) => !!r?.filters.includes(f)
  const items = useItems()
  const { warehouses } = useWarehouses(has('warehouse'))
  const parties = useParties(has('customer') || has('distributor') || has('supplier'))
  const projects = useApi<Page<{ id: string; number: string; name: string }>>(has('project') ? '/projects?limit=200' : null).data?.items ?? []
  const exporting = useAction(() => {})
  if (info.data && !r) return <PageState kind="error" title="No such report" message="It doesn't exist, or you don't have permission to see it." />
  if (!r) return <PageState kind="loading" title="Loading…" />
  const set = (k: string, v: string) => {
    const next = new URLSearchParams(params)
    if (v) next.set(k, v)
    else next.delete(k)
    setOffset(0)
    setParams(next, { replace: true })
  }
  const select = (k: string, label: string, options: [string, string][]) => (
    <div className="field"><label htmlFor={`f-${k}`}>{label}</label>
      <select id={`f-${k}`} value={params.get(k) ?? ''} onChange={(e) => set(k, e.target.value)}>
        <option value="">All</option>{options.map(([v, l]) => <option key={v} value={v}>{l}</option>)}
        {params.get(k) && !options.some(([v]) => v === params.get(k)) && <option value={params.get(k)!}>{params.get(k)!.split(',').map(words).join(', ')}</option>}
      </select></div>
  )
  const kindOf = (k: string) => parties.filter((p) => p.kinds.some((x) => (k === 'distributor' ? ['distributor', 'dealer'] : [k]).includes(x))).map((p) => [p.id, p.name] as [string, string])
  const save = (format: 'csv' | 'xlsx') => {
    const q = new URLSearchParams(params)
    q.set('format', format)
    void exporting.run(format, () => download(`/reports/${name}/export?${q}`, `${name}.${format}`))
  }
  const linkOf = (row: Record<string, unknown>) => (r.link && row.id ? r.link.replace('{id}', String(row.id)) : '')
  return (
    <>
      <Back to="/reports">Reports</Back>
      <PageHead title={r.title} intro={data ? `${data.total} ${data.total === 1 ? 'row' : 'rows'}.` : undefined}>
        {info.data?.can_export && can('reports.export') && <>
          <button className="btn btn-secondary" disabled={!!exporting.busy} onClick={() => save('csv')}>Download CSV</button>
          <button className="btn btn-secondary" disabled={!!exporting.busy} onClick={() => save('xlsx')}>Download Excel</button>
        </>}
      </PageHead>
      <FormError message={exporting.error} />
      <div className="toolbar filters" role="search" aria-label="Filters">
        {has('date') && <>
          <Field label={`${r.date_label} from`} type="date" value={params.get('date_from') ?? ''} onChange={(e) => set('date_from', e.target.value)} />
          <Field label="to" type="date" value={params.get('date_to') ?? ''} onChange={(e) => set('date_to', e.target.value)} />
        </>}
        {has('status') && select('status', 'Status', (data?.statuses ?? r.statuses).map((s) => [s, words(s)]))}
        {Object.keys(r.flags).length > 0 && select('flag', 'Show', Object.entries(r.flags))}
        {has('item') && <div className="field"><label htmlFor="f-item">Product</label>
          <select id="f-item" value={params.get('item') ?? ''} onChange={(e) => set('item', e.target.value)}><option value="">All</option><ItemOptions items={items} /></select></div>}
        {has('serial') && <Field label="Serial number" defaultValue={params.get('serial') ?? ''} onBlur={(e) => set('serial', e.target.value.trim().toUpperCase())}
          onKeyDown={(e) => e.key === 'Enter' && set('serial', e.currentTarget.value.trim().toUpperCase())} />}
        {has('customer') && select('customer', 'Customer', kindOf('customer'))}
        {has('distributor') && select('distributor', 'Distributor', kindOf('distributor'))}
        {has('supplier') && select('supplier', 'Supplier', kindOf('supplier'))}
        {has('warehouse') && select('warehouse', 'Warehouse', warehouses.map((w) => [w.id, `${w.code} ${w.name}`]))}
        {has('project') && select('project', 'Project', projects.map((p) => [p.id, `${p.number} ${p.name}`]))}
        {[...params.keys()].length > 0 && <button className="btn btn-ghost" onClick={() => { setOffset(0); navigate(`/reports/${name}`, { replace: true }) }}>Clear filters</button>}
      </div>
      {error ? <PageState kind="error" title="Couldn't run the report" message={error} /> : !data ? <PageState kind="loading" title="Loading…" />
        : data.items.length === 0 ? <p className="empty-line">No rows match these filters.</p> : (
          <div className="table-wrap"><table className="table report">
            <thead><tr>{data.columns.map((c) => <th key={c.key} scope="col">{c.label}</th>)}</tr></thead>
            <tbody>{data.items.map((row, i) => (
              <tr key={i}>{data.columns.map((c, j) => {
                const v = show(row[c.key], c.key)
                const to = j === 0 ? linkOf(row) : ''
                return <td key={c.key} className={typeof row[c.key] === 'number' ? 'num' : undefined}>{to ? <Link to={to}>{v}</Link> : v}</td>
              })}</tr>
            ))}</tbody>
          </table></div>
        )}
      {data && <Pager offset={offset} limit={50} total={data.total} onChange={setOffset} />}
    </>
  )
}

type Hit = { id: string; title: string; detail: string; link: string }

export function SearchBox() {
  const navigate = useNavigate()
  return (
    <form className="search" role="search" onSubmit={(e) => {
      e.preventDefault()
      const input = e.currentTarget.elements.namedItem('q') as HTMLInputElement
      if (input.value.trim().length >= 2) navigate(`/search?q=${encodeURIComponent(input.value.trim())}`)
    }}>
      <label htmlFor="global-search" className="sr-only">Search</label>
      <input id="global-search" name="q" type="search" placeholder="Search" minLength={2} maxLength={60} />
    </form>
  )
}

export function SearchPage() {
  const [params] = useSearchParams()
  const q = params.get('q') ?? ''
  const { data, error } = useApi<{ groups: { kind: string; label: string; items: Hit[] }[] }>(q.length >= 2 ? `/search?q=${encodeURIComponent(q)}` : null)
  return (
    <>
      <PageHead title={`Search: ${q}`} intro="Batteries, customers and suppliers, projects and documents you may see. Up to five of each." />
      {q.length < 2 ? <p className="empty-line">Type at least two characters.</p>
        : error ? <PageState kind="error" title="Couldn't search" message={error} /> : !data ? <PageState kind="loading" title="Searching…" />
          : data.groups.length === 0 ? <p className="empty-line">Nothing found for “{q}”.</p> : (
            <div className="grid-2">{data.groups.map((g) => (
              <section key={g.kind} className="panel"><h2>{g.label}</h2>
                <ul className="feed">{g.items.map((h) => <li key={h.id}><span><Link to={h.link}>{h.title}</Link>{h.detail && <span className="small muted"> {h.detail}</span>}</span></li>)}</ul>
              </section>
            ))}</div>
          )}
    </>
  )
}
