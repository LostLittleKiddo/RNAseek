# RNAseek -- Project Progress

> Last updated after: **Phase 5c: Stage 2 Epigenomics, Assay Selector & Full Genome Roster**

---

## 1. Environment & Installation

| Component              | Detail                                                                                                                                                               |
| ---------------------- | -------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| **Conda env**          | `rnaseek` (Python 3.11, R 4.3)                                                                                                                                       |
| **Python framework**   | Django 5.2.12                                                                                                                                                        |
| **Env config**         | `python-dotenv` for `.env` loading; auto-detects `RNASEEK_ENV` (development/production)                                                                              |
| **Async queue**        | Celery 5.6 + Redis (broker `redis://127.0.0.1:6379/0`)                                                                                                               |
| **Database**           | SQLite (`db.sqlite3`) — dev-mode, PostgreSQL-ready via `psycopg2-binary`                                                                                             |
| **Bioinformatics CLI** | FastQC, Trimmomatic, HISAT2, SAMtools, Subread (featureCounts), StringTie — all conda-installed                                                                      |
| **R / Bioconductor**   | DESeq2, SVA, DEXSeq, IsoformSwitchAnalyzeR, TCGAbiolinks, mixOmics, WGCNA                                                                                            |
| **Python scientific**  | NumPy, Pandas, SciPy, scikit-learn, Matplotlib, Plotly, Scanpy, Squidpy, scVI-tools, Tangram, GSEApy, Lifelines, PyWGCNA, MultIQC, and more (see `requirements.txt`) |
| **Env files**          | `environment.yml` (conda channels + R/CLI), `requirements.txt` (pip packages)                                                                                        |

---

## 2. Backend Architecture

### 2.1 Django Project Structure

```
config/             ← Django project package
  settings.py       ← Installed apps, middleware, DB, Celery
  urls.py           ← Root URL → includes pipeline.urls
  celery.py         ← Celery app factory + autodiscover
  wsgi.py / asgi.py ← WSGI/ASGI entry points

pipeline/           ← Main Django app
  models.py         ← Session, AnalysisSubmission, FileAsset, AnalysisJob
  views/            <- Package: pages.py (7 TemplateViews) + api.py (7 API views)
  urls.py           <- 7 page routes + 7 API routes
  middleware.py     ← AnonymousSessionMiddleware
  tasks/            ← Package: 11 modules (core, routes, helpers, constants, genome resolvers)
  stats/            ← Package: 5 modules (core, DESeq2, helpers, plots, R bridge)
  context_processors.py ← Session context injector (session_id for all templates)
  admin.py          ← (default)
  apps.py           ← PipelineConfig
```

### 2.1b Environment Configuration (Dev/Prod)

Settings auto-detect the environment via `RNASEEK_ENV` env variable (defaults to `development`).

| Setting             | Development (default)      | Production (`RNASEEK_ENV=production`)      |
| ------------------- | -------------------------- | ------------------------------------------ |
| `DEBUG`             | `True`                     | `False`                                    |
| `SECRET_KEY`        | Insecure default           | From `DJANGO_SECRET_KEY` env var           |
| `ALLOWED_HOSTS`     | `localhost,127.0.0.1`      | From `DJANGO_ALLOWED_HOSTS` env var        |
| `DATABASES`         | SQLite (`db.sqlite3`)      | PostgreSQL (from `DB_*` env vars)          |
| `CELERY_BROKER_URL` | `redis://127.0.0.1:6379/0` | From `CELERY_BROKER_URL` env var           |
| `CELERY_EAGER`      | `0` (async, needs worker)  | N/A (always uses worker)                   |
| `STATIC_ROOT`       | `staticfiles/`             | `staticfiles/`                             |
| `WhiteNoise`        | Enabled                    | Enabled (CompressedManifestStaticFiles)    |
| `MEDIA_ROOT`        | `media/`                   | From `MEDIA_ROOT` env var                  |
| Security headers    | Off                        | HSTS, SSL redirect, secure cookies enabled |

Files: `.env` (local dev, gitignored), `.env.prod` (documented template for production).

### 2.2 Database Schema (4 models)

**Session**
- `session_id` — UUID primary key (auto-generated)
- `created_at` — DateTimeField (auto)
- `expires_at` — DateTimeField (default: now + 14 days)
- Property: `is_expired` → True if past `expires_at`

**AnalysisSubmission**
- `submission_id` — UUID primary key
- `session` — FK → Session (cascade)
- `input_data_type` — choices: `fastq`, `alignment`, `matrix` (default: `fastq`)
- `library_type` — CharField (single/paired)
- `strandedness` — CharField (unstranded/fr-firststrand/fr-secondstrand)
- `reference_genome` — CharField
- `custom_genome_name` — CharField (optional)
- `metadata_mode` — CharField (upload/manual)
- `adjusted_pvalue` — FloatField (default: 0.05)
- `min_log2fc` / `max_log2fc` — FloatField
- `metadata_payload` — JSONField (samples, column_mapping, contrasts, quant_level)
- `created_at` — DateTimeField (auto)

**FileAsset**
- `id` — UUID primary key
- `session` — FK → Session (cascade)
- `submission` — FK → AnalysisSubmission (nullable, cascade)
- `file_role` — choices: `RAW_FASTQ`, `COUNT_MATRIX`, `H5AD_PSEUDO`, `HE_IMAGE_USER`, `HE_IMAGE_GENERIC`, `CUSTOM_GENOME_FASTA`, `CUSTOM_GENOME_ANNOTATION`, `METADATA_CSV`, `ALIGNMENT_BAM`, `USER_COUNT_MATRIX`, `PEAK_FILE`, `METHYLATION_REPORT`
- `local_path` — CharField (500)
- `is_user_uploaded` — Boolean

**AnalysisJob**
- `job_id` — UUID primary key (doubles as Celery task ID)
- `session` — FK → Session (cascade)
- `module_name` — CharField (50)
- `status` — choices: `PENDING`, `RUNNING`, `SUCCESS`, `FAILED`
- `result_payload` — JSONField
- `step_progress` — JSONField (pipeline steps, per-step status, timestamps)
- `created_at` — DateTimeField (auto)
- `updated_at` — DateTimeField (auto)

### 2.3 Middleware

**AnonymousSessionMiddleware** — Cookie-based anonymous sessions:
- Reads `Session_ID` HttpOnly cookie on every request
- Validates against DB (exists + not expired)
- Creates new Session if missing/expired
- Attaches `request.session_obj` for all views
- 14-day max_age, SameSite=Lax

### 2.4 API Endpoints

| Method | Endpoint                  | Purpose                                                                                      |
| ------ | ------------------------- | -------------------------------------------------------------------------------------------- |
| POST   | `/api/submission/create`  | Create a new AnalysisSubmission, return its UUID for scoping uploads                         |
| POST   | `/api/upload/chunk`       | Receive 5 MB file chunk, route to subdirectory by file_role, create FileAsset on final chunk |
| POST   | `/api/pipeline/core`      | Validate & trigger Core Pipeline by entry point (fastq/alignment/matrix), returns `job_id`   |
| GET    | `/api/jobs/<uuid>/`       | Poll job status (PENDING/RUNNING/SUCCESS/FAILED) + result payload                            |
| GET    | `/api/session/assets`     | List all FileAssets for current session                                                      |
| GET    | `/api/download/<uuid>/`   | Serve pipeline output files for download (FileAsset lookup)                                  |
| POST   | `/api/modules/<name>/run` | Trigger a Tier 2 module (validates Stage 2 complete, dispatches Celery task)                 |

### 2.5 Celery Tasks — Core Pipeline Router

`run_core_pipeline(session_id, submission_id)` — Router that reads `submission.input_data_type` and dispatches:

| Route                  | Entry Point          | Steps                                                                                                |
| ---------------------- | -------------------- | ---------------------------------------------------------------------------------------------------- |
| **Route A: FASTQ**     | `_route_fastq()`     | FastQC → Trimmomatic → HISAT2 → featureCounts → MultiQC → Stage 2 (DESeq2) — **parallel per sample** |
| **Route B: Alignment** | `_route_alignment()` | CRAM→BAM conversion (parallel) → BAM indexing → featureCounts → Stage 2 (DESeq2)                     |
| **Route C: Matrix**    | `_route_matrix()`    | CSV/TSV validation (non-numeric/negative/empty checks) → canonical copy → Stage 2 (DESeq2)           |

