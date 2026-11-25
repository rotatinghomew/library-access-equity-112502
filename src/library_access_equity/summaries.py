"""从原始记录生成乡镇层面的服务证据。

原则：

* 原始记录不改写；这里只做解释层聚合。
* 缺失人数不填 0，而是进入 :class:`CountBand` 的未知上界。
* 续借是同一笔借阅的延续，单独计量，不重复计服务单元。
* 线上打开页面只记为访问，不当作完成阅读。
* 到馆日期、已确认活动和已完成服务都保留原始状态与当时资源背景。
"""

from __future__ import annotations

from datetime import date
from typing import Any, Iterable, Mapping

from .models import (
    AccessSummary,
    CountBand,
    CoverageStatus,
    LoanSummary,
    ModeVisitSummary,
    ProgramSummary,
    ResourceContext,
    VisitSummary,
)

PHYSICAL_MODES = {"branch", "mobile_bus", "program", "home_delivery"}
DIGITAL_MODES = {"online"}
SUPPORTED_MODES = ("branch", "mobile_bus", "proxy", "online", "program")

_MODE_ALIASES = {
    "branch": "branch",
    "in_person": "branch",
    "walk_in": "branch",
    "library": "branch",
    "on_site": "branch",
    "mobile": "mobile_bus",
    "mobile_bus": "mobile_bus",
    "bookmobile": "mobile_bus",
    "bus": "mobile_bus",
    "outreach": "mobile_bus",
    "online": "online",
    "digital": "online",
    "web": "online",
    "website": "online",
    "program": "program",
    "event": "program",
    "activity": "program",
    "proxy": "proxy",
    "delegated": "proxy",
    "home_delivery": "home_delivery",
}

_COMMITTED_PROGRAM_STATUSES = {"confirmed", "published", "completed"}


def normalize_mode(mode: Any) -> str:
    text = str(mode or "").strip().lower()
    return _MODE_ALIASES.get(text, text or "unknown")


def parse_date(value: Any) -> date | None:
    """仅接受明确的 ISO 日期/日期时间前缀；未知返回 ``None``，不猜。"""
    if not isinstance(value, str) or len(value) < 10:
        return None
    try:
        return date.fromisoformat(value[:10])
    except ValueError:
        return None


def _number(value: Any) -> int | None:
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        return int(value)
    if isinstance(value, str) and value.strip().lstrip("-").isdigit():
        return int(value)
    return None


def _kind(record: Mapping[str, Any]) -> str:
    return str(record.get("kind") or "unknown").strip().lower()


