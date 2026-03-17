# RNAseek Pipeline Blueprint

Edited: March 5, 2026 12:50 PM
Created: March 3, 2026 4:46 PM
To Review: No
Favorite: No
Archive: No

---

# RNASeek: Enterprise Execution Blueprint

## 1. System Requirements & Scalable Infrastructure

**The Concept:** A highly decoupled, asynchronous architecture utilizing API-first microservices for absolute fault tolerance.

- **Microservices/Modular Design:** The web server (Django), the message broker (Redis), and the worker nodes (Celery) run in isolated Docker containers orchestrated by Docker Swarm or Kubernetes.
- **Storage (Shared File System):** To handle massive FASTQ files across multiple worker nodes without cloud egress fees or complex networking, the cluster utilizes a **POSIX-compliant Shared Network File System (NFS)** or a high-throughput SSD SAN mounted directly to `/app/media/` on all containers.
- **Multi-Tenant User Management (Frictionless):** No login required. The system issues a secure, `HttpOnly` 14-day `Session_ID` cookie. This ID acts as the "Tenant ID," isolating all database rows and file access per user.
- **Asynchronous Job Queues:** Backed by Redis 7+. If a worker crashes (e.g., OOM error), the broker automatically requeues the job to a healthy node.
- **CI/CD & Monitoring:** GitHub Actions automatically tests Python/R scripts. Prometheus scrapes worker metrics (CPU/RAM usage), visualized in Grafana.

---

## 2. Database Structure (Flat & Minimal)

**Purpose:** Tracks the 14-day lifecycle, standard local server paths on the NFS (`/app/media/sessions/`), and background job states.

### Table A: `Session`

*Tracks the anonymous user via browser cookies.*

- `session_id` (UUID, Primary Key)
- `created_at` (Datetime, auto-now-add)
- `expires_at` (Datetime) -> *Set to `created_at + 14 days`.*

### Table B: `FileAsset`

*Stores the absolute paths to files living on the NFS so Celery workers know exactly where to look.*

- `id` (UUID, Primary Key)
- `session` (ForeignKey to `Session`, Cascade Delete)
- `file_role` (String) -> *Choices: 'RAW_FASTQ', 'COUNT_MATRIX', 'H5AD_PSEUDO', 'HE_IMAGE_USER', 'HE_IMAGE_GENERIC'.*
- `local_path` (String) -> *e.g., `/app/media/sessions/{session_id}/raw/sample1.fq.gz`*
- `is_user_uploaded` (Boolean) -> *True for raw data, False for pipeline outputs.*

### Table C: `AnalysisJob`

*Tracks the background Celery/Redis tasks.*

- `job_id` (UUID, Primary Key) -> *Matches the Celery Task ID.*
- `session` (ForeignKey to `Session`, Cascade Delete)
- `module_name` (String) -> *e.g., 'CORE_PIPELINE', 'WGCNA', 'DECONVOLUTION'.*
- `status` (String) -> *Choices: 'PENDING', 'RUNNING', 'SUCCESS', 'FAILED'.*
- `result_payload` (JSONB) -> *Stores the final plot coordinates (e.g., `{ "x": [...], "y": [...], "p_val": 0.04 }`) to be sent to the frontend WebGL charts.*

---

## 3. API System (Backend-to-Frontend Bridge)

**Purpose:** A REST API built in Django REST Framework. The frontend uses these endpoints to upload data in chunks, trigger background processing, and poll for results without freezing the browser.

| **Endpoint** | **Method** | **Payload / Action** | **Purpose & Backend Logic** |
| --- | --- | --- | --- |
| `/api/session/init` | `GET` | *None* | **Init:** Checks if user has a `session_id` cookie. If not, generates a UUID, creates a DB row, and sets an `HttpOnly` cookie. |
| `/api/upload/chunk` | `POST` | `multipart/form-data` (5MB File Chunk) | **Chunked Upload:** Receives 5MB slices of massive FASTQ files. Django appends the chunk to a temporary file on the NFS. When the final chunk arrives, creates a `FileAsset` row. |
| `/api/pipeline/core` | `POST` | `{"metadata_mapping": [...]}` | **Start Hub:** Triggers Core Pipeline. Creates an `AnalysisJob`, pushes to Redis queue, returns `job_id`. |
| `/api/modules/{name}/run` | `POST` | `{"params": {...}}` | **Start Spoke:** Triggers specialized modules (WGCNA, Deconvolution). Returns `job_id`. |
| `/api/jobs/{job_id}` | `GET` | *None* | **Polling:** Frontend calls this every 3 seconds. Returns `{"status": "RUNNING"}` or `{"status": "SUCCESS", "payload": {...}}`. |
| `/api/session/assets` | `GET` | *None* | **State Check:** Returns a list of `FileAsset` roles for the session. *Crucial for unlocking UI elements.* |