Shared helpers:
- `_resolve_genome(genome_key, work_dir, build_hisat2)` — Resolves pre-indexed or custom genome paths
- `_run_featurecounts(bam_files, gtf, strandedness, quant_level, library_type, work_dir)` — Runs featureCounts + CSV conversion
- `_pair_fastqs(fastq_paths)` — Groups paired-end FASTQs by filename prefix
- `_parse_metadata_csv(csv_path)` — Parses uploaded metadata CSV into list of dicts
- `_strandedness_hisat2()` / `_strandedness_fc()` / `_feature_type()` — Parameter mapping helpers
- `_featurecounts_to_csv()` — Converts featureCounts output to clean CSV

---

## 3. Frontend Design & Implementation

### 3.1 Design System

**Color Palette** (extracted from RNAseek logo):
- Navy: `#02426e` — primary dark, navbar, headings
- Blue-mid: `#14648e` — secondary
- Blue-teal: `#248193` — accents
- Teal: `#059a98` — primary action color (CTA buttons, links)
- Teal-light: `#3badae` — hover states, secondary buttons
- Mint: `#6abfb0` — success/positive
- Mint-pale: `#9fe0e2` — light backgrounds

**Typography:**
- Body: Roboto (300, 400, 500, 700) via Google Fonts
- Headings: Maven Pro (400, 500, 600, 700, 900) via Google Fonts

**Icons:** Bootstrap Icons v1.11.3 (CDN)

**CSS Architecture:**
- `pipeline/static/pipeline/css/variables.css` — All design tokens as CSS custom properties
- `pipeline/static/pipeline/css/global.css` — Full component library (navbar, buttons, cards, tables, badges, forms, drop zones, modals, progress bars, hero sections, responsive breakpoints)

### 3.2 Templates (7 pages + base)

| Template              | URL                         | Description                                                                                                              |
| --------------------- | --------------------------- | ------------------------------------------------------------------------------------------------------------------------ |
| `base.html`           | –                           | Base layout: navbar (logo + 4 nav links + workflow step indicator + session badge), footer, static/font imports          |
| `home.html`           | `/`                         | Hero section with gradient background, 4-card capabilities, 12-module grid, "How It Works" 4-step flow                   |
| `tutorials.html`      | `/tutorials/`               | File format guide, metadata mapping example, workflow diagram placeholder, reference genomes table                       |
| `workspaces.html`     | `/workspaces/`              | 14-day expiry warning banner, jobs table with status badges, empty state                                                 |
| `new_submission.html` | `/analysis_submission/new/` | Entry point selector (FASTQ/BAM/Matrix), 3 conditional upload columns, genome select, metadata mapping wizard            |
| `processing.html`     | `/processing/<job_id>/`     | 6-step pipeline progress tracker, animated progress bar, auto-polling (3s), completion redirect                          |
| `core_hub.html`       | `/hub/<job_id>/`            | Downloads section, 4 visualization plots, 11 module cards (categorized), deconvolution gateway, 3 locked advanced spokes |
| `advanced.html`       | `/advanced/<job_id>/`       | Three spoke workspaces: Trajectory Analysis, Spatial Transcriptomics, Spatial Autocorrelation                            |

### 3.3 JavaScript

| File                                            | Purpose                                                                                                                                                                                                    |
| ----------------------------------------------- | ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `pipeline/static/pipeline/js/pipeline_setup.js` | Entry point selector (FASTQ/BAM/Matrix), file drop zones (FASTQ + BAM + Matrix), chunked upload, PapaParse CSV validation, metadata mapping, contrast builder, conditional validation, pipeline submission |
| `pipeline/static/pipeline/js/core_hub.js`       | Module card → modal (with module-specific inputs), module submission, deconvolution gateway, spoke unlock polling, download links                                                                          |
| (inline in `processing.html`)                   | Job status polling every 3s, step indicator updates, completion redirect                                                                                                                                   |

> **Note:** `setup_wizard.js` is an orphan legacy file from an earlier wizard-based design. `index.html` is an orphan Bootstrap template. Neither is referenced by active views.

### 3.4 Django Page Views (7 new TemplateViews)

- `HomeView` → home.html (session_id context)
- `TutorialsView` → tutorials.html
- `WorkspacesView` → workspaces.html (jobs list)
- `NewSubmissionView` → new_submission.html (session_id)
- `ProcessingView` → processing.html (job details)
- `CoreHubView` → core_hub.html (job details + has_h5ad flag for spoke locking)
- `AdvancedView` → advanced.html (job details)

### 3.5 Static Assets

```
pipeline/static/pipeline/
├── css/
│   ├── variables.css           ← Design tokens
│   ├── global.css              ← Component styles
│   └── pipeline_setup.css      ← Pipeline setup page styles (entry point cards, drop zones)
├── js/
│   ├── pipeline_setup.js       ← Entry point selector, upload, metadata, submission logic
│   ├── core_hub.js             ← Module & deconvolution logic
│   └── setup_wizard.js         ← (orphan) Legacy wizard script, unused
└── images/
    ├── rnaseek_logo.png        ← Icon logo (543×389)
    ├── rnaseek_logo_name.png   ← Full logo with name (1408×768)
    ├── Home1.png               ← Hero section image
    ├── Home2.png               ← Feature section image
    └── Home3.png               ← Feature section image
```

---

## 4. What Has Been Achieved

- [x] Conda environment `rnaseek` fully configured (Python 3.11, R 4.3, all bioinformatics tools)
- [x] Django 5.2 project scaffolded with `config` package and `pipeline` app
- [x] SQLite database with 4 models (Session, AnalysisSubmission, FileAsset, AnalysisJob) — migrations applied (0001–0004)
- [x] Celery + Redis integration configured with full pipeline router
- [x] Cookie-based anonymous session middleware (14-day expiry, HttpOnly)
- [x] Chunked file upload API (5 MB chunks, path-traversal safe, role-aware subdirectory routing)
- [x] Core Pipeline trigger API + job status polling API + session assets API
- [x] Complete CSS design system extracted from logo colors (navy-teal-mint palette)
- [x] Full component library (buttons, cards, tables, badges, forms, modals, etc.)
- [x] 7-page template system with responsive layout and consistent theming
- [x] Setup wizard with entry point selector and conditional upload UI
- [x] Real-time processing monitor with animated progress indicators
- [x] Core Hub dashboard with 11 module cards, visualization placeholders, download section
- [x] Deconvolution gateway with atlas selection
- [x] Advanced workspace page with 3 spoke analyses
- [x] All URL routes verified and Django system check passes
- [x] Dev/prod environment auto-detection via `RNASEEK_ENV` env variable (python-dotenv)
- [x] Production-ready settings: PostgreSQL, HSTS, SSL redirect, secure cookies, `STATIC_ROOT`
- [x] `.env` + `.env.example` for environment configuration (`.env` gitignored)
- [x] Library type selection UI: single-end / paired-end radio cards with naming convention tips
- [x] Reference genome dropdown: Human (hg38), Mouse (mm10/mm39), Rat (rn7), Drosophila (dm6), Zebrafish (danRer11), C. elegans (wbcel235), Yeast (r64), Arabidopsis (araTha), Chicken (galGal6), Pig (susScr11), Custom Genome
- [x] Custom genome upload: genome name input + FASTA file upload + GTF/GFF annotation upload (chunked)
- [x] FileAsset model extended with `ALIGNMENT_BAM` and `USER_COUNT_MATRIX` roles (10 total roles)
- [x] Chunk upload API extended with `file_role` parameter and subdirectory routing (raw/, aligned/, counts/, custom_genome/, metadata/)
- [x] PapaParse 5.4.1 integration for browser-side CSV parsing (metadata + count matrix pre-flight)
- [x] Dynamic metadata mapping with column mapping, contrast builder, sample validation
- [x] Stage 2 statistical engine: DESeq2, Combat-seq batch correction, Mahalanobis PCA outlier detection
- [x] Yeast R64-1-1 reference genome built (FASTA, GTF, HISAT2 index)

### Assay Type Selector & Epigenomics Stage 2 (NEW)

