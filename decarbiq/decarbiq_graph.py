"""
DECARBIQ COMPUTATIONAL RELATIONSHIP GRAPH
==========================================
Directed acyclic graph (DAG) representing energy price relationships.

Upstream drivers -> Natural Gas Price (Henry Hub) -> Electricity Price
- Generation mix moderates the gas-electricity linkage
- Policy regimes act as structural shifters changing coefficients over time
- Demand drivers affect both gas and electricity prices

Two separate graphs: ERCOT (Texas) and CAISO (California), each pre-populated
with real regression coefficients from DecarbIQ analysis.

Sources:
  - full_model_coefficients.csv (45-variable Ridge model, R²=0.76)
  - decarbiq_model_params.json (ERCOT/CAISO multivariate electricity models)
  - crude_oil_params.json (WTI-to-gas transmission)
  - correlation_results_no_outliers.csv (bivariate correlations)
  - wholesale_regression_results.csv (gas-electricity bivariate)

Author: DecarbIQ Analytics
Date: 2026-02-10
"""

from __future__ import annotations

import copy
import json
from collections import defaultdict, deque
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

# =============================================================================
# CONSTANTS
# =============================================================================

NODE_TYPES = {
    "upstream_supply",
    "upstream_demand",
    "upstream_storage",
    "gas_price",
    "elec_wholesale",
    "elec_retail",
    "moderator",
    "structural_shifter",
    "seasonal",
    "derived",
    "disruption_weather",
    "disruption_geopolitical",
    "disruption_market",
}

EDGE_TYPES = {
    "causal",
    "moderating",
    "structural_shift",
    "correlation",
}


# =============================================================================
# DATA STRUCTURES
# =============================================================================

@dataclass
class Node:
    """A node in the energy price DAG."""
    node_id: str
    node_type: str
    label: str
    unit: str = ""
    value: Optional[float] = None
    is_input: bool = False
    base_value: float = 0.0
    metadata: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict:
        return {
            "node_id": self.node_id,
            "node_type": self.node_type,
            "label": self.label,
            "unit": self.unit,
            "value": self.value,
            "is_input": self.is_input,
            "base_value": self.base_value,
            "metadata": self.metadata,
        }

    @classmethod
    def from_dict(cls, d: Dict) -> "Node":
        return cls(
            node_id=d["node_id"],
            node_type=d["node_type"],
            label=d["label"],
            unit=d.get("unit", ""),
            value=d.get("value"),
            is_input=d.get("is_input", False),
            base_value=d.get("base_value", 0.0),
            metadata=d.get("metadata", {}),
        )


@dataclass
class Edge:
    """A directed edge in the energy price DAG."""
    edge_id: str
    source: str
    target: str
    edge_type: str = "causal"
    coefficient: float = 0.0
    lag_months: int = 0
    p_value: Optional[float] = None
    r_squared: Optional[float] = None
    n_observations: Optional[int] = None
    significance: str = ""
    description: str = ""
    target_edge_id: Optional[str] = None
    active: bool = True
    start_date: Optional[str] = None
    source_file: str = ""

    def to_dict(self) -> Dict:
        return {
            "edge_id": self.edge_id,
            "source": self.source,
            "target": self.target,
            "edge_type": self.edge_type,
            "coefficient": self.coefficient,
            "lag_months": self.lag_months,
            "p_value": self.p_value,
            "r_squared": self.r_squared,
            "n_observations": self.n_observations,
            "significance": self.significance,
            "description": self.description,
            "target_edge_id": self.target_edge_id,
            "active": self.active,
            "start_date": self.start_date,
            "source_file": self.source_file,
        }

    @classmethod
    def from_dict(cls, d: Dict) -> "Edge":
        return cls(
            edge_id=d["edge_id"],
            source=d["source"],
            target=d["target"],
            edge_type=d.get("edge_type", "causal"),
            coefficient=d.get("coefficient", 0.0),
            lag_months=d.get("lag_months", 0),
            p_value=d.get("p_value"),
            r_squared=d.get("r_squared"),
            n_observations=d.get("n_observations"),
            significance=d.get("significance", ""),
            description=d.get("description", ""),
            target_edge_id=d.get("target_edge_id"),
            active=d.get("active", True),
            start_date=d.get("start_date"),
            source_file=d.get("source_file", ""),
        )


# =============================================================================
# MAIN DAG CLASS
# =============================================================================

