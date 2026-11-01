import { afterEach, expect, it, vi } from 'vitest'
import { act, cleanup, render, screen } from '@testing-library/react'
import { useApi } from './lib'

afterEach(() => { cleanup(); vi.unstubAllGlobals() })

function Banks({ customer }: { customer: string }) {
  const { data } = useApi<{ name: string }>(`/banks?customer_id=${customer}`)
  return <p>{data?.name ?? 'loading'}</p>
}

it('keeps the newest answer when an older one arrives later', async () => {
  const pending: Record<string, (r: Response) => void> = {}
  vi.stubGlobal('fetch', vi.fn((url: string) => new Promise<Response>((done) => { pending[url.split('=')[1]] = done })))
  const reply = (key: string) => act(async () => pending[key](new Response(JSON.stringify({ name: `banks of ${key}` }))))
  const view = render(<Banks customer="first" />)
  view.rerender(<Banks customer="chosen" />)
  await reply('chosen')
  await reply('first') // the slow answer for the customer no longer selected
  expect(screen.getByText('banks of chosen')).toBeTruthy()
})
