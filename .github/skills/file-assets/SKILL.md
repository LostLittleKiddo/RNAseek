---
description: "USE when creating or registering FileAsset records, handling file uploads, organizing pipeline output files, or querying existing assets."
---

# Skill: FileAsset Registration

How to correctly register pipeline-generated and user-uploaded files in the RNAseek database.

## Model reference

```python
from pipeline.models import FileAsset

# FileAsset fields:
#   id            — UUID PK (auto)
#   session       — FK to Session (required)
#   submission    — FK to AnalysisSubmission (nullable)
#   file_role     — CharField (choices below)
#   local_path    — CharField(max_length=500) — absolute path on disk
#   is_user_uploaded — BooleanField (default=True)
```

## Available file roles

| Role | Constant | Meaning |
|---|---|---|
| `RAW_FASTQ` | `FileAsset.FileRole.RAW_FASTQ` | Raw FASTQ input files |
| `ALIGNMENT_BAM` | `FileAsset.FileRole.ALIGNMENT_BAM` | BAM/CRAM alignment files |
| `USER_COUNT_MATRIX` | `FileAsset.FileRole.USER_COUNT_MATRIX` | User-uploaded count matrix |
| `COUNT_MATRIX` | `FileAsset.FileRole.COUNT_MATRIX` | Pipeline-generated count matrix |
| `NORMALIZED_COUNTS` | `FileAsset.FileRole.NORMALIZED_COUNTS` | DESeq2 normalized counts |
| `DEG_TABLE` | `FileAsset.FileRole.DEG_TABLE` | Differential expression results |
| `MULTIQC_REPORT` | `FileAsset.FileRole.MULTIQC_REPORT` | MultiQC HTML report |
| `H5AD_PSEUDO` | `FileAsset.FileRole.H5AD_PSEUDO` | Pseudo-single-cell AnnData |
| `HE_IMAGE_USER` | `FileAsset.FileRole.HE_IMAGE_USER` | User-uploaded H&E image |
| `HE_IMAGE_GENERIC` | `FileAsset.FileRole.HE_IMAGE_GENERIC` | Generic H&E image |
| `CUSTOM_GENOME_FASTA` | `FileAsset.FileRole.CUSTOM_GENOME_FASTA` | Custom genome FASTA |
| `CUSTOM_GENOME_ANNOTATION` | `FileAsset.FileRole.CUSTOM_GENOME_ANNOTATION` | Custom genome GTF/GFF |
| `METADATA_CSV` | `FileAsset.FileRole.METADATA_CSV` | Sample metadata CSV |
| `PEAK_FILE` | `FileAsset.FileRole.PEAK_FILE` | ChIP-seq peak file |
| `METHYLATION_REPORT` | `FileAsset.FileRole.METHYLATION_REPORT` | Bismark methylation report |

## Pattern: Register a pipeline-generated file

```python
from pipeline.models import FileAsset

FileAsset.objects.create(
    session_id=submission.session_id,   # ALWAYS scope by session
    submission=submission,              # Link to the submission
    file_role=FileAsset.FileRole.COUNT_MATRIX,
    local_path=abs_path,               # Must be absolute path
    is_user_uploaded=False,             # Pipeline-generated = False
)
```

## Pattern: Register Stage 2 outputs (use the shared helper)

```python
from pipeline.tasks._routes import _register_stage2_assets

# stats_result is the dict returned by run_stage2_stats()
_register_stage2_assets(submission, stats_result, qc_dir=qc_dir)
```

This automatically registers: normalized counts, DEG tables, and MultiQC report.

## Pattern: Query assets (with tenant isolation)

```python
# In a view (scoped by request.session_obj):
assets = FileAsset.objects.filter(session=request.session_obj)

# In a task (scoped by submission):
bam_paths = list(
    submission.file_assets.filter(
        file_role=FileAsset.FileRole.ALIGNMENT_BAM
    ).values_list("local_path", flat=True)
)
```

## Directory conventions

Pipeline outputs go in typed subdirectories under `submission.upload_dir`:

| Subdirectory | Content |
|---|---|
| `raw/` | FASTQ files |
| `aligned/` | BAM/CRAM files |
| `counts/` | Count matrices (`raw_counts.csv`) |
| `qc/` | FastQC + MultiQC reports |
| `trimmed/` | Trimmed FASTQ files |
| `stats/` | Stage 2 output (DEG tables, plots, normalized counts) |
| `custom_genome/` | User-uploaded FASTA + GTF |
| `metadata/` | Uploaded metadata CSV |
| `modules/<name>/` | Tier 2 module outputs |

## Rules

1. **Always use `session_id=submission.session_id`** — Never create a FileAsset without session scoping.
2. **Use absolute paths** — `local_path` must be the full filesystem path, not relative.
3. **Set `is_user_uploaded=False`** for all pipeline-generated files. Only upload handlers set `True`.
4. **Verify file exists** before registering: `if os.path.isfile(abs_path):` — prevents dangling references.
5. **Never query with bare `.all()`** on FileAsset — always filter by session or submission.
