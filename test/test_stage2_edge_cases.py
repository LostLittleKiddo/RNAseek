"""Comprehensive Stage 2 integration tests for experimental design edge cases.

Tests all 5 metadata cases from rnaseek_dev_dataset/:
  Case 1: Baseline — simple 2-condition (Control vs Treated)
  Case 2: Multi-contrast — 3 conditions, 2 explicit contrasts
  Case 3: Covariates & Batch — triggers ComBat_seq + covariate formula
  Case 4: Continuous covariate — numeric age column in DESeq2 formula
  Case 5: Rank deficiency — batch perfectly confounded with condition

Run with:
  cd /home/littlekiddo/Desktop/RNA/rnaseek
  conda run -n rnaseek python -m pytest test/test_stage2_edge_cases.py -vv -s --maxfail=1
"""

import os
import sys
import tempfile

import numpy as np
import pandas as pd
import pytest

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")

import django
django.setup()

from pipeline.stats import (
    _align_samples,
    _build_formula_string,
    _get_covariates,
    _run_deseq2,
    _sanitize_factor_levels,
)
from pipeline.stats._helpers import _combat_seq

DEV_DIR = os.path.join(os.path.dirname(__file__), "..", "rnaseek_dev_dataset")


# ──────────────────────────────────────────────────────────────
# Helpers
# ──────────────────────────────────────────────────────────────

def make_counts(n_genes=300, sample_names=None, seed=42, differential_samples=None,
                differential_genes=20, fold_change=5):
    """Synthetic negative-binomial count matrix resembling RNA-Seq data."""
    rng = np.random.default_rng(seed)
    if sample_names is None:
        sample_names = [f"Sample{i+1}" for i in range(6)]
    n_samples = len(sample_names)
    gene_names = [f"YAL{i:04d}W" for i in range(n_genes)]
    counts = rng.negative_binomial(n=5, p=0.01, size=(n_genes, n_samples))
    df = pd.DataFrame(counts, index=gene_names, columns=sample_names)
    if differential_samples:
        target_genes = df.index[:differential_genes]
        df.loc[target_genes, differential_samples] *= fold_change
    return df


def load_case_csv(filename):
    """Load a metadata CSV from rnaseek_dev_dataset/."""
    path = os.path.join(DEV_DIR, filename)
    assert os.path.exists(path), f"Test CSV not found: {path}"
    return pd.read_csv(path)


# ──────────────────────────────────────────────────────────────
# JSON payloads matching the 5 test cases
# ──────────────────────────────────────────────────────────────

PAYLOAD_CASE1 = {
    "samples": [
        {"sample": "Sample1", "condition": "Control"},
        {"sample": "Sample2", "condition": "Control"},
        {"sample": "Sample3", "condition": "Control"},
        {"sample": "Sample4", "condition": "Treated"},
        {"sample": "Sample5", "condition": "Treated"},
        {"sample": "Sample6", "condition": "Treated"},
    ],
    "column_mapping": {
        "primary_group": "condition",
    },
    "contrasts": [],
}

PAYLOAD_CASE2 = {
    "samples": [
        {"sample": f"Sample{i+1}", "condition": cond}
        for i, cond in enumerate(["WT"]*3 + ["MutA"]*3 + ["MutB"]*3)
    ],
    "column_mapping": {
        "primary_group": "condition",
    },
    "contrasts": [["MutA", "WT"], ["MutB", "WT"]],
}

PAYLOAD_CASE3 = {
    "samples": [
        {"sample": "Sample1", "condition": "Control", "sex": "M", "batch": "Batch1"},
        {"sample": "Sample2", "condition": "Control", "sex": "F", "batch": "Batch1"},
        {"sample": "Sample3", "condition": "Control", "sex": "M", "batch": "Batch2"},
        {"sample": "Sample4", "condition": "Control", "sex": "F", "batch": "Batch2"},
        {"sample": "Sample5", "condition": "Treated", "sex": "M", "batch": "Batch1"},
        {"sample": "Sample6", "condition": "Treated", "sex": "F", "batch": "Batch1"},
        {"sample": "Sample7", "condition": "Treated", "sex": "M", "batch": "Batch2"},
        {"sample": "Sample8", "condition": "Treated", "sex": "F", "batch": "Batch2"},
    ],
    "column_mapping": {
        "primary_group": "condition",
        "batch_effect": "batch",
        "additional_covariates": ["sex"],
    },
    "contrasts": [],
}