- [x] **Assay type selector UI:** 4 radio cards (Standard RNA, Small RNA, ChIP-seq, DNA Methylation) shown for FASTQ entry point; dynamic help text for ChIP-seq and Methylation metadata guidance
- [x] **ChIP-seq Stage 2:** Consensus peak SAF generation (`bedtools merge` → SAF format), `featureCounts -F SAF` on treatment BAMs, `raw_counts.csv` → DESeq2 differential binding analysis via `run_stage2_stats()`
- [x] **Methylation Stage 2 scaffold:** `_methylkit.py` R-bridge module via rpy2 — `methRead` → `filterByCoverage` → `normalizeCoverage` → `unite` → `calculateDiffMeth` → CSV export; PCA, volcano, MA plot data generation
- [x] **Methylation track wired:** `_route_methylation()` calls `run_differential_methylation()` after Bismark extraction + MultiQC; `diff_methyl` step tracked in frontend
- [x] **All 11 reference genomes present:** hg38, mm39, mm10, rn7, danRer11, galGal6, susScr11, dm6, wbcel235, araTha, r64 — HISAT2 indexes + FASTA in all directories; genome dropdown in frontend lists all 10 user-facing species + Custom option
- [x] **Pipeline step definitions updated:** `api.py` and `pages.py` include ChIP-seq featureCounts+DESeq2 steps and methylation diff_methyl step

### Dynamic Pipeline Entry Points (NEW)

- [x] **3 entry points:** FASTQ files, BAM/CRAM alignments, Count Matrix (CSV/TSV)
- [x] **`InputDataType` model field** on AnalysisSubmission with choices: `fastq`, `alignment`, `matrix`
- [x] **Frontend entry point selector:** 3 radio cards with icons, descriptions, and pipeline tool tags
- [x] **Conditional UI:** Columns (FASTQ, BAM, Matrix, Genome) show/hide based on selected entry point
- [x] **BAM upload:** Drop zone, file pills, chunked upload with `ALIGNMENT_BAM` role → `aligned/` subdirectory
- [x] **Matrix upload:** Drop zone, PapaParse pre-flight validation (non-numeric, negative, float warnings), preview table
- [x] **Conditional validation (Django):** `CorePipelineView` validates differently per entry type — genome not required for matrix, FASTA not required for alignment custom genome, library_type/strandedness only for FASTQ
- [x] **Celery task router:** `run_core_pipeline` dispatches to `_route_fastq()`, `_route_alignment()`, or `_route_matrix()`
- [x] **Route A (FASTQ):** Full pipeline — FastQC → Trimmomatic → HISAT2 → featureCounts → MultiQC → Stage 2
- [x] **Route B (Alignment):** CRAM→BAM conversion → BAM indexing → featureCounts → Stage 2
- [x] **Route C (Matrix):** CSV/TSV loading → validation (non-empty, all-numeric, non-negative) → canonical copy → Stage 2
- [x] **Shared helpers:** `_resolve_genome()` (pre-indexed + custom, optional HISAT2 build), `_run_featurecounts()` (featureCounts + CSV conversion)
- [x] **Entry point CSS:** Responsive 3-column card grid, selected state with teal accent, icon circles, pipeline tool tags
- [x] **Test suite:** 32/32 Django tests + 26/26 Stage 2 stats tests pass — model tests (6), view validation tests (18), upload routing tests (3), task router tests (8), Stage 2 tests (formula, filtering, outliers, alignment, DESeq2×4)

### Test Suite Structure

```
test/
├── __init__.py
├── test_entry_points.py   ← 32 Django TestCase tests (models, views, uploads, task routing)
├── test_stage2.py         ← 26 standalone Stage 2 stats tests (DESeq2, ComBat, outliers)
└── test_e2e.py            ← Full E2E pipeline test (synthetic yeast FASTQ → Stage 1 → Stage 2)
```

### Frontend Refinement (UI/UX Overhaul) (NEW)

- [x] **Card alignment (Matrix mode):** 3-card centered grid when Count Matrix entry point is selected (`.setup-grid.matrix-mode` with `repeat(3, 1fr)` and responsive breakpoints)
- [x] **Threshold summary theming:** Refactored `#threshold-summary` to use global design tokens (`--rna-grey-50`, `--rna-grey-200`) with a left-border accent (`--rna-teal`) matching the site's info-box pattern
- [x] **Protected condition column:** `condition` is a mandatory default column in the Manual Metadata Builder; delete functionality is disabled for it (`.group-chip.protected` styling, JS guard in `renderColumnChips`)
- [x] **Condition input section:** Dedicated "Define Conditions" builder adjacent to "Define Metadata Columns" inside a side-by-side `.metadata-builder-top` flex layout; user-defined condition values populate a `<select>` dropdown in the metadata table instead of free-text input
- [x] **Horizontal roles + contrast layout:** Assign Column Roles and Dynamic Contrast Builder placed on the same row via `.roles-contrast-row` flex container with `align-items: stretch` for equal-height behavior
- [x] **Equal height + whitespace cleanup:** Both panels stretch to the height of the taller component; excessive vertical margins removed; consistent padding and border styling across panels
- [x] **Responsive breakpoints:** Matrix-mode grid collapses at 1400px/768px; metadata-builder-top stacks vertically at 768px; roles-contrast-row stacks at 900px

### Frontend Refinement Phase 2 (Robustness & UX Fixes) (NEW)

- [x] **Scrollable file pills:** Selected-files list (`#file-pills`, `#bam-file-pills`) capped at `max-height: 5.6rem` (~3 rows) with `overflow-y: auto` — prevents infinite page growth when many files are selected
- [x] **Threshold summary fix:** Rewrote `.threshold-summary` flex to `align-items: center; flex-wrap: wrap; gap: .25rem;` and wrapped sentence text in a `<span>` to prevent flex children from splitting into separate columns
- [x] **Column-agnostic selectable values:** Replaced flat `conditionValues[]` array with `columnSelectableValues{}` object (keyed by column name). New `<select id="condition-target-column">` dropdown lets users choose which column to define selectable values for. Values show as chips with `(column)` labels. Metadata table cells auto-switch between `<select>` and `<input>` per column.
- [x] **State reset on entry point switch:** New `resetMetadataState()` function wipes manual columns (back to `["condition"]`), selectable values, column mapping, contrasts, parsed CSV data, manual table, and contrast list DOM. Called automatically when the user switches between FASTQ / BAM / Matrix entry points.
- [x] **Condition target dropdown sync:** `syncConditionTargetDropdown()` keeps the target column `<select>` in sync with `manualColumns` — called on init, column add, column remove, and state reset.
- [x] **Bulletproof error handling:** Null-guards on condition builder DOM refs, try-catch around pipeline submission fetch, contrast list DOM cleared on reset, network errors surfaced to user via alert.
- [x] **Production readiness:** All static assets via `{% static %}` tags, no hardcoded URLs, all fetch calls use relative paths, `STATIC_ROOT` configured for `collectstatic`, `backdrop-filter` gracefully degrades on older Firefox.

### Parallelism & Speed Optimization + Celery Fix (NEW)

- [x] **Dynamic CPU detection:** `_CPU_COUNT = os.cpu_count()` at module level; all tool thread counts derived automatically (`_TOOL_THREADS = CPU//2`, `_PARALLEL_SAMPLES = CPU//_TOOL_THREADS`)
- [x] **Parallel Trimmomatic:** Per-sample trimming runs concurrently via `ThreadPoolExecutor(max_workers=_PARALLEL_SAMPLES)` for both single-end and paired-end
- [x] **Parallel HISAT2 alignment:** Per-sample alignment + BAM sorting + indexing runs concurrently via `ThreadPoolExecutor`
- [x] **Parallel CRAM→BAM conversion (Route B):** All CRAM-to-BAM conversions and BAM indexing run in parallel via `ThreadPoolExecutor`
- [x] **Scaled tool threads:** FastQC uses all CPUs (`-t {_CPU_COUNT}`), HISAT2 uses `_TOOL_THREADS` per sample (`-p`), samtools uses `_TOOL_THREADS//2` (`-@`), featureCounts uses all CPUs (`-T`), hisat2-build uses all CPUs (`-p`)
- [x] **BiocParallel for DESeq2:** Registers `MulticoreParam` backend with `_R_CORES` workers; `DESeq(dds, parallel=TRUE)` enables parallel dispersion estimation and Wald tests
- [x] **Celery eager mode toggle:** `CELERY_EAGER` env var (default `"1"`) controls synchronous vs async task execution in dev. Set `CELERY_EAGER=0` to route tasks to a real Celery worker
- [x] **Celery worker concurrency:** `CELERY_WORKER_CONCURRENCY = os.cpu_count()` in settings
- [x] **Celery worker verified:** `celery -A config worker -l info` starts correctly — concurrency 20 (prefork), connected to Redis, task `pipeline.tasks.run_core_pipeline` autodiscovered
- [x] **Test fixes:** 3 validation-only tests (`test_alignment_custom_genome_only_needs_gtf`, `test_matrix_no_genome_required`, `test_matrix_no_library_type_required`) now properly mock task dispatch to avoid eager-mode file I/O failures
- [x] **Test `__init__.py`:** Added `test/__init__.py` to make test directory importable as a Django test package
- [x] **All tests pass:** 32/32 Django tests (`test_entry_points`) + 26/26 Stage 2 stats tests (`test_stage2`) — all green

