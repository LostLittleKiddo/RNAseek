
# 🧬 RNASeek: The Enterprise Masterclass Blueprint

**Version:** 1.1 (Target: March 31, 2026) | **Architecture:** Multi-Tenant Asynchronous Microservices

---

## PHASE 1: The Bare Metal & Infrastructure Architecture

*How the system is built to handle massive scale, gigabyte data streaming, and biological computation without crashing.*

**1. The Application Layer (Dockerized Microservices)**
* **Web Server (Django 5.2):** Served by Daphne (ASGI) to handle synchronous REST API calls and real-time asynchronous WebSocket connections (via Django Channels) for live progress bars. Nginx sits in front as a reverse proxy, handling SSL (Certbot) and static file delivery (WhiteNoise).
* **Message Broker (Redis 7+):** Acts as the high-throughput memory bank handling the queue of biological jobs.
* **Worker Fleet (Celery 5.6):** The heavy lifters. Isolated containers that do not run a web server; they strictly execute Python/R bioinformatics scripts. If a worker hits an Out-Of-Memory (OOM) error while aligning a 40GB genome, Redis simply requeues the job to a surviving worker.

**2. The Storage Layer (POSIX Shared NFS)**
* **The Problem:** FASTQ files are 10–50 GBs. Moving them between cloud buckets (AWS S3) and local workers costs a fortune in egress fees and network latency.
* **The Solution:** A POSIX-compliant Network File System (NFS) mounted directly to `/app/media/` on *every* Docker container.
* **The Result:** The web server accepts the upload and writes it to `/app/media/`. The Celery worker wakes up and reads it from the *exact same path*. Zero data movement. This NFS also holds the 100s of Reference Genomes (44GB each).

**3. The Security Layer (Frictionless Multi-Tenancy)**
* No usernames. No passwords.
* Upon visiting the site, the Django middleware issues a cryptographically signed, `HttpOnly` UUID cookie (`Session_ID`) valid for 14 days.
* Every database row, every file upload, and every Celery job is locked to this UUID.

---

## PHASE 2: The Data Model (Django ORM)

*The relational structure that governs state and prevents data bleeding.*

1.  **`Session`:** The root tenant. Contains `session_id` (UUID) and `expires_at` (14-day TTL).
2.  **`AnalysisSubmission`:** A child of `Session`. Represents a single "batch upload" event. Automatically generates an `upload_dir` folder on the NFS.
3.  **`FileAsset`:** Tracks physical files. Contains `file_role` (e.g., `RAW_FASTQ`, `ALIGNMENT_BAM`, `USER_COUNT_MATRIX`, `CUSTOM_GENOME_FASTA`) and the absolute `local_path` on the NFS.
4.  **`AnalysisJob`:** Tracks background processing. Contains `job_id` (matches Celery task ID), `module_name`, `status` (PENDING, RUNNING, SUCCESS, FAILED), and a `step_progress` JSON field to drive the frontend WebSockets. 
    * **[UPDATE]:** Now includes a `parent_submission` (ForeignKey to `AnalysisSubmission`) and an `is_core_pipeline` (Boolean) field. This isolates Tier 2 module jobs so they only render inside their specific submission hub and do not clutter the global "Active Workflow" UI.
5.  **`ReferenceGenome`:** Tracks local automated genomes. `genome_id`, `species`, `index_path` (for HISAT2/Bowtie), and `annotation_path` (for GTF/GFF).

---

## PHASE 3: The API Facade & Data Ingestion

*How massive data enters the system flawlessly.*

**1. The Chunked Uploader (`/api/upload/chunk`)**
* Browsers crash if you try to upload a 20GB FASTQ file via standard HTTP POST.
* Your UI chunks the files into 5MB binary slices. The Django API receives these slices, sanitizes the filename to prevent path traversal, and dynamically appends the binary bytes (`ab` mode) into the `AnalysisSubmission` NFS folder.

**2. The Master Router (`/api/pipeline/core`)**
* The "Facade Pattern" in action. The frontend UI sends a single JSON payload. Django interprets the biological state and routes the execution matrix.
* **State Routing:**
    * `input_data_type == "fastq"` ➡️ Requires full alignment. Demands paired-end validation.
    * `input_data_type == "alignment"` ➡️ BAM/CRAMs. Skips alignment. Demands GTF annotations.
    * `input_data_type == "matrix"` ➡️ User count matrix. Bypasses Stage 1 entirely.
