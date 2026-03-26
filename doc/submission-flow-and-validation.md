# RNAseek — Submission Flow & Validation Reference

> Complete walkthrough of the 5-step wizard, every validation rule, and the
> backend gate that runs before the Celery worker fleet is engaged.

---

## High-Level Architecture

```
┌─────────────────────────────────────────────────────────────────┐
│  Frontend (pipeline_setup.js)                                   │
│                                                                 │
│  Step 1 ─► Step 2 ─► Step 3 ─► Step 4 ─► Step 5 ─► Submit     │
│   each "Next" click runs validateCurrentStep()                  │
│   Submit click runs validatePreSubmission() cross-check         │
└─────────────┬───────────────────────────────────────────────────┘
              │  POST /api/pipeline/core  (JSON payload)
              ▼
┌─────────────────────────────────────────────────────────────────┐
│  Backend (CorePipelineView → validators.py)                     │
│                                                                 │
│  validate_pipeline_submission(body, submission)                  │
│    ├─ CORE_VALIDATORS (10 validators, in order)                 │
│    ├─ TRACK_VALIDATORS[(input, assay)] (extensible registry)    │
│    └─ collect_warnings() (non-blocking)                         │
│                                                                 │
│  errors? → HTTP 400 {error, errors}                             │
│  pass?   → persist payload → create AnalysisJob → Celery task   │
└─────────────────────────────────────────────────────────────────┘
```

---

## Step 1 — Project Name & Data Type

### What the user does
- Enters a **submission name**.
- Selects an **input data type**: `fastq`, `alignment`, or `matrix`.
- If `fastq` is selected, picks an **assay type**: `standard_rna`, `small_rna`, `chip_seq`, or `methylation`.

### Frontend validation (`validateCurrentStep` → case 1)

| Rule                              | Error message                       |
| --------------------------------- | ----------------------------------- |
| Submission name must not be blank | *"Please enter a submission name."* |

### Wizard routing
- If `matrix` is selected, Step 3 (Genome) is skipped. Effective steps become **1 → 2 → 4 → 5**.
- Otherwise the full sequence **1 → 2 → 3 → 4 → 5** is used.

---

## Step 2 — File Upload

### What the user does
Depending on the data type chosen in Step 1:

| Data Type     | Expected files                      | Upload widget                                                      |
| ------------- | ----------------------------------- | ------------------------------------------------------------------ |
| **FASTQ**     | `.fastq.gz` / `.fq.gz` files        | Drag-and-drop zone, 25 MB concurrent chunked uploads (6 in-flight) |
| **Alignment** | `.bam` / `.cram` files              | Drag-and-drop zone, 25 MB concurrent chunked uploads (6 in-flight) |
| **Matrix**    | Single `.csv` / `.tsv` count matrix | Drag-and-drop zone, parsed client-side with PapaParse              |

### Frontend validation (`validateCurrentStep` → case 2)

#### FASTQ path

| Rule                                                                 | Error message                                                                         |
| -------------------------------------------------------------------- | ------------------------------------------------------------------------------------- |
| Library type must be selected                                        | *"Please select a library type (Single-End or Paired-End)."*                          |
| At least one FASTQ file selected or uploaded                         | *"Please upload at least one FASTQ file."*                                            |
| **Minimum sample count (SE):** at least 2 FASTQ files                | *"At least 2 FASTQ files are required for differential expression analysis."*         |
| **Minimum sample count (PE):** at least 2 read pairs (4 files)       | *"At least 2 read pairs (4 FASTQ files) are required for differential expression..."* |
| **Paired-end:** filenames must match `_R1` / `_R2` naming convention | *"N file(s) don't match \_R1/\_R2 naming convention: ..."*                            |
| **Paired-end:** equal number of R1 and R2 files                      | *"Unequal pairs: X R1 and Y R2 files."*                                               |

#### Alignment path

| Rule                                                | Error message                                                                       |
| --------------------------------------------------- | ----------------------------------------------------------------------------------- |
| At least one BAM/CRAM file selected or uploaded     | *"Please upload at least one BAM/CRAM file."*                                       |
| **Minimum sample count:** at least 2 BAM/CRAM files | *"At least 2 BAM/CRAM files (samples) are required for differential expression..."* |