PAYLOAD_CASE4 = {
    "samples": [
        {"sample": f"Sample{i+1}", "condition": cond, "age": str(age)}
        for i, (cond, age) in enumerate([
            ("Control", 22), ("Control", 35), ("Control", 28), ("Control", 41),
            ("Treated", 24), ("Treated", 38), ("Treated", 31), ("Treated", 45),
        ])
    ],
    "column_mapping": {
        "primary_group": "condition",
        "additional_covariates": ["age"],
    },
    "contrasts": [],
}

PAYLOAD_CASE5_RANK_DEFICIENT = {
    "samples": [
        {"sample": "Sample1", "condition": "Control", "batch": "BatchA"},
        {"sample": "Sample2", "condition": "Control", "batch": "BatchA"},
        {"sample": "Sample3", "condition": "Treated", "batch": "BatchB"},
        {"sample": "Sample4", "condition": "Treated", "batch": "BatchB"},
    ],
    "column_mapping": {
        "primary_group": "condition",
        "batch_effect": "batch",
    },
    "contrasts": [],
}


# ──────────────────────────────────────────────────────────────
# Test: _get_covariates key normalization
# ──────────────────────────────────────────────────────────────

class TestGetCovariates:
    """Verify _get_covariates accepts both frontend key names."""

    def test_additional_covariates_key(self):
        mapping = {"primary_group": "condition", "additional_covariates": ["age", "sex"]}
        assert _get_covariates(mapping) == ["age", "sex"]

    def test_covariates_key(self):
        mapping = {"primary_group": "condition", "covariates": ["weight"]}
        assert _get_covariates(mapping) == ["weight"]

    def test_additional_takes_precedence(self):
        mapping = {
            "primary_group": "condition",
            "additional_covariates": ["age"],
            "covariates": ["weight"],
        }
        assert _get_covariates(mapping) == ["age"]

    def test_no_covariates(self):
        mapping = {"primary_group": "condition"}
        assert _get_covariates(mapping) == []


# ──────────────────────────────────────────────────────────────
# Test: Formula builder
# ──────────────────────────────────────────────────────────────

class TestFormulaBuilder:
    """Tests unique to edge-case file; basic cases covered by test_stage2.py."""

    def test_primary_always_last(self):
        m = {
            "primary_group": "treatment",
            "additional_covariates": ["sex"],
        }
        f = _build_formula_string(m)
        assert f.endswith("treatment")

    def test_frontend_covariates_key(self):
        """Verify the formula builder works with the frontend's 'covariates' key."""
        m = {"primary_group": "condition", "covariates": ["age"]}
        assert _build_formula_string(m) == "~ age + condition"


# ──────────────────────────────────────────────────────────────
# Test: Gene filtering
# ──────────────────────────────────────────────────────────────

# Gene filtering: basic cases covered by test_stage2.py — removed duplicates


# ──────────────────────────────────────────────────────────────
# Test: Sample alignment
# ──────────────────────────────────────────────────────────────

class TestSampleAlignment:
    """Only extra-covariate scenario here; basic alignment in test_stage2.py."""

    def test_with_extra_covariates(self):
        counts = make_counts(sample_names=["S1", "S2", "S3", "S4"])
        metadata = pd.DataFrame({
            "sample": ["S1", "S2", "S3", "S4"],
            "condition": ["C", "C", "T", "T"],
            "age": ["22", "35", "28", "41"],
        })
        meta_a, counts_a = _align_samples(metadata, counts)
        assert "age" in meta_a.columns
        assert len(meta_a) == 4


# ──────────────────────────────────────────────────────────────
# Test: Outlier detection
# ──────────────────────────────────────────────────────────────

# Outlier detection: basic cases covered by test_stage2.py — removed duplicates


# ──────────────────────────────────────────────────────────────
# Test: Factor sanitization
# ──────────────────────────────────────────────────────────────

class TestFactorSanitization:

    def test_special_characters(self):
        col_data = pd.DataFrame({"condition": ["0.4 M NaCl", "unstressed control"]})
        mapping = {"primary_group": "condition"}
        sanitized, level_maps = _sanitize_factor_levels(col_data, mapping)
        for val in sanitized["condition"]:
            assert re.match(r'^[A-Za-z0-9_.\-]+$', val), f"Unsafe R name: {val}"

    def test_level_maps_reversible(self):
        col_data = pd.DataFrame({"condition": ["Drug A", "Control (0 min)"]})
        mapping = {"primary_group": "condition"}
        sanitized, level_maps = _sanitize_factor_levels(col_data, mapping)
        # level_maps[col] = {sanitized: original}
        for sanitized_val, original_val in level_maps["condition"].items():
            assert original_val in ["Drug A", "Control (0 min)"]


import re