---

## 4. Frontend Connection & UX State Logic

**Purpose:** A React/Vue application that controls what the user can click based on backend files.

**The State Machine Flow:**

1. **State 0 (Upload):** User uploads files. Frontend handles chunking via `Resumable.js` or `Uppy`.
2. **State 1 (Processing):** Frontend polls `/api/jobs/{job_id}`. Displays a progress bar. All modules locked.
3. **State 2 (The Hub Unlocked):** Core job completes. The Standard Modules (WGCNA, Pathways, etc.) unlock.
4. **State 3 (The Deconvolution Trigger):** User clicks the optional **"Cell Deconvolution & Imputation"** module card.
5. **State 4 (The Advanced Unlocks):** The backend finishes Deconvolution and saves the `'H5AD_PSEUDO'` file asset. Frontend queries `/api/session/assets`, sees the `.h5ad` file exists, and instantly illuminates and unlocks the Single-Cell/Spatial Spokes (A, B, C).

---

## 5. The Mandatory Hub (Core Pipeline)

*Every session must pass through this high-rigor pipeline to validate data quality before unlocking the platform's advanced modules. It is strictly sequential.*

### Stage 1: Alignment, QC & Quantification (Heavy Compute)

**Purpose:** Convert raw sequencer outputs into a mathematical count matrix representing gene expression levels, while aggressively compressing files to save server disk space.

- **Concept Inputs:** Raw FASTQ files (`.fq.gz`), pre-indexed Reference Genome, GTF Annotation file.
- **Execution Sequence (Celery Worker):**
    1. **Quality Control:** Run `fastqc` on all `.fq.gz` files to check Phred quality scores and adapter contamination.
    2. **Adapter Trimming:** Run `trimmomatic` to physically clip Illumina adapters and drop low-quality reads.
    3. **Splice-Aware Alignment & Compression:** Run `hisat2 -p 8 --dta` against the Reference Genome. The `-dta` flag is absolutely critical here as it instructs the aligner to report transcripts tailored for downstream alternative splicing modules.
    4. **Piping to Disk Saver:** Pipe the `hisat2` output directly into `samtools view -C -T [genome.fa]`. This avoids writing massive `.sam` or `.bam` files to the disk entirely, outputting a highly compressed `.cram` file.
    5. **Quantification:** Run `featureCounts` across all `.cram` files to count exactly how many reads hit each gene.
    6. **Log Aggregation:** Run `multiqc` to sweep the folder and combine all `fastqc`, `hisat2`, and `featureCounts` logs into one JSON payload.
- **Outputs Saved to NFS:** `.cram` files, Raw Count Matrix (`.csv`).

### Stage 2: Batch Correction, Statistics & DEG Testing

**Purpose:** Remove technical noise (like samples being sequenced on different days) and apply rigorous statistical modeling to find which genes are truly driving the condition.

- **Concept Inputs:** Raw Count Matrix, User's Metadata (Treatment vs. Control, Batch IDs, Timepoints).
- **Execution Sequence (Python/R Bridge via `rpy2`):**
    1. **Data Cleaning:** Filter out genes with zero or ultra-low counts across all samples.
    2. **Batch Correction:** Execute `sva::ComBat_seq()` in R using the metadata's batch column to mathematically remove technical noise while preserving biological variance.
    3. **Outlier Detection:** Python calculates the Mahalanobis distance on the principal components. Samples outside the 95% confidence interval are flagged in the database.
    4. **Differential Expression:** Execute `DESeq2` in R. If the metadata features a continuous variable (like `timepoint`), the backend dynamically switches the statistical test to the Likelihood Ratio Test (LRT).
- **Outputs Saved to NFS:** Normalized Matrix (`.csv`), DEG Results Table (`.csv`), Plotly JSON payload for PCA, UMAP, and Volcano plots.

---

## 6. The Predictive Deconvolution Gateway (Optional Hub)

*This is the critical pivot. The user can optionally trigger this module to computationally "upcycle" their bulk data into a single-cell format.*

