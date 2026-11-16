#!/usr/bin/env python3
"""
Automated Data Quality Validator (validator.py)
================================================
Reads a CSV dataset, applies declarative validation rules from a YAML
configuration file (rules.yaml), prints a console audit summary, and
generates an interactive HTML discrepancy report.

Usage:
    python validator.py data.csv rules.yaml
    python validator.py sample_data/olist_orders_sample.csv rules.yaml --output reports/sample_report.html
"""

from __future__ import annotations

import argparse
import csv
import html
import os
import re
import sys
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

try:
    import pandas as pd  # type: ignore
    HAS_PANDAS = True
except ImportError:
    HAS_PANDAS = False

try:
    import yaml  # type: ignore
    HAS_YAML = True
except ImportError:
    HAS_YAML = False


@dataclass
class FailureRecord:
    rule_id: str
    sql_check_id: str
    rule_name: str
    severity: str
    row_index: int
    entity_id: str
    column: str
    actual_value: str
    expected_condition: str
    detail: str


@dataclass
class RuleResult:
    rule_id: str
    sql_check_id: str
    name: str
    rule_type: str
    severity: str
    description: str
    total_evaluated: int
    failed_count: int
    passed: bool
    failures: List[FailureRecord] = field(default_factory=list)


@dataclass
class ValidationReport:
    dataset_name: str
    csv_path: str
    total_rows: int
    rules_evaluated: int
    rules_passed: int
    rules_failed: int
    total_failures: int
    rule_results: List[RuleResult]
    generated_at: str


def _parse_simple_yaml(text: str) -> Dict[str, Any]:
    """Fallback parser for rules.yaml when PyYAML is not installed."""
    result: Dict[str, Any] = {"rules": []}
    current_rule: Optional[Dict[str, Any]] = None

    def _clean_val(raw: str) -> Any:
        val = raw.strip()
        if (val.startswith('"') and val.endswith('"')) or (val.startswith("'") and val.endswith("'")):
            val = val[1:-1]
            val = val.replace("\\\\", "\\")
            return val
        if val.lower() == "true":
            return True
        if val.lower() == "false":
            return False
        try:
            if "." in val:
                return float(val)
            return int(val)
        except ValueError:
            return val

    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if line == "rules:":
            continue
        if line.startswith("- "):
            if current_rule is not None:
                result["rules"].append(current_rule)
            current_rule = {}
            rest = line[2:].strip()
            if ":" in rest:
                k, v = rest.split(":", 1)
                current_rule[k.strip()] = _clean_val(v)
        elif ":" in line:
            k, v = line.split(":", 1)
            key = k.strip()
            val = _clean_val(v)
            if current_rule is not None and raw_line.startswith("    "):
                current_rule[key] = val
            else:
                result[key] = val

    if current_rule is not None:
        result["rules"].append(current_rule)
    return result


def load_rules(yaml_path: str | Path) -> Dict[str, Any]:
    """Load validation rules from a YAML file."""
    content = Path(yaml_path).read_text(encoding="utf-8")
    if HAS_YAML:
        return yaml.safe_load(content)
    return _parse_simple_yaml(content)


def load_dataset(csv_path: str | Path) -> List[Dict[str, Any]]:
    """Load CSV rows as a list of dictionaries (via pandas if available, else csv.DictReader)."""
    path = Path(csv_path)
    if not path.exists():
        candidate = Path(__file__).parent / "sample_data" / path.name
        if candidate.exists():
            path = candidate
    if HAS_PANDAS:
        df = pd.read_csv(path, dtype=str, keep_default_na=False)
        return df.to_dict(orient="records")
    with path.open("r", encoding="utf-8", newline="") as f:
        reader = csv.DictReader(f)
        return [dict(row) for row in reader]


def _is_null(val: Any) -> bool:
    if val is None:
        return True
    s = str(val).strip()
    return s == "" or s.upper() in ("NULL", "NAN", "NAT", "NONE")


