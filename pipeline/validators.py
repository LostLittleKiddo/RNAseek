"""Pre-submission validation for POST /api/pipeline/core.

Each validator function receives the request payload (dict) and the
AnalysisSubmission instance, returning a list of error strings.
An empty list means the check passed.

New assay tracks can register additional validators by appending to
``TRACK_VALIDATORS[track_name]``.
"""

import csv
import io
import os
import re

from pipeline.tasks._constants import _MIRBASE_SPECIES_MAP

# ---------------------------------------------------------------------------
# Registry — maps (input_data_type, assay_type) to extra validator functions.
# Each function signature:  fn(body, submission) -> list[str]
# ---------------------------------------------------------------------------
TRACK_VALIDATORS: dict[tuple[str, str], list] = {}


def register_track_validator(input_data_type: str, assay_type: str):
    """Decorator to register a validator for a specific track."""
    def decorator(fn):
        key = (input_data_type, assay_type)
        TRACK_VALIDATORS.setdefault(key, []).append(fn)
        return fn
    return decorator


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------
VALID_INPUT_DATA_TYPES = {"fastq", "alignment", "matrix"}
VALID_ASSAY_TYPES = {"standard_rna", "small_rna", "chip_seq", "methylation"}
VALID_LIBRARY_TYPES = {"single", "paired"}
VALID_STRANDEDNESS = {"unstranded", "fr-firststrand", "fr-secondstrand"}
VALID_QUANT_LEVELS = {"gene", "transcript"}
SAFE_NAME_RE = re.compile(r'^[a-zA-Z0-9_-]+$')


# ---------------------------------------------------------------------------
# Core Validators (run for every submission)
# ---------------------------------------------------------------------------

def validate_base_fields(body: dict, submission) -> list[str]:
    """Validate top-level required fields."""
    errors = []

    input_data_type = body.get("input_data_type", "fastq")
    if input_data_type not in VALID_INPUT_DATA_TYPES:
        errors.append("Invalid input_data_type.")
        return errors  # can't continue

    assay_type = body.get("assay_type", "standard_rna")
    if input_data_type == "fastq" and assay_type not in VALID_ASSAY_TYPES:
        errors.append("Invalid assay_type.")

    # Req 3: Small RNA requires single-end library
    library_type = body.get("library_type", "")
    strandedness = body.get("strandedness", "unstranded")
    if input_data_type == "fastq":
        if library_type not in VALID_LIBRARY_TYPES:
            errors.append("Invalid library_type.")
        if strandedness not in VALID_STRANDEDNESS:
            errors.append("Invalid strandedness.")
        if assay_type == "small_rna" and library_type == "paired":
            errors.append(
                "Small RNA / miRNA requires Single-End reads. "
                "Paired-End is not supported for this assay type."
            )

    reference_genome = body.get("reference_genome", "")
    quant_level = body.get("quant_level", "gene")
    if input_data_type in ("fastq", "alignment"):
        if not reference_genome:
            errors.append("Reference genome is required.")
        if quant_level not in VALID_QUANT_LEVELS:
            errors.append("Invalid quant_level.")

    metadata_mode = body.get("metadata_mode", "")
    if metadata_mode not in ("upload", "manual"):
        errors.append("Invalid metadata_mode.")

    # Thresholds
    try:
        adj_pvalue = float(body.get("adjusted_pvalue", 0.05))
        if not (0 < adj_pvalue <= 1):
            errors.append("adjusted_pvalue must be between 0 and 1.")
    except (ValueError, TypeError):
        errors.append("Invalid adjusted_pvalue.")

    try:
        float(body.get("min_log2fc", -1.0))
        float(body.get("max_log2fc", 1.0))
    except (ValueError, TypeError):
        errors.append("Invalid threshold values.")

    return errors


