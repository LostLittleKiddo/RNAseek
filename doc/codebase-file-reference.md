# RNAseek — Codebase File Reference

> **Generated:** Audit pass, March 2026  
> Line counts are approximate and reflect the file state at audit time.

---

## Root

| File                     | Lines | Purpose                                                                                                      |
| ------------------------ | ----- | ------------------------------------------------------------------------------------------------------------ |
| `manage.py`              | —     | Django management entry point                                                                                |
| `requirements.txt`       | —     | Python/pip dependency list (includes commented-out future deps: scvi-tools, mofapy2, tangram-sc, squidpy)    |
| `environment.yml`        | —     | Conda environment spec (mirrors requirements.txt for conda-based deployments)                                |
| `db.sqlite3`             | —     | SQLite database (development only)                                                                           |
| `Dockerfile`             | —     | Multi-stage: Miniconda base → conda env → pip deps → app code → collectstatic; EXPOSE 8000                   |
| `docker-compose.yml`     | —     | 5-service production stack: web (Daphne), worker (Celery, 32 GB RAM), beat, redis (7-alpine), tusd (v2 resumable uploads); shared volumes, NFS mount flags documented |
| `docker-compose.dev.yml` | —     | Dev override: live code mount, reduced concurrency (2), debug logging                                        |
| `deploy.sh`              | —     | Server deploy script: .env validation, pip install, migrate, collectstatic, Nginx symlink, systemd reload    |
| `README.md`              | —     | Project overview and setup instructions                                                                      |
| `LICENSE`                | —     | License file                                                                                                 |

---

## `config/` — Django Project Configuration

| File          | Lines | Purpose                                                                                                                                  |
| ------------- | ----- | ---------------------------------------------------------------------------------------------------------------------------------------- |
| `__init__.py` | 3     | Ensures `config` is a Python package                                                                                                     |
| `settings.py` | 257   | Django 5.2 settings: CHANNEL_LAYERS (Redis), Celery broker, WhiteNoise static storage, MEDIA_ROOT, session/security middleware, ASGI app |
| `celery.py`   | 18    | Celery 5.6 app factory: autodiscover tasks, Beat schedule (`purge-expired-sessions` crontab at 2:00 AM UTC)                              |
| `asgi.py`     | 27    | ASGI entrypoint: `ProtocolTypeRouter` with `AuthMiddlewareStack` + `URLRouter` for WebSocket, Django HTTP handler                        |
| `wsgi.py`     | 16    | WSGI entrypoint (fallback; production uses ASGI via Daphne)                                                                              |
| `urls.py`     | 23    | Root URL configuration: includes `pipeline.urls`, serves `MEDIA_ROOT` in debug mode                                                      |

---

## `pipeline/` — Core Application

### Top-Level Modules

| File                    | Lines | Purpose                                                                                                                                                                                                                                                            |
| ----------------------- | ----- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------ |
| `__init__.py`           | —     | Package marker                                                                                                                                                                                                                                                     |
| `models.py`             | 184   | 4 Django models: `Session` (UUID PK, 14-day TTL), `AnalysisSubmission` (input/assay config, metadata JSON), `FileAsset` (16 file roles), `AnalysisJob` (Celery task state, `result_payload` JSON, `step_progress` JSON). `ModuleResult` removed in migration 0010. |
| `validators.py`         | 822   | Backend validation module: 10 core validators + extensible track-validator registry + warnings collector. Validates sample counts, contrasts, metadata match, names, FASTA headers, duplicate genes, missing values, ChIP-seq metadata, batch columns.             |
| `middleware.py`         | 53    | `AnonymousSessionMiddleware`: creates/validates UUID session cookie (`HttpOnly`, `SameSite=Lax`, 14-day max-age), attaches `request.session_obj`                                                                                                                   |
| `consumers.py`          | 66    | `PipelineProgressConsumer` (`AsyncWebsocketConsumer`): WebSocket at `ws/pipeline/<job_id>/`; validates session ownership; joins channel group for real-time progress                                                                                               |
| `routing.py`            | 10    | Django Channels URL routing: maps `ws/pipeline/<uuid>/` to `PipelineProgressConsumer`                                                                                                                                                                              |
| `context_processors.py` | 6     | Injects `dev_mode` flag into template context from `settings.DEBUG`                                                                                                                                                                                                |
| `admin.py`              | 1     | Empty (no admin registrations)                                                                                                                                                                                                                                     |
| `apps.py`               | 6     | Django `AppConfig` for `pipeline`                                                                                                                                                                                                                                  |
| `urls.py`               | —     | 6 page routes + 9 API routes. No `/advanced/` route (removed).                                                                                                                                                                                                     |
| `debug_payload.py`      | 129   | Generates synthetic `result_payload` dictionary for testing the Core Hub UI without running the real pipeline                                                                                                                                                      |

