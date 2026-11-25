"""证据聚合：缺失、人数区间、机会范围与历史时点。"""

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parents[1] / "src"))

from library_access_equity import build_report
from library_access_equity.summaries import build_coverage, summarize_loans, summarize_visits


def bundle(**overrides):
    base = {
        "source_info.json": {"source_kind": "synthetic"},
        "branches.json": [],
        "loans.json": [],
        "visits.json": [],
        "programs.json": [],
        "routes.json": [],
    }
    base.update(overrides)
    return base


class LoanEvidenceTest(unittest.TestCase):
    def test_renewal_does_not_double_count_service_unit(self):
        loans = [
            {
                "loan_id": "l-1",
                "member_key": "m-1",
                "household_key": "h-1",
                "branch_id": "B-1",
                "item_id": "i-1",
                "loaned_at": "2026-09-01",
                "kind": "standard",
                "renewal_count": 2,
            }
        ]
        summary = summarize_loans(loans)
        self.assertEqual(summary.interactions, 1)
        self.assertEqual(summary.service_units, 1)
        self.assertEqual(summary.renewals, 2)

    def test_cross_branch_return_is_separate_fact(self):
        loans = [
            {
                "loan_id": "l-1",
                "member_key": "m-1",
                "branch_id": "B-1",
                "returned_branch_id": "B-2",
                "loaned_at": "2026-09-01",
                "kind": "standard",
            }
        ]
        summary = summarize_loans(loans)
        self.assertEqual(summary.cross_branch_returns, 1)
        self.assertEqual(summary.interactions, 1)

    def test_proxy_and_family_share_only_give_lower_bound(self):
        loans = [
            {"loan_id": "l-1", "member_key": "m-1", "household_key": "h-1", "branch_id": "B-1", "kind": "standard"},
            {"loan_id": "l-2", "member_key": "m-2", "household_key": "h-1", "branch_id": "B-1", "kind": "standard"},
            {"loan_id": "l-3", "member_key": None, "household_key": "h-1", "branch_id": "B-1", "kind": "proxy"},
        ]
        summary = summarize_loans(loans)
        # 两个可识别成员至少是两位读者；代借不让上界封闭。
        self.assertEqual(summary.reader_band.low, 2)
        self.assertIsNone(summary.reader_band.high)
        self.assertFalse(summary.reader_band.is_unknown is False)
        self.assertEqual(summary.known_members, 2)
        self.assertEqual(summary.households, 1)
        # 代借有家庭关联但没有独立身份，因此不计入"完全未关联"，
        # 仍通过上界未知体现。
        self.assertEqual(summary.unlinked_interactions, 0)
        self.assertEqual(summary.by_kind["proxy"], 1)

    def test_single_household_without_members_is_one_reader_lower_bound(self):
        loans = [
            {"loan_id": "l-1", "member_key": None, "household_key": "h-1", "branch_id": "B-1", "kind": "proxy"},
            {"loan_id": "l-2", "member_key": None, "household_key": "h-1", "branch_id": "B-1", "kind": "proxy"},
        ]
        summary = summarize_loans(loans)
        self.assertEqual(summary.reader_band.low, 1)
        self.assertIsNone(summary.reader_band.high)
        self.assertEqual(summary.known_members, 0)

    def test_unknown_kind_keeps_upper_bound_open(self):
        loans = [
            {"loan_id": "l-1", "member_key": "m-1", "branch_id": "B-1", "kind": "unknown"},
        ]
        summary = summarize_loans(loans)
        self.assertEqual(summary.reader_band.low, 1)
        self.assertIsNone(summary.reader_band.high)

    def test_loan_before_branch_active_date_is_flagged(self):
        loans = [
            {
                "loan_id": "l-1",
                "member_key": "m-1",
                "branch_id": "B-1",
                "loaned_at": "2025-06-01",
                "kind": "standard",
            }
        ]
        branches = {"B-1": {"branch_id": "B-1", "town": "river", "active_from": "2026-01-01"}}
        summary = summarize_loans(loans, branches=branches)
        self.assertEqual(summary.resource_unavailable_at_event, 1)

    def test_no_loans_is_zero_but_not_confirmed_no_reading(self):
        summary = summarize_loans([])
        self.assertEqual(summary.reader_band.low, 0)
        self.assertEqual(summary.reader_band.high, 0)
        self.assertIn("不是确认", summary.reader_band.note)


