# RNAseek — System Architecture

---

## 1. High-Level Structure

RNAseek is a multi-tenant asynchronous bioinformatics platform built on Django 5.2 (ASGI). The system is composed of four layers that communicate over a shared Redis bus and a POSIX-compliant shared filesystem.

```
┌──────────────────────────────────────────────────────────────────────┐
│                          BROWSER (Client)                            │
│  ┌──────────────┐  ┌──────────────┐  ┌──────────────┐                │
│  │  Wizard UI   │  │ Processing   │  │  Core Hub    │                │
│  │(pipeline_    │  │ (WebSocket + │  │ (Plotly.js + │                │
│  │ setup.js)    │  │  HTTP poll)  │  │ core_hub.js) │                │
│  └──────┬───────┘  └──────┬───────┘  └──────┬───────┘                │
└─────────┼─────────────────┼─────────────────┼────────────────────────┘
          │ HTTPS (chunks,  │ WSS / HTTPS     │ HTTPS (API)
          │ JSON)           │                 │
┌─────────▼─────────────────▼─────────────────▼────────────────────────┐
│                       NGINX (Reverse Proxy)                          │
│  • SSL termination (Let's Encrypt / Certbot)                         │
│  • /ws/ → WebSocket upgrade                                          │
│  • /files/ → tusd upstream (127.0.0.1:1080) for Tus uploads          │
│  • /static/ → WhiteNoise (30-day cache)                              │
│  • /* → Daphne upstream (127.0.0.1:8000)                             │
│  • client_max_body_size 0 (unlimited — tusd handles chunking)        │
└─────────┬────────────────────────────────────────────────────────────┘
          │
┌─────────▼───────────────────────────────────────────────────────────┐
│                    DAPHNE (ASGI Server)                             │
│  ┌────────────────────────────────────────────────────────────────┐ │
│  │  Django 5.2                                                    │ │
│  │  ┌──────────────┐  ┌────────────────┐  ┌───────────────────┐   │ │
│  │  │ Page Views   │  │  API Views     │  │  WebSocket        │   │ │
│  │  │ (pages.py)   │  │  (api.py)      │  │  Consumer         │   │ │
│  │  │ 7 templates  │  │  10 endpoints  │  │  (consumers.py)   │   │ │
│  │  └──────────────┘  └───────┬────────┘  └────────┬──────────┘   │ │
│  │                            │ dispatch            │ broadcast   │  │
│  │  ┌──────────────┐          │                     │             │  │
│  │  │ Middleware   │          │                     │             │  │
│  │  │ (Session     │          │                     │             │  │
│  │  │  Cookie)     │          │                     │             │  │
│  │  └──────────────┘          │                     │             │  │
│  └────────────────────────────┼─────────────────────┼─────────────┘  │
└───────────────────────────────┼─────────────────────┼────────────────┘
                                │                     │
┌───────────────────────────────▼─────────────────────▼────────────────┐
│                         REDIS 7+                                     │
│  • Celery message broker (queue)                                     │
│  • Django Channels layer (WebSocket pub/sub)                         │
│  • Celery result backend                                             │
└───────────────────────────────┬──────────────────────────────────────┘
                                │ consume tasks
┌───────────────────────────────▼──────────────────────────────────────┐
│                    CELERY WORKER FLEET                                │
│  ┌────────────────────────────────────────────────────────────────┐  │
│  │  Prefork pool (concurrency = CPU count)                        │  │
│  │  ┌──────────────┐  ┌──────────────┐  ┌──────────────────────┐ │  │
│  │  │ run_core_    │  │ run_tier2_   │  │ purge_expired_       │ │  │
│  │  │ pipeline()   │  │ module()     │  │ sessions()           │ │  │
│  │  └──────┬───────┘  └──────┬───────┘  └──────────────────────┘ │  │
│  │         │                 │                                    │  │
│  │  ┌──────▼─────────────────▼────────────────────────────────┐  │  │
│  │  │  Track Routers & Stats Engine                            │  │  │
│  │  │  • _track_standard.py  (HISAT2)                          │  │  │
│  │  │  • _track_mirna.py     (Bowtie + miRBase)                │  │  │
│  │  │  • _track_chipseq.py   (BWA + MACS2)                     │  │  │
│  │  │  • _track_methyl.py    (Bismark + methylKit)             │  │  │
│  │  │  • _routes.py          (BAM/Matrix entry points)         │  │  │
│  │  │  • stats/core.py       (DESeq2, ComBat, PCA, UMAP...)   │  │  │
│  │  │  • _module_wgcna.py    (PyWGCNA + Enrichr)               │  │  │
│  │  └─────────────────────────────────────────────────────────┘  │  │
│  └────────────────────────────────────────────────────────────────┘  │
└───────────────────────────────┬──────────────────────────────────────┘
                                │ read/write
┌───────────────────────────────▼──────────────────────────────────────┐
│                  SHARED FILESYSTEM (NFS / Docker Volume)             │
│                                                                      │
│  /app/media/                                                         │
│  ├── sessions/{session_uuid}/                                        │
│  │   └── {submission_uuid}/        ← per-submission work directory   │
│  │       ├── raw/                  ← uploaded FASTQ files            │
│  │       ├── aligned/              ← BAM files                       │
│  │       ├── counts/               ← count matrices                  │
│  │       ├── trimmed/              ← Trimmomatic output              │
│  │       ├── qc/                   ← FastQC + MultiQC reports        │
│  │       ├── metadata/             ← uploaded CSV metadata           │
│  │       ├── custom_genome/        ← user FASTA + GTF/GFF            │
│  │       ├── peaks/                ← MACS2 peak files                │
│  │       ├── methylation/          ← Bismark reports                 │
│  │       ├── normalized_counts.csv                                   │
│  │       ├── deg_results.csv                                         │
│  │       └── multiqc_report.html                                     │
│  │                                                                   │
│  pipeline/reference_genomes/       ← pre-indexed genomes (11)       │
│  ├── Human_GRCh38/                                                   │
│  │   ├── genome.fa                                                   │
│  │   ├── genes.gtf                                                   │
│  │   └── hisat2_index/             ← .ht2 index files               │
│  ├── Mouse_GRCm39/                                                   │
│  └── ... (9 more)                                                    │
│                                                                      │
└──────────────────────────────────────────────────────────────────────┘

┌──────────────────────────────────────────────────────────────────────┐
│                    CELERY BEAT (Scheduler)                            │
│  • purge-expired-sessions: crontab(hour=2, minute=0) daily           │
└──────────────────────────────────────────────────────────────────────┘

┌──────────────────────────────────────────────────────────────────────┐
│                    DATABASE (SQLite dev / PostgreSQL prod)            │
│  • 4 models: Session, AnalysisSubmission, FileAsset,                 │
│    AnalysisJob                                                       │
└──────────────────────────────────────────────────────────────────────┘
```

