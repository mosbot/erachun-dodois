# PlanFact Integration — Design Spec

**Date:** 2026-07-28
**Status:** Approved

---

## Summary

Post Wolt and Glovo invoices to PlanFact as outcome operations, directly from the
eRačun data this portal already holds. This absorbs the separate `pdf2planfact`
service, which parsed the same invoices out of PDFs attached to Gmail.

The XML we already store carries the invoice number, the per-rate amounts and the
tax point, all of them exact. `pdf2planfact` reconstructed the same numbers with
regexes over PDF layout, and lost documents when anything failed.

---

## Decisions

| Question | Decision |
|----------|----------|
| Scope | Wolt and Glovo only. Other suppliers are not posted to PlanFact. |
| Trigger | Automatic — a cron step that runs right after the eRačun sync. |
| Cutover | Stop running `pdf2planfact`. It was only ever run by hand, so there is no service to disable and no race window. |
| Wolt Drive | Excluded. Not an invoice we book. |
| Historical gaps | The 12 documents from Dec 2025 – mid-Feb 2026 whose pizzeria is not stated anywhere are not posted. |
| Idempotency | Enforced by us. PlanFact does not enforce `externalId` uniqueness. |

---

## Evidence behind the design

Measured against production data on 2026-07-27/28.

**The data is sufficient.** 97 Wolt and 62 Glovo invoices are already stored with
their XML. `vat_date` reproduces the operation date `pdf2planfact` computed from
the PDF billing period — invoice `87588-2553198637711-2026` sits in PlanFact with
`operationDate` 2026-07-05 and our `vat_date` is 2026-07-05.

**The pizzeria is identifiable, but not the way the current code looks for it.**
`_detect_pizzeria` scans the whole document text and only knows `TRATINSKA` and
`MAKSIMIRSKA`, so Zagreb-1 was never detected on a Wolt invoice, and two July
invoices matched the *buyer* address instead of the delivery address. The reliable
signals are:

- Wolt: `cac:Delivery/cac:DeliveryLocation/cbc:ID` — `65e990340c64206ab0881c8c`
  (Zagreb-1) and `67e560daff93ab813b57e0c2` (Zagreb-2), sometimes carrying a
  `4ujG1qM.` prefix; plus the delivery street.
- Glovo: the P-code inside the line item name, e.g.
  `Glovo provizija P705447 račun broj: 47284-1-5-2026`.

Scoped that way, detection is 100% for every Wolt and Glovo invoice from February
2026 onward. The gap is historical: 8 Wolt invoices up to 2026-01-15 and 4 Glovo
from Jan–Feb state the venue nowhere — not in the XML and not in the embedded PDF.

**Wolt Drive is a separate contract.** Its invoice numbers carry the middle segment
`2553198637741` (payouts use `2553198637711`), it has no delivery location, and its
lines are `Delivery Services` / `Heavy order charges` / `Cash processing fees`.
13 such invoices exist and they are still arriving twice a month. `pdf2planfact`
excluded them via the Gmail subject filter `GMAIL_EXCLUDE_SUBJECTS=(Drive)`.

**PlanFact does not deduplicate.** Five operations are already duplicated there —
same `externalId`, same value, posted twice:

```
157395-2553198637711-2025 ×2   157396-2553198637711-2025 ×2
4359-2553198637711-2026   ×2   91014-1-5-2025 ×2   90685-1-5-2025 ×2
```

**Glovo is numbered differently in the two systems.** Ours is
`2653472/G1/2234278`; PlanFact holds `47284-1-5-2026`, which appears inside the
XML line item name. Matching on it, 52 of our 62 Glovo and 74 of our 84 Wolt payout
invoices are already in PlanFact.

**Backlog at design time:** 14 invoices, 8 923.53 EUR gross, all with a determined
pizzeria — 8 Wolt (10–25 July) and 6 Glovo (two from 05.06, four from 22.07). The
two June Glovo were skipped even though the old service ran until 06.07, because a
failed message is marked read before processing and never returns.

---

## Flow

`sync_invoices.sh` runs two steps under the existing `flock`, so postings never
overlap and always work on freshly synced data:

```
cron */30 → sync_invoices.sh
              ├─ scripts/sync_eracun.py        (unchanged)
              └─ scripts/post_to_planfact.py   (new)
```

Anything that fails to post stays unposted and is retried on the next run. This is
the property the old service lacked.

### Components