### `pipeline/views/` — HTTP View Layer

| File          | Lines | Purpose                                                                                                                                                                                                                                                                                                                                                                                                                                                                 |
| ------------- | ----- | ----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `__init__.py` | 22    | Re-exports all view classes for clean imports                                                                                                                                                                                                                                                                                                                                                                                                                           |
| `api.py`      | ~670  | 10 API views: `FileAssetDelete`, `CreateSubmissionView`, `DeleteSubmissionView`, `ChunkUploadView` (25 MB concurrent chunks, SSD-buffered with merge-on-complete, path-traversal sanitized), `CorePipelineView` (facade → validators → Celery dispatch), `JobStatusView` (stale-job detection via AsyncResult), `SessionAssetsView`, `FileDownloadView`, `ModuleRunView` (12 approved modules), `TusdHookView` (Tus post-finish webhook: moves uploaded file, creates FileAsset). Helpers: `_subdir_for_role()`, `_merge_and_move()`, `UPLOAD_BUFFER_ROOT` |
| `pages.py`    | 203   | 6 template-based views: `HomeView`, `TutorialsView`, `WorkspacesView`, `NewSubmissionView` (genome list context), `ProcessingView` (pipeline steps context), `CoreHubView` (plot data, download links, module jobs, BAM presence check)                                                                                                                                                                                                                                 |

### `pipeline/tasks/` — Celery Task Layer

| File                      | Lines | Purpose                                                                                                                                                                                                                                                                  |
| ------------------------- | ----- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------ |
| `__init__.py`             | —     | Package marker; re-exports `run_core_pipeline` and `run_tier2_module`                                                                                                                                                                                                    |
| `core.py`                 | —     | Two Celery tasks: `run_core_pipeline()` (entry-point dispatcher → track router → Stage 2) and `run_tier2_module()` (module dispatcher for WGCNA, RNA_EDITING, TIME_SERIES + 9 stubs)                                                                                     |
| `_constants.py`           | —     | CPU/parallelism settings, `_GENOME_FOLDER_MAP` (11 genomes), `_MIRBASE_SPECIES_MAP`, `_MACS2_GENOME_SIZE`                                                                                                                                                                |
| `_helpers.py`             | —     | Shared utilities: `_run()` (subprocess wrapper), `_q()` (shlex quote), `_pair_fastqs()`, `_update_step()`, `_emit_progress()` (Channels layer broadcast), `_run_fastqc_step()`, `_run_trim_step()`, `_run_multiqc_step()`, `_sort_and_index_bam()`, strandedness mappers |
| `_genome.py`              | —     | Genome resolution: `_decompress_if_needed()`, `_genome_paths()`, `_resolve_genome()`, `_resolve_mirbase()`, `_resolve_bwa_index()`, `_resolve_bismark_genome()`                                                                                                          |
| `_featurecounts.py`       | —     | `_run_featurecounts()`, `_detect_gff_gene_attr()` (GFF/GFF3 auto-detection), `_featurecounts_to_csv()`                                                                                                                                                                   |
| `_track_standard.py`      | 170   | Track A — Standard RNA-seq: `_route_fastq()` — HISAT2 alignment with custom genome build support, featureCounts, Stage 2 handoff                                                                                                                                         |
| `_track_mirna.py`         | 191   | Track B — Small RNA/miRNA: `_route_small_rna()`, `_run_bowtie_mirna()`, `_mirna_counts_from_bams()` (samtools idxstats). Trim MINLEN:18, miRBase species-specific index                                                                                                  |
| `_track_chipseq.py`       | 308   | Track C — ChIP-seq: `_route_chip_seq()`, `_run_bwa_align()`, `_run_macs2_callpeak()`, `_split_chip_samples()`, `_build_consensus_saf()` (bedtools merge → SAF)                                                                                                           |
| `_track_methyl.py`        | 191   | Track D — DNA Methylation: `_route_methylation()`, `_run_bismark_align()`, `_run_bismark_extract()`, methylKit handoff                                                                                                                                                   |
| `_routes.py`              | 214   | Non-FASTQ entry points: `_route_alignment()` (BAM/CRAM → featureCounts → Stage 2, with CRAM→BAM parallel conversion), `_route_matrix()` (CSV/TSV validation → Stage 2), `_register_stage2_assets()`                                                                      |
| `_module_wgcna.py`        | 399   | Module D — WGCNA: `execute_wgcna_and_pathways()` — 6-step pipeline (load, find modules via PyWGCNA, module-trait correlation, hub genes, Enrichr enrichment via gseapy, plots)                                                                                           |
| `_module_rna_editing.py`  | 422   | Module B — RNA Editing: `execute_rna_editing()` — 4-step pipeline (prepare, REDItools2, filter A→I / C→U edits with MIN_COVERAGE=10, substitution bar chart + HTML table)                                                                                                |
| `_module_timeseries.py`   | 273   | Module C — Time Series: `execute_timeseries()` — 4-step pipeline (load, annotation, ImpulseDE2 via rpy2, serialize). Simple longitudinal + case-control designs                                                                                                          |
| `_module_alt_splicing.py` | 503   | Module A — Alt Splicing: `execute_alt_splicing()` — 4-step pipeline (load, importRdata, isoformSwitchAnalysisPart1, serialize). IsoformSwitchAnalyzeR via rpy2, volcano plot (dIF vs q-value), switch consequence bar chart                                              |