# ──────────────────────────────────────────────────────────────
# Case 1: Baseline — Simple 2-condition
# ──────────────────────────────────────────────────────────────

class TestCase1Baseline:
    """Simple Control vs Treated with no contrasts (DESeq2 default)."""

    def test_csv_loads(self):
        df = load_case_csv("metadata_case1_baseline.csv")
        assert len(df) == 6
        assert set(df["condition"].unique()) == {"Control", "Treated"}

    def test_deseq2_default_contrast(self):
        samples = [f"Sample{i+1}" for i in range(6)]
        counts = make_counts(n_genes=300, sample_names=samples, seed=42,
                             differential_samples=["Sample4", "Sample5", "Sample6"])
        metadata = pd.DataFrame({
            "condition": ["Control"]*3 + ["Treated"]*3,
        }, index=samples)

        mapping = PAYLOAD_CASE1["column_mapping"]
        with tempfile.TemporaryDirectory() as tmpdir:
            norm_path = os.path.join(tmpdir, "norm.csv")
            results = _run_deseq2(
                counts, metadata,
                column_mapping=mapping,
                contrasts_list=[],
                stats_dir=tmpdir,
                norm_output=norm_path,
                adj_pvalue_cutoff=0.05,
                min_log2fc=-1.0,
                max_log2fc=1.0,
            )
            assert len(results) >= 1
            assert os.path.exists(results[0])
            assert os.path.exists(norm_path)

            deg_df = pd.read_csv(results[0])
            assert "gene_id" in deg_df.columns
            assert "significant" in deg_df.columns
            assert "padj" in deg_df.columns
            assert len(deg_df) > 0

    def test_payload_structure(self):
        mapping = PAYLOAD_CASE1["column_mapping"]
        assert mapping["primary_group"] == "condition"
        assert "batch_effect" not in mapping
        assert PAYLOAD_CASE1["contrasts"] == []


# ──────────────────────────────────────────────────────────────
# Case 2: Multi-contrast — 3 conditions
# ──────────────────────────────────────────────────────────────

class TestCase2MultiContrast:
    """WT, MutA, MutB with two explicit contrasts."""

    def test_csv_loads(self):
        df = load_case_csv("metadata_case2_multi_contrast.csv")
        assert len(df) == 9
        assert set(df["condition"].unique()) == {"WT", "MutA", "MutB"}

    def test_two_deg_files_produced(self):
        samples = [f"Sample{i+1}" for i in range(9)]
        counts = make_counts(n_genes=300, sample_names=samples, seed=123,
                             differential_samples=["Sample4", "Sample5", "Sample6"],
                             fold_change=5)
        metadata = pd.DataFrame({
            "condition": ["WT"]*3 + ["MutA"]*3 + ["MutB"]*3,
        }, index=samples)

        mapping = PAYLOAD_CASE2["column_mapping"]
        contrasts = PAYLOAD_CASE2["contrasts"]

        with tempfile.TemporaryDirectory() as tmpdir:
            norm_path = os.path.join(tmpdir, "norm.csv")
            results = _run_deseq2(
                counts, metadata,
                column_mapping=mapping,
                contrasts_list=contrasts,
                stats_dir=tmpdir,
                norm_output=norm_path,
                adj_pvalue_cutoff=0.05,
                min_log2fc=-1.0,
                max_log2fc=1.0,
            )
            assert len(results) == 2

            for path in results:
                assert os.path.exists(path)
                df = pd.read_csv(path)
                assert "contrast" in df.columns
                assert "gene_id" in df.columns
                assert "significant" in df.columns
                assert len(df) > 0

            # Check filenames
            basenames = sorted(os.path.basename(p) for p in results)
            assert any("MutA_vs_WT" in b for b in basenames)
            assert any("MutB_vs_WT" in b for b in basenames)

    def test_payload_has_two_contrasts(self):
        assert len(PAYLOAD_CASE2["contrasts"]) == 2
        assert PAYLOAD_CASE2["contrasts"][0] == ["MutA", "WT"]
        assert PAYLOAD_CASE2["contrasts"][1] == ["MutB", "WT"]


# ──────────────────────────────────────────────────────────────
# Case 3: Covariates & Batch
# ──────────────────────────────────────────────────────────────

