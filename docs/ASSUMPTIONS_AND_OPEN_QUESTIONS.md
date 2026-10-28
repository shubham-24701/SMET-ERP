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
