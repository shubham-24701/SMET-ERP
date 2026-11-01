import { Link } from 'react-router'
import { Check } from 'lucide-react'
import { useApi, useCan, useMe, useWhen } from './lib'
import { PageHead } from './ui'

type Users = { total: number; counts: { active: number; password_pending: number; no_roles: number; deactivated: number } }
type Events = { items: { id: number; at: string; actor: string; summary: string; action: string }[]; total: number }

/** "Is the team set up to start work?", from the real user counts. */
function TeamSetup({ users }: { users: Users }) {
  const c = users.counts
  const steps: [string, boolean, string][] = [
    ['Company profile created', true, '/admin/company'],
    ['Add the people who will use the system', c.active > 1, '/admin/users'],
    ['Give every active person at least one role', c.no_roles === 0, '/admin/users'],
    ['Everyone has signed in and chosen a password', c.active > 1 && c.password_pending === 0, '/admin/users'],
  ]
  const done = steps.filter(([, ok]) => ok).length
  return (
    <section className="panel" aria-labelledby="setup-title">
      <h2 id="setup-title">Team setup</h2>
      <p className="figure">{done} of {steps.length} <span className="unit">steps done</span></p>
      <div className="meter" role="progressbar" aria-label="Team setup" aria-valuemin={0} aria-valuemax={steps.length} aria-valuenow={done}>
        <span style={{ inlineSize: `${(done / steps.length) * 100}%` }} />
      </div>
      <ul className="steps">
        {steps.map(([label, ok, link]) => (
          <li key={label} className={ok ? 'done' : ''}>
            {ok ? <Check size={16} aria-label="Done" /> : <span className="todo" aria-label="To do" />}
            {ok ? label : <Link to={link}>{label}</Link>}
          </li>
        ))}
      </ul>
    </section>
  )
}

type Roles = { items: { id: string; name: string; users: number }[] }

/** How many people hold each role, with a thin bar for its share of the active team; the largest in ink. */
function RolesInUse({ roles, team }: { roles: Roles; team: number }) {
  const used = roles.items.filter((r) => r.users > 0).sort((a, b) => b.users - a.users || a.name.localeCompare(b.name))
  const unused = roles.items.length - used.length
  return (
    <section className="panel" aria-labelledby="roles-title">
      <header className="panel-head">
        <h2 id="roles-title">Roles in use</h2>
        <Link to="/admin/roles">All {roles.items.length} roles</Link>
      </header>
      {used.length === 0 ? <p className="empty-line">Nobody holds a role yet.</p> : (
        <ul className="bar-list">
          {used.slice(0, 6).map((r, i) => (
            <li key={r.id}>
              <span className="bar-label">{r.name}</span><span className="num">{r.users} <span className="unit">of {team}</span></span>
              <span className={i === 0 ? 'bar bar-top' : 'bar'} style={{ inlineSize: `${Math.min(100, (r.users / Math.max(team, 1)) * 100)}%` }} aria-hidden="true" />
            </li>
          ))}
        </ul>
      )}
      {unused > 0 && <p className="muted small caption">{unused} {unused === 1 ? 'role is' : 'roles are'} not given to anyone yet.</p>}
    </section>
  )
}

function RecentChanges({ events }: { events: Events }) {
  const when = useWhen()
  return (
    <section className="panel" aria-labelledby="recent-title">
      <header className="panel-head">
        <h2 id="recent-title">Recent changes</h2>
        <Link to="/admin/audit">View all {events.total}</Link>
      </header>
      <ul className="feed">
        {events.items.map((e) => (
          <li key={e.id}><span>{e.summary || e.action}<span className="muted small">{e.actor}</span></span><span className="muted small nowrap">{when(e.at)}</span></li>
        ))}
      </ul>
    </section>
  )
}

type Total = { total: number }

/** What waits on this person's roles: each line is a count with the page that clears it. */
function useTotal(perm: string, path: string) {
  const can = useCan()
  return useApi<Total>(can(perm) ? path : null).data?.total
}

