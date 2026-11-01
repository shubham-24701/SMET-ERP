# Integration register

The proposal names four possible integrations. Email, SMS and WhatsApp adapters are built (Phase 7)
and tested against recorded requests only; nothing is switched on and no account exists. Each will get a provider-independent interface, a mock for tests, a configuration
screen with encrypted credentials and a connection test, and stays **off until the owner approves and
activates it**. Prices will be checked from the providers' own pages before any recommendation.

| Integration | Workflows it serves | Phase | Status | Needs from SMET / owner | Cost |
| --- | --- | --- | --- | --- | --- |
| Email (SMTP) | Staff copies of notifications (approvals, late deliveries, visits, renewals) for those who opt in; customer updates on service requests and warranty decisions with consent | 7 | **Built, mock-tested.** Any SMTP server (Microsoft 365, Google Workspace, a transactional service); STARTTLS/SSL; password encrypted; connection test signs in without sending; unit-tested against a fake SMTP server. Not run against a real mailbox | Which mailbox sends; a sender address; the owner's approval; EXTERNAL_SENDS set on the server | Often free with an existing mailbox. To be priced |
| SMS (Twilio) | Customer service-request and warranty updates, with consent | 7 | **Built, mock-tested.** Twilio Messages API (send, status follow-up); auth token encrypted; connection test reads the account. Requests checked against recorded calls only; not run against a real or test account | Choice of provider (another SMS provider needs its own adapter, about a day's work); account; sender number; approval | Per message; to be priced |
| WhatsApp (Meta Cloud API) | Same as SMS, when set up | 7 | **Built, mock-tested.** Sends Meta-approved templates only (the message's fields become its parameters); token encrypted; connection test reads the number. Delivery reports need a webhook (Phase 8). Not run against a real account | Meta business account, phone number ID, approved templates (question 39); approval | Per conversation; to be priced |
| Battery supplier / BMS API | Receiving maintenance alerts; obtaining reset codes | 6 | **Manual process built** (authorised staff type in the supplier's code; encrypted, audited, erased after retention). No supplier was named and no documentation exists, so no endpoint was assumed and nothing claims automatic generation, expiry or clearing | Supplier name, API documentation, credentials, test environment (question 22) | Unknown until the supplier answers |
| Cloud file storage (S3-compatible) | Installation photos, warranty evidence, service documents | 8 | **Built, tested with a stand-in client.** FILES_BACKEND=s3 with endpoint, bucket, region and keys in `.env`; objects encrypted at rest by the bucket (SSE); downloads still checked against the record's permission; health check reads the bucket. Needs `uv add boto3` when switched on. Not run against a real bucket. Default: this computer's disk | Hosting decision; a bucket and an access key limited to it | Small (per GB); to be priced with hosting |
| Virus scanner | Every upload | 8 | **Hook built**, tested with a scripted scanner: MALWARE_SCAN_COMMAND reads the file on stdin (exit 0 clean, 1 found); refusals and failures stop the upload. ClamAV is free and open source | Install ClamAV (`clamdscan`) on the server and set the command | Free (ClamAV) |

How a channel is switched on (Administration → Messages): **off** (default) → **mock** (recorded in the
log, nothing sent) → **sandbox** (every message goes to one test recipient) → **production**. The server
setting `EXTERNAL_SENDS` (off · sandbox · production, default off) caps every channel, so nothing reaches
a provider until whoever runs the server allows it, whatever the screen says. Messages are queued,
retried with a growing delay when the provider is unreachable, never sent twice (a send interrupted
mid-call is marked "unknown", not repeated), skipped for opted-out addresses, and logged with the
recipient masked.

QR codes and serial-number verification are built into the platform with an open-source library (no
paid QR service), as the proposal states.

Until a supplier API is confirmed, maintenance reset codes follow a manual process: authorised staff
record the code the supplier issued, against the service request, and the code is stored encrypted
and never logged (Phase 6).