- **`app/core/planfact_client.py`** — HTTP only: `create_outcome(payload)`,
  `list_operations(account_id, date_from, date_to)`. Retries, `isSuccess: false`
  handling on HTTP 200, empty-body tolerance.
- **`app/core/planfact_poster.py`** — domain logic: candidate selection,
  validation, payload construction, deduplication, recording the result.
- **`scripts/post_to_planfact.py`** — CLI in the shape of `sync_eracun.py`: live by
  default, `--dry-run`, `--invoice <id>`, exit codes for cron. It exits before
  touching the network when the queue is empty, which is the normal case — most
  half-hourly runs have nothing to post.
- **`scripts/reconcile_planfact.py`** — one-off: mark invoices already posted by
  `pdf2planfact`. Dry-run by default, `--apply` to write.

The defaults differ on purpose and follow the conventions already in the repo: a
recurring job runs live like `sync_eracun.py`, a one-off data migration defaults to
dry-run like `backfill_vat_dates.py`.

---

## Data model

Four columns on `Invoice`:

| Column | Purpose |
|---|---|
| `planfact_operation_id` | Operation id. `NULL` means not posted — this is the queue. |
| `planfact_external_id` | Match key. Wolt: our invoice number. Glovo: the inner number from the line item. Computed once, stored. |
| `planfact_posted_at` | Timestamp of the successful post. |
| `planfact_error` | Text of the last failure, shown in the invoice detail panel. |

No attempt counter: a failing invoice simply stays in the queue and retries every
30 minutes, and `planfact_error` shows what is stuck. Add a cap only if something
proves to loop.

`Base.metadata.create_all` never alters an existing table, so the reconcile script
adds the columns when missing, the way `backfill_vat_dates.py` does.

---

## Pizzeria detection

`_detect_pizzeria` is rewritten to look **only** where a delivery point can legitimately
appear:

- the `cac:Delivery` subtree,
- `cbc:Note`,
- `cac:InvoiceLine/cac:Item/cbc:Name`,
- `cac:OrderReference/cbc:ID`.

The buyer party is excluded on purpose — it is what produced the false matches.

Patterns move to `config.yaml` so a third pizzeria needs no code change:

```yaml
pizzeria_detection:
  Zagreb-1: [65e990340c64206ab0881c8c, kranjčevićeva, kranjceviceva,
             trešnjevka, tresnjevka, P705447, TRATIN]
  Zagreb-2: [67e560daff93ab813b57e0c2, maksimirska, MAKSIMIR, P825763]
```

Keys are the display names, because that is exactly what `_detect_pizzeria`
returns and what `invoices.dodois_pizzeria` stores — `Zagreb-1`, not `zagreb-1`.
The lowercase form is only a key inside `dodois.pizzerias`. Keeping one spelling
avoids a case-mapping layer.

If patterns for two different pizzerias match the same document, return `None` and
log a warning. Guessing is worse than not posting.

This also improves `dodois_pizzeria` for other suppliers. Existing rows are
recomputed with the existing `scripts/remap_pizzerias.py`, which is dry-run by
default and never touches invoices already uploaded to Dodois.

---

## Operation payload

`POST /api/v1/operations/outcome`. The shape below was taken from a real operation
read back from PlanFact, not from documentation, so it matches what is already there.

```json
{
  "operationDate": "<vat_date>T00:00:00Z",
  "isCommitted": true,
  "accountId": 666927,
  "value": <total_with_vat>,
  "comment": "#erachun Wolt 87588-2553198637711-2026",
  "externalId": "<planfact_external_id>",
  "items": [
    {"operationCategoryId": <commission>, "projectId": <project>,
     "value": <total_without_vat>,
     "calculationDate": "<vat_date>T00:00:00Z", "isCalculationCommitted": true},
    {"operationCategoryId": 9485374, "projectId": <project>,
     "value": <total_vat>,
     "calculationDate": "<vat_date>T00:00:00Z", "isCalculationCommitted": true}
  ]
}
```

Amounts come straight from the stored totals rather than being reconstructed by
subtraction. The comment marker changes to `#erachun`; history keeps `#pdf2pf`,
which does not matter because deduplication works on `externalId`.

### Validation — all must hold, else skip and record the reason

1. Supplier OIB is Wolt (`25531986377`) or Glovo (`48879371584`). Matching on OIB,
   not on the display name.
