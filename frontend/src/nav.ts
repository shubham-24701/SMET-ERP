import { Activity, Building2, LayoutDashboard, ScrollText, ShieldCheck, Users, type LucideIcon } from 'lucide-react'

/** `perm`: shown only to users holding it (the server enforces it regardless). */
export type NavItem = { path: string; label: string; icon: LucideIcon; perm?: string }

// Business modules join this list as each phase delivers them; nothing here is a placeholder.
export const NAV: { group: string; items: NavItem[] }[] = [
  { group: 'Overview', items: [{ path: '/', label: 'Dashboard', icon: LayoutDashboard }] },
  {
    group: 'Administration',
    items: [
      { path: '/admin/users', label: 'Users', icon: Users, perm: 'users.view' },
      { path: '/admin/roles', label: 'Roles & permissions', icon: ShieldCheck, perm: 'roles.view' },
      { path: '/admin/audit', label: 'Audit log', icon: ScrollText, perm: 'audit.view' },
      { path: '/admin/company', label: 'Company settings', icon: Building2, perm: 'settings.view' },
      { path: '/admin/system', label: 'System health', icon: Activity, perm: 'system.view' },
    ],
  },
]