class EnergyPriceDAG:
    """Directed acyclic graph for energy price relationships."""

    def __init__(self, region: str, description: str = ""):
        self.region = region
        self.description = description
        self.nodes: Dict[str, Node] = {}
        self.edges: Dict[str, Edge] = {}
        self._forward_adj: Dict[str, List[str]] = defaultdict(list)
        self._reverse_adj: Dict[str, List[str]] = defaultdict(list)
        self._topo_order: Optional[List[str]] = None

    # ---- Graph Construction ----

    def add_node(
        self,
        node_id: str,
        node_type: str,
        label: str,
        unit: str = "",
        is_input: bool = False,
        value: Optional[float] = None,
        base_value: float = 0.0,
        metadata: Optional[Dict] = None,
    ) -> Node:
        node = Node(
            node_id=node_id,
            node_type=node_type,
            label=label,
            unit=unit,
            value=value,
            is_input=is_input,
            base_value=base_value,
            metadata=metadata or {},
        )
        self.nodes[node_id] = node
        self._topo_order = None
        return node

    def add_edge(
        self,
        source: str,
        target: str,
        edge_type: str = "causal",
        coefficient: float = 0.0,
        lag_months: int = 0,
        p_value: Optional[float] = None,
        r_squared: Optional[float] = None,
        n_observations: Optional[int] = None,
        significance: str = "",
        description: str = "",
        target_edge_id: Optional[str] = None,
        active: bool = True,
        start_date: Optional[str] = None,
        source_file: str = "",
        edge_id: Optional[str] = None,
    ) -> Edge:
        if edge_id is None:
            edge_id = f"{source}_to_{target}"
            # Handle duplicates
            if edge_id in self.edges:
                i = 2
                while f"{edge_id}_{i}" in self.edges:
                    i += 1
                edge_id = f"{edge_id}_{i}"

        e = Edge(
            edge_id=edge_id,
            source=source,
            target=target,
            edge_type=edge_type,
            coefficient=coefficient,
            lag_months=lag_months,
            p_value=p_value,
            r_squared=r_squared,
            n_observations=n_observations,
            significance=significance,
            description=description,
            target_edge_id=target_edge_id,
            active=active,
            start_date=start_date,
            source_file=source_file,
        )
        self.edges[edge_id] = e
        self._forward_adj[source].append(edge_id)
        self._reverse_adj[target].append(edge_id)
        self._topo_order = None
        return e

    def remove_node(self, node_id: str) -> None:
        edges_to_remove = []
        for eid, e in self.edges.items():
            if e.source == node_id or e.target == node_id:
                edges_to_remove.append(eid)
        for eid in edges_to_remove:
            self.remove_edge(eid)
        self.nodes.pop(node_id, None)
        self._topo_order = None

    def remove_edge(self, edge_id: str) -> None:
        e = self.edges.pop(edge_id, None)
        if e:
            if edge_id in self._forward_adj[e.source]:
                self._forward_adj[e.source].remove(edge_id)
            if edge_id in self._reverse_adj[e.target]:
                self._reverse_adj[e.target].remove(edge_id)
        self._topo_order = None

    # ---- Topological Sort ----

    def _topological_sort(self) -> List[str]:
        """Kahn's algorithm. Returns node_ids in dependency order."""
        if self._topo_order is not None:
            return self._topo_order

        # Only count causal and structural_shift edges for ordering
        in_degree: Dict[str, int] = {nid: 0 for nid in self.nodes}
        adj: Dict[str, List[str]] = defaultdict(list)

        for e in self.edges.values():
            if e.edge_type in ("causal", "structural_shift", "moderating"):
                if e.source in self.nodes and e.target in self.nodes:
                    in_degree[e.target] = in_degree.get(e.target, 0) + 1
                    adj[e.source].append(e.target)

        queue = deque(nid for nid, deg in in_degree.items() if deg == 0)
        order = []

        while queue:
            nid = queue.popleft()
            order.append(nid)
            for neighbor in adj[nid]:
                in_degree[neighbor] -= 1
                if in_degree[neighbor] == 0:
                    queue.append(neighbor)

        if len(order) != len(self.nodes):
            missing = set(self.nodes) - set(order)
            raise ValueError(f"Cycle detected involving nodes: {missing}")

        self._topo_order = order
        return order

    # ---- Forward Propagation ----

    def set_inputs(self, values: Dict[str, float]) -> None:
        """Set values on input nodes."""
        for nid, val in values.items():
            if nid in self.nodes:
                self.nodes[nid].value = val

    def propagate(self) -> Dict[str, float]:
        """Forward propagation through the DAG. Returns {node_id: value}."""
        order = self._topological_sort()
        results = {}

        for nid in order:
            node = self.nodes[nid]
            if node.is_input:
                if node.value is None:
                    raise ValueError(f"Input node '{nid}' has no value set")
                results[nid] = node.value
            else:
                val = self._compute_node(nid)
                node.value = val
                results[nid] = val

        return results

    def _compute_node(self, node_id: str) -> float:
        """Compute value for a single non-input node."""
        node = self.nodes[node_id]
        incoming_eids = self._reverse_adj.get(node_id, [])

        causal_edges = []
        shift_edges = []
        for eid in incoming_eids:
            e = self.edges[eid]
            if e.edge_type == "causal":
                causal_edges.append(e)
            elif e.edge_type == "structural_shift":
                shift_edges.append(e)
            # correlation edges don't participate in computation
            # moderating edges are handled via target_edge_id lookup

        # Build moderator map: target_edge_id -> [moderating_edge]
        # Moderating edges scale the coefficient of a causal edge.
        # The moderator node value should be a normalized scalar (1.0 = baseline).
        # If the moderator node's metadata contains 'moderator_baseline', we
        # normalize automatically: effective_scale = value / baseline.
        moderator_map: Dict[str, List[Edge]] = defaultdict(list)
        for e in self.edges.values():
            if e.edge_type == "moderating" and e.target_edge_id:
                moderator_map[e.target_edge_id].append(e)

        # Start with regression intercept
        total = node.base_value

        # Sum causal contributions
        for ce in causal_edges:
            source_val = self.nodes[ce.source].value
            if source_val is None:
                continue

            eff_coeff = ce.coefficient
            for mod_edge in moderator_map.get(ce.edge_id, []):
                mod_node = self.nodes[mod_edge.source]
                mod_val = mod_node.value
                if mod_val is not None:
                    baseline = mod_node.metadata.get("moderator_baseline")
                    if baseline and baseline != 0:
                        # Auto-normalize: 43% gas share with baseline 42% -> scale = 43/42
                        eff_coeff *= (mod_val / baseline)
                    else:
                        # Assume value is already a normalized scalar (1.0 = no change)
                        eff_coeff *= mod_val

            total += eff_coeff * source_val

        # Add structural shifts (source value is binary 0/1)
        for se in shift_edges:
            if se.active:
                source_val = self.nodes[se.source].value
                if source_val is not None:
                    total += se.coefficient * source_val

        return total

    # ---- Sensitivity Analysis ----

    def sensitivity(
        self,
        input_node_id: str,
        delta: float = 1.0,
        base_inputs: Optional[Dict[str, float]] = None,
    ) -> Dict[str, float]:
        """Finite-difference sensitivity of all outputs to one input."""
        if base_inputs:
            self.set_inputs(base_inputs)

        base_results = self.propagate()

        # Perturb
        graph_copy = self._shallow_copy()
        if base_inputs:
            graph_copy.set_inputs(base_inputs)
        original = graph_copy.nodes[input_node_id].value or 0.0
        graph_copy.nodes[input_node_id].value = original + delta
        perturbed_results = graph_copy.propagate()

        sensitivities = {}
        for nid in base_results:
            if not self.nodes[nid].is_input or nid == input_node_id:
                diff = perturbed_results.get(nid, 0.0) - base_results.get(nid, 0.0)
                if abs(diff) > 1e-12:
                    sensitivities[nid] = diff / delta

        return sensitivities

    def full_sensitivity_matrix(
        self,
        base_inputs: Dict[str, float],
        deltas: Optional[Dict[str, float]] = None,
    ) -> Dict[str, Dict[str, float]]:
        """Sensitivity of every output to every input."""
        input_nodes = [nid for nid, n in self.nodes.items() if n.is_input]
        matrix = {}
        for inp in input_nodes:
            d = (deltas or {}).get(inp, 1.0)
            matrix[inp] = self.sensitivity(inp, delta=d, base_inputs=base_inputs)
        return matrix

    def _shallow_copy(self) -> "EnergyPriceDAG":
        """Create a copy with independent node values."""
        g = EnergyPriceDAG(self.region, self.description)
        for nid, n in self.nodes.items():
            g.nodes[nid] = Node(
                node_id=n.node_id,
                node_type=n.node_type,
                label=n.label,
                unit=n.unit,
                value=n.value,
                is_input=n.is_input,
                base_value=n.base_value,
                metadata=n.metadata,
            )
        g.edges = dict(self.edges)
        g._forward_adj = defaultdict(list, {k: list(v) for k, v in self._forward_adj.items()})
        g._reverse_adj = defaultdict(list, {k: list(v) for k, v in self._reverse_adj.items()})
        return g

    # ---- Path Analysis ----

    def all_paths(self, source: str, target: str) -> List[List[str]]:
        """Find all directed paths from source to target (DFS)."""
        paths = []
        self._dfs_paths(source, target, [source], set(), paths)
        return paths

    def _dfs_paths(
        self, current: str, target: str, path: List[str],
        visited: set, paths: List[List[str]]
    ):
        if current == target and len(path) > 1:
            paths.append(list(path))
            return
        visited.add(current)
        for eid in self._forward_adj.get(current, []):
            e = self.edges[eid]
            if e.edge_type in ("causal", "structural_shift") and e.target not in visited:
                path.append(e.target)
                self._dfs_paths(e.target, target, path, visited, paths)
                path.pop()
        visited.discard(current)

    def path_coefficient(self, path: List[str]) -> float:
        """Product of edge coefficients along a path."""
        product = 1.0
        for i in range(len(path) - 1):
            src, tgt = path[i], path[i + 1]
            found = False
            for eid in self._forward_adj.get(src, []):
                e = self.edges[eid]
                if e.target == tgt and e.edge_type in ("causal", "structural_shift"):
                    product *= e.coefficient
                    found = True
                    break
            if not found:
                return 0.0
        return product

    def explain_path(self, path: List[str]) -> str:
        """Human-readable path explanation."""
        parts = []
        for i in range(len(path) - 1):
            src, tgt = path[i], path[i + 1]
            src_node = self.nodes[src]
            tgt_node = self.nodes[tgt]
            for eid in self._forward_adj.get(src, []):
                e = self.edges[eid]
                if e.target == tgt and e.edge_type in ("causal", "structural_shift"):
                    sign = "+" if e.coefficient >= 0 else ""
                    parts.append(
                        f"  {src_node.label} ({src_node.unit}) "
                        f"--[{sign}{e.coefficient:.4f}]--> "
                        f"{tgt_node.label} ({tgt_node.unit})"
                    )
                    break
        chain_coeff = self.path_coefficient(path)
        header = f"Path: {' -> '.join(path)}  (chain coefficient: {chain_coeff:.6f})"
        return header + "\n" + "\n".join(parts)

    # ---- Serialization ----

    def to_dict(self) -> Dict:
        return {
            "region": self.region,
            "description": self.description,
            "nodes": {nid: n.to_dict() for nid, n in self.nodes.items()},
            "edges": {eid: e.to_dict() for eid, e in self.edges.items()},
        }

    @classmethod
    def from_dict(cls, data: Dict) -> "EnergyPriceDAG":
        g = cls(region=data["region"], description=data.get("description", ""))
        for nid, nd in data["nodes"].items():
            n = Node.from_dict(nd)
            g.nodes[nid] = n
        for eid, ed in data["edges"].items():
            e = Edge.from_dict(ed)
            g.edges[eid] = e
            g._forward_adj[e.source].append(eid)
            g._reverse_adj[e.target].append(eid)
        return g

    def to_json(self, filepath: str) -> None:
        with open(filepath, "w") as f:
            json.dump(self.to_dict(), f, indent=2)

    @classmethod
    def from_json(cls, filepath: str) -> "EnergyPriceDAG":
        with open(filepath) as f:
            return cls.from_dict(json.load(f))

    # ---- Inspection ----

    def list_inputs(self) -> List[str]:
        return [nid for nid, n in self.nodes.items() if n.is_input]

    def list_outputs(self) -> List[str]:
        return [nid for nid, n in self.nodes.items() if not n.is_input]

    def describe_node(self, node_id: str) -> str:
        n = self.nodes[node_id]
        lines = [
            f"Node: {n.node_id}",
            f"  Label: {n.label}",
            f"  Type: {n.node_type}",
            f"  Unit: {n.unit}",
            f"  Input: {n.is_input}",
            f"  Value: {n.value}",
            f"  Base Value: {n.base_value}",
        ]
        if n.metadata:
            lines.append(f"  Metadata: {json.dumps(n.metadata, indent=4)}")

        incoming = self._reverse_adj.get(node_id, [])
        if incoming:
            lines.append("  Incoming edges:")
            for eid in incoming:
                e = self.edges[eid]
                lines.append(
                    f"    <- {e.source} [{e.edge_type}] "
                    f"coeff={e.coefficient:.6f} "
                    f"(p={e.p_value}, sig={e.significance})"
                )

        outgoing = self._forward_adj.get(node_id, [])
        if outgoing:
            lines.append("  Outgoing edges:")
            for eid in outgoing:
                e = self.edges[eid]
                lines.append(
                    f"    -> {e.target} [{e.edge_type}] "
                    f"coeff={e.coefficient:.6f}"
                )

        return "\n".join(lines)

    def summary(self) -> str:
        lines = [
            f"{'=' * 70}",
            f"EnergyPriceDAG: {self.region}",
            f"{'=' * 70}",
            f"Description: {self.description}",
            f"Nodes: {len(self.nodes)}  |  Edges: {len(self.edges)}",
            "",
        ]

        # Group nodes by type
        by_type: Dict[str, List[Node]] = defaultdict(list)
        for n in self.nodes.values():
            by_type[n.node_type].append(n)

        type_order = [
            "upstream_supply", "upstream_demand", "upstream_storage",
            "seasonal", "moderator", "structural_shifter", "derived",
            "disruption_weather", "disruption_geopolitical", "disruption_market",
            "gas_price", "elec_wholesale", "elec_retail",
        ]
        for t in type_order:
            nodes = by_type.get(t, [])
            if nodes:
                lines.append(f"  [{t.upper()}] ({len(nodes)} nodes)")
                for n in sorted(nodes, key=lambda x: x.node_id):
                    val_str = f" = {n.value:.4f}" if n.value is not None else ""
                    lines.append(f"    {n.node_id:40s} {n.unit:12s}{val_str}")
                lines.append("")

        # Edge summary by type
        edge_types = defaultdict(int)
        for e in self.edges.values():
            edge_types[e.edge_type] += 1
        lines.append("  Edge types:")
        for et, count in sorted(edge_types.items()):
            lines.append(f"    {et}: {count}")

        return "\n".join(lines)

    def validate(self) -> List[str]:
        """Check graph integrity. Returns list of warnings/errors."""
        issues = []

        # Check all edge endpoints exist
        for eid, e in self.edges.items():
            if e.source not in self.nodes:
                issues.append(f"Edge {eid}: source '{e.source}' not in nodes")
            if e.target not in self.nodes:
                issues.append(f"Edge {eid}: target '{e.target}' not in nodes")

        # Check non-input nodes have incoming edges
        for nid, n in self.nodes.items():
            if not n.is_input:
                incoming = self._reverse_adj.get(nid, [])
                causal_incoming = [
                    eid for eid in incoming
                    if self.edges[eid].edge_type in ("causal", "structural_shift")
                ]
                if not causal_incoming and n.base_value == 0.0:
                    issues.append(
                        f"Non-input node '{nid}' has no incoming causal/structural edges "
                        f"and base_value=0"
                    )

        # Check for cycles
        try:
            self._topological_sort()
        except ValueError as exc:
            issues.append(str(exc))

        # Check moderating edges reference valid target edges
        for eid, e in self.edges.items():
            if e.edge_type == "moderating" and e.target_edge_id:
                if e.target_edge_id not in self.edges:
                    issues.append(
                        f"Moderating edge {eid}: target_edge_id "
                        f"'{e.target_edge_id}' not found"
                    )

        return issues


