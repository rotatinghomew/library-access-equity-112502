"""承诺、路线调整与后续使用的追加式记录。"""

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parents[1] / "src"))

from library_access_equity import ServiceLedger, evaluate_route_adjustment


def bundle(loans=(), visits=(), programs=()):
    return {
        "source_info.json": {"source_kind": "synthetic"},
        "branches.json": [],
        "loans.json": list(loans),
        "visits.json": list(visits),
        "programs.json": list(programs),
        "routes.json": [],
    }


class LedgerTest(unittest.TestCase):
    def test_only_committed_statuses_can_be_recorded_as_commitment(self):
        ledger = ServiceLedger()
        with self.assertRaises(ValueError):
            ledger.record_commitment(
                kind="program_date",
                record_id="pg-1",
                town="hill",
                when="2026-10-04T10:00",
                status="proposed",
                evidence={},
            )

    def test_ledger_rejects_duplicate_entry_id(self):
        ledger = ServiceLedger()
        first = ledger.record_route_change(
            town="hill", route_id="rt-1", action="replace", basis={"reason": "capacity"}, recorded_at="2026-11-01"
        )
        with self.assertRaises(ValueError):
            ledger.append(first)
        self.assertEqual(len(ledger), 1)

    def test_route_change_requires_basis(self):
        ledger = ServiceLedger()
        with self.assertRaises(ValueError):
            ledger.record_route_change(town="hill", route_id="rt-1", action="replace", basis={})

    def test_follow_up_without_records_is_not_improvement_claim(self):
        ledger = ServiceLedger()
        ledger.record_route_change(
            town="hill", route_id="rt-1", action="replace", basis={"reason": "x"}, recorded_at="2026-11-01"
        )
        ledger.record_follow_up(
            town="hill", route_id="rt-1", since="2026-11-02", bundle=bundle(), recorded_at="2026-11-25"
        )
        result = evaluate_route_adjustment(ledger, town="hill", route_id="rt-1")
        self.assertIsNone(result["improved"])
        self.assertIn("缺失", result["note"])

    def test_follow_up_with_after_use_shows_improvement(self):
        data = bundle(
            visits=[
                {
                    "visit_id": "v-1",
                    "town": "hill",
                    "mode": "mobile_bus",
                    "observed_at": "2026-11-10",
                    "reader_count": 12,
                    "quality": "complete",
                }
            ]
        )
        ledger = ServiceLedger()
        ledger.record_route_change(
            town="hill", route_id="rt-1", action="add", basis={"reason": "need"}, recorded_at="2026-11-01"
        )
        ledger.record_follow_up(
            town="hill", route_id="rt-1", since="2026-11-02", bundle=data, recorded_at="2026-11-25"
        )
        result = evaluate_route_adjustment(ledger, town="hill", route_id="rt-1")
        self.assertTrue(result["improved"])
        self.assertEqual(result["follow_ups"][-1]["evidence"]["usage_band"]["low"], 12)

    def test_follow_up_ignores_records_before_route_change(self):
        data = bundle(
            visits=[
                {
                    "visit_id": "v-old",
                    "town": "hill",
                    "mode": "mobile_bus",
                    "observed_at": "2026-10-10",
                    "reader_count": 50,
                    "quality": "complete",
                }
            ]
        )
        ledger = ServiceLedger()
        ledger.record_follow_up(
            town="hill", route_id="rt-1", since="2026-11-02", bundle=data, recorded_at="2026-11-25"
        )
        result = evaluate_route_adjustment(ledger, town="hill", route_id="rt-1")
        self.assertIsNone(result["improved"])

    def test_commitments_appear_as_preserved(self):
        ledger = ServiceLedger()
        ledger.record_commitment(
            kind="program_date",
            record_id="pg-1",
            town="hill",
            when="2026-10-04T10:00",
            status="confirmed",
            evidence={"program_id": "pg-1"},
            recorded_at="2026-09-01",
        )
        ledger.record_route_change(
            town="hill", route_id="rt-1", action="keep", basis={"reason": "no change"}, recorded_at="2026-11-01"
        )
        result = evaluate_route_adjustment(ledger, town="hill", route_id="rt-1")
        self.assertEqual(len(result["commitments_preserved"]), 1)


if __name__ == "__main__":
    unittest.main()