### Component Interaction Summary

| Component              | Role                                                              | Communicates With                                                                        |
| ---------------------- | ----------------------------------------------------------------- | ---------------------------------------------------------------------------------------- |
| **Nginx**              | SSL termination, reverse proxy, static serving, WebSocket upgrade | Daphne (upstream), tusd (upstream for `/files/`)                                         |
| **tusd**               | Tus protocol resumable uploads, webhook notification              | Nginx (reverse proxy), Django (post-finish webhook), Filesystem (upload writes)          |
| **Daphne**             | ASGI server: HTTP requests + WebSocket connections                | Redis (channels layer), Database (ORM), Filesystem (uploads)                             |
| **Django Views**       | Request handling, validation, template rendering                  | Database (ORM), Redis (task dispatch)                                                    |
| **WebSocket Consumer** | Real-time progress push to browser                                | Redis (channel group pub/sub)                                                            |
| **Session Middleware** | Tenant isolation via UUID cookie                                  | Database (Session model)                                                                 |
| **Celery Workers**     | Heavy bioinformatics computation (HISAT2, DESeq2, etc.)           | Redis (task queue, progress broadcast), Database (job status), Filesystem (input/output) |
| **Celery Beat**        | Scheduled maintenance (session purge)                             | Redis (task dispatch)                                                                    |
| **Redis**              | Message broker, WebSocket layer, result backend                   | All server-side components                                                               |

