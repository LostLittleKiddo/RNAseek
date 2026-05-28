# RNAseek -- Development Guide

> Local development setup without Docker, running directly on the host machine.

---

## Prerequisites

| Component  | Version  | Purpose                                   |
| ---------- | -------- | ----------------------------------------- |
| Python     | 3.11+    | Application runtime                       |
| Miniconda3 | Latest   | Conda environment (R, bioinformatics CLI) |
| Redis      | 7+       | Celery broker and Channels layer          |
| SQLite     | Built-in | Development database (auto-created)       |

---

## 1. Clone and Set Up

```bash
git clone <repository-url> /home/ubuntu/apps/rnaseek
cd /home/ubuntu/apps/rnaseek
```

### 1.1 Conda Environment

```bash
conda env create -n rnaseek -f environment.yml
conda activate rnaseek
pip install -r requirements.txt
```

### 1.2 Environment File

Create a `.env` in the project root (optional for dev -- defaults are sufficient):

```
RNASEEK_ENV=development
MEDIA_ROOT=/home/ubuntu/apps/rnaseek/media
```

When `RNASEEK_ENV` is not set or set to `development`, Django uses:
- SQLite database (`db.sqlite3`)
- `DEBUG=True`
- No SSL enforcement
- Console logging at INFO level for the `pipeline` logger

---

## 2. Initialize the Database

```bash
python manage.py migrate
```

This creates `db.sqlite3` in the project root with all tables.

---

## 3. Set Up Reference Genomes

The pipeline requires pre-built genome indices stored in `pipeline/reference_genomes/`. Scripts for this are in `doc/script/`. All scripts must be run from inside that directory with the conda environment active.

```bash
cd pipeline/reference_genomes
```

### 3.1 Download Genome FASTA Files

Downloads and extracts FASTA files for all 11 supported species (Human GRCh38, Mouse GRCm39/GRCm38, Rat rn7, Zebrafish GRCz11, Chicken GRCg6a, Pig Sscrofa11.1, Drosophila dm6, C. elegans WBcel235, Yeast sacCer3, Arabidopsis TAIR10). Each genome is placed in `<Species>/genome/`.

```bash
bash ../../doc/script/download_genomes.sh
```

### 3.2 Build HISAT2 Indices (RNA-seq)

Required for the RNA-seq track. Builds a HISAT2 index for every species found in the directory.

```bash
bash ../../doc/script/build_hisat2_indices.sh
```

### 3.3 Build BWA Indices (ChIP-seq)

Required for the ChIP-seq track. Runs up to 15 indexing jobs in parallel.

```bash
bash ../../doc/script/build_bwa_indices.sh
```

### 3.4 Build Bismark Indices (DNA Methylation)

Required for the bisulfite/WGBS track. Runs up to 15 jobs in parallel; allow ~5 GB RAM per job.

```bash
bash ../../doc/script/build_bismark_indices.sh
```

### 3.5 Build miRBase Bowtie Indices (Small RNA)

Downloads `mature.fa` from miRBase, extracts per-species sequences, and builds Bowtie indices for 8 species (hsa, mmu, rno, dre, gga, dme, cel, ath) under `miRBase/`.

```bash
bash ../../doc/script/build_mirbase_indices.sh
```

You can skip steps 3.2–3.5 for tracks you are not developing locally.

```bash
cd ../..   # return to project root
```

---

## 4. Start Services

### 4.1 Redis

Redis must be running for Celery and Django Channels:

```bash
redis-server
```

Or if installed via apt:

```bash
sudo systemctl start redis
```

### 4.2 Django Development Server

```bash
python manage.py runserver
# or use Daphne for WebSocket support:
daphne -b 127.0.0.1 -p 8000 config.asgi:application
```

The application is available at `http://localhost:8000`.

### 4.3 Celery Worker

In a separate terminal:

```bash
conda activate rnaseek
celery -A config worker --loglevel=debug --concurrency=2
```

### 4.4 Celery Beat (Optional)

Only needed if testing scheduled tasks:

```bash
celery -A config beat --loglevel=info
```

### 4.5 Eager Mode (No Worker)

For quick debugging without running a separate worker process, set `CELERY_EAGER=1`:

```bash
CELERY_EAGER=1 python manage.py runserver
```

Tasks execute synchronously in the request process. This blocks the HTTP response until the pipeline finishes.

