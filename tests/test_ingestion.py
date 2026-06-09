import csv
from foursight.models import Sensitivity
from foursight.ingestion.csv_adapter import CsvLeaveAdapter
from foursight.ingestion.payroll_redacted import PayrollRedactedAdapter


def test_csv_adapter(tmp_path):
    p = tmp_path / "leave.csv"
    with open(p, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["target_node", "capacity_drop_pct", "single_owner"])
        w.writerow(["alice_owner", "40", "true"])
    pl = CsvLeaveAdapter(str(p)).fetch()[0]
    assert pl.target_node == "alice_owner" and pl.raw["capacity_drop_pct"] == 40.0
    assert pl.raw["single_owner"] is True and pl.change.source == "Leave Calendar"


def test_payroll_pii_free():
    pl = PayrollRedactedAdapter(target_node="comp_pool").emit_comp_delta(amount=23000)
    assert pl.target_node == "comp_pool" and "salary" not in pl.raw and "name" not in pl.raw
    assert pl.change.sensitivity == Sensitivity.INTERNAL


def test_sql_adapter_fetch():
    import sqlite3
    from foursight.db import init_db, seed_metrics
    from foursight.ingestion.sql_adapter import SqlSourceAdapter
    conn = sqlite3.connect(":memory:")
    init_db(conn)
    seed_metrics(conn, [("sumco_yield", "yield_pct", 92.0)])
    a = SqlSourceAdapter(conn, "sumco_yield",
                         "SELECT field, value FROM leaf_metrics WHERE node_id='sumco_yield'")
    assert a.fetch() == {"yield_pct": 92.0}
