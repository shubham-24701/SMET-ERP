import { expect, test, type Page } from '@playwright/test'
import { ADMIN, go, newPassword, seedPeople, shot, signIn, signInAsAdmin, signOut } from './helpers'

// Synthetic data only. The administrator adds a field and adjusts built-in ones, adds a service
// status; Maya sees the forms change, fills the new field, and finds it in the report.
async function api(page: Page, method: 'POST' | 'GET', path: string, data?: object) {
  const { csrf_token } = await (await page.request.get('/api/v1/auth/csrf')).json()
  const r = await page.request.fetch(`/api/v1${path}`, { method, data, headers: { 'X-CSRF-Token': csrf_token } })
  expect(r.ok(), await r.text()).toBeTruthy()
  return r.json()
}

test('custom and built-in fields, the service flow, and a field in its report', async ({ page }) => {
  test.setTimeout(300_000)
  const s = String(Date.now()).slice(-5)
  await signInAsAdmin(page)
  const people = await seedPeople(page)

  // A required choice field on purchase orders.
  await go(page, 'Fields')
  await page.getByLabel('Record').selectOption({ label: 'Purchase orders' })
  await page.getByRole('button', { name: 'Add field' }).click()
  let d = page.getByRole('dialog')
  await d.getByLabel('Name').fill(`Incoterm ${s}`)
  await d.getByLabel('Kind').selectOption('choice')
  await d.getByLabel('Options, one per line').fill('FOB\nCIF')
  await d.getByLabel('Required').check()
  await d.getByRole('button', { name: 'Add field' }).click()
  await expect(d).toBeHidden()
  // Supplier reference renamed, Terms hidden.
  const renamed = page.getByLabel('Supplier reference: shown as')
  await renamed.fill("Supplier's order no.")
  await renamed.blur()
  await page.getByLabel('Hide Terms').click() // saved at once; ticked when the server answers
  await expect(page.getByLabel('Hide Terms')).toBeChecked()
  await shot(page, '60-fields')

  // A service step of SMET's own.
  await go(page, 'Rules & numbering')
  const wf = page.locator('form').filter({ has: page.getByRole('heading', { name: 'Workflows' }) })
  await wf.getByRole('button', { name: 'Add a status' }).click()
  await wf.getByLabel('Name').last().fill('Waiting for parts')
  await wf.getByLabel('In progress to Waiting for parts').check()
  await wf.getByLabel('Waiting for parts to In progress').check()
  await wf.getByRole('button', { name: 'Save workflows' }).click()
  await expect(wf.getByLabel('Waiting for parts to In progress')).toBeChecked()
  await page.reload()
  await expect(page.getByLabel('Waiting for parts to In progress')).toBeChecked()
  await signOut(page, ADMIN.name)

  // Maya's purchase order form follows the new rules.
  const [email, temp] = people['Maya Maker'].split('\n')
  await signIn(page, email, temp)
  await newPassword(page, temp)
  const sup = await api(page, 'POST', '/parties', { code: `F${s}`, name: `Field Supplier ${s}`, kinds: ['supplier'] })
  const it = await api(page, 'POST', '/items', { code: `FI-${s}`, name: 'Spacer', kind: 'component', unit_code: 'PCS', tracking: 'none' })
  await go(page, 'Purchase orders')
  await page.getByRole('button', { name: 'New purchase order' }).click()
  d = page.getByRole('dialog')
  await expect(d.getByLabel("Supplier's order no.")).toBeVisible()
  await expect(d.getByLabel('Terms')).toHaveCount(0)
  await d.getByRole('button', { name: 'Close' }).click()
  const po = await api(page, 'POST', '/purchase-orders', { supplier_id: sup.id, currency: 'AED', lines: [{ item_id: it.id, qty: 2, unit_price: 3 }] })
  await page.goto(`/purchasing/orders/${po.id}`)
  await expect(page.getByText(`Still to fill in: Incoterm ${s}.`)).toBeVisible()
  await page.getByRole('button', { name: 'Edit details' }).click()
  d = page.getByRole('dialog')
  await d.getByLabel(`Incoterm ${s} *`).selectOption('CIF')
  await d.getByRole('button', { name: 'Save details' }).click()
  await expect(d).toBeHidden()
  await expect(page.getByText('Still to fill in')).toHaveCount(0)
  await shot(page, '61-more-details')

  // The field is a column of the purchase orders report.
  await page.goto('/reports/purchase_orders')
  await expect(page.getByRole('columnheader', { name: `Incoterm ${s}` })).toBeVisible()
  await expect(page.getByRole('row').filter({ hasText: po.number })).toContainText('CIF')
})
