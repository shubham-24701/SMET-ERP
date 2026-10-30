# Assumptions and open questions

The proposal leaves several business rules to "detailed requirement finalisation". Each of these
will be a **setting SMET can change**. Until SMET answers, the system uses the default written
next to the question and marks the rule as unconfirmed; no engineering formula, test limit or
warranty rule is made up. Examples in brackets are only there to show the kind of answer needed.

Answer in shorthand if easier, for example "13a: 24 months for VRLA, 36 for lithium; 13b: from
commissioning".

## Working assumptions (SMET can change these)

| # | Assumption | Change if |
| --- | --- | --- |
| A1 | One company uses this system (SMET). Distributors and customers get their own logins later, and see only their own records | Secure Hive wants to offer it to other traders |
| A2 | Dates and times are shown in Dubai time | Sites in other countries need their local time on documents |
| A3 | Amounts default to AED; each document may use another currency (e.g. USD for suppliers in China) | — |
| A4 | Screens are in English only | Arabic screens or documents are needed |
| A5 | Accounting, payment of invoices, VAT returns and banking stay outside (as the proposal says) | — |
| A6 | Security defaults: sign-in lasts a working day (10 hours) and ends after 1 hour without use; passwords have at least 12 characters | SMET's IT policy says otherwise |
| A7 | The system runs on the owner's computer until a hosting option and its monthly cost are approved | — |
| A8 | Every purchase order needs an approver, and nobody approves an order they raised or submitted (question 9). An automatic-approval limit can be switched on in Rules & numbering | SMET sets a limit or allows self-approval |
| A9 | Every stock correction needs a reason and a second person's approval; the Operations Manager role can approve (questions 6, 25b) | SMET names other approvers or a quantity threshold |
| A10 | Purchase prices are kept for reference only; stock has no value in the system (question 7) | SMET wants latest price or average cost |
| A11 | Document numbers look like `PO-2026-00001`; SMET's own battery serials like `SMET-2026-00001` (questions 1a, 11). Both are changeable in Rules & numbering, and serials can also be typed in | SMET sends its formats |
| A12 | A new warehouse gets a main store, receiving inspection, quarantine, rejected-goods and assembly areas (question 5) | SMET's real layout differs |
| A13 | Final inspection applies a limit only after SMET marks it confirmed; a battery model with no checklist or readings set up can't pass at all (question 4) | — |
| A14 | Purchased battery units and serialised parts are registered by serial number on arrival; the supplier's serial is kept beside SMET's (questions 1b, 3) | Lot numbers are enough for some parts |
| A15 | A delivery larger than what is outstanding is refused, so the order is changed first (question 28) | SMET accepts a tolerance |
| A16 | Every quotation needs an approver; nobody who prepared or submitted it approves it. An automatic-approval limit (total and maximum discount) can be switched on (question 10a, 10c) | SMET sets limits |
| A17 | A quotation is valid for 30 days unless a date is typed (question 10b) | SMET's usual validity differs |
| A18 | Battery quantities on a requirement sheet are typed in and approved by an engineer; no sizing formula is built in (question 15) | SMET supplies its rules, entered under Sizing rules |
| A19 | Goods refused at delivery come back into a customer-returns area, on hold, and are owed again on the order (question 23) | SMET handles refusals differently |
| A20 | An installation visit needs at least one photograph; commissioning and handover need none; a handover needs the customer's name and date, and no open issue on its visit. All changeable in Rules & numbering (questions 16, 31) | SMET sets its evidence policy |
| A21 | No installation or commissioning checklist and no readings are built in: SMET enters them under Site checks, and a reading's limits apply only once SMET confirms them (question 16) | — |
| A22 | Batteries are grouped in banks; the three stages (installed, commissioned, handed over) are dated per bank (question 32) | SMET records stages per site or per battery |
| A24 | No warranty term is built in: each model's policy is entered by SMET (months, the event that starts it, automatic or manual approval, proof needed, replacement rule). Until then registrations wait with "no policy" (question 17) | — |
| A25 | Sending stock to a distributor never starts a warranty; a SMET installation opens one for each battery at handover; distributors' and customers' sales are registered by SMET staff or on the public form, and need someone other than the submitter to approve unless the policy approves automatically (questions 17c, 18) | SMET's policy says otherwise |
| A26 | A warranty runs to the day before the same date N months later (12 months from 15 Mar ends 14 Mar) | SMET counts differently |
| A27 | An AMC's first planned visit is one interval after its start; renewal reminders go out 60 days before it ends (question 21) | SMET plans visits differently |
| A28 | The public check shows the product, whether it is in service and the warranty state; dates only if SMET switches them on (question 20) | — |
| A29 | Reset codes are typed in by an authorised person from the supplier; stored encrypted; erased 365 days after the alert closes. The system never generates, expires or clears a code (question 22) | The supplier offers an API |
| A30 | A replaced battery goes to the "Service workshop" area of a warehouse; what happens next (repair, scrap, return to the supplier) is decided case by case (question 23) | SMET defines a returns process |
| A23 | A QR label carries only a web address with a random code; anyone scanning it sees the serial number, the product and whether it is in service. Labels are printed from the browser on any printer; no paid QR service (questions 33, 34) | SMET wants more on the public page (warranty comes in Phase 6) |

