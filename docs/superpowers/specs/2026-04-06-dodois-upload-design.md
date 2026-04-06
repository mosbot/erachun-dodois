# Dodois Upload Mechanism — Design Spec

**Date:** 2026-04-06  
**Status:** Approved

---

## Summary

Add a manual invoice upload flow to Dodois directly from the invoice detail panel. Visible upload status in the invoice table. Only for suppliers configured with `enabled: true` in `SupplierMapping`.

---

## Decisions

| Question | Decision |
|----------|----------|
| Who triggers upload? | User manually — button in invoice detail panel |
| Partially mapped invoices? | Block upload — all lines must be mapped before upload is allowed |
| Status visibility in table? | New "Dodois" column with status badges |

---

## UI Changes

### 1. Invoice Table — new "Dodois" column

Added as the last column. Value depends on supplier and invoice state:

| Condition | Badge |
|-----------|-------|
| Supplier not in Dodois | `—` (plain dash, no badge) |
| `dodois_supply_id` is set | `✓ Загружен` (green) |
| `processing_status == "error"` | `✗ Ошибка` (red) |
| Supplier enabled, not yet uploaded | `· Не загружен` (gray) |

**Note:** The table does NOT compute unmapped line counts — that requires parsing XML for every row and is too expensive. The `⚠ N без маппинга` state is only shown in the detail panel checklist. The table is a quick summary; full validation is in the detail panel.

### 2. Invoice Detail Panel — "Загрузка в Dodois" block

Replaces the standalone pizzeria `selectbox` that currently lives above `render_invoice_detail()`. The selectbox moves inside this block.

**Block is only rendered** when `is_dodois_supplier_enabled(session, invoice.sender_oib)` returns True.

Three states:

**State A — blocked (unmapped lines):**
- Pizzeria selectbox (Zagreb-1 / Zagreb-2)
- Checklist:
  - ✅ Поставщик настроен
  - ✅ / ❌ Пиццерия выбрана
  - ❌ N товаров без маппинга (with names listed)
- Link hint: "→ Маппинги → Товары"
- Upload button: disabled

**State B — ready:**
- Pizzeria selectbox
- Checklist: all green
- Upload button: active `⬆ Загрузить в Dodois`

**State C — uploaded:**
- Green success block: "✓ Загружен в Dodois"
- Pizzeria name
- Supply ID (truncated)
- "↺ Загрузить повторно" button (secondary, for error recovery)

---

## New File: `app/core/dodois_uploader.py`

Three functions:

### `validate_invoice(session, invoice) -> list[str]`

Returns list of blocking issues. Empty list = ready to upload.

Checks:
1. `SupplierMapping` exists and `enabled=True` and `dodois_catalog_id` is set
2. `invoice.dodois_pizzeria` is not None
3. Parse invoice XML → for each line, check `ProductMapping` has `dodois_raw_material_id`

Returns human-readable strings for each issue (shown in checklist).

### `build_supply_payload(session, invoice, pizzeria_cfg) -> dict`

Builds the JSON payload for `POST /Accounting/v1/incomingstock/supplies`.

Steps:
1. Parse XML lines, aggregate duplicates (same product appears multiple times with qty=1 → sum quantities)
2. For each line, look up `ProductMapping` → `DodoisRawMaterialCatalog`
3. Calculate prices using container formula:
   - `unit=5` (g): `pricePerUnit = round(totalPrice / (qty * containerSize / 1000), 2)`
   - `unit=1` (pcs): `pricePerUnit = round(totalPrice / (qty * containerSize), 2)`
   - `unit=8` (m): `pricePerUnit = round(totalPrice / (qty * containerSize), 2)`
   - Exception: `containerId=None` → `pricePerUnit = round(totalPrice / qty, 2)`
4. Map VAT percentage from UBL line → Dodois tax ID (hardcoded dict in module):
   `{25: "c2f84413...", 13: "e67b8c27...", 5: "7ec6687213e7bc4e...", 0: "11eef744..."}`
   `vatValue` = actual EUR amount (not percentage): `round(totalPriceWithVat - totalPriceWithoutVat, 2)`
5. Assemble payload with `id = uuid.uuid4().hex`

### `upload_invoice(session, invoice, client, pizzeria_cfg) -> str`

Calls `client.create_supply(payload)`, saves `supply_id` to `invoice.dodois_supply_id`, sets `processing_status = "uploaded_to_dodois"`. Returns supply ID on success. Raises exception on failure (caller sets `processing_status = "error"`).

---

## `app/web/app.py` Changes

### In `render_invoices_page`

Add `"Dodois"` key to each row dict:
- If `is_dodois_supplier_enabled(session, oib)` is False → `"—"`
- If `inv.dodois_supply_id` → `"✓ Загружен"`
- If `inv.processing_status == "error"` → `"✗ Ошибка"`
- Else → `"· Не загружен"`

Add `column_config` for the new column (text, no special formatter).

### In `render_invoice_detail`

- Remove the standalone pizzeria `selectbox` block that currently sits above the `render_invoice_detail()` call in `render_invoices_page`
- Add `render_dodois_upload_block(inv, session, cfg)` at the bottom of the left column in `render_invoice_detail`

### New function `render_dodois_upload_block(inv, session, cfg)`

- Guard: return early if supplier not enabled
- Pizzeria selector (saves to `inv.dodois_pizzeria` on change)
- Parse XML, run `validate_invoice()`, render checklist
- If no issues: show active upload button
- If `inv.dodois_supply_id`: show success state with re-upload button
- On upload button click: call `upload_invoice()`, show spinner, rerun on success/failure

---

## Files Touched

| File | Change type |
|------|-------------|
| `app/core/dodois_uploader.py` | **New** — validation, payload builder, upload |
| `app/web/app.py` | **Edit** — Dodois column in table, upload block in detail |

No DB schema changes. No new dependencies.

---

## Out of Scope

- Automatic upload on sync (explicit decision: manual only)
- Batch upload of multiple invoices
- Telegram bot integration
- Suppliers other than METRO (will work once their mappings are configured)
