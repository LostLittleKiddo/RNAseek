import os
import uuid
from datetime import timedelta

from django.conf import settings
from django.db import models
from django.utils import timezone


def default_expiry():
    return timezone.now() + timedelta(days=14)


class Session(models.Model):
    session_id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    created_at = models.DateTimeField(auto_now_add=True)
    expires_at = models.DateTimeField(default=default_expiry)

    class Meta:
        db_table = "session"

    def __str__(self):
        return str(self.session_id)

    @property
    def is_expired(self):
        return timezone.now() >= self.expires_at


class AnalysisSubmission(models.Model):
    class InputDataType(models.TextChoices):
        FASTQ = "fastq", "FASTQ Files"
        ALIGNMENT = "alignment", "BAM/CRAM Alignment"
        MATRIX = "matrix", "Count Matrix"

    class AssayType(models.TextChoices):
        STANDARD_RNA = "standard_rna", "Standard RNA-Seq (Poly-A)"
        SMALL_RNA = "small_rna", "Small RNA / miRNA"
        CHIP_SEQ = "chip_seq", "ChIP-seq"
        METHYLATION = "methylation", "DNA Methylation (Bisulfite-seq)"

    class LibraryType(models.TextChoices):
        SINGLE = "single", "Single-End"
        PAIRED = "paired", "Paired-End"

    class Strandedness(models.TextChoices):
        UNSTRANDED = "unstranded", "Unstranded"
        FR_FIRSTSTRAND = "fr-firststrand", "Forward-Reverse (fr-firststrand)"
        FR_SECONDSTRAND = "fr-secondstrand", "Forward-Reverse (fr-secondstrand)"

    class MetadataMode(models.TextChoices):
        UPLOAD = "upload", "Upload CSV"
        MANUAL = "manual", "Manual Assignment"

    submission_id = models.UUIDField(
        primary_key=True, default=uuid.uuid4, editable=False
    )
    session = models.ForeignKey(
        Session, on_delete=models.CASCADE, related_name="submissions"
    )
    created_at = models.DateTimeField(auto_now_add=True)

    input_data_type = models.CharField(
        max_length=10,
        choices=InputDataType.choices,
        default=InputDataType.FASTQ,
    )
    assay_type = models.CharField(
        max_length=20,
        choices=AssayType.choices,
        default=AssayType.STANDARD_RNA,
    )

    library_type = models.CharField(
        max_length=10, choices=LibraryType.choices, blank=True
    )
    strandedness = models.CharField(
        max_length=20,
        choices=Strandedness.choices,
        default=Strandedness.UNSTRANDED,
    )
    reference_genome = models.CharField(max_length=100, blank=True)
    custom_genome_name = models.CharField(max_length=200, blank=True)
    metadata_mode = models.CharField(
        max_length=10, choices=MetadataMode.choices, blank=True
    )

    adjusted_pvalue = models.FloatField(default=0.05)
    min_log2fc = models.FloatField(default=-1.0)
    max_log2fc = models.FloatField(default=1.0)

    metadata_payload = models.JSONField(default=dict, blank=True)

    class Meta:
        db_table = "analysis_submission"

    def __str__(self):
        return f"Submission {self.submission_id}"

    @property
    def upload_dir(self):
        return os.path.join(
            str(settings.MEDIA_ROOT),
            "sessions",
            str(self.session_id),
            "submissions",
            str(self.submission_id),
        )


class FileAsset(models.Model):
    class FileRole(models.TextChoices):
        RAW_FASTQ = "RAW_FASTQ", "Raw FASTQ"
        ALIGNMENT_BAM = "ALIGNMENT_BAM", "Alignment BAM/CRAM"
        USER_COUNT_MATRIX = "USER_COUNT_MATRIX", "User Count Matrix"
        COUNT_MATRIX = "COUNT_MATRIX", "Count Matrix"
        NORMALIZED_COUNTS = "NORMALIZED_COUNTS", "Normalized Counts"
        DEG_TABLE = "DEG_TABLE", "Differential Expression Table"
        MULTIQC_REPORT = "MULTIQC_REPORT", "MultiQC Report"
        H5AD_PSEUDO = "H5AD_PSEUDO", "H5AD Pseudo-scRNA"
        HE_IMAGE_USER = "HE_IMAGE_USER", "H&E Image (User)"
        HE_IMAGE_GENERIC = "HE_IMAGE_GENERIC", "H&E Image (Generic)"
        CUSTOM_GENOME_FASTA = "CUSTOM_GENOME_FASTA", "Custom Genome FASTA"
        CUSTOM_GENOME_ANNOTATION = "CUSTOM_GENOME_ANNOTATION", "Custom Genome Annotation"
        METADATA_CSV = "METADATA_CSV", "Metadata CSV"
        PEAK_FILE = "PEAK_FILE", "ChIP-seq Peak File"
        METHYLATION_REPORT = "METHYLATION_REPORT", "Methylation Report"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    session = models.ForeignKey(
        Session, on_delete=models.CASCADE, related_name="file_assets"
    )
    submission = models.ForeignKey(
        AnalysisSubmission,
        on_delete=models.CASCADE,
        related_name="file_assets",
        null=True,
        blank=True,
    )
    file_role = models.CharField(max_length=30, choices=FileRole.choices)
    local_path = models.CharField(max_length=500)
    is_user_uploaded = models.BooleanField(default=True)

    class Meta:
        db_table = "file_asset"

    def __str__(self):
        return f"{self.file_role} – {self.local_path}"


class AnalysisJob(models.Model):
    class Status(models.TextChoices):
        PENDING = "PENDING", "Pending"
        RUNNING = "RUNNING", "Running"
        SUCCESS = "SUCCESS", "Success"
        FAILED = "FAILED", "Failed"

    job_id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    session = models.ForeignKey(
        Session, on_delete=models.CASCADE, related_name="analysis_jobs"
    )
    module_name = models.CharField(max_length=50)
    status = models.CharField(
        max_length=10, choices=Status.choices, default=Status.PENDING
    )
    result_payload = models.JSONField(default=dict, blank=True)
    step_progress = models.JSONField(default=dict, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "analysis_job"

    def __str__(self):
        return f"{self.module_name} [{self.status}]"