def validate_uploaded_files(body: dict, submission) -> list[str]:
    """Validate that the correct files have been uploaded for the data type."""
    from pipeline.models import FileAsset

    errors = []
    input_data_type = body.get("input_data_type", "fastq")
    library_type = body.get("library_type", "")

    if input_data_type == "fastq":
        fastq_assets = submission.file_assets.filter(
            file_role=FileAsset.FileRole.RAW_FASTQ
        )
        if not fastq_assets.exists():
            errors.append("No FASTQ files uploaded.")
        else:
            count = fastq_assets.count()
            if library_type == "paired":
                if count % 2 != 0:
                    errors.append(
                        "Paired-end requires an even number of FASTQ files."
                    )
                # Req 1: minimum 2 pairs
                if count // 2 < 2:
                    errors.append(
                        "At least 2 paired-end samples (2 R1/R2 pairs) "
                        "are required for differential expression analysis."
                    )
            else:
                # Req 1: minimum 2 SE files
                if count < 2:
                    errors.append(
                        "At least 2 FASTQ files (samples) are required "
                        "for differential expression analysis."
                    )
    elif input_data_type == "alignment":
        bam_assets = submission.file_assets.filter(
            file_role=FileAsset.FileRole.ALIGNMENT_BAM
        )
        if not bam_assets.exists():
            errors.append("No BAM/CRAM files uploaded.")
        elif bam_assets.count() < 2:
            errors.append(
                "At least 2 BAM/CRAM files (samples) are required "
                "for differential expression analysis."
            )
    elif input_data_type == "matrix":
        matrix_assets = submission.file_assets.filter(
            file_role=FileAsset.FileRole.USER_COUNT_MATRIX
        )
        if not matrix_assets.exists():
            errors.append("No count matrix uploaded.")

    return errors


def validate_custom_genome(body: dict, submission) -> list[str]:
    """Validate custom genome file requirements."""
    from pipeline.models import FileAsset

    errors = []
    input_data_type = body.get("input_data_type", "fastq")
    reference_genome = body.get("reference_genome", "")

    if input_data_type not in ("fastq", "alignment"):
        return errors
    if reference_genome != "custom":
        return errors

    custom_name = body.get("custom_genome_name", "").strip()
    if not custom_name:
        errors.append("Custom genome name is required.")
    elif not SAFE_NAME_RE.match(custom_name):
        errors.append(
            "Custom genome name must contain only letters, digits, "
            "hyphens, or underscores."
        )

    has_fasta = submission.file_assets.filter(
        file_role=FileAsset.FileRole.CUSTOM_GENOME_FASTA
    ).exists()
    has_annotation = submission.file_assets.filter(
        file_role=FileAsset.FileRole.CUSTOM_GENOME_ANNOTATION
    ).exists()

    if input_data_type == "fastq":
        if not has_fasta or not has_annotation:
            errors.append(
                "Custom genome requires both FASTA and GTF/GFF files."
            )
    else:
        if not has_annotation:
            errors.append(
                "Custom genome requires a GTF/GFF annotation file."
            )

    # Req 8: basic FASTA header check on uploaded file
    if has_fasta:
        fasta_asset = submission.file_assets.filter(
            file_role=FileAsset.FileRole.CUSTOM_GENOME_FASTA
        ).first()
        if fasta_asset and fasta_asset.local_path and os.path.isfile(
            fasta_asset.local_path
        ):
            try:
                import gzip
                fasta_path = fasta_asset.local_path
                opener = (
                    gzip.open if fasta_path.endswith(".gz") else open
                )
                with opener(fasta_path, "rt") as fh:
                    for line in fh:
                        stripped = line.strip()
                        if stripped:  # first non-empty line
                            if not stripped.startswith(">"):
                                errors.append(
                                    "Uploaded FASTA file does not appear "
                                    "to be valid. The first non-empty line "
                                    "must start with '>' (FASTA header)."
                                )
                            break
            except (OSError, UnicodeDecodeError):
                errors.append(
                    "Could not read the uploaded custom genome FASTA file."
                )

    return errors