function WorkQueue() {
  const rows: [string, number | undefined, string][] = [
    ['Purchase orders waiting for approval', useTotal('purchasing.approve', '/purchase-orders?status=submitted&limit=1'), '/purchasing/orders'],
    ['Approved orders not yet delivered', useTotal('receipts.create', '/purchase-orders?status=approved,partly_received&limit=1'), '/purchasing/orders'],
    ['Stock corrections waiting for approval', useTotal('adjustments.approve', '/adjustments?status=pending&limit=1'), '/stock/corrections'],
    ['Items below minimum stock', useTotal('stock.view', '/stock/summary?low=true&limit=1'), '/stock'],
    ['Batteries waiting for inspection', useTotal('quality.create', '/inspections/queue?limit=1'), '/assembly/inspection'],
    ['Open purchase requests', useTotal('purchasing.create', '/purchase-requests?status=open&limit=1'), '/purchasing/requests'],
    ['Open enquiries', useTotal('sales.create', '/enquiries?status=open&limit=1'), '/sales/enquiries'],
    ['Your site visits to do', useTotal('installation.record', '/visits?mine=true&status=scheduled,started&limit=1'), '/installation/visits'],
    ['Battery banks to commission', useTotal('installation.create', '/banks?status=installed&limit=1'), '/installation/banks'],
    ['Battery banks to hand over', useTotal('installation.create', '/banks?status=commissioned&limit=1'), '/installation/banks'],
    ['Service requests still open', useTotal('service.create', '/service-requests?status=new,under_review,scheduled,in_progress,pending_action&limit=1'), '/service'],
    ['Warranty registrations to decide', useTotal('warranty.approve', '/warranty-registrations?status=pending&limit=1'), '/warranty'],
    ['Maintenance contracts to renew', useTotal('amc.create', '/amc?expiring=true&limit=1'), '/amc'],
    ['Maintenance alerts waiting for a code', useTotal('codes.reveal', '/maintenance-alerts?status=verified&limit=1'), '/service/alerts'],
    ['Dispatches planned or on the way', useTotal('dispatch.create', '/dispatches?status=planned,dispatched&limit=1'), '/dispatch'],
  ]
  const shown = rows.filter(([, n]) => n !== undefined)
  if (!shown.length) return null
  return (
    <section className="panel" aria-labelledby="work-title">
      <h2 id="work-title">Waiting for you</h2>
      <ul className="feed">
        {shown.map(([label, n, to]) => (
          <li key={label}><span>{n ? <Link to={to}>{label}</Link> : <span className="muted">{label}</span>}</span><strong className="num">{n}</strong></li>
        ))}
      </ul>
    </section>
  )
}

export function Dashboard() {
  const me = useMe()
  const can = useCan()
  const users = useApi<Users>(can('users.view') ? '/users?limit=1' : null)
  const events = useApi<Events>(can('audit.view') ? '/audit?limit=10' : null)
  const roles = useApi<Roles>(can('roles.view') ? '/roles' : null)
  const c = users.data?.counts
  // The screen's one finding: what still stops people from working.
  const finding = c && (c.no_roles || c.password_pending)
    ? [c.no_roles && `${c.no_roles} active ${c.no_roles === 1 ? 'person has' : 'people have'} no roles yet`,
       c.password_pending && `${c.password_pending} ${c.password_pending === 1 ? "hasn't" : "haven't"} signed in yet`].filter(Boolean).join('; ') + '.'
    : `You hold ${me.roles.length ? me.roles.join(', ') : 'no roles yet'}.`
  return (
    <>
      <PageHead title="Overview" intro={finding}>
        {can('users.create') && <Link className="btn btn-primary" to="/admin/users">Manage users</Link>}
      </PageHead>
      {me.roles.length === 0 && (
        <section className="panel" aria-labelledby="access-title">
          <h2 id="access-title">No roles yet</h2>
          <p>You can sign in, but your account has no roles, so there is nothing to open. Ask your system administrator for the roles your work needs.</p>
        </section>
      )}
      <div className="grid-2">
        <div className="stack">
          <WorkQueue />
          {users.data && <TeamSetup users={users.data} />}
          {roles.data && <RolesInUse roles={roles.data} team={c?.active ?? 0} />}
        </div>
        {events.data && <RecentChanges events={events.data} />}
      </div>
    </>
  )
}