---

## 4b. Custom Genome, GTF/GFF, Metadata Validation & Interactive Plots

### Custom Genome HISAT2 Index Build Step

| Change                                 | Detail                                                                                                                                                                                              |
| -------------------------------------- | --------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| **Backend (`tasks.py`)**               | Moved `hisat2-build` out of `_resolve_genome()` into `_route_fastq()` as a tracked pipeline step (`hisat2_build`). Custom genomes now show as a distinct step in the processing page.               |
| **Views (`views.py`)**                 | Conditionally inserts `"hisat2_build"` at position 0 of the pipeline steps list when `reference_genome == "custom"`.                                                                                |
| **Frontend (`processing.html`)**       | Added a new `<div class="pipeline-step" data-step="hisat2_build">` element ("Build HISAT2 Index") before FastQC. The JS show/hide logic automatically manages visibility based on `pipeline_steps`. |
| **Caution UI (`new_submission.html`)** | Added a yellow warning banner in the custom genome section explaining that index building can take 30 min–several hours depending on genome size.                                                   |

### GTF/GFF Dual Annotation Format Support

| Change                        | Detail                                                                                                                                                                                        |
| ----------------------------- | --------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| **`_run_featurecounts()`**    | Detects annotation file extension (`.gff`, `.gff3` vs `.gtf`). For GFF/GFF3 files, passes `-F GFF` flag to featureCounts.                                                                     |
| **`_detect_gff_gene_attr()`** | New helper that peeks at the first 200 feature lines of a GFF file to determine the correct gene ID attribute (`gene_id` vs `ID`). Falls back to `ID` (GFF3 standard) if `gene_id` not found. |

### Metadata "sample" Column Validation

| Change                             | Detail                                                                                                                                                                                                |
| ---------------------------------- | ----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| **Frontend (`pipeline_setup.js`)** | After PapaParse completes CSV parsing, validates that the first column header is named `"sample"` (case-insensitive). Shows an error message in the card if validation fails.                         |
| **Backend (`views.py`)**           | Server-side validation in `CorePipelineView.post()`: for `metadata_mode == "upload"`, checks that the first key of the sample dicts is `"sample"`. Manual mode (which uses `_sample_name`) is exempt. |

### 5 Interactive Plotly.js Plots

| Plot        | Data Source                   | Detail                                                                                                                              |
| ----------- | ----------------------------- | ----------------------------------------------------------------------------------------------------------------------------------- |
| **PCA**     | Normalized counts from DESeq2 | Log2-transformed, sklearn PCA, colored by primary_group. Shows variance explained percentages.                                      |
| **UMAP**    | Normalized counts from DESeq2 | PCA pre-reduction → umap-learn UMAP (n_neighbors auto-capped for small sample sizes). Gracefully skips if umap-learn not installed. |
| **Volcano** | First DEG results CSV         | log2FC vs -log10(padj), three categories (up/down/not-sig), threshold lines drawn.                                                  |
| **MA**      | First DEG results CSV         | log10(baseMean) vs log2FC, significant genes highlighted in red.                                                                    |
| **Heatmap** | Normalized counts + DEG table | Z-score normalized expression of top 50 significant DEGs. Blue-white-red colorscale. Group annotations per sample.                  |

| Component           | Change                                                                                                                                                                                                                                                                                     |
| ------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------ |
| **`_plots.py`**     | New function `_generate_plot_data()` computes JSON-serializable data for all 5 plots. Called at the end of `run_stage2_stats()`. Helpers: `_build_group_map()`, `_compute_pca_data()`, `_compute_umap_data()`, `_compute_volcano_data()`, `_compute_ma_data()`, `_compute_heatmap_data()`. |
| **`core_hub.html`** | Added Plotly.js CDN (v2.35.2). `core_hub.js` fetches job payload via `/api/jobs/<id>/` and renders all 5 plots with consistent styling (transparent background, Inter font, hover tooltips).                                                                                               |
| **Plot data flow**  | `stats.py` → `result_payload["plot_data"]` → `JobStatusView` JSON response → Plotly.js rendering in browser.                                                                                                                                                                               |

### Test Results

- **32/32 unit tests pass** (Django test runner)
- All Python files compile without errors

---

## 4c. Processing UX Overhaul, Strict Metadata Matching & Production Hardening

### Server-Side Pipeline Step Filtering

| Change                            | Detail                                                                                                                                                                                                                                                                   |
| --------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------ |
| **`views.py` (`ProcessingView`)** | `get_context_data()` now reads `job.step_progress["pipeline_steps"]` and intersects it with the full `PIPELINE_STEPS` list. Only the steps that actually apply to the current job are rendered in the template — no more showing all 7 steps then hiding via JavaScript. |
| **`processing.html`**             | Removed the `stepsInitialized` JavaScript variable and the client-side show/hide logic that ran on first WebSocket/poll response. Steps are now correct from initial page load.                                                                                          |

### Strict Metadata Sample Matching

| Change                             | Detail                                                                                                                                                                                                                                                                                              |
| ---------------------------------- | --------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| **Frontend (`pipeline_setup.js`)** | Rewrote `getFilteredCsvRows()` — replaced loose prefix matching with strict `Set`-based lookup. Added `stripExtension()` helper. `isMetadataValid()` now requires ALL uploaded samples to have a matching metadata row. `renderCsvPreview()` shows match count and lists unmatched samples by name. |
| **Backend (`views.py`)**           | Added ~40 lines of server-side metadata sample matching validation in `CorePipelineView.post()`. Builds `expected_stems` from uploaded FileAssets using regex for paired-end prefix extraction (`_R[12]` stripping), then checks all stems exist in metadata's sample column.                       |
| **`new_submission.html`**          | Updated the metadata CSV format hint — now shows filename stems without extensions, with explicit paired-end naming guidance (use prefix before `_R1`/`_R2`).                                                                                                                                       |

### Dev Dataset Metadata

| Change                                       | Detail                                                                                                                                                                                                                                                                 |
| -------------------------------------------- | ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| **`rnaseek_dev_dataset/metadata.csv`** (NEW) | Created correct metadata file with 6 rows matching the actual dev FASTQ filenames: `GSM9346166_Unstressed_Rep1_dev` through `GSM9346172_NaCl_Rep3_dev`. Columns: `sample`, `condition`, `batch`. Original `metadata_long.csv` (88 rows, wrong sample names) preserved. |

### Full-Screen Overlay Cards

| Change                | Detail                                                                                                                                                                                                                                                      |
| --------------------- | ----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| **`processing.html`** | Replaced inline error/result divs with full-screen overlay implementation using `.pipeline-overlay` (centered card), `.pipeline-overlay-backdrop` (dark blur background). Removed `window.alert()` calls. Overlay animates in with a fade+scale transition. |

### Page Reload Prevention

| Change                | Detail                                                                                                                                                                                                       |
| --------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------ |
| **`processing.html`** | Added `blockPageReload()` function with `beforeunload` handler, F5/Ctrl+R keyboard interception, and an `allowNavigation` flag that exempts action links inside overlays ("View Results" / "Back to Setup"). |

### Production Static File Serving (WhiteNoise)

