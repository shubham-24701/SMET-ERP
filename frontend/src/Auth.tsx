import type { ReactNode } from 'react'
import type { Me } from './api'
import { Field, FormError, Logo } from './ui'
import { useSubmit } from './lib'

// The record every battery builds up, in order: what this system keeps track of.
const LIFECYCLE = ['Supplier receipt', 'Assembly', 'Quality check', 'In stock', 'Dispatch', 'Installation', 'Warranty', 'Service']

function AuthLayout({ title, intro, children }: { title: string; intro: string; children: ReactNode }) {
  return (
    <main className="auth">
      <section className="auth-brand" aria-label="About SMET Operations">
        <p className="brand"><Logo size={28} /> SMET Operations</p>
        <p className="auth-tagline">One record for every battery, from the supplier's crate to its last service visit.</p>
        <ol className="lifecycle">
          {LIFECYCLE.map((step) => <li key={step}>{step}</li>)}
        </ol>
      </section>
      <div className="auth-panel">
        <div className="auth-form">
          <h1>{title}</h1>
          <p className="intro">{intro}</p>
          {children}
        </div>
      </div>
    </main>
  )
}

export function LoginPage({ onDone }: { onDone: (me: Me) => void }) {
  const { busy, error, fields, submit } = useSubmit<Me>('/auth/login', onDone)
  return (
    <AuthLayout title="Sign in" intro="Use the email and password your administrator gave you.">
      <form onSubmit={submit} noValidate>
        <Field label="Email" name="email" type="email" autoComplete="username" required error={fields.email} />
        <Field label="Password" name="password" type="password" autoComplete="current-password" required error={fields.password} />
        <FormError message={error} />
        <button className="btn btn-primary btn-block" type="submit" disabled={busy}>{busy ? 'Signing in…' : 'Sign in'}</button>
      </form>
    </AuthLayout>
  )
}

export function SetupPage({ onDone }: { onDone: (me: Me) => void }) {
  const { busy, error, fields, submit } = useSubmit<Me>('/auth/setup', onDone)
  return (
    <AuthLayout title="Set up SMET Operations" intro="First run: name the company and create the first system administrator. You can add everyone else after.">
      <form onSubmit={submit} noValidate>
        <Field label="Company name" name="company_name" required minLength={2} maxLength={160} error={fields.company_name} />
        <Field label="Your name" name="name" autoComplete="name" required maxLength={120} error={fields.name} />
        <Field label="Email" name="email" type="email" autoComplete="username" required error={fields.email} />
        <Field label="Password" name="password" type="password" autoComplete="new-password" required minLength={12}
          hint="At least 12 characters." error={fields.password} />
        <FormError message={error} />
        <button className="btn btn-primary btn-block" type="submit" disabled={busy}>{busy ? 'Setting up…' : 'Set up and sign in'}</button>
      </form>
    </AuthLayout>
  )
}

export function ChangePasswordForm({ onDone, forced }: { onDone: (me: Me) => void; forced?: boolean }) {
  const { busy, error, fields, submit } = useSubmit<Me>('/auth/password', onDone)
  return (
    <form onSubmit={submit} noValidate>
      <Field label={forced ? 'Temporary password' : 'Current password'} name="current_password" type="password"
        autoComplete="current-password" required error={fields.current_password} />
      <Field label="New password" name="new_password" type="password" autoComplete="new-password" required minLength={12}
        hint="At least 12 characters. Signing in elsewhere ends when you change it." error={fields.new_password} />
      <FormError message={error} />
      <button className="btn btn-primary" type="submit" disabled={busy}>{busy ? 'Saving…' : 'Change password'}</button>
    </form>
  )
}

export function ForcedPasswordPage({ onDone }: { onDone: (me: Me) => void }) {
  return (
    <AuthLayout title="Choose your password" intro="You signed in with a temporary password. Choose your own before you continue.">
      <ChangePasswordForm onDone={onDone} forced />
    </AuthLayout>
  )
}
