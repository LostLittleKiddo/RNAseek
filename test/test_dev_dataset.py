"""Pipeline test using the rnaseek_dev_dataset (88-sample yeast stress response).

Uses the matrix entry point to bypass HISAT2/alignment entirely and tests
Stage 2 (DESeq2 with batch correction) using a synthetic count matrix that
matches the real metadata sample names and conditions.

Dataset: 88 samples, 4 conditions, 2 batches (NU_HiSeq4000, UC_HiSeq4000)
  - unstressed control (0 min)
  - 5% (v/v) ethanol (30 min)
  - 0.4 mM H2O2 (30 min)
  - 0.4 M NaCl (45 min)
"""

import os
import sys

# Ensure project root is on sys.path for Django settings
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import numpy as np
import pandas as pd

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")

import django
django.setup()

from pipeline.models import AnalysisSubmission, FileAsset, Session
from pipeline.stats import run_stage2_stats

# Path to the real metadata CSV
DEV_DATASET_DIR = os.path.join(os.path.dirname(__file__), "..", "rnaseek_dev_dataset")
METADATA_CSV = os.path.join(DEV_DATASET_DIR, "metadata_long.csv")

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


def generate_synthetic_counts(metadata_df, n_genes=1000, seed=42):
    """Generate a realistic synthetic count matrix matching the metadata samples.

    Introduces differential expression between conditions so DESeq2
    has real signal to detect.
    """
    rng = np.random.default_rng(seed)
    samples = metadata_df["sample"].tolist()
    gene_names = [f"YAL{i:04d}W" for i in range(n_genes)]

    # Base expression: negative binomial (typical RNA-Seq distribution)
    counts = rng.negative_binomial(n=5, p=0.005, size=(n_genes, len(samples)))
    counts_df = pd.DataFrame(counts, index=gene_names, columns=samples)

    # Add differential expression for stress conditions vs control
    conditions = metadata_df.set_index("sample")["condition"]
    control_mask = "unstressed control (0 min)"

    # Ethanol stress: upregulate first 50 genes (~3x fold change)
    ethanol_samples = [s for s in samples if conditions[s] == "5% (v/v) ethanol (30 min)"]
    counts_df.loc[counts_df.index[:50], ethanol_samples] = (
        counts_df.loc[counts_df.index[:50], ethanol_samples] * 3
    ).astype(int)

    # H2O2 stress: upregulate genes 50-100 (~4x fold change)
    h2o2_samples = [s for s in samples if conditions[s] == "0.4 mM H2O2 (30 min)"]
    counts_df.loc[counts_df.index[50:100], h2o2_samples] = (
        counts_df.loc[counts_df.index[50:100], h2o2_samples] * 4
    ).astype(int)

    # NaCl stress: upregulate genes 100-150 (~2.5x fold change)
    nacl_samples = [s for s in samples if conditions[s] == "0.4 M NaCl (45 min)"]
    counts_df.loc[counts_df.index[100:150], nacl_samples] = (
        counts_df.loc[counts_df.index[100:150], nacl_samples] * 2.5
    ).astype(int)

    # Add batch effect (NU samples slightly higher overall)
    batches = metadata_df.set_index("sample")["batch"]
    nu_samples = [s for s in samples if batches[s] == "NU_HiSeq4000"]
    counts_df[nu_samples] = (counts_df[nu_samples] * 1.15).astype(int)

    return counts_df


