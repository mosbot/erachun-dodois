# Product Mapping via Invoice Cross-Reference — Design Spec

**Date:** 2026-04-08  
**Status:** Approved

---

## Goal

Автоматически заполнить `ProductMapping` (eRačun description → Dodois rawMaterialId)  
путём кросс-матчинга инвойсов между eRačun DB и Dodois API — **для всех поставщиков**.

---

## Data Sources

| Источник | Что берём |
|----------|-----------|
| eRačun DB (`invoices`) | `invoice_number`, `xml_path`, `sender_oib` |
| XML файлы на сервере | Строки инвойса: `description`, `quantity`, `line_total` |
| Dodois API | Поставки всех поставщиков: `invoiceNumber`, `supplierId`, items (`rawMaterialId`, `containerId`, `qty`, `totalWithVat`) |
| DB (`dodois_raw_material_catalog`) | Каталог всех поставщиков (47 поставщиков, 500+ материалов) |

---

## Invoice Number Conversion

Dodois и eRačun используют разные форматы одного инвойса:

```
Dodois:  6/0(011)0003/004488   (формат: C/0(0BB)0YYY/00000A)
eRačun:  4488/11/6003          (формат: A/BB/CYYY)

Формула: C, BB, YYY, A → A/BB/CYYY
Regex:   ^(\d)/0\(0(\d+)\)0(\d{3})/0*(\d+)$
```

Также встречается Dodois-инвойс уже в eRačun формате (`2315/11/6005`) — нормализация не нужна.

---

## Algorithm

### Step 1 — Sync Catalog

Перед матчингом убедиться что `dodois_raw_material_catalog` заполнен для всех поставщиков:
```bash
docker cp dodois-suppliers-clean.json e-rachun-dodois:/tmp/
docker exec e-rachun-dodois python scripts/sync_dodois_catalog.py /tmp/dodois-suppliers-clean.json
```

### Step 2 — Fetch Dodois Supplies

```
GET /Accounting/v1/incomingstock/departments/{dept}/supplies?from=2025-01-01&to=today
```

- Оба депта: Zagreb-1 и Zagreb-2
- **Все поставщики** — без фильтрации по supplierId
- Дедупликация по `invoiceNumber + supplierId` (один инвойс может быть в двух деп-тах)
- Для каждой поставки: `GET /Accounting/v1/incomingstock/supplies/{id}` → items

### Step 3 — Match Invoices

Для каждой Dodois поставки:
1. Конвертировать `invoiceNumber` в eRačun формат
2. Искать в DB: `Invoice WHERE invoice_number ILIKE '%{eracun_number}%' AND sender_oib = {supplier_oib}`
3. Supplier OIB: найти по `DodoisSupplierCatalog.dodois_id = supply.supplierId` → `dodois_inn` (INN/OIB поставщика)

### Step 4 — Match Lines within Invoice

Для каждой пары (eRačun Invoice ↔ Dodois Supply):
- Парсим XML (`xml_path`) → `UBLLineItem[]` (description, quantity, line_total)
- Агрегируем дубли: группировать по description, суммировать qty и line_total
- Для каждого Dodois item (rawMaterialId, qty, totalWithVat):
  - Ищем UBL строку где `abs(ubl.quantity - item.qty) < 0.01` И `abs(ubl.line_total - item.totalWithVat) < 0.02`
  - Одно совпадение → match
  - Несколько → пропустить (ambiguous)

### Step 5 — Write ProductMapping

Для каждого match:
1. Найти `SupplierMapping` по `eracun_oib = invoice.sender_oib`
2. Найти `DodoisRawMaterialCatalog` по `dodois_material_id + dodois_container_id`
3. Найти `ProductMapping` по `supplier_mapping_id + eracun_description`
4. Если `dodois_raw_material_id` уже заполнен → пропустить (не перезаписывать вручную)
5. Если NULL → заполнить + `enabled=True`

---

## Script: `scripts/match_invoices.py`

```
Usage: python scripts/match_invoices.py [--dry-run] [--from DATE] [--to DATE]

Options:
  --dry-run    Показать что будет сделано без записи в DB
  --from DATE  Начало периода (default: 2025-01-01)
  --to DATE    Конец периода (default: today)
```

Запуск на сервере:
```bash
docker exec e-rachun-dodois python scripts/match_invoices.py --dry-run
docker exec e-rachun-dodois python scripts/match_invoices.py
```

### Output
```
Catalog: 47 suppliers, 512 materials synced
Fetched 340 Dodois supplies (Zagreb-1: 210, Zagreb-2: 130, dedup: 0)
Matched 287 invoices in eRačun DB (53 not found)
Line matches: 1842 found, 34 ambiguous, 12 no XML

ProductMappings: 198 new, 0 overwritten, 5 conflicts
Unmapped eRačun descriptions: 47 (see Mappings page)
```

---

## Dependencies

- `app/core/ubl_parser.py` — as-is
- `app/core/config_loader.py` — Dodois credentials
- `app/db/models.py` — `ProductMapping`, `DodoisRawMaterialCatalog`, `SupplierMapping`
- Новый: `app/core/dodois_client.py` — GET supplies list + detail
- Новый: `scripts/match_invoices.py` — основной скрипт

---

## Dodois Auth

Используем cookies-based сессию из `dodois_uploader.py` (auto-login с TOTP).  
`dodois_client.py` переиспользует этот механизм.

---

## Edge Cases

| Кейс | Решение |
|------|---------|
| Дубли строк METRO (один товар несколько раз) | Агрегировать по description перед матчингом |
| Несколько кандидатов по qty+price | Пропустить (ambiguous), логировать |
| XML файл отсутствует | Пропустить инвойс, логировать |
| ProductMapping уже заполнен вручную | Не перезаписывать |
| Dodois invoice number уже в eRačun формате | Обработать как есть |
| Zagreb-1 и Zagreb-2 (разные dept_id) | Fetch для обоих, дедупликация по invoice+supplier |
| Поставщик без `dodois_inn` в каталоге | Матчить по invoice_number без OIB фильтра |
| SupplierMapping не существует | Создать автоматически через `get_or_create_supplier_mapping` |
