## PAGES
### 1. Home & About Page

*This is the static entry point that issues the initial session tracking.*

* **Background Action:** When the user lands here, the frontend calls `GET /api/session/init`. If no cookie exists, Django creates a `Session` row and returns an `HttpOnly` 14-day `Session_ID` cookie.
* **Hero Section:** Title, brief description of the platform's capabilities (bulk to single-cell upcycling), and three large navigation buttons: "Start New Analysis", "My Workspaces", and "Tutorials".
* **Feature Grid:** Static informational cards outlining the 12 Standard Modules and the Advanced Spatial Spokes.

### 2. Tutorials & Documentation Page

*Static educational content explaining data formatting.*

* **File Format Guide:** Text explaining that raw reads must be `.fq.gz` or `.fastq.gz`.
* **Metadata Guide:** An example table showing how to map Treatment vs. Control, Batch IDs, and Timepoints.
* **Workflow Diagram:** A visual representation of the Mandatory Hub into the Deconvolution Gateway.

### 3. Active Workspaces (14-Day History)

*A dashboard tracking all jobs tied to the user's current session cookie.*

* **Warning Banner:** A prominent notice stating that all sessions and files are automatically purged by the system after 14 days.
* **Submissions Table:** * *Data Source:* Populated by querying the backend for `AnalysisJob` rows tied to the session.
* *Columns:* Job ID, Module Name, Created Date, and Status ('PENDING', 'RUNNING', 'SUCCESS', 'FAILED').
* *Action:* A "View Dashboard" button on rows marked 'SUCCESS' that routes the user to the Core Hub for that specific dataset.



### 4. Stage 1 Setup Wizard (New Submission)

*The critical data ingestion page corresponding to State 0 (Upload).*

* **Data Upload Zone (Input):** A drag-and-drop area powered by `Resumable.js` or `Uppy`.
* *Submission:* POSTs 5MB chunks to `/api/upload/chunk` using `multipart/form-data`.


* **Reference Selector (Input):** A dropdown menu to select the Reference Genome (e.g., *Homo sapiens GRCh38*).
* **Metadata Editor (Input):** An interactive UI table where users map their uploaded FASTQ files to biological conditions, optional Batch IDs, and Timepoints.
* **Submit Button (Action):** Clicking this triggers `POST /api/pipeline/core` with the `metadata_mapping` JSON payload to fire the Celery worker.

### 5. The Processing Waiting Room

*Corresponds to State 1 (Processing).*

* **Status Poller (Element):** A UI component that calls `GET /api/jobs/{job_id}` every 3 seconds to check the background Celery task.
* **Progress Indicators:** Visual steps showing the execution sequence: FastQC, Trimmomatic, HISAT2 alignment, and featureCounts quantification.

### 6. The Core Hub (Interactive Dashboard)

*Unlocked in State 2. This is the main analytical workspace.*

* **QC & Base Downloads (Outputs):** Buttons to download `.cram` files, the Raw Count Matrix (`.csv`), the Normalized Count Matrix (`.csv`), the DEG Table (`.csv`), and the MultiQC report.
* **Core Visualizations (Outputs):** Interactive WebGL/Plotly containers displaying PCA, UMAP, Volcano Plots, MA Plots, and DEG heatmaps driven by JSON payloads from the backend.
* **Standard Module Grid (Inputs/Actions):** 12 clickable cards to trigger specific downstream analyses via `POST /api/modules/{name}/run`. When clicked, they open a modal for required inputs:
* *WGCNA:* Dropdown for Clinical Trait Selection.
* *Survival:* Dropdowns for Survival Time and Censoring, plus a Target Gene search bar.
* *MOFA/DIABLO:* Uploader for a Secondary Omics Matrix (`.csv`/`.tsv`).


* **The Deconvolution Gateway Card:** A prominent card to trigger the single-cell upcycling.
* *Input:* Dropdown for Single-Cell Reference Atlas and a High-Resolution Toggle.
* *Output:* Stacked bar charts showing Cell Composition. Generating the high-resolution `.h5ad` file unlocks the advanced workspace.



### 7. Advanced Single-Cell & Spatial Workspace

*Corresponds to State 4. Unlocked only when the frontend queries `/api/session/assets` and detects the `'H5AD_PSEUDO'` file.*

* **Spoke A - Trajectory Inference:**
* *Input:* Dropdown for Root Cell Selection.
* *Output:* Interactive PAGA / Pseudotime WebGL graphs.


* **Spoke B - Spatial Annotation:**
* *Input:* Dropdown for a Generic Spatial Reference Template, or a file uploader for a custom H&E image (`HE_IMAGE_USER`).
* *Output:* Spatial Projection Overlay showing predicted cells mapped onto the tissue slide.


* **Spoke C - Spatial Autocorrelation:**
* *Input:* Gene Search Input text bar (e.g., *CXCL10*).
* *Output:* Spatial Gene Heatmaps and a downloadable Moran's I Statistics Table (`.csv`).


## Flow
The frontend workflow is designed as a seamless, linear journey managed by a frictionless 14-day session cookie. When a user arrives at the Home page, they can either initiate a new pipeline via the Stage 1 Setup Wizard or access their recent, ongoing projects via Active Workspaces. If starting a new analysis, they upload raw sequencing files, select a reference genome, and map metadata in the Setup Wizard, which instantly transitions them to the Processing Waiting Room to monitor real-time backend execution logs. Once the heavy core compute finishes—or if the user simply clicks a previously completed job from the Active Workspaces menu to "rehydrate" a past session—they are routed directly into the Core Hub. This Hub serves as their primary interactive dashboard, allowing them to instantly review baseline visualizations (like PCA and Volcano plots), download normalized count matrices, and trigger secondary analytical modules without needing to re-run any Stage 1 processing. Finally, from this Hub, users can choose to execute the Deconvolution Gateway, which mathematically upcycles their bulk data and unlocks the Advanced Single-Cell & Spatial Workspace for high-resolution trajectory inference and physical spatial mapping.