---

## 2. Database Design

### Entity-Relationship Diagram

```
┌───────────────┐       ┌─────────────────────┐       ┌──────────────┐
│    Session    │ 1───* │ AnalysisSubmission  │ 1───* │  FileAsset   │
│───────────────│       │─────────────────────│       │──────────────│
│ session_id PK │       │ submission_id PK    │       │ id PK        │
│ created_at    │       │ session FK ─────────┤       │ session FK   │
│ expires_at    │       │ submission_name     │       │ submission FK│
│               │       │ input_data_type     │       │ file_role    │
│               │       │ assay_type          │       │ local_path   │
│               │       │ library_type        │       │ is_user_     │
│               │       │ strandedness        │       │   uploaded   │
│               │       │ reference_genome    │       └──────────────┘
│               │       │ custom_genome_name  │
│               │       │ metadata_mode       │       ┌──────────────┐
│               │       │ metadata_payload {} │
│               │       │ adjusted_pvalue     │
│               │       │ min_log2fc          │
│               │       │ max_log2fc          │
│               │       │ created_at          │
│               │       └─────────────────────┘
│               │
│               │       ┌─────────────────────┐
│               │ 1───* │   AnalysisJob       │
│               │       │─────────────────────│
│               │       │ job_id PK           │
│               │       │ session FK ─────────┤
│               │       │ parent_submission FK│ ←── FK to AnalysisSubmission
│               │       │ is_core_pipeline    │
│               │       │ module_name         │
│               │       │ status              │
│               │       │ result_payload {}   │
│               │       │ step_progress {}    │
│               │       │ created_at          │
│               │       │ updated_at          │
└───────────────┘       └─────────────────────┘
```

### Model Descriptions

**`Session`** — The root tenant. Every user interaction is scoped to a single anonymous session identified by a UUID cookie.

- `session_id` (UUID, PK) — Auto-generated, set as `HttpOnly` cookie
- `created_at` (DateTime) — Auto-set on creation
- `expires_at` (DateTime) — Default: `now + 14 days`; used by the janitor task for auto-purge
- Property `is_expired` — Computed: `timezone.now() > self.expires_at`
- **Cascade:** Deleting a Session cascades to all AnalysisSubmission, FileAsset, and AnalysisJob rows

**`AnalysisSubmission`** — A primary analysis run. Represents one user workflow from upload through Stage 2 completion. Renders as a card in the Global Workspace.

- `submission_id` (UUID, PK)
- `session` (FK → Session, CASCADE)
- `submission_name` (CharField) — User-defined label
- `input_data_type` (CharField, choices: `fastq` | `alignment` | `matrix`) — Determines the pipeline entry point
- `assay_type` (CharField, choices: `standard_rna` | `small_rna` | `chip_seq` | `methylation`) — Determines the Stage 1 track (FASTQ entry only)
- `library_type` (CharField: `single` | `paired`) — Single-end vs paired-end reads
- `strandedness` (CharField: `unstranded` | `fr-firststrand` | `fr-secondstrand`)
- `reference_genome` (CharField) — Genome key (e.g., `hg38`, `mm39`, `custom`)
- `custom_genome_name` (CharField, optional) — User label for custom genomes
- `metadata_mode` (CharField: `upload` | `manual`) — How metadata was provided
- `metadata_payload` (JSONField) — Stores: `samples` (list of dicts), `column_mapping` (primary_group, batch_effect, covariates), `contrasts` (pairwise comparisons), `quant_level` (gene/transcript)
- `adjusted_pvalue`, `min_log2fc`, `max_log2fc` (FloatField) — Statistical thresholds
- Property `upload_dir` — Computed: `media/sessions/{session_id}/{submission_id}/`