### `pipeline/stats/` — Stage 2 Statistics Engine

| File                   | Lines | Purpose                                                                                                                                                                                                      |
| ---------------------- | ----- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------ |
| `__init__.py`          | 36    | Re-exports `run_stage2_stats`                                                                                                                                                                                |
| `core.py`              | 148   | `run_stage2_stats()` — Stage 2 orchestrator: filter → batch correction → DESeq2 → outlier detection → annotation → plots → asset registration                                                                |
| `_helpers.py`          | 186   | `_load_metadata()`, `_align_samples()` (robust sample column detection), `_filter_low_counts()`, `_combat_seq()` (sva::ComBat_seq via rpy2, covariate support), `_detect_outliers()` (PCA + Mahalanobis)     |
| `_deseq2.py`           | 333   | DESeq2 R bridge: formula builder, factor sanitization, multi-contrast extraction, numeric covariate support, proactive rank-deficiency check (patsy), contrast level validation, BiocParallel MulticoreParam |
| `_annotations.py`      | 177   | `annotate_deg_table()` — MyGene.info REST API: batched queries, exponential backoff, gene descriptions and disease associations                                                                              |
| `_plots.py`            | 248   | `_generate_plot_data()` — PCA (sklearn, variance explained), UMAP (umap-learn), Volcano (log2FC vs -log10 padj), MA (baseMean vs log2FC), Heatmap (z-score, top DEGs, group color annotations)               |
| `_methylkit.py`        | 326   | `run_differential_methylation()` — methylKit R bridge: methRead, filter, normalize, unite, calculateDiffMeth, CSV export; PCA, volcano, MA plot data                                                         |
| `_plots_wgcna.py`      | 201   | `build_module_trait_heatmap()`, `build_pathway_dotplot()` — WGCNA-specific Plotly JSON helpers                                                                                                               |
| `_plots_timeseries.py` | 249   | `build_timeseries_payload()` — ImpulseDE2 result serializer; trajectory line chart with grouped means and SEM error bars                                                                                     |
| `_r_bridge.py`         | 53    | Lazy rpy2 initialization (suppressed warnings), shared converter; exports `ro`, `localconverter`, `importr`, `_converter`, `_R_CORES`                                                                        |

### `pipeline/management/commands/` — Management Commands

| File               | Lines | Purpose                                                                                       |
| ------------------ | ----- | --------------------------------------------------------------------------------------------- |
| `purge_expired.py` | 99    | `python manage.py purge_expired [--dry-run]` — deletes expired sessions (DB + NFS), audit log |

---

## `pipeline/templates/pipeline/` — HTML Templates

| File                  | Lines | Purpose                                                                                                                                                                            |
| --------------------- | ----- | ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `base.html`           | 93    | Base template: navbar (logo, nav links, 3-step workflow indicator, session badge), footer (14-day retention notice), CSRF meta tag, font/icon imports                              |
| `home.html`           | 249   | Landing page: hero slideshow (3 images, static dots), 4 action buttons, "How It Works" flow, capabilities grid, 12-module grid, reference genomes table                            |
| `tutorials.html`      | 1277  | File format guide, metadata mapping, pipeline workflow, reference genomes table, expanded documentation sections                                                                   |
| `new_submission.html` | 966   | 5-step wizard: submission name → entry point/assay/library/upload → genome selection → metadata (CSV or builder, column mapping, contrasts) → parameters/review/submit             |
| `processing.html`     | 433   | Dynamic step cards, progress bar, WebSocket connection with HTTP polling fallback, error overlay with pattern-matched diagnostic hints, success redirect to Core Hub               |
| `core_hub.html`       | 716   | 3-tab layout (Overview/Modules/Single-Cell): Plotly theater (5 plots), download buttons, figure export; master-detail module hub (12 cards); deconvolution gateway + locked spokes |
| `workspaces.html`     | 146   | Jobs table (submission name, module, status badges, action links), dev mode badge, 14-day retention warning, empty state                                                           |

