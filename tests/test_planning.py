"""流动资源安排：冲突、容量、无障碍、学校时段与承诺保护。"""

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parents[1] / "src"))

from library_access_equity import analyze_routes, build_report
from library_access_equity.models import CountBand
from library_access_equity.planning import commitment_moves, route_window


def route(**overrides):
    base = {
        "route_id": "rt-1",
        "vehicle": "bus-a",
        "driver": "d-1",
        "towns": ["hill"],
        "starts_at": "2026-10-04T09:00",
        "ends_at": "2026-10-04T12:00",
        "capacity": 20,
        "status": "proposed",
    }
    base.update(overrides)
    return base


def bundle(routes=(), programs=(), visits=(), branches=(), loans=()):
    return {
        "source_info.json": {"source_kind": "synthetic"},
        "branches.json": list(branches),
        "loans.json": list(loans),
        "visits.json": list(visits),
        "programs.json": list(programs),
        "routes.json": list(routes),
    }


class RouteWindowTest(unittest.TestCase):
    def test_missing_end_time_is_unverified(self):
        window = route_window({"route_id": "r", "starts_at": "2026-10-04T09:00"})
        self.assertFalse(window.verified)
        self.assertIsNone(window.end)

    def test_duration_can_complete_window(self):
        window = route_window(
            {"route_id": "r", "starts_at": "2026-10-04T09:00", "duration_minutes": 90}
        )
        self.assertTrue(window.verified)
        self.assertEqual(window.end.hour, 10)
        self.assertEqual(window.end.minute, 30)


class RoutePlanningTest(unittest.TestCase):
    def test_capacity_shortfall_recommends_replacement_for_pending_route(self):
        plan = analyze_routes(
            bundle(routes=[route()]),
            town_demand={"hill": CountBand(35, None)},
        )
        rec = plan.recommendations[0]
        self.assertEqual(rec.action, "replace")
        self.assertTrue(any("容量" in reason for reason in rec.reasons))
        self.assertFalse(rec.protected)

    def test_confirmed_route_is_protected_not_silently_replaced(self):
        plan = analyze_routes(
            bundle(routes=[route(status="confirmed")]),
            town_demand={"hill": CountBand(99, None)},
        )
        rec = plan.recommendations[0]
        self.assertEqual(rec.action, "keep")
        self.assertTrue(rec.protected)
        self.assertTrue(any("保留当时依据" in reason for reason in rec.reasons))

    def test_vehicle_double_booking_is_blocking_and_shows_gap(self):
        plan = analyze_routes(
            bundle(
                routes=[
                    route(route_id="rt-1", towns=["hill"]),
                    route(route_id="rt-2", towns=["river"], starts_at="2026-10-04T10:00", ends_at="2026-10-04T11:00"),
                ]
            )
        )
        conflicts = [c for c in plan.conflicts if c.kind == "resource_double_book"]
        self.assertEqual(len(conflicts), 1)
        self.assertEqual(conflicts[0].severity, "blocking")
        self.assertFalse(conflicts[0].unverified)
        gaps = conflicts[0].service_gap["dropped_route_gaps"]
        self.assertEqual({gap["town"] for gap in gaps}, {"hill", "river"})
        self.assertTrue(all(gap["detail"] for gap in gaps))

    def test_non_overlapping_shift_is_not_blocking(self):
        plan = analyze_routes(
            bundle(
                routes=[
                    route(route_id="rt-1", starts_at="2026-10-04T08:00", ends_at="2026-10-04T09:00"),
                    route(route_id="rt-2", towns=["river"], starts_at="2026-10-04T13:00", ends_at="2026-10-04T14:00"),
                ]
            )
        )
        self.assertEqual([c for c in plan.conflicts if c.kind == "resource_double_book"], [])

    def test_unverified_timing_is_reported_not_assumed_safe(self):
        plan = analyze_routes(
            bundle(
                routes=[
                    route(route_id="rt-1", starts_at="2026-10-04T09:00", ends_at=None),
                    route(route_id="rt-2", towns=["river"], starts_at="2026-10-04T09:00", ends_at=None),
                ]
            )
        )
        conflicts = [c for c in plan.conflicts if c.kind == "resource_double_book"]
        self.assertEqual(len(conflicts), 1)
        self.assertTrue(conflicts[0].unverified)
        self.assertEqual(conflicts[0].severity, "review")

    def test_school_window_misalignment_recommends_replace(self):
        plan = analyze_routes(
            bundle(
                routes=[
                    route(
                        school_windows=[
                            {"town": "hill", "opens_at": "2026-10-04T14:00", "closes_at": "2026-10-04T16:00"}
                        ]
                    )
                ]
            )
        )
        rec = plan.recommendations[0]
        self.assertEqual(rec.action, "replace")
        self.assertEqual(rec.school_alignment, "outside_window")

    def test_accessibility_gap_recommends_replace(self):
        plan = analyze_routes(
            bundle(routes=[route(accessible=False)]),
            town_access={"hill": True},
        )
        rec = plan.recommendations[0]
        self.assertEqual(rec.action, "replace")
        self.assertFalse(rec.access_satisfied)

    def test_confirmed_program_wins_route_time_conflict(self):
        plan = analyze_routes(
            bundle(
                routes=[route(starts_at="2026-10-04T09:00", ends_at="2026-10-04T12:00")],
                programs=[
                    {
                        "program_id": "pg-1",
                        "town": "hill",
                        "starts_at": "2026-10-04T10:00",
                        "ends_at": "2026-10-04T11:00",
                        "status": "confirmed",
                    }
                ],
            )
        )
        conflicts = [c for c in plan.conflicts if c.kind == "route_program_conflict"]
        self.assertEqual(len(conflicts), 1)
        self.assertIn("优先保留活动", conflicts[0].resolution)

    def test_conflict_defers_pending_route_instead_of_recommending_add(self):
        plan = analyze_routes(
            bundle(
                routes=[route(starts_at="2026-10-04T09:00", ends_at="2026-10-04T12:00")],
                programs=[
                    {
                        "program_id": "pg-1",
                        "town": "hill",
                        "starts_at": "2026-10-04T10:00",
                        "ends_at": "2026-10-04T11:00",
                        "status": "confirmed",
                    }
                ],
            )
        )
        rec = plan.recommendations[0]
        self.assertEqual(rec.action, "defer")
        self.assertFalse(any("尚未发现" in reason for reason in rec.reasons))
        self.assertIn("冲突", " ".join(rec.reasons))

    def test_confirmed_route_keeps_protection_despite_conflict(self):
        plan = analyze_routes(
            bundle(
                routes=[route(status="published", starts_at="2026-10-04T09:00", ends_at="2026-10-04T12:00")],
                programs=[
                    {
                        "program_id": "pg-1",
                        "town": "hill",
                        "starts_at": "2026-10-04T10:00",
                        "ends_at": "2026-10-04T11:00",
                        "status": "confirmed",
                    }
                ],
            )
        )
        rec = plan.recommendations[0]
        self.assertEqual(rec.action, "keep")
        self.assertTrue(rec.protected)
        self.assertTrue(any("已公布" in reason for reason in rec.reasons))

    def test_commitment_collected_and_protected(self):
        plan = analyze_routes(
            bundle(
                programs=[
                    {"program_id": "pg-1", "town": "hill", "starts_at": "2026-10-04T10:00", "status": "confirmed"}
                ],
                routes=[route(status="published")],
            )
        )
        kinds = {(item.kind, item.record_id) for item in plan.commitments}
        self.assertIn(("program_date", "pg-1"), kinds)
        self.assertIn(("route_stop", "rt-1"), kinds)
        self.assertTrue(all(item.protected for item in plan.commitments))

    def test_proposed_program_is_not_a_commitment(self):
        plan = analyze_routes(
            bundle(
                programs=[
                    {"program_id": "pg-1", "town": "hill", "starts_at": "2026-10-04T10:00", "status": "proposed"}
                ]
            )
        )
        self.assertEqual(plan.commitments, [])