class TestCase3BatchAndCovariate:
    """2 conditions, sex covariate, batch effect that triggers ComBat_seq."""

    def test_csv_loads(self):
        df = load_case_csv("metadata_case3_batch_covariate.csv")
        assert len(df) == 8
        assert "batch" in df.columns
        assert "sex" in df.columns

    def test_formula_includes_all_terms(self):
        mapping = PAYLOAD_CASE3["column_mapping"]
        formula = _build_formula_string(mapping)
        assert formula == "~ sex + batch + condition"

    def test_combat_seq_runs(self):
        """ComBat_seq should run when batch has >=2 levels."""
        samples = [f"Sample{i+1}" for i in range(8)]
        counts = make_counts(n_genes=200, sample_names=samples, seed=77)
        metadata = pd.DataFrame({
            "condition": ["Control"]*4 + ["Treated"]*4,
            "sex": ["M", "F", "M", "F", "M", "F", "M", "F"],
            "batch": ["Batch1", "Batch1", "Batch2", "Batch2",
                      "Batch1", "Batch1", "Batch2", "Batch2"],
        }, index=samples)

        corrected = _combat_seq(
            counts, metadata,
            batch_col="batch",
            group_col="condition",
            covariates=["sex"],
        )
        assert corrected.shape == counts.shape
        assert list(corrected.columns) == list(counts.columns)
        # Corrected counts should be different from original
        assert not np.array_equal(corrected.values, counts.values)

    def test_deseq2_with_batch_and_covariate(self):
        samples = [f"Sample{i+1}" for i in range(8)]
        counts = make_counts(n_genes=200, sample_names=samples, seed=77,
                             differential_samples=["Sample5", "Sample6", "Sample7", "Sample8"])
        metadata = pd.DataFrame({
            "condition": ["Control"]*4 + ["Treated"]*4,
            "sex": ["M", "F", "M", "F", "M", "F", "M", "F"],
            "batch": ["Batch1", "Batch1", "Batch2", "Batch2",
                      "Batch1", "Batch1", "Batch2", "Batch2"],
        }, index=samples)

        mapping = PAYLOAD_CASE3["column_mapping"]

        with tempfile.TemporaryDirectory() as tmpdir:
            norm_path = os.path.join(tmpdir, "norm.csv")
            results = _run_deseq2(
                counts, metadata,
                column_mapping=mapping,
                contrasts_list=[],
                stats_dir=tmpdir,
                norm_output=norm_path,
                adj_pvalue_cutoff=0.05,
                min_log2fc=-1.0,
                max_log2fc=1.0,
            )
            assert len(results) >= 1
            deg = pd.read_csv(results[0])
            assert len(deg) > 0

    def test_payload_triggers_batch(self):
        mapping = PAYLOAD_CASE3["column_mapping"]
        assert mapping.get("batch_effect") == "batch"
        assert mapping.get("additional_covariates") == ["sex"]


# ──────────────────────────────────────────────────────────────
# Case 4: Continuous covariate
# ──────────────────────────────────────────────────────────────

class TestCase4ContinuousCovariate:
    """2 conditions + numeric 'age' covariate."""

    def test_csv_loads(self):
        df = load_case_csv("metadata_case4_continuous.csv")
        assert len(df) == 8
        # Age should be parseable as numeric
        pd.to_numeric(df["age"])

    def test_formula_includes_age(self):
        mapping = PAYLOAD_CASE4["column_mapping"]
        formula = _build_formula_string(mapping)
        assert formula == "~ age + condition"

    def test_age_treated_as_numeric_not_factor(self):
        """DESeq2 should receive age as numeric, not as a multi-level factor."""
        samples = [f"Sample{i+1}" for i in range(8)]
        counts = make_counts(n_genes=200, sample_names=samples, seed=88,
                             differential_samples=["Sample5", "Sample6", "Sample7", "Sample8"])
        metadata = pd.DataFrame({
            "condition": ["Control"]*4 + ["Treated"]*4,
            "age": ["22", "35", "28", "41", "24", "38", "31", "45"],
        }, index=samples)

        mapping = PAYLOAD_CASE4["column_mapping"]

        with tempfile.TemporaryDirectory() as tmpdir:
            norm_path = os.path.join(tmpdir, "norm.csv")
            # This would FAIL before the fix because 8 unique age values
            # as factors + condition = 9 parameters > 8 samples → not full rank
            results = _run_deseq2(
                counts, metadata,
                column_mapping=mapping,
                contrasts_list=[],
                stats_dir=tmpdir,
                norm_output=norm_path,
                adj_pvalue_cutoff=0.05,
                min_log2fc=-1.0,
                max_log2fc=1.0,
            )
            assert len(results) >= 1
            deg = pd.read_csv(results[0])
            assert len(deg) > 0
            assert "padj" in deg.columns

    def test_payload_uses_additional_covariates(self):
        mapping = PAYLOAD_CASE4["column_mapping"]
        assert mapping.get("additional_covariates") == ["age"]


