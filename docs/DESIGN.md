# Interface design

## Direction

- **Reference:** IBM Carbon (awesome-design-md, `ibm`): IBM Plex Sans (free, SIL OFL, self-hosted
  through `@fontsource`, no CDN), the 4 px grid, `+0.16px` body tracking, dense data, one blue.
- **App style:** taste skill, app screens, style **Achromatic ledger** (seed 1260739466), shell
  **Open plane**. Chosen over *Framed sheet* because an ERP is worked in for whole shifts: colour
  belongs to data and status, not to the chrome.
- **Where they disagreed**, the app-screen rules won: square Carbon corners became the style's tight
  radii (8 px cards, 6 px controls, 4 px tags); Carbon's filled blue buttons became a near-black
  primary; Carbon's dark header was dropped (the study found no dark sidebar or bar on a light screen).

## Tokens (light; dark mode mirrors them on true neutrals)

| Role | Value | Use |
| --- | --- | --- |
| Ground | `#f2f4f7` steel blue-grey | Sidebar and page: the battery casing, the screen's "face" |
| Surface | `#ffffff` | Cards, tables, dialogs |
| Ink | `#161616` · muted `#525252` · faint `#6b6b6b` | Every grey that carries words is 4.5:1 or more |
| Primary action | `#161616` near-black | One per screen |
| Data hue | `#1d5fbf` | Progress, the selected filter, focus; under 1% of the screen |
| Status | pale tint + dark ink of the same hue | Active, waiting, deactivated; never colour alone |
| Type | Plex Sans 12 / 13 / 14 / 16 / 24 px; Plex Mono only for codes, serials and one-time passwords | |

## The product's own element

The sign-in page draws the eight stages of a battery's record (supplier receipt → service) as the
cells of one battery pack, terminal on top, the first cell charged. Later phases reuse it as the
lifecycle strip on each battery's page.

## Screens and their one question

| Screen | Question | Answer shown first |
| --- | --- | --- |
| Overview | Is the team ready to start work? | The finding line ("1 active person has no roles yet; 6 haven't signed in yet."), team setup steps, roles in use, recent changes |
| Users | Who can sign in, and who still needs something? | Count cards that filter the list (active, waiting for first sign-in, no roles, deactivated) |
| Roles & permissions | Who can open what? | A "Can open" column per role, built from its permissions |
| Audit log | Who changed what, when and why? | Newest first, with reasons and field changes |
| Company settings | What do dates and amounts look like? | Live preview beside each setting |
| System health | Is anything failing? | The finding line, then services and background jobs |

## Inspector results (`inspect.mjs --app`, 1440 / 768 / 390 px)

0 FAIL on every screen. Remaining WARNs, defended:

- *Overview, ragged-row (~70 px):* the left column is two stacked cards and counts as one block
  (app-screens §4.2); the difference moves with the number of audit entries shown.
- *Sign-in, hero-void and panel-void:* the space around the centred form is the screen's one rest; the
  pack of cells fills the brand field.

Not yet photographed: dark mode, and every dialog. Recheck both when Phase 2 adds screens.

## Rules kept for later phases

Tables: grey sentence-case headers, row rules, no zebra, numbers right-aligned in tabular figures.
Phones: tables become two-line rows (name and status, then the other fields). Empty states keep their
frame and one sentence; errors appear in the block that failed, with Retry.
