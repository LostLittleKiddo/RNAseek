# RNAseek — Project Instructions for GitHub Copilot

**Stack:** Django 5.2 · Celery 5.6 · Redis · Django Channels (WebSocket) · Python 3.11 · R 4.3  
**Conda env:** `/home/littlekiddo/.conda/envs/rnaseek`  
**DB:** SQLite in dev (`db.sqlite3`) · PostgreSQL in production  
**Dev commands:**
- Server: `python manage.py runserver`
- Worker: `celery -A config worker -l info`
- Tests: `python manage.py test test --verbosity=2`
- Migrate: `python manage.py migrate`

---

## Project Layout

```
config/          Django project package (settings, urls, celery, asgi, wsgi)
pipeline/        Main Django app
  models.py      All 4 ORM models
  middleware.py  AnonymousSessionMiddleware
  consumers.py   WebSocket consumer (Django Channels)
  views/
    api.py       REST API views
    pages.py     TemplateViews
  tasks/         Celery task package (see below)
  stats/         Stage 2 statistics package (see below)
  templates/     7 HTML pages + base.html
  static/        CSS design system + JS
test/            All tests (NOT pipeline/tests.py)
  test_entry_points.py   32 Django TestCase tests
  test_stage2.py         26 standalone stats tests
  test_e2e.py            Full E2E yeast pipeline test
  test_hisat2_pipeline.py
  test_dev_dataset.py
  test_assay_tracks.py
```

---

## Data Models (`pipeline/models.py`)

**Session**  
`session_id` (UUID PK) · `created_at` · `expires_at` (14-day default) · `is_expired` property

**AnalysisSubmission**  
`submission_id` (UUID PK) · `session` (FK → Session, cascade) · `input_data_type` (fastq/alignment/matrix) · `assay_type` (standard_rna/small_rna/chip_seq/methylation) · `library_type` (single/paired) · `strandedness` (unstranded/fr-firststrand/fr-secondstrand) · `reference_genome` · `custom_genome_name` · `metadata_mode` (upload/manual) · `adjusted_pvalue` (0.05) · `min_log2fc` (-1.0) · `max_log2fc` (1.0) · `metadata_payload` (JSONField) · `upload_dir` property → `media/sessions/{session_id}/submissions/{submission_id}/`

**FileAsset**  
`id` (UUID PK) · `session` (FK) · `submission` (FK, nullable) · `file_role` (see roles below) · `local_path` (500) · `is_user_uploaded`

File roles: `RAW_FASTQ` · `ALIGNMENT_BAM` · `USER_COUNT_MATRIX` · `COUNT_MATRIX` · `NORMALIZED_COUNTS` · `DEG_TABLE` · `MULTIQC_REPORT` · `H5AD_PSEUDO` · `HE_IMAGE_USER` · `HE_IMAGE_GENERIC` · `CUSTOM_GENOME_FASTA` · `CUSTOM_GENOME_ANNOTATION` · `METADATA_CSV` · `PEAK_FILE` · `METHYLATION_REPORT`

**AnalysisJob**  
`job_id` (UUID PK = Celery task ID) · `session` (FK) · `module_name` · `status` (PENDING/RUNNING/SUCCESS/FAILED) · `result_payload` (JSONField) · `step_progress` (JSONField) · `created_at` · `updated_at`

`step_progress` shape: `{ "pipeline_steps": [...], "current_step": "...", "completed_steps": [...], "failed_step": "..." }`

---

## Session / Auth Pattern

- `AnonymousSessionMiddleware` reads the `Session_ID` HttpOnly cookie on every request.
- All views read the current tenant via `request.session_obj` (a `Session` instance).
- Never use Django's built-in `request.session`. Always `request.session_obj`.
- All DB writes scope to `session=request.session_obj` to enforce multi-tenant isolation.

---

## Tasks Package (`pipeline/tasks/`)