# =============================================================================
# HELPER: Add upstream nodes shared by both ERCOT and CAISO
# =============================================================================

def _add_upstream_nodes(g: EnergyPriceDAG) -> None:
    """Add all upstream nodes and edges into henry_hub."""

    # ---- COST_SUPPLY (group R²=0.49) ----

    g.add_node("gas_rigs", "upstream_supply", "Gas Rig Count",
               unit="count", is_input=True,
               metadata={"group": "COST_SUPPLY", "group_r2": 0.494})
    g.add_node("oil_rigs", "upstream_supply", "Oil Rig Count",
               unit="count", is_input=True,
               metadata={"group": "COST_SUPPLY"})
    g.add_node("lng_exports_bcfd", "upstream_demand", "LNG Exports",
               unit="Bcf/d", is_input=True,
               metadata={"group": "COST_SUPPLY",
                         "mechanism": "LNG exports tighten domestic supply"})
    g.add_node("net_exports_bcfd", "upstream_supply", "Net Gas Exports",
               unit="Bcf/d", is_input=True,
               metadata={"group": "COST_SUPPLY"})

    # COST_SUPPLY structural shifters
    g.add_node("shale_era", "structural_shifter",
               "Shale Production Surge (2009+)",
               unit="binary", is_input=True,
               metadata={"group": "COST_SUPPLY", "start_date": "2009-01-01",
                         "mechanism": "Increased supply from shale revolution lowered prices"})
    g.add_node("us_net_exporter", "structural_shifter",
               "US Net Gas Exporter (2017+)",
               unit="binary", is_input=True,
               metadata={"group": "COST_SUPPLY", "start_date": "2017-09-01"})
    g.add_node("post_lng_exports", "structural_shifter",
               "LNG Exports Begin (2016+)",
               unit="binary", is_input=True,
               metadata={"group": "COST_SUPPLY", "start_date": "2016-02-01"})
    g.add_node("post_lng_pause", "structural_shifter",
               "DOE LNG Pause (2024+)",
               unit="binary", is_input=True,
               metadata={"group": "COST_SUPPLY", "start_date": "2024-01-26"})

    # ---- DEMAND_DRIVERS (group R²=0.32) ----

    g.add_node("electric_power_bcfd", "upstream_demand",
               "Power Sector Gas Demand",
               unit="Bcf/d", is_input=True,
               metadata={"group": "DEMAND_DRIVERS", "group_r2": 0.320,
                         "mean": 30.0,
                         "mechanism": "Higher power sector gas demand raises prices"})
    g.add_node("residential_bcfd", "upstream_demand",
               "Residential Gas Demand",
               unit="Bcf/d", is_input=True,
               metadata={"group": "DEMAND_DRIVERS"})
    g.add_node("commercial_bcfd", "upstream_demand",
               "Commercial Gas Demand",
               unit="Bcf/d", is_input=True,
               metadata={"group": "DEMAND_DRIVERS"})
    g.add_node("industrial_bcfd", "upstream_demand",
               "Industrial Gas Demand",
               unit="Bcf/d", is_input=True,
               metadata={"group": "DEMAND_DRIVERS",
                         "mechanism": "Negative coeff reflects demand destruction at high prices"})

    # Derived demand nodes
    g.add_node("power_share_of_gas", "derived",
               "Power Sector Share of Total Gas",
               unit="%", is_input=True,
               metadata={"group": "DEMAND_DRIVERS",
                         "derivation": "electric_power_bcfd / total_bcfd * 100"})
    g.add_node("heating_demand", "derived",
               "Heating Demand (Residential + Commercial)",
               unit="Bcf/d", is_input=True,
               metadata={"group": "DEMAND_DRIVERS",
                         "derivation": "residential_bcfd + commercial_bcfd"})
    g.add_node("base_demand", "derived",
               "Base Demand (Industrial + Power)",
               unit="Bcf/d", is_input=True,
               metadata={"group": "DEMAND_DRIVERS",
                         "derivation": "industrial_bcfd + electric_power_bcfd"})

    # Seasonal
    g.add_node("is_summer", "seasonal", "Summer Indicator (Jun-Aug)",
               unit="binary", is_input=True,
               metadata={"group": "DEMAND_DRIVERS"})
    g.add_node("is_winter", "seasonal", "Winter Indicator (Dec-Feb)",
               unit="binary", is_input=True,
               metadata={"group": "DEMAND_DRIVERS"})

    # ---- GENERATION_MIX (group R²=0.46) ----

    g.add_node("gas_share_pct", "moderator", "Gas Generation Share",
               unit="%", is_input=True,
               metadata={"group": "GENERATION_MIX", "group_r2": 0.456,
                         "current_value_2024": 43,
                         "moderator_baseline": 42.0,
                         "corr_with_elec": 0.809,
                         "mechanism": "Higher gas share increases electricity sensitivity to gas"})
    g.add_node("coal_share_pct", "moderator", "Coal Generation Share",
               unit="%", is_input=True,
               metadata={"group": "GENERATION_MIX",
                         "current_value_2024": 16,
                         "corr_with_elec": -0.723})
    g.add_node("nuclear_share_pct", "moderator", "Nuclear Generation Share",
               unit="%", is_input=True,
               metadata={"group": "GENERATION_MIX",
                         "current_value_2024": 18})
    g.add_node("renewable_share_pct", "moderator", "Renewable Generation Share",
               unit="%", is_input=True,
               metadata={"group": "GENERATION_MIX",
                         "current_value_2024": 22,
                         "mechanism": "Renewables reduce gas burn but add system costs"})
    g.add_node("gas_coal_ratio", "derived", "Gas-to-Coal Generation Ratio",
               unit="ratio", is_input=True,
               metadata={"group": "GENERATION_MIX",
                         "derivation": "gas_share_pct / coal_share_pct"})
    g.add_node("clean_share", "derived",
               "Clean Energy Share (Nuclear + Renewable)",
               unit="%", is_input=True,
               metadata={"group": "GENERATION_MIX",
                         "derivation": "nuclear_share_pct + renewable_share_pct"})

    # Generation mix structural shifters
    g.add_node("post_coal_decline", "structural_shifter",
               "Post-Coal Decline Era (2010+)",
               unit="binary", is_input=True,
               metadata={"group": "GENERATION_MIX", "start_date": "2010-01-01"})
    g.add_node("post_major_coal_ret", "structural_shifter",
               "Post-MATS Coal Retirements (2015+)",
               unit="binary", is_input=True,
               metadata={"group": "GENERATION_MIX", "start_date": "2015-04-16",
                         "mechanism": "Major coal retirements after MATS shifted price dynamics"})

    # ---- PERMITTING (group R²=0.46) ----

    permitting_nodes = [
        ("post_epact2005_permitting", "EPAct 2005 FERC Lead Agency",
         0.494546, "2005-08-08"),
        ("post_fra2023", "Fiscal Responsibility Act 2yr EIS Limit",
         -0.429160, "2023-06-03"),
        ("post_certificate_policy", "FERC Certificate Policy Statement",
         1.520348, "1999-09-15"),
        ("post_mvp_approval", "MVP Congressional Approval",
         -0.429160, "2023-06-03"),
        ("post_order2003", "FERC Order 2003 Interconnection",
         1.130025, "2003-07-24"),
        ("post_order2023", "FERC Order 2023 Queue Reform",
         0.175824, "2023-07-28"),
        ("post_order1000", "FERC Order 1000 Transmission Planning",
         -0.296163, "2011-07-21"),
        ("post_order1920", "FERC Order 1920 Transmission Reform",
         0.212900, "2024-05-13"),
        ("post_401_battles", "NY 401 Constitution Pipeline Denial",
         -0.553707, "2016-04-22"),
    ]
    for nid, label, coeff, start in permitting_nodes:
        g.add_node(nid, "structural_shifter", label,
                   unit="binary", is_input=True,
                   metadata={"group": "PERMITTING", "group_r2": 0.456,
                             "start_date": start, "full_model_coeff": coeff})

    # ---- POLICY_REGULATORY (group R²=0.47) ----

    policy_nodes = [
        ("post_epact2005", "EPAct 2005 Fracking Exemption",
         0.494546, "2005-08-08",
         "Fracking exemption enabled shale revolution"),
        ("post_mats", "MATS Mercury Rule",
         0.629877, "2015-04-16",
         "Mercury rules accelerated coal retirements, increased gas demand"),
        ("post_rggi", "RGGI Cap-and-Trade",
         -1.303501, "2009-01-01",
         "Carbon pricing shifted dispatch economics"),
        ("post_ca_cap", "CA Cap-and-Trade",
         0.725756, "2013-01-01",
         "California carbon pricing"),
        ("post_ira", "Inflation Reduction Act",
         -0.951432, "2022-08-16",
         "Clean energy incentives accelerated renewable deployment"),
        ("post_obbba", "One Big Beautiful Bill Act",
         1.200000, "2025-07-04",
         "Repeals/modifies most IRA clean energy tax credits, supports fossil fuel development"),
    ]
    for nid, label, coeff, start, mech in policy_nodes:
        meta = {"group": "POLICY_REGULATORY", "group_r2": 0.471,
                "start_date": start, "full_model_coeff": coeff,
                "mechanism": mech}
        if nid == "post_ira":
            meta["status"] = "SUPERSEDED"
            meta["superseded_by"] = "post_obbba"
            meta["superseded_date"] = "2025-07-04"
            meta["note"] = ("IRA clean energy provisions largely repealed/modified by OBBBA. "
                            "45Q (CCS) and 45Z (clean fuel) credits maintained. "
                            "Nuclear credits maintained with 10% bonus.")
        if nid == "post_obbba":
            meta["replaces"] = "post_ira"
            meta["signed_by"] = "President Trump"
            meta["key_provisions"] = [
                "Terminates EV tax credits by Sept 30, 2025",
                "Ends residential clean energy credit by Dec 31, 2025",
                "Accelerates wind/solar credit phaseout",
                "Maintains nuclear credits with 10% bonus",
                "Maintains carbon capture (45Q) credits",
                "Extends clean fuel credit (45Z) to 2029",
                "Creates Energy Dominance Financing Program ($1B)",
                "Postpones methane fees for 10 years",
            ]
            meta["effect_on_gas"] = "Positive - supports fossil fuel development"
            meta["effect_on_electricity"] = "Mixed - slows renewable deployment, maintains nuclear"
            meta["source"] = "decarbiq_relationship_graph.json v1.2, decarbiq_policy_database.csv"
        g.add_node(nid, "structural_shifter", label,
                   unit="binary", is_input=True, metadata=meta)

    # ---- OIL MARKET ----

    g.add_node("oil_price_wti", "upstream_supply", "WTI Crude Oil Price",
               unit="$/bbl", is_input=True,
               metadata={"contemporaneous_beta": 0.05875,
                         "contemporaneous_r2": 0.376,
                         "lagged_3m_beta": 0.05183,
                         "lagged_3m_r2": 0.303,
                         "associated_gas_bcfd_per_mbd_oil": 4.333,
                         "associated_gas_r2": 0.969,
                         "source": "crude_oil_params.json"})

    # ---- STORAGE ----

    g.add_node("storage_zscore", "upstream_storage",
               "US Storage Z-Score (deviation from seasonal norm)",
               unit="z-score", is_input=True,
               metadata={"mechanism": "High storage -> lower prices"})

    # ---- GENERATION CAPACITY DYNAMICS ----
    # (from updated decarbiq_relationship_graph.json v1.2)
    # These are qualitative nodes without regression coefficients yet.
    # They capture structural supply-side dynamics beyond the Ridge model.

    g.add_node("gas_capacity_additions_gw", "upstream_demand",
               "Gas Plant Capacity Additions",
               unit="GW", is_input=True,
               metadata={"group": "GENERATION_CAPACITY",
                         "data_source": "EIA Form 860",
                         "lag_months": 12,
                         "mechanism": "New gas plants increase generation capacity and future gas demand",
                         "direction": "positive on gas price",
                         "note": "No regression coefficient yet — qualitative relationship"})

    g.add_node("coal_retirements_gw", "upstream_demand",
               "Coal Plant Retirements",
               unit="GW", is_input=True,
               metadata={"group": "GENERATION_CAPACITY",
                         "data_source": "EIA Form 860",
                         "structural_breaks": ["2015-01-01", "2020-01-01"],
                         "mechanism": "Coal retirements shift load to gas plants, increasing gas demand",
                         "direction": "positive on gas price",
                         "note": "Accelerated post-MATS and during COVID. No regression coefficient yet."})

    g.add_node("gas_capacity_factor_pct", "upstream_demand",
               "Gas Capacity Factor",
               unit="%", is_input=True,
               metadata={"group": "GENERATION_CAPACITY",
                         "typical_range": {"ccgt": "40-60%", "peaker": "5-15%"},
                         "mechanism": "Higher capacity factors = more gas burn per installed MW",
                         "seasonal_pattern": "Higher in summer (cooling) and winter (heating)",
                         "direction": "positive on gas price",
                         "note": "Rising CFs signal dispatch economics favor gas. No regression coefficient yet."})

    # ---- DISRUPTIONS: Weather Events ----
    # (from geopolitical_regression_results.json, R²=0.315, n=336)

    g.add_node("hurricane_active", "disruption_weather",
               "Hurricane Active (Gulf Coast)",
               unit="binary", is_input=True,
               metadata={"group": "DISRUPTION_WEATHER",
                         "n_events": 15, "date_range": "1998-10 to 2021-10",
                         "mechanism": "Gulf hurricanes disrupt offshore gas production and onshore processing",
                         "source": "weather_disruptions.csv"})

    g.add_node("hurricane_major", "disruption_weather",
               "Major Hurricane (Cat 3+)",
               unit="binary", is_input=True,
               metadata={"group": "DISRUPTION_WEATHER",
                         "n_events": 8, "date_range": "2005-09 to 2008-10",
                         "mechanism": "Cat 3+ hurricanes cause severe supply disruptions (Katrina/Rita peak +$5.65/MMBtu)",
                         "data_gap": "Missing Laura (2020) and Ida (2021) — needs coding update",
                         "source": "weather_disruptions.csv"})

    g.add_node("polar_vortex_event", "disruption_weather",
               "Polar Vortex Event",
               unit="binary", is_input=True,
               metadata={"group": "DISRUPTION_WEATHER",
                         "n_events": 0,
                         "mechanism": "Extreme cold causes demand spikes and wellhead freeze-offs",
                         "data_gap": "ALL ZEROS in dataset — needs coding (Uri 2021, Vortex 2014, Elliott 2022, Heather 2024)",
                         "known_events": ["2014-01 (HH peak $7.90)", "2021-02 (HH peak $23.86, ERCOT $9000/MWh)",
                                          "2022-12 (Elliott)", "2024-01 (Heather)"],
                         "source": "weather_disruptions.csv"})

    # ---- DISRUPTIONS: Geopolitical Events ----

    g.add_node("war_active", "disruption_geopolitical",
               "Active War (Major Oil Region)",
               unit="binary", is_input=True,
               metadata={"group": "DISRUPTION_GEOPOLITICAL",
                         "n_months_active": 121, "date_range": "2003-04 to 2024-12",
                         "mechanism": "Wars in oil-producing regions affect global energy supply expectations",
                         "source": "geopolitical_disruptions.csv"})

    g.add_node("russia_gas_dispute", "disruption_geopolitical",
               "Russia Gas Dispute",
               unit="binary", is_input=True,
               metadata={"group": "DISRUPTION_GEOPOLITICAL",
                         "n_events": 2, "dates": ["2006-01", "2009-01"],
                         "mechanism": "Russia-Ukraine gas transit disputes affected global gas pricing",
                         "source": "geopolitical_disruptions.csv"})

    g.add_node("iran_sanctions_active", "disruption_geopolitical",
               "Iran Sanctions Active",
               unit="binary", is_input=True,
               metadata={"group": "DISRUPTION_GEOPOLITICAL",
                         "n_months_active": 128, "date_range": "2012-01 to 2024-12",
                         "phases": ["Initial 2012", "JCPOA relief 2016", "Max Pressure 2018", "2.0 (2025)"],
                         "source": "sanctions_disruptions.csv"})

    g.add_node("venezuela_sanctions_active", "disruption_geopolitical",
               "Venezuela Sanctions Active",
               unit="binary", is_input=True,
               metadata={"group": "DISRUPTION_GEOPOLITICAL",
                         "n_months_active": 88, "date_range": "2017-09 to 2024-12",
                         "mechanism": "Venezuela sanctions reduce global heavy crude supply",
                         "source": "sanctions_disruptions.csv"})

    g.add_node("russia_sanctions_active", "disruption_geopolitical",
               "Russia Sanctions Active",
               unit="binary", is_input=True,
               metadata={"group": "DISRUPTION_GEOPOLITICAL",
                         "n_months_active": 129, "date_range": "2014-04 to 2024-12",
                         "phases": ["Crimea 2014", "Full invasion 2022", "EU gas phase-out 2025-2027"],
                         "mechanism": "Russia sanctions redirected global gas/oil trade flows",
                         "source": "sanctions_disruptions.csv"})

    # ---- DISRUPTIONS: Market Conditions ----

    g.add_node("lng_tight_market", "disruption_market",
               "LNG Tight Market",
               unit="binary", is_input=True,
               metadata={"group": "DISRUPTION_MARKET",
                         "n_months_active": 48, "date_range": "2017-01 to 2022-12",
                         "mechanism": "Global LNG demand exceeding supply capacity drives arbitrage premium",
                         "source": "lng_competition.csv"})

    # ---- DISRUPTIONS: IRA Temporal Window ----
    # ira_era represents the period when IRA was actively in effect (before OBBBA repeal)

    g.add_node("ira_era", "structural_shifter",
               "IRA Active Era (2022-09 to OBBBA)",
               unit="binary", is_input=True,
               metadata={"group": "POLICY_REGULATORY",
                         "start_date": "2022-09-01",
                         "end_date": "2025-07-04",
                         "n_months": 28,
                         "mechanism": "Temporal window when IRA clean energy incentives were fully active",
                         "note": "Unlike post_ira (permanent structural break), ira_era captures the limited "
                                 "window before OBBBA repealed most IRA provisions. No regression coefficient yet.",
                         "source": "master_regression_dataset.csv"})

    # ---- CENTRAL: Henry Hub ----

    g.add_node("henry_hub", "gas_price", "Henry Hub Gas Price",
               unit="$/MMBtu", is_input=False, base_value=0.0,
               metadata={"mean_1997_2025": 4.18, "std": 2.14,
                         "full_model_r2": 0.761,
                         "full_model_mae": 0.714,
                         "n_variables": 45})

    # =================================================================
    # EDGES: Upstream -> Henry Hub (from full_model_coefficients.csv)
    # =================================================================

    # COST_SUPPLY causal edges
    g.add_edge("gas_rigs", "henry_hub", "causal",
               coefficient=-0.008048, description="More gas rigs -> future supply -> lower price",
               source_file="full_model_coefficients.csv")
    g.add_edge("oil_rigs", "henry_hub", "causal",
               coefficient=0.008183, description="Oil rigs proxy for associated gas",
               source_file="full_model_coefficients.csv")
    g.add_edge("lng_exports_bcfd", "henry_hub", "causal",
               coefficient=0.118665,
               description="LNG exports tighten domestic supply",
               source_file="full_model_coefficients.csv")
    g.add_edge("net_exports_bcfd", "henry_hub", "causal",
               coefficient=0.323870,
               description="Net exports reduce domestic supply",
               source_file="full_model_coefficients.csv")

    # COST_SUPPLY structural shifts
    g.add_edge("shale_era", "henry_hub", "structural_shift",
               coefficient=-1.303501, start_date="2009-01-01",
               description="Shale revolution lowered gas prices by ~$1.30/MMBtu",
               source_file="full_model_coefficients.csv")
    g.add_edge("us_net_exporter", "henry_hub", "structural_shift",
               coefficient=-0.035763, start_date="2017-09-01",
               source_file="full_model_coefficients.csv")
    g.add_edge("post_lng_exports", "henry_hub", "structural_shift",
               coefficient=0.772721, start_date="2016-02-01",
               description="LNG exports tighten domestic balance",
               source_file="full_model_coefficients.csv")
    g.add_edge("post_lng_pause", "henry_hub", "structural_shift",
               coefficient=-0.502812, start_date="2024-01-26",
               description="DOE LNG permitting pause eased price pressure",
               source_file="full_model_coefficients.csv")

    # DEMAND_DRIVERS causal edges
    g.add_edge("electric_power_bcfd", "henry_hub", "causal",
               coefficient=0.074661,
               description="Power sector demand pulls gas prices up",
               source_file="full_model_coefficients.csv")
    g.add_edge("residential_bcfd", "henry_hub", "causal",
               coefficient=0.087653,
               description="Residential heating demand",
               source_file="full_model_coefficients.csv")
    g.add_edge("commercial_bcfd", "henry_hub", "causal",
               coefficient=-0.058090,
               description="Commercial demand (negative in multivar context)",
               source_file="full_model_coefficients.csv")
    g.add_edge("industrial_bcfd", "henry_hub", "causal",
               coefficient=-0.234922,
               description="Industrial demand destruction at high prices",
               source_file="full_model_coefficients.csv")
    g.add_edge("power_share_of_gas", "henry_hub", "causal",
               coefficient=0.083947,
               source_file="full_model_coefficients.csv")
    g.add_edge("heating_demand", "henry_hub", "causal",
               coefficient=0.029563,
               description="Winter heating drives seasonal spikes",
               source_file="full_model_coefficients.csv")
    g.add_edge("base_demand", "henry_hub", "causal",
               coefficient=-0.160261,
               source_file="full_model_coefficients.csv")
    g.add_edge("is_summer", "henry_hub", "causal",
               coefficient=-0.248043,
               description="Summer has lower heating demand",
               source_file="full_model_coefficients.csv")
    g.add_edge("is_winter", "henry_hub", "causal",
               coefficient=-0.138529,
               source_file="full_model_coefficients.csv")

    # GENERATION_MIX causal edges
    g.add_edge("gas_share_pct", "henry_hub", "causal",
               coefficient=-0.111923,
               description="Higher gas share correlates with lower gas prices (shale effect)",
               source_file="full_model_coefficients.csv")
    g.add_edge("coal_share_pct", "henry_hub", "causal",
               coefficient=0.124326,
               description="Higher coal share -> less gas demand -> higher gas prices (pre-shale era)",
               source_file="full_model_coefficients.csv")
    g.add_edge("nuclear_share_pct", "henry_hub", "causal",
               coefficient=-0.177932,
               source_file="full_model_coefficients.csv")
    g.add_edge("renewable_share_pct", "henry_hub", "causal",
               coefficient=0.080428,
               description="Renewables reduce gas burn but coefficient positive in multivar context",
               source_file="full_model_coefficients.csv")
    g.add_edge("gas_coal_ratio", "henry_hub", "causal",
               coefficient=-0.450495,
               description="Higher gas/coal ratio linked to lower gas prices",
               source_file="full_model_coefficients.csv")
    g.add_edge("clean_share", "henry_hub", "causal",
               coefficient=-0.097504,
               source_file="full_model_coefficients.csv")

    # GENERATION_MIX structural shifts
    g.add_edge("post_coal_decline", "henry_hub", "structural_shift",
               coefficient=-0.028146, start_date="2010-01-01",
               source_file="full_model_coefficients.csv")
    g.add_edge("post_major_coal_ret", "henry_hub", "structural_shift",
               coefficient=0.629877, start_date="2015-04-16",
               description="Coal retirements shifted load to gas, raising demand",
               source_file="full_model_coefficients.csv")

    # PERMITTING structural shifts
    for nid, label, coeff, start in permitting_nodes:
        g.add_edge(nid, "henry_hub", "structural_shift",
                   coefficient=coeff, start_date=start,
                   source_file="full_model_coefficients.csv")

    # POLICY_REGULATORY structural shifts
    for nid, label, coeff, start, mech in policy_nodes:
        src = ("decarbiq_relationship_graph.json" if nid == "post_obbba"
               else "full_model_coefficients.csv")
        g.add_edge(nid, "henry_hub", "structural_shift",
                   coefficient=coeff, start_date=start,
                   description=mech,
                   source_file=src)

    # OIL MARKET
    g.add_edge("oil_price_wti", "henry_hub", "causal",
               coefficient=0.058753,
               r_squared=0.376,
               description="+$1/bbl oil -> +$0.06/MMBtu gas (contemporaneous)",
               lag_months=0,
               source_file="crude_oil_params.json")

    # DISRUPTION: Weather events -> Henry Hub
    # (from geopolitical_regression_results.json, separate model R²=0.315, n=336)
    # These coefficients are additive adjustments from a standalone event regression.
    # intercept = $4.55/MMBtu (not used — Ridge model provides the baseline).

    g.add_edge("hurricane_active", "henry_hub", "causal",
               coefficient=-0.275865,
               r_squared=0.315,
               n_observations=336,
               description="Active hurricane (any): -$0.28/MMBtu (supply disruption offset by demand destruction)",
               source_file="geopolitical_regression_results.json")

    g.add_edge("hurricane_major", "henry_hub", "causal",
               coefficient=5.651525,
               r_squared=0.315,
               n_observations=336,
               description="Major hurricane (Cat 3+): +$5.65/MMBtu (severe supply shock, Katrina/Rita scale)",
               source_file="geopolitical_regression_results.json")

    g.add_edge("polar_vortex_event", "henry_hub", "causal",
               coefficient=0.0,
               r_squared=0.315,
               n_observations=336,
               description="Polar vortex: coefficient=0 (DATA GAP — no events coded in dataset yet)",
               source_file="geopolitical_regression_results.json")

    # DISRUPTION: Geopolitical events -> Henry Hub

    g.add_edge("war_active", "henry_hub", "causal",
               coefficient=-0.224274,
               r_squared=0.315,
               n_observations=336,
               description="Active war in oil region: -$0.22/MMBtu (US insulated from direct supply loss)",
               source_file="geopolitical_regression_results.json")

    g.add_edge("russia_gas_dispute", "henry_hub", "causal",
               coefficient=-0.276766,
               r_squared=0.315,
               n_observations=336,
               description="Russia gas transit dispute: -$0.28/MMBtu (2006, 2009 events)",
               source_file="geopolitical_regression_results.json")

    g.add_edge("iran_sanctions_active", "henry_hub", "causal",
               coefficient=-0.199853,
               r_squared=0.315,
               n_observations=336,
               description="Iran sanctions: -$0.20/MMBtu (reduced global competition for LNG)",
               source_file="geopolitical_regression_results.json")

    g.add_edge("venezuela_sanctions_active", "henry_hub", "causal",
               coefficient=-0.076157,
               r_squared=0.315,
               n_observations=336,
               description="Venezuela sanctions: -$0.08/MMBtu (minor US gas market impact)",
               source_file="geopolitical_regression_results.json")

    g.add_edge("russia_sanctions_active", "henry_hub", "causal",
               coefficient=-1.517389,
               r_squared=0.315,
               n_observations=336,
               description="Russia sanctions: -$1.52/MMBtu (Europe pivot to LNG raised global demand, "
                           "but negative coefficient may capture post-2022 US oversupply response)",
               source_file="geopolitical_regression_results.json")

    # DISRUPTION: Market conditions -> Henry Hub

    g.add_edge("lng_tight_market", "henry_hub", "causal",
               coefficient=1.451884,
               r_squared=0.315,
               n_observations=336,
               description="LNG tight market: +$1.45/MMBtu (global LNG demand > supply capacity)",
               source_file="geopolitical_regression_results.json")


