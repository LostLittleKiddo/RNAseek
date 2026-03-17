# RNAseek Pipeline User Input

Edited: March 5, 2026 12:54 PM
Created: March 5, 2026 12:54 PM
To Review: No
Favorite: No
Archive: No

# RNASeek: Complete User Input Specification

## 1. The Mandatory Hub (Core Pipeline Inputs)

*To initiate the platform and unlock the dashboard, the user MUST provide these inputs during the initial Setup Wizard.*

### 1.1. Raw Sequencing Data

- **Mandatory:** **Raw Read Files**
    - *Format:* `.fq.gz` or `.fastq.gz` (Compressed FASTQ).
    - *Detail:* Users must upload at least two groups of samples (e.g., 3 Control, 3 Treatment) to perform statistical testing. The frontend handles chunking these large files to the server.
    - *Optional:* **Paired-End vs. Single-End Toggle.** (Usually auto-detected by the frontend, but users can explicitly flag if their reads are Forward/Reverse pairs like `R1.fq.gz` and `R2.fq.gz`).

### 1.2. Genomic References

- **Mandatory:** **Reference Genome Selection**
    - *Format:* Dropdown Menu selection (e.g., *Homo sapiens GRCh38*, *Mus musculus GRCm39*).
    - *Detail:* Pings the backend Ensembl/UCSC database index. Determines the `.fa` and `.gtf` files the aligner will use.

### 1.3. Experimental Metadata

- **Mandatory:** **Condition Mapping (The Primary Contrast)**
    - *Format:* Interactive UI table or uploaded `.csv`.
    - *Detail:* The user must assign every uploaded FASTQ file to a biological group (e.g., `Control`, `Treated`, `Disease`).
- **Optional:** **Batch ID Mapping**
    - *Format:* Categorical string mapping (e.g., `Batch_1`, `Batch_2`).
    - *Detail:* If entered, the backend automatically triggers `sva::ComBat_seq()` in Stage 2 to remove technical sequencing noise.
- **Optional:** **Timepoint Mapping**
    - *Format:* Continuous numerical mapping (e.g., `Day 0`, `Day 7`, `Day 14`).
    - *Detail:* If entered, triggers the Likelihood Ratio Test (LRT) in DESeq2 instead of the standard Wald test for time-series analysis.

---

## 2. The Predictive Deconvolution Gateway (Optional Hub)

*The user opts into this step to mathematically unmix their bulk data and unlock the Single-Cell / Spatial tools.*

### 2.1. Deconvolution Parameters

- **Mandatory (to run module):** **Single-Cell Reference Atlas Selection**
    - *Format:* Dropdown Menu.
    - *Detail:* User selects the tissue-specific atlas that matches their sample (e.g., *Human PBMC scRNA Atlas*, *Mouse Brain scRNA Atlas*). The backend uses this to train the `DestVI` model.
- **Optional:** **High-Resolution Toggle**
    - *Format:* UI Switch.
    - *Detail:* User chooses whether they just want the simple cell-fraction bar charts (Fast) or if they want to generate the massive `.h5ad` pseudo-cell matrix to unlock the Spatial tools (Compute Heavy).

---

## 3. The Advanced Unlocks (Spatial & Single-Cell Spokes)

*These inputs become available only if the user successfully generated the `.h5ad` file in the Deconvolution Gateway.*

### 3.1. Spoke A: Trajectory Inference (Pseudotime)

- **Optional:** **Root Cell Selection**
    - *Format:* Dropdown of predicted cell types.
    - *Detail:* User selects which cell type represents "Time Zero" (e.g., *Stem Cell* or *Healthy Cell*). `scanpy` uses this origin point to draw the developmental timeline graphs.

### 3.2. Spoke B: Spatial Annotation & Mapping

- **Mandatory (to run module):** **Spatial Reference Template**
    - *Format:* User has two choices:
        1. **Select Generic Template:** Dropdown (e.g., *Generic Human Lymph Node Slice*).
        2. **Upload Custom H&E Image:** Upload a `.jpg`, `.png`, or `.tiff` of a physical tissue histology slide (`HE_IMAGE_USER`).
    - *Detail:* The deep learning `Tangram` algorithm uses this canvas to project the pseudo-cells onto physical coordinates.

### 3.3. Spoke C: Spatial Autocorrelation

- **Optional:** **Gene Search Input**
    - *Format:* Search bar (Text string).
    - *Detail:* User types a specific gene name (e.g., *CXCL10*) to render its physical spatial heatmap over the tissue image.

---

## 4. The Standard Spokes (Specialized Modules)

*These modules become available immediately after the Mandatory Hub finishes. Users trigger these individually.*

### Category 1: Transcriptome Profiling

- **Module A (Alternative Splicing):**
    - *Inputs:* None required. Auto-runs based on Core Pipeline alignments.
- **Module B (RNA Editing / SNPs):**
    - *Optional Input:* **Variant Filter Toggle** (e.g., "Exclude known dbSNP variants" to only find novel mutations).

### Category 2: Systems Biology & Networks

- **Module D (WGCNA):**
    - *Mandatory Input:* **Clinical Trait Selection** (Dropdown from Metadata). The user must select which metadata column (e.g., *Tumor Size*, *Blood Pressure*) to correlate with the gene modules.
- **Module E (Pathway Enrichment):**
    - *Optional Input:* **Database Selection** (Checkboxes for *PathBank*, *KEGG*, *Reactome*).
    - *Optional Input:* **P-Value Cutoff** (Slider, default `0.05`).
- **Module F (Causal Networks & STRING):**
    - *Optional Input:* **Node Limit** (Integer, default `500`). Limits how many top DEGs are sent to the STRING API to prevent "hairball" network graphs.
- **Module G (Literature Mining):**
    - *Optional Input:* **Gene Target List** (Text box, comma-separated). User can override the default top DEGs and paste exactly which 10-20 genes they want INDRA to scan PubMed for.

### Category 3: Clinical & Translational

- **Module H (Survival Prediction / Kaplan-Meier):**
    - *Mandatory Input:* **Survival Time Column** (Dropdown from Metadata mapping to `time_to_event`, e.g., Days to Death).
    - *Mandatory Input:* **Censoring Column** (Dropdown from Metadata mapping to `event_occurred`, e.g., 1=Dead, 0=Alive).
    - *Mandatory Input:* **Target Gene** (Search bar to type the gene they want to test survival against, e.g., *TP53*).
- **Module I (TCGA Disease Integration):**
    - *Mandatory Input:* **TCGA Cohort Selection** (Dropdown menu, e.g., *TCGA-BRCA (Breast)*, *TCGA-LUAD (Lung)*). Triggers backend to download this specific public dataset.
- **Module J (Biomarker Discovery):**
    - *Optional Input:* **Biomarker Type Filter** (Checkboxes: *Diagnostic*, *Prognostic*, *Predictive*).

### Category 4: Multi-Omics

- **Modules K & L (MOFA / DIABLO):**
    - *Mandatory Input:* **Secondary Omics Matrix**
        - *Format:* `.csv` or `.tsv` count matrix.
        - *Detail:* User must upload a second dataset matching the exact sample IDs of their RNA-seq data (e.g., a DNA Methylation matrix or Proteomics matrix).
    - *Mandatory Input (DIABLO only):* **Target Condition** (Dropdown selecting which clinical outcome the supervised model should try to predict).