class CommitmentMoveTest(unittest.TestCase):
    def test_moved_confirmed_date_is_detected(self):
        before = bundle(
            programs=[
                {"program_id": "pg-1", "town": "hill", "starts_at": "2026-10-04T10:00", "status": "confirmed"}
            ]
        )
        after = bundle(
            programs=[
                {"program_id": "pg-1", "town": "hill", "starts_at": "2026-10-11T10:00", "status": "confirmed"}
            ]
        )
        changes = commitment_moves(before, after)
        self.assertEqual(len(changes), 1)
        self.assertEqual(changes[0]["change"], "moved")
        self.assertEqual(changes[0]["before"], "2026-10-04T10:00")

    def test_withdrawn_confirmed_program_is_detected(self):
        before = bundle(
            programs=[
                {"program_id": "pg-1", "town": "hill", "starts_at": "2026-10-04T10:00", "status": "confirmed"}
            ]
        )
        changes = commitment_moves(before, bundle())
        self.assertEqual(changes[0]["change"], "withdrawn")


class ReportPlanningIntegrationTest(unittest.TestCase):
    def test_missing_vehicle_data_is_called_out(self):
        data = bundle(routes=[route()])
        report = build_report(data, as_of="2026-11-25", dataset_presence={"vehicles.json": False})
        self.assertTrue(any("车辆资料尚未到达" in limit or "未核实" in limit for limit in report.comparison_limits))

    def test_route_without_town_is_gap(self):
        plan = analyze_routes(bundle(routes=[route(towns=[])]))
        self.assertTrue(any(gap.kind == "route_without_towns" for gap in plan.gaps))


if __name__ == "__main__":
    unittest.main()