# =============================================================================
# FACTORY: ERCOT GRAPH
# =============================================================================

def build_ercot_graph() -> EnergyPriceDAG:
    """Build the ERCOT (Texas) relationship graph with all regression coefficients."""

    g = EnergyPriceDAG(
        region="ERCOT",
        description=(
            "Texas/ERCOT energy price DAG. "
            "Upstream (45 Ridge vars R²=0.76 + 9 disruption vars R²=0.315) -> Henry Hub -> "
            "ERCOT Wholesale (R²=0.653) -> TX Retail (R²=0.298). "
            "Gas is the marginal price setter (42% share). Energy-only market with high volatility."
        ),
    )

    # Add shared upstream
    _add_upstream_nodes(g)

    # ---- ERCOT-specific downstream nodes ----

    g.add_node("ercot_wholesale", "elec_wholesale",
               "ERCOT Wholesale Electricity",
               unit="$/MWh", is_input=False,
               base_value=8.003964,  # intercept from model_params.json
               metadata={
                   "r_squared": 0.6527,
                   "residual_std": 6.890,
                   "implied_heat_rate": 7.0,
                   "scarcity_threshold_mwh": 65.48,
                   "scarcity_n_events": 9,
                   "scarcity_events": [
                       "2021-02", "2022-05", "2022-06", "2022-07",
                       "2022-08", "2022-09", "2023-06", "2023-08", "2023-09"
                   ],
                   "scarcity_probability_annual": 0.15,
                   "scarcity_peak_price": 1485.49,
                   "quantile_regression": {
                       "P10": {"intercept": 1.963, "henry_hub_beta": 6.964},
                       "P50": {"intercept": 8.285, "henry_hub_beta": 6.457},
                       "P90": {"intercept": 18.194, "henry_hub_beta": 8.018},
                   },
                   "seasonal_factors_mwh": {
                       "1": -3.404, "2": -12.349, "3": -9.560,
                       "4": -1.497, "5": 0.386, "6": 5.272,
                       "7": 9.095, "8": 6.077, "9": 2.408,
                       "10": 7.268, "11": 2.412, "12": 0.149,
                   },
                   "market_structure": "energy_only",
                   "bivariate_beta": 9.011,
                   "bivariate_r2": 0.641,
                   "bivariate_source": "wholesale_regression_results.csv",
                   "source": "decarbiq_model_params.json",
               })

    g.add_node("tx_retail", "elec_retail",
               "Texas Industrial Retail Electricity",
               unit="cents/kWh", is_input=False,
               base_value=8.386323,  # intercept from monthly_regression_results.csv
               metadata={
                   "r_squared": 0.298,
                   "beta_cents_kwh": 0.258,
                   "beta_mwh": 2.581,
                   "mean_2024": 9.79,
                   "henry_hub_correlation": 0.679,
                   "source": "wholesale_regression_results.csv",
               })

    # ---- Edges: Henry Hub -> ERCOT Wholesale (multivariate model) ----

    g.add_edge("henry_hub", "ercot_wholesale", "causal",
               coefficient=7.012373,
               p_value=0.0001,
               r_squared=0.6527,
               significance="***",
               description="+$1/MMBtu gas -> +$7.01/MWh wholesale electricity",
               source_file="decarbiq_model_params.json",
               edge_id="henry_hub_to_ercot_wholesale")

    g.add_edge("storage_zscore", "ercot_wholesale", "causal",
               coefficient=0.143546,
               p_value=0.89,
               significance="NS",
               description="Storage z-score effect (not significant for ERCOT)",
               source_file="decarbiq_model_params.json")

    g.add_edge("is_summer", "ercot_wholesale", "causal",
               coefficient=5.572371,
               p_value=0.04,
               significance="*",
               description="Summer premium +$5.57/MWh (AC demand)",
               source_file="decarbiq_model_params.json",
               edge_id="is_summer_to_ercot_wholesale")

    g.add_edge("is_winter", "ercot_wholesale", "causal",
               coefficient=-2.156947,
               p_value=0.41,
               significance="NS",
               description="Winter effect (not significant for ERCOT)",
               source_file="decarbiq_model_params.json",
               edge_id="is_winter_to_ercot_wholesale")

    # ---- Edge: Henry Hub -> TX Retail ----

    g.add_edge("henry_hub", "tx_retail", "causal",
               coefficient=2.581266,
               p_value=0.0000065,
               r_squared=0.298,
               significance="***",
               n_observations=60,
               description="+$1/MMBtu gas -> +$2.58/MWh (+0.258 cents/kWh) retail",
               source_file="wholesale_regression_results.csv")

    # ---- Moderating edge: gas_share moderates gas->electricity link ----

    g.add_edge("gas_share_pct", "ercot_wholesale", "moderating",
               coefficient=1.0,
               target_edge_id="henry_hub_to_ercot_wholesale",
               description="Gas generation share moderates gas-electricity passthrough. "
                           "Value 1.0 = baseline (42%). 1.1 = 10% above baseline.",
               source_file="ercot_caiso_correlations.csv",
               edge_id="gas_share_mod_ercot_wholesale")

    # ---- Correlation edges: direct correlations to ERCOT wholesale ----
    # (from correlation_results_no_outliers.csv, n=58, outliers removed)

    ercot_correlations = [
        ("gas_rigs", 0.532, 0.283, 1.7e-5, "***", 58),
        ("electric_power_bcfd", 0.410, 0.168, 0.0014, "**", 58),
        ("power_share_of_gas", 0.435, 0.189, 0.00065, "***", 58),
        ("coal_share_pct", 0.264, 0.070, 0.045, "*", 58),
        ("residential_bcfd", -0.352, 0.124, 0.0067, "**", 58),
        ("industrial_bcfd", -0.265, 0.070, 0.045, "*", 58),
    ]
    for feat, corr, r2, pval, sig, n in ercot_correlations:
        g.add_edge(feat, "ercot_wholesale", "correlation",
                   coefficient=corr,
                   r_squared=r2,
                   p_value=pval,
                   significance=sig,
                   n_observations=n,
                   description=f"Bivariate correlation (no outliers): r={corr:.3f}",
                   source_file="correlation_results_no_outliers.csv")

    # Additional correlations from ercot_caiso_correlations.csv
    ercot_cross = [
        ("lng_exports_bcfd", 0.133, "correlation with ERCOT wholesale"),
    ]
    for feat, corr, desc in ercot_cross:
        g.add_edge(feat, "ercot_wholesale", "correlation",
                   coefficient=corr,
                   description=desc,
                   source_file="ercot_caiso_correlations.csv")

    # ---- Correlation edges: industrial retail ----
    # (from correlation_results.csv, n=288)

    industrial_correlations = [
        ("gas_share_pct", 0.809, 0.654, "***", 288),
        ("electric_power_bcfd", 0.804, 0.647, "***", 288),
        ("net_exports_bcfd", 0.734, 0.539, "***", 288),
        ("coal_share_pct", -0.723, 0.522, "***", 288),
        ("lng_exports_bcfd", 0.634, 0.402, "***", 288),
        ("industrial_bcfd", 0.393, 0.154, "***", 288),
        ("residential_bcfd", -0.238, 0.057, "***", 288),
        ("gas_rigs", -0.213, 0.045, "***", 288),
    ]
    for feat, corr, r2, sig, n in industrial_correlations:
        g.add_edge(feat, "tx_retail", "correlation",
                   coefficient=corr,
                   r_squared=r2,
                   significance=sig,
                   n_observations=n,
                   description=f"Bivariate correlation with TX industrial retail: r={corr:.3f}",
                   source_file="correlation_results.csv")

    return g