class VisitEvidenceTest(unittest.TestCase):
    def test_online_open_is_not_reading_completion(self):
        visits = [
            {
                "visit_id": "v-1",
                "town": "hill",
                "mode": "online",
                "observed_at": "2026-09-10",
                "reader_count": 120,
                "quality": "complete",
            }
        ]
        summary = summarize_visits(visits)
        self.assertEqual(summary.digital_opens, 120)
        self.assertEqual(summary.digital_reading_completions, 0)
        self.assertEqual(summary.physical_attendance, 0)

    def test_missing_reader_count_is_not_zero(self):
        visits = [
            {
                "visit_id": "v-1",
                "town": "hill",
                "mode": "mobile_bus",
                "observed_at": "2026-09-10",
                "reader_count": None,
                "quality": "partial",
            }
        ]
        summary = summarize_visits(visits)
        self.assertEqual(summary.physical_attendance, 0)
        self.assertEqual(summary.physical_unknown_records, 1)
        band = summary.by_mode["mobile_bus"]
        self.assertEqual(band.unknown_volume_records, 1)
        self.assertEqual(band.known_attendance, 0)

    def test_future_visits_excluded_at_historical_as_of(self):
        visits = [
            {
                "visit_id": "v-1",
                "town": "hill",
                "mode": "mobile_bus",
                "observed_at": "2026-12-01",
                "reader_count": 10,
                "quality": "complete",
            }
        ]
        summary = summarize_visits(visits, as_of=None)
        self.assertEqual(summary.physical_attendance, 10)
        from datetime import date

        historical = summarize_visits(visits, as_of=date(2026, 9, 30))
        self.assertEqual(historical.physical_attendance, 0)
        self.assertNotIn("mobile_bus", historical.by_mode)

    def test_unrecognized_mode_is_not_counted_as_online_or_physical(self):
        visits = [
            {
                "visit_id": "v-1",
                "town": "hill",
                "mode": "kiosk",
                "observed_at": "2026-09-10",
                "reader_count": 5,
                "quality": "complete",
            }
        ]
        summary = summarize_visits(visits)
        self.assertEqual(summary.digital_opens, 0)
        self.assertEqual(summary.physical_attendance, 0)
        self.assertTrue(any("无法识别" in note for note in summary.notes))


class CoverageTest(unittest.TestCase):
    def test_missing_record_is_not_no_service(self):
        statuses = build_coverage("hill", ["mobile_bus"], [], [], [])
        status = statuses["mobile_bus"]
        self.assertEqual(status.status, "missing_record")
        self.assertTrue(status.is_gap)
        self.assertIn("不能据此认定", status.note)

    def test_no_opportunity_is_not_a_gap(self):
        statuses = build_coverage(
            "hill", ["branch", "mobile_bus"], [], [], [], available_modes=["branch"]
        )
        self.assertEqual(statuses["mobile_bus"].status, "no_opportunity")
        self.assertFalse(statuses["mobile_bus"].is_gap)

    def test_planned_only_mode_is_not_missing_record(self):
        statuses = build_coverage(
            "hill",
            ["mobile_bus"],
            [],
            [],
            [],
            available_modes=["branch"],
            planned_modes=["mobile_bus"],
        )
        self.assertEqual(statuses["mobile_bus"].status, "planned_only")
        self.assertTrue(statuses["mobile_bus"].planned)
        self.assertFalse(statuses["mobile_bus"].is_gap)

    def test_loan_records_do_not_inflate_attendance(self):
        statuses = build_coverage(
            "river",
            ["branch"],
            visits=[],
            loans=[{"loan_id": "l-1", "branch_id": "B-1", "loaned_at": "2026-09-01", "kind": "standard"}],
            programs=[],
            available_modes=["branch"],
        )
        self.assertEqual(statuses["branch"].known_volume, 0)
        self.assertEqual(statuses["branch"].unknown_volume_records, 1)

    def test_proposed_program_is_not_counted_as_service(self):
        statuses = build_coverage(
            "hill",
            ["program"],
            visits=[],
            loans=[],
            programs=[{"program_id": "pg-1", "town": "hill", "status": "proposed", "capacity": 10}],
            available_modes=["program"],
        )
        self.assertEqual(statuses["program"].status, "unconfirmed")
        self.assertEqual(statuses["program"].records, 0)
        self.assertTrue(statuses["program"].planned)


class ReportTest(unittest.TestCase):
    def test_report_marks_opportunity_and_gap_separately(self):
        data = bundle(
            **{
                "branches.json": [
                    {"branch_id": "B-hill", "town": "hill", "accessible": False, "active_from": "2026-01-01"}
                ],
                "visits.json": [
                    {
                        "visit_id": "v-1",
                        "town": "hill",
                        "mode": "online",
                        "observed_at": "2026-09-10",
                        "reader_count": None,
                        "quality": "partial",
                    }
                ],
            }
        )
        report = build_report(data, as_of="2026-11-25")
        hill = report.town("hill")
        self.assertIn("online", report.opportunities["hill"].available_modes)
        self.assertTrue(hill.access.access_gap)
        self.assertTrue(any(gap.kind == "partial" for gap in hill.evidence_gaps))
        self.assertEqual(hill.visits.physical_attendance, 0)
        self.assertEqual(hill.visits.digital_opens, 1)

    def test_branch_relocation_and_close_date_are_respected(self):
        data = bundle(
            **{
                "branches.json": [
                    {
                        "branch_id": "B-old",
                        "town": "river",
                        "accessible": True,
                        "active_from": "2024-01-01",
                        "closed_at": "2026-06-01",
                    }
                ],
                "loans.json": [
                    {
                        "loan_id": "l-1",
                        "member_key": "m-1",
                        "branch_id": "B-old",
                        "loaned_at": "2026-08-01",
                        "kind": "standard",
                    }
                ],
            }
        )
        report = build_report(data, as_of="2026-11-25")
        river = report.town("river")
        self.assertEqual(river.loans.resource_unavailable_at_event, 1)
        self.assertEqual(report.opportunities["river"].branches, ())


if __name__ == "__main__":
    unittest.main()
