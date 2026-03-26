#!/bin/bash
# Build BWA indices for all reference genomes (ChIP-seq Track C).

set -e

if ! command -v bwa &> /dev/null; then
    echo "ERROR: bwa not found. Activate your conda environment first."
    exit 1
fi

# Set maximum concurrent jobs
MAX_JOBS=15

echo "Starting BWA indexing ($MAX_JOBS concurrent jobs)..."

for dir in */; do
    FOLDER_NAME="${dir%/}"

    if [ "$FOLDER_NAME" = "miRBase" ]; then
        continue
    fi

    FASTA_FILE=$(ls "$FOLDER_NAME"/genome/*.fa 2>/dev/null | head -n 1)
    BWA_DIR="$FOLDER_NAME/bwa_index"
    mkdir -p "$BWA_DIR"

    if [ -z "$FASTA_FILE" ]; then
        echo "No .fa file found in $FOLDER_NAME/genome/. Skipping..."
    else
        FASTA_NAME=$(basename "$FASTA_FILE")
        SYMLINK="$BWA_DIR/$FASTA_NAME"
        [ ! -e "$SYMLINK" ] && ln -sf "../genome/$FASTA_NAME" "$SYMLINK"

        if [ -f "$SYMLINK.bwt" ]; then
            echo "BWA index already exists for $FASTA_NAME. Skipping..."
        else
            echo "Building BWA index for $FASTA_NAME in $BWA_DIR..."
            bwa index "$SYMLINK" &
            if [[ $(jobs -r -p | wc -l) -ge $MAX_JOBS ]]; then
                wait -n
            fi
        fi
    fi
done

# Wait for the final batch to finish
wait

echo "-------------------------------------------------"
echo "DONE! All BWA indices are built."