* **Assay Routing:**
    * `assay_type == "standard_rna"` ➡️ Poly-A routing.
    * `assay_type == "small_rna"` ➡️ microRNA routing.
    * `assay_type == "chip_seq"` ➡️ Epigenomic Peak calling routing.

---

## PHASE 4: The Hub Engine (Stage 1 Multi-Track)

*The raw biological physics layer. Triggered dynamically by the Celery `run_core_pipeline` task based on the API Router.*

* **Track A: Standard Transcriptomics (Poly-A RNA-Seq)**
    * **QC:** `FastQC` and `Trimmomatic` (removes adapter contamination).
    * **Alignment:** `HISAT2` (splice-aware aligner optimized for massive mammalian genomes). Converts SAM to compressed CRAM.
    * **Quantification:** `featureCounts` calculates exact gene-level expression.
* **Track B: Regulatory Transcriptomics (Small RNA / Non-Coding)**
    * **Alignment:** `Bowtie` (optimized for ultra-short 22bp reads). Maps against the specialized `miRBase` database instead of the whole human genome.
* **Track C: Epigenomics (ChIP-seq & DNA Methylation)**
    * **ChIP-seq:** Aligns via `BWA`. Uses `MACS2` to call biological "peaks" where transcription factors are bound to DNA.
    * **Methylation:** Uses `Bismark` to align bisulfite-converted DNA, mathematically decoding C-to-T base pair mutations into methylation beta-values.
* **Universal Output:** All tracks utilize `MultiQC` to sweep the logs and generate interactive HTML quality control reports for the user.

---

## PHASE 5: The Convergence & Normalization (Stage 2)

*Every input type (BAM, FASTQ, or uploaded Matrix) and every Assay type converges into a standardized mathematical matrix.*

**1. Batch Correction (`ComBat-seq`)**
* Uses the R bridge (`rpy2`). If the user's metadata indicates samples were sequenced on different days/machines, `sva::ComBat_seq()` mathematically removes the technical noise while preserving the biological variance.

**2. Statistical Normalization & Outliers (`DESeq2`)**
* Transforms raw counts into normalized expression values using Negative Binomial distributions.
* Calculates Mahalanobis distance across the Principal Components to detect and flag severe statistical outliers.

**3. Automated Annotation Bridge**
* Pings the `MyGene.info` REST API. Appends plain-English gene descriptions and known disease associations to the final DEG (Differentially Expressed Gene) table.

**4. The Plotly UX Generation**
* The Celery worker does the heavy graphics math on the server. It generates the exact X/Y coordinate JSON payloads for PCA, UMAP, PLS-DA, Volcano Plots, MA plots, and Heatmaps. The frontend simple ingests this JSON and renders stunning WebGL interactive plots.

---

## PHASE 6: The Standard Analytical Spokes (Tier 2)

*12 modular micro-pipelines that unlock after Stage 2. Triggered via `/api/modules/{name}/run`.* **[UPDATE]:** Now includes data routing paths for UI inputs and Tier 1 recycled dependencies.