| File | Contents |
|---|---|
| `core.py` | `run_core_pipeline(session_id, submission_id)` · `run_tier2_module(job_id, module_name, ...)` |
| `_constants.py` | `_CPU_COUNT` · `_TOOL_THREADS` · `_PARALLEL_SAMPLES` · `_GENOME_FOLDER_MAP` · `_MIRBASE_SPECIES_MAP` · `_MACS2_GENOME_SIZE` |
| `_helpers.py` | `_run(cmd, cwd)` · `_q(path)` · `_pair_fastqs()` · `_emit_progress(job)` · `_update_step(job, step_name)` · `_run_fastqc_step()` · `_run_trim_step()` · `_run_multiqc_step()` · `_strandedness_hisat2()` |
| `_genome.py` | `_resolve_genome(genome_key, work_dir, submission)` |
| `_featurecounts.py` | `_run_featurecounts(...)` · `_detect_gff_gene_attr(gff_path)` |
| `_routes.py` | `_route_alignment(submission, job)` · `_route_matrix(submission, job)` · `_register_stage2_assets(submission, stats_result, qc_dir)` |
| `_track_standard.py` | `_route_fastq(submission, job)` — FastQC → Trim → HISAT2 → featureCounts → MultiQC → Stage 2 |
| `_track_mirna.py` | `_route_small_rna(submission, job)` — Bowtie → miRBase |
| `_track_chipseq.py` | `_route_chip_seq(submission, job)` — BWA → MACS2 |
| `_track_methyl.py` | `_route_methylation(submission, job)` — Bismark |

**Core routing logic in `run_core_pipeline`:**
- `input_data_type == "fastq"` → branch on `assay_type` to one of 4 track functions
- `input_data_type == "alignment"` → `_route_alignment()`
- `input_data_type == "matrix"` → `_route_matrix()`

---

## Stats Package (`pipeline/stats/`)

| File | Contents |
|---|---|
| `core.py` | `run_stage2_stats(submission)` — orchestrator, reads `submission.metadata_payload` |
| `_helpers.py` | `_align_samples()` · `_combat_seq()` · `_detect_outliers()` · `_filter_low_counts()` · `_load_metadata()` |
| `_deseq2.py` | `_run_deseq2()` — builds dynamic design formula, BiocParallel |
| `_plots.py` | `_generate_plot_data()` — PCA, UMAP, Volcano, MA → Plotly JSON |
| `_annotations.py` | `annotate_deg_table()` — MyGene.info REST API |
| `_r_bridge.py` | rpy2 utilities |

Stage 2 input: `counts/raw_counts.csv`. Output dir: `stats/`.  
`metadata_payload` keys used by Stage 2: `samples` · `column_mapping` · `contrasts` · `quant_level` · `batch_effect`

---

## API Endpoints & URL Names

| Method | URL | `name=` | View class |
|---|---|---|---|
| POST | `/api/submission/create` | `create_submission` | `CreateSubmissionView` |
| POST | `/api/upload/chunk` | `upload_chunk` | `ChunkUploadView` |
| POST | `/api/pipeline/core` | `pipeline_core` | `CorePipelineView` |
| GET | `/api/jobs/<uuid:job_id>/` | `job_status` | `JobStatusView` |
| GET | `/api/session/assets` | `session_assets` | `SessionAssetsView` |
| GET | `/api/download/<uuid:asset_id>` | `file_download` | `FileDownloadView` |
| POST | `/api/modules/<str:module_name>/run` | `module_run` | `ModuleRunView` |

Page URLs: `home` · `tutorials` · `workspaces` · `new_submission` · `processing` (uuid) · `core_hub` (uuid) · `advanced` (uuid)

---

## Critical Conventions — Never Break

1. **Shell safety:** All external commands go through `_run(cmd, cwd)`. All user-derived paths are quoted via `_q(path)` before interpolation into command strings.
2. **Progress tracking:** Call `_update_step(job, "step_name")` before each step. Call `_emit_progress(job)` after any `job.save()` that changes status or step_progress to push WebSocket updates.
3. **FileAsset registration:** Use `FileAsset.objects.create(session_id=submission.session_id, submission=submission, file_role=FileAsset.FileRole.X, local_path=abs_path, is_user_uploaded=False)` for all pipeline-generated files.
4. **Tenant isolation:** Every DB query on user data must filter by `session=request.session_obj` or `session_id=submission.session_id`. No bare `.all()` on tenant-scoped models.
5. **Subdirectory routing:** Uploads land in typed subdirs under `submission.upload_dir`: `raw/` (FASTQ) · `aligned/` (BAM) · `counts/` (matrix) · `custom_genome/` (FASTA/GTF) · `metadata/` (CSV).
6. **Environment toggling:** `CELERY_EAGER=1` (default dev) runs tasks synchronously. `CELERY_EAGER=0` requires a live worker. Tests that trigger real file I/O must mock task dispatch when `CELERY_EAGER=1`.

---

## Frontend Conventions

