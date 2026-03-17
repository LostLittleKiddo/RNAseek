# RNAseek Pipeline Output

Edited: March 5, 2026 1:57 PM
Created: March 5, 2026 1:56 PM
To Review: No
Favorite: No
Archive: No

## 1. The Mandatory Hub (Core Pipeline Outputs)

*These are the foundational results generated for every user after the core pipeline finishes. They represent the standard deliverables of a bulk RNA-seq experiment.*

### Stage 1: Alignment & Quantification

- **Downloadable Data:**
    - **Compressed Alignments (`.cram`):** The deeply compressed files showing exactly where every sequence read mapped to the reference genome.
    - **Raw Count Matrix (`.csv`):** A table where rows are genes, columns are samples, and cells contain the exact number of reads mapped to that gene.
    - **QC Report (`.html` / `.pdf`):** A comprehensive MultiQC report showing sequence quality (Phred scores), GC content, and adapter contamination before and after trimming.

### Stage 2: Batch Correction, Statistics & DEG Testing

- **Downloadable Data:**
    - **Normalized Count Matrix (`.csv`):** The count matrix after library size normalization and batch-effect correction (ready for machine learning or external tools).
    - **Differential Expression Table (`.csv`):** The master table containing Log2 Fold Changes, p-values, adjusted p-values (FDR), and appended human-readable gene descriptions from `MyGene.info`.
- **Interactive UI Visualizations (WebGL/Plotly):**
    - **PCA & UMAP Plots:** 2D/3D scatter plots showing sample clustering.
    - **Volcano Plot:** Interactive scatter plot highlighting significantly upregulated (red) and downregulated (blue) genes.
    - **MA Plot & Heatmaps:** Sample-to-sample distance matrices and top 50 DEG expression heatmaps.

---

## 2. The Predictive Deconvolution Gateway (Optional Hub)

*If the user opted to mathematically unmix their bulk data, this is what the engine returns.*

- **Downloadable Data:**
    - **Cell Fractions Table (`.csv`):** A table listing the exact estimated percentage of each cell type within each bulk sample.
    - **Imputed Pseudo-scRNA Matrix (`.h5ad`):** The massive, simulated single-cell AnnData object containing the predicted gene expression for thousands of individual "pseudo-cells." *(This file is temporarily stored on the backend to power the spokes).*
- **Interactive UI Visualizations:**
    - **Cell Composition Bar Charts:** Interactive stacked bar charts allowing users to visually compare the immune/stromal composition between their treated and control groups.

---

## 3. The Advanced Unlocks (Spatial & Single-Cell Spokes)

*These outputs are strictly generated using the imputed `.h5ad` pseudo-cell matrix.*

### Spoke A: Trajectory Inference (Pseudotime)

- **Interactive UI Visualizations:**
    - **PAGA / Pseudotime Graph:** A force-directed or UMAP layout showing the evolutionary branching of the pseudo-cells. Nodes represent cell states, and edges represent the developmental path (e.g., tracing from stem cell to differentiated cell).

### Spoke B: Spatial Annotation & Mapping

- **Interactive UI Visualizations:**
    - **Spatial Projection Overlay:** The user's selected tissue image (H&E stain) overlaid with interactive, color-coded dots representing where the algorithm predicts the pseudo-cells physically reside.

### Spoke C: Spatial Autocorrelation

- **Downloadable Data:**
    - **Moran's I Statistics Table (`.csv`):** A ranked list of genes that exhibit the strongest spatial clustering (non-random distribution).
- **Interactive UI Visualizations:**
    - **Spatial Gene Heatmaps:** The tissue image painted with a heat gradient showing the physical localization of a specific gene's expression.

---

## 4. The Standard Spokes (Specialized Modules)

*Outputs generated dynamically when the user triggers specific analytical modules.*

### Category 1: Transcriptome Profiling

- **Module A (Alternative Splicing):**
    - *Downloads:* Table of skipped exons and predicted lost protein domains (`.csv`).
    - *Visuals:* "Switch Plots" showing the exact structural differences between transcript isoforms side-by-side.
- **Module B (RNA Editing / SNPs):**
    - *Downloads:* VCF (Variant Call Format) files containing all detected A-to-I editing events and high-confidence SNPs.

### Category 2: Systems Biology & Networks

- **Module D (WGCNA):**
    - *Downloads:* Module Eigengene lists (`.csv`).
    - *Visuals:* Hierarchical clustering dendrograms and an interactive **Module-Trait Heatmap** showing which gene clusters correlate strongly with clinical metadata.
- **Module E (Pathway Enrichment):**
    - *Downloads:* Enrichment tables (`.csv`) for GSEA and ORA.
    - *Visuals:* GSEA Ridge plots, dot plots, and interactive **PathBank diagrams** with user's DEGs highlighted in red/green directly on the biological pathway map.
- **Module F (Causal Networks & STRING):**
    - *Downloads:* Edge/Node lists (`.csv`).
    - *Visuals:* Highly interactive network graphs rendered via `Cytoscape.js`, allowing users to drag nodes and see physical protein interactions or inferred Transcription Factor-to-Target regulatory arrows.
- **Module G (Literature Mining):**
    - *Visuals:* A directed graph showing how the selected genes interact based on published literature. Clicking an edge opens a popup with the direct PubMed citation and abstract snippet proving the relationship.

### Category 3: Clinical & Translational

- **Module H (Survival Prediction / Kaplan-Meier):**
    - *Downloads:* Risk tables and Log-Rank statistics (`.csv`).
    - *Visuals:* Standard Kaplan-Meier step-curves showing the survival probability over time for "High Expressor" vs "Low Expressor" patient cohorts.
- **Module I (TCGA Disease Integration):**
    - *Visuals:* Joint PCA plots mapping the user's specific samples directly inside the clusters of a massive public TCGA cancer cohort to visually diagnose molecular subtypes.
- **Module J (Biomarker Discovery):**
    - *Downloads:* A curated table (`.csv`) flagging which of the user's top DEGs are already FDA-approved diagnostic, prognostic, or predictive biomarkers (via MarkerDB).

### Category 4: Multi-Omics

- **Modules K & L (MOFA / DIABLO):**
    - *Downloads:* Latent factor weightings (`.csv`).
    - *Visuals:* Variance Explained plots (showing how much variance each omics layer contributes), **Circos plots** showing correlations between specific genes and secondary omics features (e.g., methylation sites), and predictive AUROC performance curves.