def validate_metadata(body: dict, submission) -> list[str]:
    """Validate metadata samples, column mapping, and contrasts."""
    errors = []
    meta = body.get("metadata_payload", {})
    metadata_mode = body.get("metadata_mode", "")
    input_data_type = body.get("input_data_type", "fastq")
    library_type = body.get("library_type", "")

    samples = meta.get("samples", [])
    if not samples:
        errors.append("Metadata requires sample data.")
        return errors

    # Validate first column is 'sample' for CSV uploads
    if metadata_mode == "upload" and isinstance(samples[0], dict):
        first_col = list(samples[0].keys())[0] if samples[0] else ""
        if first_col.strip().lower() != "sample":
            errors.append(
                "The first column of metadata must be named 'sample'."
            )

    # Sample-name matching against uploaded files
    if metadata_mode == "upload" and isinstance(samples[0], dict):
        sample_col = list(samples[0].keys())[0]
        meta_sample_ids = {
            (row.get(sample_col) or "").strip() for row in samples
        }
        meta_sample_ids.discard("")

        expected_stems = _extract_expected_stems(
            submission, input_data_type, library_type
        )

        if expected_stems:
            unmatched = expected_stems - meta_sample_ids
            if unmatched:
                errors.append(
                    f"Metadata is missing rows for uploaded samples: "
                    f"{', '.join(sorted(unmatched))}. "
                    f"The 'sample' column must contain the filename stem "
                    f"(without extension)."
                )

    # Column mapping
    col_mapping = meta.get("column_mapping", {})
    primary_group = col_mapping.get("primary_group")
    if not primary_group:
        errors.append("A primary group column must be selected.")

    # Contrasts
    contrasts = meta.get("contrasts", [])
    for pair in contrasts:
        if not isinstance(pair, list) or len(pair) != 2:
            errors.append(
                "Each contrast must be a [target, reference] pair."
            )
        elif pair[0] == pair[1]:
            errors.append(
                "Contrast target and reference must be different."
            )

    # Req 2: contrast values must exist in primary group column
    if primary_group and samples and contrasts:
        group_values = set()
        for row in samples:
            val = str(row.get(primary_group, "")).strip()
            if val:
                group_values.add(val)
        for pair in contrasts:
            if not isinstance(pair, list) or len(pair) != 2:
                continue
            target, reference = pair[0], pair[1]
            if target and target not in group_values:
                errors.append(
                    f"Contrast target '{target}' does not exist in the "
                    f"'{primary_group}' column of your metadata."
                )
            if reference and reference not in group_values:
                errors.append(
                    f"Contrast reference '{reference}' does not exist in "
                    f"the '{primary_group}' column of your metadata."
                )

    # Req 7: sanitized sample names
    if samples:
        sample_key = list(samples[0].keys())[0] if isinstance(
            samples[0], dict
        ) and samples[0] else "sample"
        bad_names = []
        for row in samples:
            sname = str(row.get(sample_key, "")).strip()
            if sname and not SAFE_NAME_RE.match(sname):
                bad_names.append(sname)
        if bad_names:
            preview = ", ".join(bad_names[:5])
            extra = (
                f" (and {len(bad_names) - 5} more)"
                if len(bad_names) > 5 else ""
            )
            errors.append(
                f"Sample names must contain only letters, digits, "
                f"hyphens, or underscores. Invalid: {preview}{extra}"
            )

    return errors


# ---------------------------------------------------------------------------
# Track-Specific Validators
# ---------------------------------------------------------------------------

def validate_small_rna_genome(body: dict, submission) -> list[str]:
    """Small RNA: genome must be in MIRBASE_SPECIES_MAP, no custom."""
    errors = []
    input_data_type = body.get("input_data_type", "fastq")
    assay_type = body.get("assay_type", "standard_rna")

    if input_data_type != "fastq" or assay_type != "small_rna":
        return errors

    reference_genome = body.get("reference_genome", "")
    if reference_genome == "custom":
        errors.append(
            "Custom genomes are not supported for Small RNA / miRNA. "
            "Please select a pre-indexed organism."
        )
    elif reference_genome and reference_genome not in _MIRBASE_SPECIES_MAP:
        errors.append(
            f"Genome '{reference_genome}' does not have a miRBase index. "
            f"Please select a supported organism."
        )

    return errors


def validate_paired_end_matching(body: dict, submission) -> list[str]:
    """For FASTQ paired-end, validate that R1 and R2 files are matched."""
    from pipeline.models import FileAsset

    errors = []
    input_data_type = body.get("input_data_type", "fastq")
    library_type = body.get("library_type", "")

    if input_data_type != "fastq" or library_type != "paired":
        return errors

    fastq_assets = submission.file_assets.filter(
        file_role=FileAsset.FileRole.RAW_FASTQ
    )
    filenames = [
        os.path.basename(p)
        for p in fastq_assets.values_list("local_path", flat=True)
    ]

    pair_re = re.compile(
        r'^(.+?)(?:_R([12])|_([12]))\.(?:fq|fastq)(?:\.gz)?$',
        re.IGNORECASE,
    )
    r1_stems = set()
    r2_stems = set()
    unmatched = []

    for name in filenames:
        m = pair_re.match(name)
        if not m:
            unmatched.append(name)
            continue
        stem = m.group(1)
        read_num = m.group(2) or m.group(3)
        if read_num == "1":
            r1_stems.add(stem)
        else:
            r2_stems.add(stem)

    if unmatched:
        errors.append(
            f"{len(unmatched)} file(s) don't match _R1/_R2 naming: "
            f"{', '.join(unmatched)}"
        )

    r1_only = r1_stems - r2_stems
    r2_only = r2_stems - r1_stems
    if r1_only:
        errors.append(
            f"Missing R2 pair for: {', '.join(sorted(r1_only))}"
        )
    if r2_only:
        errors.append(
            f"Missing R1 pair for: {', '.join(sorted(r2_only))}"
        )

    return errors