| Change                 | Detail                                                                                                                                                                                                        |
| ---------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| **`settings.py`**      | Added `whitenoise.middleware.WhiteNoiseMiddleware` after `SecurityMiddleware`. Changed `STATIC_ROOT` to always be set (was `None` in dev). Added `STORAGES` dict with `CompressedManifestStaticFilesStorage`. |
| **`requirements.txt`** | Added `whitenoise>=6.6,<7.0`.                                                                                                                                                                                 |
| **`collectstatic`**    | 138 static files collected, 372 post-processed (gzip + brotli compressed with content hashes).                                                                                                                |

### Production Deployment Documentation

| Change                                         | Detail                                                                                                                                                                                                                                                                                                                        |
| ---------------------------------------------- | ----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| **`doc/Production Deployment Guide.md`** (NEW) | Comprehensive 19-section guide covering: system packages, conda setup, PostgreSQL, Redis, env vars, Django migrations, Daphne ASGI server, Celery worker, Nginx reverse proxy (with WebSocket support), SSL/HTTPS via Certbot, systemd services, session cleanup cron, firewall, verification checklist, and troubleshooting. |

### .gitignore Overhaul

| Change           | Detail                                                                                                                                                                                                                                                                                             |
| ---------------- | -------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| **`.gitignore`** | Rewrote from generic template to RNAseek-specific rules. Now excludes: reference genomes (44 GB), media uploads, dev dataset FASTQs, all bioinformatics file types (`.ht2`, `.fa`, `.gtf`, `.bam`, `.cram`, `.fastq.gz`, `.h5ad`, etc.), database, staticfiles output, R artifacts, and IDE files. |

---

## 4d. Phase 4 Hub Engine: Multi-Assay Pipeline Tracks

### Overview

Extended the Core Pipeline router from a single RNA-seq track to a multi-assay hub engine. The pipeline now dispatches by both `input_data_type` (fastq/alignment/matrix) and `assay_type` (standard_rna/small_rna/chip_seq/methylation) using the Facade Pattern. All tracks share common FastQC, Trimmomatic, and MultiQC steps via extracted helpers to eliminate code duplication.

### Model Changes

| Change                            | Detail                                                                                                  |
| --------------------------------- | ------------------------------------------------------------------------------------------------------- |
| **`AssayType` choices**           | New `TextChoices` class on `AnalysisSubmission`: `standard_rna`, `small_rna`, `chip_seq`, `methylation` |
| **`assay_type` field**            | CharField(max_length=20) with default `standard_rna` — determines pipeline track for FASTQ entry        |
| **`PEAK_FILE` FileRole**          | New FileAsset role for MACS2 narrowPeak/broadPeak output files                                          |
| **`METHYLATION_REPORT` FileRole** | New FileAsset role for Bismark cytosine methylation reports                                             |
| **Migration**                     | `0007_add_assay_type_and_track_roles` — adds field + updates FileRole choices                           |

### Track B: Small RNA / miRNA Pipeline (`_route_small_rna`)

| Step                    | Tool              | Detail                                                                                                        |
| ----------------------- | ----------------- | ------------------------------------------------------------------------------------------------------------- |
| 1. FastQC               | FastQC            | Quality control on raw reads (shared helper)                                                                  |
| 2. Trimmomatic          | Trimmomatic       | Adapter trimming with `MINLEN:18` (not 36) for ultra-short miRNA reads (~22 bp)                               |
| 3. Bowtie Alignment     | Bowtie v1         | Maps against miRBase database (not whole genome). Flags: `-v 1 --best --strata -m 5 --norc -S` for 22bp reads |
| 4. miRNA Quantification | samtools idxstats | Per-miRNA read counts — each miRBase reference is a single miRNA                                              |
| 5. MultiQC              | MultiQC           | Aggregate QC report (shared helper)                                                                           |
| 6. Stage 2              | DESeq2            | Differential expression on miRNA count matrix                                                                 |

Key design decisions:
- Bowtie v1 (not v2) — optimized for ultra-short reads
- `samtools idxstats` for quantification — miRBase references are individual miRNAs, no GTF needed
- miRBase species resolved from genome key via `_MIRBASE_SPECIES_MAP` (hg38 -> hsa, mm39 -> mmu, etc.)

### Track C: ChIP-seq Pipeline (`_route_chip_seq`)

| Step                  | Tool        | Detail                                                                                              |
| --------------------- | ----------- | --------------------------------------------------------------------------------------------------- |
| 1. FastQC             | FastQC      | Quality control (shared helper)                                                                     |
| 2. Trimmomatic        | Trimmomatic | Standard adapter trimming (shared helper)                                                           |
| 3. BWA MEM Alignment  | BWA         | Read alignment to reference genome. Paired-end: `bwa mem ref.fa R1.fq R2.fq`                        |
| 4. MACS2 Peak Calling | MACS2       | Identifies transcription factor binding sites. Uses effective genome size from `_MACS2_GENOME_SIZE` |
| 5. MultiQC            | MultiQC     | Aggregate QC report (shared helper)                                                                 |

Key design decisions:
- No featureCounts or Stage 2 DESeq2 — peaks are the primary output
- `_split_chip_samples()` separates treatment (IP) vs control (Input) using metadata condition column
- MACS2 genome size mapped per species (hg38 -> "hs", dm6 -> "dm", etc.)
- Control samples are optional — MACS2 uses local background model if no control provided

### Track C: DNA Methylation / Bisulfite-seq Pipeline (`_route_methylation`)

| Step                      | Tool                          | Detail                                                                                                            |
| ------------------------- | ----------------------------- | ----------------------------------------------------------------------------------------------------------------- |
| 0. Bismark Genome Prep    | bismark_genome_preparation    | Builds bisulfite-converted genome index (if not already present)                                                  |
| 1. FastQC                 | FastQC                        | Quality control (shared helper)                                                                                   |
| 2. Trimmomatic            | Trimmomatic                   | Standard adapter trimming (shared helper)                                                                         |
| 3. Bismark Alignment      | Bismark                       | Aligns bisulfite-converted DNA with `--bowtie2`. Handles C-to-T converted reads                                   |
| 4. Methylation Extraction | bismark_methylation_extractor | Decodes C-to-T mutations into methylation beta-values. Flags: `--comprehensive --merge_non_CpG --cytosine_report` |
| 5. MultiQC                | MultiQC                       | Aggregate QC report (shared helper)                                                                               |

Key design decisions:
- No featureCounts or Stage 2 DESeq2 — methylation reports (beta-values) are the primary output
- Bismark genome preparation auto-detected and skipped if `Bisulfite_Genome/` directory exists
- Both single-end and paired-end supported

### Code Deduplication: Shared Step Helpers

Extracted common pipeline steps into reusable helpers used by all 4 tracks:

| Helper                                                    | Purpose                                                            |
| --------------------------------------------------------- | ------------------------------------------------------------------ |
| `_run_fastqc_step(job, fastq_paths, qc_dir)`              | FastQC with step progress tracking                                 |
| `_run_trim_step(job, assets, dir, library_type, min_len)` | Trimmomatic with configurable MINLEN (18 for miRNA, 36 for others) |
| `_sort_and_index_bam(sam, bam_out)`                       | samtools sort + index                                              |
| `_run_multiqc_step(job, work_dir, qc_dir)`                | MultiQC with step progress tracking                                |

Track A (`_route_fastq`) was refactored to use these same shared helpers.

### Genome Resolvers

| Resolver                              | Purpose                                             |
| ------------------------------------- | --------------------------------------------------- |
| `_resolve_mirbase(genome_key)`        | Finds/builds Bowtie index for miRBase species FASTA |
| `_resolve_bwa_index(genome_fasta)`    | Finds/builds BWA index from genome FASTA            |
| `_resolve_bismark_genome(genome_dir)` | Finds/runs bismark_genome_preparation               |

### Views Changes

| Change                       | Detail                                                                                                                                         |
| ---------------------------- | ---------------------------------------------------------------------------------------------------------------------------------------------- |
| **`VALID_ASSAY_TYPES`**      | New validation set: `{standard_rna, small_rna, chip_seq, methylation}`                                                                         |
| **Assay type validation**    | `CorePipelineView.post()` validates `assay_type` for FASTQ entry; defaults to `standard_rna`                                                   |
| **Pipeline steps per assay** | Different step lists generated per assay type (e.g., chip_seq gets `bwa_align + macs2_peaks`, no `deseq2`)                                     |
| **ProcessingView steps**     | Added 7 new step definitions: `bowtie_mirna`, `mirna_quantify`, `bwa_align`, `macs2_peaks`, `bismark_prep`, `bismark_align`, `bismark_extract` |

