import uuid
from datetime import timedelta

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


class FileAsset(models.Model):
    class FileRole(models.TextChoices):
        RAW_FASTQ = "RAW_FASTQ", "Raw FASTQ"
        COUNT_MATRIX = "COUNT_MATRIX", "Count Matrix"
        H5AD_PSEUDO = "H5AD_PSEUDO", "H5AD Pseudo-scRNA"
        HE_IMAGE_USER = "HE_IMAGE_USER", "H&E Image (User)"
        HE_IMAGE_GENERIC = "HE_IMAGE_GENERIC", "H&E Image (Generic)"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    session = models.ForeignKey(
        Session, on_delete=models.CASCADE, related_name="file_assets"
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

    class Meta:
        db_table = "analysis_job"

    def __str__(self):
        return f"{self.module_name} [{self.status}]"
