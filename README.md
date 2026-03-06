# RNAseek — Installation Guide

## Prerequisites

| Dependency         | Version         | Notes                                   |
| ------------------ | --------------- | --------------------------------------- |
| **Arch Linux**     | Rolling release | Tested on current Arch                  |
| **Conda**          | ≥ 25.x          | Miniconda or Miniforge                  |
| **Redis / Valkey** | 7+              | System package (`pacman -S valkey`)     |
| **PostgreSQL**     | 15+             | System package (`pacman -S postgresql`) |
| **Docker**         | 29+             | Optional; for containerised deployment  |

## Quick Start

```bash
# 1. Clone the repository
git clone <repo-url> && cd RNAseek

# 2. Create the conda environment (Python 3.11 + R 4.3 + bioinformatics tools + R/Bioconductor packages)
conda env create -f environment.yml

# 3. Activate
conda activate rnaseek

# 4. Install Python pip packages
pip install -r requirements.txt

# 5. Verify installation
python -c "import django, scanpy, scvi, rpy2; print('OK')"
Rscript -e 'library(DESeq2); library(sva); cat("R packages OK\n")'
```

## What Gets Installed

### Conda Packages (via `environment.yml`)

**Bioinformatics CLI tools:**

| Tool                      | Purpose                    |
| ------------------------- | -------------------------- |
| `fastqc`                  | Sequencing quality control |
| `trimmomatic`             | Adapter trimming           |
| `hisat2`                  | Splice-aware alignment     |
| `samtools`                | SAM/BAM/CRAM manipulation  |
| `subread` (featureCounts) | Read quantification        |
| `stringtie`               | Transcript assembly        |

**R / Bioconductor packages:**

| Package                 | Purpose                             |
| ----------------------- | ----------------------------------- |
| `DESeq2`                | Differential expression analysis    |
| `sva` (ComBat_seq)      | Batch correction                    |
| `DEXSeq`                | Differential exon usage             |
| `IsoformSwitchAnalyzeR` | Alternative splicing analysis       |
| `TCGAbiolinks`          | TCGA data integration               |
| `mixOmics` (DIABLO)     | Multi-omics integration             |
| `WGCNA`                 | Weighted gene co-expression network |

### Python Packages (via `requirements.txt`)

**Web Framework & Infrastructure:**

- Django 5.2 + Django REST Framework 3.16
- Celery 5.6 with Redis broker
- Gunicorn (WSGI server)
- psycopg2 (PostgreSQL adapter)

**Scientific Core:**

- NumPy, Pandas, SciPy, scikit-learn
- Matplotlib, Plotly, Seaborn
- h5py, statsmodels

**Python-R Bridge:**

- rpy2 3.6 — calls R packages (DESeq2, sva, etc.) from Python

**Single-Cell / Spatial:**

- scanpy — single-cell analysis
- squidpy — spatial transcriptomics
- scvi-tools — DestVI deconvolution
- tangram-sc — spatial cell mapping

**Bioinformatics Analysis:**

- gseapy — Gene Set Enrichment Analysis
- lifelines — Kaplan-Meier survival analysis
- mofapy2 — Multi-Omics Factor Analysis
- arboreto — GRNBoost2 gene regulatory networks
- PyWGCNA — Python WGCNA wrapper
- multiqc — QC report aggregation
- indra — Literature mining / NLP pathway reconstruction

## System Services

Make sure these are running before starting the application:

```bash
# Redis (Valkey on Arch)
sudo systemctl enable --now valkey

# PostgreSQL
sudo systemctl enable --now postgresql
```

## Verify Everything

```bash
conda activate rnaseek

# Check CLI tools
fastqc --version
hisat2 --version | head -1
samtools --version | head -1
featureCounts -v 2>&1 | head -1
multiqc --version

# Check R packages
Rscript -e '
pkgs <- c("DESeq2","sva","DEXSeq","IsoformSwitchAnalyzeR","TCGAbiolinks","mixOmics","WGCNA")
for (p in pkgs) cat(p, ": ", as.character(packageVersion(p)), "\n")
'

# Check Python packages
python -c "
import django, rest_framework, celery, redis
import scanpy, squidpy, scvi, tangram
import gseapy, lifelines, mofapy2, arboreto, PyWGCNA
import anndata, mudata, rpy2, h5py, plotly, indra, multiqc
print('All Python imports OK')
"

# Check Python-R bridge
python -c "
import rpy2.robjects as ro
ro.r('library(DESeq2)')
print('rpy2 -> DESeq2 bridge OK')
"
```

## Directory Structure (after setup)

```
RNAseek/
├── environment.yml          # Conda env definition (R, CLI tools, Bioconductor)
├── requirements.txt         # Python pip dependencies
├── README.md                # This file
└── RNAseek Pipeline Blueprint.md  # Architecture blueprint
```

## Troubleshooting

### Conda solver is too slow
```bash
# Use libmamba solver (default in conda ≥23.10)
conda config --set solver libmamba
```

### rpy2 can't find R
```bash
# Ensure R is from the conda env, not system R
which R  # Should point to ~/.conda/envs/rnaseek/bin/R
```

### Redis connection refused
```bash
sudo systemctl start valkey
redis-cli ping  # Should return PONG
```
