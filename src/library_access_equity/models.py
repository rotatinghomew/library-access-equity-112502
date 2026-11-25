"""共享值对象。

这里刻意不把"读者人数"压成一个必填整数。合成资料里代借、家庭共用账号、
无身份关联和线上打开页面都只能证明"发生过某种服务接触"，不能直接换算成
若干独立读者。因此使用 :class:`CountBand` 保存下界与上界，上界为 ``None``
表示现有资料无法给出封闭范围。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Iterable, Mapping


@dataclass(frozen=True)
class CountBand:
    """一个计数区间：已知至少 ``low``，至多 ``high``；``None`` 表示上界未知。"""

    low: int
    high: int | None
    note: str = ""

    def __post_init__(self) -> None:
        if self.low < 0:
            raise ValueError("low must be >= 0")
        if self.high is not None and self.high < self.low:
            raise ValueError("high must be >= low")

    @classmethod
    def exact(cls, value: int, note: str = "") -> "CountBand":
        return cls(int(value), int(value), note)

    @classmethod
    def at_least(cls, low: int, note: str = "") -> "CountBand":
        return cls(low, None, note)

    @classmethod
    def unknown(cls, note: str = "") -> "CountBand":
        return cls(0, None, note)

    @classmethod
    def from_maybe(cls, value: Any, note: str = "") -> "CountBand":
        """``None``/缺失保留为未知，而不是 0。"""
        parsed = _as_int(value)
        if parsed is None:
            return cls.unknown(note)
        return cls.exact(parsed, note)

    @property
    def is_exact(self) -> bool:
        return self.high is not None and self.high == self.low

    @property
    def is_unknown(self) -> bool:
        return self.high is None

    def __add__(self, other: "CountBand") -> "CountBand":
        if not isinstance(other, CountBand):
            return NotImplemented
        high = None if self.high is None or other.high is None else self.high + other.high
        notes = "; ".join(part for part in (self.note, other.note) if part)
        return CountBand(self.low + other.low, high, notes)

    def __bool__(self) -> bool:
        return self.low > 0

    def as_dict(self) -> dict[str, Any]:
        return {
            "low": self.low,
            "high": self.high,
            "exact": self.is_exact,
            "upper_unresolved": self.high is None,
            "note": self.note,
        }

    def __str__(self) -> str:
        if self.is_exact:
            return str(self.low)
        if self.high is None:
            return f">={self.low}（上界未知）"
        return f"{self.low}-{self.high}"


def sum_bands(bands: Iterable[CountBand], note: str = "") -> CountBand:
    total = CountBand.exact(0)
    for band in bands:
        total = total + band
    if note and total.note:
        return CountBand(total.low, total.high, f"{note}; {total.note}")
    if note:
        return CountBand(total.low, total.high, note)
    return total


def _as_int(value: Any) -> int | None:
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        return int(value)
    if isinstance(value, str):
        text = value.strip()
        if text.isdigit():
            return int(text)
    return None


@dataclass(frozen=True)
class EvidenceGap:
    """资料缺口。``kind`` 用于机器比较，``detail`` 供人阅读。"""

    kind: str
    detail: str
    town: str | None = None
    mode: str | None = None
    record_id: str | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "kind": self.kind,
            "detail": self.detail,
            "town": self.town,
            "mode": self.mode,
            "record_id": self.record_id,
        }


@dataclass(frozen=True)
class CoverageStatus:
    """一个乡镇×服务方式的证据覆盖状态。

    ``missing_record`` 表示按可用资源本应有记录却没有找到，不等于确认
    "没有服务"；``no_opportunity`` 表示当时没有该服务机会，不能作为缺口；
    ``unknown_volume`` 表示有记录但人数缺失。
    """

    town: str
    mode: str
    status: str
    records: int
    known_volume: int
    unknown_volume_records: int
    partial_records: int
    last_observed: str | None = None
    available: bool = True
    planned: bool = False
    note: str = ""

    @property
    def is_gap(self) -> bool:
        return self.available and self.status in {
            "missing_record",
            "unknown_volume",
            "partial",
            "unconfirmed",
        }

    def as_dict(self) -> dict[str, Any]:
        return {
            "town": self.town,
            "mode": self.mode,
            "status": self.status,
            "records": self.records,
            "known_volume": self.known_volume,
            "unknown_volume_records": self.unknown_volume_records,
            "partial_records": self.partial_records,
            "last_observed": self.last_observed,
            "available": self.available,
            "planned": self.planned,
            "gap": self.is_gap,
            "note": self.note,
        }


@dataclass(frozen=True)
class ResourceContext:
    """某乡镇在某时点可用的实体资源，用于解释历史报告。"""

    town: str
    branches: tuple[str, ...] = ()
    accessible_branch: bool | None = None
    facilities: tuple[str, ...] = ()
    facility_gaps: tuple[str, ...] = ()
    notes: tuple[str, ...] = ()

    def as_dict(self) -> dict[str, Any]:
        return {
            "town": self.town,
            "branches": list(self.branches),
            "accessible_branch": self.accessible_branch,
            "facilities": list(self.facilities),
            "facility_gaps": list(self.facility_gaps),
            "notes": list(self.notes),
        }


@dataclass
class LoanSummary:
    interactions: int = 0
    renewals: int = 0
    cross_branch_returns: int = 0
    service_units: int = 0
    known_members: int = 0
    households: int = 0
    unlinked_interactions: int = 0
    reader_band: CountBand = field(default_factory=CountBand.unknown)
    by_kind: dict[str, int] = field(default_factory=dict)
    resource_unavailable_at_event: int = 0
    notes: list[str] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        return {
            "interactions": self.interactions,
            "renewals": self.renewals,
            "cross_branch_returns": self.cross_branch_returns,
            "service_units": self.service_units,
            "known_members": self.known_members,
            "households": self.households,
            "unlinked_interactions": self.unlinked_interactions,
            "reader_band": self.reader_band.as_dict(),
            "by_kind": dict(sorted(self.by_kind.items())),
            "resource_unavailable_at_event": self.resource_unavailable_at_event,
            "notes": list(self.notes),
        }


@dataclass
class ModeVisitSummary:
    records: int = 0
    known_attendance: int = 0
    unknown_volume_records: int = 0
    partial_records: int = 0
    reading_completions: int = 0
    note: str = ""

    def as_dict(self) -> dict[str, Any]:
        return {
            "records": self.records,
            "known_attendance": self.known_attendance,
            "unknown_volume_records": self.unknown_volume_records,
            "partial_records": self.partial_records,
            "reading_completions": self.reading_completions,
            "attendance_band": CountBand(
                self.known_attendance,
                None if self.unknown_volume_records else self.known_attendance,
            ).as_dict(),
            "note": self.note,
        }


@dataclass
class VisitSummary:
    by_mode: dict[str, ModeVisitSummary] = field(default_factory=dict)
    physical_attendance: int = 0
    physical_unknown_records: int = 0
    digital_opens: int = 0
    digital_reading_completions: int = 0
    last_observed: str | None = None
    notes: list[str] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        return {
            "by_mode": {mode: summary.as_dict() for mode, summary in sorted(self.by_mode.items())},
            "physical_attendance": self.physical_attendance,
            "physical_unknown_records": self.physical_unknown_records,
            "digital_opens": self.digital_opens,
            "digital_reading_completions": self.digital_reading_completions,
            "last_observed": self.last_observed,
            "notes": list(self.notes),
        }


@dataclass
class ProgramSummary:
    confirmed: int = 0
    published: int = 0
    completed: int = 0
    proposed: int = 0
    cancelled: int = 0
    registered_known: int = 0
    registration_unknown_records: int = 0
    attended_known: int = 0
    attendance_unknown_records: int = 0
    cancelled_capacity: int = 0
    missing_cancel_reasons: int = 0
    access_unmet_program_ids: list[str] = field(default_factory=list)
    over_capacity_program_ids: list[str] = field(default_factory=list)
    commitments: list[dict[str, Any]] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        return {
            "confirmed": self.confirmed,
            "published": self.published,
            "completed": self.completed,
            "proposed": self.proposed,
            "cancelled": self.cancelled,
            "registered_known": self.registered_known,
            "registration_unknown_records": self.registration_unknown_records,
            "attended_known": self.attended_known,
            "attendance_unknown_records": self.attendance_unknown_records,
            "cancelled_capacity": self.cancelled_capacity,
            "missing_cancel_reasons": self.missing_cancel_reasons,
            "access_unmet_program_ids": list(self.access_unmet_program_ids),
            "over_capacity_program_ids": list(self.over_capacity_program_ids),
            "commitments": list(self.commitments),
            "notes": list(self.notes),
        }


@dataclass
class AccessSummary:
    branch_accessible: bool | None = None
    accessible_facilities: list[str] = field(default_factory=list)
    unavailable_facilities: list[str] = field(default_factory=list)
    mobile_or_outreach: bool = False
    access_gap: bool = False
    notes: list[str] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        return {
            "branch_accessible": self.branch_accessible,
            "accessible_facilities": list(self.accessible_facilities),
            "unavailable_facilities": list(self.unavailable_facilities),
            "mobile_or_outreach": self.mobile_or_outreach,
            "access_gap": self.access_gap,
            "notes": list(self.notes),
        }


@dataclass
class TownEvidence:
    town: str
    loans: LoanSummary = field(default_factory=LoanSummary)
    visits: VisitSummary = field(default_factory=VisitSummary)
    programs: ProgramSummary = field(default_factory=ProgramSummary)
    access: AccessSummary = field(default_factory=AccessSummary)
    coverage: dict[str, CoverageStatus] = field(default_factory=dict)
    resource_context: ResourceContext | None = None
    evidence_gaps: list[EvidenceGap] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)

    @property
    def coverage_gaps(self) -> list[CoverageStatus]:
        return [status for status in self.coverage.values() if status.is_gap]

    def as_dict(self) -> dict[str, Any]:
        return {
            "town": self.town,
            "loans": self.loans.as_dict(),
            "visits": self.visits.as_dict(),
            "programs": self.programs.as_dict(),
            "access": self.access.as_dict(),
            "coverage": {mode: status.as_dict() for mode, status in sorted(self.coverage.items())},
            "coverage_gaps": [status.as_dict() for status in self.coverage_gaps],
            "resource_context": self.resource_context.as_dict() if self.resource_context else None,
            "evidence_gaps": [gap.as_dict() for gap in self.evidence_gaps],
            "notes": list(self.notes),
        }


def to_jsonable(value: Any) -> Any:
    """把 dataclass/元组递归转换为可序列化结构。"""

    if hasattr(value, "as_dict"):
        return value.as_dict()
    if isinstance(value, Mapping):
        return {str(key): to_jsonable(item) for key, item in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [to_jsonable(item) for item in value]
    return value
