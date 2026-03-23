#!/bin/bash

# 1. Check if hisat2-build is available in the current Conda environment
if ! command -v hisat2-build &> /dev/null
then
    echo "ERROR: hisat2-build could not be found."
    echo "Please run 'conda activate <your_env>' before running this script."
    exit 1
fi

echo "Starting HISAT2 indexing..."

# 2. Loop through every directory (Human, Mouse, etc.)
for dir in */; do
    FOLDER_NAME="${dir%/}"
    
    echo "-------------------------------------------------"
    echo "Entering: $FOLDER_NAME"
    
    FASTA_FILE=$(ls "$FOLDER_NAME"/genome/*.fa 2>/dev/null | head -n 1)
    HISAT2_DIR="$FOLDER_NAME/hisat2_index"
    mkdir -p "$HISAT2_DIR"
    
    if [ -z "$FASTA_FILE" ]; then
        echo "No .fa file found in $FOLDER_NAME/genome/. Skipping..."
    else
        echo "Building HISAT2 index for $FASTA_FILE..."
        hisat2-build -p 8 "$FASTA_FILE" "$HISAT2_DIR/$FOLDER_NAME"
        echo "Finished indexing $FOLDER_NAME."
    fi
done

echo "-------------------------------------------------"
echo "DONE! All indices are built and ready for alignment."