2. Not the Wolt Drive series `2553198637741`.
3. Not a credit note (`document_type_id` 381 / `is_credit_note`). None exist for
   these two suppliers today; this is a guard.
4. Pizzeria determined.
5. `vat_date` present.
6. `abs(total_without_vat + total_vat - total_with_vat) <= 0.01`.
7. `planfact_external_id` could be computed. For Glovo this means the
   `račun broj: …` fragment was found in a line item name; the early-2026 invoices
   that lack it are already excluded by rule 4, but the check stands on its own so
   an invoice can never be posted under an empty match key.

---

## Deduplication

Three layers, because `externalId` alone is demonstrably not enough.

1. **Stored id.** `planfact_operation_id IS NOT NULL` → the invoice is never considered.
2. **One-time reconcile.** `reconcile_planfact.py` reads existing operations for both
   accounts, matches on `externalId`, and fills the id for the ~126 invoices already
   posted. This is what stops history from being posted a second time.
3. **Pre-flight check.** Before each POST, query PlanFact for operations on that
   account within ±3 days of `vat_date` and look for the `externalId`. This covers
   the case where a POST succeeded but the response was lost: the next run finds the
   operation and records its id instead of creating a second one.

The 14 backlog invoices need no special path — they are simply unposted invoices
with a known pizzeria and get picked up by the first live run.

---

## Error handling and observability

The client retries three times with increasing delay on timeout, connection error
and 5xx. A 4xx or `isSuccess: false` is not retried: record `planfact_error` and
move to the next invoice, so one bad document never blocks the rest.

An invoice is marked posted **only** when an operation id exists — from the response
or from the pre-flight check. Never on assumption.

On failure, send a Telegram message with the invoice number and the reason, reusing
`app/core/telegram_notifier.py`. Successes are not notified: ~25 a month would be
noise. Silence is what caused the three-week gap and the two lost June invoices.

**Notify on transitions only.** The job runs every 30 minutes and a failing invoice
stays in the queue, so notifying on every failure would send the same message 48
times a day. A message goes out only when the failure is new — `planfact_error` was
`NULL`, or its text changed. An invoice failing the same way twice in a row is
silent; it is visible in the detail panel and in the run summary.

The invoice detail panel gains a PlanFact status line: posted with date, not posted,
or the error text. No new column in the invoice list.

---

## Configuration

`config.yaml`:

```yaml
planfact:
  enabled: true
  base_url: https://api.planfact.io/api/v1
  accounts:   {wolt: 666927, glovo: 666928}
  projects:   {Zagreb-1: 1172400, Zagreb-2: 1198217}   # keyed by dodois_pizzeria
  categories:
    wolt_commission: 8563181
    glovo_commission: 8563431
    vat: 9485374
  providers:
    wolt:  {oib: "25531986377", exclude_series: ["2553198637741"]}
    glovo: {oib: "48879371584"}
```

The API key goes to `config.local.yaml` next to the other secrets. Note the
`_deep_merge` footgun documented in CLAUDE.md: an empty string in the local file
silently overrides a valid base value.

---

## Testing

- **Payload construction** — amounts, dates, category and project mapping.
- **Validation rules** — Wolt Drive excluded by series, credit note blocked,
  undetermined pizzeria blocked, amount mismatch blocked.
- **Pizzeria detection** — synthetic XML fixtures in the style of the credit-note
  tests: Wolt by `Delivery/DeliveryLocation/ID`, Glovo by P-code, the buyer address
  must **not** match, two matching pizzerias yield `None`. No real supplier
  documents in git.
- **Deduplication** — skip when the id is stored; skip and record when the
  pre-flight finds an operation.
- **Client** — mocked session, as in the Dodois tests: empty body on success, 200
  with `isSuccess: false`, 5xx retried, 4xx not retried.

---

## Rollout

1. Add columns and reconcile against PlanFact (`--apply`), marking the ~126 already
   posted.
2. Run `post_to_planfact.py --dry-run` and check the result against the known
   backlog: 14 invoices, 8 923.53 EUR.
3. Run live, verify the operations in PlanFact.
4. Add the step to `sync_invoices.sh`.
5. Stop running `pdf2planfact` by hand.

---

## Out of scope

- Suppliers other than Wolt and Glovo.
- The 12 historical invoices with no stated venue.
- Wolt Drive.
- Credit notes for these suppliers — blocked, not handled.
- Retiring the `pdf2planfact` repository. It stays as-is; it is simply no longer run.
