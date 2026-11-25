"""县域图书馆服务公平证据与流动资源安排。"""

from .analysis import EquityReport, TownOpportunity, build_report, render_text
from .input_contract import dataset_presence, load_bundle
from .ledger import ServiceLedger, evaluate_route_adjustment
from .models import CountBand, EvidenceGap, TownEvidence
from .planning import (
    RoutePlan,
    analyze_routes,
    commitment_moves,
    route_window,
    service_gap_if_dropped,
)

__all__ = [
    "CountBand",
    "EquityReport",
    "EvidenceGap",
    "RoutePlan",
    "ServiceLedger",
    "TownEvidence",
    "TownOpportunity",
    "analyze_routes",
    "build_report",
    "commitment_moves",
    "dataset_presence",
    "evaluate_route_adjustment",
    "load_bundle",
    "render_text",
    "route_window",
    "service_gap_if_dropped",
]
