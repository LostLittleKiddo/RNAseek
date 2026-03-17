#!/bin/bash

echo "Resuming GTF downloads..."

GTFS=(
    "Human_GRCh38 https://ftp.ensembl.org/pub/release-111/gtf/homo_sapiens/Homo_sapiens.GRCh38.111.gtf.gz"
    "Mouse_GRCm39 https://ftp.ensembl.org/pub/release-111/gtf/mus_musculus/Mus_musculus.GRCm39.111.gtf.gz"
    "Mouse_GRCm38 https://ftp.ensembl.org/pub/release-102/gtf/mus_musculus/Mus_musculus.GRCm38.102.gtf.gz"
    "Rat_rn7 https://hgdownload.soe.ucsc.edu/goldenPath/rn7/bigZips/genes/rn7.ncbiRefSeq.gtf.gz"
    "Zebrafish_GRCz11 https://ftp.ensembl.org/pub/release-111/gtf/danio_rerio/Danio_rerio.GRCz11.111.gtf.gz"
    "Chicken_GRCg6a https://hgdownload.soe.ucsc.edu/goldenPath/galGal6/bigZips/genes/galGal6.ncbiRefSeq.gtf.gz"
    "Pig_Sscrofa11.1 https://hgdownload.soe.ucsc.edu/goldenPath/susScr11/bigZips/genes/susScr11.ncbiRefSeq.gtf.gz"
    "Drosophila_dm6 https://ftp.ensembl.org/pub/release-111/gtf/drosophila_melanogaster/Drosophila_melanogaster.BDGP6.46.111.gtf.gz"
    "Celegans_WBcel235 https://ftp.ensembl.org/pub/release-111/gtf/caenorhabditis_elegans/Caenorhabditis_elegans.WBcel235.111.gtf.gz"
    "Yeast_sacCer3 https://ftp.ensembl.org/pub/release-111/gtf/saccharomyces_cerevisiae/Saccharomyces_cerevisiae.R64-1-1.111.gtf.gz"
    "Arabidopsis_TAIR10 https://ftp.ensemblgenomes.ebi.ac.uk/pub/plants/release-58/gtf/arabidopsis_thaliana/Arabidopsis_thaliana.TAIR10.58.gtf.gz"
)

for item in "${GTFS[@]}"; do
    FOLDER=$(echo $item | awk '{print $1}')
    URL=$(echo $item | awk '{print $2}')
    
    echo "========================================"
    
    if [ -d "$FOLDER" ]; then
        echo "Processing: $FOLDER"
        cd "$FOLDER"
        
        # Check if GTF is already fully unzipped from a previous successful run
        EXISTING_GTF=$(ls *.gtf 2>/dev/null | head -n 1)
        
        if [ -z "$EXISTING_GTF" ]; then 
            echo "Attempting to download GTF..."
            
            # If it's a UCSC link, try the 3 standard database names if the first 404s
            if [[ "$URL" == *"ucsc"* ]]; then
                wget -c --show-progress "$URL" || \
                wget -c --show-progress "${URL/ncbiRefSeq/refGene}" || \
                wget -c --show-progress "${URL/ncbiRefSeq/ensGene}"
            else
                wget -c --show-progress "$URL"
            fi
            
            # Find whatever .gz file was successfully downloaded and unzip it
            DOWNLOADED_GZ=$(ls *.gtf.gz 2>/dev/null | head -n 1)
            if [ -n "$DOWNLOADED_GZ" ]; then
                echo "Unzipping $DOWNLOADED_GZ..."
                gunzip -f "$DOWNLOADED_GZ"
            else
                echo "WARNING: All download attempts failed for $FOLDER. Moving on..."
            fi
        else
            echo "GTF already exists ($EXISTING_GTF). Skipping..."
        fi
        
        cd ..
    else
        echo "WARNING: Directory $FOLDER does not exist. Skipping."
    fi
done

echo "========================================"
echo "GTF pipeline finished!"
