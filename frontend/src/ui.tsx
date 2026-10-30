import { X } from 'lucide-react'
import { useEffect, useId, useRef, type InputHTMLAttributes, type ReactNode, type SelectHTMLAttributes, type TextareaHTMLAttributes } from 'react'

function useDescribedBy(error?: string, hint?: string) {
  const id = useId()
  const describedBy = [error && `${id}-err`, hint && `${id}-hint`].filter(Boolean).join(' ') || undefined
  const notes = (
    <>
      {hint && <p id={`${id}-hint`} className="hint">{hint}</p>}
      {error && <p id={`${id}-err`} className="field-error">{error}</p>}
    </>
  )
  return { id, describedBy, notes }
}

type Extra = { label: string; error?: string; hint?: string }

export function Field({ label, error, hint, ...input }: InputHTMLAttributes<HTMLInputElement> & Extra) {
  const { id, describedBy, notes } = useDescribedBy(error, hint)
  return (
    <div className="field">
      <label htmlFor={id}>{label}</label>
      <input id={id} aria-invalid={error ? true : undefined} aria-describedby={describedBy} {...input} />
      {notes}
    </div>
  )
}

export function TextareaField({ label, error, hint, ...area }: TextareaHTMLAttributes<HTMLTextAreaElement> & Extra) {
  const { id, describedBy, notes } = useDescribedBy(error, hint)
  return (
    <div className="field">
      <label htmlFor={id}>{label}</label>
      <textarea id={id} rows={2} aria-invalid={error ? true : undefined} aria-describedby={describedBy} {...area} />
      {notes}
    </div>
  )
}

export function SelectField({ label, error, hint, options, ...select }: SelectHTMLAttributes<HTMLSelectElement> & Extra & {
  options: [value: string, label: string][]
}) {
  const { id, describedBy, notes } = useDescribedBy(error, hint)
  return (
    <div className="field">
      <label htmlFor={id}>{label}</label>
      <select id={id} aria-invalid={error ? true : undefined} aria-describedby={describedBy} {...select}>
        {options.map(([v, l]) => <option key={v} value={v}>{l}</option>)}
      </select>
      {notes}
    </div>
  )
}

export type Tone = 'neutral' | 'success' | 'warning' | 'danger' | 'info'

/** A status tag; always words too, never colour alone. */
export function Tag({ tone = 'neutral', children }: { tone?: Tone; children: ReactNode }) {
  return <span className={`tag tag-${tone}`}>{children}</span>
}

export function PageState({ kind, title, message, action }: {
  kind: 'loading' | 'empty' | 'error'
  title: string
  message?: string
  action?: ReactNode
}) {
  return (
    <section className={`page-state page-state-${kind}`} role={kind === 'error' ? 'alert' : 'status'} aria-busy={kind === 'loading' || undefined}>
      <h2>{title}</h2>
      {message && <p>{message}</p>}
      {action}
    </section>
  )
}

export function FormError({ message }: { message: string }) {
  return message ? <p className="form-error" role="alert">{message}</p> : null
}

export function PageHead({ title, intro, children }: { title: string; intro?: string; children?: ReactNode }) {
  return (
    <header className="page-head">
      <div>
        <h1>{title}</h1>
        {intro && <p className="intro">{intro}</p>}
      </div>
      {children && <div className="page-actions">{children}</div>}
    </header>
  )
}

export function Pager({ offset, limit, total, onChange }: {
  offset: number
  limit: number
  total: number
  onChange: (offset: number) => void
}) {
  if (total <= limit) return null
  return (
    <nav className="pager" aria-label="Pages">
      <button className="btn btn-secondary" disabled={offset === 0} onClick={() => onChange(Math.max(0, offset - limit))}>Previous</button>
      <span className="muted">{offset + 1}–{Math.min(offset + limit, total)} of {total}</span>
      <button className="btn btn-secondary" disabled={offset + limit >= total} onClick={() => onChange(offset + limit)}>Next</button>
    </nav>
  )
}

/** A modal dialog on the native <dialog>: focus is trapped and Escape closes it. */
export function Dialog({ title, onClose, children, wide }: { title: string; onClose: () => void; children: ReactNode; wide?: boolean }) {
  const ref = useRef<HTMLDialogElement>(null)
  // No close() on unmount: closing fires onClose, which in development's double-run of effects
  // would unmount the dialog the moment it opened. Removing the element ends the modal anyway.
  useEffect(() => {
    const d = ref.current
    if (d && !d.open) d.showModal?.()
  }, [])
  const titleId = useId()
  return (
    <dialog ref={ref} className={wide ? 'dialog dialog-wide' : 'dialog'} aria-labelledby={titleId} onClose={onClose}
      onCancel={(e) => { e.preventDefault(); onClose() }}>
      <header className="dialog-head">
        <h2 id={titleId}>{title}</h2>
        <button className="btn btn-ghost" onClick={onClose} aria-label="Close"><X size={18} aria-hidden="true" /></button>
      </header>
      <div className="dialog-body">{children}</div>
    </dialog>
  )
}

/** The SMET mark: a battery cell holding a charge bolt. */
export function Logo({ size = 24 }: { size?: number }) {
  return (
    <svg width={size} height={size} viewBox="0 0 32 32" aria-hidden="true" className="logo">
      <rect x="3" y="8" width="23" height="16" fill="currentColor" />
      <rect x="26" y="13" width="3" height="6" fill="currentColor" />
      <path d="M16 10.5l-5 7h4.5l-2 6 6.5-8.5h-4.5l2-4.5z" fill="var(--logo-bolt)" />
    </svg>
  )
}
