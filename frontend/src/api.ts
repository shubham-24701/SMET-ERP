export class ApiError extends Error {
  status: number
  fields: Record<string, string>
  constructor(status: number, message: string, fields: Record<string, string> = {}) {
    super(message)
    this.status = status
    this.fields = fields
  }
}

export type Me = {
  user: { id: string; name: string; email: string; must_change_password: boolean }
  company: { name: string; timezone: string; currency: string }
  roles: string[]
  permissions: string[]
}

let csrf: string | null = null

async function csrfToken(): Promise<string> {
  if (!csrf) {
    const res = await fetch('/api/v1/auth/csrf', { credentials: 'same-origin' })
    csrf = (await res.json()).csrf_token as string
  }
  return csrf
}

function toError(status: number, data: unknown): ApiError {
  const detail = (data as { detail?: unknown } | null)?.detail
  if (status === 422 && Array.isArray(detail)) {
    const fields: Record<string, string> = {}
    const general: string[] = []
    for (const e of detail as { loc: string[]; msg: string }[]) {
      const field = e.loc.length > 1 ? String(e.loc.at(-1)) : ''
      const msg = e.msg.replace(/^Value error, /, '')
      if (field) fields[field] = msg
      else general.push(msg) // a check on the whole form, e.g. "An email needs a subject"
    }
    return new ApiError(status, general.join(' ') || 'Please fix the highlighted fields.', fields)
  }
  if (typeof detail === 'string') return new ApiError(status, detail)
  if (detail && typeof detail === 'object' && 'message' in detail) {
    const d = detail as { message: string; fields?: Record<string, string> }
    return new ApiError(status, d.message, d.fields ?? {})
  }
  if (status >= 500) return new ApiError(status, 'The server had a problem. Please try again.')
  return new ApiError(status, `Request failed (${status}).`)
}

/** Downloads a file from /api/v1 (exports), keeping the session cookie. */
export async function download(path: string, filename: string) {
  const res = await fetch(`/api/v1${path}`, { credentials: 'same-origin' })
  if (!res.ok) throw toError(res.status, await res.json().catch(() => null))
  const url = URL.createObjectURL(await res.blob())
  const a = Object.assign(document.createElement('a'), { href: url, download: filename })
  a.click()
  URL.revokeObjectURL(url)
}

/** JSON call to /api/v1. Adds the CSRF header on writes; a 401 tells the app the session ended. */
export async function api<T>(path: string, init: { method?: string; body?: unknown } = {}): Promise<T> {
  const method = init.method ?? 'GET'
  const headers: Record<string, string> = {}
  if (init.body !== undefined) headers['Content-Type'] = 'application/json'
  if (method !== 'GET') headers['X-CSRF-Token'] = await csrfToken()
  let res: Response
  try {
    res = await fetch(`/api/v1${path}`, {
      method,
      headers,
      credentials: 'same-origin',
      body: init.body === undefined ? undefined : JSON.stringify(init.body),
    })
  } catch {
    throw new ApiError(0, 'Cannot reach the server. Is the backend running?')
  }
  if (res.status === 204) return undefined as T
  const data: unknown = await res.json().catch(() => null)
  if (!res.ok) {
    if (res.status === 401) window.dispatchEvent(new Event('session-expired'))
    throw toError(res.status, data)
  }
  return data as T
}
