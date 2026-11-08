"""Canonicalization and pure rarity math. Expected numbers are computed by hand here."""
from __future__ import annotations

import math
from dataclasses import replace
from typing import get_args

import pytest

from prism.contract import EntityKind
from prism.engine.config import ConfigError, load_fraud_config
from prism.fraud import canon, rarity


# --------------------------------------------------------------------------- canon

def test_legal_suffix_variants_share_one_key():
    keys = {canon.split_legal_suffix(s) for s in ("Zeta Works, L.L.C.", "ZETA WORKS LLC", "zeta works llc")}
    assert len(keys) == 1
    k = keys.pop()
    assert (k.base, k.suffix, k.full) == ("zeta works", "llc", "zeta works llc")


def test_different_suffix_same_base():
    a, b = canon.split_legal_suffix("Zeta Works LLC"), canon.split_legal_suffix("Zeta Works Inc")
    assert a.base == b.base == "zeta works"
    assert a.suffix != b.suffix and a.full != b.full


def test_ampersand_and_suffix_only_names():
    k = canon.split_legal_suffix("Smith & Co")
    assert (k.base, k.suffix) == ("smith and", "co")
    assert canon.canon_text("A & B") == canon.canon_text("A and B")
    only = canon.split_legal_suffix("LLC")
    assert only.base == "llc" and only.suffix == "" and only.full == "llc"
    assert canon.split_legal_suffix("Acme Incorporated").suffix == "inc"
    assert canon.canon_text("O'Brien Ltd.") == "obrien ltd"


def test_phone_variants_collapse():
    for raw in ("(210) 555-0142", "210.555.0142", "1-210-555-0142"):
        assert canon.canon_phone(raw) == "2105550142"
    assert canon.canon_phone("") == ""
    assert canon.canon_phone(None) == ""


def test_address_variants_collapse_and_ignore_order():
    a = canon.canon_address("12 Main St., Suite 4")
    assert a == canon.canon_address("12 main street ste 4")
    assert canon.canon_address("Suite 4, 12 Main Street") == a


def test_canon_value_is_idempotent_for_every_kind():
    samples = ["Zeta Works, L.L.C.", "12 Main St., Suite 4", "(210) 555-0142", "AB-12 cd", "Smith & Co", "  "]
    for kind in get_args(EntityKind):
        for s in samples:
            once = canon.canon_value(kind, s)
            assert canon.canon_value(kind, once) == once, (kind, s)
    assert canon.canon_value("vendor", None) == ""
    assert canon.canon_value("vendor", "Zeta Works Inc.") == "zeta works inc"
    assert canon.canon_value("remit_account", "ab-12 3") == "ab123"


# --------------------------------------------------------------------------- rarity

def test_surprisal_values():
    n = 257
    for df, want in ((0, 8.011), (5, 5.43), (15, 4.01), (200, 0.36)):
        assert rarity.surprisal(df, n) == pytest.approx(want, abs=0.01)
        assert rarity.surprisal(df, n) == pytest.approx(-math.log2((df + 1) / (n + 1)))
    assert rarity.surprisal(0, 0) == 0.0
    assert rarity.p_hat(2, 9) == pytest.approx(0.3)


def test_is_strong_rules():
    assert rarity.is_strong(5, 257, 2, 0.02, 2)
    assert not rarity.is_strong(6, 257, 2, 0.02, 2)
    assert not rarity.is_strong(0, 257, 1, 0.02, 2)
    assert not rarity.is_strong(2, 257, 2, 0.02, 3)
    assert rarity.is_strong(2, 257, 3, 0.02, 3)
    assert not rarity.is_strong(0, 0, 5, 0.02, 2)


def test_outcome_discount():
    assert rarity.outcome_discount(2, 2, 0.5) == pytest.approx(0.625)
    assert rarity.outcome_discount(0, 2, 0.5) == pytest.approx(0.875)
    assert rarity.outcome_discount(2, 2, 0.0) == 1.0
    assert rarity.outcome_discount(7, 9, 0.0) == 1.0


def test_params_from_config_and_validation():
    cfg = load_fraud_config()
    p = rarity.FraudParams.from_config(cfg)
    assert p.rho == cfg.f1_rarity_fraction and p.min_linked == cfg.f1_min_linked_deals
    assert p.type_weights["remit_account"] > p.type_weights["address"] > 0
    assert 0 <= p.outcome_beta <= 1
    assert p.train_end == cfg.train_end
    assert rarity.FraudParams.from_config(None).type_weights == {}

    def bad(**raw_f1):
        raw = {**cfg.raw, "families": {**cfg.raw["families"], "F1": {**cfg.raw["families"]["F1"], **raw_f1}}}
        return replace(cfg, raw=raw)

    with pytest.raises(ConfigError):
        rarity.FraudParams.from_config(bad(type_weights={"preparer": -1.0}))
    with pytest.raises(ConfigError):
        rarity.FraudParams.from_config(bad(outcome_beta=1.5))
    with pytest.raises(ConfigError):
        rarity.FraudParams.from_config(bad(outcome_beta="x"))


