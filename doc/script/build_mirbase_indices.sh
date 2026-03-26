#!/bin/bash
# Download miRBase mature sequences and build Bowtie indices for small RNA
# alignment (Track B).
# Run from inside pipeline/reference_genomes/
# Requires: bowtie-build (conda activate <env>)
#
# miRBase provides a single mature.fa containing all species. This script
# extracts species-specific sequences and builds per-species Bowtie indices.

set -e

if ! command -v bowtie-build &> /dev/null; then
    echo "ERROR: bowtie-build not found. Activate your conda environment first."
    exit 1
fi

MIRBASE_DIR="miRBase"
mkdir -p "$MIRBASE_DIR"
cd "$MIRBASE_DIR"

# Download mature miRNA sequences from miRBase (if not already present)
if [ ! -f "mature.fa" ]; then
    echo "Downloading miRBase mature.fa..."
    wget -q "https://www.mirbase.org/download/mature.fa" -O mature.fa
    echo "Downloaded mature.fa"
fi

# Species codes that map to our supported genomes
# Format: "three_letter_code species_prefix_in_fasta"
SPECIES=(
    "hsa"   # Human (hg38)
    "mmu"   # Mouse (mm39, mm10)
    "rno"   # Rat (rn7)
    "dre"   # Zebrafish (danRer11)
    "gga"   # Chicken (galGal6)
    "dme"   # Drosophila (dm6)
    "cel"   # C. elegans (wbcel235)
    "ath"   # Arabidopsis (araTha)
)

for sp in "${SPECIES[@]}"; do
    echo "-------------------------------------------------"
    echo "Processing miRBase species: $sp"

    mkdir -p "$sp"

    # Extract species-specific sequences from mature.fa
    SPECIES_FA="$sp/${sp}_mature.fa"
    if [ ! -f "$SPECIES_FA" ]; then
        echo "Extracting $sp sequences..."
        awk -v sp="$sp" '
            /^>/ { keep = ($0 ~ "^>" sp "-") }
            keep { print }
        ' mature.fa > "$SPECIES_FA"

        SEQ_COUNT=$(grep -c "^>" "$SPECIES_FA" 2>/dev/null || echo "0")
        echo "Extracted $SEQ_COUNT sequences for $sp"

        if [ "$SEQ_COUNT" -eq 0 ]; then
            echo "WARNING: No sequences found for $sp. Skipping index build."
            continue
        fi
    fi

    # Build Bowtie index
    IDX_PREFIX="$sp/mirbase_idx"
    if [ -f "${IDX_PREFIX}.1.ebwt" ]; then
        echo "Bowtie index already exists for $sp. Skipping..."
    else
        echo "Building Bowtie index for $sp..."
        bowtie-build "$SPECIES_FA" "$IDX_PREFIX"
        echo "Finished Bowtie index for $sp."
    fi
done

cd ..

echo "-------------------------------------------------"
echo "DONE! All miRBase Bowtie indices are built."
