# Roles, permissions and data boundaries

Source of truth: `backend/app/rbac.py` (`PERMISSIONS`, `TEMPLATES`). Roles are configurable in
**Roles & permissions**; the templates below are where SMET starts, not fixed rules.

## How access works

- A **permission** is `module.action`. Actions follow the proposal: view, create, edit, delete,
  submit, approve, reject, export, configure, sensitive. Each phase adds the permissions of the
  modules it delivers, and gives them to the matching templates once.
- A **role** is a named set of permissions. A person can hold several roles and may do whatever any
  of them allows. Changes take effect on the person's next request (access is read fresh each time).
- The **server checks every request**. Hidden menu items are for clarity only; a test walks every API
  route and proves a user without the permission gets 403 (`tests/test_security_matrix.py`).
- **Data boundaries** (from Phase 2 on): staff see company records their permissions allow; a
  distributor's users see only that distributor's records; a customer's users only that customer's.
  Enforced in the queries, not in the screens.

## Guards against privilege escalation

| Rule | Where |
| --- | --- |
| Nobody changes their own roles or switches off their own account | `admin.update_user` |
| Nobody edits or deletes a role they hold | `admin._editable_role` |
| At least one active person keeps both `users.edit` and `roles.configure` (no lock-out) | `admin._ensure_an_admin_remains` |
| Portal roles (distributor, customer) can't be given to staff accounts | `admin._checked_roles` |
| Giving roles when adding a user needs `users.edit`, not only `users.create` | `admin.create_user` |
| Only known permissions can be granted | `admin._checked_perms` |
| Every role and user change is audited with the permissions added and removed and the reason given | `audit_events` |
| The System Administrator template holds no business permission (no approvals, stock or warranty changes) | `test_admin_holds_no_business_permissions` |
| Self-approval of purchase orders, quotations, adjustments and warranty corrections is refused by default (configurable per document type) | Phase 2 onwards |

Residual risk, accepted for now: an administrator can create a role with an approval permission and
give it to a second account they control. Mitigation: it is audited, and Management can review role
changes in the audit log; a two-person rule for permission changes can be added if SMET wants it.

## Role templates

| Role | For | Purpose (from the proposal) | Phase 1 permissions | Gains in |
| --- | --- | --- | --- | --- |
| System Administrator | Staff | Users, roles, settings, technical configuration; no business approvals | all Phase 1 permissions below | each phase: configuration only |
| Management / Operations Manager | Staff | Dashboards and visibility across modules, specifically granted approvals | view users, roles, audit, settings, system | 2–7: view everywhere, dashboards |
| Procurement Officer | Staff | Suppliers, purchase requests, supplier quotations, POs, receipt tracking | — | 2 |
| Procurement Approver | Staff | Approves or rejects POs with reasons | — | 2 |
| Warehouse / Inventory Officer | Staff | Receipts, transfers, reservations, counts, permitted adjustments | — | 2 |
| Assembly Technician | Staff | Assigned assembly orders, consumption, checklists | — | 3 |
| Quality Inspector | Staff | Inspections, pass / hold / rework, approved test parameters | — | 3 |
| Sales Officer | Staff | Enquiries, quotations, customers, sales orders | — | 4 |
| Sales Approver | Staff | Commercial approvals, discounts, quotation approval | — | 4 |
| Project Manager | Staff | Projects, site requirements, milestones, documents, installation coordination | — | 4–5 |
| Installation Technician | Staff | Assigned visits, installation records, commissioning, handover evidence | — | 5 |
| Warranty Administrator | Staff | Registration review, activation, permitted corrections | — | 6 |
| Service Coordinator | Staff | Service intake, coverage checks, assignments, scheduling | — | 6 |
| Service Technician | Staff | Assigned jobs, diagnostics, work performed, parts used | — | 6 |
| Maintenance Code Authorizer | Staff | Explicitly authorised reset-code workflows (sensitive) | — | 6 |
| Distributor User | Distributor portal | Only this distributor's records and registrations | — | 6 |
| Customer User | Customer portal | Only this customer's batteries, warranties, service requests, documents | — | 6 |
| Public Visitor | No account | Limited warranty verification by serial or QR; registration and service-request entry points | not a role in the database: the public pages are fixed and return only approved fields | 6 |

## Phase 1 permission catalogue

| Permission | Module | Allows | System Administrator | Management / Operations Manager |
| --- | --- | --- | --- | --- |
| `users.view` | Users | See users and the roles they hold | ✓ | ✓ |
| `users.create` | Users | Add users | ✓ |  |
| `users.edit` | Users | Change a user's roles, reset passwords, deactivate and reactivate | ✓ |  |
| `roles.view` | Roles & permissions | See roles and what each allows | ✓ | ✓ |
| `roles.configure` | Roles & permissions | Create, change and delete roles | ✓ |  |
| `audit.view` | Audit log | Read the audit log | ✓ | ✓ |
| `audit.export` | Audit log | Download the audit log as CSV | ✓ |  |
| `settings.view` | Company settings | See company settings | ✓ | ✓ |
| `settings.configure` | Company settings | Change company name, time zone and currency | ✓ |  |
| `system.view` | System health | See system health and background jobs | ✓ | ✓ |

Signed in without any permission, a person can still see their own profile and change their own
password (`/auth/me`, `/auth/password`).
