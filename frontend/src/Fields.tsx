import { useEffect, useState, type ReactNode } from 'react'
import { api, ApiError } from './api'
import { FieldConfigContext, FieldScope, useAction, useApi, useCan, type CustomDef, type FieldConfig } from './lib'
import { day, Facts } from './ops'
import { Dialog, Field, FormError, PageHead, PageState, SelectField, Tag, TextareaField } from './ui'

/** Loads the field rules once for every form; Administration → Fields refreshes it after a change. */
export function FieldConfigProvider({ children }: { children: ReactNode }) {
  const { data, reload } = useApi<FieldConfig>('/fields/config')
  useEffect(() => {
    const again = () => void reload()
    window.addEventListener('fields-changed', again)
    return () => window.removeEventListener('fields-changed', again)
  }, [reload])
  return <FieldConfigContext value={data ?? {}}>{children}</FieldConfigContext>
}

type Values = { fields: CustomDef[]; values: Record<string, string | number | boolean>; version: number; missing: string[] }

const shown = (f: CustomDef, v: unknown) => (v === undefined || v === null || v === '' ? '—'
  : f.kind === 'yes_no' ? (v ? 'Yes' : 'No') : f.kind === 'date' ? day(String(v)) : String(v))

/** SMET's extra fields on a record: shown, and edited by whoever may edit the record. */
export function CustomFields({ entity, id, editPerm }: { entity: string; id: string; editPerm: string }) {
  const can = useCan()
  const { data, reload } = useApi<Values>(`/custom-values/${entity}/${id}`)
  const [open, setOpen] = useState(false)
  if (!data || data.fields.length === 0) return null
  const edit = editPerm.split('|').some(can)
  return (
    <section className="section" aria-labelledby={`cf-${id}`}>
      <header className="panel-head"><h2 id={`cf-${id}`}>More details</h2>{edit && <button className="btn btn-secondary" onClick={() => setOpen(true)}>Edit details</button>}</header>
      {data.missing.length > 0 && <p className="notice notice-warning">Still to fill in: {data.missing.join(', ')}.</p>}
      <Facts rows={data.fields.map((f) => [f.label, shown(f, data.values[f.key])])} />
      {open && <ValuesForm entity={entity} id={id} data={data} onClose={() => setOpen(false)} onSaved={() => { setOpen(false); reload() }} />}
    </section>
  )
}

function ValuesForm({ entity, id, data, onClose, onSaved }: { entity: string; id: string; data: Values; onClose: () => void; onSaved: () => void }) {
  const [fields, setFields] = useState<Record<string, string>>({})
  const action = useAction(onSaved)
  return (
    <Dialog title="More details" onClose={onClose}>
      <FieldScope value=""><form noValidate onSubmit={(e) => {
        e.preventDefault()
        const fd = new FormData(e.currentTarget)
        const values = Object.fromEntries(data.fields.map((f) => [f.key, f.kind === 'yes_no' ? (fd.get(f.key) === '' ? null : fd.get(f.key) === 'yes') : fd.get(f.key) ?? '']))
        setFields({})
        void action.run('save', () => api(`/custom-values/${entity}/${id}`, { method: 'PUT', body: { version: data.version, values } })
          .catch((err) => { setFields(err instanceof ApiError ? err.fields : {}); throw err }))
      }}>
        {data.fields.map((f) => {
          const v = data.values[f.key]
          const common = { label: f.label + (f.required ? ' *' : ''), name: f.key, hint: f.help || undefined, error: fields[`values.${f.key}`] }
          if (f.kind === 'long_text') return <TextareaField key={f.key} {...common} defaultValue={String(v ?? '')} maxLength={4000} rows={3} />
          if (f.kind === 'choice') return <SelectField key={f.key} {...common} defaultValue={String(v ?? '')}
            options={[['', '—'], ...[...f.options, ...(v !== undefined && !f.options.includes(String(v)) ? [String(v)] : [])].map((o): [string, string] => [o, o])]} />
          if (f.kind === 'yes_no') return <SelectField key={f.key} {...common} defaultValue={v === undefined ? '' : v ? 'yes' : 'no'} options={[['', '—'], ['yes', 'Yes'], ['no', 'No']]} />
          return <Field key={f.key} {...common} type={f.kind === 'number' ? 'number' : f.kind === 'date' ? 'date' : 'text'} step={f.kind === 'number' ? 'any' : undefined}
            defaultValue={String(v ?? '')} maxLength={f.kind === 'text' ? 300 : undefined} />
        })}
        <FormError message={action.error} />
        <div className="dialog-actions"><button className="btn btn-primary" disabled={!!action.busy}>Save details</button></div>
      </form></FieldScope>
    </Dialog>
  )
}

