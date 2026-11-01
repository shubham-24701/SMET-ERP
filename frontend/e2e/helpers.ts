import { expect, type Page } from '@playwright/test'

// Synthetic test data only.
export const ADMIN = { company: 'Demo Trading LLC', name: 'Test Admin', email: 'admin@example.test', password: 'correct-horse-battery' }

export const go = (page: Page, name: string) => page.getByRole('navigation', { name: 'Main' }).getByRole('link', { name }).click()
export const shot = (page: Page, name: string) => page.screenshot({ path: `e2e-screens/${name}.png`, fullPage: true })

export async function signIn(page: Page, email: string, password: string) {
  await page.getByLabel('Email').fill(email)
  await page.getByLabel('Password').fill(password)
  await page.getByRole('button', { name: 'Sign in' }).click()
}

/** First run creates the company; later runs on the same database sign in. */
export async function signInAsAdmin(page: Page) {
  await page.goto('/')
  const heading = page.getByRole('heading', { level: 1 })
  await expect(heading).toHaveText(/Set up SMET Operations|Sign in/)
  if ((await heading.textContent())?.includes('Set up')) {
    await page.getByLabel('Company name').fill(ADMIN.company)
    await page.getByLabel('Your name').fill(ADMIN.name)
    await page.getByLabel('Email').fill(ADMIN.email)
    await page.getByLabel('Password').fill(ADMIN.password)
    await page.getByRole('button', { name: 'Set up and sign in' }).click()
  } else {
    await signIn(page, ADMIN.email, ADMIN.password)
  }
  await expect(page.getByRole('heading', { name: 'Overview' })).toBeVisible()
}

export async function signOut(page: Page, name: string) {
  await page.getByRole('button', { name: new RegExp(name) }).click()
  await page.getByRole('button', { name: 'Sign out' }).click()
  await expect(page.getByRole('heading', { name: 'Sign in' })).toBeVisible()
}
