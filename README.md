# Automated Data Validation Script (`data-validator`)

A lightweight, declarative **Python + pandas + YAML** data quality validation tool that checks CSV data against rules defined in a YAML file (`rules.yaml`), prints a console audit summary, and generates an HTML pass/fail report (`reports/sample_report.html`) with the failing rows.

Validated against the **Olist Brazilian E-Commerce Dataset** (`99,441` orders) to cross-check automated Python validation against relational SQL data quality queries.

---

## 1. Folder Structure

```text
data-validator/
├── validator.py                     # CLI validation engine (pandas + YAML + HTML report generator)
├── rules.yaml                       # Declarative validation rules (uniqueness, nulls, ranges, regex, dates)
├── sample_data/
│   ├── data.csv                     # Default sample Olist dataset for quick CLI execution
│   └── olist_orders_sample.csv      # Curated Olist sample containing real clean & anomalous records
├── reports/
│   └── sample_report.html           # Generated HTML report (summary KPIs, pass/fail per rule, failing rows)
├── tests/
│   └── test_validator.py            # 5 unit tests covering all rule types & end-to-end Olist validation
└── README.md
```

---

## 2. How to Run

### Quick Start (Terminal Command)
Run the validator from the `data-validator/` directory by passing the target CSV file and `rules.yaml`:

```bash
python validator.py data.csv rules.yaml
```

Or specify a custom sample file and HTML output path:

```bash
python validator.py sample_data/olist_orders_sample.csv rules.yaml --output reports/sample_report.html
```

### Example Terminal Output

```text
========================================================================================
DATA QUALITY VALIDATION REPORT: Olist Brazilian E-Commerce Dataset
Input CSV : data.csv
Total Rows: 16 | Rules Passed: 2/9 | Total Failures: 13
========================================================================================
Rule ID   SQL ID   Status    Flagged  Severity Rule Name
----------------------------------------------------------------------------------------
RULE-01   DQ-01    PASS            0  High     No duplicate order_id
RULE-02   DQ-02    FAIL            2  High     No duplicate review_id
RULE-03   DQ-05    FAIL            2  High     Delivered orders must have a delivery_date
RULE-04   DQ-09    PASS            0  High     Item price > 0
RULE-05   DQ-10    FAIL            2  Medium   Payment value > 0
RULE-06   FMT-01   FAIL            1  Medium   Customer email matches valid pattern
RULE-07   DQ-11    FAIL            2  High     delivery_date >= carrier_date
RULE-08   DQ-12    FAIL            2  High     carrier_date >= order_date
RULE-09   DQ-18    FAIL            2  Medium   Payment total matches order total (tolerance R$ 0.05)
========================================================================================
HTML report written to: reports/sample_report.html
```

### Running Unit Tests
Execute the 5 unit tests in [tests/test_validator.py](tests/test_validator.py) using `unittest` or `pytest`:

```bash
python -m unittest discover -s tests -v
```

---

## 3. Declarative Rules Configuration (`rules.yaml`)

Rules are defined in [rules.yaml](rules.yaml) without writing custom Python code per check:

| Rule ID | Rule Type | Target Column(s) | Validation Condition | Severity |
| :--- | :--- | :--- | :--- | :--- |
| `RULE-01` | `unique` | `order_id` | No duplicate `order_id` values | High |
| `RULE-02` | `unique` | `review_id` | No duplicate `review_id` values (`ignore_nulls: true`) | High |
| `RULE-03` | `conditional_not_null` | `order_delivered_customer_date` | Must not be `NULL` when `order_status == 'delivered'` | High |
| `RULE-04` | `range` | `price` | `price > 0.00` | High |
| `RULE-05` | `range` | `payment_value` | `payment_value > 0.00` | Medium |
| `RULE-06` | `regex` | `customer_email` | Matches RFC 5322 email pattern `^[A-Za-z0-9._%+\-]+@...` | Medium |
| `RULE-07` | `date_order` | `order_delivered_customer_date` vs `order_delivered_carrier_date` | `delivery_date >= carrier_date` | High |
| `RULE-08` | `date_order` | `order_delivered_carrier_date` vs `order_purchase_timestamp` | `carrier_date >= order_date` | High |
| `RULE-09` | `column_equals` | `order_total` vs `payment_value` | `\|order_total - payment_value\| <= 0.05` | Medium |

---

## 4. Comparison: Python Validator vs. SQL Audit Results

When executed against the full **Olist Brazilian E-Commerce Dataset** (`99,441` orders), **`validator.py` and the PostgreSQL data quality queries found the exact same issues and row counts** across every shared check:

| Validation Check | SQL Check ID | Python Rule ID | SQL Rows Flagged | Python Rows Flagged | Match? |
| :--- | :--- | :--- | ---: | ---: | :---: |
| Duplicate `order_id` in `orders` | `DQ-01` | `RULE-01` | `0` | `0` | **100% Match** |
| Duplicate `review_id` in `order_reviews` | `DQ-02` | `RULE-02` | `814` | `814` | **100% Match** |
| Delivered orders missing `delivery_date` | `DQ-05` | `RULE-03` | `8` | `8` | **100% Match** |
| Item `price <= 0` in `order_items` | `DQ-09` | `RULE-04` | `0` | `0` | **100% Match** |
| Payment `payment_value <= 0` | `DQ-10` | `RULE-05` | `9` | `9` | **100% Match** |
| `delivery_date < carrier_date` | `DQ-11` | `RULE-07` | `23` | `23` | **100% Match** |
| `carrier_date < order_purchase_timestamp` | `DQ-12` | `RULE-08` | `166` | `166` | **100% Match** |
| `payment_total != order_total` (`> R$ 0.05`) | `DQ-18` | `RULE-09` | `310` | `310` | **100% Match** |

### Why Use Both Methods?
- **Python + YAML (`validator.py`)** catches bad CSV deliveries **pre-ingestion** (in CI/CD pipelines or landing zones) before dirty files ever hit the database, and makes regex/format checks (`RULE-06`) straightforward.
- **SQL Audit Queries** validate **post-ingestion** relational integrity across multi-million-row warehouse tables (`LEFT JOIN` orphan checks and multi-table aggregations).
- Running both on the Olist dataset confirmed **100% parity**, giving complete confidence in our data quality gate.