#### Matrix path

| Rule                                                        | Error message                                                                           |
| ----------------------------------------------------------- | --------------------------------------------------------------------------------------- |
| A count matrix file must be uploaded                        | *"Please upload a count matrix file."*                                                  |
| Matrix must have ≥ 3 columns (gene ID + ≥ 2 samples)        | *"Matrix must have at least 3 columns (gene ID + 2 or more samples)..."*                |
| **No duplicate gene IDs** (all rows scanned)                | *"Duplicate gene IDs found: X, Y... Each gene ID must be unique."*                      |
| **No missing values** (empty, NA, NaN, null cells rejected) | *"Count matrix contains empty or missing values (NA/NaN/null). All cells must have..."* |
| All values must be integers (not TPM/FPKM)                  | *"Found non-integer values. Please upload raw integer counts..."*                       |
| No negative values                                          | *"Count matrix contains negative values..."*                                            |

### Background upload trigger
When the user clicks **Next** from Step 2, `startBackgroundUploads()` begins
concurrent chunked upload of all selected files to `POST /api/upload/chunk`.
Each file is split into 25 MB chunks with up to 6 chunks uploaded in parallel
via `uploadFileConcurrently()`. On the server, chunks are buffered to a fast
local SSD directory (`/tmp/rnaseek_uploads/`). When all chunks for a file
arrive, they are merged in order and moved to the final NFS path under
`media/sessions/<session_id>/<submission_id>/<subdir>/`.

---

## Step 3 — Reference Genome

> **Skipped entirely** when `input_data_type === "matrix"`.

### What the user does
- Selects a **reference genome** from the dropdown (e.g. `hg38`, `mm10`, `dm6`, …).
- Or selects **"custom"** and uploads:
  - A FASTA file (`.fa`, `.fasta`, optionally gzipped)
  - A GTF/GFF annotation file

### Frontend validation (`validateCurrentStep` → case 3)

| Rule                                                         | Error message                                                                      |
| ------------------------------------------------------------ | ---------------------------------------------------------------------------------- |
| A genome must be selected, or custom genome fully configured | *"Please select a reference genome or configure a custom genome."*                 |
| **Custom genome name** must match `^[a-zA-Z0-9_-]+$`         | *"Custom genome name must contain only letters, digits, hyphens, or underscores."* |

### `isGenomeValid()` logic
- If genome selector is empty → invalid.
- If genome is `"custom"`:
  - FASTQ path → both FASTA and annotation files required.
  - Alignment path → annotation file required.
  - A custom genome name must be entered.
  - Custom genome name must pass `SAFE_NAME_RE` (`^[a-zA-Z0-9_-]+$`).

---

## Step 4 — Metadata & Experimental Design

### What the user does
1. **Provides sample metadata** via one of two modes:
   - **Upload mode**: uploads a CSV where the first column is `sample` and subsequent columns are experimental variables.
   - **Manual mode**: builds metadata in a table UI by adding columns and assigning values per sample.
2. **Assigns column roles** in the Column Mapping panel:
   - **Primary group** (required) — the experimental condition column.
   - **Batch effect** (optional) — column for ComBat-seq batch correction.
   - **Covariates** (optional) — additional adjustment variables.
3. **Defines pairwise contrasts** (e.g. Treatment vs Control).

### Frontend validation (`validateCurrentStep` → case 4)

