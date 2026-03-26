"""End-to-end test: Stage 2 stats with synthetic yeast-like count data.

Tests:
  1. Simple 2-group design (Control vs Treatment)
  2. Multi-group design with explicit contrasts (Drug_A, Drug_B, Control)
  3. Complex design with covariates (condition + batch + age)
  4. Error case: model matrix not full rank (perfect confounding)
"""

import os
import sys
import tempfile

import numpy as np
import pandas as pd

# Ensure Django settings are configured
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")

import django
django.setup()

from pipeline.stats import (
    _align_samples,
    _build_formula_string,
    _detect_outliers,
    _filter_low_counts,
    _run_deseq2,
)

PASS = 0
FAIL = 0


def report(name, passed, detail=""):
    global PASS, FAIL
    if passed:
        PASS += 1
        print(f"  [PASS] {name}")
    else:
        FAIL += 1
        print(f"  [FAIL] {name} — {detail}")


def make_synthetic_counts(n_genes=500, n_samples=6, seed=42):
    """Generate a synthetic count matrix resembling yeast RNA-Seq data."""
    rng = np.random.default_rng(seed)
    # Simulate counts with negative binomial (size=5, typical for RNA-Seq)
    gene_names = [f"YAL{i:04d}W" for i in range(n_genes)]
    sample_names = [f"Sample{i+1}" for i in range(n_samples)]
    counts = rng.negative_binomial(n=5, p=0.01, size=(n_genes, n_samples))
    return pd.DataFrame(counts, index=gene_names, columns=sample_names)


def test_formula_builder():
    """Test dynamic formula string construction."""
    print("\n== Test: Formula Builder ==")

    m1 = {"primary_group": "condition", "batch_effect": None, "additional_covariates": []}
    report("Simple formula", _build_formula_string(m1) == "~ condition")

    m2 = {"primary_group": "condition", "batch_effect": "batch", "additional_covariates": ["age", "sex"]}
    f2 = _build_formula_string(m2)
    report("Complex formula", f2 == "~ age + sex + batch + condition", f2)

    m3 = {"primary_group": "treatment", "batch_effect": None, "additional_covariates": ["sex"]}
    f3 = _build_formula_string(m3)
    report("Primary group last", f3.endswith("treatment"), f3)


def test_gene_filtering():
    """Test low-count gene filtering."""
    print("\n== Test: Gene Filtering ==")
    counts = make_synthetic_counts()
    # Add a gene with all zeros
    counts.loc["ZERO_GENE"] = 0
    # Add a gene with just below threshold
    counts.loc["LOW_GENE"] = [1, 1, 1, 1, 1, 2]

    filtered = _filter_low_counts(counts, min_total=10)
    report("Zero gene removed", "ZERO_GENE" not in filtered.index)
    report("Low gene removed", "LOW_GENE" not in filtered.index)
    report("Most genes kept", len(filtered) > 450)


def test_outlier_detection():
    """Test Mahalanobis outlier detection."""
    print("\n== Test: Outlier Detection ==")
    counts = make_synthetic_counts(n_genes=200, n_samples=20, seed=42)
    # Make one sample an extreme outlier (shift to completely different range)
    counts.iloc[:, 0] = 50000

    flags = _detect_outliers(counts, confidence=0.95)
    first_sample = counts.columns[0]
    report("Returns dict", isinstance(flags, dict))
    report("All samples flagged", len(flags) == 20)
    report("Outlier detected", flags.get(first_sample, False) is True)


def test_align_samples():
    """Test metadata/count matrix alignment."""
    print("\n== Test: Sample Alignment ==")
    counts = make_synthetic_counts(n_samples=4)
    counts.columns = ["SampleA", "SampleB", "SampleC", "SampleD"]

    metadata = pd.DataFrame({
        "sample": ["SampleA_R1.fq.gz", "SampleB_R1.fq.gz", "SampleC_R1.fq.gz", "SampleD_R1.fq.gz"],
        "condition": ["Control", "Control", "Treatment", "Treatment"],
    })

    meta_aligned, counts_aligned = _align_samples(metadata, counts)
    report("4 samples aligned", len(counts_aligned.columns) == 4)
    report("Metadata rows match", len(meta_aligned) == 4)
    report("Condition column present", "condition" in meta_aligned.columns)

    # Extra-covariate scenario: columns in alphabetical order put "age" before "sample"
    metadata_extra = pd.DataFrame([
        {"age": "12", "condition": "Control",   "sample": "SampleA_R1.fq.gz"},
        {"age": "15", "condition": "Control",   "sample": "SampleB_R1.fq.gz"},
        {"age": "16", "condition": "Treatment", "sample": "SampleC_R1.fq.gz"},
        {"age": "17", "condition": "Treatment", "sample": "SampleD_R1.fq.gz"},
    ])
    meta2, counts2 = _align_samples(metadata_extra, counts)
    report("Extra-covariate: 4 samples aligned", len(counts2.columns) == 4)
    report("Extra-covariate: 'age' column kept", "age" in meta2.columns)


