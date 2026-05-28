# RNASeek Pipeline Blueprint

**Version:** 1.3 | **Architecture:** Multi-Tenant Asynchronous Microservices

---

## Phase 1: Infrastructure Architecture

### 1.1 Application Layer (Dockerized Microservices)

* **Web Server (Django 5.2):** Served by Daphne (ASGI) for synchronous REST API calls and real-time asynchronous WebSocket connections (Django Channels) for live progress bars. Nginx sits in front as a reverse proxy handling SSL (Let's Encrypt / Certbot), WebSocket upgrades at `/ws/`, Tus upload proxying at `/files/` (streaming, no request buffering), and 30-day static file caching.
* **Message Broker (Redis 7+):** Memory-based queue for Celery tasks and Channels layer backend.
* **Worker Fleet (Celery 5.6):** Prefork workers with CPU-matched concurrency. Isolated containers that strictly execute Python/R bioinformatics scripts. 32 GB RAM limit per worker container.
* **Scheduler (Celery Beat):** Single-replica container running periodic tasks (session purge at 2:00 AM UTC).
* **Upload Daemon (tusd v2):** Handles resumable file uploads via the Tus protocol. Writes directly to the shared NFS volume. On upload completion, sends a `post-finish` webhook to Django at `/api/tusd-hooks/` which registers the `FileAsset`.
* **Microbial Engine (BASys2 Docker):** An isolated local Docker container running the BASys2 pipeline and database. This processes unannotated bacterial FASTA uploads, rapidly generating complete structural, operon, and metabolome annotations (JSON/GenBank) in ~10 seconds for downstream alignment and counting without relying on external API calls.

### 1.2 Storage Layer (POSIX Shared NFS)

* A POSIX-compliant Network File System (NFS) mounted to `/app/media/` on every Docker container (web, worker, beat, tusd). The `docker-compose.yml` defines a `media-data` volume with documented NFS mount flags (async, noatime, rsize/wsize 1 MB, hard).
* The web server writes uploads to `/app/media/sessions/{uuid}/`. Celery workers read from the exact same path. Zero data movement.
* Reference genomes (11 pre-indexed) reside under `pipeline/reference_genomes/`.

### 1.3 Security Layer (Anonymous Sessions)

* No usernames or passwords. `AnonymousSessionMiddleware` issues a cryptographically random UUID cookie (`Session_ID`), `HttpOnly`, `SameSite=Lax`, with a 14-day TTL.
* The middleware creates or validates a `Session` model per request and attaches `request.session_obj`.
* Every database row (`FileAsset`, `AnalysisJob`, `AnalysisSubmission`) is scoped to this session via foreign key.

---

## Phase 2: Data Model (Django ORM)

1. **`Session`:** Root tenant. `session_id` (UUID PK), `created_at`, `expires_at` (14-day TTL), `is_expired` property.
2. **`AnalysisSubmission`:** Child of `Session`. UUID PK. Represents a primary Core Pipeline run. Contains `input_data_type` (fastq / alignment / matrix), `assay_type` (standard_rna / small_rna / chip_seq / methylation), `library_type` (single / paired), `strandedness` (unstranded / fr-firststrand / fr-secondstrand), `reference_genome`, `metadata_mode` (upload / manual), `metadata_payload` (JSON), threshold fields (`adjusted_pvalue`, `min_log2fc`, `max_log2fc`), `custom_genome_name`, and `submission_name`. The `upload_dir` property generates the NFS path. These are the only items rendered in the global Active Workspace.
3. **`FileAsset`:** UUID PK. FK to Session and FK to Submission (nullable). 15 `file_role` choices: `RAW_FASTQ`, `ALIGNMENT_BAM`, `USER_COUNT_MATRIX`, `COUNT_MATRIX`, `NORMALIZED_COUNTS`, `DEG_TABLE`, `MULTIQC_REPORT`, `H5AD_PSEUDO`, `HE_IMAGE_USER`, `HE_IMAGE_GENERIC`, `PEAK_FILE`, `METHYLATION_REPORT`, `CUSTOM_GENOME_FASTA`, `CUSTOM_GENOME_ANNOTATION`, `METADATA_CSV`, plus `is_user_uploaded` flag and `local_path`.
4. **`AnalysisJob`:** UUID PK (equals Celery task ID). FK to Session, FK to Submission (`parent_submission`, nullable). `is_core_pipeline` boolean (default `True`; Tier 2 module jobs set to `False`). `module_name` (string), `status` (PENDING / RUNNING / SUCCESS / FAILED), `result_payload` (JSON), `step_progress` (JSON), timestamps. Module outputs are stored directly in `result_payload` -- no separate `ModuleResult` model.
5. **Reference Genomes:** No DB model. Resolved via filesystem lookup (`_GENOME_FOLDER_MAP` dict in `_genome.py`). Pre-built indices for HISAT2, BWA, Bismark, and Bowtie/miRBase reside under `pipeline/reference_genomes/`.

---

## Phase 3: API Facade and Data Ingestion

### 3.1 Chunked Uploader (`POST /api/upload/chunk`)

Files are split into 25 MB binary slices client-side and uploaded with up to 6 concurrent chunks in flight per file. On the server, the Django API sanitizes filenames, then buffers each chunk to a fast local SSD directory (`/tmp/rnaseek_uploads/`) as individually named files (`chunk_0`, `chunk_1`, ...) using atomic write-then-rename. When all chunks for a file arrive (detected via directory listing), the server claims merge responsibility with a POSIX-atomic `O_EXCL` marker, stitches the chunks in order, performs a single `shutil.move()` to the final NFS path in the role-aware subdirectory (`raw/`, `aligned/`, `counts/`, `metadata/`, `custom_genome/`), creates the `FileAsset` row, and cleans up the temporary buffer directory.

### 3.2 Master Router (`POST /api/pipeline/core`)

Facade pattern. The frontend sends a single JSON payload. Django validates `input_data_type`, `assay_type`, file presence, metadata, genome selection, and contrast configuration. It sets `pipeline_steps` and dispatches `run_core_pipeline` as a Celery task, flagging the resulting `AnalysisJob` as `is_core_pipeline=True`.

**State routing:**
* `fastq` -- Full alignment pipeline. Requires paired-end validation. Routes by assay type (standard RNA / small RNA / ChIP-seq / methylation).
* `alignment` -- BAM/CRAM input. Skips alignment. Routes to featureCounts then Stage 2.
* `matrix` -- User count matrix. Bypasses Stage 1 entirely. Validates CSV (non-empty, all-numeric, non-negative).

**Assay routing (FASTQ only):**
* `standard_rna` -- HISAT2 alignment track.
* `small_rna` -- Bowtie/miRBase track. Genomes restricted to `MIRBASE_GENOMES` only; custom genome blocked.
* `chip_seq` -- BWA + MACS2 track.
* `methylation` -- Bismark track.

### 3.3 Other API Endpoints

| Endpoint                                     | Method | Purpose                                                                                                                                                            |
| :------------------------------------------- | :----- | :----------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `/api/submission/create`                     | POST   | Creates `AnalysisSubmission`, returns UUID                                                                                                                         |
| `/api/submission/delete`                     | POST   | Cascade-deletes submission + disk files; `sendBeacon`-safe (csrf_exempt)                                                                                           |
| `/api/jobs/<uuid>/`                          | GET    | Polls job status; detects stale RUNNING jobs via Celery `AsyncResult` (marks FAILED if REVOKED)                                                                    |
| `/api/session/assets`                        | GET    | Lists session file assets; filterable by `role` and `job_id`                                                                                                       |
| `/api/download/<uuid>`                       | GET    | Serves files with path-traversal protection and MIME detection                                                                                                     |
| `/api/files/<uuid>/`                         | DELETE | Deletes user-uploaded asset from DB and disk                                                                                                                       |
| `/api/submissions/<uuid>/modules/<name>/run` | POST   | Validates module name against 12 approved modules, verifies core job completed, creates `AnalysisJob` with `is_core_pipeline=False`, dispatches `run_tier2_module` |

---

## Phase 4: Hub Engine (Stage 1 Multi-Track)

Triggered by the Celery `run_core_pipeline` task. Progress updates are emitted via `_emit_progress()` through the Channels layer.

### Track A: Standard Transcriptomics (Poly-A RNA-Seq)

* **QC:** `FastQC` (parallelized with `-t CPU_COUNT`) and `Trimmomatic` (parallel per-sample via `ThreadPoolExecutor`; single-end and paired-end modes).
* **Alignment:** `HISAT2` (splice-aware). `_TOOL_THREADS` per sample, parallel via `ThreadPoolExecutor`. SAM converted to sorted/indexed BAM (featureCounts requires BAM, not CRAM).
* **Custom genome:** On-demand HISAT2 index build (`hisat2_build` pipeline step) from user-uploaded FASTA + GTF/GFF. Tracked as a separate step with a warning banner in the UI.
* **EuGlid Multi-Species:** For organisms beyond the 11 pre-built assemblies, EuGlid provides on-demand genome retrieval and HISAT2 index generation for 800+ eukaryotic species. The pipeline resolves the selected species against the EuGlid registry, fetches the reference assembly and annotation, builds indices, caches them for reuse, and proceeds with the standard alignment track — no user-uploaded FASTA required.
* **Quantification:** `featureCounts` at gene-level and transcript-level. Auto-detects GFF/GFF3 gene attribute via `_detect_gff_gene_attr`. Exports CSV.
* **QC Report:** `MultiQC` interactive HTML saved to the submission hub.
* **Handoff:** Calls `run_stage2_stats()` after quantification.

### Track B: Regulatory Transcriptomics (Small RNA / miRNA)

* **Route:** FastQC, Trimmomatic (MINLEN:18), Bowtie alignment against species-specific miRBase index, miRNA quantification via `samtools idxstats`, MultiQC, Stage 2 handoff.

### Track C: Epigenomics -- ChIP-seq

* **Route:** FastQC, Trimmomatic, BWA MEM alignment (with BWA index resolution), MACS2 peak calling (genome-size lookup via `_MACS2_GENOME_SIZE`), consensus peak SAF via `bedtools merge`, featureCounts on peaks (treatment BAMs only, `-F SAF` flag), MultiQC, Stage 2 handoff.
* **Feature:** Input/control sample splitting from metadata (`_split_chip_samples()`).

### Track C: Epigenomics -- DNA Methylation

* **Route:** FastQC, Trimmomatic, Bismark genome preparation, Bismark alignment, methylation extraction, MultiQC, differential methylation via methylKit R bridge (`methRead`, filter, normalize, unite, `calculateDiffMeth`, CSV export; PCA, volcano, MA plot data).

### Track D: Microbial / Bacterial Transcriptomics

* **Route:** FastQC, Trimmomatic (quality filtering).
* **On-Demand Annotation:** If a user uploads an unannotated bacterial FASTA, the pipeline dispatches it to the local BASys2 Docker container. BASys2 leverages its 10 integrated databases to generate complete genomic, operon, and whole-metabolome annotations.
* **Alignment & Quantification:** Bowtie2 or BWA alignment followed by `featureCounts` utilizing the BASys2-generated genomic coordinates.
* **QC Report:** MultiQC interactive HTML.
* **Handoff:** Calls `run_stage2_stats()` with specialized routing for microbial operon and metabolome mapping.

### Entry Points B and C (Non-FASTQ)

* **Alignment (BAM/CRAM):** CRAM-to-BAM conversion (parallel), BAM indexing, featureCounts, Stage 2.
* **Count Matrix (CSV/TSV):** CSV/TSV loading, validation (non-empty, all-numeric, non-negative), canonical copy, Stage 2.

---

## Phase 5: Convergence and Normalization (Stage 2)

Orchestrated by `run_stage2_stats()` in `pipeline/stats/core.py`.

1. **Gene Filtering:** `_filter_low_counts()` with `min_total=10` threshold.
2. **Batch Correction:** `_combat_seq()` via `sva::ComBat_seq()` through rpy2. Conditional on batch column presence in metadata.
3. **DESeq2 Normalization and DGE:** `_run_deseq2()` with dynamic formula construction, factor sanitization, multi-contrast extraction, BiocParallel `MulticoreParam` for parallel dispersion estimation.
4. **Outlier Detection:** `_detect_outliers()` via PCA-based Mahalanobis distance.
5. **Automated Annotation:** `annotate_deg_table()` queries MyGene.info REST API in batches with exponential backoff retry. Appends gene descriptions and disease associations to the DEG table.
6. **Plotly Visualization Data:** `_generate_plot_data()` produces JSON payloads for PCA (sklearn, variance explained), UMAP (umap-learn), Volcano (log2FC vs -log10 padj with threshold lines), MA (baseMean vs log2FC), and Heatmap (z-score, top DEGs with group color annotations).

### Stats Source Files

| File              | Purpose                                                                                               |
| :---------------- | :---------------------------------------------------------------------------------------------------- |
| `core.py`         | `run_stage2_stats()` -- main Stage 2 driver                                                           |
| `_helpers.py`     | `_load_metadata()`, `_align_samples()`, `_filter_low_counts()`, `_combat_seq()`, `_detect_outliers()` |
| `_deseq2.py`      | DESeq2 R bridge: formula builder, factor sanitization, multi-contrast extraction                      |
| `_annotations.py` | `annotate_deg_table()` -- MyGene.info REST API batched queries                                        |
| `_methylkit.py`   | `run_differential_methylation()` -- methylKit R bridge for bisulfite data                             |
| `_plots.py`       | `_generate_plot_data()` -- PCA, UMAP, Volcano, MA, Heatmap JSON serialization                         |
| `_plots_wgcna.py` | `build_module_trait_heatmap()`, `build_pathway_dotplot()` -- WGCNA-specific Plotly helpers            |
| `_r_bridge.py`    | Shared lazy rpy2 initialization; provides `ro`, `localconverter`, `importr`, `_converter`, `_R_CORES` |

---

## Phase 6: Standard Analytical Spokes (Tier 2)

12 modular micro-pipelines that unlock inside a specific Submission Hub after Stage 2 completion. Triggered via `POST /api/submissions/{id}/modules/{name}/run`.

**Hub Isolation Principle:** Module jobs are flagged `is_core_pipeline=False`. They execute in the background and write output to `AnalysisJob.result_payload`. The global Active Workspace remains uncluttered.

The Core Hub UI uses a 3-tab layout (Overview | Modules | Single-Cell). Tab 2 contains a master-detail split pane: a scrollable module list on the left and a dynamic detail pane on the right. The detail pane supports four states: empty, history list, new run form, and result view.

| #    | Module          | Engine                       | Reused Hub Data                | Hub UI Input                                                                                      | Backend Status |
| :--- | :-------------- | :--------------------------- | :----------------------------- | :------------------------------------------------------------------------------------------------ | :------------- |
| A    | Alt Splicing    | `IsoformSwitchAnalyzeR`      | Aligned BAMs, GTF              | Condition mapping (manual table or CSV upload, with downloadable BAM-prefilled template)          | Implemented    |
| B    | RNA Editing     | `REDItools2`                 | Aligned BAMs, Reference Genome | Whole transcriptome checkbox or BED file upload                                                   | Implemented    |
| C    | Time Series     | `ImpulseDE2`                 | Normalized Expression          | Timepoints (comma-separated) + time unit dropdown                                                 | Implemented    |
| D    | WGCNA           | `PyWGCNA`                    | Normalized Expression          | Soft-power threshold (1-30) + clinical traits (CSV upload, example, or manual builder)            | Implemented    |
| E    | Pathways        | `gseapy` + `clusterProfiler` | Final DEG Table                | Gene-set database dropdown (PathBank, Hallmark, C2 KEGG/Reactome, C5 GO BP/MF/CC) + FDR threshold | Stub           |
| F    | Causal Networks | `arboreto` (GRNBoost2)       | Normalized Expression          | Transcription factors (textarea) + STRING confidence threshold                                    | Stub           |
| G    | Literature NLP  | INDRA Bio API                | Final DEG Table                | Context keywords                                                                                  | Stub           |
| H    | Survival        | `lifelines`                  | Normalized Expression          | Genes of interest (comma-separated) + clinical survival data (CSV/example/builder)                | Stub           |
| I    | TCGA Cancer     | `TCGAbiolinks`               | Normalized Expression          | Target TCGA cohort dropdown (BRCA, LUAD, COAD, PRAD, LIHC, KIRC, GBM, OV)                         | Stub           |
| J    | Biomarkers      | MarkerDB API                 | Final DEG Table                | Disease context                                                                                   | Stub           |
| K    | MOFA            | `mofapy2`                    | Normalized Expression          | Number of factors (2-50) + secondary omics matrix (CSV/example/builder)                           | Stub           |
| L    | DIABLO          | `mixOmics`                   | Normalized Expression          | Number of components (2-20) + secondary omics matrix (CSV/example/builder)                        | Stub           |

**WGCNA implementation details:** 6-step pipeline in `_module_wgcna.py` (load data, find modules, module-trait correlation, hub gene extraction, Enrichr enrichment, plot generation). Stats helpers in `_plots_wgcna.py`. 24 unit tests.

**Alt Splicing implementation details:** 4-step pipeline in `_module_alt_splicing.py` (load data, importRdata, isoformSwitchAnalysisPart1, serialize). Uses IsoformSwitchAnalyzeR via rpy2 bridge. Outputs include volcano plot (dIF vs q-value) and switch consequence bar chart.

**RNA Editing implementation details:** 4-step pipeline in `_module_rna_editing.py` (prepare, REDItools2, filter, plots). Filters for A-to-I (AG) and C-to-U (TC) edits with MIN_COVERAGE=10. Outputs include substitution bar chart and HTML table preview.

**Time Series implementation details:** 4-step pipeline in `_module_timeseries.py` (load, annotation, ImpulseDE2 via rpy2, serialize). Supports simple longitudinal and case-control designs. Stats helpers in `_plots_timeseries.py` build trajectory line chart with grouped means and SEM error bars.

### Module E: Pathway & Gene Set Enrichment

* **Engine:** `gseapy` and custom R scripts (`clusterProfiler`).
* **Databases Included:**
    * **PathBank:** Comprehensive integration for interactive mapping of metabolic, disease, and signaling pathways. Used to visualize DEGs directly on dynamic pathway diagrams.
    * **Standard Gene Sets:** MSigDB Hallmark, C2 (KEGG, Reactome), C5 (GO BP/MF/CC).
    * **Microbial Pathways (BASys2):** For bacterial datasets, utilizes the whole metabolome and operon annotations extracted from the Phase 4 BASys2 output to map microbial differentially expressed genes to specific metabolic pathways.
* **Outputs:** PathBank interactive network graphs, GO/KEGG dot plots, enrichment data tables, and pathway visualization HTML files.

---

## Phase 7: Predictive Single-Cell and Spatial Gateway (Tier 3 and 4)

### Tier 3: Deconvolution Engine

* Takes the normalized bulk matrix and a single-cell reference atlas. Planned engines: `DestVI` or `BayesPrism`.
* Frontend gateway UI is complete: atlas selector dropdown (4 atlases), high-resolution toggle, "Run Deconvolution" button, completion polling, spoke unlock logic (all in `core_hub.js`).
* Backend is not implemented. `scvi-tools` is commented out in `requirements.txt`. `H5AD_PSEUDO` file role is defined in the model but no code generates `.h5ad` files.

### Tier 4: Advanced Spatial Spokes

* **Trajectory Inference (scanpy/PAGA):** No backend. `scanpy` is installed but unused for trajectory.
* **Spatial Mapping (Tangram):** No backend. `tangram-sc` is commented out in `requirements.txt`.
* **Spatial Autocorrelation (Squidpy):** No backend. `squidpy` is commented out in `requirements.txt`.
* All three appear as locked spoke cards in the Core Hub Single-Cell tab, unlockable after deconvolution completion.

---

## Phase 8: DevOps, Janitorial and Server Security

### 8.1 Auto-Purge Janitor

* Celery Beat task at 2:00 AM UTC (`purge-expired-sessions` crontab in `config/celery.py`).
* Queries expired `Session` rows, executes `shutil.rmtree()` on `/app/media/sessions/{uuid}/`, Django cascade-deletes DB rows.
* Management command: `python manage.py purge_expired [--dry-run]` with audit logging.

### 8.2 CI/CD and Testing

* **GitHub Actions:** `.github/workflows/e2e.yml` runs Miniconda setup, bioinformatics tool verification, yeast HISAT2 index build, Django migrations, and `test_e2e.py` on a synthetic sacCer3 dataset on every push.
* **Test suite:** 12 test files covering models, views, uploads, all 4 pipeline tracks, Stage 2 stats (including edge cases), WGCNA, Tus hooks, genome index resolution, validators, and end-to-end integration.
* **`.gitignore`:** Blocks `.fastq.gz`, `.bam`, `.cram`, `.sam`, `.h5ad`, `.ht2`, and `.RData` to prevent repo bloat.

### 8.3 Docker and Deployment

* **Dockerfile:** Multi-stage production container (Miniconda base, conda env, pip deps, app code, collectstatic, EXPOSE 8000).
* **`docker-compose.yml`:** 5 services (web/Daphne, worker/Celery with 32 GB RAM limit, beat, redis/7-alpine, tusd/v2). Shared `media-data` volume with NFS mount flags documented. Health checks on all services.
* **`docker-compose.dev.yml`:** Override with live code mount, reduced concurrency (2), debug log level.
* **`deploy.sh`:** Production deployment script (.env check, pip install, migrate, collectstatic, Nginx symlink, systemd reload).
* **`nginx/rnaseek.conf`:** HTTPS (Let's Encrypt), WebSocket upgrade, Tus `/files/` proxy to tusd (streaming, `proxy_request_buffering off`), `client_max_body_size 0` (unlimited), 30-day static cache, gzip compression.
* **systemd:** `rnaseek-web.service` (Daphne), `rnaseek-worker.service` (Celery), `rnaseek-beat.service`, plus `rnaseek-tusd.service` for bare-metal tusd deployment.

### 8.4 Pre-Built Reference Indices

All 11 reference genomes ship with pre-built HISAT2 indices (`.ht2`), FASTA, and GTF annotation files. Build scripts under `doc/script/` generate indices for BWA, Bismark, and miRBase/Bowtie. Only custom user-uploaded genomes trigger runtime index building via the `hisat2_build` pipeline step.

**EuGlid Extended Species Support (800+ Species):** For eukaryotic organisms not covered by the 11 pre-built assemblies, the pipeline integrates EuGlid — a curated multi-species genome registry. When a user selects an EuGlid species, the pipeline fetches the reference FASTA and annotation from the EuGlid data source, builds HISAT2 indices on demand, and persists them under `pipeline/reference_genomes/euglid/` for subsequent reuse. This expands standard RNA-seq support from 11 organisms to 800+ eukaryotic species without requiring a custom genome upload.

### 8.5 Not Implemented

* **Prometheus and Grafana monitoring:** No monitoring infrastructure exists for Celery queue depth or worker RAM.

---

## Real-Time Progress (WebSocket)

* **Consumer:** `PipelineProgressConsumer` (AsyncWebsocketConsumer) at `ws/pipeline/<job_id>/`. Validates session ownership via cookie. Joins `pipeline_{job_id}` channel group. Broadcasts `pipeline_progress` events.
* **ASGI routing:** `config/asgi.py` configures `ProtocolTypeRouter` with `AuthMiddlewareStack` + `URLRouter`.
* **Channel layer:** Redis backend.
* **Task-side:** `_emit_progress()` in `_helpers.py` sends progress updates.
* **Frontend:** `processing.html` opens WebSocket with reconnect logic (2s x retry backoff); falls back to HTTP polling if WebSocket fails.

---

## Reference Genomes

| Genome      | Assembly                                                                                                |
| :---------- | :------------------------------------------------------------------------------------------------------ |
| Human       | GRCh38 (hg38)                                                                                           |
| Mouse       | GRCm39 (mm39)                                                                                           |
| Mouse       | GRCm38 (mm10)                                                                                           |
| Rat         | rn7                                                                                                     |
| Zebrafish   | GRCz11 (danRer11)                                                                                       |
| Chicken     | GRCg6a (galGal6)                                                                                        |
| Pig         | Sscrofa11.1 (susScr11)                                                                                  |
| Drosophila  | dm6                                                                                                     |
| C. elegans  | WBcel235 (wbcel235)                                                                                     |
| Yeast       | sacCer3 (R64-1-1)                                                                                       |
| Arabidopsis | TAIR10 (araTha)                                                                                         |
| Custom      | User-uploaded FASTA + GTF/GFF with on-demand index build                                                |
| Bacterial   | BASys2 On-Demand Annotation (FASTA -> Annotated GenBank/JSON)                                           |
| EuGlid      | 800+ eukaryotic species via on-demand EuGlid genome registry (HISAT2 index built and cached at runtime) |

***