// ---------- Administration → Fields ----------

type Builtin = { field: string; default_label: string; label: string; hidden: boolean; required: boolean; version: number }
type Entity = { key: string; label: string; builtin: Builtin[]; custom: CustomDef[] }
const KIND: Record<string, string> = { text: 'Short text', long_text: 'Long text', number: 'Number', date: 'Date', choice: 'Choice from a list', yes_no: 'Yes or no' }
const changed = () => window.dispatchEvent(new Event('fields-changed'))

export function FieldsPage() {
  const can = useCan()
  const { data, error, reload } = useApi<{ entities: Entity[] }>('/fields')
  const [entity, setEntity] = useState('item')
  const [edit, setEdit] = useState<CustomDef | 'new' | null>(null)
  const action = useAction(() => { reload(); changed() })
  if (error) return <PageState kind="error" title="Couldn't load the fields" message={error} />
  if (!data) return <PageState kind="loading" title="Loading…" />
  const e = data.entities.find((x) => x.key === entity)!
  const allowed = can('fields.configure')
  const saveBuiltin = (b: Builtin, change: Partial<Builtin>) => action.run(b.field, () => api(`/field-settings/${entity}/${b.field}`, { method: 'PUT',
    body: { version: b.version, label: b.label, hidden: b.hidden, required: b.required, ...change } }))
  return (
    <>
      <PageHead title="Fields" intro="Add your own fields to records, and rename, hide or require the optional built-in ones. Changes apply to every form at once and are audited." />
      <div className="toolbar"><SelectField label="Record" value={entity} onChange={(ev) => setEntity(ev.target.value)} options={data.entities.map((x) => [x.key, x.label])} /></div>
      <FormError message={action.error} />
      <section className="section" aria-labelledby="cf-title">
        <header className="panel-head"><h2 id="cf-title">Your fields</h2>{allowed && <button className="btn btn-primary" onClick={() => setEdit('new')}>Add field</button>}</header>
        {e.custom.length === 0 ? <p className="empty-line">No extra fields on {e.label.toLowerCase()} yet. They appear on the record's page, in its reports and in downloads.</p> : (
          <div className="table-wrap"><table className="table stack">
            <thead><tr><th scope="col">Field</th><th scope="col">Kind</th><th scope="col">Required</th><th scope="col">State</th></tr></thead>
            <tbody>{e.custom.map((f) => (
              <tr key={f.id}><td className="cell-main">{allowed ? <button className="link-btn" onClick={() => setEdit(f)}>{f.label}</button> : f.label}{f.help && <span className="small muted">{f.help}</span>}</td>
                <td className="cell-sub">{KIND[f.kind]}{f.kind === 'choice' && `: ${f.options.join(', ')}`}</td>
                <td className="cell-sub">{f.required ? 'Yes' : 'No'}</td>
                <td className="cell-tag">{f.active ? <Tag tone="success">On</Tag> : <Tag>Off</Tag>}</td></tr>
            ))}</tbody>
          </table></div>
        )}
      </section>
      {e.builtin.length > 0 && (
        <section className="section" aria-labelledby="bf-title">
          <h2 id="bf-title">Built-in fields</h2>
          <p className="muted">Optional fields of {e.label.toLowerCase()}. Required ones are checked by the server too; a hidden field keeps its value.</p>
          <div className="table-wrap"><table className="table stack">
            <thead><tr><th scope="col">Field</th><th scope="col">Shown as</th><th scope="col">Hidden</th><th scope="col">Required</th></tr></thead>
            <tbody>{e.builtin.map((b) => (
              <tr key={b.field}>
                <td className="cell-main">{b.default_label}</td>
                <td className="cell-sub"><input aria-label={`${b.default_label}: shown as`} defaultValue={b.label} placeholder={b.default_label} maxLength={80} disabled={!allowed}
                  onBlur={(ev) => ev.target.value.trim() !== b.label && void saveBuiltin(b, { label: ev.target.value.trim() })} /></td>
                <td className="cell-sub"><input type="checkbox" aria-label={`Hide ${b.default_label}`} checked={b.hidden} disabled={!allowed || b.required || !!action.busy} onChange={(ev) => void saveBuiltin(b, { hidden: ev.target.checked })} /></td>
                <td className="cell-sub"><input type="checkbox" aria-label={`Require ${b.default_label}`} checked={b.required} disabled={!allowed || b.hidden || !!action.busy} onChange={(ev) => void saveBuiltin(b, { required: ev.target.checked })} /></td>
              </tr>
            ))}</tbody>
          </table></div>
        </section>
      )}
      {edit && <FieldForm entity={entity} f={edit === 'new' ? null : edit} onClose={() => setEdit(null)} onSaved={() => { setEdit(null); reload(); changed() }} />}
    </>
  )
}