- **Concept Inputs:** Normalized Bulk Matrix + Single-Cell Reference Atlas.
- **Process:** Mathematically unmixes the "smoothie" of bulk RNA into its component "fruits".
- **Execution:** Python worker runs `DestVI` or `BayesPrism` to extract imputed individual cell profiles.
- **Outputs:** 1. A stacked bar chart showing cell fraction percentages per sample.
    
    2. A highly complex `.h5ad` AnnData matrix of "pseudo-cells" saved to the NFS. **(The generation of this file triggers the UI to unlock Spokes A, B, and C).**
    

---

## 7. The Advanced Unlocks (Spatial & Single-Cell Spokes)

*These modules remain locked until the Deconvolution Gateway successfully generates the `.h5ad` pseudo-cell matrix.*

- **Spoke A: Trajectory Inference (Pseudotime)**
    - **Inputs:** Imputed Pseudo-scRNA `.h5ad` matrix on the NFS.
    - **Execution:** Runs `scanpy` (`sc.tl.paga` and `sc.tl.dpt`) to map the continuous lineage of the pseudo-cells.
    - **Outputs:** Interactive WebGL trajectory graphs.
- **Spoke B: Spatial Annotation & Mapping (Dual-Option)**
    - **Inputs:** Pseudo-scRNA matrix + Generic Spatial Reference Template (stored on the server) OR an uploaded H&E image.
    - **Execution:** Deep learning (`Tangram` in Python) calculates the probability of each imputed cell residing at specific $(X, Y)$ pixels on the tissue image.
    - **Outputs:** Interactive visualization showing imputed cells mapped onto a tissue slide.
- **Spoke C: Spatial Autocorrelation**
    - **Inputs:** Spatially-mapped Pseudo-scRNA matrix.
    - **Execution:** Runs `squidpy` (`sq.gr.spatial_autocorr`) to calculate Moran's I on the mapped coordinates.
    - **Outputs:** Spatial heatmaps highlighting regionally specific gene expression.

---

## 8. The Standard Spokes (Specialized Modules)

*These 12 modules unlock immediately after the Mandatory Hub (Stage 2) completes. They can be triggered concurrently via the asynchronous job queue.*

### Category 1: Transcriptome Profiling

- **Module A: Alternative Splicing & Exon Usage**
    - **Process:** Detect transcripts that physically alter their structure between conditions and predict if the resulting protein loses a functional domain.
    - **Execution:** R script via `rpy2` calls `IsoformSwitchAnalyzeR`. Imports StringTie quantifications, runs DEXSeq for differential usage, and predicts open reading frames (ORFs).
    - **Outputs:** "Switch Plots" showing exact exon structures side-by-side, and tables of lost protein domains.
- **Module B: RNA Editing & SNP Detection**
    - **Process:** Scan RNA reads base-by-base to find high-confidence mutations or A-to-I editing events not present in the DNA code.
    - **Execution:** Trigger `REDItools2` (Python parallelized script). Uses SAMtools mpileup under the hood. Filters against dbSNP.
    - **Outputs:** VCF files and mutation frequency tables.

### Category 2: Systems Biology & Networks

- **Module D: Co-Expression Analysis (WGCNA)**
    - **Process:** Group highly correlated genes into color-coded modules, then test if any specific module strongly correlates with a clinical trait.
    - **Execution:** Run `PyWGCNA`. Constructs a topological overlap matrix (TOM), clusters via dynamic tree cut, and correlates "Module Eigengenes" with the metadata.
    - **Outputs:** Hierarchical clustering dendrogram and a Module-Trait heatmap.
- **Module E: Pathway Enrichment (GSEA/ORA)**
    - **Process:** Determine if the significantly altered genes biologically group into known pathways.
    - **Execution:** Pass ranked genes to `gseapy.prerank()`. Cross-reference output with the PathBank REST API.
    - **Outputs:** Interactive dot plots, ridge plots, and mapped PathBank pathway diagrams.
- **Module F: Causal Network Inference & STRING PPI**
    - **Process:** Infer directed gene regulatory networks and physical protein-protein interactions.
    - **Execution:** Parallel execution. Python `arboreto` runs GRNBoost2 for directed edges. Backend sends HTTP POST to `string-db.org/api/json/network`.
    - **Outputs:** JSON edge/node lists formatted specifically for `Cytoscape.js` network rendering.
- **Module G: Automated Pathway Reconstruction (Literature Mining)**
    - **Process:** Use NLP to read millions of published scientific papers and physically reconstruct signaling pathways based on literature evidence.
    - **Execution:** Query the `INDRA` Python API to scan PubMed and extract mechanistic relationships.
    - **Outputs:** Directed graph with clickable edges linking to PubMed abstracts.

### Category 3: Clinical & Translational