# ------------------------------------------------------------ Task 2: shared math

def test_percentile_interpolates():
    v = [0, 10, 20, 30, 40]
    assert rarity.percentile(v, 0.95) == pytest.approx(38.0)
    assert rarity.percentile(v, 0.5) == pytest.approx(20.0)
    assert rarity.percentile(v, 0) == 0.0 and rarity.percentile(v, 1) == 40.0
    assert rarity.percentile([40, 0, 20, 10, 30], 0.95) == pytest.approx(38.0)
    assert rarity.percentile([7], 0.95) == 7
    assert rarity.percentile([], 0.95) is None


def test_upper_tail_bits():
    s = [1, 2, 3, 4]
    assert rarity.upper_tail_bits(3, s) == pytest.approx(-math.log2(3 / 5))
    assert rarity.upper_tail_bits(10, s) == pytest.approx(-math.log2(1 / 5))
    assert rarity.upper_tail_bits(0, s) == 0.0
    assert rarity.upper_tail_bits(5, []) == 0.0


def test_fit_beta_mom_and_fallbacks():
    a, b = rarity.fit_beta_mom([0.1, 0.2, 0.3])
    assert (a, b) == (pytest.approx(3.0), pytest.approx(12.0))
    for shares in ([], [0.4], [0.2, 0.2, 0.2], [0.0, 1.0], [0.0, 0.0]):
        a, b = rarity.fit_beta_mom(shares)
        assert a > 0 and b > 0
    a, b = rarity.fit_beta_mom([])
    assert a == pytest.approx(b)  # centred on 0.5
    a, b = rarity.fit_beta_mom([0.2, 0.2])
    assert a + b == pytest.approx(rarity.PRIOR_STRENGTH) and a / (a + b) == pytest.approx(0.2)


def test_shrunk_share_pulls_small_samples():
    assert rarity.shrunk_share(4, 4, 3, 12) == pytest.approx(7 / 19)
    assert rarity.shrunk_share(400, 400, 3, 12) == pytest.approx(403 / 415)
    vals = [rarity.shrunk_share(n, n, 3, 12) for n in (1, 4, 40, 400)]
    assert vals == sorted(vals)


def test_entropy_and_kl():
    assert rarity.entropy_bits([0.5, 0.5]) == pytest.approx(1.0)
    assert rarity.entropy_bits([0.2] * 5) == pytest.approx(math.log2(5))
    assert rarity.kl_bits([0.3, 0.7], [0.3, 0.7]) == pytest.approx(0.0)
    assert rarity.kl_bits([1.0, 0.0], [0.5, 0.5]) == pytest.approx(1.0)


def test_parse_weekdays():
    assert rarity.parse_weekdays("Mon-Fri") == (0, 1, 2, 3, 4)
    assert len(rarity.parse_weekdays("Mon-Sat")) == 6
    assert len(rarity.parse_weekdays("all days")) == 7
    assert rarity.parse_weekdays("Mon,Thu") == (0, 3)
    assert rarity.parse_weekdays("  mon , THU ") == (0, 3)
    assert rarity.parse_weekdays("Sat-Mon") == (0, 5, 6)
    assert rarity.parse_weekdays("Mon–Wed") == (0, 1, 2)
    for bad in ("", None, "someday", "Mon-Zzz", "Mon,"):
        assert rarity.parse_weekdays(bad) is None


def test_edit_distance_and_similarity():
    assert rarity.edit_distance("kitten", "sitting") == 3
    assert rarity.edit_distance("", "abc") == 3 and rarity.edit_distance("same", "same") == 0
    assert rarity.token_set_similarity("kappa supply", "supply kappa") == 1.0
    assert rarity.token_set_similarity("kappa", "kappa supply") < 1.0
    assert rarity.base_similar("kappa supply", "kappa suply", 0.8, 2)
    assert rarity.base_similar("kappa supply", "supply kappa", 0.8, 2)
    assert not rarity.base_similar("kappa supply", "omega trucking", 0.8, 2)
    assert rarity.base_similar("abcd", "abxy", 0.99, 2)  # edit distance alone is enough


def test_params_task2_keys_validate():
    cfg = load_fraud_config()
    p = rarity.FraudParams.from_config(cfg)
    assert (p.min_similarity, p.max_edit_distance, p.min_vendor_deals) == (0.8, 2, 3)
    assert p.dirichlet_alpha == 0.5

    def bad(fam, **kw):
        fams = {**cfg.raw["families"], fam: {**cfg.raw["families"][fam], **kw}}
        return replace(cfg, raw={**cfg.raw, "families": fams})

    for fam, kw in (("F3", {"min_similarity": 0}), ("F3", {"min_similarity": 1.5}),
                    ("F3", {"max_edit_distance": -1}), ("F3", {"min_vendor_deals": 0}),
                    ("F5", {"dirichlet_alpha": 0}), ("F5", {"dirichlet_alpha": -1})):
        with pytest.raises(ConfigError):
            rarity.FraudParams.from_config(bad(fam, **kw))