def validate_chipseq_metadata(body: dict, submission) -> list[str]:
    """ChIP-seq: metadata must define at least one 'input' control sample
    and at least one non-input (treatment/IP) sample."""
    errors = []
    input_data_type = body.get("input_data_type", "fastq")
    assay_type = body.get("assay_type", "standard_rna")

    if input_data_type != "fastq" or assay_type != "chip_seq":
        return errors

    meta = body.get("metadata_payload", {})
    samples = meta.get("samples", [])
    col_mapping = meta.get("column_mapping", {})
    primary_group = col_mapping.get("primary_group", "condition")

    has_control = False
    has_treatment = False
    for row in samples:
        condition = str(row.get(primary_group, "")).strip().lower()
        if condition == "input":
            has_control = True
        else:
            has_treatment = True

    if not has_control:
        errors.append(
            "ChIP-seq requires at least one sample labeled 'input' "
            "(case-insensitive) in the primary group column as control."
        )
    if not has_treatment:
        errors.append(
            "ChIP-seq requires at least one non-input (treatment/IP) sample."
        )

    return errors


def validate_batch_column(body: dict, submission) -> list[str]:
    """If batch correction is requested, the batch column must exist in
    the metadata samples."""
    errors = []
    meta = body.get("metadata_payload", {})
    col_mapping = meta.get("column_mapping", {})
    batch_col = col_mapping.get("batch_effect", None)

    if not batch_col:
        return errors

    samples = meta.get("samples", [])
    if not samples:
        return errors

    # Check that batch column is present in sample rows
    first_row = samples[0] if isinstance(samples[0], dict) else {}
    available_cols = {k.strip().lower() for k in first_row.keys()}
    if batch_col.strip().lower() not in available_cols:
        errors.append(
            f"Batch correction column '{batch_col}' not found in metadata. "
            f"Available columns: {', '.join(sorted(first_row.keys()))}."
        )
        return errors

    # Check that each batch has at least 2 samples (ComBat-seq requirement)
    batch_counts: dict[str, int] = {}
    for row in samples:
        val = str(row.get(batch_col, "")).strip()
        if val:
            batch_counts[val] = batch_counts.get(val, 0) + 1

    singleton_batches = [b for b, c in batch_counts.items() if c < 2]
    if singleton_batches:
        errors.append(
            f"ComBat-seq requires at least 2 samples per batch. "
            f"Batch(es) with only 1 sample: {', '.join(sorted(singleton_batches))}. "
            f"Merge singleton batches or remove the batch column."
        )

    return errors


