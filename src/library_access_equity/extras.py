"""可选资料（设施、授权、车辆、馆藏下架、家庭组成）的读取辅助。

这些资料可能尚未到达。函数在缺失时返回空索引，绝不合成"没有"的记录；
调用方应把"资料未到达"作为未核实，而不是零。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from typing import Any, Iterable, Mapping

from .summaries import branch_active_on, parse_date


def _records(bundle: Mapping[str, Any], name: str) -> list[Mapping[str, Any]]:
    value = bundle.get(name)
    if not isinstance(value, list):
        return []
    return [record for record in value if isinstance(record, Mapping)]


def _active(record: Mapping[str, Any], as_of: date | None) -> bool:
    start = parse_date(record.get("from") or record.get("available_from") or record.get("installed_at"))
    end = parse_date(record.get("to") or record.get("available_to") or record.get("removed_at"))
    if as_of is None:
        return True
    if start is not None and start > as_of:
        return False
    if end is not None and as_of >= end:
        return False
    return True


@dataclass
class AuthorizationIndex:
    """家庭/机构授权关联。没有授权记录时所有查询返回空集合。"""

    member_to_households: dict[str, set[str]] = field(default_factory=dict)
    household_members: dict[str, set[str]] = field(default_factory=dict)
    household_institutions: dict[str, set[str]] = field(default_factory=dict)
    authorized_ids: set[str] = field(default_factory=set)
    present: bool = False

    def members_for_household(self, household_key: str) -> set[str]:
        return set(self.household_members.get(str(household_key), ()))

    def households_for_member(self, member_key: str) -> set[str]:
        return set(self.member_to_households.get(str(member_key), ()))


def build_authorization_index(bundle: Mapping[str, Any], as_of: date | None = None) -> AuthorizationIndex:
    index = AuthorizationIndex(present="authorizations.json" in bundle)
    for auth in _records(bundle, "authorizations.json"):
        if not _active(auth, as_of):
            continue
        status = str(auth.get("status") or "active").strip().lower()
        if status in {"revoked", "expired", "declined"}:
            continue
        member = auth.get("member_key")
        household = auth.get("household_key")
        institution = auth.get("institution_id")
        if not member and not household:
            continue
        if member and household:
            index.member_to_households.setdefault(str(member), set()).add(str(household))
            index.household_members.setdefault(str(household), set()).add(str(member))
        if household and institution:
            index.household_institutions.setdefault(str(household), set()).add(str(institution))
        if auth.get("authorization_id"):
            index.authorized_ids.add(str(auth["authorization_id"]))
    # 家庭组成资料可以补充授权未覆盖的关联。
    for household in _records(bundle, "households.json"):
        key = household.get("household_key")
        if not key:
            continue
        members = household.get("member_keys") or household.get("members") or ()
        for member in members:
            index.household_members.setdefault(str(key), set()).add(str(member))
            index.member_to_households.setdefault(str(member), set()).add(str(key))
    return index


@dataclass
class FacilityIndex:
    by_town: dict[str, list[dict[str, Any]]] = field(default_factory=dict)
    by_branch: dict[str, list[dict[str, Any]]] = field(default_factory=dict)
    present: bool = False


def build_facility_index(
    bundle: Mapping[str, Any],
    branches: Iterable[Mapping[str, Any]],
    as_of: date | None = None,
) -> FacilityIndex:
    index = FacilityIndex(present="facilities.json" in bundle)
    branch_town = {str(branch.get("branch_id")): str(branch.get("town")) for branch in branches}
    for facility in _records(bundle, "facilities.json"):
        if not _active(facility, as_of):
            continue
        available = facility.get("available")
        if available is None:
            available = facility.get("status") not in {"out_of_service", "broken", "unavailable", "retired"}
        town = facility.get("town") or branch_town.get(str(facility.get("branch_id")))
        entry = {
            "facility_id": facility.get("facility_id"),
            "kind": facility.get("kind") or facility.get("facility_type") or "unknown",
            "available": bool(available),
            "town": town,
            "branch_id": facility.get("branch_id"),
            "note": facility.get("note"),
        }
        if town:
            index.by_town.setdefault(str(town), []).append(entry)
        if facility.get("branch_id"):
            index.by_branch.setdefault(str(facility["branch_id"]), []).append(entry)
    return index


@dataclass
class VehicleIndex:
    by_id: dict[str, dict[str, Any]] = field(default_factory=dict)
    present: bool = False


def build_vehicle_index(bundle: Mapping[str, Any]) -> VehicleIndex:
    index = VehicleIndex(present="vehicles.json" in bundle)
    for vehicle in _records(bundle, "vehicles.json"):
        vehicle_id = vehicle.get("vehicle_id") or vehicle.get("id")
        if not vehicle_id:
            continue
        index.by_id[str(vehicle_id)] = dict(vehicle)
    return index


@dataclass
class ItemIndex:
    withdrawn_at: dict[str, date] = field(default_factory=dict)
    replacement: dict[str, str] = field(default_factory=dict)
    present: bool = False

    def withdrawn_on_or_before(self, item_id: Any, as_of: date | None) -> date | None:
        stamp = self.withdrawn_at.get(str(item_id))
        if stamp is None or as_of is None:
            return stamp
        return stamp if stamp <= as_of else None


def build_item_index(bundle: Mapping[str, Any]) -> ItemIndex:
    index = ItemIndex(present="items.json" in bundle)
    for item in _records(bundle, "items.json"):
        item_id = item.get("item_id")
        if not item_id:
            continue
        stamp = parse_date(item.get("withdrawn_at") or item.get("deaccessioned_at"))
        if stamp is not None:
            index.withdrawn_at[str(item_id)] = stamp
        if item.get("replacement_item_id"):
            index.replacement[str(item_id)] = str(item["replacement_item_id"])
    return index


def facility_sets(index: FacilityIndex, town: str, branch_id: str | None = None) -> tuple[list[str], list[str]]:
    entries = index.by_town.get(town, []) if branch_id is None else index.by_branch.get(branch_id, [])
    available = sorted({str(entry["kind"]) for entry in entries if entry["available"]})
    unavailable = sorted({str(entry["kind"]) for entry in entries if not entry["available"]})
    return available, unavailable


def town_facility_gaps(index: FacilityIndex, town: str) -> list[str]:
    """没有设施记录 ≠ 没有设施；只在明确记录不可用时返回缺口。"""
    return sorted({str(entry["kind"]) for entry in index.by_town.get(town, []) if not entry["available"]})


def branch_active(branch: Mapping[str, Any], as_of: date | None) -> bool:
    return branch_active_on(branch, as_of)