| Rule                                                                                             | Condition                      | Error message                                                                                                       |
| ------------------------------------------------------------------------------------------------ | ------------------------------ | ------------------------------------------------------------------------------------------------------------------- |
| Metadata must be configured                                                                      | Always                         | *"Please configure your metadata..."*                                                                               |
| Primary group column assigned                                                                    | Always                         | *"Please assign the primary group column..."*                                                                       |
| All contrasts must be complete (both sides filled)                                               | Contrast section visible       | *"Please complete all pairwise comparisons..."*                                                                     |
| CSV first column must be named `sample`                                                          | Upload mode                    | *"CSV must have a column named 'sample'..."*                                                                        |
| **ChIP-seq:** metadata has ≥ 1 `"input"` control and ≥ 1 treatment sample                        | `assay_type === "chip_seq"`    | *"ChIP-seq requires at least one sample labeled 'input'..."* / *"...at least one non-input (treatment/IP) sample."* |
| **Batch correction:** batch column exists in metadata                                            | Batch column selected          | *"Batch correction column '...' not found in metadata."*                                                            |
| **Batch correction:** every batch has ≥ 2 samples (ComBat-seq)                                   | Batch column selected          | *"ComBat-seq requires ≥ 2 samples per batch. Singleton batch(es): ..."*                                             |  | **Contrast level validity:** target and reference must exist in primary group values | Contrast section visible | *"Contrast target 'X' does not appear in the primary-group column."* |
| **Sanitized sample names:** every sample name must match `^[a-zA-Z0-9_-]+$`                      | Always                         | *"Sample names must contain only letters, digits, hyphens, or underscores. Invalid: ..."*                           |
| **Matrix-metadata header match:** matrix column headers must match metadata sample names exactly | `input_data_type === "matrix"` | *"Matrix column headers and metadata sample names must match exactly. Mismatched -- ..."*                           |
### Helper functions
- `validateChipSeqMetadata()` — iterates metadata rows; checks primary group column for `"input"` (case-insensitive) and at least one other value.
- `validateBatchColumn()` — reads `batchEffectSelect.value`, confirms the column exists, then counts samples per batch.
- `getActiveMetadataSamples()` — returns the current metadata rows regardless of upload or manual mode.

---

## Step 5 — Thresholds & Review

### What the user does
- Sets **Adjusted P-value** cutoff (default 0.05).
- Sets **Min / Max Log2 Fold Change** thresholds.
- Reviews a summary checklist (`validateAll()` marks each item ✓ or ✗).

### Frontend validation (`validateCurrentStep` → case 5)

| Rule                               | Error message                                             |
| ---------------------------------- | --------------------------------------------------------- |
| Adjusted P-value must be in (0, 1] | *"Adjusted P-value must be between 0 (exclusive) and 1."* |
| Min Log2FC must be < Max Log2FC    | *"Min Log2FC must be less than Max Log2FC."*              |

### `validateAll()` review checklist
Sets valid/invalid indicators for:
- Submission name
- Library type (hidden for matrix)
- Files uploaded
- Genome selected (hidden for matrix)
- Metadata configured
- Column mapping assigned

The **Submit** button is disabled until all indicators are green.

---

## Pre-Submission Cross-Check (Frontend)

When the user clicks **Submit & Launch**, `validatePreSubmission()` runs a
final cross-cutting sweep **before** setting `isSubmitting = true`:

| #   | Check                                                                                | Applicable when                |
| --- | ------------------------------------------------------------------------------------ | ------------------------------ |
| 1   | Matrix content sanity (re-validate: 3-col min, duplicate genes, missing values)      | `input_data_type === "matrix"` |
| 2   | Paired-end R1/R2 file matching (all files, including already uploaded)               | FASTQ + paired-end             |
| 3   | **Small RNA forces Single-End** (auto-corrected in UI; blocked here if still paired) | FASTQ + `small_rna`            |
| 4   | Small RNA genome must be in miRBase index; custom not allowed                        | FASTQ + `small_rna`            |
| 5   | ChIP-seq input/control split present                                                 | FASTQ + `chip_seq`             |
| 6   | Custom genome files present + name filled + **name matches SAFE_NAME_RE**            | `genome === "custom"`          |
| 7   | Batch column validation (exists, ≥ 2 per batch)                                      | Batch column selected          |
| 8   | **Contrast level validity** (target/reference exist in primary group values)         | Contrasts defined              |
| 9   | **Minimum sample count** (catch-all: >=2 samples for all data types)                 | Always (FASTQ/BAM)             |
| 10  | Metadata present and primary group assigned                                          | Always                         |

If any errors are found, they are displayed as toast notifications and
submission is blocked.

---

## Submission Sequence (Frontend → Backend)