Technical choices (database, sign-in mechanism) are recorded for developers in
[engineering/ARCHITECTURE_DECISIONS.md](engineering/ARCHITECTURE_DECISIONS.md).

## Questions for SMET

Questions marked **(Phase 2)** are needed soonest; Phase 2 can start without them because each has a
safe default, but they must be answered before the system is used for real.

### Products, stock and assembly
1. **Serial numbers.**
   a. What does SMET's own serial number for a finished battery look like (e.g. prefix, year, running number)?
   b. Should the supplier's serial number also be printed on the label? *(Default: kept in the record, not printed.)*
2. **Battery models (Phase 2).** The list of models with capacity (Ah), voltage, chemistry and
   category; and which are assembled in Dubai and which are sold as received.
3. **Parts of each assembled battery.** For each configuration, the parts and quantities.
   Does any part carry its own serial number that must be recorded one by one (for example a BMS
   board), or is a lot number enough?
4. **Quality test readings.** For each model, which readings are taken at quality check (for example
   open-circuit voltage, internal resistance, capacity) and the pass limits. *(Default: readings are
   recorded, no pass limit is applied until SMET sets one.)*
5. **Warehouses (Phase 2).** Each location and storage area (for example Dubai warehouse, assembly
   floor, quarantine area).
6. **Stock corrections (Phase 2).** Who may approve a correction to stock? Is a second approver needed
   above a certain quantity or value?
7. **Stock value (Phase 2).** Purchase prices are kept for reference. Should stock be valued at the
   latest purchase price, an average, or not at all?
8. **Storage of charged batteries.** Do batteries in stock need a top-up charge every so many months,
   or have a maximum age before sale? *(Default: not tracked.)*

### Purchasing and sales
9. **Purchase approval (Phase 2).**
   a. Above what order value does a purchase order need Management's approval?
   b. May the person who raised a purchase order also approve it? *(Default: no.)*
10. **Quotations.**
    a. How much discount may a Sales Officer give without approval?
    b. How many days is a quotation valid?
    c. Which quotations always need approval?
11. **Document numbers (Phase 2).** The format for purchase orders, goods receipts, quotations, sales
    orders, delivery notes and service requests. *(Default: `PO-2026-00001` style.)*
12. **Price lists.** Does each distributor or customer have its own prices?
13. **Quotation and delivery note layout.** A sample of each (logo, terms, whether VAT is shown).
14. **Accounting system.** Which accounting system does SMET use, and does it need an export of
    purchase orders, goods receipts or sales orders from this system?

### Projects and installation
15. **Battery sizing.** The rules SMET uses to work out how many batteries a site needs (load, backup
    time, voltage, ageing and temperature allowances), and who must approve the result.
    *(Default: quantities are typed in and need engineering approval.)*