**`FileAsset`** — Tracks every physical file on the shared filesystem. Supports 16 roles spanning raw uploads through pipeline outputs.

- `id` (UUID, PK)
- `session` (FK → Session, CASCADE)
- `submission` (FK → AnalysisSubmission, CASCADE, nullable) — Files can be detached during upload
- `file_role` — One of: `RAW_FASTQ`, `ALIGNMENT_BAM`, `USER_COUNT_MATRIX`, `COUNT_MATRIX`, `NORMALIZED_COUNTS`, `DEG_TABLE`, `MULTIQC_REPORT`, `H5AD_PSEUDO`, `HE_IMAGE_USER`, `HE_IMAGE_GENERIC`, `CUSTOM_GENOME_FASTA`, `CUSTOM_GENOME_ANNOTATION`, `METADATA_CSV`, `PEAK_FILE`, `METHYLATION_REPORT`
- `local_path` (CharField, 500) — Absolute path on the shared filesystem
- `is_user_uploaded` (Boolean) — Distinguishes user uploads from pipeline-generated files

**`AnalysisJob`** — Tracks Celery task execution. The `job_id` doubles as the Celery task ID for direct `AsyncResult` lookup.

- `job_id` (UUID, PK) — Also used as the Celery `task_id`
- `session` (FK → Session, CASCADE)
- `parent_submission` (FK → AnalysisSubmission, CASCADE, nullable)
- `is_core_pipeline` (Boolean, default `True`) — Core pipeline jobs appear in the Global Workspace; Tier 2 module jobs (`False`) are scoped to the Hub
- `module_name` (CharField, 50) — E.g., `CORE_PIPELINE`, `WGCNA`, `PATHWAY`
- `status` (CharField: `PENDING` | `RUNNING` | `SUCCESS` | `FAILED`)
- `result_payload` (JSONField) — Output data: plot coordinates, DEG summaries, module results
- `step_progress` (JSONField) — Per-step status tracking: `{ "steps": {"fastqc": "done", "hisat2": "running", ...}, "current_step": "hisat2" }`
- Timestamps: `created_at`, `updated_at`

### Relationship Cardinalities

| Relationship                     | Cardinality |
| -------------------------------- | ----------- |
| Session → AnalysisSubmission     | 1 : N       |
| Session → FileAsset              | 1 : N       |
| Session → AnalysisJob            | 1 : N       |
| AnalysisSubmission → FileAsset   | 1 : N       |
| AnalysisSubmission → AnalysisJob | 1 : N       |

All foreign keys use `CASCADE` deletion — deleting a Session recursively removes all associated data.

---

## 3. Data Flow

### 3.1 Core Pipeline Execution (FASTQ Entry Point)

This is the primary data flow. BAM and Matrix entry points skip to steps 5 and 6 respectively.

