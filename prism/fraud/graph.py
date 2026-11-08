"""Entity graph over deals and history (owner: Phase 5, FRD-02).

Contract: built from the DealState entities visible now plus the baseline; pure and
deterministic (sorted iteration, no randomness). Every EntityRef is re-canonicalized with
``canon_value`` so Read output and history keys always agree, whatever form Read emits.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Mapping, get_args

from prism.contract import EntityKind
from prism.engine.state import DealState, EntityRef
from prism.fraud.baseline import Baseline, DealProfile
from prism.fraud.canon import canon_value
from prism.fraud.rarity import is_strong, surprisal

ENTITY_KINDS = frozenset(get_args(EntityKind))


@dataclass(frozen=True)
class EntityWeight:
    """Rarity weight of one entity that appears in a current deal."""

    df: int  # history deals using it
    links: int  # current deals plus history deals carrying it
    bits: float  # surprisal of its history frequency
    strong: bool  # rare in history and links >= min_linked


@dataclass
class EntityGraph:
    deal_entities: dict[str, list[EntityRef]] = field(default_factory=dict)
    profiles: dict[str, DealProfile] = field(default_factory=dict)
    entity_deals: dict[tuple[str, str], list[str]] = field(default_factory=dict)
    entity_weights: dict[tuple[str, str], EntityWeight] = field(default_factory=dict)


def build_graph(states: Mapping[str, DealState], baseline: Baseline) -> EntityGraph:
    """Link current deals and history deals through canonical entities and weight each entity."""
    graph = EntityGraph()
    carriers: dict[tuple[str, str], set[str]] = {}
    for folder in sorted(states):
        state = states[folder]
        if state.error:
            continue
        seen: dict[tuple[str, str], EntityRef] = {}
        for ref in state.entities:
            if ref.kind not in ENTITY_KINDS:
                continue
            value = canon_value(ref.kind, ref.value)
            if value and (ref.kind, value) not in seen:
                seen[(ref.kind, value)] = EntityRef(ref.kind, value, ref.source_doc)
        keys = sorted(seen)
        graph.deal_entities[folder] = [seen[k] for k in keys]
        by_kind: dict[str, list[str]] = {}
        for kind, value in keys:
            by_kind.setdefault(kind, []).append(value)
            carriers.setdefault((kind, value), set()).add(folder)
        graph.profiles[folder] = DealProfile(
            deal_id=folder, entities={k: tuple(v) for k, v in by_kind.items()}
        )
    params = baseline.params
    n = baseline.n_effective()
    for key in sorted(carriers):
        kind, value = key
        history = baseline.deals_with(kind, value)
        graph.entity_deals[key] = sorted(carriers[key] | set(history))
        df = len(history)
        links = len(graph.entity_deals[key])
        graph.entity_weights[key] = EntityWeight(
            df=df,
            links=links,
            bits=surprisal(df, n),
            strong=is_strong(df, n, links, params.rho, params.min_linked),
        )
    return graph
