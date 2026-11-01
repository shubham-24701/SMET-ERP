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

// Synthetic data only. Two operations users: work one raises, the other approves.
const PW = 'operations-pass-2026'

export async function newPassword(page: Page, temp: string) {
  await expect(page.getByRole('heading', { name: 'Choose your password' })).toBeVisible()
  await page.getByLabel('Temporary password').fill(temp)
  await page.getByLabel('New password').fill(PW)
  await page.getByRole('button', { name: 'Change password' }).click()
  await expect(page.getByRole('heading', { name: 'Overview' })).toBeVisible()
}

/** The administrator makes a role with every business permission and two people holding it. */
export async function seedPeople(page: Page) {
  const { csrf_token } = await (await page.request.get('/api/v1/auth/csrf')).json()
  const headers = { 'X-CSRF-Token': csrf_token }
  const perms = (await (await page.request.get('/api/v1/permissions')).json()).items
    .map((p: { code: string }) => p.code).filter((c: string) => !c.startsWith('users.') && !c.startsWith('roles.'))
  const role = await (await page.request.post('/api/v1/roles', { headers, data: { name: `Operations ${Date.now()}`, permissions: perms } })).json()
  const people: Record<string, string> = {}
  for (const name of ['Maya Maker', 'Chen Checker']) {
    const email = `${name.split(' ')[0].toLowerCase()}${Date.now()}@example.test`
    const r = await (await page.request.post('/api/v1/users', { headers, data: { name, email, role_ids: [role.id] } })).json()
    people[name] = `${email}\n${r.temporary_password}`
  }
  return people
}

export async function as(page: Page, who: string, people: Record<string, string>, current: string) {
  await signOut(page, current)
  const [email, temp] = people[who].split('\n')
  await signIn(page, email, temp)
  await newPassword(page, temp)
}
