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
    
    cd "$FOLDER_NAME"
    
    # Find the .fa file (handles different naming conventions)
    FASTA_FILE=$(ls *.fa 2>/dev/null | head -n 1)
    
    if [ -z "$FASTA_FILE" ]; then
        echo "No .fa file found in $FOLDER_NAME. Skipping..."
    else
        echo "Building index for $FASTA_FILE..."
        
        # -p 8 uses 8 CPU threads to speed it up
        # We name the index using the FOLDER_NAME as the prefix
        hisat2-build -p 8 "$FASTA_FILE" "$FOLDER_NAME"
        
        echo "Finished indexing $FOLDER_NAME."
    fi
    
    cd ..
done

echo "-------------------------------------------------"
echo "DONE! All indices are built and ready for alignment."