# =============================================================================
# FACTORY: CAISO GRAPH
# =============================================================================

def build_caiso_graph() -> EnergyPriceDAG:
    """Build the CAISO (California) relationship graph with all regression coefficients."""

    g = EnergyPriceDAG(
        region="CAISO",
        description=(
            "California/CAISO energy price DAG. "
            "Upstream (45 Ridge vars R²=0.76 + 9 disruption vars R²=0.315) -> Henry Hub -> "
            "CAISO Wholesale (R²=0.441). "
            "Gas is peaking-only (38% share). Duck curve dynamics dominate. "
            "Retail prices NOT driven by gas (R²<0.05) — wildfire, RPS, grid hardening dominate."
        ),
    )

    # Add shared upstream
    _add_upstream_nodes(g)

    # ---- CAISO-specific downstream nodes ----

    g.add_node("caiso_wholesale", "elec_wholesale",
               "CAISO Wholesale Electricity",
               unit="$/MWh", is_input=False,
               base_value=-32.874315,  # intercept from model_params.json
               metadata={
                   "r_squared": 0.4414,
                   "residual_std": 14.148,
                   "implied_heat_rate": 29.7,
                   "note_heat_rate": "Much higher than theoretical 7.0 due to peaker reliance and duck curve",
                   "scarcity_threshold_mwh": 91.42,
                   "scarcity_n_events": 2,
                   "scarcity_events": ["2022-12", "2023-01"],
                   "scarcity_prices": {"2022-12": 253.78, "2023-01": 138.72},
                   "quantile_regression": {
                       "P10": {"intercept": -35.147, "henry_hub_beta": 24.010},
                       "P50": {"intercept": -26.290, "henry_hub_beta": 27.527},
                       "P90": {"intercept": -52.320, "henry_hub_beta": 47.495},
                   },
                   "seasonal_factors_mwh": {
                       "1": 24.539, "2": -6.440, "3": -19.717,
                       "4": -25.934, "5": -24.547, "6": -9.821,
                       "7": 14.132, "8": 22.164, "9": -12.083,
                       "10": 8.423, "11": 10.675, "12": 4.240,
                   },
                   "market_structure": "regulated_retail",
                   "bivariate_beta": 13.274,
                   "bivariate_r2": 0.242,
                   "bivariate_source": "wholesale_regression_results.csv",
                   "note_retail": (
                       "CA retail electricity is NOT driven by gas prices (R²<0.05, p=0.56). "
                       "Non-fuel drivers dominate: wildfire risk/liability, RPS compliance costs, "
                       "grid hardening (PG&E undergrounding), duck curve dynamics."
                   ),
                   "non_fuel_drivers": [
                       "wildfire_risk", "rps_compliance",
                       "grid_hardening", "duck_curve",
                       "aliso_canyon_constraints",
                   ],
                   "source": "decarbiq_model_params.json",
               })

    # ---- Edges: Henry Hub -> CAISO Wholesale (multivariate model) ----

    g.add_edge("henry_hub", "caiso_wholesale", "causal",
               coefficient=29.691748,
               p_value=0.004,
               r_squared=0.4414,
               significance="**",
               description="+$1/MMBtu gas -> +$29.69/MWh wholesale (high due to duck curve)",
               source_file="decarbiq_model_params.json",
               edge_id="henry_hub_to_caiso_wholesale")

    g.add_edge("storage_zscore", "caiso_wholesale", "causal",
               coefficient=-6.293650,
               p_value=0.12,
               significance="NS (borderline)",
               description="High storage -> lower CAISO prices (borderline significant)",
               source_file="decarbiq_model_params.json")

    g.add_edge("is_summer", "caiso_wholesale", "causal",
               coefficient=6.792707,
               p_value=0.40,
               significance="NS",
               description="Summer effect (not significant in multivariate)",
               source_file="decarbiq_model_params.json",
               edge_id="is_summer_to_caiso_wholesale")

    g.add_edge("is_winter", "caiso_wholesale", "causal",
               coefficient=1.786686,
               p_value=0.86,
               significance="NS",
               description="Winter effect (not significant)",
               source_file="decarbiq_model_params.json",
               edge_id="is_winter_to_caiso_wholesale")

    # ---- Moderating edge: gas_share moderates gas->electricity link ----

    g.add_edge("gas_share_pct", "caiso_wholesale", "moderating",
               coefficient=1.0,
               target_edge_id="henry_hub_to_caiso_wholesale",
               description="Gas generation share moderates gas-electricity passthrough. "
                           "Value 1.0 = baseline (38%). Weaker than ERCOT due to peaking-only role.",
               source_file="ercot_caiso_correlations.csv",
               edge_id="gas_share_mod_caiso_wholesale")

    # ---- Correlation edges: direct correlations to CAISO wholesale ----
    # (from correlation_results_no_outliers.csv / correlation_results.csv)

    caiso_correlations = [
        ("coal_share_pct", 0.668, 0.446, 0.00014, "***", 27),
        ("gas_rigs", 0.557, 0.310, 0.0026, "**", 27),
        ("total_rigs", 0.577, 0.333, 0.0016, "**", 27),
        ("residential_bcfd", 0.485, 0.235, 0.010, "*", 27),
        ("lng_exports_bcfd", -0.626, 0.391, 0.00048, "***", 27),
        ("net_exports_bcfd", -0.625, 0.391, 0.00049, "***", 27),
    ]
    for feat, corr, r2, pval, sig, n in caiso_correlations:
        # total_rigs node may not exist; add it if needed
        if feat == "total_rigs" and feat not in g.nodes:
            g.add_node("total_rigs", "upstream_supply", "Total Rig Count",
                       unit="count", is_input=True,
                       metadata={"group": "COST_SUPPLY"})
        g.add_edge(feat, "caiso_wholesale", "correlation",
                   coefficient=corr,
                   r_squared=r2,
                   p_value=pval,
                   significance=sig,
                   n_observations=n,
                   description=f"Bivariate correlation: r={corr:.3f}",
                   source_file="correlation_results.csv")

    # ---- Cross-regional insights stored as metadata on CAISO ----
    # CA Industrial retail correlations (from ercot_caiso_correlations.csv)
    g.nodes["caiso_wholesale"].metadata["ca_industrial_correlations"] = {
        "henry_hub": -0.349,
        "power_demand": 0.862,
        "gas_gen_share": 0.862,
        "coal_gen_share": -0.807,
        "lng_exports": 0.847,
        "post_mats": 0.701,
        "post_ira": 0.729,
        "note": "CA industrial retail correlations show divergent behavior from TX"
    }

    return g


