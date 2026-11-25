"""服务承诺与路线调整的追加式记录。

每条记录只追加、不改写，带 ``recorded_at`` 和 ``basis``（当时的依据）。这样
可以回答：

* 已公布/已完成的到馆与活动承诺是否被挪动或撤销；
* 一次路线调整是在什么证据下做出的；
* 调整之后对应乡镇是否真的出现了新的使用。

不使用数据库，也没有隐式覆盖：调用方负责保存列表。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Any, Iterable, Mapping

from .models import CountBand, to_jsonable
from .summaries import parse_date


def _stamp(value: str | date | datetime | None) -> str:
    if value is None:
        return ""
    if isinstance(value, datetime):
        return value.isoformat(timespec="minutes")
    if isinstance(value, date):
        return value.isoformat()
    return str(value)


def _town_usage(
    bundle: Mapping[str, Any], town: str, start: str, end: str | None
) -> tuple[CountBand, int]:
    """统计调整生效后该乡镇的实际使用，作为"是否改善机会"的观察值。"""
    start_date = parse_date(start)
    end_date = parse_date(end) if end else None
    known = 0
    missing = False
    records = 0
    for loan in bundle.get("loans.json", ()) or ():
        if str(loan.get("town") or "") != town:
            continue
        event = parse_date(loan.get("loaned_at"))
        if event is None or start_date is None or event < start_date:
            continue
        if end_date is not None and event > end_date:
            continue
        records += 1
        known += 1
    for visit in bundle.get("visits.json", ()) or ():
        if str(visit.get("town")) != town:
            continue
        event = parse_date(visit.get("observed_at"))
        if event is None or start_date is None or event < start_date:
            continue
        if end_date is not None and event > end_date:
            continue
        records += 1
        count = visit.get("reader_count")
        if isinstance(count, int) and not isinstance(count, bool):
            known += count
        else:
            missing = True
    for program in bundle.get("programs.json", ()) or ():
        if str(program.get("town")) != town:
            continue
        event = parse_date(program.get("starts_at"))
        if event is None or start_date is None or event < start_date:
            continue
        if end_date is not None and event > end_date:
            continue
        records += 1
        count = program.get("attended_count", program.get("actual_attendance"))
        if isinstance(count, int) and not isinstance(count, bool):
            known += count
        else:
            missing = True
    if records == 0:
        return CountBand.exact(0, "调整后尚无可用记录；不能视为没有改善"), 0
    if missing:
        return CountBand(known, None, "调整后记录有人数缺失，改善幅度下界"), records
    return CountBand.exact(known, "调整后记录完整"), records


@dataclass
class LedgerEntry:
    entry_id: str
    kind: str
    recorded_at: str
    town: str | None
    route_id: str | None
    action: str
    basis: dict[str, Any]
    status: str = "recorded"
    evidence: dict[str, Any] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        return {
            "entry_id": self.entry_id,
            "kind": self.kind,
            "recorded_at": self.recorded_at,
            "town": self.town,
            "route_id": self.route_id,
            "action": self.action,
            "basis": to_jsonable(self.basis),
            "status": self.status,
            "evidence": to_jsonable(self.evidence),
        }


class ServiceLedger:
    """只追加的服务记录。"""

    def __init__(self, entries: Iterable[LedgerEntry] = ()) -> None:
        self._entries: list[LedgerEntry] = list(entries)

    def __iter__(self):
        return iter(self._entries)

    def __len__(self) -> int:
        return len(self._entries)

    def append(self, entry: LedgerEntry) -> LedgerEntry:
        if any(existing.entry_id == entry.entry_id for existing in self._entries):
            raise ValueError(f"entry_id {entry.entry_id} 已存在，只能追加新记录")
        self._entries.append(entry)
        return entry

    def _next_id(self, kind: str) -> str:
        return f"{kind}-{len(self._entries) + 1:03d}"

    def record_commitment(
        self,
        *,
        kind: str,
        record_id: str,
        town: str,
        when: str | None,
        status: str,
        evidence: Mapping[str, Any],
        recorded_at: str | date | datetime | None = None,
    ) -> LedgerEntry:
        """登记已公布/已确认/已完成的服务承诺。"""
        if status not in {"confirmed", "published", "completed"}:
            raise ValueError("只有 confirmed/published/completed 才构成受保护承诺")
        return self.append(
            LedgerEntry(
                entry_id=self._next_id("commit"),
                kind="commitment",
                recorded_at=_stamp(recorded_at),
                town=town,
                route_id=record_id if kind == "route_stop" else None,
                action="recorded",
                basis={"kind": kind, "record_id": record_id, "when": when, "status": status, "evidence": dict(evidence)},
                status="protected",
            )
        )

    def record_route_change(
        self,
        *,
        town: str,
        route_id: str,
        action: str,
        basis: Mapping[str, Any],
        recorded_at: str | date | datetime | None = None,
        status: str = "recorded",
    ) -> LedgerEntry:
        """记录一次路线增加/替换/保留；``basis`` 保存当时看到的证据。"""
        if action not in {"add", "replace", "keep", "defer", "withdraw"}:
            raise ValueError(f"未知路线动作: {action}")
        if action in {"replace", "withdraw"} and not basis:
            raise ValueError("调整已安排路线必须保留当时依据")
        return self.append(
            LedgerEntry(
                entry_id=self._next_id("route"),
                kind="route_change",
                recorded_at=_stamp(recorded_at),
                town=town,
                route_id=route_id,
                action=action,
                basis=dict(basis),
                status=status,
            )
        )

    def record_follow_up(
        self,
        *,
        town: str,
        route_id: str,
        since: str,
        bundle: Mapping[str, Any],
        until: str | None = None,
        recorded_at: str | date | datetime | None = None,
    ) -> LedgerEntry:
        """在调整生效后回看对应乡镇的实际使用。"""
        usage, observed_records = _town_usage(bundle, town, since, until)
        return self.append(
            LedgerEntry(
                entry_id=self._next_id("followup"),
                kind="follow_up",
                recorded_at=_stamp(recorded_at),
                town=town,
                route_id=route_id,
                action="observed",
                basis={"since": since, "until": until},
                status="observed",
                evidence={"usage_band": usage.as_dict(), "usage": str(usage), "observed_records": observed_records},
            )
        )

    def as_list(self) -> list[dict[str, Any]]:
        return [entry.as_dict() for entry in self._entries]

    def for_town(self, town: str) -> list[LedgerEntry]:
        return [entry for entry in self._entries if entry.town == town]


def evaluate_route_adjustment(
    ledger: ServiceLedger,
    *,
    town: str,
    route_id: str,
) -> dict[str, Any]:
    """回看一次路线调整：前后证据、承诺是否保住、后续使用是否改善。"""
    changes = [
        entry
        for entry in ledger
        if entry.kind == "route_change" and entry.town == town and entry.route_id == route_id
    ]
    follow_ups = [
        entry
        for entry in ledger
        if entry.kind == "follow_up" and entry.town == town and entry.route_id == route_id
    ]
    # 该乡镇的承诺属于整体服务承诺；一次路线调整不能把它们从记录里排除。
    commitments = [
        entry for entry in ledger if entry.kind == "commitment" and entry.town == town
    ]
    improved: bool | None = None
    if follow_ups:
        latest = follow_ups[-1]
        band = latest.evidence.get("usage_band", {})
        observed = latest.evidence.get("observed_records", 0)
        if observed == 0 or not band.get("exact"):
            # 没有后续记录或有人数缺失时，不能断言改善或未改善。
            improved = None
        else:
            improved = band.get("low", 0) > 0
    return {
        "town": town,
        "route_id": route_id,
        "route_changes": [entry.as_dict() for entry in changes],
        "commitments_preserved": [
            entry.as_dict() for entry in commitments if entry.status == "protected"
        ],
        "follow_ups": [entry.as_dict() for entry in follow_ups],
        "improved": improved,
        "note": (
            "后续使用只反映观察到的记录；缺失不能当作改善或未改善。"
            if improved is None
            else "后续使用已有记录，可与调整前依据对照。"
        ),
    }