function FieldForm({ entity, f, onClose, onSaved }: { entity: string; f: CustomDef | null; onClose: () => void; onSaved: () => void }) {
  const [kind, setKind] = useState(f?.kind ?? 'text')
  const [fields, setFields] = useState<Record<string, string>>({})
  const action = useAction(onSaved)
  return (
    <Dialog title={f ? `Field: ${f.label}` : 'Add field'} onClose={onClose}>
      <form noValidate onSubmit={(e) => {
        e.preventDefault()
        const fd = new FormData(e.currentTarget)
        const body = { label: fd.get('label'), help: fd.get('help') ?? '', required: fd.get('required') === 'on', active: f ? fd.get('active') === 'on' : true,
          sort: Number(fd.get('sort') || 0), options: String(fd.get('options') ?? '').split('\n').map((x) => x.trim()).filter(Boolean) }
        setFields({})
        void action.run('save', () => (f ? api(`/custom-fields/${f.id}`, { method: 'PUT', body: { ...body, version: f.version } })
          : api('/custom-fields', { method: 'POST', body: { ...body, entity, kind } })).catch((err) => { setFields(err instanceof ApiError ? err.fields : {}); throw err }))
      }}>
        <Field label="Name" name="label" required maxLength={80} defaultValue={f?.label} error={fields.label} />
        {f ? <p className="hint">Kind: {KIND[f.kind]} (fixed, so stored values keep their meaning)</p>
          : <SelectField label="Kind" value={kind} onChange={(e) => setKind(e.target.value as CustomDef['kind'])} options={Object.entries(KIND)} />}
        {kind === 'choice' && <TextareaField label="Options, one per line" name="options" rows={4} defaultValue={f?.options.join('\n')} error={fields.options} />}
        <Field label="Help text" name="help" maxLength={200} defaultValue={f?.help} />
        <Field label="Order" name="sort" type="number" min={0} max={999} defaultValue={f?.sort ?? 0} />
        <label className="check"><input type="checkbox" name="required" defaultChecked={f?.required} /> <span>Required (the record shows "still to fill in" until it is)</span></label>
        {f && <label className="check"><input type="checkbox" name="active" defaultChecked={f.active} /> <span>On (switched off, it is hidden but its values are kept)</span></label>}
        <FormError message={action.error} />
        <div className="dialog-actions"><button className="btn btn-primary" disabled={!!action.busy}>{f ? 'Save field' : 'Add field'}</button></div>
      </form>
    </Dialog>
  )
}
