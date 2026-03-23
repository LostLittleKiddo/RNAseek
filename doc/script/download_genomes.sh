#!/bin/bash

# Stop the script if any command fails
set -e

# Create a main directory for all the references
MAIN_DIR="reference_genomes"
mkdir -p "$MAIN_DIR"
cd "$MAIN_DIR"

echo "Creating directories and starting downloads in ./$MAIN_DIR"

# Define a list of genomes. Format: "FolderName URL"
GENOMES=(
    "Human_GRCh38 https://ftp.ensembl.org/pub/release-111/fasta/homo_sapiens/dna/Homo_sapiens.GRCh38.dna.primary_assembly.fa.gz"
    "Mouse_GRCm39 https://ftp.ensembl.org/pub/release-111/fasta/mus_musculus/dna/Mus_musculus.GRCm39.dna.primary_assembly.fa.gz"
    "Mouse_GRCm38 https://ftp.ensembl.org/pub/release-102/fasta/mus_musculus/dna/Mus_musculus.GRCm38.dna.primary_assembly.fa.gz"
    "Rat_rn7 https://hgdownload.soe.ucsc.edu/goldenPath/rn7/bigZips/rn7.fa.gz"
    "Zebrafish_GRCz11 https://ftp.ensembl.org/pub/release-111/fasta/danio_rerio/dna/Danio_rerio.GRCz11.dna.primary_assembly.fa.gz"
    "Chicken_GRCg6a https://hgdownload.soe.ucsc.edu/goldenPath/galGal6/bigZips/galGal6.fa.gz"
    "Pig_Sscrofa11.1 https://hgdownload.soe.ucsc.edu/goldenPath/susScr11/bigZips/susScr11.fa.gz"
    "Drosophila_dm6 https://ftp.ensembl.org/pub/release-111/fasta/drosophila_melanogaster/dna/Drosophila_melanogaster.BDGP6.46.dna.toplevel.fa.gz"
    "Celegans_WBcel235 https://ftp.ensembl.org/pub/release-111/fasta/caenorhabditis_elegans/dna/Caenorhabditis_elegans.WBcel235.dna.toplevel.fa.gz"
    "Yeast_sacCer3 https://ftp.ensembl.org/pub/release-111/fasta/saccharomyces_cerevisiae/dna/Saccharomyces_cerevisiae.R64-1-1.dna.toplevel.fa.gz"
    "Arabidopsis_TAIR10 https://ftp.ensemblgenomes.ebi.ac.uk/pub/plants/release-58/fasta/arabidopsis_thaliana/dna/Arabidopsis_thaliana.TAIR10.dna.toplevel.fa.gz"
)
# Loop through each genome in the list
for item in "${GENOMES[@]}"; do
    # Extract folder name and URL from the list
    FOLDER=$(echo $item | awk '{print $1}')
    URL=$(echo $item | awk '{print $2}')
    FILENAME=$(basename "$URL")
    
    echo "========================================"
    echo "Processing: $FOLDER"
    
    # Create the organism's folder structure and enter genome/ subdir
    mkdir -p "$FOLDER/genome" "$FOLDER/hisat2_index" "$FOLDER/bwa_index" "$FOLDER/bismark_index"
    cd "$FOLDER/genome"
    
    # Download the file (-c allows resuming an interrupted download, -q makes it quieter)
    if [ ! -f "${FILENAME%.gz}" ]; then 
        echo "Downloading $FILENAME..."
        wget -c "$URL"
        
        # Unzip the file
        echo "Extracting..."
        gunzip -f "$FILENAME"
    else
        echo "Unzipped FASTA already exists. Skipping..."
    fi
    
    # Move back to the main directory
    cd ../..
done

echo "========================================"
echo "Success! All genomes downloaded and extracted."
