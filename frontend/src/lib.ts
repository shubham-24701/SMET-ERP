import { createContext, useCallback, useContext, useEffect, useRef, useState, type FormEvent } from 'react'
import { api, ApiError, type Me } from './api'

export const MeContext = createContext<Me | null>(null)

/** Built-in fields SMET renamed, hid or made required, per kind of record (Administration → Fields). */
export type FieldRule = { label: string; hidden: boolean; required: boolean }
export type CustomDef = { id: string; entity: string; key: string; label: string; kind: 'text' | 'long_text' | 'number' | 'date' | 'choice' | 'yes_no'
  options: string[]; required: boolean; help: string; sort: number; active: boolean; version: number }
export type FieldConfig = Record<string, { builtin: Record<string, FieldRule>; custom: CustomDef[] }>
export const FieldConfigContext = createContext<FieldConfig>({})
/** The kind of record a form edits: its fields follow that record's rules. */
export const FieldScope = createContext('')
export function useFieldRule(name?: string): FieldRule | undefined {
  const entity = useContext(FieldScope)
  const config = useContext(FieldConfigContext)
  return entity && name ? config[entity]?.builtin[name] : undefined
}
export const useMe = () => useContext(MeContext)!

/** Hides what the user can't use. The server checks every request anyway; this is for clarity only. */
export function useCan() {
  const me = useMe()
  return (perm: string) => me.permissions.includes(perm)
}

/** A timestamp in the company's time zone (Asia/Dubai by default), e.g. "28 Oct 2026, 14:05". */
export function useWhen() {
  const tz = useMe().company.timezone
  return (iso: string | null | undefined) => {
    if (!iso) return '—'
    const opts = { dateStyle: 'medium', timeStyle: 'short' } as const
    try {
      return new Date(iso).toLocaleString('en-GB', { ...opts, timeZone: tz })
    } catch { // a zone this browser doesn't know: show the browser's own time rather than nothing
      return new Date(iso).toLocaleString('en-GB', opts)
    }
  }
}

/** GET a path and keep it fresh; `reload` after writes. A null path loads nothing. */
export function useApi<T>(path: string | null) {
  const [data, setData] = useState<T | null>(null)
  const [error, setError] = useState('')
  const latest = useRef(0) // only the newest request's answer is kept: an older, slower one can't overwrite it
  const load = useCallback(async () => {
    if (!path) return
    const n = ++latest.current
    try {
      const d = await api<T>(path)
      if (n !== latest.current) return
      setData(d)
      setError('')
    } catch (e) {
      if (n === latest.current) setError(e instanceof Error ? e.message : 'Something went wrong.')
    }
  }, [path])
  // oxlint-disable-next-line react/set-state-in-effect -- loading from the API on mount is syncing with an external system
  useEffect(() => { void load() }, [load])
  return { data, error, reload: load }
}

/** Submit a form as JSON. Empty fields are left out; field errors come back from the server. */
export function useSubmit<T>(
  path: string,
  onDone: (result: T, form: HTMLFormElement) => void,
  { method = 'POST', extra = {} }: { method?: string; extra?: Record<string, unknown> } = {},
) {
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const [fields, setFields] = useState<Record<string, string>>({})
  async function submit(e: FormEvent<HTMLFormElement>) {
    e.preventDefault()
    const form = e.currentTarget
    setBusy(true)
    setError('')
    setFields({})
    try {
      const values = [...new FormData(form)].filter(([, v]) => v !== '')
      onDone(await api<T>(path, { method, body: { ...Object.fromEntries(values), ...extra } }), form)
    } catch (err) {
      const apiErr = err instanceof ApiError ? err : new ApiError(0, 'Something went wrong.')
      setError(apiErr.message)
      setFields(apiErr.fields)
    } finally {
      setBusy(false)
    }
  }
  return { busy, error, fields, submit }
}

/** Run one write at a time from a button; the error stays on screen until the next try. */
export function useAction(onDone: () => void) {
  const [busy, setBusy] = useState('')
  const [error, setError] = useState('')
  async function run(key: string, fn: () => Promise<unknown>, confirmText?: string) {
    if (confirmText && !window.confirm(confirmText)) return
    setBusy(key)
    setError('')
    try {
      await fn()
      onDone()
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Something went wrong.')
    } finally {
      setBusy('')
    }
  }
  return { busy, error, run }
}
