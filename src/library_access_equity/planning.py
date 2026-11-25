"""流动资源安排、冲突检查与承诺保护。

这里把两类决定分开：

* **可重评**：``proposed``/``planned`` 路线、尚未确认的活动，可以在新借阅或
  新设施记录到达后重算。
* **不可静默挪动**：``confirmed``/``published``/``completed`` 的到馆日期和活动
  是已公布承诺。本模块只把它们标记为受保护并检查变更，不提供"按总榜重排"的
  入口。

冲突以"同一车辆/同一司机/同一乡镇在重叠时段被占用"为判断依据；资料无法给出
时段时，明确标为 ``timing_unverified``，不假定不存在冲突。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Iterable, Mapping

from .models import CountBand, EvidenceGap
from .summaries import branch_active_on, normalize_mode

COMMITTED_STATUSES = {"confirmed", "published", "completed"}
PENDING_STATUSES = {"proposed", "planned", "draft"}
INACTIVE_STATUSES = {"cancelled", "withdrawn", "archived", "declined"}


def _dt(value: Any) -> datetime | None:
    if not isinstance(value, str) or len(value) < 10:
        return None
    text = value.strip().replace("Z", "+00:00")
    try:
        return datetime.fromisoformat(text)
    except ValueError:
        try:
            return datetime.fromisoformat(value[:10])
        except ValueError:
            return None


def _minutes(value: Any) -> int | None:
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return int(value)
    if isinstance(value, str) and value.strip().lstrip("-").isdigit():
        return int(value)
    return None


@dataclass(frozen=True)
class TimeWindow:
    start: datetime | None
    end: datetime | None
    verified: bool
    source: str = "routes"

    def overlaps(self, other: "TimeWindow") -> bool | None:
        if not self.verified or not other.verified:
            return None
        if self.start is None or self.end is None or other.start is None or other.end is None:
            return None
        return self.start < other.end and other.start < self.end

    def as_dict(self) -> dict[str, Any]:
        return {
            "start": self.start.isoformat() if self.start else None,
            "end": self.end.isoformat() if self.end else None,
            "verified": self.verified,
            "source": self.source,
        }


def route_window(route: Mapping[str, Any]) -> TimeWindow:
    """推导一次路线运行的时段；缺少结束时间即为未核实时段。"""
    stops = route.get("stops") or route.get("visits") or ()
    starts: list[datetime] = []
    ends: list[datetime] = []
    for stop in stops:
        if not isinstance(stop, Mapping):
            continue
        start = _dt(stop.get("arrive_at") or stop.get("starts_at"))
        end = _dt(stop.get("depart_at") or stop.get("ends_at"))
        if start:
            starts.append(start)
        if end:
            ends.append(end)
    if starts and ends:
        return TimeWindow(min(starts), max(ends), True, "stops")

    start = _dt(route.get("starts_at") or route.get("departs_at"))
    end = _dt(route.get("ends_at") or route.get("returns_at"))
    duration = _minutes(route.get("duration_minutes"))
    if start and end:
        return TimeWindow(start, end, True, "route")
    if start and duration is not None:
        return TimeWindow(start, _add_minutes(start, duration), True, "route")
    return TimeWindow(start, None, False, "route")


def _add_minutes(value: datetime, minutes: int) -> datetime:
    from datetime import timedelta

    return value + timedelta(minutes=minutes)


def stop_window(route: Mapping[str, Any], town: str) -> TimeWindow:
    for stop in route.get("stops") or ():
        if isinstance(stop, Mapping) and str(stop.get("town")) == town:
            start = _dt(stop.get("arrive_at") or stop.get("starts_at"))
            end = _dt(stop.get("depart_at") or stop.get("ends_at"))
            return TimeWindow(start, end, bool(start and end), "stop")
    return route_window(route)


@dataclass
class RouteConflict:
    kind: str
    severity: str
    route_ids: tuple[str, ...]
    towns: tuple[str, ...]
    detail: str
    resolution: str
    dropped_route_id: str | None = None
    service_gap: dict[str, Any] | None = None
    unverified: bool = False

    def as_dict(self) -> dict[str, Any]:
        return {
            "kind": self.kind,
            "severity": self.severity,
            "route_ids": list(self.route_ids),
            "towns": list(self.towns),
            "detail": self.detail,
            "resolution": self.resolution,
            "dropped_route_id": self.dropped_route_id,
            "service_gap": self.service_gap,
            "unverified": self.unverified,
        }


@dataclass
class RouteRecommendation:
    route_id: str
    town: str
    action: str
    status: str
    reasons: list[str] = field(default_factory=list)
    protected: bool = False
    demand_band: CountBand | None = None
    capacity: int | None = None
    access_requirement: str | None = None
    access_satisfied: bool | None = None
    driver: str | None = None
    vehicle: str | None = None
    school_window: str | None = None
    school_alignment: str | None = None
    alternatives: list[str] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        return {
            "route_id": self.route_id,
            "town": self.town,
            "action": self.action,
            "status": self.status,
            "reasons": list(self.reasons),
            "protected": self.protected,
            "demand_band": self.demand_band.as_dict() if self.demand_band else None,
            "capacity": self.capacity,
            "access_requirement": self.access_requirement,
            "access_satisfied": self.access_satisfied,
            "driver": self.driver,
            "vehicle": self.vehicle,
            "school_window": self.school_window,
            "school_alignment": self.school_alignment,
            "alternatives": list(self.alternatives),
        }


@dataclass
class Commitment:
    kind: str
    record_id: str
    town: str
    when: str | None
    status: str
    evidence: dict[str, Any]
    protected: bool = True

    def as_dict(self) -> dict[str, Any]:
        return {
            "kind": self.kind,
            "record_id": self.record_id,
            "town": self.town,
            "when": self.when,
            "status": self.status,
            "evidence": dict(self.evidence),
            "protected": self.protected,
        }


@dataclass
class RoutePlan:
    as_of: str | None
    recommendations: list[RouteRecommendation] = field(default_factory=list)
    conflicts: list[RouteConflict] = field(default_factory=list)
    commitments: list[Commitment] = field(default_factory=list)
    gaps: list[EvidenceGap] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)

    def actions(self) -> dict[str, list[str]]:
        result: dict[str, list[str]] = {"add": [], "replace": [], "keep": [], "defer": []}
        for rec in self.recommendations:
            result.setdefault(rec.action, []).append(rec.route_id)
        return result

    def as_dict(self) -> dict[str, Any]:
        return {
            "as_of": self.as_of,
            "recommendations": [rec.as_dict() for rec in self.recommendations],
            "actions": self.actions(),
            "conflicts": [conflict.as_dict() for conflict in self.conflicts],
            "commitments": [commitment.as_dict() for commitment in self.commitments],
            "gaps": [gap.as_dict() for gap in self.gaps],
            "notes": list(self.notes),
        }


@dataclass
class ServiceGap:
    town: str
    dropped_route_id: str
    reader_band: CountBand
    lost_visits: int
    lost_programs: list[str]
    access_loss: bool
    alternatives: list[str]
    detail: str

    def as_dict(self) -> dict[str, Any]:
        return {
            "town": self.town,
            "dropped_route_id": self.dropped_route_id,
            "reader_band": self.reader_band.as_dict(),
            "lost_visits": self.lost_visits,
            "lost_programs": list(self.lost_programs),
            "access_loss": self.access_loss,
            "alternatives": list(self.alternatives),
            "detail": self.detail,
        }


def _lost_stop_count(route: Mapping[str, Any], town: str) -> int:
    stops = [stop for stop in route.get("stops") or () if isinstance(stop, Mapping)]
    matched = [stop for stop in stops if str(stop.get("town")) == town]
    return len(matched) if matched else 1


def _apply_conflicts_to_recommendations(
    recommendations: Iterable[RouteRecommendation],
    conflicts: Iterable[RouteConflict],
) -> None:
    """把冲突回写到路线建议，避免建议与冲突列表相互矛盾。"""
    for conflict in conflicts:
        for rec in recommendations:
            if rec.route_id not in conflict.route_ids:
                continue
            if conflict.towns and rec.town not in conflict.towns:
                continue
            rec.alternatives = sorted(set(rec.alternatives) | set(conflict.route_ids) - {rec.route_id})
            if rec.protected:
                rec.reasons.append(
                    f"与 {', '.join(rid for rid in conflict.route_ids if rid != rec.route_id)} "
                    "存在冲突；该路线已公布，保留原安排并需显式记录变更。"
                )
                continue
            if rec.action in {"add", "keep"}:
                rec.action = "defer"
            rec.reasons = [reason for reason in rec.reasons if "尚未发现" not in reason]
            rec.reasons.append(f"存在 {conflict.kind} 冲突，需先解决时段或资源占用再安排。")


def _route_capacity(route: Mapping[str, Any]) -> int | None:
    for key in ("capacity", "book_capacity", "seat_capacity"):
        value = _minutes(route.get(key))
        if value is not None:
            return value
    return None


def _route_accessible(route: Mapping[str, Any]) -> bool | None:
    for key in ("accessible", "wheelchair_accessible", "access_equipment"):
        if key in route:
            value = route.get(key)
            if isinstance(value, bool):
                return value
            if isinstance(value, str):
                return value.strip().lower() not in {"", "none", "no", "false", "0"}
    return None


def _overlap_routes(routes: list[Mapping[str, Any]]) -> list[RouteConflict]:
    conflicts: list[RouteConflict] = []
    active = [
        route
        for route in routes
        if str(route.get("status") or "").strip().lower() not in INACTIVE_STATUSES
    ]
    for index, left in enumerate(active):
        for right in active[index + 1 :]:
            left_window = route_window(left)
            right_window = route_window(right)
            overlap = left_window.overlaps(right_window)

            shared_vehicle = left.get("vehicle") and left.get("vehicle") == right.get("vehicle")
            shared_driver = left.get("driver") and left.get("driver") == right.get("driver")
            left_towns = set(left.get("towns") or ())
            right_towns = set(right.get("towns") or ())
            shared_towns = left_towns & right_towns

            if shared_vehicle or shared_driver:
                resource = "车辆" if shared_vehicle else "司机"
                resource_value = left.get("vehicle") if shared_vehicle else left.get("driver")
                if overlap is False:
                    continue
                conflicts.append(
                    RouteConflict(
                        kind="resource_double_book",
                        severity="blocking" if overlap else "review",
                        route_ids=(str(left.get("route_id")), str(right.get("route_id"))),
                        towns=tuple(sorted(left_towns | right_towns)),
                        detail=(
                            f"{resource} {resource_value} 被两条路线同时占用"
                            + ("，且时段重叠。" if overlap else "；资料不足以核实时段，需人工确认。")
                        ),
                        resolution="保留一条并记录放弃另一条的服务缺口，或改派其他车辆/司机。",
                        unverified=overlap is None,
                    )
                )
            elif shared_towns and overlap is not False:
                conflicts.append(
                    RouteConflict(
                        kind="town_double_book",
                        severity="blocking" if overlap else "review",
                        route_ids=(str(left.get("route_id")), str(right.get("route_id"))),
                        towns=tuple(sorted(shared_towns)),
                        detail=(
                            f"乡镇 {sorted(shared_towns)} 在同一路线周期内被两条路线同时安排"
                            + ("，时段重叠。" if overlap else "；时段未核实。")
                        ),
                        resolution="合并停靠或明确先后顺序；若只能保留一条，显示另一乡镇的服务缺口。",
                        unverified=overlap is None,
                    )
                )
    return conflicts


def _program_window(program: Mapping[str, Any]) -> TimeWindow:
    start = _dt(program.get("starts_at"))
    end = _dt(program.get("ends_at"))
    if start and not end:
        duration = _minutes(program.get("duration_minutes")) or 60
        end = _add_minutes(start, duration)
    return TimeWindow(start, end, bool(start and end), "program")


def _route_program_conflicts(
    routes: Iterable[Mapping[str, Any]],
    programs: Iterable[Mapping[str, Any]],
) -> list[RouteConflict]:
    conflicts: list[RouteConflict] = []
    for route in routes:
        if str(route.get("status") or "").strip().lower() in INACTIVE_STATUSES:
            continue
        for town in route.get("towns") or ():
            window = stop_window(route, str(town))
            for program in programs:
                if str(program.get("town")) != str(town):
                    continue
                if str(program.get("status") or "").strip().lower() in {"cancelled", "declined"}:
                    continue
                overlap = window.overlaps(_program_window(program))
                if overlap is False:
                    continue
                if overlap is None and not (
                    _dt(program.get("starts_at")) and window.start
                ):
                    continue
                conflicts.append(
                    RouteConflict(
                        kind="route_program_conflict",
                        severity="review",
                        route_ids=(str(route.get("route_id")),),
                        towns=(str(town),),
                        detail=(
                            f"路线 {route.get('route_id')} 停靠 {town} 与活动 "
                            f"{program.get('program_id')} 的时间重叠或无法排除重叠。"
                        ),
                        resolution=(
                            "活动为已确认/已公布时优先保留活动；调整未确认路线，并记录调整依据。"
                            if str(program.get("status") or "").lower() in COMMITTED_STATUSES
                            else "两条都未最终确认，可按证据重排；保留变更记录。"
                        ),
                        unverified=overlap is None,
                    )
                )
    return conflicts


def _school_alignment(route: Mapping[str, Any], town: str) -> tuple[str | None, str | None]:
    """对比路线停靠时段与学校合作时段；资料不足时返回未核实。"""
    windows = route.get("school_windows")
    if not windows:
        return None, None
    window = stop_window(route, town)
    for entry in windows:
        if not isinstance(entry, Mapping):
            continue
        entry_town = entry.get("town")
        if entry_town not in (None, "", town) and str(entry_town) != town:
            continue
        start = _dt(entry.get("opens_at") or entry.get("starts_at"))
        end = _dt(entry.get("closes_at") or entry.get("ends_at"))
        window_text = (
            f"{entry.get('opens_at') or entry.get('starts_at')}–"
            f"{entry.get('closes_at') or entry.get('ends_at')}"
        )
        candidate = TimeWindow(start, end, bool(start and end), "school")
        overlap = window.overlaps(candidate)
        if overlap:
            return window_text, "within_window"
        if overlap is None:
            return window_text, "unverified"
    return None, "outside_window"


def _route_equipment(route: Mapping[str, Any]) -> set[str]:
    equipment: set[str] = set()
    for key in ("equipment", "access_equipment", "accessibility_equipment"):
        value = route.get(key)
        if isinstance(value, str):
            equipment.update(part.strip() for part in value.split(",") if part.strip())
        elif isinstance(value, (list, tuple, set)):
            equipment.update(str(part) for part in value)
    return equipment


def analyze_routes(
    bundle: Mapping[str, Any],
    *,
    as_of: str | None = None,
    town_demand: Mapping[str, CountBand] | None = None,
    town_access: Mapping[str, bool] | None = None,
) -> RoutePlan:
    """给出每条路线的增/换/保留建议，并列出冲突、承诺与资料缺口。"""
    routes = [route for route in bundle.get("routes.json", ()) or () if isinstance(route, Mapping)]
    programs = [program for program in bundle.get("programs.json", ()) or () if isinstance(program, Mapping)]
    branches = [branch for branch in bundle.get("branches.json", ()) or () if isinstance(branch, Mapping)]
    town_demand = town_demand or {}
    town_access = town_access or {}

    plan = RoutePlan(as_of=as_of)

    for route in routes:
        status = str(route.get("status") or "unknown").strip().lower()
        if status in INACTIVE_STATUSES:
            continue
        route_id = str(route.get("route_id"))
        capacity = _route_capacity(route)
        accessible = _route_accessible(route)
        towns = [str(town) for town in route.get("towns") or ()]
        if not towns:
            plan.gaps.append(
                EvidenceGap(
                    kind="route_without_towns",
                    detail=f"路线 {route_id} 没有可识别的乡镇停靠点，无法评估服务范围。",
                    record_id=route_id,
                )
            )
            continue
        for town in towns:
            demand = town_demand.get(town)
            access_required = town_access.get(town)
            reasons: list[str] = []
            action = "keep"
            protected = status in COMMITTED_STATUSES

            if demand is not None and capacity is not None and demand.low > capacity:
                action = "replace" if status in PENDING_STATUSES else "keep"
                reasons.append(
                    f"已知需求下界 {demand.low} 超过车辆/书车容量 {capacity}；需要加车或拆分停靠。"
                )
            if access_required and accessible is False:
                action = "replace" if status in PENDING_STATUSES else "keep"
                reasons.append("该乡镇需要无障碍服务，但路线未标明可用无障碍设备。")
            school_window, alignment = _school_alignment(route, town)
            if alignment == "outside_window":
                if status in PENDING_STATUSES:
                    action = "replace"
                reasons.append("停靠时段落在学校合作时段之外，学生读者难以使用。")
            elif alignment == "unverified":
                reasons.append("停靠时段或学校合作时段不完整，无法确认是否对齐。")

            if status in PENDING_STATUSES:
                if action == "keep":
                    # 提议路线尚未运行，默认建议是"增加"；只有明确标记为现有
                    # 待批调整时才保留。
                    action = "keep" if route.get("existing") else "add"
                if action == "add":
                    reasons.append(
                        "尚未发现容量、无障碍或时段冲突，可作为新增路线；新资料到达后可重评。"
                    )
            else:
                reasons.append("已公布/已完成路线保留当时依据，不因新排名静默改动。")

            plan.recommendations.append(
                RouteRecommendation(
                    route_id=route_id,
                    town=town,
                    action=action,
                    status=status,
                    reasons=reasons,
                    protected=protected,
                    demand_band=demand,
                    capacity=capacity,
                    access_requirement="required" if access_required else None,
                    access_satisfied=None if accessible is None else (accessible or not access_required),
                    driver=str(route.get("driver")) if route.get("driver") else None,
                    vehicle=str(route.get("vehicle")) if route.get("vehicle") else None,
                    school_window=school_window,
                    school_alignment=alignment,
                )
            )

    conflicts = _overlap_routes(routes)
    conflicts.extend(_route_program_conflicts(routes, programs))

    # 推荐必须与冲突一致：未确认路线若卷入冲突，就不能再说"尚未发现冲突"。
    _apply_conflicts_to_recommendations(plan.recommendations, conflicts)

    # 为每个资源冲突补上"放弃其中一条"造成的服务缺口。
    for conflict in conflicts:
        if conflict.kind != "resource_double_book":
            continue
        first, second = conflict.route_ids
        affected = sorted(set(conflict.towns))
        gaps = []
        for route_id in (first, second):
            route = next((r for r in routes if str(r.get("route_id")) == route_id), None)
            if route is None:
                continue
            for town in route.get("towns") or ():
                gap = service_gap_if_dropped(
                    str(town),
                    route,
                    bundle=bundle,
                    alternative_routes=[
                        other
                        for other in routes
                        if str(other.get("route_id")) not in {first, second}
                        and str(town) in (other.get("towns") or ())
                    ],
                    town_demand=town_demand,
                    town_access=town_access,
                )
                gaps.append(gap.as_dict())
        conflict.service_gap = {"dropped_route_gaps": gaps} if gaps else None

    plan.conflicts = conflicts
    plan.commitments = list(_collect_commitments(bundle))
    plan.notes.append("未确认路线可在新借阅或设施记录到达后重评；已公布活动日期受保护。")
    return plan


def service_gap_if_dropped(
    town: str,
    route: Mapping[str, Any],
    *,
    bundle: Mapping[str, Any],
    alternative_routes: Iterable[Mapping[str, Any]] = (),
    town_demand: Mapping[str, CountBand] | None = None,
    town_access: Mapping[str, bool] | None = None,
) -> ServiceGap:
    """如果放弃该路线，对应乡镇会失去哪些服务机会。"""
    town_demand = town_demand or {}
    town_access = town_access or {}
    programs = [
        program
        for program in bundle.get("programs.json", ()) or ()
        if str(program.get("town")) == town
        and str(program.get("status") or "").lower() not in {"cancelled", "declined"}
    ]
    committed_programs = [
        str(program.get("program_id"))
        for program in programs
        if str(program.get("status") or "").lower() in COMMITTED_STATUSES
    ]
    visits = [
        visit
        for visit in bundle.get("visits.json", ()) or ()
        if str(visit.get("town")) == town
        and normalize_mode(visit.get("mode")) == "mobile_bus"
    ]
    alternatives = [str(other.get("route_id")) for other in alternative_routes]
    demand = town_demand.get(town)
    if demand is None:
        known = sum(
            int(visit.get("reader_count"))
            for visit in visits
            if isinstance(visit.get("reader_count"), int) and not isinstance(visit.get("reader_count"), bool)
        )
        missing = any(not isinstance(visit.get("reader_count"), int) for visit in visits)
        if not visits or missing:
            # 没有记录或人数缺失时不能把需求当成 0；只能给下界。
            demand = CountBand.at_least(known, "按历史流动服务记录估计；缺失部分使上界未知")
        else:
            demand = CountBand.exact(known, "按历史流动服务记录估计")

    branches = [
        branch
        for branch in bundle.get("branches.json", ()) or ()
        if str(branch.get("town")) == town and branch_active_on(branch)
    ]
    accessible_branch = any(branch.get("accessible") is True for branch in branches)
    access_loss = bool(town_access.get(town)) and not accessible_branch and not alternatives
    return ServiceGap(
        town=town,
        dropped_route_id=str(route.get("route_id")),
        reader_band=demand,
        lost_visits=_lost_stop_count(route, town),
        lost_programs=committed_programs,
        access_loss=access_loss,
        alternatives=alternatives,
        detail=(
            f"放弃路线 {route.get('route_id')} 后，{town} 将失去本周期流动停靠"
            + (f"，且已确认活动 {', '.join(committed_programs)} 需另找到场方式。" if committed_programs else "。")
            + (" 该乡镇没有其他可用的无障碍服务。" if access_loss else "")
        ),
    )


def _collect_commitments(bundle: Mapping[str, Any]) -> Iterable[Commitment]:
    branches = [branch for branch in bundle.get("branches.json", ()) or () if isinstance(branch, Mapping)]
    branch_town = {str(branch.get("branch_id")): str(branch.get("town")) for branch in branches}
    for program in bundle.get("programs.json", ()) or ():
        status = str(program.get("status") or "").strip().lower()
        if status not in COMMITTED_STATUSES:
            continue
        yield Commitment(
            kind="program_date",
            record_id=str(program.get("program_id")),
            town=str(program.get("town")),
            when=program.get("starts_at"),
            status=status,
            evidence=dict(program),
        )
    for route in bundle.get("routes.json", ()) or ():
        status = str(route.get("status") or "").strip().lower()
        if status not in COMMITTED_STATUSES:
            continue
        for town in route.get("towns") or ():
            yield Commitment(
                kind="route_stop",
                record_id=str(route.get("route_id")),
                town=str(town),
                when=route.get("starts_at"),
                status=status,
                evidence=dict(route),
            )
    for visit in bundle.get("visits.json", ()) or ():
        if str(visit.get("quality") or "").lower() in {"cancelled", "no_show"}:
            continue
        if visit.get("status") not in COMMITTED_STATUSES:
            continue
        yield Commitment(
            kind="visit_date",
            record_id=str(visit.get("visit_id")),
            town=str(visit.get("town")),
            when=visit.get("observed_at"),
            status=str(visit.get("status")),
            evidence=dict(visit),
        )


def commitment_moves(
    before: Iterable[Mapping[str, Any]],
    after: Iterable[Mapping[str, Any]],
) -> list[dict[str, Any]]:
    """比较两次资料快照，列出被静默挪动或删除的已公布承诺。"""
    def index(records: Iterable[Mapping[str, Any]], key: str) -> dict[str, Mapping[str, Any]]:
        return {
            str(record.get(key)): record
            for record in records
            if isinstance(record, Mapping)
            and str(record.get("status") or "").lower() in COMMITTED_STATUSES
        }

    changes: list[dict[str, Any]] = []
    for kind, key, fields in (
        ("program_date", "program_id", ("town", "starts_at")),
        ("route_stop", "route_id", ("towns", "starts_at")),
    ):
        source = "programs.json" if kind == "program_date" else "routes.json"
        old = index(before.get(source, ()) or (), key)
        new = index(after.get(source, ()) or (), key)
        for record_id, record in old.items():
            replacement = new.get(record_id)
            if replacement is None:
                changes.append(
                    {
                        "kind": kind,
                        "record_id": record_id,
                        "change": "withdrawn",
                        "detail": "已公布承诺在后续资料中消失，必须显式撤销而不是静默替换。",
                    }
                )
                continue
            for field_name in fields:
                if record.get(field_name) != replacement.get(field_name):
                    changes.append(
                        {
                            "kind": kind,
                            "record_id": record_id,
                            "change": "moved",
                            "field": field_name,
                            "before": record.get(field_name),
                            "after": replacement.get(field_name),
                            "detail": "已公布到馆日期发生变化；需要保留原依据并公开说明。",
                        }
                    )
    return changes
