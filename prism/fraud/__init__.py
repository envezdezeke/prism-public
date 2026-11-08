"""FRAUD: history baseline, entity graph, signal families, hold decision (owner: Phase 5).

Module map (data flows top to bottom; see prism/engine/state.py for the hand-off table):

- canon: idempotent canonical keys per entity kind (names split into base and legal suffix)
- rarity: FraudParams from config plus the pure math (bits of surprise, percentiles, tails,
  beta-binomial shrinkage, entropy, KL, edit distance and token-set similarity)
- baseline: history index from deals.csv and monthly_deposits.csv, leave-one-out queries,
  vendor groups, deposit features, Calibration; ``build_baseline`` never raises on content
- graph: current deals plus history linked through canonical entities, with rarity weights
- signals: F1 (shared rare entity), F3 (lookalike vendor or account), F5 (deposit pattern)
- calibrate: startup thresholds (tau) from the clean calibration set, recorded for audit
- eval_baseline: time-split report on history alone
- decide: hold rule (stub until Phase 5 proper)
- graph_out: FraudGraph (UI contract I4) with rarity-weighted edges, evidence, first-visible times

Time-split report (writes out/baseline_eval.json and out/baseline_eval.md):

    python -m prism.fraud.eval_baseline --history data/participant/history --out-dir out
"""