# =============================================================================
# MAIN DEMO
# =============================================================================

if __name__ == "__main__":
    print("Building ERCOT graph...")
    ercot = build_ercot_graph()

    print("Building CAISO graph...")
    caiso = build_caiso_graph()

    # Validate
    for g, name in [(ercot, "ERCOT"), (caiso, "CAISO")]:
        issues = g.validate()
        if issues:
            print(f"\n{name} validation issues:")
            for i in issues:
                print(f"  ! {i}")
        else:
            print(f"{name}: validated OK")

    # Print summaries
    print("\n" + ercot.summary())
    print("\n" + caiso.summary())

    # ---- ERCOT Demo: Current era scenario ----
    print("\n" + "=" * 70)
    print("ERCOT DEMO: Current Era (2024) Summer Scenario")
    print("=" * 70)

    # Current-era structural shifters (all active post their start dates)
    current_era = {
        # Cost/Supply
        "gas_rigs": 115,
        "oil_rigs": 500,
        "lng_exports_bcfd": 14.5,
        "net_exports_bcfd": 10.0,
        "oil_price_wti": 70.0,
        # Demand
        "electric_power_bcfd": 35.0,
        "residential_bcfd": 8.0,
        "commercial_bcfd": 5.0,
        "industrial_bcfd": 7.0,
        "power_share_of_gas": 43.0,
        "heating_demand": 13.0,
        "base_demand": 42.0,
        # Seasonal
        "is_summer": 1,
        "is_winter": 0,
        # Generation mix
        "gas_share_pct": 43.0,
        "coal_share_pct": 16.0,
        "nuclear_share_pct": 18.0,
        "renewable_share_pct": 22.0,
        "gas_coal_ratio": 2.69,
        "clean_share": 40.0,
        # Storage
        "storage_zscore": 0.0,
        # Structural shifters (all current-era active = 1)
        "shale_era": 1,
        "us_net_exporter": 1,
        "post_lng_exports": 1,
        "post_lng_pause": 1,
        "post_coal_decline": 1,
        "post_major_coal_ret": 1,
        "post_epact2005_permitting": 1,
        "post_fra2023": 1,
        "post_certificate_policy": 1,
        "post_mvp_approval": 1,
        "post_order2003": 1,
        "post_order2023": 1,
        "post_order1000": 1,
        "post_order1920": 1,
        "post_401_battles": 1,
        "post_epact2005": 1,
        "post_mats": 1,
        "post_rggi": 1,
        "post_ca_cap": 1,
        "post_ira": 1,
        "post_obbba": 1,
        # Generation capacity dynamics (qualitative, no regression edges yet)
        "gas_capacity_additions_gw": 10.0,
        "coal_retirements_gw": 5.0,
        "gas_capacity_factor_pct": 45.0,
        # Disruption events (current era = no active disruptions)
        "hurricane_active": 0,
        "hurricane_major": 0,
        "polar_vortex_event": 0,
        "war_active": 1,           # Russia-Ukraine conflict ongoing
        "russia_gas_dispute": 0,
        "iran_sanctions_active": 1, # Iran sanctions 2.0 active
        "venezuela_sanctions_active": 1,  # Venezuela sanctions active
        "russia_sanctions_active": 1,     # Russia sanctions active
        "lng_tight_market": 0,      # Market loosened post-2022
        # Policy temporal window
        "ira_era": 0,              # OBBBA repealed most IRA provisions
    }

    ercot.set_inputs(current_era)
    results = ercot.propagate()

    print(f"\n  Henry Hub:         ${results['henry_hub']:.2f}/MMBtu")
    print(f"  ERCOT Wholesale:   ${results['ercot_wholesale']:.2f}/MWh")
    print(f"  TX Retail:         {results['tx_retail']:.2f} cents/kWh")

    # Sensitivity analysis
    print("\n--- Sensitivity Analysis (per unit increase) ---")
    key_inputs = [
        ("gas_rigs", 10, "rigs"),
        ("lng_exports_bcfd", 1.0, "Bcf/d"),
        ("electric_power_bcfd", 1.0, "Bcf/d"),
        ("industrial_bcfd", 1.0, "Bcf/d"),
        ("oil_price_wti", 10.0, "$/bbl"),
        ("is_summer", -1, "(summer -> non-summer)"),
        ("hurricane_major", 1, "(Cat 3+ hurricane hits)"),
        ("lng_tight_market", 1, "(LNG market tightens)"),
    ]
    for inp, delta, unit_label in key_inputs:
        sens = ercot.sensitivity(inp, delta=delta, base_inputs=current_era)
        hh = sens.get("henry_hub", 0)
        ew = sens.get("ercot_wholesale", 0)
        print(f"  +{delta} {unit_label} {inp}:")
        print(f"    Henry Hub: {'+' if hh >= 0 else ''}{hh:.4f} $/MMBtu")
        print(f"    ERCOT Wholesale: {'+' if ew >= 0 else ''}{ew:.3f} $/MWh")

    # Path analysis
    print("\n--- Paths: lng_exports_bcfd -> ercot_wholesale ---")
    paths = ercot.all_paths("lng_exports_bcfd", "ercot_wholesale")
    for p in paths:
        print(ercot.explain_path(p))
        print()

    # ---- CAISO Demo ----
    print("\n" + "=" * 70)
    print("CAISO DEMO: Same upstream, different downstream")
    print("=" * 70)

    # Add total_rigs to current_era for CAISO
    current_era_caiso = dict(current_era)
    current_era_caiso["total_rigs"] = 615

    caiso.set_inputs(current_era_caiso)
    caiso_results = caiso.propagate()

    print(f"\n  Henry Hub:         ${caiso_results['henry_hub']:.2f}/MMBtu")
    print(f"  CAISO Wholesale:   ${caiso_results['caiso_wholesale']:.2f}/MWh")
    print(f"\n  (Note: CA retail NOT modeled — non-fuel costs dominate)")

    # Compare regions
    print("\n--- Regional Comparison ---")
    print(f"  ERCOT Wholesale:   ${results['ercot_wholesale']:.2f}/MWh  (R²=0.653)")
    print(f"  CAISO Wholesale:   ${caiso_results['caiso_wholesale']:.2f}/MWh  (R²=0.441)")
    print(f"  Gas->ERCOT coeff:  7.01 $/MWh per $/MMBtu (heat rate: 7.0)")
    print(f"  Gas->CAISO coeff:  29.69 $/MWh per $/MMBtu (heat rate: 29.7, duck curve)")

    # Export JSON
    ercot.to_json("/Users/Shadi/Downloads/decarbiq/ercot_graph.json")
    caiso.to_json("/Users/Shadi/Downloads/decarbiq/caiso_graph.json")
    print("\nExported: ercot_graph.json, caiso_graph.json")

    # Describe a key node
    print("\n--- Node Detail: henry_hub ---")
    print(ercot.describe_node("henry_hub"))
