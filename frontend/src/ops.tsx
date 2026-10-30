import { Plus, Trash2 } from 'lucide-react'
import { useState, type ReactNode } from 'react'
import { Link } from 'react-router'
import { useApi } from './lib'
import { Tag, type Tone } from './ui'

/** Shared pieces of the operations screens (purchasing, stock, assembly, master data). */

export type Ref = { id: string; code: string; name: string; unit_code: string; tracking: 'none' | 'lot' | 'serial' }
export type Item = Ref & {
  kind: string; category: string; assembled: boolean; capacity_ah: number | null; voltage_v: number | null
  chemistry: string; specs: Record<string, string>; min_stock: number; notes: string; active: boolean; version: number
}
export type Loc = { id: string; warehouse_id: string; code: string; name: string; purpose: string; active: boolean; version: number; label: string; warehouse: string }
export type Warehouse = { id: string; code: string; name: string; address: string; active: boolean; version: number; locations: Loc[] }
export type Page<T> = { items: T[]; total: number; counts?: Record<string, number> }

export const qty = (v: number | null | undefined, unit?: string) =>
  v == null ? '—' : `${v.toLocaleString('en-GB', { maximumFractionDigits: 3 })}${unit ? ` ${unit}` : ''}`
export const money = (v: number, currency: string) =>
  `${v.toLocaleString('en-GB', { minimumFractionDigits: 2, maximumFractionDigits: 2 })} ${currency}`
export const day = (iso: string | null | undefined) =>
  iso ? new Date(`${iso}T00:00:00`).toLocaleDateString('en-GB', { day: 'numeric', month: 'short', year: 'numeric' }) : '—'

/** Today in the browser's own time zone, as yyyy-mm-dd (UTC would be yesterday just after midnight in Dubai). */
export const today = () => new Date().toLocaleDateString('en-CA')

/** A fresh key per form: the server records a repeated submit (double click, retry) only once. */
export const newKey = () => crypto.randomUUID().replaceAll('-', '')

export const KIND: Record<string, string> = {
  battery_model: 'Battery model', battery_unit: 'Battery unit', component: 'Component', accessory: 'Accessory', material: 'Assembly material',
}
export const PURPOSE: Record<string, string> = {
  available: 'Available stock', inspection: 'Awaiting inspection', quarantine: 'On hold', rejected: 'Rejected, to return',
  assembly: 'Under assembly', repair: 'Under repair', returns: 'Returned',
}

const TONE: Record<string, Tone> = {
  draft: 'neutral', submitted: 'info', approved: 'success', rejected: 'danger', partly_received: 'warning', received: 'success',
  closed: 'neutral', cancelled: 'neutral', open: 'info', ordered: 'success', pending: 'warning', active: 'success',
  released: 'info', in_progress: 'warning', completed: 'success', retired: 'neutral', available: 'success',
  awaiting_inspection: 'info', on_hold: 'warning', under_assembly: 'info', rework: 'warning', issued: 'info',
  consumed: 'neutral', written_off: 'danger', returned_to_supplier: 'neutral', used: 'neutral', selected: 'success',
  declined: 'neutral', passed: 'success', quoted: 'info', lost: 'neutral', accepted: 'success', revised: 'neutral',
  expired: 'warning', partly_dispatched: 'warning', dispatched: 'info', delivered: 'success', planning: 'neutral',
  planned: 'neutral', superseded: 'neutral', done: 'success', scheduled: 'info', started: 'warning',
  installed: 'info', commissioned: 'info', handed_over: 'success', in_service: 'success', removed: 'warning',
  under_review: 'info', pending_action: 'warning', correction_required: 'warning', ended: 'neutral', renewed: 'neutral',
  urgent: 'danger', high: 'warning', normal: 'neutral', low: 'neutral', in_repair: 'warning', verified: 'info', code_recorded: 'success',
}
const WORDS: Record<string, string> = {
  partly_received: 'Partly received', awaiting_inspection: 'Awaiting inspection', on_hold: 'On hold',
  under_assembly: 'Built, awaiting inspection', in_progress: 'In progress', written_off: 'Written off',
  returned_to_supplier: 'Returned to supplier', issued: 'On the assembly floor', submitted: 'Waiting for approval',
  partly_dispatched: 'Partly dispatched', declined: 'Declined by customer', accepted: 'Accepted by customer',
  dispatched: 'On the way', started: 'In progress', handed_over: 'Handed over', in_service: 'In service',
  removed: 'Removed from its bank', correction_required: 'Correction asked for', in_repair: 'At the workshop',
  code_recorded: 'Code recorded', no_fault: 'No fault found',
}
export const words = (s: string) => WORDS[s] ?? s.charAt(0).toUpperCase() + s.slice(1).replaceAll('_', ' ')