def _parse_float(val: Any) -> Optional[float]:
    if _is_null(val):
        return None
    try:
        return float(str(val).strip())
    except ValueError:
        return None


def _parse_datetime(val: Any) -> Optional[datetime]:
    if _is_null(val):
        return None
    s = str(val).strip()
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%d"):
        try:
            return datetime.strptime(s, fmt)
        except ValueError:
            continue
    return None


def evaluate_rule(
    rows: List[Dict[str, Any]],
    rule: Dict[str, Any],
    primary_key: str = "order_id",
) -> RuleResult:
    """Evaluate a single declarative validation rule across all dataset rows."""
    rule_id = str(rule.get("id", "RULE-00"))
    sql_check_id = str(rule.get("sql_check_id", "N/A"))
    name = str(rule.get("name", rule_id))
    rule_type = str(rule.get("type", "")).lower()
    severity = str(rule.get("severity", "Medium"))
    description = str(rule.get("description", ""))

    failures: List[FailureRecord] = []
    total_evaluated = len(rows)

    if rule_type == "unique":
        col = str(rule["column"])
        ignore_nulls = bool(rule.get("ignore_nulls", False))
        counts: Dict[str, int] = {}
        first_seen: Dict[str, int] = {}

        for idx, row in enumerate(rows, start=1):
            val = str(row.get(col, "")).strip()
            if _is_null(val) and ignore_nulls:
                continue
            counts[val] = counts.get(val, 0) + 1
            if val not in first_seen:
                first_seen[val] = idx

        # Flag excess duplicate occurrences (consistent with SQL GROUP BY HAVING COUNT(*) > 1)
        seen_so_far: Dict[str, int] = {}
        for idx, row in enumerate(rows, start=1):
            val = str(row.get(col, "")).strip()
            if _is_null(val) and ignore_nulls:
                continue
            seen_so_far[val] = seen_so_far.get(val, 0) + 1
            if counts.get(val, 0) > 1 and seen_so_far[val] > 1:
                entity_id = str(row.get(primary_key) or val or f"row_{idx}")
                failures.append(
                    FailureRecord(
                        rule_id=rule_id,
                        sql_check_id=sql_check_id,
                        rule_name=name,
                        severity=severity,
                        row_index=idx,
                        entity_id=entity_id,
                        column=col,
                        actual_value=f"{val} (occurrence {seen_so_far[val]} of {counts[val]})",
                        expected_condition=f"Unique {col} (1 occurrence)",
                        detail=f"Duplicate value '{val}' in column '{col}' (first seen at row {first_seen[val]})",
                    )
                )

    elif rule_type in ("not_null", "conditional_not_null"):
        col = str(rule["column"])
        when_col = rule.get("when_column")
        when_eq = rule.get("when_equals")

        for idx, row in enumerate(rows, start=1):
            if when_col is not None:
                cond_val = str(row.get(str(when_col), "")).strip()
                if cond_val != str(when_eq):
                    continue
            val = row.get(col)
            if _is_null(val):
                entity_id = str(row.get(primary_key) or f"row_{idx}")
                cond_desc = f" when {when_col}='{when_eq}'" if when_col else ""
                failures.append(
                    FailureRecord(
                        rule_id=rule_id,
                        sql_check_id=sql_check_id,
                        rule_name=name,
                        severity=severity,
                        row_index=idx,
                        entity_id=entity_id,
                        column=col,
                        actual_value="NULL",
                        expected_condition=f"NOT NULL{cond_desc}",
                        detail=f"Missing required value in '{col}'{cond_desc}",
                    )
                )

    elif rule_type == "range":
        col = str(rule["column"])
        min_exc = rule.get("min_exclusive")
        min_inc = rule.get("min_inclusive")
        max_inc = rule.get("max_inclusive")

        for idx, row in enumerate(rows, start=1):
            raw_val = row.get(col)
            if _is_null(raw_val):
                continue
            num = _parse_float(raw_val)
            entity_id = str(row.get(primary_key) or f"row_{idx}")
            if num is None:
                failures.append(
                    FailureRecord(
                        rule_id=rule_id,
                        sql_check_id=sql_check_id,
                        rule_name=name,
                        severity=severity,
                        row_index=idx,
                        entity_id=entity_id,
                        column=col,
                        actual_value=str(raw_val),
                        expected_condition="Numeric value",
                        detail=f"Non-numeric value '{raw_val}' in '{col}'",
                    )
                )
                continue

            violated = False
            expected_parts = []
            if min_exc is not None:
                expected_parts.append(f"> {min_exc}")
                if num <= float(min_exc):
                    violated = True
            if min_inc is not None:
                expected_parts.append(f">= {min_inc}")
                if num < float(min_inc):
                    violated = True
            if max_inc is not None:
                expected_parts.append(f"<= {max_inc}")
                if num > float(max_inc):
                    violated = True

            if violated:
                failures.append(
                    FailureRecord(
                        rule_id=rule_id,
                        sql_check_id=sql_check_id,
                        rule_name=name,
                        severity=severity,
                        row_index=idx,
                        entity_id=entity_id,
                        column=col,
                        actual_value=f"{num:.2f}",
                        expected_condition=f"{col} {' and '.join(expected_parts)}",
                        detail=f"Value {num:.2f} in '{col}' violates range constraint ({' and '.join(expected_parts)})",
                    )
                )

    elif rule_type == "regex":
        col = str(rule["column"])
        pattern_str = str(rule["pattern"])
        regex = re.compile(pattern_str)

        for idx, row in enumerate(rows, start=1):
            raw_val = row.get(col)
            if _is_null(raw_val):
                continue
            s_val = str(raw_val).strip()
            if not regex.match(s_val):
                entity_id = str(row.get(primary_key) or f"row_{idx}")
                failures.append(
                    FailureRecord(
                        rule_id=rule_id,
                        sql_check_id=sql_check_id,
                        rule_name=name,
                        severity=severity,
                        row_index=idx,
                        entity_id=entity_id,
                        column=col,
                        actual_value=s_val,
                        expected_condition=f"Matches /{pattern_str}/",
                        detail=f"Value '{s_val}' in '{col}' does not match required pattern",
                    )
                )

    elif rule_type == "date_order":
        earlier_col = str(rule["earlier_column"])
        later_col = str(rule["later_column"])

        for idx, row in enumerate(rows, start=1):
            dt_early = _parse_datetime(row.get(earlier_col))
            dt_late = _parse_datetime(row.get(later_col))
            if dt_early is None or dt_late is None:
                continue
            if dt_late < dt_early:
                entity_id = str(row.get(primary_key) or f"row_{idx}")
                diff_hours = (dt_late - dt_early).total_seconds() / 3600.0
                failures.append(
                    FailureRecord(
                        rule_id=rule_id,
                        sql_check_id=sql_check_id,
                        rule_name=name,
                        severity=severity,
                        row_index=idx,
                        entity_id=entity_id,
                        column=f"{later_col} vs {earlier_col}",
                        actual_value=f"{row.get(later_col)} < {row.get(earlier_col)} ({diff_hours:.2f}h)",
                        expected_condition=f"{later_col} >= {earlier_col}",
                        detail=f"Chronological inversion: {later_col} ({row.get(later_col)}) precedes {earlier_col} ({row.get(earlier_col)})",
                    )
                )

    elif rule_type == "column_equals":
        left_col = str(rule["left_column"])
        right_col = str(rule["right_column"])
        tolerance = float(rule.get("tolerance", 0.0))

        for idx, row in enumerate(rows, start=1):
            left_val = _parse_float(row.get(left_col))
            right_val = _parse_float(row.get(right_col))
            if left_val is None or right_val is None:
                continue
            diff = right_val - left_val
            if abs(diff) > tolerance + 1e-9:
                entity_id = str(row.get(primary_key) or f"row_{idx}")
                failures.append(
                    FailureRecord(
                        rule_id=rule_id,
                        sql_check_id=sql_check_id,
                        rule_name=name,
                        severity=severity,
                        row_index=idx,
                        entity_id=entity_id,
                        column=f"{left_col} != {right_col}",
                        actual_value=f"{left_col}={left_val:.2f}, {right_col}={right_val:.2f} (diff={diff:+.2f})",
                        expected_condition=f"|{left_col} - {right_col}| <= {tolerance:.2f}",
                        detail=f"Cross-column mismatch: {left_col} ({left_val:.2f}) != {right_col} ({right_val:.2f}), diff={diff:+.2f}",
                    )
                )

    else:
        raise ValueError(f"Unsupported rule type: '{rule_type}' in rule '{rule_id}'")

    failed_count = len(failures)
    return RuleResult(
        rule_id=rule_id,
        sql_check_id=sql_check_id,
        name=name,
        rule_type=rule_type,
        severity=severity,
        description=description,
        total_evaluated=total_evaluated,
        failed_count=failed_count,
        passed=(failed_count == 0),
        failures=failures,
    )