### Environment Changes

Added 4 new bioinformatics CLI tools to `environment.yml`: `bowtie`, `bwa`, `macs2`, `bismark`.

### Pipeline Router (updated)

```
run_core_pipeline(session_id, submission_id)
  |
  +-- input_type == "fastq"
  |     +-- assay_type == "standard_rna" --> _route_fastq()      [Track A]
  |     +-- assay_type == "small_rna"    --> _route_small_rna()   [Track B]
  |     +-- assay_type == "chip_seq"     --> _route_chip_seq()    [Track C]
  |     +-- assay_type == "methylation"  --> _route_methylation() [Track C]
  |
  +-- input_type == "alignment"          --> _route_alignment()
  +-- input_type == "matrix"             --> _route_matrix()
```

### Test Suite

| Test Class                      | Tests  | Coverage                                                               |
| ------------------------------- | ------ | ---------------------------------------------------------------------- |
| `AssayTypeModelTest`            | 5      | AssayType choices, default value, persistence                          |
| `TrackFileRoleTest`             | 2      | PEAK_FILE and METHYLATION_REPORT FileAsset roles                       |
| `CorePipelineViewAssayTypeTest` | 7      | assay_type validation, steps per track, persistence, non-fastq default |
| `ProcessingViewStepsTest`       | 4      | All track step definitions present                                     |
| `TaskRouterAssayDispatchTest`   | 4      | Router dispatches to correct route function per assay_type             |
| `SmallRNARouteTest`             | 3      | Bowtie miRNA flags, MINLEN:18, correct tool calls                      |
| `ChIPSeqRouteTest`              | 2      | BWA + MACS2 calls, PEAK_FILE asset registration                        |
| `SplitChipSamplesTest`          | 3      | Treatment/control separation, no-control allowed, all-control raises   |
| `MethylationRouteTest`          | 2      | Bismark calls, METHYLATION_REPORT asset registration                   |
| `SharedHelperTest`              | 5      | FastQC, Trim (single+paired), MultiQC, sort+index helpers              |
| `GenomeResolverTest`            | 6      | Species maps, miRBase/BWA/Bismark resolve + build-if-missing           |
| **Total**                       | **44** | All pass (+ 32 existing = 76 total)                                    |

---

## 5. Phase 5: Pipeline Audit, Code Splitting & Docker Documentation

### 5a. Bioinformatics Pipeline Audit — 5 Critical Bugs Fixed

A professional-level audit of all bioinformatics tool invocations identified 5 bugs, ranked by severity:

| #   | Severity     | Bug                                                                    | Impact                                                                                                                         | Fix                                                                                                                                       |
| --- | ------------ | ---------------------------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------ | ----------------------------------------------------------------------------------------------------------------------------------------- |
| 1   | **CRITICAL** | Piped commands (`hisat2 \| samtools sort`) only checked last exit code | HISAT2 could fail silently; samtools would produce an empty/corrupt BAM, propagating bad data to featureCounts                 | `_run()` helper now prepends `set -o pipefail;` and uses `executable="/bin/bash"` for all piped commands                                  |
| 2   | **CRITICAL** | No normalized data detection for Count Matrix entry                    | If user uploads TPM/FPKM matrix → DESeq2 silently produces invalid differential expression results (false positives/negatives) | `_route_matrix()` checks if >30% of values are non-integer → raises `ValueError` with clear message explaining DESeq2 requires raw counts |
| 3   | **CRITICAL** | Shell injection via f-string paths with `shell=True`                   | Filenames containing shell metacharacters (`$`, `;`, backticks) could execute arbitrary commands                               | New `_q()` helper wraps all path variables with `shlex.quote()` before interpolation into shell commands                                  |
| 4   | **HIGH**     | Unpaired FASTQ files silently dropped in paired-end mode               | If an R1 file had no matching R2 (or vice versa), it was quietly ignored — user loses data without warning                     | `_pair_fastqs()` now emits `warnings.warn()` for each unmatched file and raises `RuntimeError` if zero valid pairs found                  |
| 5   | **HIGH**     | ChIP-seq stem matching fragile (`str.replace("_R1", "")`)              | Failed on filenames like `sample_R1_001.fastq.gz` or `R1_control.fastq.gz` where `_R1` appears in unexpected positions         | `_split_chip_samples()` now uses `re.sub(r"_R[12](?=[\._])", "", stem)` for precise paired-end suffix stripping                           |

All fixes are implemented in the new `pipeline/tasks/` package (see below).

### 5b. Code Splitting — 3 Monolithic Files → 3 Packages

Three files exceeded 600+ lines and were split into focused submodules:

#### `pipeline/tasks/` Package (was `tasks.py` — 1,481 lines → 11 files)

| File                 | Lines | Purpose                                                                                                                                                 |
| -------------------- | ----- | ------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `__init__.py`        | ~90   | Re-exports all public symbols for backward compatibility                                                                                                |
| `_constants.py`      | ~50   | `_CPU_COUNT`, `_TOOL_THREADS`, `_PARALLEL_SAMPLES`, `_GENOME_BASE`, genome maps                                                                         |
| `_helpers.py`        | ~200  | `_run()` (with pipefail fix), `_q()` (shlex.quote), `_pair_fastqs()`, `_update_step()`, `_emit_progress()`, shared FastQC/Trim/MultiQC/sort-index steps |
| `_genome.py`         | ~100  | `_resolve_genome()`, `_resolve_mirbase()`, `_resolve_bwa_index()`, `_resolve_bismark_genome()`                                                          |
| `_featurecounts.py`  | ~80   | `_run_featurecounts()`, `_featurecounts_to_csv()`, `_detect_gff_gene_attr()`                                                                            |
| `_routes.py`         | ~60   | `_route_alignment()`, `_route_matrix()` (with normalized data detection)                                                                                |
| `_track_standard.py` | ~120  | Track A: Standard RNA-seq (`_route_fastq()`)                                                                                                            |
| `_track_mirna.py`    | ~120  | Track B: Small RNA/miRNA (`_route_small_rna()`)                                                                                                         |
| `_track_chipseq.py`  | ~140  | Track C: ChIP-seq (`_route_chip_seq()`, `_split_chip_samples()` with regex fix)                                                                         |
| `_track_methyl.py`   | ~120  | Track C: DNA Methylation (`_route_methylation()`)                                                                                                       |
| `core.py`            | ~100  | Celery tasks: `run_core_pipeline()`, `purge_expired_sessions()`                                                                                         |

#### `pipeline/views/` Package (was `views.py` — 655 lines → 3 files)

| File          | Lines | Purpose                                                                                           |
| ------------- | ----- | ------------------------------------------------------------------------------------------------- |
| `__init__.py` | ~20   | Re-exports all 13 view classes                                                                    |
| `pages.py`    | ~300  | 7 TemplateViews (Home, Tutorials, Workspaces, NewSubmission, Processing, CoreHub, Advanced)       |
| `api.py`      | ~350  | 6 API views (CreateSubmission, ChunkUpload, CorePipeline, JobStatus, SessionAssets, FileDownload) |

#### `pipeline/stats/` Package (was `stats.py` — 764 lines → 5 files)

| File           | Lines | Purpose                                                                                               |
| -------------- | ----- | ----------------------------------------------------------------------------------------------------- |
| `__init__.py`  | ~30   | Re-exports `run_stage2_stats` + all internal functions                                                |
| `_r_bridge.py` | ~30   | rpy2 initialization, R warning suppression, `_R_CORES`, `_converter`                                  |
| `_helpers.py`  | ~150  | `_load_metadata()`, `_align_samples()`, `_filter_low_counts()`, `_combat_seq()`, `_detect_outliers()` |
| `_deseq2.py`   | ~200  | `_build_formula_string()`, `_sanitize_factor_levels()`, `_run_deseq2()`, contrast extraction          |
| `_plots.py`    | ~200  | PCA, UMAP, volcano, MA plot data generation                                                           |
| `core.py`      | ~80   | `run_stage2_stats()` orchestrator                                                                     |

**Backward compatibility preserved:** All `__init__.py` files re-export every public symbol, so existing imports like `from pipeline.tasks import run_core_pipeline` and `from pipeline.stats import run_stage2_stats` continue to work without changes to `urls.py`, `consumers.py`, or any other module.

