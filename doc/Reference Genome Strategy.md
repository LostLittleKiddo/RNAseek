# RNAseek — Reference Genome Strategy

> **Problem:** Pre-built HISAT2/BWA/Bismark genome indexes total ~44 GB — too large for Git or Docker images.  
> **This document** describes how to build, store, and distribute reference genomes across development and production environments.

---

## Table of Contents

1. [What's in `pipeline/reference_genomes/`](#1-whats-in-pipelinereference_genomes)
2. [Why Git Can't Handle This](#2-why-git-cant-handle-this)
3. [Recommended Strategy: Host-Side Storage + Bind Mount](#3-recommended-strategy-host-side-storage--bind-mount)
4. [Building Indexes from Scratch](#4-building-indexes-from-scratch)
5. [Transfer Between Machines](#5-transfer-between-machines)
6. [Docker Integration](#6-docker-integration)
7. [Custom Genomes (User-Uploaded)](#7-custom-genomes-user-uploaded)
8. [Alternatives Considered](#8-alternatives-considered)

---

## 1. What's in `pipeline/reference_genomes/`

Each organism subdirectory contains the files needed by the bioinformatics tools:

```
pipeline/reference_genomes/
├── Human_GRCh38/
│   ├── genome.fa              ← Genome FASTA (samtools reference)
│   ├── genes.gtf              ← Gene annotation (featureCounts)
│   ├── genome.1.ht2 … .8.ht2 ← HISAT2 index (8 segment files)
│   ├── genome.fa.fai          ← FASTA index (samtools faidx)
│   └── (BWA/Bismark indexes if built)
├── Mouse_GRCm39/
│   └── …
├── Yeast_sacCer3/             ← Smallest genome, ideal for dev testing
│   └── …
└── … (11 organisms total)
```

| Organism              | Approximate Size |
| --------------------- | ---------------- |
| Human (GRCh38)        | 8.5 GB           |
| Mouse (GRCm38)        | 7.4 GB           |
| Mouse (GRCm39)        | 7.2 GB           |
| Rat (rn7)             | 6.3 GB           |
| Pig (Sscrofa11.1)     | 6.3 GB           |
| Zebrafish (GRCz11)    | 3.7 GB           |
| Chicken (GRCg6a)      | 2.8 GB           |
| Arabidopsis (TAIR10)  | 555 MB           |
| Drosophila (dm6)      | 525 MB           |
| C. elegans (WBcel235) | 422 MB           |
| Yeast (sacCer3)       | 44 MB            |
| **Total**             | **~44 GB**       |

---

## 2. Why Git Can't Handle This

| Approach                  | Problem                                                                                                                                                     |
| ------------------------- | ----------------------------------------------------------------------------------------------------------------------------------------------------------- |
| **Regular Git**           | GitHub has a 100 MB per-file limit. HISAT2 index segments for human are ~1 GB each. Pushes are rejected.                                                    |
| **Git LFS**               | Tracks large files via pointer + external storage. GitHub LFS has a 2 GB free quota, then $5/50 GB/month. 44 GB would cost ~$40/month and slow every clone. |
| **Embed in Docker image** | Image becomes 44+ GB. Every build pushes/pulls the full image. Registry storage costs spike. Layers can't be shared across images.                          |

**Conclusion:** Reference genomes should live outside Git and outside Docker images. They are managed as external data.

---

## 3. Recommended Strategy: Host-Side Storage + Bind Mount

### The Pattern

1. Store genome files on the host filesystem (e.g., `/data/rnaseek/reference_genomes/`).
2. Bind-mount into Docker containers at runtime.
3. Git tracks only the directory structure (via `.gitignore`), not the actual files.

### `.gitignore` Setup (Already Configured)

```gitignore
# Reference genomes — too large for Git (44 GB)
pipeline/reference_genomes/
pipeline/reference_genomes/*
```

The directory exists in Git as an empty placeholder. The actual genome files are excluded.

### Development Setup

On a developer's machine, genomes live at `pipeline/reference_genomes/` inside the project directory. Since this path is gitignored, each developer maintains their own copy.

For development, you only need the **Yeast (sacCer3)** genome (44 MB) — the E2E test suite uses it.

### Production Setup (Docker)

Bind-mount from the host into the container:

```yaml
# In docker-compose.yml, under x-app-common volumes:
volumes:
  - media-data:/app/media
  - /data/rnaseek/reference_genomes:/app/pipeline/reference_genomes:ro
```

### Production Setup (Bare-Metal)

Genomes sit directly at `/opt/rnaseek/pipeline/reference_genomes/` alongside the application code. See [Production Deployment Guide § 10](Production%20Deployment%20Guide.md).

---

## 4. Building Indexes from Scratch

If you need to rebuild indexes (e.g., for a new genome assembly), here are the commands:

### HISAT2 Index (Required for Standard RNA-seq — Track A)

```bash
conda activate rnaseek
cd pipeline/reference_genomes/Human_GRCh38

# Download genome FASTA + GTF from Ensembl/GENCODE
wget https://ftp.ensembl.org/pub/release-113/fasta/homo_sapiens/dna/Homo_sapiens.GRCh38.dna.primary_assembly.fa.gz
wget https://ftp.ensembl.org/pub/release-113/gtf/homo_sapiens/Homo_sapiens.GRCh38.113.gtf.gz
gunzip *.gz
mv Homo_sapiens.GRCh38.dna.primary_assembly.fa genome.fa
mv Homo_sapiens.GRCh38.113.gtf genes.gtf

# Build HISAT2 index (takes 1-3 hours for human, uses ~200 GB RAM peak)
hisat2-build -p $(nproc) genome.fa genome

# Build FASTA index (for samtools)
samtools faidx genome.fa
```

### BWA Index (Required for ChIP-seq — Track C)

```bash
bwa index genome.fa
# Produces: genome.fa.amb, genome.fa.ann, genome.fa.bwt, genome.fa.pac, genome.fa.sa
```

### Bismark Genome Preparation (Required for Methylation — Track C)

```bash
bismark_genome_preparation --bowtie2 .
# Produces: Bisulfite_Genome/ directory with CT_conversion/ and GA_conversion/
```

### Bowtie Index for miRBase (Required for Small RNA — Track B)

miRBase indexes are built automatically by the pipeline at runtime if not present. The pipeline downloads the species-specific miRNA FASTA from miRBase and runs `bowtie-build`.

---

## 5. Transfer Between Machines

### rsync (Recommended)

```bash
# Dev → Production (push)
rsync -avz --progress \
    pipeline/reference_genomes/ \
    user@production-server:/data/rnaseek/reference_genomes/

# Production → Dev (pull)
rsync -avz --progress \
    user@production-server:/data/rnaseek/reference_genomes/ \
    pipeline/reference_genomes/
```

`rsync` is the best option because:
- Resumes interrupted transfers (`-avz` = archive + verbose + compress).
- Only transfers changed/new files (delta transfer).
- Works over SSH (encrypted by default).

### tar + scp (Alternative)

```bash
# On source machine
tar czf ref_genomes.tar.gz -C pipeline reference_genomes/

# Transfer
scp ref_genomes.tar.gz user@target:/data/rnaseek/

# On target
cd /data/rnaseek && tar xzf ref_genomes.tar.gz
```

### Verification After Transfer

```bash
# Count organism directories (should be 11)
ls /data/rnaseek/reference_genomes/ | wc -l

# Check HISAT2 index completeness (8 segment files per genome)
ls /data/rnaseek/reference_genomes/Human_GRCh38/*.ht2 | wc -l
# → 8

# Quick integrity check — HISAT2 inspect prints genome info
hisat2-inspect -s /data/rnaseek/reference_genomes/Human_GRCh38/genome | head -5
```

---

## 6. Docker Integration

### Build-Time vs. Run-Time

| Approach                  | Image Size | Build Time | Flexibility                     |
| ------------------------- | ---------- | ---------- | ------------------------------- |
| **COPY in Dockerfile**    | 44+ GB     | Very slow  | Genomes frozen at build time    |
| **Bind mount at runtime** | ~2 GB      | Fast       | Genomes updated without rebuild |

**We use bind mounts** — the Docker image stays lightweight (~2 GB), and genomes can be added or updated without rebuilding the image.

### docker-compose.yml Configuration

```yaml
x-app-common: &app-common
  build: .
  env_file: .env
  volumes:
    - media-data:/app/media
    - /data/rnaseek/reference_genomes:/app/pipeline/reference_genomes:ro
```

The `:ro` flag makes the mount read-only inside the container. This prevents accidental modification of shared genome files.

**Exception:** If users upload custom genomes that need HISAT2 index building, those indexes are built inside the session's `media/sessions/<uuid>/` directory — not in the reference_genomes mount.

---

## 7. Custom Genomes (User-Uploaded)

When a user selects "Custom Genome" in the web interface:

1. **User uploads** a genome FASTA (`.fa`) and GTF/GFF annotation via the chunked upload API.
2. **Files are stored** in `media/sessions/<session_uuid>/custom_genome/`.
3. **HISAT2 index is built** at pipeline runtime as a tracked step ("Build HISAT2 Index") — this can take 30 minutes to several hours depending on genome size.
4. **Index files** are written alongside the uploaded FASTA in the same session directory.

Custom genome indexes are **ephemeral** — they are purged along with the session after 14 days.

---

## 8. Alternatives Considered

### Git LFS

**Rejected.** At 44 GB, LFS storage costs ~$40/month on GitHub. Every `git clone` would download all genome files unless sparse checkout is configured. Adds complexity for marginal benefit over rsync.

### Cloud Object Storage (S3 / GCS)

**Future option.** Store genome tarballs in S3 with a download script that fetches on first use:

```bash
# Hypothetical setup script
./scripts/download_genomes.sh --species human,mouse,yeast --dest pipeline/reference_genomes/
```

This would work well for CI/CD or ephemeral cloud instances. Not implemented yet because the current rsync approach works for the team's single-server deployment.

### DVC (Data Version Control)

**Overkill for this use case.** DVC tracks data file versions alongside Git commits — useful for ML datasets that change frequently. Reference genomes are versioned by assembly name (GRCh38, GRCm39, etc.) and change only when new assemblies are released (every few years). A simple directory structure with assembly names is sufficient.

### Dedicated Genome Container Image

**Rejected.** Building a separate Docker image just for genomes (44 GB) is possible but wasteful — the image would rarely change but take enormous registry space. Bind mounts are simpler and more flexible.

---

## Summary

| Environment                 | Genome Location                                                                           | Method                                                                 |
| --------------------------- | ----------------------------------------------------------------------------------------- | ---------------------------------------------------------------------- |
| **Development (host)**      | `pipeline/reference_genomes/`                                                             | Git-ignored, developer maintains locally. Only Yeast needed for tests. |
| **Development (Docker)**    | Bind-mounted from host                                                                    | `docker-compose.dev.yml` inherits the bind mount                       |
| **Production (bare-metal)** | `/opt/rnaseek/pipeline/reference_genomes/`                                                | rsync from dev machine                                                 |
| **Production (Docker)**     | Host `/data/rnaseek/reference_genomes/` → container `/app/pipeline/reference_genomes/:ro` | Bind mount in `docker-compose.yml`                                     |
| **Custom (user-uploaded)**  | `media/sessions/<uuid>/custom_genome/`                                                    | Built at runtime, purged after 14 days                                 |