**Removed templates:** `advanced.html` (standalone Tier 4 page) and `index.html` — both deleted. Spoke cards now live in Core Hub Single-Cell tab.

---

## `pipeline/static/pipeline/js/` — JavaScript

| File                | Lines | Purpose                                                                                                                                                                                                                                                                                                                                                                                                                                                |
| ------------------- | ----- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------ |
| `pipeline_setup.js` | ~2760 | 5-step wizard navigation, entry point/assay/library selectors, FASTQ/BAM/Matrix drop zones, concurrent chunked upload (25 MB chunks, 6 concurrent workers per file, 3 retries, 5-min timeout), paired-end validation, CSV metadata with PapaParse, manual metadata builder, column mapping, contrast builder, per-step validation, 9 of 10 backend validation rules mirrored client-side (FASTA header `>` check is backend-only), toast notifications |
| `core_hub.js`       | 1773  | Tab switching, Plotly resize, Module Hub state machine (empty/history/form/result), 12 module form builders with D&D and tabbed input, history list with status badges, result view with Plotly rendering + download, module submission + polling, deconvolution gateway + spoke unlock, 5 Plotly render functions (PCA, UMAP, Volcano, MA, Heatmap), interactive figure export as HTML                                                                |

---

## `pipeline/static/pipeline/css/` — Stylesheets

| File                    | Lines | Purpose                                                                                                                                                                         |
| ----------------------- | ----- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `variables.css`         | 54    | Design tokens: navy-teal-mint palette, gradients, typography scale, border radii, shadows                                                                                       |
| `global.css`            | 2418  | Component library: navbar, footer, buttons, cards, module cards (category color icons), grids, forms, badges, progress bars, tabs, master-detail layout, dropzones, toast anim. |
| `submission_layers.css` | 2020  | 5-step wizard layout (1/3 + 2/3 split-screen), step indicators, radio cards, drop zones with file pills, metadata table/builder, contrast builder, upload modal, tooltips       |

---

## `test/` — Test Suite (12 files)

| File                        | Lines | Coverage Area                                                                                                 |
| --------------------------- | ----- | ------------------------------------------------------------------------------------------------------------- |
| `test_entry_points.py`      | 753   | Model fields, view validation per entry type, upload routing, task dispatch, genome resolution, matrix checks |
| `test_stage2.py`            | 304   | DESeq2 formula builder, gene filtering, outlier detection, sample alignment, contrasts, covariates            |
| `test_stage2_edge_cases.py` | 607   | Numeric covariates, rank-deficient designs, contrast level validation                                         |
| `test_assay_tracks.py`      | 1018  | Assay model fields, file roles, pipeline steps, 4-track dispatch, shared helpers, ChIP-seq split, miRNA flags |
| `test_wgcna.py`             | 516   | Heatmap structure, module-trait correlation, hub genes, enrichment dotplot, dispatch, failure handling        |
| `test_validators.py`        | 752   | All 10 backend validators: sample counts, contrasts, names, FASTA headers, matrix match, duplicates, ChIP-seq |
| `test_upload_api.py`        | 661   | Upload API: chunked upload, file roles, path sanitization, abort, edge cases                                  |
| `test_hisat2_pipeline.py`   | 282   | HISAT2 alignment integration tests                                                                            |
| `test_dev_dataset.py`       | 259   | Dev dataset validation (metadata, sample matching)                                                            |
| `test_e2e.py`               | 373   | Full end-to-end: synthetic yeast FASTQ → Stage 1 → Stage 2 (used in GitHub Actions CI)                        |
| `test_genome_indices.py`    | 155   | Genome index resolution, pre-built index verification                                                         |
| `test_tusd_hooks.py`        | ~200  | TusdHookView: 18 tests covering post-finish file creation, file move, sidecar cleanup, file_role handling, session validation, error cases |

---

## `doc/` — Documentation