### 5c. Test Suite — All 76 Tests Passing

After the code split, mock targets in test files needed updating to point to the new submodule locations where functions are consumed (not re-exported). All fixes were verified:

| Test File              | Tests  | Status     |
| ---------------------- | ------ | ---------- |
| `test_entry_points.py` | 32     | ✅ All pass |
| `test_assay_tracks.py` | 44     | ✅ All pass |
| **Total**              | **76** | ✅ All pass |

### 5d. Docker Documentation

| Document                                                             | Purpose                                                                                                                                                                              |
| -------------------------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------ |
| [Docker Deployment Guide.md](doc/Docker%20Deployment%20Guide.md)     | Production deployment using Docker Compose — environment config, reference genome bind mounts, scaling workers, SSL/reverse proxy, persistent storage, monitoring, updates           |
| [Docker Development Guide.md](doc/Docker%20Development%20Guide.md)   | Local development with Docker — quick start, live reloading via bind mounts, running tests, working without Docker, transitioning to production                                      |
| [Reference Genome Strategy.md](doc/Reference%20Genome%20Strategy.md) | Why genomes can't go in Git or Docker images, host-side storage + bind mount strategy, building indexes from scratch, transfer via rsync, alternatives considered (Git LFS, S3, DVC) |

---

## 5b. Bug Fixes and Enhancements

### BAM File Download Fix

| Bug                                                                               | Impact                                                                   | Fix                                                                                                             |
| --------------------------------------------------------------------------------- | ------------------------------------------------------------------------ | --------------------------------------------------------------------------------------------------------------- |
| `_route_fastq()` never registered aligned BAM files as `ALIGNMENT_BAM` FileAssets | "Compressed Alignments" download button returned nothing in the Core Hub | BAM files are now registered as `FileAsset.FileRole.ALIGNMENT_BAM` immediately after HISAT2 alignment completes |
| `_route_alignment()` never registered converted BAM files as FileAssets           | CRAM-to-BAM converted files were not downloadable                        | Converted BAMs (from CRAM input) are now registered as `ALIGNMENT_BAM` assets after conversion                  |
| Core Hub label said "Compressed Alignments (.cram)"                               | Misleading — pipeline produces sorted BAMs, not CRAMs                    | Label corrected to "Aligned Reads (.bam)" with accurate description                                             |

### Heatmap Plot Added (5th Core Visualization)

| Component           | Change                                                                                                                                                                                                                                                      |
| ------------------- | ----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| **`_plots.py`**     | New `_compute_heatmap_data()` function: selects top 50 significant DEGs (sorted by padj), extracts normalized expression from the count matrix, applies row-wise Z-score normalization. Returns gene names, sample names, group labels, and z-score matrix. |
| **`core_hub.html`** | Added dedicated full-width heatmap card below the 2x2 plot grid. MA Plot separated from heatmap into its own card.                                                                                                                                          |
| **`core_hub.js`**   | New `renderHeatmap()` function: Plotly.js heatmap trace with blue-white-red colorscale (`zmid=0`), reversed y-axis (most significant gene on top), group color annotations above the heatmap, hover showing gene/sample/group/z-score detail.               |

### Plot Descriptions Added

All 5 core visualization cards now include an explanatory paragraph beneath the card header that explains:
- What the plot shows in plain language
- How to read the axes
- What patterns to look for
- What scientific conclusions can be drawn

### JavaScript Bug Fix

| Bug                                                                     | Impact                                                                           | Fix                                        |
| ----------------------------------------------------------------------- | -------------------------------------------------------------------------------- | ------------------------------------------ |
| `core_hub.js` queried `.querySelector("h4")` for module card title text | Module card click threw `null reference` error — template uses `<h3>` not `<h4>` | Changed selector to `.querySelector("h3")` |

### Unrecorded Features Now Documented

The following features were already implemented but not recorded in progress.md:
- `ModuleRunView` API endpoint (`POST /api/modules/<name>/run`) with 12 approved modules, Stage 2 completion validation, and Celery dispatch
- `run_tier2_module()` Celery task (placeholder — dispatches and records SUCCESS with module name, no actual analysis)
- `purge_expired_sessions()` Celery Beat task (shutil.rmtree on expired session dirs, cascade DB delete)
- Celery Beat schedule configured in `config/celery.py` (nightly at 2:00 AM UTC)
- `_annotations.py` — MyGene.info REST API integration for gene description annotation (batch POST, 500 genes/request)
- WebSocket consumer for live progress (`pipeline/consumers.py` + `routing.py`)
- `pipeline/context_processors.py` — session_id injector for all templates
- `_r_bridge.py` — shared rpy2 bridge with converter, BiocParallel setup
- Docker configs: `Dockerfile`, `docker-compose.yml`, `docker-compose.dev.yml`

---

## 6. What Remains (Next Phases)