# ──────────────────────────────────────────────────────────────
# Case 5: Rank deficiency (perfect confounding)
# ──────────────────────────────────────────────────────────────

class TestCase5RankDeficiency:
    """Batch perfectly confounded with condition — must produce clear error."""

    def test_csv_loads(self):
        df = load_case_csv("metadata_case5_rank_deficient.csv")
        assert len(df) == 4
        # Verify confounding: all Control in BatchA, all Treated in BatchB
        for _, row in df.iterrows():
            if row["condition"] == "Control":
                assert row["batch"] == "BatchA"
            else:
                assert row["batch"] == "BatchB"

    def test_raises_rank_error(self):
        samples = ["Sample1", "Sample2", "Sample3", "Sample4"]
        counts = make_counts(n_genes=100, sample_names=samples, seed=99)
        metadata = pd.DataFrame({
            "condition": ["Control", "Control", "Treated", "Treated"],
            "batch": ["BatchA", "BatchA", "BatchB", "BatchB"],
        }, index=samples)

        mapping = PAYLOAD_CASE5_RANK_DEFICIENT["column_mapping"]

        with tempfile.TemporaryDirectory() as tmpdir:
            norm_path = os.path.join(tmpdir, "norm.csv")
            with pytest.raises(RuntimeError, match=r"(?i)rank|confound"):
                _run_deseq2(
                    counts, metadata,
                    column_mapping=mapping,
                    contrasts_list=[],
                    stats_dir=tmpdir,
                    norm_output=norm_path,
                    adj_pvalue_cutoff=0.05,
                    min_log2fc=-1.0,
                    max_log2fc=1.0,
                )

    def test_payload_structure(self):
        mapping = PAYLOAD_CASE5_RANK_DEFICIENT["column_mapping"]
        assert mapping["primary_group"] == "condition"
        assert mapping["batch_effect"] == "batch"


# ──────────────────────────────────────────────────────────────
# Additional edge case: invalid contrast level
# ──────────────────────────────────────────────────────────────

class TestInvalidContrastLevel:
    """Contrast referencing a non-existent group level should fail early."""

    def test_bad_target(self):
        samples = [f"Sample{i+1}" for i in range(6)]
        counts = make_counts(n_genes=100, sample_names=samples, seed=42)
        metadata = pd.DataFrame({
            "condition": ["Control"]*3 + ["Treated"]*3,
        }, index=samples)

        mapping = {"primary_group": "condition"}
        contrasts = [["NonExistent", "Control"]]

        with tempfile.TemporaryDirectory() as tmpdir:
            norm_path = os.path.join(tmpdir, "norm.csv")
            with pytest.raises(RuntimeError, match="not found"):
                _run_deseq2(
                    counts, metadata,
                    column_mapping=mapping,
                    contrasts_list=contrasts,
                    stats_dir=tmpdir,
                    norm_output=norm_path,
                    adj_pvalue_cutoff=0.05,
                    min_log2fc=-1.0,
                    max_log2fc=1.0,
                )

    def test_bad_reference(self):
        samples = [f"Sample{i+1}" for i in range(6)]
        counts = make_counts(n_genes=100, sample_names=samples, seed=42)
        metadata = pd.DataFrame({
            "condition": ["Control"]*3 + ["Treated"]*3,
        }, index=samples)

        mapping = {"primary_group": "condition"}
        contrasts = [["Treated", "Phantom"]]

        with tempfile.TemporaryDirectory() as tmpdir:
            norm_path = os.path.join(tmpdir, "norm.csv")
            with pytest.raises(RuntimeError, match="not found"):
                _run_deseq2(
                    counts, metadata,
                    column_mapping=mapping,
                    contrasts_list=contrasts,
                    stats_dir=tmpdir,
                    norm_output=norm_path,
                    adj_pvalue_cutoff=0.05,
                    min_log2fc=-1.0,
                    max_log2fc=1.0,
                )


# ──────────────────────────────────────────────────────────────
# Additional edge case: combat_seq skips single-batch
# ──────────────────────────────────────────────────────────────

class TestCombatSeqSkipsSingleBatch:

    def test_single_batch_returns_original(self):
        samples = [f"S{i}" for i in range(6)]
        counts = make_counts(n_genes=100, sample_names=samples, seed=42)
        metadata = pd.DataFrame({
            "condition": ["A"]*3 + ["B"]*3,
            "batch": ["OnlyBatch"]*6,
        }, index=samples)

        result = _combat_seq(counts, metadata, "batch", "condition")
        assert np.array_equal(result.values, counts.values)