---

## 5. Running Tests

### 5.1 Full Test Suite

```bash
python manage.py test test -v2
```

**Note:** The test runner creates a temporary database. If using PostgreSQL locally, the DB user needs `CREATEDB` permission:

```bash
sudo -u postgres psql -c "ALTER USER rnaseek CREATEDB;"
```

### 5.2 Specific Test File

```bash
python manage.py test test.test_tusd_hooks -v2
python manage.py test test.test_upload_api -v2
python manage.py test test.test_validators -v2
```

### 5.3 Single Test

```bash
python manage.py test test.test_tusd_hooks.TusdHookViewTests.test_post_finish_creates_file_asset -v2
```

---

## 6. Project Structure

```
rnaseek/
  config/           Django project settings, Celery app, ASGI/WSGI entrypoints
  pipeline/
    views/           HTTP views (api.py for REST, pages.py for templates)
    tasks/           Celery tasks (pipeline tracks, modules)
    stats/           Stage 2 statistics engine (DESeq2, plots)
    templates/       HTML templates
    static/          CSS, JavaScript
    migrations/      Database migrations
    management/      Management commands (purge_expired)
  test/              Test suite (12 files)
  doc/               Documentation
  nginx/             Nginx configuration
  systemd/           systemd service files
  scripts/           Utility scripts (benchmarking)
  media/             User uploads and pipeline outputs (gitignored)
```

---

## 7. Key Configuration

### 7.1 Django Settings

`config/settings.py` auto-detects the environment via `RNASEEK_ENV`:

| Setting      | Development                     | Production          |
| ------------ | ------------------------------- | ------------------- |
| `DEBUG`      | `True`                          | `False`             |
| Database     | SQLite                          | PostgreSQL          |
| `SECRET_KEY` | Hardcoded insecure              | From `.env`         |
| SSL redirect | Disabled                        | Enabled (HSTS)      |
| Celery eager | Configurable (`CELERY_EAGER=1`) | Always async        |
| Logging      | INFO for `pipeline`             | INFO for `pipeline` |

### 7.2 URL Routes

| Path                                         | View                 | Purpose                 |
| -------------------------------------------- | -------------------- | ----------------------- |
| `/`                                          | HomeView             | Landing page            |
| `/tutorials/`                                | TutorialsView        | File format guides      |
| `/workspaces/`                               | WorkspacesView       | Active submissions list |
| `/analysis_submission/new/`                  | NewSubmissionView    | 5-step wizard           |
| `/processing/<uuid>/`                        | ProcessingView       | Real-time progress      |
| `/hub/<uuid>/`                               | CoreHubView          | Results and modules     |
| `/api/submission/create`                     | CreateSubmissionView | Create submission       |
| `/api/submission/delete`                     | DeleteSubmissionView | Delete submission       |
| `/api/upload/chunk`                          | ChunkUploadView      | Legacy chunked upload   |
| `/api/upload/tus-asset`                      | TusAssetLookupView   | Tus asset ID lookup     |
| `/api/webhooks/tus/`                         | TusWebhookView       | tusd webhook (HMAC)     |
| `/api/tusd-hooks/`                           | TusdHookView         | tusd webhook handler    |
| `/api/pipeline/core`                         | CorePipelineView     | Pipeline dispatch       |
| `/api/jobs/<uuid>/`                          | JobStatusView        | Job status polling      |
| `/api/files/<uuid>/`                         | FileAssetDeleteView  | Delete uploaded file    |
| `/api/session/assets`                        | SessionAssetsView    | List session assets     |
| `/api/download/<uuid>`                       | FileDownloadView     | File download           |
| `/api/submissions/<uuid>/modules/<name>/run` | ModuleRunView        | Tier 2 dispatch         |

---

## 8. Common Development Tasks

### Add a New Migration

```bash
python manage.py makemigrations pipeline
python manage.py migrate
```

### Generate Synthetic Test Data

The `debug_payload.py` module generates synthetic `result_payload` for testing the Core Hub UI without running the real pipeline:

```bash
python manage.py shell
>>> from pipeline.debug_payload import generate_debug_payload
>>> payload = generate_debug_payload()
```

### Collect Static Files

```bash
python manage.py collectstatic --noinput
```

### Check for Deployment Issues

```bash
python manage.py check --deploy
```
