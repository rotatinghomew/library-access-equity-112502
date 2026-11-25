"""乡镇层面的证据汇总与报告。

报告把"观察到的服务量"和"可获得的机会范围"分开呈现，并用
:class:`CountBand` 表示人数下界/未知上界，避免把代借、家庭共用账号、
线上打开页面或缺失记录直接压成单一成绩排名。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from typing import Any, Iterable, Mapping

from .input_contract import dataset_presence as detect_dataset_presence
from .extras import (
    AuthorizationIndex,
    FacilityIndex,
    build_authorization_index,
    build_facility_index,
    facility_sets,
)
from .models import (
    CountBand,
    EvidenceGap,
    TownEvidence,
)
from .planning import RoutePlan, analyze_routes
from .summaries import (
    branch_active_on,
    build_access_summary,
    build_coverage,
    build_resource_context,
    normalize_mode,
    parse_date,
    summarize_loans,
    summarize_programs,
    summarize_visits,
)


@dataclass
class TownOpportunity:
    """一个乡镇可获得的机会范围，用于解释覆盖率而不只是成绩。

    ``available_modes`` 只含报告时点已确认可用的服务；``planned_modes`` 是
    已提议或待批、尚不能计入成绩的机会。两者分开，避免把"还没安排"说成
    "服务缺口"，也避免把提议当作已兑现。
    """

    town: str
    available_modes: tuple[str, ...]
    planned_modes: tuple[str, ...]
    branches: tuple[str, ...]
    accessible: bool | None
    mobile_route_ids: tuple[str, ...]
    program_ids: tuple[str, ...]
    notes: tuple[str, ...] = ()

    def as_dict(self) -> dict[str, Any]:
        return {
            "town": self.town,
            "available_modes": list(self.available_modes),
            "planned_modes": list(self.planned_modes),
            "branches": list(self.branches),
            "accessible": self.accessible,
            "mobile_route_ids": list(self.mobile_route_ids),
            "program_ids": list(self.program_ids),
            "notes": list(self.notes),
        }


@dataclass
class EquityReport:
    as_of: str
    towns: dict[str, TownEvidence] = field(default_factory=dict)
    opportunities: dict[str, TownOpportunity] = field(default_factory=dict)
    route_plan: RoutePlan | None = None
    dataset_presence: dict[str, bool] = field(default_factory=dict)
    data_gaps: list[EvidenceGap] = field(default_factory=list)
    comparison_limits: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        return {
            "as_of": self.as_of,
            "towns": {town: evidence.as_dict() for town, evidence in sorted(self.towns.items())},
            "opportunities": {
                town: opportunity.as_dict() for town, opportunity in sorted(self.opportunities.items())
            },
            "route_plan": self.route_plan.as_dict() if self.route_plan else None,
            "dataset_presence": dict(self.dataset_presence),
            "data_gaps": [gap.as_dict() for gap in self.data_gaps],
            "comparison_limits": list(self.comparison_limits),
            "notes": list(self.notes),
        }

    def town(self, town: str) -> TownEvidence:
        return self.towns[town]

    def render_text(self) -> str:
        return render_text(self)


def _town_list(bundle: Mapping[str, Any]) -> list[str]:
    towns: set[str] = set()
    for branch in bundle.get("branches.json", ()) or ():
        if branch.get("town"):
            towns.add(str(branch["town"]))
    for name in ("visits.json", "programs.json"):
        for record in bundle.get(name, ()) or ():
            if record.get("town"):
                towns.add(str(record["town"]))
    for route in bundle.get("routes.json", ()) or ():
        for town in route.get("towns") or ():
            towns.add(str(town))
    for loan in bundle.get("loans.json", ()) or ():
        if loan.get("town"):
            towns.add(str(loan["town"]))
    return sorted(towns)


def _branches_by_id(bundle: Mapping[str, Any]) -> dict[str, Mapping[str, Any]]:
    return {
        str(branch.get("branch_id")): branch
        for branch in bundle.get("branches.json", ()) or ()
        if branch.get("branch_id") is not None
    }


def access_branches(
    branches: Iterable[Mapping[str, Any]], as_of: date | None
) -> list[str]:
    return [
        str(branch.get("branch_id"))
        for branch in branches
        if branch.get("branch_id") is not None and branch_active_on(branch, as_of)
    ]


def _town_for_loan(loan: Mapping[str, Any], branches: Mapping[str, Mapping[str, Any]], town: str) -> bool:
    if loan.get("town") is not None:
        return str(loan.get("town")) == town
    branch = branches.get(str(loan.get("branch_id")))
    return bool(branch and str(branch.get("town")) == town)


def _loans_for_town(
    bundle: Mapping[str, Any],
    branches: Mapping[str, Mapping[str, Any]],
    town: str,
    as_of: date | None,
) -> list[Mapping[str, Any]]:
    result = []
    for loan in bundle.get("loans.json", ()) or ():
        if not _town_for_loan(loan, branches, town):
            continue
        event_date = parse_date(loan.get("loaned_at"))
        if as_of is not None and event_date is not None and event_date > as_of:
            continue
        result.append(loan)
    return result


def _visits_for_town(bundle: Mapping[str, Any], town: str, as_of: date | None) -> list[Mapping[str, Any]]:
    result = []
    for visit in bundle.get("visits.json", ()) or ():
        if str(visit.get("town")) != town:
            continue
        event_date = parse_date(visit.get("observed_at"))
        if as_of is not None and event_date is not None and event_date > as_of:
            continue
        result.append(visit)
    return result


def _programs_for_town(bundle: Mapping[str, Any], town: str, as_of: date | None) -> list[Mapping[str, Any]]:
    result = []
    for program in bundle.get("programs.json", ()) or ():
        if str(program.get("town")) != town:
            continue
        event_date = parse_date(program.get("starts_at"))
        if as_of is not None and event_date is not None and event_date > as_of:
            continue
        result.append(program)
    return result


_COMMITTED_ROUTE_STATUSES = {"confirmed", "published", "completed", "active", "running"}
_PENDING_ROUTE_STATUSES = {"proposed", "planned", "draft", "pending"}
_COMMITTED_PROGRAM_STATUSES = {"confirmed", "published", "completed"}


def _available_modes(
    town: str,
    branch_ids: Iterable[str],
    visits: Iterable[Mapping[str, Any]],
    programs: Iterable[Mapping[str, Any]],
    routes: Iterable[Mapping[str, Any]],
) -> tuple[tuple[str, ...], tuple[str, ...]]:
    """按当时的可用资源推导 (已可用方式, 仍待确认的方式)。

    只有已确认/已完成的路线或活动才算"本应有记录"；提议中的安排进入
    ``planned``，不能用来断定一个乡镇缺服务，也不能算作已兑现服务。
    """
    available: set[str] = set()
    planned: set[str] = set()
    if list(branch_ids):
        available.add("branch")
    if any(
        normalize_mode(visit.get("mode")) == "mobile_bus"
        or normalize_mode(visit.get("mode")) == "home_delivery"
        for visit in visits
    ):
        available.add("mobile_bus")
    for route in routes:
        status = str(route.get("status") or "").lower()
        if status in _COMMITTED_ROUTE_STATUSES:
            available.add("mobile_bus")
        elif status in _PENDING_ROUTE_STATUSES:
            planned.add("mobile_bus")
    for program in programs:
        status = str(program.get("status") or "").lower()
        if status in _COMMITTED_PROGRAM_STATUSES:
            available.add("program")
        elif status in {"proposed", "planned", "draft", "pending"}:
            planned.add("program")
    if any(normalize_mode(visit.get("mode")) == "online" for visit in visits):
        available.add("online")
    return tuple(sorted(available)), tuple(sorted(planned - available))


def build_opportunities(
    bundle: Mapping[str, Any],
    towns: Iterable[str],
    as_of: date | None,
) -> dict[str, TownOpportunity]:
    branches = [branch for branch in bundle.get("branches.json", ()) or () if isinstance(branch, Mapping)]
    routes = [route for route in bundle.get("routes.json", ()) or () if isinstance(route, Mapping)]
    result: dict[str, TownOpportunity] = {}
    for town in towns:
        active = [branch for branch in branches if str(branch.get("town")) == town and branch_active_on(branch, as_of)]
        branch_ids = tuple(str(branch.get("branch_id")) for branch in active)
        visits = _visits_for_town(bundle, town, as_of)
        programs = _programs_for_town(bundle, town, as_of)
        active_routes = [
            route
            for route in routes
            if town in (route.get("towns") or ())
            and str(route.get("status") or "").lower() not in {"cancelled", "withdrawn", "archived"}
        ]
        notes: list[str] = []
        if any(not branch_active_on(branch, as_of) for branch in branches if str(branch.get("town")) == town):
            notes.append("该乡镇有分馆在报告时点尚未启用或已停用；历史报告按当时资源解释。")
        if not active:
            notes.append("报告时点没有启用中的分馆，服务只能通过流动或线上方式覆盖。")
        available_modes, planned_modes = _available_modes(
            town, branch_ids, visits, programs, active_routes
        )
        if planned_modes:
            notes.append(
                "以下服务方式仅为提议或待批，尚不能计入已提供机会："
                + ", ".join(planned_modes)
            )
        result[town] = TownOpportunity(
            town=town,
            available_modes=available_modes,
            planned_modes=planned_modes,
            branches=branch_ids,
            accessible=(
                True
                if any(branch.get("accessible") is True for branch in active)
                else (False if active else None)
            ),
            mobile_route_ids=tuple(
                str(route.get("route_id"))
                for route in active_routes
                if str(route.get("status") or "").lower() in {"confirmed", "published", "completed", "proposed", "planned"}
            ),
            program_ids=tuple(
                str(program.get("program_id"))
                for program in programs
                if str(program.get("status") or "").lower() not in {"cancelled", "declined"}
            ),
            notes=tuple(notes),
        )
    return result


def build_report(
    bundle: Mapping[str, Any],
    *,
    as_of: str | date | None = None,
    dataset_presence: Mapping[str, bool] | None = None,
    authorizations: AuthorizationIndex | None = None,
    facilities: FacilityIndex | None = None,
) -> EquityReport:
    """生成可回看的乡镇服务证据报告。``as_of`` 之前的记录才计入当时成绩。"""
    parsed_as_of = as_of if isinstance(as_of, date) else parse_date(as_of)
    as_of_text = parsed_as_of.isoformat() if parsed_as_of else (str(as_of) if as_of else "")
    branches = [branch for branch in bundle.get("branches.json", ()) or () if isinstance(branch, Mapping)]
    branches_by_id = _branches_by_id(bundle)
    if authorizations is None:
        authorizations = build_authorization_index(bundle, parsed_as_of)
    if facilities is None:
        facilities = build_facility_index(bundle, branches, parsed_as_of)
    routes = [route for route in bundle.get("routes.json", ()) or () if isinstance(route, Mapping)]
    towns = _town_list(bundle)

    report = EquityReport(
        as_of=as_of_text,
        dataset_presence=dict(
            dataset_presence if dataset_presence is not None else detect_dataset_presence(bundle)
        ),
    )
    report.opportunities = build_opportunities(bundle, towns, parsed_as_of)

    for town in towns:
        visits = _visits_for_town(bundle, town, parsed_as_of)
        programs = _programs_for_town(bundle, town, parsed_as_of)
        loans = _loans_for_town(bundle, branches_by_id, town, parsed_as_of)
        town_branches = [branch for branch in branches if str(branch.get("town")) == town]

        access = build_access_summary(town, town_branches, visits, as_of=parsed_as_of)
        if facilities.present:
            # 设施资料到达后细化无障碍机会；缺失仍保留原判断，不凭空补设施。
            for branch_id in access_branches(town_branches, parsed_as_of):
                available, unavailable = facility_sets(facilities, town, branch_id)
                access.accessible_facilities = sorted(
                    set(access.accessible_facilities) | set(available)
                )
                access.unavailable_facilities = sorted(
                    set(access.unavailable_facilities) | set(unavailable)
                )
            if access.accessible_facilities:
                access.access_gap = False
            if access.unavailable_facilities:
                access.notes.append(
                    "设施资料显示部分设备不可用："
                    + ", ".join(access.unavailable_facilities)
                )
                if access.branch_accessible is False or not access.mobile_or_outreach:
                    access.access_gap = True
        program_summary = summarize_programs(programs, access=access, as_of=parsed_as_of)
        loan_summary = summarize_loans(
            loans,
            branches=branches_by_id,
            as_of=parsed_as_of,
            household_members=authorizations.household_members,
        )
        visit_summary = summarize_visits(visits, as_of=parsed_as_of)

        opportunity = report.opportunities[town]
        coverage = build_coverage(
            town,
            opportunity.available_modes,
            visits,
            loans,
            programs,
            as_of=parsed_as_of,
            available_modes=opportunity.available_modes,
            planned_modes=opportunity.planned_modes,
        )
        # 借阅也可以来自镇外分馆或流动服务，因此单独检查"借阅证据"。
        context = build_resource_context(town, town_branches, programs, routes, as_of=parsed_as_of)

        evidence = TownEvidence(
            town=town,
            loans=loan_summary,
            visits=visit_summary,
            programs=program_summary,
            access=access,
            coverage=coverage,
            resource_context=context,
        )
        evidence.evidence_gaps.extend(_town_gaps(town, evidence, opportunity))
        evidence.notes.extend(access.notes)
        evidence.notes.extend(opportunity.notes)
        if not facilities.present and (program_summary.access_unmet_program_ids or access.access_gap):
            evidence.evidence_gaps.append(
                EvidenceGap(
                    kind="facilities_data_missing",
                    detail="设施资料尚未到达；无障碍缺口只能按分馆标记和活动字段判断，需后续核实。",
                    town=town,
                    mode="access",
                )
            )
            report.data_gaps.append(evidence.evidence_gaps[-1])
        report.towns[town] = evidence
        report.data_gaps.extend(evidence.evidence_gaps)

    report.route_plan = analyze_routes(
        bundle,
        as_of=as_of_text or None,
        town_demand={
            town: _demand_band(evidence) for town, evidence in report.towns.items()
        },
        town_access={town: not evidence.access.access_gap for town, evidence in report.towns.items()},
    )
    report.comparison_limits = _comparison_limits(report)
    report.notes.append("报告按 as_of 截断事件流；as_of 之后的记录不回溯计入当时成绩。")
    report.notes.append("没有身份关联时不推断独立读者人数；读到下界和未解决上界即说明范围。")
    return report


def _demand_band(evidence: TownEvidence) -> CountBand:
    """乡镇需求下界：可识别读者下界与物理到馆人数取较大者，上界保持未知。"""
    band = evidence.loans.reader_band
    physical = evidence.visits.physical_attendance
    low = max(band.low, physical)
    if band.is_unknown or evidence.visits.physical_unknown_records:
        return CountBand(low, None, "由借阅与到馆下界合并；存在缺失或代借，上界未知")
    return CountBand(low, max(band.high or low, physical), "由借阅与到馆记录合并")


def _town_gaps(town: str, evidence: TownEvidence, opportunity: TownOpportunity) -> list[EvidenceGap]:
    gaps: list[EvidenceGap] = []
    for status in evidence.coverage.values():
        if not status.is_gap:
            continue
        gaps.append(
            EvidenceGap(
                kind=status.status,
                detail=f"[{status.mode}] {status.note}",
                town=town,
                mode=status.mode,
                record_id=None,
            )
        )
    if evidence.loans.interactions == 0 and "branch" in opportunity.available_modes:
        gaps.append(
            EvidenceGap(
                kind="no_loan_records",
                detail="有启用中的分馆却没有任何借阅记录；可能是资料缺口，也可能是代借未记名。",
                town=town,
                mode="branch",
            )
        )
    if evidence.programs.attendance_unknown_records:
        gaps.append(
            EvidenceGap(
                kind="program_attendance_missing",
                detail=f"{evidence.programs.attendance_unknown_records} 场活动缺少实际参加人数，报名不等于出席。",
                town=town,
                mode="program",
            )
        )
    if evidence.programs.access_unmet_program_ids:
        gaps.append(
            EvidenceGap(
                kind="program_access_unmet",
                detail=(
                    "以下活动缺少无障碍支持，且该乡镇无可用无障碍馆点或流动服务："
                    + ", ".join(evidence.programs.access_unmet_program_ids)
                ),
                town=town,
                mode="program",
            )
        )
    if evidence.programs.missing_cancel_reasons:
        gaps.append(
            EvidenceGap(
                kind="cancel_reason_missing",
                detail=f"{evidence.programs.missing_cancel_reasons} 场取消活动缺少原因，无法判断服务缺口能否补回。",
                town=town,
                mode="program",
            )
        )
    if evidence.loans.resource_unavailable_at_event:
        gaps.append(
            EvidenceGap(
                kind="resource_unavailable_at_event",
                detail=f"{evidence.loans.resource_unavailable_at_event} 笔借阅发生在分馆启用之前，历史报告需注明。",
                town=town,
            )
        )
    return gaps


def _comparison_limits(report: EquityReport) -> list[str]:
    limits: list[str] = []
    if any(evidence.visits.digital_opens for evidence in report.towns.values()):
        limits.append("线上访问只表示打开过页面，不能与到馆人数或完成阅读直接比较。")
    if any(evidence.loans.unlinked_interactions for evidence in report.towns.values()):
        limits.append("存在未关联身份的借阅，读者人数只能给出下界。")
    if any(evidence.loans.by_kind.get("proxy") for evidence in report.towns.values()):
        limits.append("家庭代借已计入服务接触，但不等同于多个独立读者。")
    if any(evidence.coverage_gaps for evidence in report.towns.values()):
        limits.append("部分乡镇×服务方式缺记录，跨乡镇比较应同时看覆盖状态。")
    if any(
        presence in {"facilities.json", "authorizations.json", "vehicles.json"} and not exists
        for presence, exists in report.dataset_presence.items()
    ):
        limits.append("设施、授权或车辆资料尚未到达；相关机会范围只能标注为未核实。")
    return limits


def render_text(report: EquityReport) -> str:
    lines: list[str] = []
    lines.append(f"图书馆服务证据报告（截至 {report.as_of or '资料最新日'}）")
    if report.dataset_presence:
        present = [name for name, exists in sorted(report.dataset_presence.items()) if exists]
        missing = [name for name, exists in sorted(report.dataset_presence.items()) if not exists]
        if present:
            lines.append(f"已收到资料：{', '.join(present)}")
        if missing:
            lines.append(f"未收到资料：{', '.join(missing)}（不代表没有服务）")
    lines.append("")
    for town, evidence in sorted(report.towns.items()):
        opportunity = report.opportunities[town]
        lines.append(f"## {town}")
        lines.append(
            f"- 可获得机会：{', '.join(opportunity.available_modes) or '无'}；"
            f"待确认：{', '.join(opportunity.planned_modes) or '无'}；"
            f"启用分馆：{', '.join(opportunity.branches) or '无'}；"
            f"无障碍：{'是' if opportunity.accessible else ('否' if opportunity.accessible is False else '未核实')}"
        )
        loans = evidence.loans
        lines.append(
            f"- 借阅：交互 {loans.interactions} 次（续借 {loans.renewals} 次，跨馆归还 "
            f"{loans.cross_branch_returns} 次）；读者下界 {loans.reader_band}"
        )
        visits = evidence.visits
        lines.append(
            f"- 到馆/线上：物理到馆 {visits.physical_attendance}"
            + (f"（另有 {visits.physical_unknown_records} 条人数缺失）" if visits.physical_unknown_records else "")
            + f"；线上打开 {visits.digital_opens} 次，完成阅读 {visits.digital_reading_completions} 次"
        )
        programs = evidence.programs
        lines.append(
            f"- 活动：已确认 {programs.confirmed}、已公布 {programs.published}、已完成 {programs.completed}、"
            f"提议 {programs.proposed}、取消 {programs.cancelled}；"
            f"报名 {programs.registered_known}、实际参加 {programs.attended_known}"
        )
        if evidence.coverage:
            parts = [
                f"{status.mode}:{status.status}"
                for status in sorted(evidence.coverage.values(), key=lambda item: item.mode)
            ]
            lines.append(f"- 证据覆盖：{'；'.join(parts)}")
        if programs.commitments:
            lines.append(
                "- 已承诺事项："
                + "；".join(
                    f"{item['program_id']}@{item['starts_at']}({item['status']})"
                    for item in programs.commitments
                )
            )
        for gap in evidence.evidence_gaps:
            lines.append(f"- 资料缺口[{gap.kind}]：{gap.detail}")
        lines.append("")

    if report.route_plan:
        plan = report.route_plan
        lines.append("## 流动资源建议")
        for rec in plan.recommendations:
            flag = "（受保护）" if rec.protected else ""
            reasons = "；".join(rec.reasons) or "无"
            lines.append(
                f"- {rec.route_id}/{rec.town}：{rec.action}{flag} — {reasons}"
            )
        for conflict in plan.conflicts:
            lines.append(f"- 冲突[{conflict.kind}]：{conflict.detail} 处理：{conflict.resolution}")
            if conflict.service_gap:
                for gap in conflict.service_gap.get("dropped_route_gaps", []):
                    lines.append(f"  - 若放弃 {gap['dropped_route_id']}：{gap['detail']}")
        lines.append("")

    if report.comparison_limits:
        lines.append("## 比较限制")
        for limit in report.comparison_limits:
            lines.append(f"- {limit}")
    return "\n".join(lines).rstrip() + "\n"