- [ ] Implement individual module runners (~~WGCNA~~, GSEA, Survival, MOFA, DIABLO, etc.)
- [x] **WGCNA & Pathway Enrichment module implemented (first Tier 2 module)**
- [ ] Implement deconvolution pipeline (BisqueRNA/MuSiC/Scaden → h5ad generation)
- [ ] Implement advanced spoke pipelines (Trajectory via scVI/PAGA, Spatial via Tangram/Squidpy, Autocorrelation via Moran's I)
- [x] ~~Wire up Plotly.js for interactive visualization rendering (PCA, UMAP, Volcano, MA, Heatmap)~~
- [x] ~~Add real download file serving from pipeline output directories~~
- [x] ~~Add WebSocket support (Django Channels) for real-time progress updates~~
- [x] ~~Production static file serving (WhiteNoise + collectstatic)~~
- [x] ~~Production deployment guide (Daphne + Nginx + systemd)~~
- [x] ~~Docker deployment guide (Docker Compose for production)~~
- [x] ~~Docker development guide (local dev with Docker)~~
- [x] ~~Reference genome strategy (storage, distribution, Docker bind mount)~~
- [x] ~~Bioinformatics pipeline audit (5 critical bugs found and fixed)~~
- [x] ~~Code splitting (tasks.py -> 11 files, views.py -> 3 files, stats.py -> 5 files)~~
- [x] ~~Docker containerization~~
- [x] ~~BAM file download fix (FileAsset registration after alignment)~~
- [x] ~~Heatmap plot (5th core visualization — top 50 DEGs z-score heatmap)~~
- [x] ~~Plot descriptions (explanatory text for all 5 core plots)~~
- [x] ~~Module card JS selector fix (h4 -> h3)~~
- [x] ~~ModuleRunView API endpoint (POST /api/modules/<name>/run)~~
- [x] ~~Celery Beat schedule (purge_expired_sessions nightly at 2 AM)~~
- [ ] Session cleanup: Celery Beat daemon deployment (schedule defined but Beat not started in dev)
- [x] ~~ChIP-seq Stage 2 stats integration (consensus peaks → featureCounts → DESeq2)~~
- [x] ~~Methylation Stage 2 stats integration (methylKit differential methylation via rpy2)~~
- [ ] End-to-end integration test with real FASTQ/BAM/matrix data
- [ ] 12 Tier 2 module implementations (dispatcher exists as placeholder):
  - [ ] Alternative Splicing (IsoformSwitchAnalyzeR)
  - [ ] RNA Editing & SNP Detection (REDItools2)
  - [ ] Time Series analysis (ImpulseDE2)
  - [x] ~~WGCNA co-expression (PyWGCNA) + Pathway Enrichment (gseapy)~~
  - [ ] Pathway Enrichment / GSEA (gseapy) — standalone module
  - [ ] Causal Network Inference (arboreto / GRNBoost2 + STRING-DB)
  - [ ] Literature Mining (INDRA API)
  - [ ] Survival Prediction (lifelines)
  - [ ] TCGA Disease Integration (TCGAbiolinks)
  - [ ] Biomarker Discovery (MarkerDB API)
  - [ ] Multi-Omics Factor Analysis (mofapy2)
  - [ ] Supervised Multi-Omics / DIABLO (mixOmics)
- [ ] Deconvolution engine (DestVI / BayesPrism -> H5AD output)
- [ ] Advanced spokes: Trajectory Inference (scanpy PAGA/pseudotime)
- [ ] Advanced spokes: Spatial Mapping (Tangram deep learning)
- [ ] Advanced spokes: Spatial Autocorrelation (Squidpy / Moran's I)
- [x] ~~All 10 reference genomes downloaded and indexed (hg38, mm39, mm10, rn7, danRer11, galGal6, susScr11, dm6, wbcel235, araTha)~~

---

## 6a. Phase 6: WGCNA & Pathway Enrichment Module (First Tier 2 Module)

### Overview

Implemented the first real Tier 2 analytical module: WGCNA (Weighted Gene Co-expression Network Analysis) combined with pathway enrichment via Enrichr. This replaces the `run_tier2_module` placeholder for the `WGCNA` branch with a full engine, Plotly plot serializers, and dispatcher integration.

### Architecture

```
ModuleRunView (POST /api/modules/wgcna/run)
  |
  +-- run_tier2_module.apply_async(session_id, core_job_id, "WGCNA")
        |
        +-- _dispatch_wgcna()           [core.py — resolves FileAssets]
              |
              +-- execute_wgcna_and_pathways()   [_module_wgcna.py — engine]
                    |
                    +-- _load_and_validate()     Load & intersect matrix + metadata
                    +-- _run_pywgcna()           Adjacency -> TOM -> modules
                    +-- _encode_traits()         One-hot encode categoricals
                    +-- _correlate_modules_traits()  Pearson r per ME x trait
                    +-- _find_top_module()        Best non-grey module by p-value
                    +-- _extract_hub_genes()     kME ranking (vectorised np.corrcoef)
                    +-- _run_enrichr()           gseapy.enrichr Fisher's exact test
                    +-- build_module_trait_heatmap()  [_plots_wgcna.py]
                    +-- build_pathway_dotplot()       [_plots_wgcna.py]
```

### New Files

| File                              | Lines | Purpose                                                                                                                                    |
| --------------------------------- | ----- | ------------------------------------------------------------------------------------------------------------------------------------------ |
| `pipeline/tasks/_module_wgcna.py` | ~340  | WGCNA engine: PyWGCNA network construction, module-trait correlation, hub gene extraction (kME), gseapy Enrichr pathway enrichment         |
| `pipeline/stats/_plots_wgcna.py`  | ~190  | Plotly JSON serializers: module-trait heatmap (blue-white-red diverging colorscale) and pathway dot plot (bubble chart with Viridis scale) |
| `test/test_wgcna.py`              | ~330  | 24 Django TestCase tests covering plots, helpers, dispatcher, and integration                                                              |

### Modified Files

| File                         | Change                                                                                                                                 |
| ---------------------------- | -------------------------------------------------------------------------------------------------------------------------------------- |
| `pipeline/tasks/core.py`     | Added `_dispatch_wgcna()` function; replaced placeholder in `run_tier2_module` with WGCNA dispatch branch for `module_name == "WGCNA"` |
| `pipeline/tasks/__init__.py` | Re-exports `execute_wgcna_and_pathways`                                                                                                |
| `pipeline/stats/__init__.py` | Re-exports `build_module_trait_heatmap`, `build_pathway_dotplot`                                                                       |

### Pipeline Steps (6-step progress tracking)

| Step                 | What it does                                                                                             |
| -------------------- | -------------------------------------------------------------------------------------------------------- |
| `wgcna_load_data`    | Load normalized counts CSV + metadata CSV, validate sample overlap                                       |
| `wgcna_find_modules` | PyWGCNA: preprocess -> soft threshold -> adjacency -> TOM -> hierarchical clustering -> module detection |
| `wgcna_module_trait` | One-hot encode categorical traits, Pearson correlate every module eigengene with every trait             |
| `wgcna_hub_genes`    | Find top module (lowest p-value, grey excluded), extract top N hub genes by kME                          |
| `wgcna_enrichment`   | gseapy.enrichr on hub genes against KEGG 2021 + GO Biological Process 2023                               |
| `wgcna_plots`        | Build Plotly JSON for module-trait heatmap and pathway dot plot                                          |

### Plotly Visualizations (2 new plots)

| Plot                 | Type         | Description                                                                                                                                    |
| -------------------- | ------------ | ---------------------------------------------------------------------------------------------------------------------------------------------- |
| Module-Trait Heatmap | Heatmap      | Rows = module eigengenes, Cols = traits/conditions, Color = Pearson r (blue-white-red), Text = r value with significance stars (*/\*\*/\*\*\*) |
| Pathway Dot Plot     | Bubble chart | Y = pathway terms, X = Combined Score, Size = overlap gene count, Color = -log10(adj p) on Viridis scale                                       |

### Key Design Decisions

1. **Manual module-trait correlation** instead of `wgcna_obj.analyseWGCNA()` -- gives full control over trait encoding (one-hot for categoricals) and avoids CSV format mismatches in PyWGCNA's `updateSampleInfo`.
2. **Vectorised kME** via `np.corrcoef` -- entire module gene set correlated with eigengene in one matrix operation.
3. **Grey module exclusion** -- `_find_top_module` drops any index containing "grey" (unassigned genes with no coherent biological signal).
4. **Existing signature preserved** -- `run_tier2_module` keeps its `(self, session_id, core_job_id, module_name, params=None)` signature; WGCNA branch added alongside placeholder for other modules.
5. **step_progress managed by engine** -- `execute_wgcna_and_pathways` initializes and updates `step_progress` with 6 steps; `run_tier2_module` refreshes from DB after engine returns.

### Test Suite

| Test Class               | Tests  | Coverage                                                                                         |
| ------------------------ | ------ | ------------------------------------------------------------------------------------------------ |
| `ModuleTraitHeatmapTest` | 3      | Heatmap structure, ME prefix stripping, z-value accuracy                                         |
| `PathwayDotplotTest`     | 5      | Empty/None/no-sig placeholders, significant terms scatter, max_terms cap                         |
| `SignificanceLabelsTest` | 1      | Star threshold mapping (\*\*\*/\*\*/\*/ns)                                                       |
| `LoadAndValidateTest`    | 2      | Shared sample filtering, no-overlap ValueError                                                   |
| `EncodeTraitsTest`       | 3      | Numeric passthrough, categorical one-hot, mixed columns                                          |
| `FindTopModuleTest`      | 3      | Lowest p-value selection, grey exclusion, all-grey ValueError                                    |
| `ExtractHubGenesTest`    | 2      | Correct hub count, empty module ValueError                                                       |
| `DispatchWgcnaTest`      | 2      | FileAsset path resolution, missing asset raises                                                  |
| `Tier2WgcnaRoutingTest`  | 3      | Engine dispatch via Celery .apply(), failure sets FAILED status, non-WGCNA placeholder preserved |
| **Total**                | **24** | All pass (+ 32 entry_points = 56 total WGCNA-related)                                            |

---

## 7. Copilot Customization Tools (.github/)

The `.github/` directory contains Copilot automation tools for consistent module development:

### Agents
- **tier2-module** (`.github/agents/tier2-module.agent.md`): Specialized agent invoked via `@tier2-module` in Copilot Chat. Scaffolds Tier 2 module files, wires dispatch, re-exports, and writes tests.

### Prompt Files
- **new-tier2-module.prompt.md**: Template for implementing a new Tier 2 module end-to-end. Accepts `{{module_name}}`, `{{purpose}}`, `{{engine}}` variables.
- **new-pipeline-track.prompt.md**: Template for adding a new pipeline assay track. Accepts `{{track_name}}`, `{{assay_key}}`, `{{aligner}}`, `{{quantifier}}`.
- **write-tests.prompt.md**: Template for writing tests following RNAseek conventions.

### Skills
- **celery-tasks** (`.github/skills/celery-tasks/SKILL.md`): Reference for progress tracking, step_progress JSON shape, AnalysisJob lifecycle.
- **file-assets** (`.github/skills/file-assets/SKILL.md`): Reference for FileAsset creation, file roles, upload vs pipeline-generated assets.
- **r-bridge** (`.github/skills/r-bridge/SKILL.md`): Reference for rpy2 usage, converter, BiocParallel.

### CI/CD
- **e2e.yml** (`.github/workflows/e2e.yml`): GitHub Actions workflow — builds yeast index, runs E2E + full test suite on push/PR to main/develop.

---

## 8. Image Placeholders Needed

See section at end of this document for all images required.