def validate_dataset(
    rows: List[Dict[str, Any]],
    config: Dict[str, Any],
    csv_path: str = "in_memory.csv",
) -> ValidationReport:
    """Apply all rules in config against the loaded dataset rows."""
    dataset_name = str(config.get("dataset_name", "Dataset Validation"))
    primary_key = str(config.get("primary_key", "order_id"))
    rules = config.get("rules", [])

    rule_results: List[RuleResult] = []
    for rule in rules:
        res = evaluate_rule(rows, rule, primary_key=primary_key)
        rule_results.append(res)

    rules_passed = sum(1 for r in rule_results if r.passed)
    rules_failed = len(rule_results) - rules_passed
    total_failures = sum(r.failed_count for r in rule_results)

    return ValidationReport(
        dataset_name=dataset_name,
        csv_path=str(csv_path),
        total_rows=len(rows),
        rules_evaluated=len(rule_results),
        rules_passed=rules_passed,
        rules_failed=rules_failed,
        total_failures=total_failures,
        rule_results=rule_results,
        generated_at=datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
    )


def generate_html_report(report: ValidationReport, output_path: str | Path) -> None:
    """Write a self-contained HTML validation report with rule summaries and failing rows."""
    out = Path(output_path)
    out.parent.mkdir(parents=True, exist_ok=True)

    summary_rows_html = []
    for r in report.rule_results:
        status_badge = (
            '<span class="badge pass">PASS</span>'
            if r.passed
            else '<span class="badge fail">FAIL</span>'
        )
        sev_class = r.severity.lower()
        summary_rows_html.append(
            f"""
            <tr>
              <td><code>{html.escape(r.rule_id)}</code></td>
              <td><code>{html.escape(r.sql_check_id)}</code></td>
              <td><strong>{html.escape(r.name)}</strong><br><small>{html.escape(r.description)}</small></td>
              <td><code>{html.escape(r.rule_type)}</code></td>
              <td><span class="sev {sev_class}">{html.escape(r.severity)}</span></td>
              <td class="num">{r.total_evaluated:,}</td>
              <td class="num">{r.failed_count:,}</td>
              <td>{status_badge}</td>
            </tr>
            """
        )

    failure_rows_html = []
    for r in report.rule_results:
        for f in r.failures:
            sev_class = f.severity.lower()
            failure_rows_html.append(
                f"""
                <tr>
                  <td><code>{html.escape(f.rule_id)}</code></td>
                  <td><code>{html.escape(f.sql_check_id)}</code></td>
                  <td><span class="sev {sev_class}">{html.escape(f.severity)}</span></td>
                  <td class="num">{f.row_index}</td>
                  <td><code>{html.escape(f.entity_id)}</code></td>
                  <td><code>{html.escape(f.column)}</code></td>
                  <td><code>{html.escape(f.actual_value)}</code></td>
                  <td><code>{html.escape(f.expected_condition)}</code></td>
                  <td>{html.escape(f.detail)}</td>
                </tr>
                """
            )

    html_doc = f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <title>Data Quality Validation Report — {html.escape(report.dataset_name)}</title>
  <style>
    :root {{
      --bg: #f8fafc;
      --card: #ffffff;
      --text: #0f172a;
      --muted: #475569;
      --border: #e2e8f0;
      --pass-bg: #dcfce7;
      --pass-fg: #15803d;
      --fail-bg: #fee2e2;
      --fail-fg: #b91c1c;
      --high-bg: #fef2f2;
      --high-fg: #dc2626;
      --med-bg: #fffbeb;
      --med-fg: #d97706;
    }}
    body {{
      font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
      background: var(--bg);
      color: var(--text);
      margin: 0;
      padding: 32px;
      line-height: 1.5;
    }}
    .container {{
      max-width: 1280px;
      margin: 0 auto;
    }}
    header {{
      background: var(--card);
      border: 1px solid var(--border);
      border-radius: 12px;
      padding: 24px 28px;
      margin-bottom: 24px;
      box-shadow: 0 1px 3px rgba(0,0,0,0.04);
    }}
    h1 {{ margin: 0 0 6px 0; font-size: 24px; }}
    h2 {{ margin: 28px 0 12px 0; font-size: 18px; }}
    .meta {{ color: var(--muted); font-size: 14px; }}
    .kpi-grid {{
      display: grid;
      grid-template-columns: repeat(5, 1fr);
      gap: 16px;
      margin-bottom: 24px;
    }}
    .kpi-card {{
      background: var(--card);
      border: 1px solid var(--border);
      border-radius: 10px;
      padding: 18px 20px;
      box-shadow: 0 1px 2px rgba(0,0,0,0.03);
    }}
    .kpi-label {{ color: var(--muted); font-size: 12px; text-transform: uppercase; letter-spacing: 0.04em; }}
    .kpi-value {{ font-size: 26px; font-weight: 700; margin-top: 4px; }}
    table {{
      width: 100%;
      border-collapse: collapse;
      background: var(--card);
      border: 1px solid var(--border);
      border-radius: 10px;
      overflow: hidden;
      font-size: 13.5px;
      margin-bottom: 28px;
    }}
    th, td {{
      padding: 11px 14px;
      border-bottom: 1px solid var(--border);
      text-align: left;
      vertical-align: top;
    }}
    th {{
      background: #f1f5f9;
      font-weight: 600;
      color: #1e293b;
    }}
    td.num, th.num {{ text-align: right; font-variant-numeric: tabular-nums; }}
    small {{ color: var(--muted); }}
    .badge {{
      display: inline-block;
      padding: 3px 10px;
      border-radius: 999px;
      font-weight: 700;
      font-size: 11.5px;
    }}
    .badge.pass {{ background: var(--pass-bg); color: var(--pass-fg); }}
    .badge.fail {{ background: var(--fail-bg); color: var(--fail-fg); }}
    .sev {{
      display: inline-block;
      padding: 2px 8px;
      border-radius: 6px;
      font-weight: 600;
      font-size: 11.5px;
    }}
    .sev.high {{ background: var(--high-bg); color: var(--high-fg); }}
    .sev.medium {{ background: var(--med-bg); color: var(--med-fg); }}
  </style>
