"""读取合成资料并执行输入契约校验。

必需资料保持最小集合；设施、授权、车辆等记录是后续可能到达的可选资料，
存在时一并读取，绝不因缺失而补默认记录。
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

REQUIRED_FILES = (
    'source_info.json',
    'branches.json',
    'loans.json',
    'visits.json',
    'programs.json',
    'routes.json',
)

# 这些资料不是每个资料包都已具备；缺失代表"尚未收到"，不是"没有"。
OPTIONAL_FILES = (
    'facilities.json',
    'authorizations.json',
    'vehicles.json',
    'households.json',
    'items.json',
)

_LIST_FILES = tuple(name for name in REQUIRED_FILES + OPTIONAL_FILES if name.endswith('.json') and name != 'source_info.json')


def load_bundle(root: str | Path) -> dict[str, Any]:
    """读取资料包，保留原始记录、缺失值和顺序。"""
    base = Path(root)
    bundle: dict[str, Any] = {}
    for name in REQUIRED_FILES:
        path = base / name
        if not path.is_file():
            raise FileNotFoundError(path)
        with path.open(encoding="utf-8") as handle:
            bundle[name] = json.load(handle)
    for name in OPTIONAL_FILES:
        path = base / name
        if path.is_file():
            with path.open(encoding="utf-8") as handle:
                bundle[name] = json.load(handle)
    _check_contract(bundle)
    return bundle


def _check_contract(bundle: dict[str, Any]) -> None:
    source_info = bundle["source_info.json"]
    if not isinstance(source_info, dict):
        raise ValueError("source_info.json 必须是对象")
    if source_info.get("source_kind") != "synthetic":
        raise ValueError("资料包必须标明为 synthetic")
    for name in _LIST_FILES:
        if name not in bundle:
            continue
        if not isinstance(bundle[name], list):
            raise ValueError(f"{name} 必须是记录列表")
        for index, record in enumerate(bundle[name]):
            if not isinstance(record, dict):
                raise ValueError(f"{name} 第 {index} 条记录必须是对象")


def dataset_presence(bundle: dict[str, Any]) -> dict[str, bool]:
    """说明每类可选资料是否已到达，供报告标注证据覆盖。"""
    return {name: name in bundle for name in OPTIONAL_FILES}