def validate_matrix_content(body: dict, submission) -> list[str]:
    """Pre-validate the uploaded count matrix: non-empty, all-numeric,
    non-negative values."""
    from pipeline.models import FileAsset

    errors = []
    input_data_type = body.get("input_data_type", "fastq")
    if input_data_type != "matrix":
        return errors

    matrix_asset = submission.file_assets.filter(
        file_role=FileAsset.FileRole.USER_COUNT_MATRIX
    ).first()
    if not matrix_asset or not os.path.isfile(matrix_asset.local_path):
        return errors  # already caught by validate_uploaded_files

    path = matrix_asset.local_path
    try:
        with open(path, "r", newline="") as f:
            sample = f.read(8192)

        # Detect delimiter
        sniffer = csv.Sniffer()
        try:
            dialect = sniffer.sniff(sample, delimiters=",\t")
        except csv.Error:
            dialect = csv.excel

        with open(path, "r", newline="") as f:
            reader = csv.reader(f, dialect)
            header = next(reader, None)

        if not header or len(header) < 2:
            errors.append(
                "Count matrix must have at least 2 columns "
                "(gene ID + 1 sample)."
            )
            return errors

        # Req 1 (matrix): need >= 3 columns (gene ID + >= 2 samples)
        if len(header) < 3:
            errors.append(
                "Count matrix must have at least 3 columns "
                "(gene ID + 2 or more samples). DESeq2 requires "
                "at least 2 samples."
            )
            return errors

        # Scan first 50 data rows for validity
        with open(path, "r", newline="") as f:
            reader = csv.reader(f, dialect)
            next(reader)  # skip header
            row_count = 0
            gene_ids_seen: dict[str, int] = {}
            for row in reader:
                # Track gene IDs for uniqueness (Req 4)
                if row:
                    gid = row[0].strip()
                    gene_ids_seen[gid] = gene_ids_seen.get(gid, 0) + 1

                if row_count < 50:
                    for val in row[1:]:  # skip gene ID column
                        val = val.strip()
                        # Req 5: no missing/NA/NaN/null
                        if not val or val.lower() in (
                            "na", "nan", "null"
                        ):
                            errors.append(
                                "Count matrix contains empty or missing "
                                "values (NA/NaN/null). All cells must "
                                "have integer counts."
                            )
                            return errors
                        try:
                            num = float(val)
                        except ValueError:
                            errors.append(
                                f"Count matrix contains non-numeric value: "
                                f"'{val}'. All values must be numeric."
                            )
                            return errors
                        if num < 0:
                            errors.append(
                                "Count matrix contains negative values. "
                                "Only non-negative raw counts are accepted."
                            )
                            return errors
                row_count += 1

        if row_count == 0:
            errors.append("Count matrix is empty (no data rows).")
            return errors

        # Req 4: duplicate gene ID check
        duplicates = [
            gid for gid, cnt in gene_ids_seen.items() if cnt > 1
        ]
        if duplicates:
            preview = ", ".join(duplicates[:5])
            extra = (
                f" (and {len(duplicates) - 5} more)"
                if len(duplicates) > 5 else ""
            )
            errors.append(
                f"Duplicate gene IDs found: {preview}{extra}. "
                f"Each gene ID must be unique."
            )

        # Req 6: matrix header / metadata sample name match
        meta = body.get("metadata_payload", {})
        meta_samples = meta.get("samples", [])
        if meta_samples and header:
            mat_sample_headers = set(h.strip() for h in header[1:])
            sample_key = (
                list(meta_samples[0].keys())[0]
                if isinstance(meta_samples[0], dict) and meta_samples[0]
                else "sample"
            )
            meta_sample_names = set(
                str(row.get(sample_key, "")).strip()
                for row in meta_samples
            )
            meta_sample_names.discard("")
            in_mat_only = mat_sample_headers - meta_sample_names
            in_meta_only = meta_sample_names - mat_sample_headers
            if in_mat_only or in_meta_only:
                parts = []
                if in_mat_only:
                    parts.append(
                        "in matrix but not metadata: "
                        + ", ".join(sorted(in_mat_only)[:5])
                    )
                if in_meta_only:
                    parts.append(
                        "in metadata but not matrix: "
                        + ", ".join(sorted(in_meta_only)[:5])
                    )
                errors.append(
                    "Matrix column headers and metadata sample names "
                    "must match exactly. Mismatched — "
                    + "; ".join(parts) + "."
                )

    except (OSError, UnicodeDecodeError):
        errors.append("Could not read the count matrix file.")

    return errors


def validate_bacterial_fasta(body: dict, submission) -> list[str]:
    """Flag unannotated bacterial FASTA (custom genome without GTF) for
    BASys2 Docker annotation engine."""
    from pipeline.models import FileAsset

    errors = []
    input_data_type = body.get("input_data_type", "fastq")
    reference_genome = body.get("reference_genome", "")

    if input_data_type not in ("fastq", "alignment"):
        return errors
    if reference_genome != "custom":
        return errors

    has_annotation = submission.file_assets.filter(
        file_role=FileAsset.FileRole.CUSTOM_GENOME_ANNOTATION
    ).exists()

    # If a FASTA is provided without annotation, flag for BASys2
    if not has_annotation:
        has_fasta = submission.file_assets.filter(
            file_role=FileAsset.FileRole.CUSTOM_GENOME_FASTA
        ).exists()
        if has_fasta:
            errors.append(
                "Unannotated genome FASTA detected. A GTF/GFF annotation "
                "file is required. If this is a bacterial genome without "
                "public annotation, it will be routed to the BASys2 "
                "annotation engine — please upload the FASTA only and "
                "contact support for BASys2 processing."
            )

    return errors


