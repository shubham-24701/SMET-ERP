import { afterEach, describe, expect, it, vi } from 'vitest'
import { cleanup, render, screen } from '@testing-library/react'
import App from './App'
import { serialList, words } from './ops'

afterEach(() => { cleanup(); vi.unstubAllGlobals() })

/** A fake API: each path answers with a status and a body. */
function stubApi(routes: Record<string, [number, unknown]>) {
  vi.stubGlobal('fetch', vi.fn(async (url: string) => {
    const path = url.replace('/api/v1', '').split('?')[0]
    const [status, body] = routes[path] ?? [404, { detail: 'Not found' }]
    return new Response(JSON.stringify(body), { status, headers: { 'Content-Type': 'application/json' } })
  }))
}

const me = (over: Partial<{ permissions: string[]; roles: string[]; must_change_password: boolean }> = {}) => ({
  user: { id: 'u1', name: 'Asha Rao', email: 'asha@example.test', must_change_password: over.must_change_password ?? false },
  company: { name: 'Demo Trading LLC', timezone: 'Asia/Dubai', currency: 'AED' },
  roles: over.roles ?? ['Sales Officer'],
  permissions: over.permissions ?? [],
})

describe('App', () => {
  it('opens first-run setup on a fresh install', async () => {
    stubApi({ '/auth/setup-status': [200, { needs_setup: true }] })
    render(<App />)
    expect(await screen.findByRole('heading', { name: 'Set up SMET Operations' })).toBeTruthy()
  })

  it('asks to sign in when there is no session', async () => {
    stubApi({ '/auth/setup-status': [200, { needs_setup: false }], '/auth/me': [401, { detail: 'Not signed in' }] })
    render(<App />)
    expect(await screen.findByRole('heading', { name: 'Sign in' })).toBeTruthy()
  })

  it('makes a temporary password be replaced before anything else', async () => {
    stubApi({ '/auth/setup-status': [200, { needs_setup: false }], '/auth/me': [200, me({ must_change_password: true })] })
    render(<App />)
    expect(await screen.findByRole('heading', { name: 'Choose your password' })).toBeTruthy()
  })

  it('shows only the menu items the roles allow', async () => {
    stubApi({ '/auth/setup-status': [200, { needs_setup: false }], '/auth/me': [200, me({ permissions: ['audit.view'] })] })
    render(<App />)
    const nav = await screen.findByRole('navigation', { name: 'Main' })
    expect(nav.textContent).toContain('Audit log')
    expect(nav.textContent).not.toContain('Users')
  })

  it('explains when the server cannot be reached', async () => {
    vi.stubGlobal('fetch', vi.fn(async () => { throw new TypeError('offline') }))
    render(<App />)
    expect(await screen.findByRole('alert')).toBeTruthy()
    expect(screen.getByText(/Cannot reach the server/)).toBeTruthy()
  })
})


it('splits, trims and upper-cases scanned serial numbers', () => {
  expect(serialList(' c-1\nC-2, c-3;;  c-4 ')).toEqual(['C-1', 'C-2', 'C-3', 'C-4'])
  expect(serialList('')).toEqual([])
})

it('shows statuses as words', () => {
  expect(words('partly_received')).toBe('Partly received')
  expect(words('under_assembly')).toBe('Built, awaiting inspection')
  expect(words('closed')).toBe('Closed')
})
