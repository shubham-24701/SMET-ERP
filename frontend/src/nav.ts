import {
  Activity, ArrowLeftRight, Barcode, Briefcase, Calculator, FileSignature, Inbox, ReceiptText, Send, Boxes, Building2, ClipboardCheck, ClipboardList, Factory, FileText, LayoutDashboard,
  BadgeCheck, BellRing, CalendarClock, Headset, ListTree, MapPin, ShieldHalf, Package, ScanLine, Wrench, Layers, PackageCheck, ScrollText, Settings2, ShieldCheck, ShoppingCart, Truck, Users, Warehouse, type LucideIcon,
} from 'lucide-react'

/** `perm`: shown only to users holding it, or one of `a|b` (the server enforces it regardless). */
export type NavItem = { path: string; label: string; icon: LucideIcon; perm?: string }

// Business modules join this list as each phase delivers them; nothing here is a placeholder.
export const NAV: { group: string; items: NavItem[] }[] = [
  { group: 'Overview', items: [{ path: '/', label: 'Dashboard', icon: LayoutDashboard }] },
  {
    group: 'Sales',
    items: [
      { path: '/sales/enquiries', label: 'Enquiries', icon: Inbox, perm: 'sales.view' },
      { path: '/sales/quotations', label: 'Quotations', icon: FileSignature, perm: 'sales.view' },
      { path: '/sales/orders', label: 'Sales orders', icon: ReceiptText, perm: 'sales.view' },
      { path: '/dispatch', label: 'Dispatch', icon: Send, perm: 'dispatch.view' },
    ],
  },
  {
    group: 'Projects',
    items: [
      { path: '/projects', label: 'Projects', icon: Briefcase, perm: 'projects.view' },
      { path: '/projects/rules', label: 'Sizing rules', icon: Calculator, perm: 'projects.view' },
    ],
  },
  {
    group: 'Installation',
    items: [
      { path: '/installation/visits', label: 'Site visits', icon: MapPin, perm: 'installation.view' },
      { path: '/installation/banks', label: 'Battery banks', icon: Layers, perm: 'installation.view' },
      { path: '/trace', label: 'Find a battery', icon: ScanLine, perm: 'stock.view' },
      { path: '/installation/checks', label: 'Site checks', icon: Wrench, perm: 'installation.view|installation.configure' },
    ],
  },
  {
    group: 'After-sales',
    items: [
      { path: '/service', label: 'Service requests', icon: Headset, perm: 'service.view' },
      { path: '/amc', label: 'Maintenance contracts', icon: CalendarClock, perm: 'amc.view' },
      { path: '/warranty', label: 'Warranty registrations', icon: BadgeCheck, perm: 'warranty.view' },
      { path: '/warranty/policies', label: 'Warranty policies', icon: ShieldHalf, perm: 'warranty.view' },
      { path: '/service/alerts', label: 'Maintenance alerts', icon: BellRing, perm: 'codes.view' },
    ],
  },
  {
    group: 'Purchasing',
    items: [
      { path: '/purchasing/requests', label: 'Purchase requests', icon: FileText, perm: 'purchasing.view' },
      { path: '/purchasing/orders', label: 'Purchase orders', icon: ShoppingCart, perm: 'purchasing.view' },
    ],
  },
  {
    group: 'Stock',
    items: [
      { path: '/stock', label: 'Stock', icon: Boxes, perm: 'stock.view' },
      { path: '/stock/moves', label: 'Movements', icon: ArrowLeftRight, perm: 'stock.view' },
      { path: '/stock/serials', label: 'Serial numbers', icon: Barcode, perm: 'stock.view' },
      { path: '/stock/reservations', label: 'Reservations', icon: PackageCheck, perm: 'stock.view' },
      { path: '/stock/corrections', label: 'Counts & corrections', icon: ClipboardList, perm: 'stock.view' },
    ],
  },
  {
    group: 'Assembly',
    items: [
      { path: '/assembly/orders', label: 'Assembly orders', icon: Factory, perm: 'assembly.view' },
      { path: '/assembly/inspection', label: 'Final inspection', icon: ClipboardCheck, perm: 'assembly.view' },
      { path: '/assembly/boms', label: 'Bills of materials', icon: ListTree, perm: 'bom.view' },
    ],
  },
  {
    group: 'Master data',
    items: [
      { path: '/masters/items', label: 'Items', icon: Package, perm: 'items.view' },
      { path: '/masters/parties', label: 'Suppliers & customers', icon: Truck, perm: 'parties.view' },
      { path: '/masters/warehouses', label: 'Warehouses', icon: Warehouse, perm: 'warehouses.view' },
    ],
  },
  {
    group: 'Administration',
    items: [
      { path: '/admin/users', label: 'Users', icon: Users, perm: 'users.view' },
      { path: '/admin/roles', label: 'Roles & permissions', icon: ShieldCheck, perm: 'roles.view' },
      { path: '/admin/audit', label: 'Audit log', icon: ScrollText, perm: 'audit.view' },
      { path: '/admin/company', label: 'Company settings', icon: Building2, perm: 'settings.view' },
      { path: '/admin/rules', label: 'Rules & numbering', icon: Settings2, perm: 'settings.view' },
      { path: '/admin/system', label: 'System health', icon: Activity, perm: 'system.view' },
    ],
  },
]
