# RNAseek — Project Progress

> Last updated after: **Frontend design & implementation phase**

---

## 1. Environment & Installation

| Component              | Detail                                                                                                                                                               |
| ---------------------- | -------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| **Conda env**          | `rnaseek` (Python 3.11, R 4.3)                                                                                                                                       |
| **Python framework**   | Django 5.2.12                                                                                                                                                        |
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
  models.py         ← Session, FileAsset, AnalysisJob
  views.py          ← Page views + API views
  urls.py           ← 7 page routes + 4 API routes
  middleware.py     ← AnonymousSessionMiddleware
  tasks.py          ← Celery task stubs
  admin.py          ← (default)
  apps.py           ← PipelineConfig
```

### 2.2 Database Schema (3 models)

**Session**
- `session_id` — UUID primary key (auto-generated)
- `created_at` — DateTimeField (auto)
- `expires_at` — DateTimeField (default: now + 14 days)
- Property: `is_expired` → True if past `expires_at`

**FileAsset**
- `id` — UUID primary key
- `session` — FK → Session (cascade)
- `file_role` — choices: `RAW_FASTQ`, `COUNT_MATRIX`, `H5AD_PSEUDO`, `HE_IMAGE_USER`, `HE_IMAGE_GENERIC`
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

| Method | Endpoint              | Purpose                                                                                 |
| ------ | --------------------- | --------------------------------------------------------------------------------------- |
| POST   | `/api/upload/chunk`   | Receive 5 MB file chunk, append to temp file, create FileAsset when final chunk arrives |
| POST   | `/api/pipeline/core`  | Trigger Core Pipeline Celery task, returns `job_id`                                     |
| GET    | `/api/jobs/<uuid>/`   | Poll job status (PENDING/RUNNING/SUCCESS/FAILED) + result payload                       |
| GET    | `/api/session/assets` | List all FileAssets for current session                                                 |

### 2.5 Celery Tasks

- `run_core_pipeline(session_id)` — Placeholder that transitions PENDING → RUNNING → SUCCESS. Bioinformatics logic (FastQC → Trimmomatic → HISAT2 → featureCounts → DESeq2 → MultiQC) to be implemented.

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

| Template              | URL                     | Description                                                                                                              |
| --------------------- | ----------------------- | ------------------------------------------------------------------------------------------------------------------------ |
| `base.html`           | –                       | Base layout: navbar (logo + 4 nav links + session badge), footer, static/font imports                                    |
| `home.html`           | `/`                     | Hero section with gradient background, 4-card capabilities, 12-module grid, "How It Works" 4-step flow                   |
| `tutorials.html`      | `/tutorials/`           | File format guide, metadata mapping example, workflow diagram placeholder, reference genomes table                       |
| `workspaces.html`     | `/workspaces/`          | 14-day expiry warning banner, jobs table with status badges, empty state                                                 |
| `new_submission.html` | `/new/`                 | 3-step wizard (Upload FASTQ → Select Reference Genome → Map Metadata), drop zone, paired-end toggle                      |
| `processing.html`     | `/processing/<job_id>/` | 6-step pipeline progress tracker, animated progress bar, auto-polling (3s), completion redirect                          |
| `core_hub.html`       | `/hub/<job_id>/`        | Downloads section, 4 visualization plots, 11 module cards (categorized), deconvolution gateway, 3 locked advanced spokes |
| `advanced.html`       | `/advanced/<job_id>/`   | Three spoke workspaces: Trajectory Analysis, Spatial Transcriptomics, Spatial Autocorrelation                            |

### 3.3 JavaScript

| File                                          | Purpose                                                                                                                           |
| --------------------------------------------- | --------------------------------------------------------------------------------------------------------------------------------- |
| `pipeline/static/pipeline/js/setup_wizard.js` | File drop zone, drag & drop, file pills, chunked upload (5 MB), step navigation (1→2→3), metadata table, pipeline submission      |
| `pipeline/static/pipeline/js/core_hub.js`     | Module card → modal (with module-specific inputs), module submission, deconvolution gateway, spoke unlock polling, download links |
| (inline in `processing.html`)                 | Job status polling every 3s, step indicator updates, completion redirect                                                          |

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
│   └── global.css              ← Component styles
├── js/
│   ├── setup_wizard.js         ← Upload & wizard logic
│   └── core_hub.js             ← Module & deconvolution logic
└── images/
    ├── rnaseek_logo.png        ← Icon logo (543×389)
    └── rnaseek_logo_name.png   ← Full logo with name (1408×768)
```

---

## 4. What Has Been Achieved

- [x] Conda environment `rnaseek` fully configured (Python 3.11, R 4.3, all bioinformatics tools)
- [x] Django 5.2 project scaffolded with `config` package and `pipeline` app
- [x] SQLite database with 3 models (Session, FileAsset, AnalysisJob) — migrations applied
- [x] Celery + Redis integration configured and task placeholder in place
- [x] Cookie-based anonymous session middleware (14-day expiry, HttpOnly)
- [x] Chunked file upload API (5 MB chunks, path-traversal safe)
- [x] Core Pipeline trigger API + job status polling API + session assets API
- [x] Complete CSS design system extracted from logo colors (navy-teal-mint palette)
- [x] Full component library (buttons, cards, tables, badges, forms, modals, etc.)
- [x] 7-page template system with responsive layout and consistent theming
- [x] Setup wizard with 3-step flow (upload → genome → metadata)
- [x] Real-time processing monitor with animated progress indicators
- [x] Core Hub dashboard with 11 module cards, visualization placeholders, download section
- [x] Deconvolution gateway with atlas selection
- [x] Advanced workspace page with 3 spoke analyses
- [x] All URL routes verified and Django system check passes

---

## 5. What Remains (Next Phases)

- [ ] Implement bioinformatics pipeline logic in Celery tasks (FastQC → Trimmomatic → HISAT2 → featureCounts → DESeq2 → MultiQC)
- [ ] Implement individual module runners (WGCNA, GSEA, Survival, MOFA, DIABLO, etc.)
- [ ] Implement deconvolution pipeline (BisqueRNA/MuSiC/Scaden → h5ad generation)
- [ ] Implement advanced spoke pipelines (Trajectory via scVI/PAGA, Spatial via Tangram/Squidpy, Autocorrelation via Moran's I)
- [ ] Wire up Plotly.js for interactive visualization rendering (PCA, UMAP, Volcano, MA, Heatmap)
- [ ] Add real download file serving from pipeline output directories
- [ ] Add WebSocket support (Django Channels) for real-time progress updates
- [ ] Session cleanup cron job (purge expired sessions + files)
- [ ] Migrate to PostgreSQL for production
- [ ] Docker containerization
- [ ] Production deployment (Gunicorn + Nginx)

---

## 6. Image Placeholders Needed

See section at end of this document for all images required.
