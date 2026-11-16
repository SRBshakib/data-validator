"""
Unit Tests for Automated Data Validator (tests/test_validator.py)
=================================================================
Run with:
    python -m unittest discover -s tests
    pytest tests/
"""

import sys
import unittest
from pathlib import Path

# Ensure parent directory is importable
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from validator import evaluate_rule, load_dataset, load_rules, validate_dataset  # noqa: E402


class TestDataValidator(unittest.TestCase):
    """5 core unit tests verifying rule evaluation and Olist sample parity."""

    def test_01_no_duplicate_order_id(self) -> None:
        """Rule type 'unique': flags duplicate order_id rows and passes unique ones."""
        rule = {
            "id": "RULE-01",
            "sql_check_id": "DQ-01",
            "name": "No duplicate order_id",
            "type": "unique",
            "column": "order_id",
            "severity": "High",
        }
        rows = [
            {"order_id": "ord_100"},
            {"order_id": "ord_101"},
            {"order_id": "ord_100"},  # duplicate
        ]
        result = evaluate_rule(rows, rule)
        self.assertFalse(result.passed)
        self.assertEqual(result.failed_count, 1)
        self.assertEqual(result.failures[0].entity_id, "ord_100")

    def test_02_price_greater_than_zero(self) -> None:
        """Rule type 'range': flags price <= 0 and passes positive prices."""
        rule = {
            "id": "RULE-04",
            "sql_check_id": "DQ-09",
            "name": "Item price > 0",
            "type": "range",
            "column": "price",
            "min_exclusive": 0.0,
            "severity": "High",
        }
        rows = [
            {"order_id": "ord_1", "price": "29.99"},
            {"order_id": "ord_2", "price": "0.00"},
            {"order_id": "ord_3", "price": "-15.50"},
        ]
        result = evaluate_rule(rows, rule)
        self.assertFalse(result.passed)
        self.assertEqual(result.failed_count, 2)
        flagged_ids = [f.entity_id for f in result.failures]
        self.assertEqual(flagged_ids, ["ord_2", "ord_3"])

    def test_03_email_matches_pattern(self) -> None:
        """Rule type 'regex': flags invalid email strings and passes RFC-valid emails."""
        rule = {
            "id": "RULE-06",
            "sql_check_id": "FMT-01",
            "name": "Customer email matches valid pattern",
            "type": "regex",
            "column": "customer_email",
            "pattern": r"^[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}$",
            "severity": "Medium",
        }
        rows = [
            {"order_id": "ord_ok", "customer_email": "ana.silva@olist.com.br"},
            {"order_id": "ord_bad", "customer_email": "invalid-email-no-at-sign"},
        ]
        result = evaluate_rule(rows, rule)
        self.assertFalse(result.passed)
        self.assertEqual(result.failed_count, 1)
        self.assertEqual(result.failures[0].entity_id, "ord_bad")

    def test_04_delivery_date_on_or_after_order_date(self) -> None:
        """Rule type 'date_order': flags delivery timestamps earlier than order/carrier timestamps."""
        rule = {
            "id": "RULE-07",
            "sql_check_id": "DQ-11",
            "name": "delivery_date >= carrier_date",
            "type": "date_order",
            "earlier_column": "order_delivered_carrier_date",
            "later_column": "order_delivered_customer_date",
            "severity": "High",
        }
        rows = [
            {
                "order_id": "ord_valid",
                "order_delivered_carrier_date": "2017-07-05 10:00:00",
                "order_delivered_customer_date": "2017-07-10 15:00:00",
            },
            {
                "order_id": "2fb597c2f7720ab52ef16ca73d38c890",
                "order_delivered_carrier_date": "2017-07-10 19:50:48",
                "order_delivered_customer_date": "2017-07-05 15:43:29",
            },
        ]
        result = evaluate_rule(rows, rule)
        self.assertFalse(result.passed)
        self.assertEqual(result.failed_count, 1)
        self.assertEqual(result.failures[0].entity_id, "2fb597c2f7720ab52ef16ca73d38c890")

    def test_05_end_to_end_olist_sample_matches_sql_audit(self) -> None:
        """End-to-end validation on sample_data/olist_orders_sample.csv matches SQL checks."""
        base_dir = Path(__file__).resolve().parent.parent
        rules = load_rules(base_dir / "rules.yaml")
        rows = load_dataset(base_dir / "sample_data" / "olist_orders_sample.csv")
        report = validate_dataset(rows, rules)

        self.assertEqual(report.total_rows, 16)
        self.assertEqual(report.rules_evaluated, 9)
        self.assertEqual(report.rules_passed, 2)  # RULE-01 (order_id) and RULE-04 (price > 0)
        self.assertEqual(report.rules_failed, 7)
        self.assertEqual(report.total_failures, 13)


if __name__ == "__main__":
    unittest.main()