| File                                              | Purpose                                                                           |
| ------------------------------------------------- | --------------------------------------------------------------------------------- |
| `progress.md`                                     | Project progress tracker (this audit's primary target)                            |
| `architecture.md`                                 | System architecture overview                                                      |
| `RNAseek Pipeline Blueprint.md`                   | Current pipeline blueprint (v1.3)                                                 |
| `submission-flow-and-validation.md`               | Submission wizard flow, frontend + backend validation rule tables, payload shapes |
| `archive/RNAseek Frontend Blueprint.md`           | Archived frontend design spec                                                     |
| `archive/RNAseek Pipeline Blueprint.md`           | Archived pipeline blueprint (earlier version)                                     |
| `archive/RNAseek Pipeline Output.md`              | Archived output specification                                                     |
| `archive/RNAseek Pipeline User Input.md`          | Archived user input specification                                                 |
| `guide/RNAseek User Tutorial.md`                  | End-user tutorial                                                                 |
| `guide/Docker/Docker Deployment Guide.md`         | Docker production deployment guide                                                |
| `guide/Docker/Docker Development Guide.md`        | Docker development environment guide                                              |
| `guide/Production/Production Commands.md`         | Production server command reference                                               |
| `guide/Production/Production Deployment Guide.md` | Bare-metal production deployment guide (tusd, Nginx, systemd, NFS)                |
| `guide/Development/Development Guide.md`          | Local development setup, testing, project structure                                |

### `doc/script/` — Reference Genome Build Scripts

| File                       | Purpose                                                        |
| -------------------------- | -------------------------------------------------------------- |
| `download_genomes.sh`      | Downloads FASTA + GTF for all 11 supported reference genomes   |
| `build_hisat2_indices.sh`  | Builds HISAT2 `.ht2` indices for all genomes                   |
| `build_bwa_indices.sh`     | Builds BWA indices for ChIP-seq track                          |
| `build_bismark_indices.sh` | Builds Bismark indices for methylation track                   |
| `build_mirbase_indices.sh` | Downloads miRBase hairpins + builds Bowtie indices per species |

---

## `nginx/`

| File           | Purpose                                                                                               |
| -------------- | ----------------------------------------------------------------------------------------------------- |
| `rnaseek.conf` | Nginx: HTTPS (Let's Encrypt), WebSocket `/ws/` upgrade, Tus `/files/` proxy to tusd (streaming, no request buffering), `client_max_body_size 0` (unlimited), 30-day static cache, gzip |

---

## `systemd/`

| File                     | Purpose                                               |
| ------------------------ | ----------------------------------------------------- |
| `rnaseek-web.service`    | systemd unit for Daphne ASGI server                   |
| `rnaseek-worker.service` | systemd unit for Celery worker (prefork, CPU-matched) |
| `rnaseek-beat.service`   | systemd unit for Celery Beat scheduler                |

---

## `scripts/`

| File                            | Purpose                                                                                            |
| ------------------------------- | -------------------------------------------------------------------------------------------------- |
| `benchmark-upload-capacity.sh`  | Non-disruptive production benchmark: network bandwidth (iperf3/curl), NFS disk I/O (dd/fio)        |

---

## `.github/` — CI/CD and Agent Configuration

| File                           | Purpose                                                                                     |
| ------------------------------ | ------------------------------------------------------------------------------------------- |
| `workflows/e2e.yml`            | GitHub Actions: Miniconda setup, tool verification, yeast index build, migrations, E2E test |
| `instructions.md`              | Copilot agent instructions for this repository                                              |
| `AGENTS.md`                    | Copilot agent definitions                                                                   |
| `skills/celery-tasks/SKILL.md` | Copilot skill: Celery task conventions and patterns                                         |
| `skills/file-assets/SKILL.md`  | Copilot skill: FileAsset handling conventions                                               |
| `skills/r-bridge/SKILL.md`     | Copilot skill: rpy2 R bridge conventions                                                    |

---

## `rnaseek_dev_dataset/`

| File           | Purpose                                     |
| -------------- | ------------------------------------------- |
| `metadata.csv` | Sample metadata CSV for development/testing |

---

## `pipeline/migrations/` — Database Migrations

Django auto-generated migration files. Notable migrations:
- `0010_remove_moduleresult.py` — Removes the `ModuleResult` model (outputs now stored in `AnalysisJob.result_payload`)

---

## `pipeline/reference_genomes/` — Pre-Built Genome Indices

Contains HISAT2 `.ht2` index files, FASTA sequences, and GTF annotations for 11 supported reference genomes (human GRCh38, mouse GRCm39/GRCm38, rat rn7, zebrafish GRCz11, chicken GRCg6a, pig Sscrofa11.1, Drosophila dm6, C. elegans WBcel235, yeast sacCer3, Arabidopsis TAIR10). Also contains BWA indices, Bismark genomes, and miRBase Bowtie indices for specialized tracks.
