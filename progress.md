# RNAseek -- Project Progress

> Last updated after: **Custom Genome, GTF/GFF Support, Metadata Validation & Interactive Plots**

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
  views.py          ← Page views + API views (conditional validation per entry point)
  urls.py           ← 7 page routes + 5 API routes
  middleware.py     ← AnonymousSessionMiddleware
  tasks.py          ← Core pipeline router + 3 route functions + parallel execution
  stats.py          ← Stage 2 statistical analysis (DESeq2, Combat-seq, PCA outliers)
  context_processors.py ← Session context injector (session_id for all templates)
  admin.py          ← (default)
  apps.py           ← PipelineConfig
```

### 2.1b Environment Configuration (Dev/Prod)

Settings auto-detect the environment via `RNASEEK_ENV` env variable (defaults to `development`).

| Setting             | Development (default)        | Production (`RNASEEK_ENV=production`)      |
| ------------------- | ---------------------------- | ------------------------------------------ |
| `DEBUG`             | `True`                       | `False`                                    |
| `SECRET_KEY`        | Insecure default             | From `DJANGO_SECRET_KEY` env var           |
| `ALLOWED_HOSTS`     | `localhost,127.0.0.1`        | From `DJANGO_ALLOWED_HOSTS` env var        |
| `DATABASES`         | SQLite (`db.sqlite3`)        | PostgreSQL (from `DB_*` env vars)          |
| `CELERY_BROKER_URL` | `redis://127.0.0.1:6379/0`   | From `CELERY_BROKER_URL` env var           |
| `CELERY_EAGER`      | `1` (sync, no worker needed) | N/A (always uses worker)                   |
| `STATIC_ROOT`       | `None`                       | `staticfiles/`                             |
| `MEDIA_ROOT`        | `media/`                     | From `MEDIA_ROOT` env var                  |
| Security headers    | Off                          | HSTS, SSL redirect, secure cookies enabled |

Files: `.env` (local dev, gitignored), `.env.example` (documented template for production).

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
- `file_role` — choices: `RAW_FASTQ`, `COUNT_MATRIX`, `H5AD_PSEUDO`, `HE_IMAGE_USER`, `HE_IMAGE_GENERIC`, `CUSTOM_GENOME_FASTA`, `CUSTOM_GENOME_ANNOTATION`, `METADATA_CSV`, `ALIGNMENT_BAM`, `USER_COUNT_MATRIX`
- `local_path` — CharField (500)
- `is_user_uploaded` — Boolean

**AnalysisJob**
- `job_id` — UUID primary key (doubles as Celery task ID)
- `session` — FK → Session (cascade)
- `module_name` — CharField (50)
- `status` — choices: `PENDING`, `RUNNING`, `SUCCESS`, `FAILED`
- `result_payload` — JSONField

### 2.3 Middleware

**AnonymousSessionMiddleware** — Cookie-based anonymous sessions:
- Reads `Session_ID` HttpOnly cookie on every request
- Validates against DB (exists + not expired)
- Creates new Session if missing/expired
- Attaches `request.session_obj` for all views
- 14-day max_age, SameSite=Lax

### 2.4 API Endpoints

| Method | Endpoint                 | Purpose                                                                                      |
| ------ | ------------------------ | -------------------------------------------------------------------------------------------- |
| POST   | `/api/submission/create` | Create a new AnalysisSubmission, return its UUID for scoping uploads                         |
| POST   | `/api/upload/chunk`      | Receive 5 MB file chunk, route to subdirectory by file_role, create FileAsset on final chunk |
| POST   | `/api/pipeline/core`     | Validate & trigger Core Pipeline by entry point (fastq/alignment/matrix), returns `job_id`   |
| GET    | `/api/jobs/<uuid>/`      | Poll job status (PENDING/RUNNING/SUCCESS/FAILED) + result payload                            |
| GET    | `/api/session/assets`    | List all FileAssets for current session                                                      |

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

### 4 Interactive Plotly.js Plots

| Plot        | Data Source                   | Detail                                                                                                                              |
| ----------- | ----------------------------- | ----------------------------------------------------------------------------------------------------------------------------------- |
| **PCA**     | Normalized counts from DESeq2 | Log2-transformed, sklearn PCA, colored by primary_group. Shows variance explained percentages.                                      |
| **UMAP**    | Normalized counts from DESeq2 | PCA pre-reduction → umap-learn UMAP (n_neighbors auto-capped for small sample sizes). Gracefully skips if umap-learn not installed. |
| **Volcano** | First DEG results CSV         | log2FC vs -log10(padj), three categories (up/down/not-sig), threshold lines drawn.                                                  |
| **MA**      | First DEG results CSV         | log10(baseMean) vs log2FC, significant genes highlighted in red.                                                                    |

| Component           | Change                                                                                                                                                                                                                                                          |
| ------------------- | --------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| **`stats.py`**      | New function `_generate_plot_data()` computes JSON-serializable data for all 4 plots. Called at the end of `run_stage2_stats()`. Helpers: `_build_group_map()`, `_compute_pca_data()`, `_compute_umap_data()`, `_compute_volcano_data()`, `_compute_ma_data()`. |
| **`core_hub.html`** | Added Plotly.js CDN (v2.35.2). Inline `<script>` fetches job payload via `/api/jobs/<id>/` and renders all 4 plots with consistent styling (transparent background, Inter font, hover tooltips).                                                                |
| **Plot data flow**  | `stats.py` → `result_payload["plot_data"]` → `JobStatusView` JSON response → Plotly.js rendering in browser.                                                                                                                                                    |

### Test Results

- **32/32 unit tests pass** (Django test runner)
- All Python files compile without errors

---

## 5. What Remains (Next Phases)

- [ ] Implement individual module runners (WGCNA, GSEA, Survival, MOFA, DIABLO, etc.)
- [ ] Implement deconvolution pipeline (BisqueRNA/MuSiC/Scaden → h5ad generation)
- [ ] Implement advanced spoke pipelines (Trajectory via scVI/PAGA, Spatial via Tangram/Squidpy, Autocorrelation via Moran's I)
- [x] ~~Wire up Plotly.js for interactive visualization rendering (PCA, UMAP, Volcano, MA, Heatmap)~~
- [ ] Add real download file serving from pipeline output directories
- [ ] Add WebSocket support (Django Channels) for real-time progress updates
- [ ] Session cleanup cron job (purge expired sessions + files)
- [ ] Docker containerization
- [ ] Production deployment (Gunicorn + Nginx)
- [ ] End-to-end integration test with real FASTQ/BAM/matrix data

---

## 6. Image Placeholders Needed

See section at end of this document for all images required.
