import { expect, test, type Page } from '@playwright/test'
import { ADMIN, go, newPassword, seedPeople, shot, signIn, signInAsAdmin, signOut } from './helpers'

// Synthetic data only. Maya coordinates the installation; Chen is the technician on site.
async function signedIn(page: Page, who: string, people: Record<string, string>) {
  const [email, temp] = people[who].split('\n')
  await page.goto('/')
  await signIn(page, email, temp)
  await newPassword(page, temp)
}

async function api(page: Page, method: 'POST' | 'GET' | 'PUT', path: string, data?: object) {
  const { csrf_token } = await (await page.request.get('/api/v1/auth/csrf')).json()
  const r = await page.request.fetch(`/api/v1${path}`, { method, data, headers: { 'X-CSRF-Token': csrf_token } })
  expect(r.ok(), await r.text()).toBeTruthy()
  return r.json()
}

const JPEG = Buffer.concat([Buffer.from([0xff, 0xd8, 0xff, 0xe0]), Buffer.alloc(200, 48)])

test('batteries delivered to a site are installed, commissioned and traced to a public QR check', async ({ page, browser }) => {
  test.setTimeout(180_000)
  const s = String(Date.now()).slice(-5)
  await signInAsAdmin(page)
  const people = await seedPeople(page)
  await signOut(page, ADMIN.name)
  await signedIn(page, 'Maya Maker', people)
  const chen = await (await browser.newContext()).newPage()
  await signedIn(chen, 'Chen Checker', people)

  // Delivered to the site through the API (the screens for this are the Phase 4 journey).
  const wh = await api(page, 'POST', '/warehouses', { code: `I${s}`, name: 'Install warehouse' })
  const store = wh.locations.find((l: { purpose: string }) => l.purpose === 'available')
  const unit = await api(page, 'POST', '/items', { code: `CELL-${s}`, name: '2 V 600 Ah cell', kind: 'battery_unit', unit_code: 'PCS', tracking: 'serial' })
  const serials = [1, 2, 3].map((i) => `C${s}${i}`)
  const adj = await api(page, 'POST', '/adjustments', { key: `k${s}i`.padEnd(10, 'x'), reason: 'Opening stock', lines: [{ item_id: unit.id, location_id: store.id, serials_in: serials }] })
  await api(chen, 'POST', `/adjustments/${adj.id}/approve`, { version: adj.version })
  const cust = await api(page, 'POST', '/parties', { code: `T${s}`, name: `Desert Telecom ${s}`, kinds: ['customer'] })
  const pr = await api(page, 'POST', '/projects', { name: `Tower power ${s}`, customer_id: cust.id })
  const site = await api(page, 'POST', `/projects/${pr.id}/sites`, { name: 'Tower A', address: 'Al Quoz, Dubai' })
  let qt = await api(page, 'POST', '/quotations', { customer_id: cust.id, project_id: pr.id, lines: [{ line_type: 'battery', item_id: unit.id, qty: 3, unit_price: 400 }] })
  qt = await api(page, 'POST', `/quotations/${qt.id}/submit`, { version: qt.revisions[0].version })
  qt = await api(chen, 'POST', `/quotations/${qt.id}/approve`, { version: qt.revisions[0].version })
  qt = await api(page, 'POST', `/quotations/${qt.id}/answer`, { version: qt.revisions[0].version, accepted: true, by: 'Buyer', on: new Date().toLocaleDateString('en-CA') })
  const so = await api(page, 'POST', `/quotations/${qt.id}/convert`, { version: qt.revisions[0].version })
  let dn = await api(page, 'POST', '/dispatches', { order_id: so.id, warehouse_id: wh.id, site_id: site.id, lines: [{ order_line_id: so.lines[0].id, qty: 3, serials }] })
  dn = await api(page, 'POST', `/dispatches/${dn.id}/send`, { version: dn.version, key: `k${s}send`.padEnd(10, 'x') })
  dn = await api(page, 'GET', `/dispatches/${dn.id}`)
  await api(page, 'POST', `/dispatches/${dn.id}/deliver`, { version: dn.version, key: `k${s}dlv`.padEnd(10, 'x'), received_by: 'Site guard', delivered_on: new Date().toLocaleDateString('en-CA') })
  await api(page, 'POST', '/stage-checks', { stage: 'installation', kind: 'check', text: `Terminals torqued ${s}` })
  await api(page, 'POST', '/stage-checks', { stage: 'commissioning', kind: 'reading', text: `Float voltage ${s}`, unit: 'V', min_value: 6.6, max_value: 6.9, confirmed: true })

  // Maya plans a bank at the site and schedules Chen.
  await page.goto(`/projects/${pr.id}`)
  await page.getByRole('button', { name: 'Add battery bank' }).click()
  let d = page.getByRole('dialog')
  await d.getByLabel('Name').fill('Bank 1')
  await d.getByLabel('Configuration').fill('3 × 2 V in series, 6 V')
  await d.getByRole('button', { name: 'Add bank' }).click()
  await expect(d).toBeHidden()
  await page.getByRole('button', { name: 'Schedule visit' }).click()
  d = page.getByRole('dialog')
  await d.getByRole('checkbox', { name: /Bank 1/ }).check()
  for (const c of await d.getByRole('checkbox', { name: 'Chen Checker' }).all()) await c.check() // earlier journeys add their own Chen
  await d.getByLabel('Site contact').fill('Omar')
  await d.getByLabel('Access requirements').fill('Gate pass from the landlord')
  await d.getByRole('button', { name: 'Schedule visit' }).click()
  await expect(page.getByRole('heading', { level: 1 })).toHaveText(/^VIS-\d{4}-\d+ · Installation$/)

  // Chen finds it among his visits, places the cells, records the work and completes it.
  await go(chen, 'Site visits')
  await chen.getByLabel('Only my visits').check()
  await chen.getByRole('row', { name: /Tower A/ }).getByRole('link').click()
  await chen.getByRole('button', { name: 'Place batteries' }).click()
  d = chen.getByRole('dialog')
  await d.getByRole('button', { name: 'Number them in order from 1' }).click()
  await d.getByRole('button', { name: 'Place 3 batteries' }).click()
  await expect(d).toBeHidden()
  await chen.getByRole('button', { name: 'Add the 3 batteries placed in these banks' }).click()
  await chen.getByRole('checkbox', { name: `Terminals torqued ${s}` }).check()
  await chen.getByRole('button', { name: 'Save', exact: true }).click()
  await expect(chen.getByText('Photographs: 0 of 1')).toBeVisible()
  chen.once('dialog', (x) => x.accept())
  await chen.getByRole('button', { name: 'Save and complete' }).click()
  await expect(chen.getByText(/Not ready to complete: Photographs: 0 of 1/)).toBeVisible() // refused, and the form still works after
  await chen.getByLabel('File').setInputFiles({ name: 'rack.jpg', mimeType: 'image/jpeg', buffer: JPEG })
  await chen.getByLabel('Caption').fill('Rack after installation')
  await chen.getByRole('button', { name: 'Add', exact: true }).click()
  await expect(chen.getByText('Everything SMET requires is recorded.')).toBeVisible()
  await shot(chen, '30-visit')
  chen.once('dialog', (x) => x.accept())
  await chen.getByRole('button', { name: 'Save and complete' }).click()
  await expect(chen.getByText(/^Completed on/)).toBeVisible()

  // Commissioning: a reading outside SMET's confirmed limits is flagged and stops completion.
  const bank = (await api(page, 'GET', `/projects/${pr.id}/installation`)).banks[0]
  const cv = await api(page, 'POST', '/visits', { site_id: site.id, stage: 'commissioning', bank_ids: [bank.id], scheduled_on: new Date().toLocaleDateString('en-CA'), technician_ids: [] })
  await chen.goto(`/installation/visits/${cv.id}`)
  await chen.getByLabel(`Float voltage ${s}`).fill('7.4')
  await expect(chen.getByText('· outside')).toBeVisible()
  await chen.getByLabel(`Float voltage ${s}`).fill('6.75')
  chen.once('dialog', (x) => x.accept())
  await chen.getByRole('button', { name: 'Save and complete' }).click()
  await expect(chen.getByText(/^Completed on/)).toBeVisible()

  // The bank shows its stages; a cell's lifecycle runs from stock to commissioning.
  await page.goto(`/installation/banks/${bank.id}`)
  await expect(page.getByText('Commissioned', { exact: true }).first()).toBeVisible()
  await page.getByRole('link', { name: serials[0] }).first().click()
  await expect(page.getByRole('heading', { name: 'Lifecycle' })).toBeVisible()
  for (const t of ['Delivered', 'Placed in a battery bank', 'Installed', 'Commissioned']) await expect(page.getByText(t, { exact: true }).first()).toBeVisible()
  await shot(page, '31-lifecycle')

  // The label's QR leads anyone to the public check, which shows nothing about the customer.
  const serialId = page.url().split('/').at(-1)!
  const label = (await api(page, 'GET', `/serial-labels?ids=${serialId}`)).items[0]
  await page.goto(`/print/labels?ids=${serialId}`)
  await expect(page.getByRole('img', { name: `QR code for ${serials[0]}` })).toBeVisible()
  await shot(page, '32-label')
  const stranger = await (await browser.newContext()).newPage()
  await stranger.goto(`/v/${label.url.split('/').at(-1)}`)
  await expect(stranger.getByRole('heading', { name: 'Genuine SMET battery' })).toBeVisible()
  await expect(stranger.getByText(serials[0])).toBeVisible()
  await expect(stranger.getByText(`Desert Telecom ${s}`)).toHaveCount(0)
  await expect(stranger.getByText('Tower A')).toHaveCount(0)

  // Find a battery by its scanned label.
  await page.goto('/')
  await go(page, 'Find a battery')
  await page.getByLabel('Label, serial or supplier serial').fill(label.url)
  await page.getByRole('button', { name: 'Find' }).click()
  await expect(page.getByRole('heading', { level: 1 })).toHaveText(serials[0])

  // Chen's visit page works at phone width without sideways scrolling.
  await chen.setViewportSize({ width: 390, height: 844 })
  await chen.goto(`/installation/visits/${cv.id}`)
  await expect(chen.getByRole('heading', { level: 1 })).toBeVisible()
  expect(await chen.evaluate(() => document.documentElement.scrollWidth)).toBeLessThanOrEqual(390)
  await shot(chen, '33-visit-phone')
})
