#!/bin/bash
# Build Bismark bisulfite-converted genome indices for all reference genomes
# (DNA Methylation Track C).

set -e

if ! command -v bismark_genome_preparation &> /dev/null; then
    echo "ERROR: bismark_genome_preparation not found. Activate your conda environment first."
    exit 1
fi

# Set the maximum number of genomes to index at the same time.
# Rule of thumb: MAX_JOBS * 5GB = estimated peak RAM usage.
MAX_JOBS=15

echo "Starting Bismark genome preparation ($MAX_JOBS concurrent jobs)..."

for dir in */; do
    FOLDER_NAME="${dir%/}"

    if [ "$FOLDER_NAME" = "miRBase" ]; then
        continue
    fi

    BISMARK_DIR="$FOLDER_NAME/bismark_index"
    mkdir -p "$BISMARK_DIR"
    FASTA_FILE=$(ls "$FOLDER_NAME"/genome/*.fa 2>/dev/null | head -n 1)

    if [ -z "$FASTA_FILE" ]; then
        echo "No .fa file found in $FOLDER_NAME/genome/. Skipping..."
    elif [ -d "$BISMARK_DIR/Bisulfite_Genome" ]; then
        echo "Bisulfite_Genome already exists in $BISMARK_DIR. Skipping..."
    else
        FASTA_NAME=$(basename "$FASTA_FILE")
        SYMLINK="$BISMARK_DIR/$FASTA_NAME"
        [ ! -e "$SYMLINK" ] && ln -sf "../genome/$FASTA_NAME" "$SYMLINK"

        echo "Running Bismark genome preparation for $BISMARK_DIR..."
        bismark_genome_preparation --bowtie2 "$BISMARK_DIR" &
        if [[ $(jobs -r -p | wc -l) -ge $MAX_JOBS ]]; then
            wait -n
        fi
    fi
done

# Wait for any remaining jobs to finish before exiting
wait 

echo "-------------------------------------------------"
echo "DONE! All Bismark genomes are prepared."