</head>
<body>
  <div class="container">
    <header>
      <h1>Automated Data Quality Report — {html.escape(report.dataset_name)}</h1>
      <div class="meta">
        Source File: <code>{html.escape(report.csv_path)}</code> &nbsp;|&nbsp;
        Generated At: <code>{html.escape(report.generated_at)}</code> &nbsp;|&nbsp;
        Cross-Checked Against: <strong>PostgreSQL Audit (DQ-01..DQ-20)</strong>
      </div>
    </header>

    <div class="kpi-grid">
      <div class="kpi-card">
        <div class="kpi-label">Total Rows Evaluated</div>
        <div class="kpi-value">{report.total_rows:,}</div>
      </div>
      <div class="kpi-card">
        <div class="kpi-label">Rules Evaluated</div>
        <div class="kpi-value">{report.rules_evaluated}</div>
      </div>
      <div class="kpi-card">
        <div class="kpi-label">Rules Passed</div>
        <div class="kpi-value" style="color: var(--pass-fg);">{report.rules_passed}</div>
      </div>
      <div class="kpi-card">
        <div class="kpi-label">Rules Failed</div>
        <div class="kpi-value" style="color: var(--fail-fg);">{report.rules_failed}</div>
      </div>
      <div class="kpi-card">
        <div class="kpi-label">Total Failing Records</div>
        <div class="kpi-value" style="color: var(--fail-fg);">{report.total_failures:,}</div>
      </div>
    </div>

    <h2>1. Validation Summary by Rule</h2>
    <table>
      <thead>
        <tr>
          <th>Rule ID</th>
          <th>SQL Check ID</th>
          <th>Rule Name &amp; Description</th>
          <th>Check Type</th>
          <th>Severity</th>
          <th class="num">Rows Checked</th>
          <th class="num">Rows Flagged</th>
          <th>Status</th>
        </tr>
      </thead>
      <tbody>
        {''.join(summary_rows_html)}
      </tbody>
    </table>

    <h2>2. Detailed Failing Rows ({report.total_failures:,} Flagged Violations)</h2>
    <table>
      <thead>
        <tr>
          <th>Rule ID</th>
          <th>SQL Check</th>
          <th>Severity</th>
          <th class="num">CSV Row #</th>
          <th>Entity ID (order_id)</th>
          <th>Column(s)</th>
          <th>Actual Value</th>
          <th>Expected Rule</th>
          <th>Failure Detail</th>
        </tr>
      </thead>
      <tbody>
        {''.join(failure_rows_html)}
      </tbody>
    </table>
  </div>
