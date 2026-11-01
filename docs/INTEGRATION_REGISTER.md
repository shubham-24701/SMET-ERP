# Integration register

The proposal names four possible integrations. None is built yet; nothing is switched on and no
account exists. Each will get a provider-independent interface, a mock for tests, a configuration
screen with encrypted credentials and a connection test, and stays **off until the owner approves and
activates it**. Prices will be checked from the providers' own pages before any recommendation.

| Integration | Workflows it serves | Phase | Status | Needs from SMET / owner | Cost |
| --- | --- | --- | --- | --- | --- |
| Email | Warranty confirmations, maintenance and AMC reminders, service updates, approval requests, password resets | 7 | Not built | Which mailbox sends (Microsoft 365 or Google Workspace via SMTP, or a transactional service); a sender address | Often free with an existing mailbox; transactional services charge per volume. To be priced |
| SMS / WhatsApp | Customer reminders and service notifications | 7 | Not built | Choice of channel and provider; for WhatsApp, a Meta business account and approved templates; consent for each recipient | Per message; to be priced |
| Battery supplier / BMS API | Receiving maintenance alerts; obtaining reset codes | 6 | Not built: **no supplier documentation yet** | Supplier name, API documentation, credentials, test environment | Unknown until the supplier answers |
| Cloud file storage | Installation photos, warranty evidence, service documents | 7 | Not built: files will be stored on the server's disk first, behind permission checks | Hosting decision (Phase 8) | Small (per GB); to be priced with hosting |

QR codes and serial-number verification are built into the platform with an open-source library (no
paid QR service), as the proposal states.

Until a supplier API is confirmed, maintenance reset codes follow a manual process: authorised staff
record the code the supplier issued, against the service request, and the code is stored encrypted
and never logged (Phase 6).