16. **Checklists.** The installation and commissioning checklist items, and what must be attached
    before handover (photos, readings, customer signature).

### Warranty, maintenance contracts and service
17. **Warranty per model.**
    a. How long is the warranty for each model?
    b. What starts it: the sale to the end customer, installation, or commissioning?
    c. Does sending stock to a distributor ever start it? *(Default: no; the warranty starts only on
       the event chosen in 17b.)*
18. **Registration by distributors.**
    a. What must a distributor send to register an end customer (invoice, photos, site address)?
    b. Is the warranty switched on automatically when everything is there, or always checked by SMET?
19. **Replacement batteries.** Does a replacement carry on the original warranty, get a new one, or
    the longer of the two?
20. **Checking a battery on the website.** When anyone types a serial number on SMET's website, what
    may they see: model, warranty status, start and end dates? *(Default: model and warranty status only.)*
21. **Maintenance contracts (AMC).**
    a. Which contract types do you sell, and how often are visits?
    b. What is covered and what is excluded?
    c. How long before expiry should the renewal reminder go out?
22. **Maintenance alert codes.**
    a. Which supplier's batteries show maintenance alerts that need a code?
    b. How do you get a code today (supplier portal, email, a tool)?
    c. Can a code be used only once, and does it expire?
    d. Can the supplier's system send codes to ours automatically?
    *(Default: authorised staff type in the code the supplier issued.)*
23. **Returns and old batteries.** How are customer returns handled, and what happens to batteries
    taken back or replaced (scrap, recycling, return to supplier)?

### Messages, people and hosting
24. **Messages.**
    a. Email only at first, or also SMS or WhatsApp?
    b. Does SMET already have Microsoft 365, Google Workspace or an SMS provider?
25. **People.**
    a. Roughly how many staff, distributor and customer users?
    b. Who approves purchases, quotations, stock corrections and warranty changes?
26. **Where data is kept.** Must the data stay in the UAE (data-protection law or customer contracts)?
    This decides the hosting options and their cost.
27. **Existing data.** Customers, suppliers, products and opening stock: in Excel? A sample file lets us
    build the import templates.
28. **Over-delivery (Phase 2).** When a supplier delivers more than ordered, is a small surplus
    accepted (for example up to 2%), or must the order be changed first? *(Default: changed first.)*
29. **Assembly and recipes (Phase 3).**
    a. Who plans assembly orders, and who may make a new bill of materials version active?
       *(Default: the Operations Manager role for both; Quality sets inspection limits.)*
    b. During rework, are components ever swapped (for example a weak cell)? If so, which ones?
30. **Orders without a quotation (Phase 4).** Do distributors place repeat orders at agreed prices without a
    new quotation? *(Default: every sales order comes from an accepted quotation.)*
31. **Evidence before completion (Phase 5).** How many photographs at installation, commissioning and
    handover; is a signed handover form always attached; may a handover complete with snags still
    open? *(Default: A20.)*
32. **Battery banks (Phase 5).** How SMET names banks and positions on site (string and cell number,
    rack and shelf), and whether stages are signed off per bank or per site. *(Default: A22.)*
33. **Labels (Phase 5).** Label size and printer (thermal label printer or A4 sheets), what goes on the
    label besides the QR code and serial, and when labels are printed (after inspection, or at
    dispatch). *(Default: 90 × 40 mm, any printer, printed from the battery or the bank.)*
34. **Public address (Phase 5, before printing real labels).** The internet address QR labels should
    open (e.g. a subdomain of SMET's website). Labels printed before it is set point to this computer
    only. *(Needs the hosting decision, open question on hosting.)*
35. **Service levels (Phase 6).** How fast must SMET answer an urgent, high, normal or low request, and
    who coordinates service requests? *(Default: no deadlines; urgent ones are listed first.)*
36. **Distributor and customer logins.** Should distributors register their sales and customers
    see their own batteries through their own login, rather than the public form and SMET staff?
    *(Default: not built; the public form and SMET staff cover it.)*
