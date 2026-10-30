import { expect, test } from '@playwright/test'
import { ADMIN, as, go, seedPeople, shot, signInAsAdmin } from './helpers'

test('buy, receive, inspect, assemble and pass a battery', async ({ page }) => {
  test.setTimeout(120_000)
  const stamp = String(Date.now()).slice(-5)
  const cable = `CABLE-${stamp}`
  const model = `SMET48-${stamp}`
  const cableOpt = `${cable} · Battery cable 25 mm²`
  const modelOpt = `${model} · 48 V 100 Ah lithium battery`
  await signInAsAdmin(page)
  const people = await seedPeople(page)
  await as(page, 'Maya Maker', people, ADMIN.name)

  // Master data: a warehouse (with its standard areas), a material, a battery model, a supplier.
  await go(page, 'Warehouses')
  await page.getByRole('button', { name: 'Add warehouse' }).click()
  let d = page.getByRole('dialog')
  await d.getByLabel('Code').fill(`W${stamp}`)
  await d.getByLabel('Name').fill('Dubai main warehouse')
  await d.getByRole('button', { name: 'Add warehouse' }).click()
  await expect(page.getByRole('heading', { name: /Dubai main warehouse/ }).first()).toBeVisible()

  await go(page, 'Items')
  for (const [code, kind, tracking] of [[cable, 'Assembly material', 'Quantity only'], [model, 'Battery model', 'By serial number, one per unit']]) {
    await page.getByRole('button', { name: 'Add item' }).click()
    d = page.getByRole('dialog', { name: 'Add item' })
    await d.getByLabel('Code').fill(code)
    await d.getByLabel('Name', { exact: true }).fill(code === cable ? 'Battery cable 25 mm²' : '48 V 100 Ah lithium battery')
    await d.getByLabel('Kind', { exact: true }).selectOption({ label: kind })
    await d.getByLabel('Tracking', { exact: true }).selectOption({ label: tracking })
    if (code === model) {
      await d.getByLabel('Capacity (Ah)').fill('100')
      await d.getByLabel('Voltage (V)').fill('48')
      await d.getByLabel(/Assembled by SMET/).check()
    }
    await d.getByRole('button', { name: 'Add item' }).click()
    await expect(d).toBeHidden()
  }
  await expect(page.getByRole('row', { name: new RegExp(model) })).toContainText('100 Ah')
  await shot(page, '10-items')

  await go(page, 'Suppliers & customers')
  await page.getByRole('button', { name: 'Add company' }).click()
  d = page.getByRole('dialog')
  await d.getByLabel('Code').fill(`SUP${stamp}`)
  await d.getByLabel('Name', { exact: true }).fill(`Gulf Cables ${stamp}`)
  await d.getByLabel('Supplier').check()
  await d.getByRole('button', { name: 'Add', exact: true }).click()
  await expect(d).toBeHidden()

  // A purchase order: drafted and submitted by Maya, who can't approve her own order.
  await go(page, 'Purchase orders')
  await page.getByRole('button', { name: 'New purchase order' }).click()
  d = page.getByRole('dialog')
  await d.getByLabel('Supplier', { exact: true }).selectOption({ label: `Gulf Cables ${stamp}` })
  await d.getByLabel('Item, line 1').selectOption({ label: cableOpt })
  await d.getByLabel('Quantity').fill('10')
  await d.getByLabel('Unit price').fill('12.5')
  await d.getByRole('button', { name: 'Create draft' }).click()
  await expect(page.getByRole('heading', { level: 1 })).toHaveText(/^PO-/)
  const poUrl = page.url()
  await page.getByRole('button', { name: 'Submit for approval' }).click()
  await expect(page.getByText('You raised, changed or submitted this order, so someone else approves it.')).toBeVisible()
  await shot(page, '11-po-waiting')

  // Chen approves, records the delivery, and decides: 8 accepted, 2 rejected.
  await as(page, 'Chen Checker', people, 'Maya Maker')
  await page.goto(poUrl)
  await page.getByRole('button', { name: 'Approve' }).click()
  await page.getByRole('dialog').getByRole('button', { name: 'Approve order' }).click()
  await page.getByRole('button', { name: 'Record delivery' }).click()
  d = page.getByRole('dialog')
  await d.getByLabel('Delivery note number').fill('DN-501')
  await d.getByRole('button', { name: 'Record delivery' }).click()
  await expect(d).toBeHidden()
  await page.getByRole('button', { name: new RegExp(`Decide on ${cable}`) }).click()
  d = page.getByRole('dialog')
  await d.getByLabel('Accepted', { exact: true }).fill('8')
  await d.getByLabel('Rejected', { exact: true }).fill('2')
  await d.getByRole('button', { name: 'Record decision' }).click()
  await expect(d).toBeHidden()
  const line = page.getByRole('row', { name: new RegExp(cable) })
  await expect(line).toContainText('8')
  await expect(page.getByRole('heading', { level: 1 })).toBeVisible()
  await shot(page, '12-po-received')

  await go(page, 'Stock')
  await page.getByLabel('Search code or name').fill(cable)
  await expect(page.getByRole('row', { name: new RegExp(cable) })).toContainText('8 M'.replace(' M', ''))
  await shot(page, '13-stock')

  // A bill of materials (2 m of cable per battery) and a one-line inspection checklist.
  await go(page, 'Bills of materials')
  await page.getByRole('button', { name: 'New bill of materials' }).click()
  d = page.getByRole('dialog')
  await d.getByLabel('Battery model').selectOption({ label: modelOpt })
  await d.getByLabel('Item, line 1').selectOption({ label: cableOpt })
  await d.getByLabel('Quantity').fill('2')
  await d.getByRole('button', { name: /Save as new draft/ }).click()
  await expect(d).toBeHidden()
  page.once('dialog', (x) => x.accept())
  await page.getByRole('row', { name: new RegExp(model) }).getByRole('button', { name: 'Make active' }).click()
  await expect(page.getByRole('row', { name: new RegExp(model) })).toContainText('Active')

  await go(page, 'Items')
  await page.getByRole('button', { name: `Inspection set-up for ${model}` }).click()
  d = page.getByRole('dialog')
  await d.getByRole('button', { name: 'Add check' }).click()
  await d.getByLabel('Check').fill('Terminals torqued and label applied')
  await d.getByRole('button', { name: 'Save inspection set-up' }).click()
  await expect(d).toBeHidden()

  // Assembly: plan one, release (reserves the cable), issue, build, inspect, pass.
  await go(page, 'Assembly orders')
  await page.getByRole('button', { name: 'Plan an order' }).click()
  d = page.getByRole('dialog')
  await d.getByLabel('Battery model').selectOption({ label: modelOpt })
  await d.getByLabel('Batteries to build').fill('1')
  await d.getByLabel('Warehouse').selectOption({ label: `W${stamp} · Dubai main warehouse` })
  await d.getByRole('button', { name: 'Plan order' }).click()
  await expect(page.getByRole('heading', { level: 1 })).toHaveText(/^AO-/)
  await page.getByRole('button', { name: 'Release and reserve' }).click()
  await expect(page.getByText('Released', { exact: true }).first()).toBeVisible()
  await page.getByRole('button', { name: 'Issue materials' }).click()
  await page.getByRole('dialog').getByRole('button', { name: 'Issue materials' }).click()
  await page.getByRole('button', { name: 'Record a battery' }).click()
  await page.getByRole('dialog').getByRole('button', { name: 'Record battery' }).click()
  await expect(page.getByText(/Battery SMET-\d{4}-\d+ recorded/)).toBeVisible()
  await shot(page, '14-assembly-order')
  await page.getByRole('button', { name: /^Inspect / }).click()
  d = page.getByRole('dialog')
  await d.getByLabel('Terminals torqued and label applied').check()
  await d.getByRole('button', { name: 'Record inspection' }).click()
  await expect(d).toBeHidden()
  await expect(page.getByRole('main').getByText('Available', { exact: true })).toBeVisible()

  // The battery's record: built from the cable, passed, in the main store.
  await page.getByRole('link', { name: /SMET-\d{4}-/ }).click()
  await expect(page.getByRole('heading', { name: 'What went into it' })).toBeVisible()
  await expect(page.getByRole('row', { name: new RegExp(cable) })).toContainText('2')
  await expect(page.getByText('Passed inspection')).toBeVisible()
  await shot(page, '15-serial')

  await page.setViewportSize({ width: 390, height: 844 })
  await page.goto(poUrl)
  await expect(page.getByRole('heading', { level: 1 })).toBeVisible()
  // nothing reaches past a phone's width: wide tables scroll inside their own box
  expect(await page.evaluate(() => document.documentElement.scrollWidth)).toBeLessThanOrEqual(390)
  await shot(page, '16-po-phone')
})