- Design tokens in `pipeline/static/pipeline/css/variables.css` (CSS custom properties prefixed `--rna-`).
- Component library: `global.css`. Page-specific: `pipeline_setup.css`.
- Main JS: `pipeline_setup.js` (upload, metadata, submission) · `core_hub.js` (modules, deconvolution).
- `setup_wizard.js` is an orphan legacy file — do not reference or build on it.
- All fetch calls use relative paths. No hardcoded URLs. Static assets use `{% static %}` tags only.
- Processing page polls `/api/jobs/<id>/` every 3 seconds via inline JS.

---

## Reference Genomes

Available pre-indexed keys: `hg38` · `mm39` · `mm10` · `rn7` · `danRer11` · `galGal6` · `susScr11` · `dm6` · `wbcel235` · `r64` (yeast — only one currently built) · `araTha`  
Folder name map: `_GENOME_FOLDER_MAP` in `_constants.py`  
Index location: `pipeline/reference_genomes/{FOLDER_NAME}/` with subfolders `index/` and annotation file.

Custom genome: uploads FASTA + GTF to `custom_genome/` subdir. `_route_fastq` runs `hisat2-build` as a tracked step (`hisat2_build`) before FastQC.

---

## Current Implementation State (Phase 5 complete)

Completed: all 4 pipeline tracks · Stage 2 stats (DESeq2 + ComBat + outliers + plots + annotations) · 4 Plotly plots · 7-page frontend · chunked upload · WebSocket progress · anonymous session middleware · dev/prod env split · 32 + 26 + E2E tests passing · yeast R64 reference genome built.

Pending (Phases 6–8):
- 12 Tier 2 module implementations (ModuleRunView dispatch shell exists; individual module logic not yet implemented)
- Deconvolution engine (DestVI / BayesPrism → H5AD)
- Spatial spokes (Trajectory / Tangram / Squidpy)
- Celery Beat janitor (`purge_expired` management command exists; Beat schedule not configured)
- Remaining 10 reference genomes to download and index

---

## CI/CD — GitHub Actions

Workflow file: `.github/workflows/e2e.yml`  
Triggers on push/PR to `main` and `develop`. Steps:

1. Checkout → Conda env from `environment.yml` → pip install `requirements.txt`
2. Verify bioinformatics tools (hisat2, samtools, fastqc, trimmomatic, featureCounts, R)
3. Build yeast HISAT2 index (`.ht2` files are gitignored)
4. Django migrations
5. E2E test: `CELERY_EAGER=1 python test/test_e2e.py`
6. Full test suite: `python manage.py test test/ --verbosity=2`

**Run CI tests locally:**
```bash
conda activate rnaseek
CELERY_EAGER=1 python test/test_e2e.py      # E2E only
python manage.py test test --verbosity=2     # All Django tests
python test/test_stage2.py                   # Standalone stats tests
```

---

## Dependencies

**Conda (R + bioinformatics CLI) — `environment.yml`:**
- Python 3.11, R 4.3
- Bioconductor: DESeq2, sva, DEXSeq, IsoformSwitchAnalyzeR, TCGAbiolinks, mixOmics
- R packages: WGCNA
- CLI tools: fastqc, trimmomatic, hisat2, samtools, subread, stringtie, bowtie, bwa, macs2, bismark

**Pip (Python) — `requirements.txt`:**
- Web: Django 5.2, DRF, Celery[redis], Channels, Daphne, WhiteNoise
- Scientific: numpy, pandas, scipy, scikit-learn, plotly, statsmodels
- R bridge: rpy2
- Single-cell: anndata, scanpy, scvi-tools, tangram-sc, squidpy
- Analysis: gseapy, lifelines, mofapy2, arboreto, pywgcna, multiqc, indra

**Adding new dependencies:**
- R/Bioconductor packages → add to `environment.yml` under `dependencies`
- Python packages → add to `requirements.txt`
- CLI tools (bioinformatics) → add to `environment.yml` via conda-forge/bioconda

---

## R Bridge Conventions (`pipeline/stats/_r_bridge.py`)

All R code goes through the shared rpy2 bridge. Never initialize rpy2 directly.

```python
from pipeline.stats._r_bridge import ro, importr, _converter, localconverter, _R_CORES
```

- `_converter` = combined numpy + pandas converter — always wrap R calls in `with localconverter(_converter):`
- `_R_CORES` = `max(2, os.cpu_count() // 2)` — for BiocParallel
- rpy2 is NOT thread-safe — never call R from multiple Python threads
- New R packages must be added to `environment.yml`, not installed at runtime