```
 BROWSER                    DJANGO                     CELERY WORKER              FILESYSTEM
 ───────                    ──────                     ─────────────              ──────────
    │                          │                            │                         │
    │  1. POST /api/           │                            │                         │
    │     submission/create    │                            │                         │
    │ ─────────────────────► │                            │                         │
    │  ◄── { submission_id }   │                            │                         │
    │                          │                            │                         │
    │  2. POST /api/upload/    │                            │                         │
    │     chunk (×N per file,  │                            │                         │
    │     6 concurrent)        │                            │                         │
    │ ─────────────────────► │                            │                         │
    │     25 MB binary chunks  │──── buffer to local SSD ► │                         │
    │  ◄── { progress }        │     (when all arrived:     │                         │
    │                          │      merge → move to NFS → │                         │
    │                          │      create FileAsset)     │                         │
    │                          │                            │                         │
    │  3. POST /api/           │                            │                         │
    │     pipeline/core        │                            │                         │
    │     { input_data_type,   │                            │                         │
    │       assay_type,        │                            │                         │
    │       metadata, ... }    │                            │                         │
    │ ─────────────────────► │                            │                         │
    │                          │── validate payload         │                         │
    │                          │── create AnalysisJob       │                         │
    │                          │── dispatch task ──────► │                         │
    │  ◄── { job_id }          │   (via Redis queue)        │                         │
    │                          │                            │                         │
    │  4. WebSocket connect    │                            │                         │
    │     ws://pipeline/{id}/  │                            │                         │
    │ ◄═══════════════════════ │                            │                         │
    │                          │                            │                         │
    │                          │                     5. run_core_pipeline()           │
    │                          │                        │                              │
    │                          │                        │── read FASTQ ◄──────────── │
    │                          │                        │── FastQC ─────────────────► │
    │                          │                        │── Trimmomatic ────────────► │
    │                          │                        │── HISAT2 align ───────────► │
    │                          │                        │── featureCounts ──────────► │
    │                          │                        │── MultiQC ────────────────► │
    │                          │                        │                              │
    │                          │                        │── (each step emits progress  │
    │                          │                        │    via Redis Channels layer)  │
    │  ◄═══ progress event ════│◄══════════════════════ │                              │
    │  (update UI per step)    │                        │                              │
    │                          │                     6. run_stage2_stats()             │
    │                          │                        │── filter low counts           │
    │                          │                        │── ComBat-seq (if batch)       │
    │                          │                        │── DESeq2 (via rpy2)           │
    │                          │                        │── outlier detection            │
    │                          │                        │── annotate (MyGene.info)      │
    │                          │                        │── generate plots (PCA,         │
    │                          │                        │   UMAP, Volcano, MA, Heatmap) │
    │                          │                        │── write CSVs ────────────────► │
    │                          │                        │── register FileAssets          │
    │                          │                        │── set job SUCCESS              │
    │  ◄═══ success event  ════│◄══════════════════════ │                               │
    │                          │                            │                            │
    │  7. GET /hub/{job_id}/   │                            │                            │
    │ ─────────────────────► │                            │                            │
    │  ◄── Core Hub HTML       │                            │                            │
    │  (Plotly plots,          │                            │                            │
    │   downloads,             │                            │                            │
    │   module cards)          │                            │                            │
```

### 3.2 Tier 2 Module Execution (WGCNA Example)

```
 BROWSER (Core Hub)         DJANGO                     CELERY WORKER
 ──────────────────         ──────                     ─────────────
    │                          │                            │
    │  1. Click WGCNA card     │                            │
    │     → modal opens        │                            │
    │     → user sets params   │                            │
    │                          │                            │
    │  2. POST /api/           │                            │
    │     submissions/{id}/    │                            │
    │     modules/WGCNA/run    │                            │
    │     { soft_power: 6,     │                            │
    │       enrichr_libs: [...]}                            │
    │ ─────────────────────► │                            │
    │                          │── verify core job SUCCESS  │
    │                          │── create AnalysisJob       │
    │                          │   (is_core_pipeline=False) │
    │                          │── dispatch task ──────► │
    │  ◄── { job_id }          │                            │
    │                          │                     3. run_tier2_module()
    │  4. Poll GET             │                        │── read normalized_counts.csv
    │     /api/jobs/{id}/      │                        │── PyWGCNA: find modules
    │     every 4s             │                        │── module-trait correlation
    │ ─────────────────────► │                        │── hub gene extraction
    │  ◄── { status, payload } │                        │── Enrichr pathway enrichment
    │                          │                        │── generate plots
    │  5. On SUCCESS:          │                        │── store in result_payload
    │     update module card   │                        │── set job SUCCESS
    │     render results       │                            │
```