def main():
    print("=" * 60)
    print("RNAseek Dev Dataset Pipeline Test")
    print("  88-sample yeast stress response (matrix entry point)")
    print("=" * 60)

    # ── Step 1: Load real metadata ──
    print("\n[1/5] Loading metadata from rnaseek_dev_dataset...")
    report("Metadata CSV exists", os.path.exists(METADATA_CSV))
    metadata_df = pd.read_csv(METADATA_CSV)
    report("Has 88 samples", len(metadata_df) == 88)
    report("Has 3 columns (sample, condition, batch)",
           list(metadata_df.columns) == ["sample", "condition", "batch"])

    conditions = metadata_df["condition"].unique()
    batches = metadata_df["batch"].unique()
    print(f"    Conditions: {list(conditions)}")
    print(f"    Batches: {list(batches)}")
    report("Has 4 conditions", len(conditions) == 4)
    report("Has 2 batches", len(batches) == 2)

    # ── Step 2: Generate synthetic count matrix ──
    print("\n[2/5] Generating synthetic count matrix (1000 genes x 88 samples)...")
    counts_df = generate_synthetic_counts(metadata_df, n_genes=1000)
    report("Count matrix shape", counts_df.shape == (1000, 88),
           f"got {counts_df.shape}")
    report("All values non-negative", (counts_df >= 0).all().all())
    report("Columns match metadata samples",
           set(counts_df.columns) == set(metadata_df["sample"]))
    print(f"    Total counts: {counts_df.values.sum():,.0f}")
    print(f"    Mean counts per gene: {counts_df.values.mean():.1f}")

    # ── Step 3: Set up Django submission (matrix entry point) ──
    print("\n[3/5] Setting up Django submission (matrix entry)...")
    session = Session.objects.create()
    sub = AnalysisSubmission.objects.create(
        session=session,
        input_data_type="matrix",
        metadata_mode="upload",
        adjusted_pvalue=0.05,
        min_log2fc=-1.0,
        max_log2fc=1.0,
    )

    work_dir = sub.upload_dir
    counts_dir = os.path.join(work_dir, "counts")
    metadata_dir = os.path.join(work_dir, "metadata")
    os.makedirs(counts_dir, exist_ok=True)
    os.makedirs(metadata_dir, exist_ok=True)

    # Save count matrix
    count_matrix_path = os.path.join(counts_dir, "raw_counts.csv")
    counts_df.to_csv(count_matrix_path)
    FileAsset.objects.create(
        session=session,
        submission=sub,
        file_role=FileAsset.FileRole.COUNT_MATRIX,
        local_path=count_matrix_path,
        is_user_uploaded=False,
    )

    # Copy real metadata CSV
    import shutil
    meta_dest = os.path.join(metadata_dir, "metadata_long.csv")
    shutil.copy2(METADATA_CSV, meta_dest)
    FileAsset.objects.create(
        session=session,
        submission=sub,
        file_role=FileAsset.FileRole.METADATA_CSV,
        local_path=meta_dest,
        is_user_uploaded=True,
    )

    # Configure metadata payload with multi-contrast design
    # 4 conditions → 3 contrasts (each stress vs control)
    sub.metadata_payload = {
        "column_mapping": {
            "primary_group": "condition",
            "batch_effect": "batch",
            "additional_covariates": [],
        },
        "contrasts": [
            ["5% (v/v) ethanol (30 min)", "unstressed control (0 min)"],
            ["0.4 mM H2O2 (30 min)", "unstressed control (0 min)"],
            ["0.4 M NaCl (45 min)", "unstressed control (0 min)"],
        ],
    }
    sub.save()

    print(f"    Submission ID: {sub.submission_id}")
    print(f"    Work dir: {work_dir}")
    print(f"    Entry point: matrix (bypasses HISAT2)")

    # ── Step 4: Run Stage 2 (ComBat_seq + DESeq2) ──
    print("\n[4/5] Running Stage 2: Batch correction + DESeq2...")
    print("    This includes ComBat_seq batch correction (2 batches)")
    print("    and DESeq2 with 3 contrasts (each stress vs control)...")

    stats_result = run_stage2_stats(sub)

    print(f"    Stats dir: {stats_result['stats_dir']}")
    print(f"    Batch corrected: {stats_result['batch_corrected']}")
    print(f"    Primary group: {stats_result['primary_group']}")
    print(f"    Contrasts used: {len(stats_result['contrasts_used'])}")

    # ── Step 5: Validate results ──
    print("\n[5/5] Validating results...")

    # Batch correction
    report("Batch correction applied", stats_result["batch_corrected"] is True)
    corrected_path = os.path.join(stats_result["stats_dir"], "batch_corrected_counts.csv")
    report("Batch corrected counts file exists", os.path.exists(corrected_path))

    # Normalized counts
    report("Normalized counts exist", os.path.exists(stats_result["normalized_counts"]))
    norm_df = pd.read_csv(stats_result["normalized_counts"])
    report("Normalized counts have gene_id", "gene_id" in norm_df.columns)
    # gene_id + 88 samples
    report("Normalized counts have 88 sample columns",
           len(norm_df.columns) >= 89,
           f"got {len(norm_df.columns)} columns")

    # Outlier detection
    report("Outlier flags present", "outlier_flags" in stats_result)
    report("Outlier flags is dict", isinstance(stats_result["outlier_flags"], dict))
    outlier_count = sum(1 for v in stats_result["outlier_flags"].values() if v)
    print(f"    Outlier samples detected: {outlier_count}/{len(stats_result['outlier_flags'])}")

    # DEG results (3 contrasts)
    report("3 DEG result files", len(stats_result["deg_results"]) == 3,
           f"got {len(stats_result['deg_results'])}")

    total_sig = 0
    for deg_path in stats_result["deg_results"]:
        fname = os.path.basename(deg_path)
        report(f"DEG file exists: {fname}", os.path.exists(deg_path))

        deg_df = pd.read_csv(deg_path)
        report(f"  Has padj column", "padj" in deg_df.columns)
        report(f"  Has log2FoldChange", "log2FoldChange" in deg_df.columns)
        report(f"  Has significant column", "significant" in deg_df.columns)
        report(f"  Has contrast column", "contrast" in deg_df.columns)
        report(f"  Has gene_id column", "gene_id" in deg_df.columns)

        n_sig = deg_df["significant"].sum()
        total_sig += n_sig
        print(f"    {fname}: {len(deg_df)} genes tested, {n_sig} significant")

    report("At least some DE genes found across contrasts", total_sig > 0,
           f"found {total_sig} total")

    # Print summary
    print(f"\n{'=' * 60}")
    print(f"Dev Dataset Test Results: {PASS} passed, {FAIL} failed")
    print(f"{'=' * 60}")

    # Cleanup info
    print(f"\nTest submission ID: {sub.submission_id}")
    print(f"Session ID: {session.session_id}")
    print(f"Work directory: {work_dir}")

    return FAIL == 0


if __name__ == "__main__":
    success = main()
    sys.exit(0 if success else 1)