</body>
</html>
"""
    out.write_text(html_doc, encoding="utf-8")


def print_console_report(report: ValidationReport) -> None:
    """Print formatted validation results to stdout."""
    print("=" * 88)
    print(f"DATA QUALITY VALIDATION REPORT: {report.dataset_name}")
    print(f"Input CSV : {report.csv_path}")
    print(f"Total Rows: {report.total_rows:,} | Rules Passed: {report.rules_passed}/{report.rules_evaluated} | Total Failures: {report.total_failures:,}")
    print("=" * 88)
    print(f"{'Rule ID':<9} {'SQL ID':<8} {'Status':<8} {'Flagged':>8}  {'Severity':<8} {'Rule Name'}")
    print("-" * 88)
    for r in report.rule_results:
        status = "PASS" if r.passed else "FAIL"
        print(f"{r.rule_id:<9} {r.sql_check_id:<8} {status:<8} {r.failed_count:>8,}  {r.severity:<8} {r.name}")
    print("=" * 88)


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Validate a CSV dataset against declarative YAML rules.")
    parser.add_argument("csv_path", help="Path to the input CSV file (e.g., data.csv or sample_data/olist_orders_sample.csv)")
    parser.add_argument("rules_path", help="Path to the YAML rules file (e.g., rules.yaml)")
    parser.add_argument(
        "--output",
        "-o",
        default="reports/sample_report.html",
        help="Path to write the HTML report (default: reports/sample_report.html)",
    )
    args = parser.parse_args(argv)

    config = load_rules(args.rules_path)
    rows = load_dataset(args.csv_path)
    report = validate_dataset(rows, config, csv_path=args.csv_path)

    print_console_report(report)
    generate_html_report(report, args.output)
    print(f"HTML report written to: {args.output}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