Once `validatePreSubmission()` passes, the modal-driven sequence runs:

```
1. ensureSubmission()         →  POST /api/submission/create
2. waitForUploads()           →  await background chunk uploads
3. uploadMatrixFile()         →  (matrix only) inline upload
4. uploadCsvFile()            →  (CSV upload mode) metadata upload
5. uploadCustomGenomeFiles()  →  (custom genome) FASTA + GTF upload
6. POST /api/pipeline/core    →  JSON payload with all config
7. Redirect to /processing/<job_id>/
```

### JSON payload shape

```json
{
  "submission_id": "uuid",
  "submission_name": "My Analysis",
  "input_data_type": "fastq|alignment|matrix",
  "assay_type": "standard_rna|small_rna|chip_seq|methylation",
  "library_type": "single|paired",
  "strandedness": "unstranded|fr-firststrand|fr-secondstrand",
  "reference_genome": "hg38|mm10|...|custom",
  "custom_genome_name": "",
  "quant_level": "gene|transcript",
  "metadata_mode": "upload|manual",
  "adjusted_pvalue": 0.05,
  "min_log2fc": -1.0,
  "max_log2fc": 1.0,
  "metadata_payload": {
    "samples": [ { "sample": "s1", "condition": "treated", ... } ],
    "column_mapping": {
      "primary_group": "condition",
      "batch_effect": "",
      "covariates": []
    },
    "contrasts": [ ["treated", "control"] ]
  }
}
```

---

## Backend Validation Gate

**File:** `pipeline/validators.py`
**Entry point:** `validate_pipeline_submission(body, submission) → (errors, warnings)`

### Core Validators (executed in order for every submission)

| #   | Validator                      | What it checks                                                                                                                                                                                                                                                                                                                                                                                                                            |
| --- | ------------------------------ | ----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| 1   | `validate_base_fields`         | `input_data_type` ∈ {fastq, alignment, matrix}; `assay_type` ∈ valid set (fastq only); `library_type` ∈ {single, paired} (fastq only); `strandedness` valid (fastq only); **small_rna + paired rejected** (*"Small RNA / miRNA requires Single-End reads"*); `reference_genome` non-empty (fastq/alignment); `quant_level` ∈ {gene, transcript}; `metadata_mode` ∈ {upload, manual}; `adjusted_pvalue` ∈ (0, 1]; numeric threshold values |
| 2   | `validate_uploaded_files`      | FASTQ SE: ≥ 2 `RAW_FASTQ` assets. FASTQ PE: ≥ 4 assets (2 pairs), even count. BAM: ≥ 2 `ALIGNMENT_BAM` assets. Matrix: ≥ 1 `USER_COUNT_MATRIX` asset                                                                                                                                                                                                                                                                                      |
| 3   | `validate_custom_genome`       | If genome is `"custom"`: custom name non-empty + **passes `SAFE_NAME_RE`** (`^[a-zA-Z0-9_-]+$`); FASTQ → both FASTA + annotation; Alignment → annotation; **FASTA header check** (first non-empty line must start with `>`, supports gzip)                                                                                                                                                                                                |
| 4   | `validate_small_rna_genome`    | Small RNA: genome must be in `_MIRBASE_SPECIES_MAP`; custom not allowed                                                                                                                                                                                                                                                                                                                                                                   |
| 5   | `validate_paired_end_matching` | Paired-end FASTQ: all filenames match `_R1`/`_R2` pattern; every R1 has a matching R2 and vice versa                                                                                                                                                                                                                                                                                                                                      |
| 6   | `validate_metadata`            | Samples non-empty; first column is `"sample"` (upload mode); uploaded filenames match metadata sample names; primary group column assigned; contrasts are valid [target, reference] pairs with no self-comparisons; **contrast target/reference must exist in primary group values**; **all sample names must pass `SAFE_NAME_RE`**                                                                                                       |
| 7   | `validate_chipseq_metadata`    | ChIP-seq: ≥ 1 sample with `"input"` in primary group (control); ≥ 1 non-input sample (treatment)                                                                                                                                                                                                                                                                                                                                          |
| 8   | `validate_batch_column`        | Batch column exists in sample rows; each batch has ≥ 2 samples (ComBat-seq requirement)                                                                                                                                                                                                                                                                                                                                                   |
| 9   | `validate_matrix_content`      | Matrix file readable; **≥ 3 columns** (gene ID + 2 samples); all values numeric (first 50 rows); no negative values; not empty; **no missing/NA/NaN/null values**; **duplicate gene IDs rejected** (all rows scanned); **matrix headers must match metadata sample names exactly**                                                                                                                                                        |
| 10  | `validate_bacterial_fasta`     | Custom genome FASTA without annotation → flags for BASys2 Docker annotation (informational)                                                                                                                                                                                                                                                                                                                                               |

