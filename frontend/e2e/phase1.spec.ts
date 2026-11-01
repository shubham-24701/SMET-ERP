import { expect, test } from '@playwright/test'
import { ADMIN, go, shot, signIn, signInAsAdmin, signOut } from './helpers'

test('admin adds a person with a role; they replace the temporary password and see only their work', async ({ page }) => {
  await signInAsAdmin(page)
  await shot(page, '01-overview')

  // Add a person with one role; the temporary password is shown once.
  await go(page, 'Users')
  await page.getByRole('button', { name: 'Add user' }).click()
  const dialog = page.getByRole('dialog', { name: 'Add user' })
  await dialog.getByLabel('Name').fill('Ravi Kumar')
  await dialog.getByLabel('Email').fill('ravi@example.test')
  await dialog.getByLabel(/Procurement Officer/).check()
  await dialog.getByRole('button', { name: 'Add user' }).click()
  const temp = (await dialog.locator('code.secret').textContent())!.trim()
  expect(temp.length).toBeGreaterThanOrEqual(16)
  await dialog.getByRole('button', { name: 'Close' }).click()
  const row = page.getByRole('row', { name: /Ravi Kumar/ })
  await expect(row).toContainText('Procurement Officer')
  await expect(row).toContainText('Waiting for first sign-in')

  // The count cards filter the list.
  await page.getByRole('button', { name: /Waiting for first sign-in/ }).click()
  await expect(page.getByRole('row', { name: /Test Admin/ })).toHaveCount(0)
  await shot(page, '02-users')

  // Nobody edits a role they hold.
  await go(page, 'Roles & permissions')
  await page.getByRole('row', { name: /System Administrator/ }).getByRole('button', { name: /Edit/ }).click()
  await expect(page.getByText("You hold this role, so you can't change it.")).toBeVisible()
  await page.getByRole('button', { name: 'Close' }).click()

  // Company settings preview what the time zone does.
  await go(page, 'Company settings')
  await expect(page.getByText(/Now in Asia\/Dubai/)).toBeVisible()

  // The audit log has the new user, with who added them.
  await go(page, 'Audit log')
  await expect(page.getByRole('row', { name: /Ravi Kumar added/ })).toContainText(ADMIN.name)
  await shot(page, '03-audit')
  await signOut(page, ADMIN.name)

  // The new person replaces the temporary password, then sees only what their role allows.
  await signIn(page, 'ravi@example.test', temp)
  await expect(page.getByRole('heading', { name: 'Choose your password' })).toBeVisible()
  await page.getByLabel('Temporary password').fill(temp)
  await page.getByLabel('New password').fill('ravis-own-long-password')
  await page.getByRole('button', { name: 'Change password' }).click()
  await expect(page.getByRole('heading', { name: 'Overview' })).toBeVisible()
  const nav = page.getByRole('navigation', { name: 'Main' })
  await expect(nav).not.toContainText('Users')
  await expect(nav).not.toContainText('Audit log')
  // Typing an admin address shows a calm refusal, and the server refuses the data as well.
  await page.goto('/admin/users')
  await expect(page.getByRole('heading', { name: "You don't have access to this page" })).toBeVisible()
  expect((await page.request.get('/api/v1/users')).status()).toBe(403)
})

test('phone width: the menu folds away and the page never scrolls sideways', async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 844 })
  await signInAsAdmin(page)
  await expect(page.getByRole('navigation', { name: 'Main' })).toBeHidden()
  await page.getByRole('button', { name: 'Menu' }).click()
  await page.getByRole('link', { name: 'Users', exact: true }).click()
  await expect(page.getByRole('heading', { name: 'Users', level: 1 })).toBeVisible()
  expect(await page.evaluate(() => document.documentElement.scrollWidth - window.innerWidth)).toBeLessThanOrEqual(0)
  await shot(page, '04-phone-users')
})
