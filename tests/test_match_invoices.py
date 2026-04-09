"""Tests for invoice matching logic."""
import pytest
from scripts.match_invoices import (
    aggregate_ubl_lines,
    dodois_to_eracun,
    extract_invoice_key,
    find_dodois_match,
    match_lines,
)


class TestExtractInvoiceKey:
    def test_metro_slash_format(self):
        assert extract_invoice_key("4488/11/6003") == ("4488", "11", "6003")

    def test_kristy_dash_format(self):
        assert extract_invoice_key("2357-1-1") == ("2357", "1", "1")

    def test_pivac_alpha_middle(self):
        assert extract_invoice_key("5854/V211/10") == ("5854", "V211", "10")

    def test_leading_zeros_stripped(self):
        assert extract_invoice_key("00217-100-26") == ("217", "100", "26")

    def test_granolio_alpha(self):
        assert extract_invoice_key("1840/IRK/26") == ("1840", "IRK", "26")

    def test_makromikro(self):
        assert extract_invoice_key("141-VP01-2") == ("141", "VP01", "2")

    def test_inter_alfa(self):
        assert extract_invoice_key("1530-4-49051") == ("1530", "4", "49051")

    def test_lowercase_normalized(self):
        assert extract_invoice_key("100/v211/10") == ("100", "V211", "10")

    def test_empty_returns_none(self):
        assert extract_invoice_key("") is None
        assert extract_invoice_key("   ") is None

    def test_single_part_returns_none(self):
        assert extract_invoice_key("12345") is None

    def test_whitespace_inside_part_kept_alpha(self):
        # "616 7000899-25" → ("616 7000899", "25") — alpha-ish, kept as-is
        result = extract_invoice_key("616 7000899-25")
        assert result == ("616 7000899", "25")


class TestFindDodoisMatch:
    def test_direct_match(self):
        key = extract_invoice_key("2357-1-1")
        supplies = [{"invoiceNumber": "2357-1-1", "id": "x"}]
        assert find_dodois_match(key, supplies) == supplies[0]

    def test_metro_translated_match(self):
        key = extract_invoice_key("4488/11/6003")
        supplies = [{"invoiceNumber": "6/0(011)0003/004488", "id": "y"}]
        assert find_dodois_match(key, supplies) == supplies[0]

    def test_no_match(self):
        key = extract_invoice_key("999-1-1")
        supplies = [{"invoiceNumber": "2357-1-1"}]
        assert find_dodois_match(key, supplies) is None

    def test_alpha_middle_match(self):
        key = extract_invoice_key("5854/V211/10")
        supplies = [{"invoiceNumber": "5854/V211/10", "id": "p"}]
        assert find_dodois_match(key, supplies) == supplies[0]


class TestDodoisToEracun:
    def test_standard_format(self):
        assert dodois_to_eracun("6/0(011)0003/004488") == "4488/11/6003"

    def test_zero_prefix(self):
        assert dodois_to_eracun("0/0(010)0001/003895") == "3895/10/0001"

    def test_different_seq(self):
        assert dodois_to_eracun("6/0(010)0005/001729") == "1729/10/6005"

    def test_already_eracun_format(self):
        # "2315/11/6005" is already eRačun format — return unchanged
        assert dodois_to_eracun("2315/11/6005") == "2315/11/6005"

    def test_invalid_returns_unchanged(self):
        assert dodois_to_eracun("INVALID") == "INVALID"


class TestAggregateUblLines:
    def _make_line(self, description, quantity, line_total):
        """Helper: create a dict line (same format match_lines expects)."""
        return {"description": description, "quantity": quantity, "line_total": line_total}

    def test_no_duplicates_unchanged(self):
        lines = [
            self._make_line("PRODUCT A", 2.0, 10.00),
            self._make_line("PRODUCT B", 1.0, 5.00),
        ]
        result = aggregate_ubl_lines(lines)
        assert len(result) == 2

    def test_duplicates_merged(self):
        lines = [
            self._make_line("PRODUCT A", 1.0, 5.00),
            self._make_line("PRODUCT A", 1.0, 5.00),
        ]
        result = aggregate_ubl_lines(lines)
        assert len(result) == 1
        assert result[0]["quantity"] == 2.0
        assert abs(result[0]["line_total"] - 10.00) < 0.001

    def test_empty_description_skipped(self):
        lines = [
            self._make_line("", 1.0, 5.00),
            self._make_line("PRODUCT A", 2.0, 10.00),
        ]
        result = aggregate_ubl_lines(lines)
        assert len(result) == 1
        assert result[0]["description"] == "PRODUCT A"


class TestMatchLines:
    def test_exact_match(self):
        ubl_lines = [
            {"description": "JALAPENO 450G", "quantity": 6.0, "line_total": 46.98},
            {"description": "CHEDDAR 1KG", "quantity": 2.0, "line_total": 20.00},
        ]
        dodois_items = [
            {"rawMaterialId": "mat-a", "containerId": "con-a", "qty": 6.0, "totalWithVat": 46.98},
        ]
        result = match_lines(ubl_lines, dodois_items)
        assert len(result) == 1
        assert result[0]["description"] == "JALAPENO 450G"
        assert result[0]["rawMaterialId"] == "mat-a"

    def test_price_tolerance(self):
        ubl_lines = [
            {"description": "PRODUCT A", "quantity": 1.0, "line_total": 10.01},
        ]
        dodois_items = [
            {"rawMaterialId": "mat-a", "containerId": "con-a", "qty": 1.0, "totalWithVat": 10.00},
        ]
        result = match_lines(ubl_lines, dodois_items)
        assert len(result) == 1

    def test_ambiguous_skipped(self):
        ubl_lines = [
            {"description": "PRODUCT A", "quantity": 2.0, "line_total": 20.00},
            {"description": "PRODUCT B", "quantity": 2.0, "line_total": 20.00},
        ]
        dodois_items = [
            {"rawMaterialId": "mat-a", "containerId": "con-a", "qty": 2.0, "totalWithVat": 20.00},
        ]
        result = match_lines(ubl_lines, dodois_items)
        assert len(result) == 0  # ambiguous → skipped

    def test_aggregate_then_match(self):
        # METRO sends same product twice with qty=1 each → aggregate first, then match
        raw_lines = [
            {"description": "PRODUCT A", "quantity": 1.0, "line_total": 5.00},
            {"description": "PRODUCT A", "quantity": 1.0, "line_total": 5.00},
        ]
        aggregated = aggregate_ubl_lines(raw_lines)
        dodois_items = [
            {"rawMaterialId": "mat-a", "containerId": "con-a", "qty": 2.0, "totalWithVat": 10.00},
        ]
        result = match_lines(aggregated, dodois_items)
        assert len(result) == 1
        assert result[0]["description"] == "PRODUCT A"
