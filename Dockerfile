# ============================================================
# RNAseek Multi-Stage Dockerfile
# ============================================================
# Base image: Miniconda3 (provides conda for R + bioinformatics
# CLI tools like HISAT2, samtools, featureCounts, FastQC, etc.)
#
# The same image is used for all services (web, worker, beat).
# The CMD/entrypoint is overridden per-service in docker-compose.
# ============================================================

FROM continuumio/miniconda3:25.1.1-2 AS base

# Prevent interactive prompts during package installation
ENV DEBIAN_FRONTEND=noninteractive \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

# System dependencies required by R, samtools, and compilation
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    libcurl4-openssl-dev \
    libssl-dev \
    libxml2-dev \
    libhdf5-dev \
    pkg-config \
    procps \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# ── Stage 1: Conda environment (R + bioinformatics CLI tools) ──
# This layer is cached unless environment.yml changes.
COPY environment.yml /app/environment.yml
RUN conda env update -n base --file environment.yml \
    && conda clean -afy

# ── Stage 2: Python pip dependencies ──
# Installed after conda so pip packages overlay correctly.
COPY requirements.txt /app/requirements.txt
RUN pip install --no-cache-dir -r requirements.txt

# ── Stage 3: Application code ──
COPY . /app/

# Collect static files for WhiteNoise serving
RUN python manage.py collectstatic --noinput

# Default port for Daphne ASGI server
EXPOSE 8000

# Default entrypoint: Daphne ASGI (overridden per service)
CMD ["daphne", "-b", "0.0.0.0", "-p", "8000", "config.asgi:application"]