export function Status({ s }: { s: string }) {
  return <Tag tone={TONE[s] ?? 'neutral'}>{words(s)}</Tag>
}

export type Option = Ref & { kind: string; assembled: boolean }

/** Every active item, for pickers (the native select lets people type the code to jump to it). */
export function useItems(kind?: string) {
  const { data } = useApi<{ items: Option[] }>(`/items/options${kind ? `?kind=${kind}` : ''}`)
  return data?.items ?? []
}

export function useWarehouses(enabled = true) {
  const { data, reload } = useApi<{ items: Warehouse[] }>(enabled ? '/warehouses' : null)
  return { warehouses: data?.items ?? [], loaded: !!data, reload }
}

export function ItemOptions({ items }: { items: Ref[] }) {
  return <>{items.map((i) => <option key={i.id} value={i.id}>{i.code} · {i.name}</option>)}</>
}

export function LocationOptions({ warehouses, purpose }: { warehouses: Warehouse[]; purpose?: string }) {
  return (
    <>
      {warehouses.filter((w) => w.active).map((w) => (
        <optgroup key={w.id} label={`${w.code} · ${w.name}`}>
          {w.locations.filter((l) => l.active && (!purpose || l.purpose === purpose)).map((l) => (
            <option key={l.id} value={l.id}>{l.label} · {l.name}</option>
          ))}
        </optgroup>
      ))}
    </>
  )
}

/** Serial numbers typed or scanned: one per line, or separated by commas or spaces. */
export const serialList = (text: string) => text.split(/[\s,;]+/).map((s) => s.trim().toUpperCase()).filter(Boolean)

/** Facts across the top of a record: label and value pairs. */
export function Facts({ rows }: { rows: [string, ReactNode][] }) {
  return (
    <dl className="facts">
      {rows.map(([k, v]) => <div key={k}><dt>{k}</dt><dd>{v}</dd></div>)}
    </dl>
  )
}

export function Back({ to, children }: { to: string; children: ReactNode }) {
  return <p className="small back"><Link to={to}>← {children}</Link></p>
}

export type Row = { key: string } & Record<string, string>

/** Editable document lines: each row is one item with its own fields (quantity, price, lot…). */
export function LineRows({ items, rows, setRows, columns, label = 'Lines' }: {
  items: Ref[]
  rows: Row[]
  setRows: (r: Row[]) => void
  columns: { name: string; label: string; type?: string; step?: string; width?: string }[]
  label?: string
}) {
  const set = (i: number, name: string, v: string) => setRows(rows.map((r, j) => (j === i ? { ...r, [name]: v } : r)))
  return (
    <fieldset className="lines">
      <legend>{label}</legend>
      {rows.map((r, i) => (
        <div className="line" key={r.key}>
          <label className="line-item">
            <span className="sr-only">Item, line {i + 1}</span>
            <select value={r.item_id} onChange={(e) => set(i, 'item_id', e.target.value)} required>
              <option value="">Choose an item</option>
              <ItemOptions items={items} />
            </select>
          </label>
          {columns.map((c) => (
            <label key={c.name} style={{ inlineSize: c.width }}>
              <span className="line-label">{c.label}</span>
              <input type={c.type ?? 'number'} step={c.step ?? 'any'} min={c.type ? undefined : 0} value={r[c.name] ?? ''}
                onChange={(e) => set(i, c.name, e.target.value)} />
            </label>
          ))}
          <button type="button" className="btn btn-ghost" aria-label={`Remove line ${i + 1}`} onClick={() => setRows(rows.filter((_, j) => j !== i))}>
            <Trash2 size={16} aria-hidden="true" />
          </button>
        </div>
      ))}
      <button type="button" className="btn btn-secondary" onClick={() => setRows([...rows, { key: newKey(), item_id: '' }])}>
        <Plus size={16} aria-hidden="true" /> Add line
      </button>
    </fieldset>
  )
}

export const blankRow = (): Row => ({ key: newKey(), item_id: '' })
export const num = (v: string | undefined) => (v === undefined || v === '' ? undefined : Number(v))

export function useRows(initial?: Row[]) {
  return useState<Row[]>(initial ?? [blankRow()])
}