### 3.3 Session Lifecycle & Auto-Purge

```
 BROWSER                    MIDDLEWARE                  CELERY BEAT
 ───────                    ──────────                  ──────────
    │                          │                            │
    │  1. First visit          │                            │
    │ ─────────────────────► │                            │
    │                          │── no Session_ID cookie     │
    │                          │── create Session (14-day)  │
    │  ◄── Set-Cookie:         │                            │
    │      Session_ID={uuid}   │                            │
    │      HttpOnly; SameSite  │                            │
    │                          │                            │
    │  2. Subsequent requests  │                            │
    │ ─────────────────────► │                            │
    │                          │── read Session_ID cookie   │
    │                          │── lookup Session in DB     │
    │                          │── check is_expired         │
    │                          │── attach request.session_obj
    │                          │                            │
    │                          │                     3. Every day at 2:00 AM UTC
    │                          │                        │── query: expires_at < now
    │                          │                        │── for each expired session:
    │                          │                        │   ├── shutil.rmtree(
    │                          │                        │   │   media/sessions/{uuid}/)
    │                          │                        │   └── session.delete()
    │                          │                        │       (CASCADE: submission,
    │                          │                        │        file_asset, job)
```

### 3.4 Concurrent Chunked File Upload (25 MB Slices, SSD Buffered)

```
 BROWSER                    DJANGO API                 LOCAL SSD        NFS
 ───────                    ──────────                 ─────────        ───
    │                          │                          │              │
    │  File selected (e.g.,   │                          │              │
    │  sample_R1.fq.gz 2 GB)  │                          │              │
    │                          │                          │              │
    │  1. 6 concurrent POST    │                          │              │
    │     /api/upload/chunk    │                          │              │
    │     FormData:            │                          │              │
    │       file: <25 MB blob> │                          │              │
    │       submission_id      │                          │              │
    │       filename           │                          │              │
    │       chunk_index: 0..5  │                          │              │
    │       total_chunks: 80   │                          │              │
    │       file_role: RAW_FASTQ                          │              │
    │ ─────────────────────► │                          │              │
    │                          │── sanitize filename      │              │
    │                          │── write chunk_{i}.tmp ──► │              │
    │                          │── rename → chunk_{i}      │              │
    │  ◄── { status: "ok" }    │ (atomic, no NFS I/O)     │              │
    │                          │                          │              │
    │  ... (6 concurrent       │                          │              │
    │   workers fill pool)     │                          │              │
    │                          │                          │              │
    │  2. Final chunk lands    │                          │              │
    │     (all 80 present)     │                          │              │
    │ ─────────────────────► │                          │              │
    │                          │── claim merge (O_EXCL)   │              │
    │                          │── stitch chunk_0..79 ──► │              │
    │                          │── shutil.move() ─────────────────────► │
    │                          │── rmtree(buffer_dir)      │              │
    │                          │── create FileAsset(       │              │
    │                          │     role=RAW_FASTQ,       │              │
    │                          │     local_path=NFS/...)   │              │
    │  ◄── { status: "ok",     │                          │              │
    │        asset_id: uuid }  │                          │              │
```

### 3.5 Tus Resumable Upload (tusd)

