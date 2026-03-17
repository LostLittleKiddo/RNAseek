#!/bin/bash

# ==============================================================================
# RNAseek Dev Environment Setup: 6-Sample Smoketest Downloader
# ==============================================================================
# This script downloads a minimal 6-sample subset of the GSE312455 yeast dataset
# (YPS606 strain: 3x Unstressed vs 3x 0.4 M NaCl) and subsets them to 100k reads.
# ==============================================================================

# Exit immediately if a command exits with a non-zero status
set -e

# Define the number of reads for your dev test (100,000 is plenty for testing)
READ_COUNT=100000
OUTPUT_DIR="./rnaseek_dev_dataset"

# ------------------------------------------------------------------------------
# STEP 1: Define your 6 subset samples 
# -> REPLACE the 'SRRXXXXXXX' values with the actual IDs from your RunInfo CSV
# ------------------------------------------------------------------------------
declare -A SAMPLES=(
    # Control: Unstressed
    ["GSM9346166_Unstressed_Rep1"]="SRR36299420"
    ["GSM9346167_Unstressed_Rep2"]="SRR36299419"
    ["GSM9346168_Unstressed_Rep3"]="SRR36299418"
    
    # Treatment: 0.4 M NaCl
    ["GSM9346170_NaCl_Rep1"]="SRR36299416"
    ["GSM9346171_NaCl_Rep2"]="SRR36299415"
    ["GSM9346172_NaCl_Rep3"]="SRR36299414"
)

mkdir -p "$OUTPUT_DIR"
cd "$OUTPUT_DIR"

echo "========================================================"
echo " Starting RNAseek Dev Dataset Generation (Smoketest) "
echo "========================================================"

for GSM_NAME in "${!SAMPLES[@]}"; do
    SRR_ID="${SAMPLES[$GSM_NAME]}"
    
    echo "--------------------------------------------------------"
    echo "Processing $GSM_NAME ($SRR_ID)..."
    
    # Step 2: Download the raw FASTQ using fasterq-dump
    # --split-files separates paired-end reads if applicable
    echo "[1/3] Downloading full FASTQ data..."
    fasterq-dump "$SRR_ID" --split-files --force
    
    # Step 3: Subsample the reads using seqtk
    # Note: Checking if it's Single-End or Paired-End. SRA usually outputs _1.fastq and _2.fastq for paired.
    if [ -f "${SRR_ID}_1.fastq" ]; then
        echo "[2/3] Paired-end data detected. Subsampling to $READ_COUNT reads..."
        
        # Subsample Forward Read (with fixed seed -s100 for reproducibility)
        seqtk sample -s100 "${SRR_ID}_1.fastq" $READ_COUNT > "${GSM_NAME}_R1_dev.fastq"
        # Subsample Reverse Read
        seqtk sample -s100 "${SRR_ID}_2.fastq" $READ_COUNT > "${GSM_NAME}_R2_dev.fastq"
        
        echo "[3/3] Compressing dev files and cleaning up heavy raw files..."
        gzip "${GSM_NAME}_R1_dev.fastq"
        gzip "${GSM_NAME}_R2_dev.fastq"
        rm "${SRR_ID}"*.fastq
        
    elif [ -f "${SRR_ID}.fastq" ]; then
        echo "[2/3] Single-end data detected. Subsampling to $READ_COUNT reads..."
        
        seqtk sample -s100 "${SRR_ID}.fastq" $READ_COUNT > "${GSM_NAME}_dev.fastq"
        
        echo "[3/3] Compressing dev file and cleaning up heavy raw files..."
        gzip "${GSM_NAME}_dev.fastq"
        rm "${SRR_ID}.fastq"
    else
        echo "Error: Failed to download $SRR_ID"
        exit 1
    fi
    
    echo "Done with $GSM_NAME!"
done

echo "========================================================"
echo "✅ Dev dataset generation complete!"
echo "Your lightweight FASTQ files are ready in: $OUTPUT_DIR"
echo "========================================================"