def _is_true(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return value != 0
    if isinstance(value, str):
        return value.strip().lower() in {"true", "yes", "1", "y", "completed", "complete"}
    return False


def _loan_identity(record: Mapping[str, Any]) -> tuple[str, str | None]:
    """返回 (身份类别, 身份值)，用于家庭/账号去重。"""
    member = record.get("member_key")
    household = record.get("household_key")
    if member:
        return "member", str(member)
    if household:
        return "household", str(household)
    return "unlinked", None


def _renewal_count(record: Mapping[str, Any]) -> int:
    for key in ("renewal_count", "renewals", "renewed_count"):
        value = _number(record.get(key))
        if value is not None:
            return max(0, value)
    return 0


def _returned_branch(record: Mapping[str, Any]) -> str | None:
    for key in ("returned_branch_id", "return_branch_id", "returned_at_branch"):
        value = record.get(key)
        if value:
            return str(value)
    return None


def branch_active_on(branch: Mapping[str, Any], as_of: date | None) -> bool:
    """按事件日期判断分馆是否在服务期内，支持启用、关闭与搬迁停用。"""
    active_from = parse_date(branch.get("active_from"))
    active_to = None
    for key in ("active_to", "closed_at", "inactive_from", "relocated_at"):
        active_to = parse_date(branch.get(key))
        if active_to is not None:
            break
    if as_of is None:
        return True
    if active_from is not None and active_from > as_of:
        return False
    if active_to is not None and as_of >= active_to:
        return False
    return True


def summarize_loans(
    loans: Iterable[Mapping[str, Any]],
    *,
    branches: Mapping[str, Mapping[str, Any]] | None = None,
    as_of: date | None = None,
    household_members: Mapping[str, Iterable[str]] | None = None,
) -> LoanSummary:
    """按借阅交互聚合，保留读者人数的上下界而不是强行给出单一人数。

    ``household_members`` 来自授权关联资料（若已到达）。它只说明家庭里登记了
    哪些人，不能证明某次代借就是这些人本人在使用，因此不抬高下界，也不封闭
    上界；仅在说明里标注该资料已用于核对。
    """
    summary = LoanSummary()
    branches = branches or {}
    household_members = household_members or {}
    authorized_households_checked = 0
    member_keys: set[str] = set()
    households: set[str] = set()
    households_with_known_member: set[str] = set()
    kinds: dict[str, int] = {}
    unresolved = False

    for loan in loans:
        summary.interactions += 1
        summary.service_units += 1

        kind = _kind(loan)
        kinds[kind] = kinds.get(kind, 0) + 1
        summary.by_kind[kind] = kinds[kind]

        renewals = _renewal_count(loan)
        if renewals:
            summary.renewals += renewals

        returned = _returned_branch(loan)
        if returned and loan.get("branch_id") and returned != loan.get("branch_id"):
            summary.cross_branch_returns += 1

        identity_kind, identity_value = _loan_identity(loan)
        member = loan.get("member_key")
        household = loan.get("household_key")
        if identity_kind == "unlinked":
            summary.unlinked_interactions += 1
            unresolved = True
        elif member:
            member_keys.add(str(member))
            if household:
                households.add(str(household))
                households_with_known_member.add(str(household))
        elif household:
            households.add(str(household))
            if household_members.get(str(household)):
                authorized_households_checked += 1
        if household:
            households.add(str(household))

        if kind == "proxy":
            # 代借确认了家庭层面的服务接触；它不代表若干独立读者。
            unresolved = True
        elif kind == "unknown":
            unresolved = True

        branch_id = loan.get("branch_id")
        branch = branches.get(str(branch_id)) if branch_id is not None else None
        event_date = parse_date(loan.get("loaned_at"))
        # 用借阅发生日判断当时是否有该馆藏点；关闭/搬迁日期同样适用。
        if branch is not None and not branch_active_on(branch, event_date):
            summary.resource_unavailable_at_event += 1
            summary.notes.append(
                f"借阅 {loan.get('loan_id')} 发生在 {branch_id} 按资料尚未启用的日期，"
                "历史报告应注明当时无该馆藏点。"
            )

    summary.households = len(households)
    summary.known_members = len(member_keys)

    # 下界：每个已知账号至少一位读者；没有任何已知成员的家庭至少一位读者；
    # 未关联记录合并计 1（彼此可能是同一人）。家庭共用账号、代借和未知类型
    # 都让上界无法封闭，因此保留为 None。
    household_only = households - households_with_known_member
    lower = len(member_keys) + len(household_only) + (1 if summary.unlinked_interactions else 0)
    high = None if unresolved else lower
    if summary.interactions == 0:
        summary.reader_band = CountBand.exact(0, "无借阅记录（不是确认无人阅读）")
    else:
        note = "账号/家庭数仅作读者下界；家庭共用账号或代借可使实际读者更多。"
        if authorized_households_checked:
            note += f" 已用授权资料核对 {authorized_households_checked} 条代借的家庭，但仍不能确定实际使用人。"
        summary.reader_band = CountBand(lower, high, note)
    return summary


def summarize_visits(
    visits: Iterable[Mapping[str, Any]],
    *,
    as_of: date | None = None,
) -> VisitSummary:
    summary = VisitSummary()
    for visit in visits:
        mode = normalize_mode(visit.get("mode"))
        observed = parse_date(visit.get("observed_at"))
        if as_of is not None and observed is not None and observed > as_of:
            # 观察日期晚于报告时点，不能算进当时成绩。
            continue
        mode_summary = summary.by_mode.setdefault(mode, ModeVisitSummary())
        mode_summary.records += 1

        quality = str(visit.get("quality") or "").strip().lower()
        if quality and quality not in {"complete", "full", "ok"}:
            mode_summary.partial_records += 1

        count = _number(visit.get("reader_count"))
        if count is None:
            mode_summary.unknown_volume_records += 1
        else:
            mode_summary.known_attendance += max(0, count)

        if _is_true(visit.get("reading_completed")) or str(
            visit.get("completion") or ""
        ).strip().lower() in {"complete", "completed"}:
            mode_summary.reading_completions += 1

        if mode in DIGITAL_MODES:
            # 线上记录只计"打开/访问"次数；人数未知时也不推断具体人次。
            summary.digital_opens += 1 if count is None else max(0, count)
            if count is None:
                summary.notes.append(
                    f"访问 {visit.get('visit_id')} 为线上记录且人数缺失；打开页面不等于完成阅读。"
                )
        elif mode in PHYSICAL_MODES:
            if count is None:
                summary.physical_unknown_records += 1
            else:
                summary.physical_attendance += count
        else:
            # 服务方式未知时既不并入到馆人数，也不并入线上打开；保留原值待核实。
            mode_summary.unknown_volume_records += 1
            summary.notes.append(
                f"访问 {visit.get('visit_id')} 的服务方式 {visit.get('mode')!r} 无法识别，未计入到馆或线上。"
            )

        if summary.last_observed is None or (
            observed is not None and observed.isoformat() > summary.last_observed
        ):
            summary.last_observed = visit.get("observed_at")

    # 给每个模式一个可解释的说明与人数区间。
    for mode, mode_summary in summary.by_mode.items():
        if mode in DIGITAL_MODES:
            mode_summary.note = "线上访问只证明打开过页面，完成阅读需单独字段；不并入到馆人数。"
            summary.digital_reading_completions += mode_summary.reading_completions
        elif mode_summary.unknown_volume_records:
            mode_summary.note = "部分记录人数缺失，到馆人数只能给下界。"
        elif mode_summary.partial_records:
            mode_summary.note = "记录质量不完整，人数应标注为暂定。"
        else:
            mode_summary.note = "按原始到馆/活动人数汇总。"
    return summary


def _program_access_available(
    program: Mapping[str, Any],
    access: AccessSummary,
    *,
    branch_accessible: bool | None,
) -> bool:
    support = str(program.get("access_support") or "").strip().lower()
    if support and support not in {"none", "unknown", "no", "not_available"}:
        return True
    if branch_accessible:
        return True
    return bool(access.mobile_or_outreach)


def summarize_programs(
    programs: Iterable[Mapping[str, Any]],
    *,
    access: AccessSummary,
    as_of: date | None = None,
) -> ProgramSummary:
    summary = ProgramSummary()
    for program in programs:
        status = str(program.get("status") or "unknown").strip().lower()
        starts = parse_date(program.get("starts_at"))
        capacity = _number(program.get("capacity"))

        if status in {"confirmed", "published", "completed"}:
            # 已公布/已确认的到馆日期是承诺，原样保留，不因后续排名挪动。
            summary.commitments.append(
                {
                    "program_id": program.get("program_id"),
                    "starts_at": program.get("starts_at"),
                    "status": status,
                    "capacity": capacity,
                    "access_support": program.get("access_support"),
                    "preserve": True,
                }
            )
        if status == "confirmed":
            summary.confirmed += 1
        elif status == "published":
            summary.published += 1
        elif status == "completed":
            summary.completed += 1
        elif status == "proposed":
            summary.proposed += 1
        elif status == "cancelled":
            summary.cancelled += 1
            if capacity:
                summary.cancelled_capacity += capacity
            reason = program.get("cancel_reason")
            if not reason:
                summary.missing_cancel_reasons += 1
                summary.notes.append(
                    f"活动 {program.get('program_id')} 已取消但缺少取消原因，无法判断是否可替代。"
                )

        registered = _number(program.get("registered_count", program.get("registrations")))
        attended = _number(program.get("attended_count", program.get("actual_attendance")))
        if registered is None and program.get("registration_count") is not None:
            registered = _number(program.get("registration_count"))
        if registered is None:
            summary.registration_unknown_records += 1
        else:
            summary.registered_known += registered
        if attended is None:
            if status in {"completed", "confirmed", "published"}:
                summary.attendance_unknown_records += 1
        else:
            summary.attended_known += attended

        if capacity is not None:
            if registered is not None and registered > capacity:
                summary.over_capacity_program_ids.append(str(program.get("program_id")))
            if attended is not None and attended > capacity:
                summary.over_capacity_program_ids.append(str(program.get("program_id")))

        if status != "cancelled" and not _program_access_available(
            program, access, branch_accessible=access.branch_accessible
        ):
            summary.access_unmet_program_ids.append(str(program.get("program_id")))
            summary.notes.append(
                f"活动 {program.get('program_id')} 无无障碍支持，且该乡镇无可用无障碍馆点或流动服务。"
            )

        if (
            as_of is not None
            and status in _COMMITTED_PROGRAM_STATUSES
            and starts is not None
            and starts > as_of
        ):
            summary.notes.append(
                f"活动 {program.get('program_id')} 在报告日之后举行，列为已承诺事项而非已兑现服务。"
            )
    summary.over_capacity_program_ids = sorted(set(summary.over_capacity_program_ids))
    return summary


def build_access_summary(
    town: str,
    branches: Iterable[Mapping[str, Any]],
    visits: Iterable[Mapping[str, Any]],
    as_of: date | None = None,
) -> AccessSummary:
    summary = AccessSummary()
    facilities: set[str] = set()
    unavailable: set[str] = set()
    accessible_flags: list[bool] = []
    for branch in branches:
        if str(branch.get("town")) != town:
            continue
        if not branch_active_on(branch, as_of):
            summary.notes.append(
                f"分馆 {branch.get('branch_id')} 在报告时点尚未启用；历史成绩不应按现有资源解释。"
            )
            continue
        if branch.get("accessible") is True:
            accessible_flags.append(True)
        elif branch.get("accessible") is False:
            accessible_flags.append(False)
        for facility in branch.get("facilities") or ():
            facilities.add(str(facility))
        for gap in branch.get("facility_gaps") or ():
            unavailable.add(str(gap))
    if accessible_flags:
        summary.branch_accessible = any(accessible_flags)
    summary.mobile_or_outreach = any(
        normalize_mode(visit.get("mode")) in {"mobile_bus", "home_delivery"}
        for visit in visits
        if str(visit.get("town")) == town
    )
    summary.accessible_facilities = sorted(facilities)
    summary.unavailable_facilities = sorted(unavailable)
    summary.access_gap = summary.branch_accessible is False and not summary.mobile_or_outreach
    if summary.access_gap:
        summary.notes.append("该乡镇无启用中的无障碍分馆，也未见流动服务记录；无障碍机会存在缺口。")
    return summary


def build_resource_context(
    town: str,
    branches: Iterable[Mapping[str, Any]],
    programs: Iterable[Mapping[str, Any]],
    routes: Iterable[Mapping[str, Any]],
    as_of: date | None = None,
) -> ResourceContext:
    active_ids: list[str] = []
    facilities: set[str] = set()
    gaps: set[str] = set()
    notes: list[str] = []
    accessible: bool | None = None
    for branch in branches:
        if str(branch.get("town")) != town:
            continue
        if not branch_active_on(branch, as_of):
            if branch.get("active_from"):
                notes.append(
                    f"分馆 {branch.get('branch_id')} 于 {branch.get('active_from')} 起启用，"
                    f"报告时点 {as_of} 尚无该馆藏点。"
                )
            continue
        active_ids.append(str(branch.get("branch_id")))
        if branch.get("accessible") is True:
            accessible = True
        elif branch.get("accessible") is False and accessible is None:
            accessible = False
        for facility in branch.get("facilities") or ():
            facilities.add(str(facility))
        for gap in branch.get("facility_gaps") or ():
            gaps.add(str(gap))
    for program in programs:
        if str(program.get("town")) != town:
            continue
        support = program.get("access_support")
        if support and str(support).lower() not in {"none", "unknown"}:
            facilities.add(str(support))
    for route in routes:
        if town in (route.get("towns") or ()):
            if route.get("status") in {"cancelled", "withdrawn"}:
                notes.append(
                    f"路线 {route.get('route_id')} 已取消，不能算作报告时点可用的流动资源。"
                )
    return ResourceContext(
        town=town,
        branches=tuple(active_ids),
        accessible_branch=accessible,
        facilities=tuple(sorted(facilities)),
        facility_gaps=tuple(sorted(gaps)),
        notes=tuple(notes),
    )


def build_coverage(
    town: str,
    modes: Iterable[str],
    visits: Iterable[Mapping[str, Any]],
    loans: Iterable[Mapping[str, Any]],
    programs: Iterable[Mapping[str, Any]],
    *,
    as_of: date | None = None,
    available_modes: Iterable[str] | None = None,
    planned_modes: Iterable[str] | None = None,
) -> dict[str, CoverageStatus]:
    """生成乡镇×服务方式的覆盖状态；没有记录 ≠ 确认没有服务。

    ``modes`` 是要检查的模式（通常是该乡镇在报告期内出现的服务方式）。
    ``available_modes`` 是"按当时可用资源本应有机会"的模式；不在其中的模式
    若属于 ``planned_modes``，标为 ``planned_only``（仅提议，未核实），
    否则标为 ``no_opportunity``，既不算缺口也不算成绩。默认把所有 ``modes``
    视为可用。
    """
    visit_list = list(visits)
    loan_list = list(loans)
    program_list = list(programs)
    available = set(modes) if available_modes is None else set(available_modes)
    planned = set(planned_modes or ())
    modes = sorted(
        set(modes)
        | {mode for mode in available if mode in SUPPORTED_MODES}
        | {mode for mode in planned if mode in SUPPORTED_MODES}
    )

    statuses: dict[str, CoverageStatus] = {}
    for mode in modes:
        records = 0
        known = 0
        unknown = 0
        partial = 0
        last_observed: str | None = None

        def take(
            record: Mapping[str, Any],
            volume: int | None,
            quality: str = "complete",
            *,
            count_unknown: bool = True,
        ) -> None:
            nonlocal records, known, unknown, partial, last_observed
            records += 1
            if volume is None:
                if count_unknown:
                    unknown += 1
            else:
                known += max(0, volume)
            if quality and quality not in {"complete", "full", "ok"}:
                partial += 1
            stamp = record.get("observed_at") or record.get("loaned_at") or record.get("starts_at")
            if isinstance(stamp, str) and (last_observed is None or stamp > last_observed):
                last_observed = stamp

        if mode == "branch":
            attendance_records = 0
            for visit in visit_list:
                if normalize_mode(visit.get("mode")) == "branch":
                    attendance_records += 1
                    take(visit, _number(visit.get("reader_count")), str(visit.get("quality") or ""))
            # 借阅证明分馆在运转，但不提供到馆人数；只计记录，不把交互次数
            # 混进到馆人数。
            for loan in loan_list:
                take(loan, None, "complete", count_unknown=False)
            if loan_list and attendance_records == 0:
                unknown += 1
                last_observed = last_observed or str(loan_list[0].get("loaned_at") or "")
        elif mode == "mobile_bus":
            for visit in visit_list:
                if normalize_mode(visit.get("mode")) == "mobile_bus":
                    take(visit, _number(visit.get("reader_count")), str(visit.get("quality") or ""))
        elif mode == "online":
            for visit in visit_list:
                if normalize_mode(visit.get("mode")) == "online":
                    take(
                        visit,
                        _number(visit.get("reader_count")),
                        str(visit.get("quality") or ""),
                    )
        elif mode == "proxy":
            for loan in loan_list:
                if _kind(loan) == "proxy":
                    take(
                        loan,
                        1 if loan.get("household_key") or loan.get("member_key") else None,
                        "partial" if not loan.get("member_key") else "complete",
                    )
        elif mode == "program":
            proposed = 0
            for program in program_list:
                status = str(program.get("status") or "").strip().lower()
                if status in {"cancelled", "declined"}:
                    continue
                if status in {"proposed", "planned", "draft", "unknown"}:
                    # 提议中的活动是机会，不是已提供的服务。
                    proposed += 1
                    continue
                volume = _number(program.get("attended_count", program.get("actual_attendance")))
                if volume is None:
                    volume = _number(program.get("registered_count", program.get("registrations")))
                take(program, volume, str(program.get("quality") or "complete"))
            if records == 0 and proposed:
                statuses[mode] = CoverageStatus(
                    town=town,
                    mode=mode,
                    status="unconfirmed",
                    records=0,
                    known_volume=0,
                    unknown_volume_records=0,
                    partial_records=0,
                    last_observed=last_observed,
                    available=mode in available,
                    planned=True,
                    note=f"有 {proposed} 场活动仍为提议状态，尚未确认；不能按已提供服务计。",
                )
                continue

        is_available = mode in available
        if records == 0 and not is_available and mode in planned:
            status = "planned_only"
            note = "该服务方式只有提议或待批安排，尚不能计入已提供机会，也不是已确认缺口。"
        elif records == 0 and not is_available:
            status = "no_opportunity"
            note = "报告期内该乡镇没有此类服务机会；无记录是预期情况，不计为缺口。"
        elif records == 0:
            status = "missing_record"
            note = "按可用资源本应有该类记录却未找到；不能据此认定该乡镇从未获得此类服务。"
        elif partial:
            status = "partial"
            note = "存在质量不完整的记录，人数与覆盖范围应视为暂定。"
        elif unknown:
            status = "unknown_volume"
            note = "有服务记录但人数缺失，只能确认接触发生，不能确定服务量。"
        else:
            status = "observed"
            note = "记录完整，可用于计算该方式的观察值。"

        statuses[mode] = CoverageStatus(
            town=town,
            mode=mode,
            status=status,
            records=records,
            known_volume=known,
            unknown_volume_records=unknown,
            partial_records=partial,
            last_observed=last_observed,
            available=is_available,
            planned=mode in planned,
            note=note,
        )
    return statuses
