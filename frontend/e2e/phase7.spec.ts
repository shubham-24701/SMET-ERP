import { expect, test, type Page } from '@playwright/test'
import { ADMIN, go, newPassword, seedPeople, shot, signIn, signInAsAdmin, signOut } from './helpers'

// Synthetic data only. The administrator tries SMS in mock mode and edits a message; Maya submits a
// purchase order and Chen hears about it; a stranger asks for service with updates; Maya reads the
// reports, downloads one, and searches.
async function signedIn(page: Page, who: string, people: Record<string, string>) {
  const [email, temp] = people[who].split('\n')
  await page.goto('/')
  await signIn(page, email, temp)
  await newPassword(page, temp)
}

async function api(page: Page, method: 'POST' | 'GET', path: string, data?: object) {
  const { csrf_token } = await (await page.request.get('/api/v1/auth/csrf')).json()
  const r = await page.request.fetch(`/api/v1${path}`, { method, data, headers: { 'X-CSRF-Token': csrf_token } })
  expect(r.ok(), await r.text()).toBeTruthy()
  return r.json()
}

test('notifications, mock messages, reports with filters and export, and search', async ({ page, browser }) => {
  test.setTimeout(300_000)
  const s = String(Date.now()).slice(-5)
  await signInAsAdmin(page)
  const people = await seedPeople(page)

  // The administrator switches SMS on in mock mode: recorded here, nothing leaves the server.
  await go(page, 'Messages')
  await expect(page.getByText(/This server sends nothing outside/)).toBeVisible()
  const sms = page.locator('section.panel').filter({ has: page.getByRole('heading', { name: 'SMS' }) })
  await sms.getByRole('button', { name: 'Set up' }).click()
  let d = page.getByRole('dialog')
  await d.getByLabel('Mode').selectOption('mock')
  await d.getByRole('button', { name: 'Save channel' }).click()
  await expect(d).toBeHidden()
  await sms.getByRole('button', { name: 'Test connection' }).click()
  await expect(sms.getByText(/Connection test passed/)).toBeVisible()
  await shot(page, '50-message-channels')

  // ...and rewords the approval request, with a live preview and a check on the fields used.
  await page.getByRole('tab', { name: 'Wording' }).click()
  await page.getByRole('button', { name: 'Approval requested' }).click()
  d = page.getByRole('dialog')
  await d.getByLabel('Title').fill('{number} {total} needs you')
  await expect(d.getByText('Unknown field {total}')).toBeVisible()
  await d.getByLabel('Title').fill('Please approve {number}')
  await expect(d.getByText('Please approve SR-2026-00012')).toBeVisible() // sample values, marked as such
  await shot(page, '51-message-wording')
  await d.getByRole('button', { name: 'Save wording' }).click()
  await expect(d).toBeHidden()
  await signOut(page, ADMIN.name)

  // Maya submits a purchase order; Chen, who approves, is told.
  await signedIn(page, 'Maya Maker', people)
  const chen = await (await browser.newContext()).newPage()
  await signedIn(chen, 'Chen Checker', people)
  const sup = await api(page, 'POST', '/parties', { code: `S${s}`, name: `Gulf Cells ${s}`, kinds: ['supplier'] })
  const cell = await api(page, 'POST', '/items', { code: `C7-${s}`, name: '12 V 100 Ah battery', kind: 'battery_unit', unit_code: 'PCS', tracking: 'serial' })
  let po = await api(page, 'POST', '/purchase-orders', { supplier_id: sup.id, currency: 'AED', lines: [{ item_id: cell.id, qty: 10, unit_price: 400 }] })
  po = await api(page, 'POST', `/purchase-orders/${po.id}/submit`, { version: po.version })
  // The worker builds the notice just after the commit; the bell checks on each page load.
  await expect(async () => {
    await chen.reload()
    await expect(chen.getByRole('link', { name: /Notifications, \d+ unread/ })).toBeVisible({ timeout: 2_000 })
  }).toPass({ timeout: 60_000 })
  await chen.getByRole('link', { name: /Notifications/ }).click()
  await expect(chen.getByRole('link', { name: `Please approve ${po.number}` })).toBeVisible()
  await shot(chen, '52-notifications')
  await chen.getByRole('link', { name: `Please approve ${po.number}` }).click()
  await expect(chen.getByRole('heading', { name: po.number })).toBeVisible()
  await chen.getByRole('link', { name: 'Notifications', exact: true }).click()
  await expect(chen.getByText('All read.')).toBeVisible()

  // A stranger asks for service on the public page and agrees to text updates.
  const serial = `P${s}1`
  const store = (await api(page, 'POST', '/warehouses', { code: `R${s}`, name: 'Reports warehouse' })).locations.find((l: { purpose: string }) => l.purpose === 'available')
  const adj = await api(page, 'POST', '/adjustments', { key: `k${s}r`.padEnd(10, 'x'), reason: 'Opening stock', lines: [{ item_id: cell.id, location_id: store.id, serials_in: [serial] }] })
  await api(chen, 'POST', `/adjustments/${adj.id}/approve`, { version: adj.version })
  const stranger = await (await browser.newContext()).newPage()
  await stranger.goto('/verify')
  await stranger.getByLabel('Battery serial number').fill(serial)
  await stranger.getByRole('button', { name: 'Check' }).click()
  await stranger.getByRole('button', { name: 'Ask for service' }).click()
  await stranger.getByLabel('Your name').fill('Site office')
  await stranger.getByLabel('Phone').fill('050 765 4321')
  await stranger.getByLabel('What is wrong').fill(`The battery alarm keeps sounding ${s}`)
  await stranger.getByLabel(/Send me updates/).check()
  await stranger.getByRole('button', { name: 'Send request' }).click()
  await expect(stranger.getByText(/SMET has received it/)).toBeVisible()
  // Maya takes it up; the stranger, who asked for updates, gets a text (mock: recorded only).
  const srs = await api(page, 'GET', '/service-requests?q=Site%20office')
  const sr = srs.items.find((x: { issue: string }) => x.issue.includes(s)) ?? srs.items[0]
  await api(page, 'POST', `/service-requests/${sr.id}/status`, { version: sr.version, to: 'under_review' })

  // Maya reads the dashboards; a figure opens the rows it counts; she filters and downloads.
  await go(page, 'Reports')
  const proc = page.locator('section.panel').filter({ has: page.getByRole('heading', { name: 'Procurement' }) })
  await expect(proc.getByRole('link', { name: 'Waiting for approval' })).toBeVisible()
  await shot(page, '53-reports')
  await proc.getByRole('link', { name: 'Waiting for approval' }).click()
  await expect(page.getByRole('heading', { name: 'Purchase orders' })).toBeVisible()
  await expect(page.getByRole('link', { name: po.number })).toBeVisible()
  await page.getByLabel('Supplier').selectOption({ label: `Gulf Cells ${s}` })
  await expect(page.getByText('1 row.')).toBeVisible()
  await shot(page, '54-report-filtered')
  const download = page.waitForEvent('download')
  await page.getByRole('button', { name: 'Download Excel' }).click()
  expect((await download).suggestedFilename()).toBe('purchase_orders.xlsx')

  // The service request's text update is in the message log, recipient masked, never sent out.
  await go(page, 'Messages')
  await page.getByRole('tab', { name: 'Sent messages' }).click()
  const row = page.getByRole('row').filter({ hasText: 'Service request status' }).first()
  await expect(row).toContainText('+9715')
  await expect(row).toContainText('Recorded, not sent')
  await expect(page.getByText('+971507654321')).toHaveCount(0)
  await shot(page, '55-sent-messages')

  // Search finds the battery and the supplier.
  await page.getByLabel('Search').fill(serial)
  await page.getByLabel('Search').press('Enter')
  await expect(page.getByRole('link', { name: serial })).toBeVisible()
  await shot(page, '56-search')
})