- **Module H: Survival Prediction (Kaplan-Meier)**
    - **Process:** Split patients into "High" and "Low" expressors of a specific gene to see if that gene impacts survival time.
    - **Execution:** Python `lifelines` library. Fits `KaplanMeierFitter()` to both cohorts and calculates significance via `lifelines.statistics.logrank_test()`.
    - **Outputs:** Kaplan-Meier survival curve coordinates and Log-Rank p-value.
- **Module I: TCGA Disease Integration**
    - **Process:** Download massive public tumor datasets, harmonize them with the user's data, and determine which cancer subtype the user's samples most resemble.
    - **Execution:** R script via `TCGAbiolinks` downloads the cohort. Runs Combat-seq to remove batch effects, and computes a joint PCA.
    - **Outputs:** Joint PCA plots and comparative DEG tables.
- **Module J: Biomarker Discovery**
    - **Process:** Verify if differentially expressed genes are already FDA-approved clinical biomarkers.
    - **Execution:** Python queries the MarkerDB REST API.
    - **Outputs:** Filtered table highlighting genes and their biomarker type (Diagnostic, Predictive, Prognostic).

### Category 4: Multi-Omics

- **Module K: Multi-Omics Factor Analysis (MOFA)**
    - **Process:** Find latent factors that explain the shared biological variance across different molecular layers.
    - **Execution:** Python builds a `MuData` object and feeds it to `mofapy2`.
    - **Outputs:** Variance Explained plots and Feature Weight plots.
- **Module L: Supervised Multi-Omics (DIABLO)**
    - **Process:** Find a highly correlated multi-omics "signature" that perfectly predicts the clinical outcome.
    - **Execution:** R script via `mixOmics` package runs `block.splsda` (DIABLO).
    - **Outputs:** Circos plots and predictive AUROC curves.

---

## 9. External Data Source & API Directory

*For your Backend Engineers: This is exactly where the platform reaches out to the broader internet to fetch annotations, networks, and reference data.*

| **Target System / Module** | **Platform / API Provider** | **Endpoint / Location / Method** | **Purpose in Platform** |
| --- | --- | --- | --- |
| **Stage 1: Genomes** | **Ensembl / UCSC Genomes** | `ftp.ensembl.org/pub/release-*/fasta/`
`ftp.ensembl.org/pub/release-*/gtf/` | Celery Beat background task downloads the raw `.fa` and `.gtf` files to build the HISAT2 indexes. |
| **Stage 2: Gene IDs** | **MyGene.info** | `POST https://mygene.info/v3/query` | Takes the raw Ensembl IDs (e.g., ENSG000...) from DESeq2 and queries the API to append human-readable gene symbols and biological summaries. |
| **Module E: Pathways** | **PathBank API** | `GET https://pathbank.org/api/`
*(Often handled natively via `gseapy`)* | Cross-references the GSEA enrichment output to fetch the coordinate data for drawing biological pathway diagrams. |
| **Module F: Networks** | **STRING DB** | `POST https://string-db.org/api/json/network` | Submits the top 500 DEGs to get physical protein-protein interactions. Required parameters: `identifiers` (list of genes), `species` (e.g., 9606 for Human). |
| **Module G: Literature** | **INDRA API** | `POST http://api.indra.bio:8000/assemblers/` | Submits a list of genes. INDRA scans PubMed abstracts via NLP and returns JSON containing causal mechanisms and the PubMed IDs for citation mapping. |
| **Module I: TCGA Data** | **GDC Data Portal** | Native R API: `TCGAbiolinks::GDCquery()` | Backend script dynamically pings the NCI Genomic Data Commons to securely download public tumor matrix arrays for direct PCA comparisons. |
| **Module J: Biomarkers** | **MarkerDB** | `GET https://markerdb.ca/api/v1/markers/` | Matches the DEG list against the database to flag if any upregulated gene is already an FDA-approved clinical diagnostic biomarker. |

---

## 10. The Auto-Purge Janitor

- **Logic:** A Celery Beat scheduled task runs every night at 2:00 AM.
- **Execution:**Python
    
    `import shutil
    from django.utils import timezone
    
    expired_sessions = Session.objects.filter(expires_at__lte=timezone.now())
    for session in expired_sessions:
        # 1. Wipe local NFS directory completely to ensure zero data hoarding
        shutil.rmtree(f'/app/media/sessions/{session.session_id}/', ignore_errors=True)
        # 2. Wipe DB rows (Cascade handles AnalysisJobs and FileAssets)
        session.delete()`