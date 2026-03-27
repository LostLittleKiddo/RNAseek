# RNAseek -- Project Progress

> **Blueprint version:** 1.3  
> **Audit date:** March 24, 2026  

---

## Legend

| Icon    | Meaning                                  |
| ------- | ---------------------------------------- |
| Done    | Frontend and backend fully implemented   |
| Partial | Details below on what is done vs missing |
| No      | No implementation exists                 |

---

## Phase 1: Infrastructure Architecture

### 1.1 Application Layer

| Feature                  | Status | Notes                                                                                                                |
| ------------------------ | ------ | -------------------------------------------------------------------------------------------------------------------- |
| Django 5.2 (Daphne ASGI) | Done   | Serves HTTP and WebSocket via Django Channels                                                                        |
| Redis 7+ Message Broker  | Done   | Celery broker and Channels layer backend in `settings.py`                                                            |
| Celery 5.6 Worker Fleet  | Done   | Asymmetric queue routing: CPU-bound worker (`cpu_bound` queue, concurrency=5, 8 threads/tool, 40 cores) and RAM-bound worker (`ram_bound` queue, concurrency=4, MemoryMax=96G). `CELERY_TASK_ROUTES` in `settings.py` dispatches `run_core_pipeline` to CPU pool and `run_tier2_module` to RAM pool. |
| Nginx Reverse Proxy      | Done   | `nginx/rnaseek.conf`: HTTPS (Let's Encrypt), WebSocket `/ws/` upgrade, Tus `/files/` proxy to tusd (keepalive 64 upstream pool, streaming, `proxy_request_buffering off`, `Upload-Concat` header for parallel chunks), `client_max_body_size 0` (unlimited), `sendfile on; tcp_nopush on; tcp_nodelay on`, 30-day static cache |

### 1.2 Storage Layer (POSIX Shared NFS)

| Feature                     | Status | Notes                                                                                                             |
| --------------------------- | ------ | ----------------------------------------------------------------------------------------------------------------- |
| Shared `/app/media/` mount  | Done   | `docker-compose.yml` defines `media-data` volume on web, worker, beat, tusd; NFS-ready with documented mount flags (async, noatime, rsize/wsize 1 MB, hard) |
| Reference genome file store | Done   | 11 pre-indexed genomes under `pipeline/reference_genomes/`                                                        |

### 1.3 Security Layer (Anonymous Sessions)

| Feature                | Status | Notes                                                                                                                                       |
| ---------------------- | ------ | ------------------------------------------------------------------------------------------------------------------------------------------- |
| UUID cookie middleware | Done   | `AnonymousSessionMiddleware`: `HttpOnly`, `SameSite=Lax`, 14-day max-age. Creates/validates `Session` model, attaches `request.session_obj` |
| Tenant isolation       | Done   | Every DB row (FileAsset, AnalysisJob, AnalysisSubmission) scoped to Session via FK                                                          |

---

## Phase 2: Data Model (Django ORM)

| Model                | Status | Notes                                                                                                                                                                                                                                                                                                                                                             |
| -------------------- | ------ | ----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `Session`            | Done   | UUID PK, `created_at`, `expires_at` (14-day TTL), `is_expired` property                                                                                                                                                                                                                                                                                           |
| `AnalysisSubmission` | Done   | UUID PK, FK to Session, `input_data_type` (fastq/alignment/matrix), `assay_type` (standard_rna/small_rna/chip_seq/methylation), `submission_name`, `library_type`, `strandedness`, `reference_genome`, `metadata_mode`, `metadata_payload` (JSON), threshold fields, `custom_genome_name`, `upload_dir` property                                                  |
| `FileAsset`          | Done   | UUID PK, FK to Session, FK to Submission (nullable), 15 `file_role` choices (RAW_FASTQ, ALIGNMENT_BAM, USER_COUNT_MATRIX, COUNT_MATRIX, NORMALIZED_COUNTS, DEG_TABLE, MULTIQC_REPORT, H5AD_PSEUDO, HE_IMAGE_USER, HE_IMAGE_GENERIC, PEAK_FILE, METHYLATION_REPORT, CUSTOM_GENOME_FASTA, CUSTOM_GENOME_ANNOTATION, METADATA_CSV), `local_path`, `is_user_uploaded` |
| `AnalysisJob`        | Done   | UUID PK (= Celery task ID), FK to Session, FK to Submission (`parent_submission`, nullable), `is_core_pipeline` boolean, `module_name`, `status` (PENDING/RUNNING/SUCCESS/FAILED), `result_payload` (JSON), `step_progress` (JSON), timestamps                                                                                                                    |
| `ModuleResult`       | Done   | Removed. Module outputs stored in `AnalysisJob.result_payload`. Model deleted in migration `0010_remove_moduleresult`.                                                                                                                                                                                                                                            |
| `ReferenceGenome`    | Done   | Not a DB model. Resolved via filesystem lookup in `_genome.py` using `_GENOME_FOLDER_MAP` dict.                                                                                                                                                                                                                                                                   |

---

## Phase 3: API Facade and Data Ingestion

### 3.1 Chunked Uploader

| Feature                  | Status | Notes                                                                                                                                                                                                                                                                                                                                                                                                                   |
| ------------------------ | ------ | ----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `POST /api/upload/chunk` | Done   | 25 MB chunks (concurrent, up to 6 in-flight per file), SSD-buffered with merge-on-complete (no direct NFS writes during upload), path-traversal sanitization, atomic chunk writes via tmp+rename, POSIX-atomic merge claim (`O_EXCL`), `shutil.move()` to NFS on completion, role-aware subdirectory routing (raw/, aligned/, counts/, metadata/, custom_genome/), `FileAsset` created after merge, temp buffer cleanup |
| Frontend chunked upload  | Done   | `pipeline_setup.js`: drop zones for FASTQ, BAM, Matrix, custom genome files; concurrent chunk upload (25 MB chunks, 6 workers via `uploadFileConcurrently()`), progress tracking, retry logic (3 attempts, 5-min timeout per chunk), abort support                                                                                                                                                                      |
| Tus resumable uploads    | Done   | tusd v2.9.2 daemon handles Tus protocol uploads at `/files/`. Nginx proxies with streaming (keepalive 64 pool, no request buffering, `Upload-Concat` header). Post-finish webhook notifies Django at `/api/tusd-hooks/` to register FileAsset. tusd v2 sends event type as `Type` in the JSON body (view supports both the v2 body field and the legacy v1 `Hook-Name` header). Enabled events limited to `post-finish` via `-hooks-enabled-events`. `-disable-download` blocks file retrieval via tusd. `LimitNOFILE=65536`. Frontend uses Uppy.js with `parallelUploads: 6` (Concatenation extension) and 100 MB chunk size for maximum WAN throughput. Systemd service: `rnaseek-tusd`. Supports resume after network interruption. |

### 3.2 Master Router

| Feature                                                 | Status | Notes                                                                                                                                       |
| ------------------------------------------------------- | ------ | ------------------------------------------------------------------------------------------------------------------------------------------- |
| `POST /api/pipeline/core`                               | Done   | Facade pattern: delegates all validation to `validators.py`; sets `pipeline_steps`; dispatches `run_core_pipeline` Celery task              |
| State routing (fastq/alignment/matrix)                  | Done   | Full validation per entry type; conditional field requirements (matrix skips genome)                                                        |
| Assay routing (standard/small_rna/chip_seq/methylation) | Done   | Each assay generates correct `pipeline_steps` list; FASTQ entry point only. Small RNA restricted to miRBase genomes; custom genome blocked. |

### 3.4 Backend Validation Module

| Feature                                 | Status | Notes                                                                                                                                           |
| --------------------------------------- | ------ | ----------------------------------------------------------------------------------------------------------------------------------------------- |
| `pipeline/validators.py` extraction     | Done   | All inline validation extracted from `CorePipelineView` into a dedicated module with 10 core validators + extensible track-validator registry   |
| Minimum sample counts                   | Done   | FASTQ SE >= 2 files, FASTQ PE >= 2 pairs (4 files), BAM >= 2 files, Matrix >= 3 columns (gene ID + 2 samples)                                   |
| Contrast level validation               | Done   | Target and reference values must exist in the primary group column of metadata                                                                  |
| Small RNA forces single-end             | Done   | `small_rna` with `paired` library type rejected at both frontend (auto-force) and backend (hard error)                                          |
| Duplicate gene ID detection             | Done   | All rows of count matrix scanned for duplicate gene IDs (not just first 50)                                                                     |
| Missing data rejection                  | Done   | Empty, NA, NaN, and null cells in count matrix rejected                                                                                         |
| Matrix-metadata sample match            | Done   | Matrix column headers must match metadata sample names exactly; mismatch details reported                                                       |
| Sanitized names (`SAFE_NAME_RE`)        | Done   | Sample names and custom genome names validated against `^[a-zA-Z0-9_-]+$`                                                                       |
| FASTA header validation                 | Done   | First non-empty line of uploaded custom genome FASTA must start with `>` (supports gzip)                                                        |
| Frontend validation hardening           | Done   | 9 of 10 rules mirrored in `pipeline_setup.js` with toast notifications (Steps 2, 3, 4, pre-submission). FASTA header `>` check is backend-only. |
| `doc/submission-flow-and-validation.md` | Done   | Complete reference doc: every wizard step, frontend + backend rule tables, pre-submission sweep, payload shape, pipeline step routing           |

### 3.3 Other API Endpoints

| Endpoint                                          | Status  | Notes                                                                                                                                                                             |
| ------------------------------------------------- | ------- | --------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `POST /api/submission/create`                     | Done    | Creates `AnalysisSubmission`, returns UUID                                                                                                                                        |
| `POST /api/submission/delete`                     | Done    | Cascade-deletes submission + disk files; `sendBeacon`-safe (csrf_exempt)                                                                                                          |
| `GET /api/jobs/<uuid>/`                           | Done    | Polls job status; detects stale RUNNING jobs via Celery `AsyncResult` (marks FAILED if REVOKED)                                                                                   |
| `GET /api/session/assets`                         | Done    | Lists session file assets; filterable by `role` and `job_id`                                                                                                                      |
| `GET /api/download/<uuid>`                        | Done    | Serves files with path-traversal protection and MIME detection                                                                                                                    |
| `DELETE /api/files/<uuid>/`                       | Done    | Deletes user-uploaded asset from DB and disk                                                                                                                                      |
| `POST /api/tusd-hooks/`                           | Done    | tusd post-finish webhook: validates session, moves uploaded file to submission subdirectory, creates FileAsset. 18 unit tests.                                                    |
| `POST /api/submissions/<uuid>/modules/<name>/run` | Partial | API routing and validation complete for all 12 modules. Alt Splicing, WGCNA, RNA Editing, and Time Series dispatch to real backend engines; 8 others return placeholder payloads. |

---

## Phase 4: Hub Engine -- Stage 1 Multi-Track

### Track A: Standard Transcriptomics (Poly-A RNA-Seq)

| Step                         | Status | Notes                                                                                                      |
| ---------------------------- | ------ | ---------------------------------------------------------------------------------------------------------- |
| FastQC                       | Done   | Parallelized (`-t CPU_COUNT`), shared helper `_run_fastqc_step()`                                          |
| Trimmomatic                  | Done   | Parallel per-sample via `ThreadPoolExecutor`; single-end and paired-end modes                              |
| HISAT2 alignment             | Done   | Splice-aware; `_TOOL_THREADS` per sample, parallel via `ThreadPoolExecutor`; SAM to sorted/indexed BAM     |
| Custom genome HISAT2 build   | Done   | Tracked as separate `hisat2_build` pipeline step; warning banner in UI                                     |
| featureCounts quantification | Done   | Gene-level and transcript-level; GFF/GFF3 auto-detection (`_detect_gff_gene_attr`); CSV conversion         |
| MultiQC report               | Done   | Interactive HTML saved to submission hub                                                                   |
| Stage 2 handoff              | Done   | Calls `run_stage2_stats()` after quantification                                                            |
| Frontend (processing page)   | Done   | All steps tracked with icons (idle/running/done/failed), progress bar, error overlay with diagnostic hints |

### Track B: Regulatory Transcriptomics (Small RNA / miRNA)

| Step                             | Status | Notes                                                                                               |
| -------------------------------- | ------ | --------------------------------------------------------------------------------------------------- |
| Bowtie alignment against miRBase | Done   | `_run_bowtie_mirna()` with species-specific miRBase index resolution                                |
| miRNA quantification             | Done   | `_mirna_counts_from_bams()` via `samtools idxstats`                                                 |
| Route orchestrator               | Done   | `_route_small_rna()`: FastQC, Trim (MINLEN:18), Bowtie, quantify, MultiQC, Stage 2                  |
| Frontend                         | Done   | Small RNA pipeline steps rendered in processing page; assay type selector card in submission wizard |

### Track C: Epigenomics -- ChIP-seq

| Step                           | Status | Notes                                                                          |
| ------------------------------ | ------ | ------------------------------------------------------------------------------ |
| BWA MEM alignment              | Done   | `_run_bwa_align()` with BWA index resolution                                   |
| MACS2 peak calling             | Done   | `_run_macs2_callpeak()` with genome-size lookup (`_MACS2_GENOME_SIZE`)         |
| Consensus peak SAF             | Done   | `_build_consensus_saf()` via `bedtools merge` to SAF format                    |
| featureCounts on peaks         | Done   | Treatment BAMs only; `-F SAF` flag                                             |
| Input/Control sample splitting | Done   | `_split_chip_samples()` from metadata                                          |
| Route orchestrator             | Done   | `_route_chip_seq()`: FastQC, Trim, BWA, MACS2, featureCounts, MultiQC, Stage 2 |
| Frontend                       | Done   | ChIP-seq pipeline steps rendered; assay type card with metadata guidance       |

### Track C: Epigenomics -- DNA Methylation

| Step                                 | Status | Notes                                                                                                                       |
| ------------------------------------ | ------ | --------------------------------------------------------------------------------------------------------------------------- |
| Bismark genome preparation           | Done   | `_resolve_bismark_genome()`                                                                                                 |
| Bismark alignment                    | Done   | `_run_bismark_align()`                                                                                                      |
| Methylation extraction               | Done   | `_run_bismark_extract()`                                                                                                    |
| Differential methylation (methylKit) | Done   | `_methylkit.py` R bridge: `methRead`, filter, normalize, unite, `calculateDiffMeth`, CSV export; PCA, volcano, MA plot data |
| Route orchestrator                   | Done   | `_route_methylation()`: FastQC, Trim, Bismark prep, align, extract, MultiQC, methylKit                                      |
| Frontend                             | Done   | Methylation pipeline steps rendered; assay type card                                                                        |

### Entry Points B and C (Non-FASTQ)

| Feature                         | Status | Notes                                                                                                          |
| ------------------------------- | ------ | -------------------------------------------------------------------------------------------------------------- |
| Route B: Alignment (BAM/CRAM)   | Done   | `_route_alignment()`: CRAM-to-BAM conversion (parallel), BAM indexing, featureCounts, Stage 2                  |
| Route C: Count Matrix (CSV/TSV) | Done   | `_route_matrix()`: CSV/TSV loading, validation (non-empty, all-numeric, non-negative), canonical copy, Stage 2 |
| Frontend entry point selector   | Done   | 3 radio cards; conditional UI show/hide; matrix mode skips genome step in wizard                               |

---

## Phase 5: Convergence and Normalization (Stage 2)

| Feature                            | Status | Notes                                                                                                                                                                                                                  |
| ---------------------------------- | ------ | ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| Gene filtering                     | Done   | `_filter_low_counts()` with `min_total=10` threshold                                                                                                                                                                   |
| Batch Correction (ComBat-seq)      | Done   | `_combat_seq()` via `sva::ComBat_seq()` through rpy2; conditional on batch column in metadata; supports additional covariates passed as `covar_mod` matrix                                                             |
| DESeq2 normalization and DGE       | Done   | `_run_deseq2()`: dynamic formula construction, multi-contrast extraction, BiocParallel `MulticoreParam`; numeric covariate support; proactive rank-deficiency check via patsy; contrast level validation before R call |
| Mahalanobis outlier detection      | Done   | `_detect_outliers()`: PCA-based Mahalanobis distance                                                                                                                                                                   |
| Automated annotation (MyGene.info) | Done   | `annotate_deg_table()`: batched REST API queries, exponential backoff retry, appends gene descriptions and disease associations                                                                                        |
| Plotly visualization data          | Done   | `_generate_plot_data()`: PCA (sklearn, variance explained), UMAP (umap-learn), Volcano (log2FC vs -log10 padj with threshold lines), MA (baseMean vs log2FC), Heatmap (z-score, top DEGs, group color annotations)     |
| Frontend (Core Hub)                | Done   | All 5 plots rendered via Plotly.js 2.35.2 with custom color scheme, hover templates, responsive layout                                                                                                                 |
| Downloads                          | Done   | 5 download items: aligned BAMs, raw counts, normalized counts, DEG table, MultiQC report via `FileDownloadView`                                                                                                        |

### Stats Package Source Files

| File                   | Purpose                                                                                                                                                                         |
| ---------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `core.py`              | `run_stage2_stats()` -- main Stage 2 driver; orchestrates all steps below                                                                                                       |
| `_helpers.py`          | `_load_metadata()`, `_align_samples()` (robust `sample` column detection), `_filter_low_counts()`, `_combat_seq()` (covariate support via `covar_mod`), `_detect_outliers()`    |
| `_deseq2.py`           | DESeq2 R bridge: formula builder, factor sanitization, multi-contrast extraction, numeric covariate support, proactive rank-deficiency check (patsy), contrast level validation |
| `_annotations.py`      | `annotate_deg_table()` -- MyGene.info REST API batched queries                                                                                                                  |
| `_methylkit.py`        | `run_differential_methylation()` -- methylKit R bridge for bisulfite data                                                                                                       |
| `_plots.py`            | `_generate_plot_data()` -- PCA, UMAP, Volcano, MA, Heatmap JSON serialization for Plotly.js                                                                                     |
| `_plots_wgcna.py`      | `build_module_trait_heatmap()`, `build_pathway_dotplot()` -- WGCNA-specific Plotly helpers                                                                                      |
| `_plots_timeseries.py` | `build_timeseries_payload()` -- ImpulseDE2 result serializer; trajectory line chart with grouped means and SEM error bars                                                       |
| `_r_bridge.py`         | Shared lazy rpy2 initialization; provides `ro`, `localconverter`, `importr`, `_converter`, `_R_CORES`                                                                           |

---

## Phase 6: Standard Analytical Spokes (Tier 2 Modules)

| #   | Module          | Blueprint Engine        | Status | Details                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                |
| --- | --------------- | ----------------------- | ------ | -------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| A   | Alt Splicing    | `IsoformSwitchAnalyzeR` | Done   | `_module_alt_splicing.py`: 4-step pipeline (`splicing_load_data`, `splicing_import_rdata`, `splicing_analysis`, `splicing_serialize`). Resolves ALIGNMENT_BAM assets and reference GTF, builds condition design matrix from frontend payload (manual table or CSV upload), runs `importRdata()` and `isoformSwitchAnalysisPart1()` via rpy2 bridge, extracts top switches. Plotly volcano plot (dIF vs -log10 q-value) and switch consequence bar chart. API endpoint and frontend (manual BAM condition table / CSV upload) complete. |
| B   | RNA Editing     | `REDItools2`            | Done   | `_module_rna_editing.py`: 4-step pipeline (`rna_editing_prepare`, `rna_editing_reditools`, `rna_editing_filter`, `rna_editing_plots`). Runs REDItools2 on each ALIGNMENT_BAM, merges TSV output, filters for A-to-I (AG) and C-to-U (TC) edits with `MIN_COVERAGE=10`, builds substitution bar chart and HTML table preview. API endpoint and frontend (whole-transcriptome checkbox + BED dropzone) complete.                                                                                                                         |
| C   | Time Series     | `ImpulseDE2`            | Done   | `_module_timeseries.py`: 4-step pipeline (`ts_load_data`, `ts_build_annotation`, `ts_run_impulse`, `ts_serialize`). Supports simple longitudinal and case-control longitudinal designs. rpy2 bridge to ImpulseDE2 R engine. `_plots_timeseries.py` builds trajectory line chart with grouped means and SEM error bars. API endpoint and frontend (timepoints mapping + time unit dropdown) complete.                                                                                                                                   |
| D   | WGCNA           | `PyWGCNA`               | Done   | `_module_wgcna.py`: 6-step pipeline (load, find modules, module-trait correlation, hub genes, Enrichr enrichment, plots). Stats helpers in `_plots_wgcna.py`. 24 unit tests. Frontend: card, form with soft-power threshold + clinical traits (CSV/example/builder).                                                                                                                                                                                                                                                                   |
| E   | Pathways        | `gseapy`                | No     | API endpoint registered. Frontend: card, form with gene-set database dropdown (Hallmark, C2 KEGG, C5 GO BP, C5 GO MF, Reactome) + FDR threshold. Backend returns placeholder.                                                                                                                                                                                                                                                                                                                                                          |
| F   | Causal Networks | `arboreto` (GRNBoost2)  | No     | API endpoint registered. Frontend: card, form with TF textarea + STRING confidence threshold. Backend returns placeholder.                                                                                                                                                                                                                                                                                                                                                                                                             |
| G   | Literature NLP  | INDRA Bio API           | No     | API endpoint registered. Frontend: card, form with context keywords input. Backend returns placeholder.                                                                                                                                                                                                                                                                                                                                                                                                                                |
| H   | Survival        | `lifelines`             | No     | API endpoint registered. Frontend: card, form with genes input + clinical survival data (CSV/example/builder). Backend returns placeholder.                                                                                                                                                                                                                                                                                                                                                                                            |
| I   | TCGA Cancer     | `TCGAbiolinks`          | No     | API endpoint registered. Frontend: card, form with TCGA cohort dropdown (8 cohorts). Backend returns placeholder.                                                                                                                                                                                                                                                                                                                                                                                                                      |
| J   | Biomarkers      | MarkerDB API            | No     | API endpoint registered. Frontend: card, form with disease context input. Backend returns placeholder.                                                                                                                                                                                                                                                                                                                                                                                                                                 |
| K   | MOFA            | `mofapy2`               | No     | API endpoint registered. Frontend: card, form with factors input + secondary omics matrix (CSV/example/builder). Backend returns placeholder. `mofapy2` commented out in `requirements.txt`.                                                                                                                                                                                                                                                                                                                                           |
| L   | DIABLO          | `mixOmics`              | No     | API endpoint registered. Frontend: card, form with components input + secondary omics matrix (CSV/example/builder). Backend returns placeholder.                                                                                                                                                                                                                                                                                                                                                                                       |

**Summary:** 4 of 12 modules fully implemented (Alt Splicing, WGCNA, RNA Editing, Time Series). 8 modules have complete frontend UI (master-detail cards with module-specific form inputs in the detail pane) and API endpoint routing, but return stub payloads from the Celery task layer.

### Module Hub UI (Master-Detail State Machine)

The Modules tab in `core_hub.html` uses a master-detail split pane. The left master pane lists 12 module cards (Alt Splicing conditionally shown based on `has_bam_files` template variable). The right detail pane is driven by a state machine in `core_hub.js` with four views:

1. **Empty state** -- default, prompts user to select a module.
2. **History list** -- shows past runs with status badges (Completed/Processing/Pending/Failed), "New Run" button, and clickable completed entries.
3. **New run form** -- module-specific configuration form with "Back to History" navigation and "Run Module" submit button. Complex modules use tabbed data input (Upload CSV / Example Format / Manual Builder).
4. **Result view** -- displays payload (summary text, Plotly plots, data preview tables, hub genes, enrichment summary), with download and back navigation.

### Hub Isolation Principle

| Feature                       | Status  | Notes                                                                                                                                     |
| ----------------------------- | ------- | ----------------------------------------------------------------------------------------------------------------------------------------- |
| `is_core_pipeline` flag       | Done    | `AnalysisJob` has `is_core_pipeline` boolean (default `True`); module jobs created with `False`                                           |
| Module output storage         | Done    | Module outputs stored in `AnalysisJob.result_payload`; no separate model                                                                  |
| WebSocket broadcast isolation | Partial | Consumer broadcasts to `pipeline_{job_id}` channel group but does not distinguish core vs hub broadcasts based on `is_core_pipeline` flag |

---

## Phase 7: Predictive Single-Cell and Spatial Gateway (Tier 3 and 4)

### Tier 3: Deconvolution Engine

| Feature                     | Status | Notes                                                                                                                                                   |
| --------------------------- | ------ | ------------------------------------------------------------------------------------------------------------------------------------------------------- |
| DestVI / BayesPrism backend | No     | No implementation. `scvi-tools` commented out in `requirements.txt`.                                                                                    |
| `.h5ad` AnnData output      | No     | `H5AD_PSEUDO` file role defined in `FileAsset` model but no code generates `.h5ad` files                                                                |
| Frontend gateway UI         | Done   | Atlas selector dropdown (4 atlases), high-resolution toggle, "Run Deconvolution" button, completion polling, spoke unlock logic -- all in `core_hub.js` |

### Tier 4: Advanced Spatial Spokes

| Feature                           | Status | Notes                                                                                                         |
| --------------------------------- | ------ | ------------------------------------------------------------------------------------------------------------- |
| Trajectory Inference (scanpy)     | No     | No backend. `scanpy` installed but unused for trajectory/PAGA. Locked spoke card in Core Hub Single-Cell tab. |
| Spatial Mapping (Tangram)         | No     | No backend. `tangram-sc` commented out in `requirements.txt`. Locked spoke card.                              |
| Spatial Autocorrelation (Squidpy) | No     | No backend. `squidpy` commented out in `requirements.txt`. Locked spoke card.                                 |

---

## Phase 8: DevOps, Janitorial and Server Security

### 8.1 Auto-Purge Janitor

| Feature                       | Status | Notes                                                                                     |
| ----------------------------- | ------ | ----------------------------------------------------------------------------------------- |
| Celery Beat schedule          | Done   | `purge-expired-sessions` crontab at 2:00 AM UTC in `config/celery.py`                     |
| `purge_expired_sessions` task | Done   | Queries expired `Session` rows, `shutil.rmtree()` on NFS directory, Django cascade delete |
| Management command            | Done   | `python manage.py purge_expired [--dry-run]` with `--dry-run` flag and audit logging      |

### 8.2 CI/CD and Testing

| Feature                | Status | Notes                                                                                                                                                            |
| ---------------------- | ------ | ---------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| GitHub Actions E2E     | Done   | `.github/workflows/e2e.yml`: Miniconda setup, bioinformatics tool verification, yeast HISAT2 index build, migrations, `test_e2e.py` on synthetic sacCer3 dataset |
| Test suite             | Done   | 12 test files (see Test Suite section below)                                                                                                                     |
| Prometheus and Grafana | No     | Blueprint requires Celery queue depth and worker RAM monitoring -- no monitoring infrastructure exists                                                           |
| Upload benchmarking    | Done   | `scripts/benchmark-upload-capacity.sh`: network bandwidth (iperf3/curl), NFS disk I/O (dd/fio), non-disruptive                                                  |
| `.gitignore`           | Done   | Blocks `.fastq.gz`, `.bam`, `.cram`, `.sam`, `.h5ad`, `.ht2`, `.RData`                                                                                           |

### 8.3 Docker and Deployment

| Feature                  | Status | Notes                                                                                                                         |
| ------------------------ | ------ | ----------------------------------------------------------------------------------------------------------------------------- |
| Dockerfile               | Done   | Multi-stage: Miniconda base, conda env, pip deps, app code, collectstatic, EXPOSE 8000                                        |
| `docker-compose.yml`     | Done   | 5 services: web (Daphne), worker (Celery, 32 GB RAM limit), beat, redis (7-alpine), tusd (v2 resumable uploads); shared `media-data` volume; NFS mount flags documented; health checks |
| `docker-compose.dev.yml` | Done   | Override with live code mount, reduced concurrency (2), debug log level                                                       |
| `deploy.sh`              | Done   | .env check, pip install, migrate, collectstatic, Nginx symlink, systemd reload                                                |
| systemd services         | Done   | `rnaseek-web.service`, `rnaseek-celery-cpu.service` (CPU-bound, `cpu_bound` queue, concurrency=5), `rnaseek-celery-ram.service` (RAM-bound, `ram_bound` queue, concurrency=4, MemoryHigh=88G, MemoryMax=96G), `rnaseek-beat.service`, `rnaseek-tusd.service`. Legacy `rnaseek-worker.service` replaced. |
| Nginx config             | Done   | HTTPS (Let's Encrypt), WebSocket upgrade, Tus `/files/` proxy to tusd (keepalive 64 upstream pool, streaming, `Upload-Concat` header), `client_max_body_size 0` (unlimited), `sendfile on; tcp_nopush on; tcp_nodelay on`, 30-day static cache |
| OS & Network Tuning      | Done   | `scripts/tune-production-os.sh`: TCP BBR congestion control, 128 MB socket buffers, `vm.overcommit_memory=1`, `vm.swappiness=10`, dirty page tuning, 2M file descriptors. Production-only (refuses to run on <16-core machines). |

---

## Frontend

### Pages and Templates

| Page                  | Lines | Status | Notes                                                                                                                                                                                                                                                                                                                                                                                                                                                                                    |
| --------------------- | ----- | ------ | ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `base.html`           | 93    | Done   | Navbar (logo, nav links, 3-step workflow indicator: Upload, Processing, Core Hub, session badge), footer (14-day retention notice), CSRF meta tag, font/icon imports                                                                                                                                                                                                                                                                                                                     |
| `home.html`           | 249   | Done   | Hero slideshow (3 images, static dots -- auto-rotate JS not implemented), 4 action buttons, "How It Works" 4-card flow, capabilities grid, 12-module grid, reference genomes table                                                                                                                                                                                                                                                                                                       |
| `tutorials.html`      | 1277  | Done   | File format guide, metadata mapping example, pipeline workflow text flow, reference genomes table                                                                                                                                                                                                                                                                                                                                                                                        |
| `new_submission.html` | 966   | Done   | 5-step wizard: submission name, entry point + assay type + library type + file upload, genome selection (11 built-in + custom), metadata (CSV upload or manual builder, column mapping, contrast builder), statistical parameters + review + submit                                                                                                                                                                                                                                      |
| `processing.html`     | 433   | Done   | Dynamic step cards from `pipeline_steps` context, progress bar, WebSocket connection with HTTP polling fallback, error overlay with pattern-matched diagnostic hints, success redirect to Core Hub                                                                                                                                                                                                                                                                                       |
| `core_hub.html`       | 716   | Done   | 3-tab layout (Overview, Modules, Single-Cell): Tab 1 has Plotly visualization theater (5 plots with pill navigation) + 5 download buttons + interactive figure export. Tab 2 has master-detail split pane with 12 module cards (Alt Splicing conditional on `has_bam_files`) and dynamic detail pane. Tab 3 has deconvolution gateway (atlas selector, hi-res toggle, run button) + 3 locked advanced spoke cards. Module job history loaded from server context via `data-module-jobs`. |
| `workspaces.html`     | 146   | Done   | Jobs table with real data (submission name, module, status badges, action links), dev mode badge, 14-day retention warning, empty state                                                                                                                                                                                                                                                                                                                                                  |
| `advanced.html`       | --    | Done   | Removed. Standalone Tier 4 spoke page deleted. Spoke cards now live exclusively in the Core Hub Single-Cell tab.                                                                                                                                                                                                                                                                                                                                                                         |

### JavaScript

| File                | Lines | Status | Notes                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                               |
| ------------------- | ----- | ------ | --------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `pipeline_setup.js` | ~2700 | Done   | 5-step wizard navigation, entry point selector, assay type selector (4 tracks), library type toggle, FASTQ/BAM/Matrix drop zones, chunked upload with retry (25 MB chunks, 6 concurrent workers, 3 attempts, 5-min timeout), paired-end validation, custom genome FASTA/GTF upload, CSV metadata upload with PapaParse (auto-strips FASTQ extensions from sample names), manual metadata builder, column mapping, contrast builder, per-step validation, background upload orchestration with modal, toast notifications, body-cloned tooltip positioning, defensive validation hardening (min sample counts, duplicate genes, missing values, contrast validity, name sanitization, matrix-metadata match)                |
| `core_hub.js`       | ~1773 | Done   | Tab switching (Overview/Modules/Single-Cell), Plotly resize on tab change, Module Hub state machine (empty/history/form/result), 12 module-specific form builders with D&D dropzones and tabbed data input (Upload CSV/Example/Manual Builder), splicing mode toggle (manual vs CSV with downloadable BAM-prefilled template), history list with status badges, result view with Plotly rendering + download, module submission + polling with badge updates, deconvolution gateway + polling + spoke unlock, 5 Plotly visualization rendering functions (PCA, UMAP, Volcano, MA, Heatmap with dynamic height), download interactive figure as standalone HTML, download links, toast notifications |

### CSS

| File                    | Lines | Status | Notes                                                                                                                                                                                                                                                                                                                                                                                                                            |
| ----------------------- | ----- | ------ | -------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `variables.css`         | 54    | Done   | Design token system (navy-teal-mint palette, gradients, typography, border radii, shadows)                                                                                                                                                                                                                                                                                                                                       |
| `global.css`            | 2418  | Done   | Component library: navbar, footer, buttons, cards, module cards (with category color icons), grids, forms, badges, progress bars, tab bar, module status badges (running/done/failed), master-detail layout, detail pane (header/body/empty states), history entries, form sections/footer, dropzones, upload progress, data input tabs, builder tables, example tables, result sections, responsive utilities, toast animations |
| `submission_layers.css` | 2020  | Done   | 5-step wizard split-screen layout (1/3 + 2/3), step indicators with progress bar, radio cards, library-type cards, drop zones with file pills, file management panel, metadata table, CSV viewer, manual builder, column mapping, contrast builder, validation messages, threshold fields, glowing submit button, upload modal, toast animations, wizard-panel zoom (0.8), body-cloned tooltip styles, custom genome section     |

---

## Real-Time Progress (WebSocket)

| Feature                    | Status | Notes                                                                                                                                        |
| -------------------------- | ------ | -------------------------------------------------------------------------------------------------------------------------------------------- |
| `PipelineProgressConsumer` | Done   | `AsyncWebsocketConsumer` at `ws/pipeline/<job_id>/`; validates session ownership; joins channel group; broadcasts `pipeline_progress` events |
| ASGI routing               | Done   | `config/asgi.py` configures `ProtocolTypeRouter` with `AuthMiddlewareStack` + `URLRouter`                                                    |
| Channel layer (Redis)      | Done   | `CHANNEL_LAYERS` configured with Redis backend                                                                                               |
| Task-side emission         | Done   | `_emit_progress()` in `_helpers.py` sends progress updates via Channels layer                                                                |
| Frontend connection        | Done   | `processing.html` opens WebSocket with reconnect logic (2s x retry count backoff); falls back to HTTP polling if WebSocket fails             |

---

## Reference Genomes

| Genome               | Assembly               | Status |
| -------------------- | ---------------------- | ------ |
| Human                | GRCh38 (hg38)          | Done   |
| Mouse                | GRCm39 (mm39)          | Done   |
| Mouse                | GRCm38 (mm10)          | Done   |
| Rat                  | rn7                    | Done   |
| Zebrafish            | GRCz11 (danRer11)      | Done   |
| Chicken              | GRCg6a (galGal6)       | Done   |
| Pig                  | Sscrofa11.1 (susScr11) | Done   |
| Drosophila           | dm6                    | Done   |
| C. elegans           | WBcel235 (wbcel235)    | Done   |
| Yeast                | sacCer3 (R64-1-1)      | Done   |
| Arabidopsis          | TAIR10 (araTha)        | Done   |
| Custom genome upload | --                     | Done   |

All 11 genomes have HISAT2 indices (`.ht2`), FASTA, and GTF annotation files. Pre-built index scripts exist for BWA (`build_bwa_indices.sh`), Bismark (`build_bismark_indices.sh`), and miRBase/Bowtie (`build_mirbase_indices.sh`). Custom genome supports FASTA + GTF/GFF upload with on-demand HISAT2 index build. Pre-built genomes do not build indices at runtime.

---

## Test Suite

| File                        | Tests | Coverage Area                                                                                                                                                                                                                                                                                                      |
| --------------------------- | ----- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------ |
| `test_entry_points.py`      | 32    | Model fields, view validation (per entry type), upload routing, task dispatch, genome resolution, matrix validation                                                                                                                                                                                                |
| `test_stage2.py`            | 26    | DESeq2 formula builder, gene filtering, outlier detection, sample alignment, single/multi-contrast, covariates, full-rank error                                                                                                                                                                                    |
| `test_assay_tracks.py`      | ~40   | Assay model fields, file roles, pipeline step definitions, task dispatch routing (all 4 tracks), shared helpers (FastQC, Trim, MultiQC, sort_and_index_bam), ChIP-seq sample splitting, miRNA alignment flags                                                                                                      |
| `test_wgcna.py`             | 24    | Heatmap structure, module-trait correlation, hub gene extraction, enrichment dotplot, WGCNA dispatch, failure handling, trait matrix encoding                                                                                                                                                                      |
| `test_upload_api.py`        | 53    | Upload API: chunked upload, file roles, path sanitization, out-of-order chunk merging, buffer cleanup, multi-chunk concatenation, session isolation, FASTA extension validation                                                                                                                                    |
| `test_hisat2_pipeline.py`   | --    | HISAT2 alignment integration                                                                                                                                                                                                                                                                                       |
| `test_dev_dataset.py`       | --    | Dev dataset validation                                                                                                                                                                                                                                                                                             |
| `test_e2e.py`               | --    | Full E2E: synthetic yeast FASTQ to Stage 1 to Stage 2 (used in CI)                                                                                                                                                                                                                                                 |
| `test_genome_indices.py`    | --    | Genome index resolution and pre-built index verification                                                                                                                                                                                                                                                           |
| `test_validators.py`        | 52    | All 10 backend core validators: base fields, uploaded files, custom genome, small RNA, paired-end matching, metadata, ChIP-seq, batch column, matrix content, bacterial FASTA; min sample counts, contrast validation, name sanitization, FASTA header check, matrix-metadata match, duplicate genes, missing data |
| `test_stage2_edge_cases.py` | --    | Stage 2 edge cases: numeric covariates, rank-deficient designs, contrast level validation                                                                                                                                                                                                                          |
| `test_tusd_hooks.py`        | 21    | TusdHookView: post-finish file creation, file move to submission dir, sidecar cleanup, file_role handling (RAW_FASTQ, METADATA_CSV, ALIGNMENT_BAM), session validation, error cases (invalid JSON, missing fields, file not on disk), unknown role fallback, tusd v2 body-based type detection, cross-filesystem fallback |

---

## Summary by Blueprint Phase

| Phase | Description                                              | Status  | Completion                                                                                                                |
| ----- | -------------------------------------------------------- | ------- | ------------------------------------------------------------------------------------------------------------------------- |
| 1     | Infrastructure (Django, Redis, Celery, Nginx, NFS, Auth) | Done    | 100%                                                                                                                      |
| 2     | Data Model (ORM)                                         | Done    | 100%                                                                                                                      |
| 3     | API Facade and Data Ingestion                            | Done    | 100%                                                                                                                      |
| 4     | Hub Engine -- Stage 1 Multi-Track                        | Done    | 100% -- All 4 tracks + 3 entry points                                                                                     |
| 5     | Convergence and Normalization (Stage 2)                  | Done    | 100%                                                                                                                      |
| 6     | Standard Analytical Spokes (Tier 2)                      | Partial | ~33% -- Alt Splicing, WGCNA, RNA Editing, Time Series done (4/12). Frontend UI complete for all 12. 8 backends are stubs. |
| 7     | Predictive Single-Cell and Spatial (Tier 3 and 4)        | Partial | ~10% -- Frontend UI done. Zero backend implementation. Key deps commented out.                                            |
| 8     | DevOps and Security                                      | Partial | ~85% -- Janitor, CI/CD, Docker, deploy all done. Prometheus/Grafana missing.                                              |