# ---------------------------------------------------------------------------
# Warnings (non-blocking, returned alongside errors)
# ---------------------------------------------------------------------------

def collect_warnings(body: dict, submission) -> list[str]:
    """Return non-blocking warnings that should be surfaced to the user."""
    warnings = []

    reference_genome = body.get("reference_genome", "")
    input_data_type = body.get("input_data_type", "fastq")
    assay_type = body.get("assay_type", "standard_rna")

    if reference_genome == "custom" and input_data_type == "fastq":
        assay_labels = {
            "standard_rna": "HISAT2",
            "chip_seq": "BWA",
            "methylation": "Bismark",
        }
        tool = assay_labels.get(assay_type, "aligner")
        warnings.append(
            f"Custom genome selected. This will trigger an on-demand "
            f"{tool} index build, which may add significant processing time."
        )

    # Batch correction with custom genome
    meta = body.get("metadata_payload", {})
    col_mapping = meta.get("column_mapping", {})
    if col_mapping.get("batch_effect"):
        warnings.append(
            "Batch correction (ComBat-seq) is enabled. Ensure batch "
            "assignments reflect real technical variation, not biological "
            "groups."
        )

    return warnings


# ---------------------------------------------------------------------------
# Orchestrator
# ---------------------------------------------------------------------------

# Ordered list of core validators (run for every submission)
CORE_VALIDATORS = [
    validate_base_fields,
    validate_uploaded_files,
    validate_custom_genome,
    validate_small_rna_genome,
    validate_paired_end_matching,
    validate_metadata,
    validate_chipseq_metadata,
    validate_batch_column,
    validate_matrix_content,
    validate_bacterial_fasta,
]


def validate_pipeline_submission(
    body: dict, submission
) -> tuple[list[str], list[str]]:
    """Run all validators and return (errors, warnings).

    Stops on the first validator that returns errors to avoid cascading
    messages from downstream validators.
    """
    errors: list[str] = []

    # Run core validators
    for validator in CORE_VALIDATORS:
        result = validator(body, submission)
        if result:
            errors.extend(result)

    # Run track-specific validators
    input_data_type = body.get("input_data_type", "fastq")
    assay_type = body.get("assay_type", "standard_rna")
    key = (input_data_type, assay_type)
    for validator in TRACK_VALIDATORS.get(key, []):
        result = validator(body, submission)
        if result:
            errors.extend(result)

    warnings = collect_warnings(body, submission) if not errors else []

    return errors, warnings


# ---------------------------------------------------------------------------
# Helper
# ---------------------------------------------------------------------------

def _extract_expected_stems(submission, input_data_type, library_type):
    """Extract expected sample stems from uploaded files."""
    from pipeline.models import FileAsset

    expected_stems = set()

    if input_data_type == "fastq":
        fastq_assets = submission.file_assets.filter(
            file_role=FileAsset.FileRole.RAW_FASTQ
        )
        fq_names = [
            os.path.basename(p)
            for p in fastq_assets.values_list("local_path", flat=True)
        ]
        if library_type == "paired":
            pair_re = re.compile(
                r'^(.+?)(?:_R[12]|_[12])\.(?:fq|fastq)\.gz$',
                re.IGNORECASE,
            )
            for name in fq_names:
                m = pair_re.match(name)
                if m:
                    expected_stems.add(m.group(1))
        else:
            for name in fq_names:
                stem = re.sub(
                    r'\.(fq|fastq)(\.gz)?$', '', name,
                    flags=re.IGNORECASE,
                )
                expected_stems.add(stem)
    elif input_data_type == "alignment":
        bam_assets = submission.file_assets.filter(
            file_role=FileAsset.FileRole.ALIGNMENT_BAM
        )
        for p in bam_assets.values_list("local_path", flat=True):
            stem = re.sub(
                r'\.(bam|cram)$', '', os.path.basename(p),
                flags=re.IGNORECASE,
            )
            expected_stems.add(stem)

    return expected_stems
