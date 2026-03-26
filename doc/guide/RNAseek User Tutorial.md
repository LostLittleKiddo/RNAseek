# RNAseek User Tutorial

**Version:** 1.0 | **Last Updated:** March 24, 2026

---

## Table of Contents

1. [Getting Started](#1-getting-started)
2. [Uploading Data](#2-uploading-data)
3. [Viewing Results](#3-viewing-results)

---

## 1. Getting Started

### 1.1 No Account Required — Just Open and Go

RNAseek is designed to remove every barrier between you and your analysis. There are **no usernames, no passwords, and no registration forms**. The moment you open the platform in your browser, a secure, private session is created for you automatically.

Behind the scenes, RNAseek assigns your browser a **cryptographically random session ID** (a UUID) stored as a secure, HttpOnly cookie. This ID links your uploads, pipeline runs, and results to your browser session — and only your browser session. No one else can access your data.

### 1.2 Your 14-Day Session Window

Your session — and all data associated with it — remains **active for 14 days** from the moment it is created. During that window you can:

- Upload new datasets and launch analyses at any time.
- Return to the platform and pick up exactly where you left off.
- Download any results, reports, or visualizations you have generated.

After 14 days, the session expires and an automated janitor permanently deletes all uploaded files and results from the server. **There is no way to recover data after expiration**, so be sure to download anything you need before your session ends.

> **Tip:** Bookmark the RNAseek URL in the same browser you used for your first visit. Your session cookie is browser-specific — opening RNAseek in a different browser or in a private/incognito window will start a brand-new session.

### 1.3 What You Need Before You Begin

Before starting an analysis, gather the following:

| Item                     | Required? | Details                                                                                                                   |
| :----------------------- | :-------: | :------------------------------------------------------------------------------------------------------------------------ |
| **Raw sequencing reads** |  **Yes**  | `.fq.gz` or `.fastq.gz` files (compressed FASTQ). At least two experimental groups (e.g., 3 Control + 3 Treated).         |
| **Reference genome**     |  **Yes**  | Select from 11 pre-indexed genomes (Human, Mouse, Rat, Zebrafish, and more), or upload your own FASTA + GTF/GFF.          |
| **Condition mapping**    |  **Yes**  | A table or CSV that assigns every sample to a biological group (e.g., Control vs. Treated).                               |
| **Batch IDs**            | Optional  | If your samples were sequenced in different batches, include a Batch column to trigger automatic batch-effect correction. |
| **Timepoints**           | Optional  | For time-course experiments, include a Timepoint column to switch DESeq2 to a Likelihood Ratio Test.                      |

If you already have aligned BAM/CRAM files or a pre-computed count matrix, you can skip the alignment step entirely — see [Section 2.5](#25-alternative-entry-points-bam-cram-or-count-matrix) for details.

### 1.4 Supported Analysis Types (Assay Tracks)

RNAseek supports four core assay types. Choose the one that matches your experiment during the Setup Wizard:

| Assay                         | Best For                      | Aligner                  | Key Tools                          |
| :---------------------------- | :---------------------------- | :----------------------- | :--------------------------------- |
| **Standard RNA-Seq** (Poly-A) | Gene expression profiling     | HISAT2 (splice-aware)    | featureCounts, DESeq2              |
| **Small RNA / miRNA**         | Regulatory RNA quantification | Bowtie (miRBase indices) | samtools idxstats, DESeq2          |
| **ChIP-seq**                  | Histone/TF binding sites      | BWA MEM                  | MACS2 peak calling, featureCounts  |
| **DNA Methylation**           | Bisulfite sequencing          | Bismark                  | methylKit differential methylation |

For **microbial / bacterial transcriptomics**, upload an unannotated bacterial FASTA and RNAseek will automatically dispatch it to a local BASys2 engine. BASys2 generates complete structural, operon, and metabolome annotations in roughly 10 seconds — no manual annotation required.

### 1.5 Quick-Start Walkthrough

Here is the end-to-end workflow at a glance:

```
Upload Files  →  Configure Metadata  →  Select Genome  →  Launch Pipeline
     ↓                                                          ↓
  Chunked       ┌──────────────────────────────────────────────────┐
  transfer      │  Stage 1: QC, Trimming, Alignment, Quantification │
  (25 MB slices,│  Stage 2: Normalization, DEG Testing, Plots       │
                └──────────────────────────────────────────────────┘
                                        ↓
                              Core Hub (Results)
                                        ↓
                     Tier 2 Modules (WGCNA, Pathways, …)
```

1. **Navigate** to RNAseek in your browser — your session starts automatically.
2. **Create a new submission** from the Active Workspace page.
3. **Upload** your compressed FASTQ files (the uploader handles large files seamlessly).
4. **Map your conditions** using the interactive table or a CSV upload.
5. **Select a reference genome** from the dropdown (or upload a custom genome).
6. **Launch** the core pipeline and watch progress in real time.
7. **Explore results** in the Core Hub — interactive plots, downloadable tables, and advanced modules.

---

## 2. Uploading Data

### 2.1 The Chunked Uploader — Built for Large Files

Genomics files are large. A single paired-end RNA-seq experiment can easily produce tens of gigabytes of compressed FASTQ data. RNAseek's uploader is specifically engineered for this reality.

**How it works:**

- Your browser automatically splits each file into **25 MB binary chunks** before transmission.
- Up to **6 chunks upload simultaneously**, maximizing your available bandwidth.
- Chunks are buffered on the server's fast local storage and merged into the final file only once all pieces have arrived.
- If a network interruption occurs mid-upload, only the affected chunk needs to be retransmitted — you do not lose the entire file.

This chunked architecture means you can confidently upload files of any size, even over slower or less reliable connections. There is no maximum file count; upload as many samples as your experiment requires.

### 2.2 Accepted File Formats

| Input Type    | Accepted Formats                   | Notes                                                                               |
| :------------ | :--------------------------------- | :---------------------------------------------------------------------------------- |
| Raw reads     | `.fq.gz`, `.fastq.gz`              | Must be gzip-compressed. Uncompressed `.fastq` or `.fq` files are **not accepted**. |
| Aligned reads | `.bam`, `.cram`                    | For users who bring pre-aligned data (skips alignment).                             |
| Count matrix  | `.csv`, `.tsv`                     | Rows = genes, columns = samples. All values must be non-negative integers.          |
| Metadata      | `.csv`                             | Condition mapping, batch IDs, timepoints.                                           |
| Custom genome | `.fa` / `.fasta` + `.gtf` / `.gff` | Triggers on-demand HISAT2 index build. Not available for Small RNA track.           |

> **Important:** Uncompressed FASTQ files are rejected by the server. If your files end in `.fastq` or `.fq` (without `.gz`), compress them first:
> ```bash
> gzip sample_R1.fastq
> ```

### 2.3 Paired-End Read Detection

If your experiment uses paired-end sequencing, name your files with the standard `_R1` / `_R2` convention:

```
SampleA_R1.fq.gz    SampleA_R2.fq.gz
SampleB_R1.fq.gz    SampleB_R2.fq.gz
```

RNAseek auto-detects paired reads from the filenames. You can also manually toggle between Single-End and Paired-End mode in the Setup Wizard if auto-detection does not match your naming scheme.

### 2.4 Mapping Experimental Conditions (Metadata)

After your files finish uploading, the Setup Wizard asks you to define the experimental design. You have two options:

**Option A — Interactive Table (recommended for small experiments)**

The wizard displays a table pre-populated with your uploaded filenames. Simply select a condition from the dropdown for each sample.

**Option B — CSV Upload (recommended for large experiments)**

Prepare a `.csv` file with the following columns:

```csv
Filename,Condition,Batch,Timepoint
WT_rep1_R1.fq.gz,Control,Batch_1,Day 0
WT_rep2_R1.fq.gz,Control,Batch_1,Day 0
WT_rep3_R1.fq.gz,Control,Batch_2,Day 0
KO_rep1_R1.fq.gz,Treated,Batch_2,Day 7
KO_rep2_R1.fq.gz,Treated,Batch_1,Day 7
KO_rep3_R1.fq.gz,Treated,Batch_1,Day 7
```

- **Filename** and **Condition** columns are required.
- **Batch** is optional. Providing it automatically triggers ComBat-seq batch correction during the normalization stage, removing technical sequencing noise between batches.
- **Timepoint** is optional. Providing it switches the statistical model from the standard Wald test to the Likelihood Ratio Test (LRT), which is more appropriate for time-series experimental designs.

### 2.5 Alternative Entry Points (BAM, CRAM, or Count Matrix)

Not starting from raw reads? RNAseek supports two shortcut entry points:

**Pre-Aligned Reads (BAM/CRAM)**
Upload your aligned BAM or CRAM files. RNAseek will skip the QC and alignment steps and proceed directly to gene quantification (featureCounts) and then Stage 2 normalization and differential expression testing.

**Pre-Computed Count Matrix (CSV/TSV)**
If you already have a gene-level count matrix, upload it as a CSV or TSV file. RNAseek will bypass Stage 1 entirely and jump straight to Stage 2 (filtering, normalization, DESeq2, and visualization). The matrix must contain:
- Rows representing genes (gene IDs or symbols).
- Columns representing samples.
- Non-negative integer values only.

### 2.6 Selecting a Reference Genome

During setup, choose the reference genome that matches your organism. RNAseek ships with **11 pre-indexed genomes**:

| Organism    | Assembly               |
| :---------- | :--------------------- |
| Human       | GRCh38 (hg38)          |
| Mouse       | GRCm39 (mm39)          |
| Mouse       | GRCm38 (mm10)          |
| Rat         | mRatBN7.2 (rn7)        |
| Zebrafish   | GRCz11 (danRer11)      |
| Chicken     | GRCg6a (galGal6)       |
| Pig         | Sscrofa11.1 (susScr11) |
| Drosophila  | BDGP6 (dm6)            |
| C. elegans  | WBcel235               |
| Yeast       | sacCer3 (R64-1-1)      |
| Arabidopsis | TAIR10                 |

**Custom Genomes:** If your organism is not listed, upload a FASTA file (`.fa` / `.fasta`) and a matching annotation file (`.gtf` / `.gff`). RNAseek will build a HISAT2 index on-demand before alignment begins. A progress banner will indicate when index building is underway.

> **Note:** Custom genome uploads are available for the Standard RNA-Seq and ChIP-seq tracks. The Small RNA / miRNA track requires species-specific miRBase indices and does not support custom genomes.

**Bacterial Genomes:** For unannotated microbial FASTA files, RNAseek automatically invokes its local BASys2 engine to produce full structural and metabolic annotations — no GTF upload is needed.

### 2.7 Launching the Pipeline

Once your files are uploaded, metadata is mapped, and a genome is selected:

1. Review your configuration in the Setup Wizard summary panel.
2. Click **Launch Pipeline**.
3. You will be redirected to the **Processing** page, where real-time progress bars track every step of your analysis.

---

## 3. Viewing Results

### 3.1 Real-Time Progress Tracking

After launching the pipeline, the Processing page connects to the server via a live **WebSocket** connection. You will see a step-by-step progress bar updating in real time as each stage completes:

- **Quality Control** — FastQC and adapter trimming (Trimmomatic).
- **Alignment** — Mapping reads to the reference genome.
- **Quantification** — Counting reads per gene/feature.
- **Normalization & Statistics** — DESeq2 differential expression testing.
- **Visualization** — Generating interactive plots.

If the WebSocket connection drops (e.g., due to a brief network interruption), the page automatically falls back to HTTP polling so you never lose sight of your pipeline's progress.

> **Tip:** You do not need to keep the browser open. Close the tab, and when you return (within your 14-day session window), your results will be waiting in the Core Hub.

### 3.2 The Core Hub — Your Results Dashboard

When the pipeline finishes, you are taken to the **Core Hub** — a three-tab dashboard that organizes all of your results:

| Tab             | Contents                                                                         |
| :-------------- | :------------------------------------------------------------------------------- |
| **Overview**    | Summary statistics, QC report, downloadable data files.                          |
| **Modules**     | 12 advanced analytical modules (see [Section 3.5](#35-advanced-modules-tier-2)). |
| **Single-Cell** | Deconvolution gateway and spatial analysis spokes (advanced).                    |

### 3.3 Stage 1 Results — Alignment & QC

The Overview tab provides the foundational outputs of your analysis:

**Downloadable Files:**

| File                  | Format  | Description                                                                                                               |
| :-------------------- | :------ | :------------------------------------------------------------------------------------------------------------------------ |
| Compressed Alignments | `.cram` | Deeply compressed alignment files showing where every read mapped to the genome.                                          |
| Raw Count Matrix      | `.csv`  | Genes × Samples matrix of raw read counts.                                                                                |
| QC Report             | `.html` | Interactive MultiQC report with Phred quality scores, GC content, adapter contamination metrics, and trimming statistics. |

### 3.4 Stage 2 Results — Normalization, DEG Testing & Visualizations

This is where RNAseek transforms your raw data into biological insight — fully automatically.

**What happens behind the scenes:**

1. **Low-count filtering** removes genes with insufficient evidence (fewer than 10 total reads across all samples).
2. **Batch correction** (if you provided Batch IDs) applies ComBat-seq to remove technical noise.
3. **DESeq2 normalization** adjusts for library size differences and performs differential gene expression testing with FDR-corrected p-values.
4. **Outlier detection** uses PCA-based Mahalanobis distance to flag potential outlier samples.
5. **Gene annotation** automatically queries the MyGene.info API to append human-readable gene descriptions and known disease associations to every gene in your results table.

**Downloadable Files:**

| File                          | Format | Description                                                                                                                                               |
| :---------------------------- | :----- | :-------------------------------------------------------------------------------------------------------------------------------------------------------- |
| Normalized Count Matrix       | `.csv` | Library-size-normalized (and batch-corrected, if applicable) expression values. Ready for use in external tools or machine learning.                      |
| Differential Expression Table | `.csv` | The master results table containing Log2 Fold Change, p-value, adjusted p-value (FDR), gene descriptions, and disease associations for every tested gene. |

**Interactive Visualizations:**

RNAseek automatically generates a suite of publication-quality, interactive Plotly visualizations. Every plot is rendered directly in your browser — zoom, pan, hover for gene names, and export as PNG or SVG.

- **PCA Plot (2D/3D):** Principal Component Analysis scatter plot showing how your samples cluster. Variance explained is displayed on each axis. Outlier samples are immediately visible.

- **UMAP Plot:** A non-linear dimensionality reduction that can reveal structure PCA might miss. Especially useful for complex experimental designs.

- **Volcano Plot:** The classic differential expression view. The x-axis shows Log2 Fold Change, the y-axis shows statistical significance (−log10 adjusted p-value). Significantly upregulated genes appear in red on the right; significantly downregulated genes appear in blue on the left. Hover over any point to see the gene name and statistics.

- **MA Plot:** Plots the mean expression level (baseMean) against the Log2 Fold Change for every gene, highlighting significantly differentially expressed genes.

- **Heatmap:** A clustered heatmap of the top 50 differentially expressed genes, with z-score normalization and color-coded group annotations. Instantly reveals expression patterns across your conditions.

> **Tip:** All Plotly visualizations are interactive. Click and drag to zoom into a region of interest, double-click to reset the view, and use the camera icon in the toolbar to download the plot as an image file.

### 3.5 Advanced Modules (Tier 2)

After the core pipeline completes, the **Modules** tab in the Core Hub unlocks **12 specialized analytical micro-pipelines**. These modules reuse your existing results — no re-uploading required. The Modules tab uses a master-detail layout: browse the list on the left, configure and view results on the right.

Key modules include:

**WGCNA (Weighted Gene Co-expression Network Analysis)**
Identify clusters (modules) of co-expressed genes and correlate them with clinical traits. Upload a traits CSV or build one interactively. Outputs include module-trait correlation heatmaps, hub gene lists, and Enrichr pathway enrichment results.

**Pathway & Gene Set Enrichment**
Map your differentially expressed genes onto biological pathways and curated gene sets. RNAseek integrates multiple databases:

- **PathBank** — Dynamic, interactive pathway diagrams showing exactly where your DEGs fall in metabolic, disease, and signaling pathways.
- **MSigDB Collections** — Hallmark gene sets, C2 (KEGG, Reactome), and C5 (GO Biological Process, Molecular Function, Cellular Component).
- **Microbial Pathways (BASys2)** — For bacterial datasets, maps differentially expressed genes to BASys2-derived metabolome and operon annotations.

Results include interactive pathway network graphs, dot plots, and downloadable enrichment tables.

**Additional Modules:**
| Module               | What It Does                                                                       |
| :------------------- | :--------------------------------------------------------------------------------- |
| Alternative Splicing | Detects skipped exons and predicts protein domain changes (IsoformSwitchAnalyzeR). |
| RNA Editing / SNPs   | Identifies A-to-I editing events and high-confidence variants (REDItools2).        |
| Time Series          | Models gene expression dynamics over time (ImpulseDE2).                            |
| Causal Networks      | Infers gene regulatory networks from expression data (GRNBoost2).                  |
| Literature NLP       | Mines published literature for known gene interactions (INDRA Bio).                |
| Survival Analysis    | Correlates gene expression with clinical survival outcomes (lifelines).            |
| TCGA Comparison      | Compares your data against public TCGA cancer cohorts.                             |
| Biomarker Discovery  | Cross-references DEGs with the MarkerDB clinical biomarker database.               |
| MOFA / DIABLO        | Multi-omics factor analysis for integrating multiple data layers.                  |

### 3.6 Downloading Your Data

Every downloadable file in the Core Hub has a clearly marked download button. You can download:

- Individual files (click the download icon next to any file).
- The complete differential expression table with gene annotations.
- Raw and normalized count matrices for use in external tools such as R, Python, or Excel.
- The interactive MultiQC HTML report for sharing with collaborators.

> **Remember:** Your session expires after 14 days. Download all files you wish to keep before the session window closes. Once expired, data is permanently and irrecoverably deleted by the automated cleanup process.

### 3.7 Single-Cell & Spatial Analysis (Advanced)

The **Single-Cell** tab in the Core Hub provides access to predictive deconvolution and spatial analysis tools:

1. **Deconvolution Gateway** — Select a tissue-specific single-cell reference atlas and run computational deconvolution to estimate cell-type fractions within your bulk samples. Toggle between a quick cell-fraction summary and a high-resolution mode that generates a full pseudo-single-cell matrix (`.h5ad`).

2. **Trajectory Inference** — Once deconvolution is complete, trace developmental or disease trajectories through predicted cell states using pseudotime analysis.

3. **Spatial Mapping** — Project predicted cell types onto a tissue image (select a generic template or upload your own H&E histology slide) to visualize where cells physically reside.

4. **Spatial Autocorrelation** — Search for specific genes and visualize their spatial expression patterns as heatmaps overlaid on the tissue image.

---

## Quick Reference

| Question                        | Answer                                                                                                                                                                             |
| :------------------------------ | :--------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| Do I need an account?           | No. Sessions are anonymous and automatic.                                                                                                                                          |
| How long does my data persist?  | 14 days from session creation.                                                                                                                                                     |
| What file formats are accepted? | `.fq.gz` / `.fastq.gz` (reads), `.bam` / `.cram` (alignments), `.csv` / `.tsv` (count matrices).                                                                                   |
| Is there a file size limit?     | The server supports uploads up to 10 GB per file. Files are chunked at 5 MB for reliability.                                                                                       |
| Can I use a custom genome?      | Yes. Upload a FASTA + GTF/GFF and an index will be built automatically (Standard RNA-Seq and ChIP-seq tracks).                                                                     |
| What organisms are supported?   | 11 pre-indexed genomes (Human, Mouse, Rat, Zebrafish, Drosophila, C. elegans, Yeast, Arabidopsis, Chicken, Pig) plus custom uploads and on-demand bacterial annotation via BASys2. |
| What statistics are used?       | DESeq2 for differential expression (Wald test by default; LRT for time-series). ComBat-seq for batch correction.                                                                   |
| Are the plots interactive?      | Yes. All visualizations are rendered with Plotly — zoom, pan, hover, and export as images.                                                                                         |
| Can I come back later?          | Yes. Return in the same browser within 14 days to access your results.                                                                                                             |
| What happens after 14 days?     | All session data is permanently deleted by an automated cleanup process. Download results before expiration.                                                                       |