def test_deseq2_simple():
    """Test DESeq2 with 2-group design (no contrasts)."""
    print("\n== Test: DESeq2 Simple 2-Group ==")
    with tempfile.TemporaryDirectory() as tmpdir:
        counts = make_synthetic_counts(n_genes=300, n_samples=6)
        metadata = pd.DataFrame({
            "condition": ["Control", "Control", "Control", "Treatment", "Treatment", "Treatment"],
        }, index=counts.columns)

        column_mapping = {
            "primary_group": "condition",
            "batch_effect": None,
            "additional_covariates": [],
        }

        norm_path = os.path.join(tmpdir, "norm.csv")
        results = _run_deseq2(
            counts, metadata,
            column_mapping=column_mapping,
            contrasts_list=[],
            stats_dir=tmpdir,
            norm_output=norm_path,
            adj_pvalue_cutoff=0.05,
            min_log2fc=-1.0,
            max_log2fc=1.0,
        )

        report("Returns result paths", len(results) >= 1)
        report("DEG file exists", os.path.exists(results[0]))
        report("Normalized counts exist", os.path.exists(norm_path))

        deg_df = pd.read_csv(results[0])
        report("Has gene_id column", "gene_id" in deg_df.columns)
        report("Has significant column", "significant" in deg_df.columns)
        report("Has padj column", "padj" in deg_df.columns)
        print(f"    → {len(deg_df)} genes, {deg_df['significant'].sum()} significant")


def test_deseq2_multi_contrast():
    """Test DESeq2 with 3 groups and explicit contrasts."""
    print("\n== Test: DESeq2 Multi-Group Contrasts ==")
    with tempfile.TemporaryDirectory() as tmpdir:
        counts = make_synthetic_counts(n_genes=300, n_samples=9, seed=123)
        counts.columns = [f"S{i+1}" for i in range(9)]

        metadata = pd.DataFrame({
            "condition": ["Control", "Control", "Control",
                          "Drug_A", "Drug_A", "Drug_A",
                          "Drug_B", "Drug_B", "Drug_B"],
        }, index=counts.columns)

        # Make Drug_A slightly different from Control for testability
        drug_a_genes = counts.index[:20]
        counts.loc[drug_a_genes, ["S4", "S5", "S6"]] *= 5

        column_mapping = {
            "primary_group": "condition",
            "batch_effect": None,
            "additional_covariates": [],
        }
        contrasts = [["Drug_A", "Control"], ["Drug_B", "Control"]]

        norm_path = os.path.join(tmpdir, "norm.csv")
        results = _run_deseq2(
            counts, metadata,
            column_mapping=column_mapping,
            contrasts_list=contrasts,
            stats_dir=tmpdir,
            norm_output=norm_path,
            adj_pvalue_cutoff=0.05,
            min_log2fc=-1.0,
            max_log2fc=1.0,
        )

        report("Two result files", len(results) == 2)

        for path in results:
            fname = os.path.basename(path)
            exists = os.path.exists(path)
            report(f"File {fname} exists", exists)
            if exists:
                df = pd.read_csv(path)
                report(f"File {fname} has contrast col", "contrast" in df.columns)
                print(f"    → {fname}: {len(df)} genes, {df['significant'].sum()} significant")


def test_deseq2_with_covariates():
    """Test DESeq2 with additional covariates in the formula."""
    print("\n== Test: DESeq2 with Covariates ==")
    with tempfile.TemporaryDirectory() as tmpdir:
        counts = make_synthetic_counts(n_genes=200, n_samples=8, seed=55)
        counts.columns = [f"S{i+1}" for i in range(8)]

        metadata = pd.DataFrame({
            "condition": ["Control", "Control", "Control", "Control",
                          "Treatment", "Treatment", "Treatment", "Treatment"],
            "sex": ["M", "F", "M", "F", "M", "F", "M", "F"],
        }, index=counts.columns)

        column_mapping = {
            "primary_group": "condition",
            "batch_effect": None,
            "additional_covariates": ["sex"],
        }

        norm_path = os.path.join(tmpdir, "norm.csv")
        results = _run_deseq2(
            counts, metadata,
            column_mapping=column_mapping,
            contrasts_list=[],
            stats_dir=tmpdir,
            norm_output=norm_path,
            adj_pvalue_cutoff=0.05,
            min_log2fc=-1.0,
            max_log2fc=1.0,
        )

        report("Result file produced", len(results) >= 1)
        report("DEG file exists", os.path.exists(results[0]))


def test_full_rank_error():
    """Test that perfect confounding produces a clear error."""
    print("\n== Test: Model Matrix Not Full Rank ==")
    with tempfile.TemporaryDirectory() as tmpdir:
        counts = make_synthetic_counts(n_genes=100, n_samples=4, seed=99)
        counts.columns = ["S1", "S2", "S3", "S4"]

        # Perfect confounding: batch == condition
        metadata = pd.DataFrame({
            "condition": ["A", "A", "B", "B"],
            "confound": ["A", "A", "B", "B"],
        }, index=counts.columns)

        column_mapping = {
            "primary_group": "condition",
            "batch_effect": None,
            "additional_covariates": ["confound"],
        }

        norm_path = os.path.join(tmpdir, "norm.csv")
        try:
            _run_deseq2(
                counts, metadata,
                column_mapping=column_mapping,
                contrasts_list=[],
                stats_dir=tmpdir,
                norm_output=norm_path,
                adj_pvalue_cutoff=0.05,
                min_log2fc=-1.0,
                max_log2fc=1.0,
            )
            report("Should have raised RuntimeError", False, "No exception raised")
        except RuntimeError as e:
            msg = str(e).lower()
            is_rank = "rank" in msg or "full rank" in msg or "confound" in msg
            report("Clear error message", is_rank, str(e)[:120])


if __name__ == "__main__":
    print("=" * 60)
    print("RNAseek Stage 2 – End-to-End Stats Tests")
    print("=" * 60)

    test_formula_builder()
    test_gene_filtering()
    test_outlier_detection()
    test_align_samples()
    test_deseq2_simple()
    test_deseq2_multi_contrast()
    test_deseq2_with_covariates()
    test_full_rank_error()

    print("\n" + "=" * 60)
    print(f"Results: {PASS} passed, {FAIL} failed")
    print("=" * 60)
    sys.exit(1 if FAIL > 0 else 0)
