# RNAseek — Project Progress

> **Blueprint version:** 1.2 (Target: March 31, 2026)  
> **Audit date:** March 23, 2026  

---

## Legend

| Icon | Meaning                                                           |
| ---- | ----------------------------------------------------------------- |
| ✅    | **Finished** — Frontend and backend fully implemented             |
| 🚧    | **Partially Finished** — Details below on what is done vs missing |
| ❌    | **Not Started** — No implementation exists                        |

---

## Phase 1: Infrastructure Architecture

### 1.1 Application Layer

| Feature                      | Status | Notes                                                                                                          |
| ---------------------------- | ------ | -------------------------------------------------------------------------------------------------------------- |
| **Django 5.2 (Daphne ASGI)** | ✅      | Served via Daphne; supports HTTP and WebSocket via Django Channels                                             |
| **Redis 7+ Message Broker**  | ✅      | Configured in `settings.py` as both Celery broker and Channels layer backend                                   |
| **Celery 5.6 Worker Fleet**  | ✅      | Prefork workers, CPU-matched concurrency, task auto-discovery enabled                                          |
| **Nginx Reverse Proxy**      | ✅      | `nginx/rnaseek.conf`: HTTPS (Let's Encrypt), WebSocket `/ws/` upgrade, 10 GB upload limit, 30-day static cache |

### 1.2 Storage Layer (POSIX Shared NFS)

| Feature                         | Status | Notes                                                                                                                                      |
| ------------------------------- | ------ | ------------------------------------------------------------------------------------------------------------------------------------------ |
| **Shared `/app/media/` mount**  | ✅      | `docker-compose.yml` defines a `media-data` volume mounted on web, worker, and beat containers; NFS-ready with commented swap instructions |
| **Reference genome file store** | ✅      | 11 pre-indexed genomes under `pipeline/reference_genomes/`                                                                                 |

### 1.3 Security Layer (Anonymous Sessions)

| Feature                    | Status | Notes                                                                                                                                                   |
| -------------------------- | ------ | ------------------------------------------------------------------------------------------------------------------------------------------------------- |
| **UUID cookie middleware** | ✅      | `AnonymousSessionMiddleware`: `HttpOnly`, `SameSite=Lax`, 14-day max-age. Creates/validates `Session` model per request, attaches `request.session_obj` |
| **Tenant isolation**       | ✅      | Every DB row (FileAsset, AnalysisJob, AnalysisSubmission) scoped to Session via FK                                                                      |

---

## Phase 2: Data Model (Django ORM)

| Model                    | Status | Notes                                                                                                                                                                                                                                                                  |
| ------------------------ | ------ | ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| **`Session`**            | ✅      | UUID PK, `created_at`, `expires_at` (14-day TTL), `is_expired` property                                                                                                                                                                                                |
| **`AnalysisSubmission`** | ✅      | UUID PK, FK→Session, `input_data_type` (fastq/alignment/matrix), `assay_type` (standard_rna/small_rna/chip_seq/methylation), `submission_name`, `library_type`, `strandedness`, `reference_genome`, `metadata_payload` (JSON), threshold fields, `upload_dir` property |
| **`FileAsset`**          | ✅      | UUID PK, FK→Session, FK→Submission (nullable), 16 `file_role` choices, `local_path`, `is_user_uploaded`                                                                                                                                                                |
| **`AnalysisJob`**        | ✅      | UUID PK (= Celery task ID), FK→Session, FK→Submission (`parent_submission`, nullable), `is_core_pipeline` boolean, `module_name`, `status`, `result_payload` (JSON), `step_progress` (JSON), timestamps                                                                |
| **`ModuleResult`**       | ✅      | **Removed.** Module outputs stored in `AnalysisJob.result_payload`. Model deleted in migration `0010_remove_moduleresult`.                                                                                                                                             |
| **`ReferenceGenome`**    | ✅      | **Not needed.** Genomes resolved via filesystem lookup in `_genome.py` using `_GENOME_FOLDER_MAP` dict. Pre-built indices for all tools reside under `pipeline/reference_genomes/`.                                                                                    |

---

## Phase 3: API Facade & Data Ingestion

### 3.1 Chunked Uploader

| Feature                      | Status | Notes                                                                                                                                                                                |
| ---------------------------- | ------ | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------ |
| **`POST /api/upload/chunk`** | ✅      | 5 MB chunks, path-traversal sanitization, `ab` append mode, role-aware subdirectory routing (raw/, aligned/, counts/, metadata/, custom_genome/), `FileAsset` created on final chunk |
| **Frontend chunked upload**  | ✅      | `pipeline_setup.js`: drop zones for FASTQ, BAM, Matrix, custom genome files; progress tracking, retry logic (3 attempts), abort support                                              |

### 3.2 Master Router

| Feature                                                     | Status | Notes                                                                                                                                                 |
| ----------------------------------------------------------- | ------ | ----------------------------------------------------------------------------------------------------------------------------------------------------- |
| **`POST /api/pipeline/core`**                               | ✅      | Facade pattern: validates `input_data_type`, `assay_type`, files, metadata, genome; sets `pipeline_steps`; dispatches `run_core_pipeline` Celery task |
| **State routing (fastq/alignment/matrix)**                  | ✅      | Full validation per entry type; conditional field requirements (e.g., matrix skips genome)                                                            |
| **Assay routing (standard/small_rna/chip_seq/methylation)** | ✅      | Each assay generates correct `pipeline_steps` list; only applicable for FASTQ entry point                                                             |

### 3.3 Other API Endpoints

| Endpoint                                              | Status | Notes                                                                                                                                              |
| ----------------------------------------------------- | ------ | -------------------------------------------------------------------------------------------------------------------------------------------------- |
| **`POST /api/submission/create`**                     | ✅      | Creates `AnalysisSubmission`, returns UUID                                                                                                         |
| **`POST /api/submission/delete`**                     | ✅      | Cascade-deletes submission + disk files; `sendBeacon`-safe                                                                                         |
| **`GET /api/jobs/<uuid>/`**                           | ✅      | Polls job status; detects stale RUNNING jobs via Celery `AsyncResult`                                                                              |
| **`GET /api/session/assets`**                         | ✅      | Lists session file assets; filterable by `role` and `job_id`                                                                                       |
| **`GET /api/download/<uuid>`**                        | ✅      | Serves files with path-traversal protection and MIME detection                                                                                     |
| **`DELETE /api/files/<uuid>/`**                       | ✅      | Deletes user-uploaded asset from DB and disk                                                                                                       |
| **`POST /api/submissions/<uuid>/modules/<name>/run`** | 🚧      | **API routing and validation complete for all 12 modules.** Only WGCNA dispatches to a real backend engine; 11 others return placeholder payloads. |

---

## Phase 4: Hub Engine — Stage 1 Multi-Track

### Track A: Standard Transcriptomics (Poly-A RNA-Seq)

| Step                             | Status | Notes                                                                                                                       |
| -------------------------------- | ------ | --------------------------------------------------------------------------------------------------------------------------- |
| **FastQC**                       | ✅      | Parallelized (`-t {CPU_COUNT}`), shared helper `_run_fastqc_step()`                                                         |
| **Trimmomatic**                  | ✅      | Parallel per-sample via `ThreadPoolExecutor`; single-end and paired-end modes                                               |
| **HISAT2 alignment**             | ✅      | Splice-aware; `_TOOL_THREADS` per sample, parallel via `ThreadPoolExecutor`; SAM→BAM (featureCounts requires BAM, not CRAM) |
| **Custom genome HISAT2 build**   | ✅      | Tracked as separate `hisat2_build` pipeline step; warning banner in UI                                                      |
| **featureCounts quantification** | ✅      | Gene-level and transcript-level; GFF/GFF3 auto-detection (`_detect_gff_gene_attr`); CSV conversion                          |
| **MultiQC report**               | ✅      | Interactive HTML saved to submission hub                                                                                    |
| **Stage 2 handoff**              | ✅      | Calls `run_stage2_stats()` after quantification                                                                             |
| **Frontend (processing page)**   | ✅      | All steps tracked with icons (idle/running/done/failed), progress bar, error overlay with diagnostic hints                  |

### Track B: Regulatory Transcriptomics (Small RNA / miRNA)

| Step                                 | Status | Notes                                                                                               |
| ------------------------------------ | ------ | --------------------------------------------------------------------------------------------------- |
| **Bowtie alignment against miRBase** | ✅      | `_run_bowtie_mirna()` with species-specific miRBase index resolution                                |
| **miRNA quantification**             | ✅      | `_mirna_counts_from_bams()` via `samtools idxstats`                                                 |
| **Route orchestrator**               | ✅      | `_route_small_rna()`: FastQC → Trim (MINLEN:18) → Bowtie → quantify → MultiQC → Stage 2             |
| **Frontend**                         | ✅      | Small RNA pipeline steps rendered in processing page; assay type selector card in submission wizard |

### Track C: Epigenomics — ChIP-seq

| Step                               | Status | Notes                                                                                |
| ---------------------------------- | ------ | ------------------------------------------------------------------------------------ |
| **BWA MEM alignment**              | ✅      | `_run_bwa_align()` with BWA index resolution                                         |
| **MACS2 peak calling**             | ✅      | `_run_macs2_callpeak()` with genome-size lookup (`_MACS2_GENOME_SIZE`)               |
| **Consensus peak SAF**             | ✅      | `_build_consensus_saf()` via `bedtools merge` → SAF format                           |
| **featureCounts on peaks**         | ✅      | Treatment BAMs only; `-F SAF` flag                                                   |
| **Input/Control sample splitting** | ✅      | `_split_chip_samples()` from metadata                                                |
| **Route orchestrator**             | ✅      | `_route_chip_seq()`: FastQC → Trim → BWA → MACS2 → featureCounts → MultiQC → Stage 2 |
| **Frontend**                       | ✅      | ChIP-seq pipeline steps rendered; assay type card with metadata guidance             |

### Track C: Epigenomics — DNA Methylation

| Step                                     | Status | Notes                                                                                                                            |
| ---------------------------------------- | ------ | -------------------------------------------------------------------------------------------------------------------------------- |
| **Bismark genome preparation**           | ✅      | `_resolve_bismark_genome()`                                                                                                      |
| **Bismark alignment**                    | ✅      | `_run_bismark_align()`                                                                                                           |
| **Methylation extraction**               | ✅      | `_run_bismark_extract()`                                                                                                         |
| **Differential methylation (methylKit)** | ✅      | `_methylkit.py` R bridge: `methRead` → filter → normalize → unite → `calculateDiffMeth` → CSV export; PCA, volcano, MA plot data |
| **Route orchestrator**                   | ✅      | `_route_methylation()`: FastQC → Trim → Bismark prep → align → extract → MultiQC → methylKit                                     |
| **Frontend**                             | ✅      | Methylation pipeline steps rendered; assay type card                                                                             |

### Entry Points B & C (Non-FASTQ)

| Feature                             | Status | Notes                                                                                                             |
| ----------------------------------- | ------ | ----------------------------------------------------------------------------------------------------------------- |
| **Route B: Alignment (BAM/CRAM)**   | ✅      | `_route_alignment()`: CRAM→BAM conversion (parallel) → BAM indexing → featureCounts → Stage 2                     |
| **Route C: Count Matrix (CSV/TSV)** | ✅      | `_route_matrix()`: CSV/TSV loading → validation (non-empty, all-numeric, non-negative) → canonical copy → Stage 2 |
| **Frontend entry point selector**   | ✅      | 3 radio cards; conditional UI show/hide; matrix mode skips genome step in wizard                                  |

---

## Phase 5: Convergence & Normalization (Stage 2)

| Feature                                | Status | Notes                                                                                                                                                                |
| -------------------------------------- | ------ | -------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| **Gene filtering**                     | ✅      | `_filter_low_counts()` with `min_total=10` threshold                                                                                                                 |
| **Batch Correction (ComBat-seq)**      | ✅      | `_combat_seq()` via `sva::ComBat_seq()` through rpy2; conditional on batch column in metadata                                                                        |
| **DESeq2 normalization & DGE**         | ✅      | `_run_deseq2()`: dynamic formula construction, multi-contrast extraction, BiocParallel `MulticoreParam` for parallel dispersion estimation                           |
| **Mahalanobis outlier detection**      | ✅      | `_detect_outliers()`: PCA-based Mahalanobis distance                                                                                                                 |
| **Automated annotation (MyGene.info)** | ✅      | `annotate_deg_table()`: batched REST API queries, exponential backoff retry, appends gene descriptions and disease associations to DEG table                         |
| **Plotly UX generation**               | ✅      | `_generate_plot_data()`: PCA (sklearn, variance explained), UMAP (umap-learn), Volcano (log2FC vs -log10 padj), MA (baseMean vs log2FC), Heatmap (z-score, top DEGs) |
| **Frontend (Core Hub)**                | ✅      | All 5 plots rendered via Plotly.js 2.35.2 with custom color scheme, hover templates, responsive layout                                                               |
| **Downloads**                          | ✅      | 5 download items: aligned BAMs, raw counts, normalized counts, DEG table, MultiQC report — served via `FileDownloadView`                                             |

### Stats Package Source Files

| File               | Purpose                                                                                               |
| ------------------ | ----------------------------------------------------------------------------------------------------- |
| `core.py`          | `run_stage2_stats()` — main Stage 2 driver; orchestrates all steps below                              |
| `_helpers.py`      | `_load_metadata()`, `_align_samples()`, `_filter_low_counts()`, `_combat_seq()`, `_detect_outliers()` |
| `_deseq2.py`       | DESeq2 R bridge: formula builder, factor sanitisation, multi-contrast extraction                      |
| `_annotations.py`  | `annotate_deg_table()` — MyGene.info REST API batched queries                                         |
| `_methylkit.py`    | `run_differential_methylation()` — methylKit R bridge for bisulfite data                              |
| `_plots.py`        | `_generate_plot_data()` — PCA, UMAP, Volcano, MA, Heatmap JSON serialisation for Plotly.js            |
| `_plots_wgcna.py`  | `build_module_trait_heatmap()`, `build_pathway_dotplot()` — WGCNA-specific Plotly helpers             |
| `_r_bridge.py`     | Shared lazy rpy2 initialisation; provides `ro`, `localconverter`, `importr`, `_converter`, `_R_CORES` |

---

## Phase 6: Standard Analytical Spokes (Tier 2 Modules)

| #   | Module                   | Blueprint Engine        | Status | Details                                                                                                                                                                                                                 |
| --- | ------------------------ | ----------------------- | ------ | ----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| A   | **Alt Splicing**         | `IsoformSwitchAnalyzeR` | ❌      | API endpoint registered, frontend card + modal exist, backend returns placeholder                                                                                                                                       |
| B   | **RNA Editing**          | `REDItools2`            | ❌      | API endpoint registered, frontend card + modal exist, backend returns placeholder                                                                                                                                       |
| C   | **Time Series**          | `ImpulseDE2`            | ❌      | API endpoint registered, frontend card + modal exist, backend returns placeholder                                                                                                                                       |
| D   | **WGCNA**                | `PyWGCNA`               | ✅      | `_module_wgcna.py`: 6-step pipeline (load → find modules → module-trait correlation → hub genes → Enrichr enrichment → plots). Stats helpers in `_plots_wgcna.py`. 24 unit tests. Frontend modal with soft-power input. |
| E   | **Pathways**             | `gseapy`                | ❌      | API endpoint registered, frontend card + modal exist (gene-set selector), backend returns placeholder                                                                                                                   |
| F   | **Causal Networks**      | `arboreto` (GRNBoost2)  | ❌      | API endpoint registered, frontend card + modal exist, backend returns placeholder                                                                                                                                       |
| G   | **Protein Interactions** | STRING-DB API           | ❌      | API endpoint registered, frontend card + modal exist, backend returns placeholder                                                                                                                                       |
| H   | **Literature NLP**       | INDRA Bio API           | ❌      | API endpoint registered, frontend card + modal exist, backend returns placeholder                                                                                                                                       |
| I   | **Survival**             | `lifelines`             | ❌      | API endpoint registered, frontend card + modal exist (survival data params), backend returns placeholder                                                                                                                |
| J   | **TCGA Cancer**          | `TCGAbiolinks`          | ❌      | API endpoint registered, frontend card + modal exist, backend returns placeholder                                                                                                                                       |
| K   | **Biomarkers**           | MarkerDB API            | ❌      | API endpoint registered, frontend card + modal exist, backend returns placeholder                                                                                                                                       |
| L   | **MOFA / DIABLO**        | `mofapy2` / `mixOmics`  | ❌      | API endpoint registered, frontend card + modal exist (MOFA factors, DIABLO components), backend returns placeholder. `mofapy2` commented out in `requirements.txt`.                                                     |

**Summary:** 1 of 12 modules fully implemented (WGCNA). 11 modules have complete frontend UI (cards, modals, module-specific inputs) and API endpoint routing, but return stub payloads from the Celery task layer.

### Hub Isolation Principle

| Feature                           | Status | Notes                                                                                                                                                                      |
| --------------------------------- | ------ | -------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| **`is_core_pipeline` flag**       | ✅      | `AnalysisJob` has `is_core_pipeline` boolean (default `True`); module jobs created with `False`                                                                            |
| **`ModuleResult` storage**        | ✅      | `ModuleResult` model removed. Module outputs stored directly in `AnalysisJob.result_payload`                                                                               |
| **WebSocket broadcast isolation** | 🚧      | Consumer (`PipelineProgressConsumer`) broadcasts to `pipeline_{job_id}` channel group but does **not** distinguish core vs hub broadcasts based on `is_core_pipeline` flag |

---

## Phase 7: Predictive Single-Cell & Spatial Gateway (Tier 3 & 4)

### Tier 3: Deconvolution Engine

| Feature                         | Status | Notes                                                                                                                                                  |
| ------------------------------- | ------ | ------------------------------------------------------------------------------------------------------------------------------------------------------ |
| **DestVI / BayesPrism backend** | ❌      | No implementation. `scvi-tools` commented out in `requirements.txt`.                                                                                   |
| **`.h5ad` AnnData output**      | ❌      | `H5AD_PSEUDO` file role defined in `FileAsset` model but no code generates `.h5ad` files                                                               |
| **Frontend gateway UI**         | ✅      | Atlas selector dropdown (4 atlases), high-resolution toggle, "Run Deconvolution" button, completion polling, spoke unlock logic — all in `core_hub.js` |

### Tier 4: Advanced Spatial Spokes

| Feature                               | Status | Notes                                                                                                                                                  |
| ------------------------------------- | ------ | ------------------------------------------------------------------------------------------------------------------------------------------------------ |
| **Trajectory Inference (scanpy)**     | ❌      | No backend. `scanpy` installed but unused for trajectory/PAGA. Locked spoke card in Core Hub Single-Cell tab; standalone `advanced.html` page removed. |
| **Spatial Mapping (Tangram)**         | ❌      | No backend. `tangram-sc` commented out in `requirements.txt`. Locked spoke card in Core Hub Single-Cell tab; standalone `advanced.html` page removed.  |
| **Spatial Autocorrelation (Squidpy)** | ❌      | No backend. `squidpy` commented out in `requirements.txt`. Locked spoke card in Core Hub Single-Cell tab; standalone `advanced.html` page removed.     |

---

## Phase 8: DevOps, Janitorial & Server Security

### 8.1 Auto-Purge Janitor

| Feature                           | Status | Notes                                                                                       |
| --------------------------------- | ------ | ------------------------------------------------------------------------------------------- |
| **Celery Beat schedule**          | ✅      | `purge-expired-sessions` crontab at 2:00 AM UTC in `config/celery.py`                       |
| **`purge_expired_sessions` task** | ✅      | Queries expired `Session` rows → `shutil.rmtree()` on NFS directory → Django cascade delete |
| **Management command**            | ✅      | `python manage.py purge_expired [--dry-run]` with `--dry-run` flag and audit logging        |

### 8.2 CI/CD & Observability

| Feature                  | Status | Notes                                                                                                                                                                                                                                                         |
| ------------------------ | ------ | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| **GitHub Actions E2E**   | ✅      | `.github/workflows/e2e.yml`: Miniconda setup, bioinformatics tool verification, yeast HISAT2 index build, migrations, `test_e2e.py` on synthetic sacCer3 dataset                                                                                              |
| **Test suite**           | ✅      | 9 test files: `test_entry_points.py` (32 tests), `test_stage2.py` (26 tests), `test_assay_tracks.py` (~40 tests), `test_wgcna.py` (24 tests), `test_genome_indices.py`, `test_upload_api.py`, `test_hisat2_pipeline.py`, `test_dev_dataset.py`, `test_e2e.py` |
| **Prometheus & Grafana** | ❌      | Blueprint requires Celery queue depth and worker RAM monitoring — no monitoring infrastructure exists                                                                                                                                                         |
| **`.gitignore`**         | ✅      | Blocks `.fastq.gz`, `.bam`, `.cram`, `.sam`, `.h5ad`, `.ht2`, `.RData` to prevent repo bloat                                                                                                                                                                  |

### 8.3 Docker & Deployment

| Feature                      | Status | Notes                                                                                                            |
| ---------------------------- | ------ | ---------------------------------------------------------------------------------------------------------------- |
| **Dockerfile**               | ✅      | Production container                                                                                             |
| **`docker-compose.yml`**     | ✅      | 4 services: web (Daphne), worker (Celery, 32 GB RAM limit), beat, redis; shared `media-data` volume              |
| **`docker-compose.dev.yml`** | ✅      | Override with live code mount, reduced concurrency (2), debug loglevel                                           |
| **`deploy.sh`**              | ✅      | Production deployment script: env file check, pip install, migrate, collectstatic, nginx symlink, systemd reload |
| **systemd services**         | ✅      | `rnaseek-web.service`, `rnaseek-worker.service`, `rnaseek-beat.service`                                          |
| **Nginx config**             | ✅      | HTTPS (Let's Encrypt), WebSocket upgrade, 10 GB uploads, static caching                                          |

---

## Frontend

### Pages & Templates

| Page                      | Status | Notes                                                                                                                                                                                                                                                                  |
| ------------------------- | ------ | ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| **`base.html`**           | ✅      | Navbar (logo, nav links, 3-step workflow indicator: Upload → Processing → Core Hub, session badge), footer (14-day retention notice), CSRF meta tag, font/icon imports                                                                                                 |
| **`home.html`**           | ✅      | Hero slideshow (3 images), 4 action buttons, "How It Works" 4-card flow, capabilities grid, 12-module grid, reference genomes table. Slideshow auto-rotate JS not implemented (static dots exist).                                                                     |
| **`tutorials.html`**      | ✅      | File format guide, metadata mapping example, pipeline workflow text flow, reference genomes table                                                                                                                                                                      |
| **`new_submission.html`** | ✅      | 5-step wizard: submission name → entry point + assay type → file upload (FASTQ/BAM/Matrix drop zones) → genome selection (11 built-in + custom) → metadata (CSV upload or manual builder, column mapping, contrast builder) → statistical parameters → review + submit |
| **`processing.html`**     | ✅      | Dynamic step cards from `pipeline_steps` context, progress bar, WebSocket connection with HTTP polling fallback, error overlay with pattern-matched diagnostic hints, success redirect to Core Hub                                                                     |
| **`core_hub.html`**       | ✅      | 3-tab layout (Overview                                                                                                                                                                                                                                                 | Modules | Single-Cell): Tab 1 has downloads + 5 Plotly plots, Tab 2 has 11 module cards with status badges (Running/Completed/Failed) and inline result panel, Tab 3 has deconvolution gateway + 3 locked advanced spoke cards. Module jobs loaded from server context. |
| **`workspaces.html`**     | ✅      | Jobs table with real data (submission name, module, status badges, action links), dev mode badge, 14-day retention warning, empty state                                                                                                                                |
| **`advanced.html`**       | ✅      | **Removed.** Standalone Tier 4 spoke page deleted. Spoke cards now live exclusively in the Core Hub Single-Cell tab as locked module cards that unlock after deconvolution.                                                                                            |

### JavaScript

| File                                  | Status | Notes                                                                                                                                                                                                                                                                                                                                                                     |
| ------------------------------------- | ------ | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| **`pipeline_setup.js`** (~2400 lines) | ✅      | 5-step wizard navigation, entry point selector, assay type selector, library type toggle, FASTQ/BAM/Matrix drop zones, chunked upload with retry, paired-end validation, CSV metadata upload with PapaParse, manual metadata builder, column mapping, contrast builder, per-step validation, toast notifications, body-cloned tooltip positioning (escapes overflow/zoom) |
| **`core_hub.js`** (~600 lines)        | ✅      | Tab switching (Overview/Modules/Single-Cell), Plotly resize on tab change, module status badge rendering from server JSON, module card click (completed shows result panel, others open config modal), 5 Plotly rendering functions, module submission + polling with badge updates, deconvolution gateway + polling + spoke unlock, download link handlers               |

### CSS

| File                                     | Status | Notes                                                                                                                                                                                             |
| ---------------------------------------- | ------ | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| **`variables.css`**                      | ✅      | Full design token system (navy-teal-mint palette, gradients, typography, border radii, shadows)                                                                                                   |
| **`global.css`** (~1500 lines)           | ✅      | Complete component library: navbar, footer, buttons, cards, module cards, grids, forms, badges, modals, progress bars, tab bar, module status badges (running/done/failed), responsive utilities  |
| **`submission_layers.css`** (~2000 lines) | ✅      | 5-step wizard layout, step indicators, radio cards, drop zones, file management panel, metadata table, validation messages, toast animations, wizard-panel zoom (0.8), body-cloned tooltip styles |

---

## Real-Time Progress (WebSocket)

| Feature                        | Status | Notes                                                                                                                                        |
| ------------------------------ | ------ | -------------------------------------------------------------------------------------------------------------------------------------------- |
| **`PipelineProgressConsumer`** | ✅      | `AsyncWebsocketConsumer` at `ws/pipeline/<job_id>/`; validates session ownership; joins channel group; broadcasts `pipeline_progress` events |
| **ASGI routing**               | ✅      | `config/asgi.py` configures `ProtocolTypeRouter` with `AuthMiddlewareStack` + `URLRouter` for WebSocket                                      |
| **Channel layer (Redis)**      | ✅      | `CHANNEL_LAYERS` configured with Redis backend                                                                                               |
| **Task-side emission**         | ✅      | `_emit_progress()` in `_helpers.py` sends progress updates via Channels layer                                                                |
| **Frontend connection**        | ✅      | `processing.html` opens WebSocket with reconnect logic (2s × retry count backoff); falls back to HTTP polling if WS fails                    |

---

## Reference Genomes

| Genome               | Assembly               | Status |
| -------------------- | ---------------------- | ------ |
| Human                | GRCh38 (hg38)          | ✅      |
| Mouse                | GRCm39 (mm39)          | ✅      |
| Mouse                | GRCm38 (mm10)          | ✅      |
| Rat                  | rn7                    | ✅      |
| Zebrafish            | GRCz11 (danRer11)      | ✅      |
| Chicken              | GRCg6a (galGal6)       | ✅      |
| Pig                  | Sscrofa11.1 (susScr11) | ✅      |
| Drosophila           | dm6                    | ✅      |
| C. elegans           | WBcel235 (wbcel235)    | ✅      |
| Yeast                | sacCer3 (R64-1-1)      | ✅      |
| Arabidopsis          | TAIR10 (araTha)        | ✅      |
| Custom genome upload | —                      | ✅      |

All 11 genomes have HISAT2 indices (`.ht2`), FASTA, and GTF annotation files. Pre-built index scripts exist for BWA (`build_bwa_indices.sh`), Bismark (`build_bismark_indices.sh`), and miRBase/Bowtie (`build_mirbase_indices.sh`). Custom genome supports FASTA + GTF/GFF upload with on-demand index build (HISAT2, BWA, Bismark). Pre-built genomes do not build indices at runtime.

---

## Test Suite

| File                      | Tests | Coverage Area                                                                                                                                                                                                 |
| ------------------------- | ----- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `test_entry_points.py`    | 32    | Model fields, view validation (per entry type), upload routing, task dispatch, genome resolution, matrix validation                                                                                           |
| `test_stage2.py`          | 26    | DESeq2 formula builder, gene filtering, outlier detection, sample alignment, single/multi-contrast, covariates, full-rank error                                                                               |
| `test_assay_tracks.py`    | ~40   | Assay model fields, file roles, pipeline step definitions, task dispatch routing (all 4 tracks), shared helpers (FastQC, Trim, MultiQC, sort_and_index_bam), ChIP-seq sample splitting, miRNA alignment flags |
| `test_wgcna.py`           | 24    | Heatmap structure, module-trait correlation, hub gene extraction, enrichment dotplot, WGCNA dispatch, failure handling, trait matrix encoding                                                                 |
| `test_upload_api.py`      | —     | Upload API tests                                                                                                                                                                                              |
| `test_hisat2_pipeline.py` | —     | HISAT2 alignment integration                                                                                                                                                                                  |
| `test_dev_dataset.py`     | —     | Dev dataset validation                                                                                                                                                                                        |
| `test_e2e.py`             | —     | Full E2E: synthetic yeast FASTQ → Stage 1 → Stage 2 (used in CI)                                                                                                                                             |
| `test_genome_indices.py`  | —     | Genome index resolution and pre-built index verification                                                                                                                                                      |
---

## Summary by Blueprint Phase

| Phase | Description                                              | Status | Completion                                                                                                         |
| ----- | -------------------------------------------------------- | ------ | ------------------------------------------------------------------------------------------------------------------ |
| **1** | Infrastructure (Django, Redis, Celery, Nginx, NFS, Auth) | ✅      | 100%                                                                                                               |
| **2** | Data Model (ORM)                                         | ✅      | 100% — `ModuleResult` removed (outputs in `AnalysisJob.result_payload`), `ReferenceGenome` resolved via filesystem |
| **3** | API Facade & Data Ingestion                              | ✅      | 100%                                                                                                               |
| **4** | Hub Engine — Stage 1 Multi-Track                         | ✅      | 100% — All 4 tracks + 3 entry points implemented                                                                   |
| **5** | Convergence & Normalization (Stage 2)                    | ✅      | 100%                                                                                                               |
| **6** | Standard Analytical Spokes (Tier 2)                      | 🚧      | ~15% — WGCNA done (1/12). Frontend UI complete for all 12. 11 backends are stubs.                                  |
| **7** | Predictive Single-Cell & Spatial (Tier 3 & 4)            | 🚧      | ~10% — Frontend UI done. Zero backend implementation. Key deps commented out.                                      |
| **8** | DevOps & Security                                        | 🚧      | ~85% — Janitor, CI/CD, Docker, deploy all done. Prometheus/Grafana missing.                                        |