| Module                   | Biological Purpose                                                 | Computational Engine    | Reused Tier 1 Data             | Required Hub UI Input                                                         |
| :----------------------- | :----------------------------------------------------------------- | :---------------------- | :----------------------------- | :---------------------------------------------------------------------------- |
| **A. Alt Splicing**      | Detects structural transcript alterations/exon skipping.           | `IsoformSwitchAnalyzeR` | Aligned BAMs, GTF Annotation   | **Condition Mapping:** Specific experimental groups to compare.               |
| **B. RNA Editing**       | Base-by-base scans of BAMs to find A-to-I mutations/SNPs.          | `REDItools2`            | Aligned BAMs, Reference Genome | **Target Regions:** BED file of coordinates, or "Whole Transcriptome".        |
| **C. Time Series**       | Clusters genes by their temporal expression across time.           | `ImpulseDE2`            | Normalized Expression Values   | **Temporal Metadata:** Timepoints assigned to each sample (e.g., 0h, 12h).    |
| **D. WGCNA**             | Groups highly correlated gene networks into color modules.         | `PyWGCNA`               | Normalized Expression Values   | **Clinical Traits (Optional):** Numeric metadata to correlate with networks.  |
| **E. Pathways**          | Maps upregulated genes against KEGG/PathBank diagrams.             | `gseapy`                | Final DEG Table                | **Database Selection:** Desired pathway database to map against.              |
| **F. Causal Networks**   | Machine learning infers causal gene-to-gene edges.                 | `arboreto` (GRNBoost2)  | Normalized Expression Values   | **Transcription Factors (Optional):** Custom gene list to restrict inference. |
| **G. Protein Interacts** | Fetches physical protein binding networks.                         | STRING-DB API           | Final DEG Table                | **Thresholds:** Confidence score cutoff and max node limit.                   |
| **H. Literature NLP**    | Scans millions of PubMed abstracts for causal biology.             | INDRA Bio API           | Final DEG Table                | **Context Keyword:** Biological/disease context to guide the scan.            |
| **I. Survival**          | Kaplan-Meier curves predicting high/low expression survival.       | `lifelines`             | Normalized Expression Values   | **Clinical Survival Data:** CSV mapping samples to Days/Vital Status.         |
| **J. TCGA Cancer**       | Harmonizes user data against public GDC tumor cohorts.             | `TCGAbiolinks`          | Normalized Expression Values   | **Target Cohort:** Dropdown selection of the public tumor cohort.             |
| **K. Biomarkers**        | Flags FDA-approved diagnostic/prognostic biomarkers.               | MarkerDB API            | Final DEG Table                | **Disease Context:** Target condition for cross-referencing.                  |
| **L. MOFA / DIABLO**     | Mathematically fuses RNA-seq with ChIP-seq to find latent factors. | `mofapy2` / `mixOmics`  | Normalized Expression Values   | **Secondary Omics Upload:** A second normalized matrix matched to samples.    |

---

## PHASE 7: The Predictive Single-Cell & Spatial Gateway

*The bleeding edge of bioinformatics. Converting bulk fluid into physical tissue mapping.*

**Tier 3: The Deconvolution Engine**
* **The Math:** Takes the normalized bulk matrix and a Single-Cell Reference Atlas. Runs `DestVI` or `BayesPrism` to decompose bulk samples into pseudo-single-cells.
* **The Output:** A stacked bar chart of cell fractions (e.g., 20% Macrophage, 80% T-Cell) and an `.h5ad` AnnData matrix.

**Tier 4: The Advanced Spatial Spokes**
* **Trajectory Inference:** `scanpy` uses the pseudo-cells to map developmental paths (e.g., watching a stem cell turn into a neuron in virtual space).
* **Spatial Mapping:** `Tangram` (Deep Learning) maps the imputed pseudo-cells onto a user-uploaded H&E histology tissue image.
* **Spatial Autocorrelation:** `Squidpy` (Moran's I) identifies physical "hot spots" of gene expression across the tissue.

---

## PHASE 8: DevOps, Janitorial & Server Security

*How the system survives in production without human intervention.*

**1. The Auto-Purge Janitor**
* A Celery Beat task runs `python manage.py purge_expired` every night at 2:00 AM.
* It searches for any `Session` where `expires_at` is past the current time.
* It executes `shutil.rmtree()` on the user's specific `/app/media/sessions/{uuid}/` directory, physically obliterating 50GB+ of their BAMs/FASTQs to prevent disk hoarding. Django Cascade deletion wipes the DB rows.

**2. CI/CD & Observability**
* **GitHub Actions:** Runs a lightweight integration test (`test_e2e.py`) using a tiny yeast dataset on every push.
* **Prometheus & Grafana:** Monitors the Celery queue depth and the RAM usage of the worker nodes to ensure `HISAT2` doesn't cause OOM kernel panics.
* **The Gitignore:** Explicitly blocks `.fastq.gz`, `.bam`, `.cram`, `.sam`, `.h5ad`, `.ht2`, and `.RData` files to prevent repo bloat.