### Track Validators (extensible registry)

Additional validators can be registered per `(input_data_type, assay_type)` tuple:

```python
@register_track_validator("fastq", "chip_seq")
def my_custom_chip_check(body, submission):
    ...
```

These run after the 10 core validators.

### Non-Blocking Warnings (`collect_warnings`)

Warnings are returned alongside a successful response but do not block submission:

| Warning                                                                  | Condition                                |
| ------------------------------------------------------------------------ | ---------------------------------------- |
| Custom genome will trigger on-demand index build (HISAT2/BWA/Bismark)    | `reference_genome == "custom"` and FASTQ |
| Batch correction advisory ("ensure batches reflect technical variation") | `batch_effect` column is set             |

### Response format

**Validation failure (HTTP 400):**
```json
{
  "error": "First error message (backward compat)",
  "errors": ["First error", "Second error", ...]
}
```

**Success (HTTP 200):**
```json
{
  "job_id": "uuid",
  "status": "PENDING",
  "warnings": ["Custom genome will trigger an on-demand HISAT2 index build..."]
}
```

---

## Pipeline Steps Created on Success

After validation passes, `CorePipelineView` determines the pipeline steps
based on `(input_data_type, assay_type)`:

| Input Type    | Assay Type                     | Pipeline Steps                                                                                    |
| ------------- | ------------------------------ | ------------------------------------------------------------------------------------------------- |
| **FASTQ**     | `standard_rna`                 | fastqc → trimmomatic → hisat2 → featurecounts → multiqc → deseq2                                  |
| **FASTQ**     | `standard_rna` + custom genome | hisat2\_build → fastqc → trimmomatic → hisat2 → featurecounts → multiqc → deseq2                  |
| **FASTQ**     | `small_rna`                    | fastqc → trimmomatic → bowtie\_mirna → mirna\_quantify → multiqc → deseq2                         |
| **FASTQ**     | `chip_seq`                     | fastqc → trimmomatic → bwa\_align → macs2\_peaks → featurecounts → multiqc → deseq2               |
| **FASTQ**     | `methylation`                  | fastqc → trimmomatic → bismark\_prep → bismark\_align → bismark\_extract → multiqc → diff\_methyl |
| **Alignment** | —                              | featurecounts → deseq2                                                                            |
| **Matrix**    | —                              | deseq2                                                                                            |

An `AnalysisJob` record is created and dispatched to the Celery worker via
`run_core_pipeline.apply_async()`. The user is redirected to `/processing/<job_id>/`.

---

## Validation Layer Summary

```
User clicks "Next"          User clicks "Submit"          POST /api/pipeline/core
       │                           │                              │
       ▼                           ▼                              ▼
validateCurrentStep()      validatePreSubmission()      validate_pipeline_submission()
  (per-step rules)         (cross-cutting sweep)          (10 core + track validators)
       │                           │                              │
       ▼                           ▼                              ▼
  Toast errors              Toast errors                 HTTP 400 {errors}
  Block navigation          Block submission             ── OR ──
                                                         HTTP 200 + Celery dispatch
```

Three layers ensure that:
1. **Step-level** — the user cannot proceed past a step with incomplete data.
2. **Pre-submission** — cross-cutting rules catch conflicts across steps (e.g. ChIP-seq metadata vs. genome).
3. **Backend** — server-side gate validates everything again using the real database state (uploaded files, etc.) before any Celery task is created.
