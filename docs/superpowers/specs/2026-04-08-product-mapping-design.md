# Product Mapping via Invoice Cross-Reference — Design Spec

**Date:** 2026-04-08  
**Status:** Approved

---

## Goal

Автоматически заполнить `ProductMapping` (eRačun description → Dodois rawMaterialId)  
путём кросс-матчинга инвойсов между eRačun DB и Dodois API.

---

## Data Sources

| Источник | Что берём |
|----------|-----------|
| eRačun DB (`invoices`) | `invoice_number`, `xml_path`, `sender_oib` |
| XML файлы на сервере | Строки инвойса: `description`, `quantity`, `line_total` |
| Dodois API | Поставки METRO: `invoiceNumber`, items (`rawMaterialId`, `containerId`, `qty`, `totalWithVat`) |
| DB (`dodois_raw_material_catalog`) | Уже синхронизирован через `sync_dodois_catalog.py` |

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

### Step 1 — Fetch Dodois Supplies

```
GET /Accounting/v1/incomingstock/departments/{dept}/supplies?from=2025-01-01&to=today
```

- Оба депта: Zagreb-1 и Zagreb-2
- Фильтр по supplierId = METRO (`11eeeb8be458f06caf0d5b3908d3a4aa`)
- Для каждой поставки: `GET /Accounting/v1/incomingstock/supplies/{id}` — получить items

### Step 2 — Match Invoices

Конвертировать Dodois `invoiceNumber` → eRačun формат.  
Искать в DB: `Invoice.invoice_number ILIKE '%{eracun_number}%'`  
(eRačun может хранить номер с префиксами типа "Račun ")

### Step 3 — Match Lines within Invoice

Для каждой пары (eRačun Invoice ↔ Dodois Supply):
- Парсим XML (`xml_path`) → `UBLLineItem[]` (description, quantity, line_total)
- Для каждого Dodois item (rawMaterialId, qty, totalWithVat):
  - Ищем UBL строку где `abs(ubl.quantity - item.qty) < 0.01` И `abs(ubl.line_total - item.totalWithVat) < 0.02`
  - Если одно совпадение → match
  - Если несколько → берём лучшее (минимальная разница по обоим полям)

### Step 4 — Write ProductMapping

Для каждого match:
1. Найти `DodoisRawMaterialCatalog` по `dodois_material_id + dodois_container_id`
2. Найти `ProductMapping` по `supplier_mapping_id + eracun_description`
3. Если `dodois_raw_material_id` уже заполнен — пропустить (не перезаписывать вручную заданные)
4. Если NULL → заполнить + установить `enabled=True`

---

## Price Tolerance

METRO инвойсы могут содержать строки-дубли (одинаковый товар несколько раз).  
`ubl_parser.py` агрегирует их перед загрузкой в Dodois.  
При матчинге используем **агрегированные** строки (сгруппировать по description, суммировать qty и line_total).

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
Fetched 85 Dodois supplies for METRO
Matched 72 invoices in eRačun DB
Line matches: 312 found, 18 skipped (ambiguous), 5 skipped (no XML)

ProductMappings updated: 67 new, 0 updated, 3 conflicts
Unmapped eRačun descriptions: 23 (see mappings page)
```

---

## Dependencies

- `app/core/ubl_parser.py` — уже есть, используем as-is
- `app/core/config_loader.py` — конфиг с Dodois credentials
- `app/db/models.py` — `ProductMapping`, `DodoisRawMaterialCatalog`, `SupplierMapping`
- Новый: `app/core/dodois_client.py` — Dodois API client (GET supplies)
- Новый: `scripts/match_invoices.py` — основной скрипт

---

## Dodois Auth

Используем cookies-based сессию из `dodois_uploader.py` (auto-login с TOTP).  
`dodois_client.py` переиспользует этот механизм.

---

## Edge Cases

| Кейс | Решение |
|------|---------|
| Один товар в инвойсе несколько раз (дубли METRO) | Агрегировать UBL строки перед матчингом |
| Несколько кандидатов по qty+price | Пропустить (ambiguous), логировать |
| XML файл отсутствует | Пропустить инвойс, логировать |
| ProductMapping уже заполнен вручную | Не перезаписывать |
| Dodois invoice number уже в eRačun формате | Обработать как есть |
| Zagreb-1 и Zagreb-2 (разные dept_id) | Fetch для обоих, дедупликация по invoice number |