```
 BROWSER                    NGINX                      tusd                DJANGO API           NFS
 ───────                    ─────                      ────                ──────────           ───
    │                          │                          │                    │                 │
    │  1. POST /files/         │                          │                    │                 │
    │     (Tus Creation)       │                          │                    │                 │
    │ ─────────────────────► │                          │                    │                 │
    │                          │── proxy (streaming) ──► │                    │                 │
    │                          │   no request buffering   │── write file ───────────────────► │
    │  ◄── 201 Location:       │                          │   /app/media/     │                 │
    │      /files/{upload-id}  │                          │   uploads/        │                 │
    │                          │                          │                    │                 │
    │  2. PATCH /files/{id}    │                          │                    │                 │
    │     (Tus Upload,         │                          │                    │                 │
    │      resumable chunks)   │                          │                    │                 │
    │ ─────────────────────► │── stream-through ──────► │                    │                 │
    │  ◄── 204 (offset ack)    │                          │── append data ──────────────────► │
    │                          │                          │                    │                 │
    │  ... (repeat until done) │                          │                    │                 │
    │                          │                          │                    │                 │
    │  3. Final PATCH lands    │                          │                    │                 │
    │                          │                          │── POST webhook ──► │                 │
    │                          │                          │   /api/tusd-hooks/ │                 │
    │                          │                          │   Hook-Name:       │                 │
    │                          │                          │   post-finish      │                 │
    │                          │                          │                    │── move file     │
    │                          │                          │                    │   to submission  │
    │                          │                          │                    │   subdir ──────► │
    │                          │                          │                    │── create         │
    │                          │                          │                    │   FileAsset      │
    │                          │                          │  ◄── 200 OK ────── │                 │
```

### 3.6 WebSocket Real-Time Progress

```
 CELERY WORKER              REDIS CHANNELS             DAPHNE CONSUMER          BROWSER
 ─────────────              ──────────────             ───────────────          ───────
    │                          │                            │                      │
    │  _update_step(job,       │                            │                      │
    │    "hisat2", "running")  │                            │                      │
    │  _emit_progress(job)     │                            │                      │
    │ ─────────────────────► │                            │                      │
    │  channel_layer.          │── route to group           │                      │
    │  group_send(             │   "pipeline_{job_id}"      │                      │
    │    "pipeline_{job_id}",  │ ─────────────────────► │                      │
    │    { type:               │                            │── ws.send(JSON)      │
    │      pipeline_progress,  │                            │ ─────────────────► │
    │      step_progress:      │                            │                      │── update
    │        {...},            │                            │                      │   step
    │      status: "RUNNING"}) │                            │                      │   icons
    │                          │                            │                      │
```

---

## 4. Docker Service Architecture

```
┌─────────────────────────────────────────────────────────────────┐
│  docker-compose.yml                                              │
│                                                                  │
│  ┌───────────┐ ┌──────────┐ ┌────────┐ ┌────────┐ ┌──────────┐ │
│  │   web      │ │  worker  │ │  beat  │ │ redis  │ │   tusd   │ │
│  │ (Daphne)  │ │ (Celery) │ │(Celery │ │7-alpine│ │(tusd v2) │ │
│  │ port 8000 │ │ 32 GB    │ │ Beat)  │ │ port   │ │ port     │ │
│  │           │ │ limit    │ │        │ │ 6379   │ │ 1080     │ │
│  └─────┬─────┘ └────┬─────┘ └───┬────┘ └───┬────┘ └────┬─────┘ │
│        │            │           │          │           │        │
│        └────────────┴───────────┴──────────┴───────────┘        │
│                          │                                      │
│                   media-data volume                              │
│                   (/app/media/)                                  │
│                                                                  │
└─────────────────────────────────────────────────────────────────┘
```

| Service    | Image                  | Command                                                          | Resources          |
| ---------- | ---------------------- | ---------------------------------------------------------------- | ------------------ |
| **web**    | Custom (Dockerfile)    | `daphne -b 0.0.0.0 -p 8000 config.asgi:application`              | —                  |
| **worker** | Custom (Dockerfile)    | `celery -A config worker --concurrency=${CELERY_CONCURRENCY:-4}` | 32 GB memory limit |
| **beat**   | Custom (Dockerfile)    | `celery -A config beat`                                          | 1 replica          |
| **redis**  | `redis:7-alpine`       | Default                                                          | —                  |
| **tusd**   | `tusproject/tusd:v2`   | Tus daemon with webhook to Django                                | —                  |

All services share a `media-data` Docker volume mounted at `/app/media/`, providing zero-copy file access between the web server, workers, and tusd.
