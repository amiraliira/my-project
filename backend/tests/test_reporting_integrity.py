"""Exact reporting aggregation, including old/new score evidence mixed ties."""
from decimal import Decimal, localcontext
import sqlite3
import unittest
from pak_maintenance.reporting_service import register_functions, cents_score, rounded_cents


class ReportingIntegrityTests(unittest.TestCase):
    def test_half_even_ties_include_individual_cents_before_rounding(self):
        cases = ((0, "0.005", 0), (1, "0.005", 2), (2, "0.005", 2), (3, "0.005", 4), (1, "0.015", 2))
        for cents, legacy, expected in cases:
            with self.subTest(cents=cents, legacy=legacy):
                self.assertEqual(rounded_cents(cents, legacy), expected)

    def test_cent_sum_exceeds_int64_without_float_or_ambient_context_loss(self):
        c = sqlite3.connect(":memory:")
        try:
            register_functions(c)
            c.execute("CREATE TABLE facts(cents INTEGER)")
            c.executemany("INSERT INTO facts VALUES(?)", [(9_000_000_000_000_000_000,)] * 2)
            with localcontext() as context:
                context.prec = 5
                total = c.execute("SELECT pak_fact_total(pak_cent_sum(cents),NULL) FROM facts").fetchone()[0]
            self.assertEqual(Decimal(total), Decimal("180000000000000000.00"))
            self.assertEqual(cents_score("12345678901234567890123456789"), Decimal("123456789012345678901234567.89"))
        finally:
            c.close()

    def test_empty_or_unknown_evidence_is_not_a_fabricated_grade(self):
        c = sqlite3.connect(":memory:")
        try:
            register_functions(c)
            self.assertEqual(Decimal(c.execute("SELECT pak_fact_total(NULL,NULL)").fetchone()[0]), Decimal(0))
            self.assertIsNone(c.execute("SELECT pak_participant_score(60,NULL,3)").fetchone()[0])
        finally:
            c.close()


if __name__ == "__main__":
    unittest.main()
