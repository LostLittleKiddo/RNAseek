"""
Tests for Dynamic Pipeline Entry Points.

Covers:
- Model: InputDataType choices, new FileRole values
- Views: CorePipelineView conditional validation for fastq/alignment/matrix
- Views: ChunkUploadView routing for new file roles
- Tasks: Router dispatch (_route_fastq, _route_alignment, _route_matrix)
- Tasks: _resolve_genome, _run_featurecounts helpers
- Tasks: _route_matrix CSV validation (non-numeric, negative, empty)
"""
import csv
import json
import os
import tempfile
from unittest.mock import MagicMock, patch

from django.test import RequestFactory, TestCase

from pipeline.models import AnalysisJob, AnalysisSubmission, FileAsset, Session


class InputDataTypeModelTest(TestCase):
    """Test that the InputDataType field and new FileRole values work."""

    def setUp(self):
        self.session = Session.objects.create()

    def test_default_input_data_type_is_fastq(self):
        sub = AnalysisSubmission.objects.create(session=self.session)
        self.assertEqual(sub.input_data_type, "fastq")

    def test_can_set_alignment(self):
        sub = AnalysisSubmission.objects.create(
            session=self.session, input_data_type="alignment"
        )
        sub.refresh_from_db()
        self.assertEqual(sub.input_data_type, "alignment")

    def test_can_set_matrix(self):
        sub = AnalysisSubmission.objects.create(
            session=self.session, input_data_type="matrix"
        )
        sub.refresh_from_db()
        self.assertEqual(sub.input_data_type, "matrix")

    def test_choices_list(self):
        choices = dict(AnalysisSubmission.InputDataType.choices)
        self.assertIn("fastq", choices)
        self.assertIn("alignment", choices)
        self.assertIn("matrix", choices)

    def test_alignment_bam_file_role(self):
        sub = AnalysisSubmission.objects.create(session=self.session)
        fa = FileAsset.objects.create(
            session=self.session,
            submission=sub,
            file_role=FileAsset.FileRole.ALIGNMENT_BAM,
            local_path="/tmp/test.bam",
        )
        fa.refresh_from_db()
        self.assertEqual(fa.file_role, "ALIGNMENT_BAM")

    def test_user_count_matrix_file_role(self):
        sub = AnalysisSubmission.objects.create(session=self.session)
        fa = FileAsset.objects.create(
            session=self.session,
            submission=sub,
            file_role=FileAsset.FileRole.USER_COUNT_MATRIX,
            local_path="/tmp/counts.csv",
        )
        fa.refresh_from_db()
        self.assertEqual(fa.file_role, "USER_COUNT_MATRIX")


class CorePipelineViewValidationTest(TestCase):
    """Test CorePipelineView conditional validation per entry point."""

    def setUp(self):
        self.factory = RequestFactory()
        self.session = Session.objects.create()
        self.submission = AnalysisSubmission.objects.create(session=self.session)

        # Common valid metadata payload
        self.meta_payload = {
            "samples": [
                {"_sample_name": "s1", "condition": "A"},
                {"_sample_name": "s2", "condition": "B"},
            ],
            "column_mapping": {
                "primary_group": "condition",
                "batch_effect": None,
                "additional_covariates": [],
            },
            "contrasts": [],
        }

    def _post(self, body):
        from pipeline.views import CorePipelineView
        req = self.factory.post(
            "/api/pipeline/core",
            data=json.dumps(body),
            content_type="application/json",
        )
        req.session_obj = self.session
        return CorePipelineView.as_view()(req)

    # ── FASTQ entry tests ──

    def test_fastq_missing_library_type(self):
        body = {
            "submission_id": str(self.submission.submission_id),
            "input_data_type": "fastq",
            "library_type": "",
            "strandedness": "unstranded",
            "reference_genome": "r64",
            "metadata_mode": "manual",
            "metadata_payload": self.meta_payload,
        }
        resp = self._post(body)
        self.assertEqual(resp.status_code, 400)
        self.assertIn("library_type", json.loads(resp.content)["error"])

    def test_fastq_missing_files(self):
        body = {
            "submission_id": str(self.submission.submission_id),
            "input_data_type": "fastq",
            "library_type": "single",
            "strandedness": "unstranded",
            "reference_genome": "r64",
            "metadata_mode": "manual",
            "metadata_payload": self.meta_payload,
        }
        resp = self._post(body)
        self.assertEqual(resp.status_code, 400)
        self.assertIn("FASTQ", json.loads(resp.content)["error"])

    def test_fastq_missing_genome(self):
        # Add a FASTQ file asset
        FileAsset.objects.create(
            session=self.session,
            submission=self.submission,
            file_role=FileAsset.FileRole.RAW_FASTQ,
            local_path="/tmp/s1.fq.gz",
        )
        body = {
            "submission_id": str(self.submission.submission_id),
            "input_data_type": "fastq",
            "library_type": "single",
            "strandedness": "unstranded",
            "reference_genome": "",
            "metadata_mode": "manual",
            "metadata_payload": self.meta_payload,
        }
        resp = self._post(body)
        self.assertEqual(resp.status_code, 400)
        self.assertIn("genome", json.loads(resp.content)["error"].lower())

    # ── Alignment entry tests ──

    def test_alignment_no_library_type_required(self):
        """Alignment entry should not require library_type in the same way."""
        body = {
            "submission_id": str(self.submission.submission_id),
            "input_data_type": "alignment",
            "reference_genome": "r64",
            "metadata_mode": "manual",
            "metadata_payload": self.meta_payload,
        }
        resp = self._post(body)
        # Should fail because no BAM files, not because of library_type
        self.assertEqual(resp.status_code, 400)
        self.assertIn("BAM", json.loads(resp.content)["error"])

    def test_alignment_missing_bam_files(self):
        body = {
            "submission_id": str(self.submission.submission_id),
            "input_data_type": "alignment",
            "reference_genome": "r64",
            "metadata_mode": "manual",
            "metadata_payload": self.meta_payload,
        }
        resp = self._post(body)
        self.assertEqual(resp.status_code, 400)
        self.assertIn("BAM", json.loads(resp.content)["error"])

    def test_alignment_missing_genome(self):
        FileAsset.objects.create(
            session=self.session,
            submission=self.submission,
            file_role=FileAsset.FileRole.ALIGNMENT_BAM,
            local_path="/tmp/s1.bam",
        )
        body = {
            "submission_id": str(self.submission.submission_id),
            "input_data_type": "alignment",
            "reference_genome": "",
            "metadata_mode": "manual",
            "metadata_payload": self.meta_payload,
        }
        resp = self._post(body)
        self.assertEqual(resp.status_code, 400)
        self.assertIn("genome", json.loads(resp.content)["error"].lower())

    @patch("pipeline.views.api.run_core_pipeline")
    def test_alignment_custom_genome_only_needs_gtf(self, mock_task):
        """Alignment custom genome should only require GTF, not FASTA."""
        mock_task.apply_async.return_value = MagicMock()
        FileAsset.objects.create(
            session=self.session,
            submission=self.submission,
            file_role=FileAsset.FileRole.ALIGNMENT_BAM,
            local_path="/tmp/s1.bam",
        )
        # Add only annotation, no FASTA
        FileAsset.objects.create(
            session=self.session,
            submission=self.submission,
            file_role=FileAsset.FileRole.CUSTOM_GENOME_ANNOTATION,
            local_path="/tmp/custom.gtf",
        )
        body = {
            "submission_id": str(self.submission.submission_id),
            "input_data_type": "alignment",
            "reference_genome": "custom",
            "custom_genome_name": "MyGenome",
            "metadata_mode": "manual",
            "metadata_payload": self.meta_payload,
        }
        resp = self._post(body)
        # Should pass custom genome validation (only GTF needed)
        # Might fail at Celery dispatch but not at genome validation
        data = json.loads(resp.content)
        # If error, it should NOT be about custom genome files
        if resp.status_code == 400:
            self.assertNotIn("GTF", data.get("error", ""))
            self.assertNotIn("FASTA", data.get("error", ""))

    def test_alignment_custom_genome_needs_fasta_for_fastq_entry(self):
        """FASTQ custom genome needs both FASTA and GTF."""
        FileAsset.objects.create(
            session=self.session,
            submission=self.submission,
            file_role=FileAsset.FileRole.RAW_FASTQ,
            local_path="/tmp/s1.fq.gz",
        )
        FileAsset.objects.create(
            session=self.session,
            submission=self.submission,
            file_role=FileAsset.FileRole.CUSTOM_GENOME_ANNOTATION,
            local_path="/tmp/custom.gtf",
        )
        body = {
            "submission_id": str(self.submission.submission_id),
            "input_data_type": "fastq",
            "library_type": "single",
            "strandedness": "unstranded",
            "reference_genome": "custom",
            "custom_genome_name": "MyGenome",
            "metadata_mode": "manual",
            "metadata_payload": self.meta_payload,
        }
        resp = self._post(body)
        self.assertEqual(resp.status_code, 400)
        data = json.loads(resp.content)
        self.assertIn("FASTA", data["error"])

    # ── Matrix entry tests ──

    @patch("pipeline.views.api.run_core_pipeline")
    def test_matrix_no_genome_required(self, mock_task):
        """Matrix entry should not require reference_genome."""
        mock_task.apply_async.return_value = MagicMock()
        FileAsset.objects.create(
            session=self.session,
            submission=self.submission,
            file_role=FileAsset.FileRole.USER_COUNT_MATRIX,
            local_path="/tmp/counts.csv",
        )
        body = {
            "submission_id": str(self.submission.submission_id),
            "input_data_type": "matrix",
            "reference_genome": "",
            "metadata_mode": "manual",
            "metadata_payload": self.meta_payload,
        }
        resp = self._post(body)
        data = json.loads(resp.content)
        # Should not fail on genome
        if resp.status_code == 400:
            self.assertNotIn("genome", data.get("error", "").lower())

    @patch("pipeline.views.api.run_core_pipeline")
    def test_matrix_no_library_type_required(self, mock_task):
        """Matrix entry should not require library_type."""
        mock_task.apply_async.return_value = MagicMock()
        FileAsset.objects.create(
            session=self.session,
            submission=self.submission,
            file_role=FileAsset.FileRole.USER_COUNT_MATRIX,
            local_path="/tmp/counts.csv",
        )
        body = {
            "submission_id": str(self.submission.submission_id),
            "input_data_type": "matrix",
            "metadata_mode": "manual",
            "metadata_payload": self.meta_payload,
        }
        resp = self._post(body)
        data = json.loads(resp.content)
        if resp.status_code == 400:
            self.assertNotIn("library_type", data.get("error", ""))

    def test_matrix_missing_count_matrix(self):
        body = {
            "submission_id": str(self.submission.submission_id),
            "input_data_type": "matrix",
            "metadata_mode": "manual",
            "metadata_payload": self.meta_payload,
        }
        resp = self._post(body)
        self.assertEqual(resp.status_code, 400)
        self.assertIn("count matrix", json.loads(resp.content)["error"].lower())

    def test_invalid_input_data_type(self):
        body = {
            "submission_id": str(self.submission.submission_id),
            "input_data_type": "invalid",
        }
        resp = self._post(body)
        self.assertEqual(resp.status_code, 400)
        self.assertIn("input_data_type", json.loads(resp.content)["error"])

    # ── Successful dispatch test (mocked) ──

    @patch("pipeline.views.api.run_core_pipeline")
    def test_fastq_successful_dispatch(self, mock_task):
        mock_task.apply_async.return_value = MagicMock()
        FileAsset.objects.create(
            session=self.session,
            submission=self.submission,
            file_role=FileAsset.FileRole.RAW_FASTQ,
            local_path="/tmp/s1.fq.gz",
        )
        body = {
            "submission_id": str(self.submission.submission_id),
            "input_data_type": "fastq",
            "library_type": "single",
            "strandedness": "unstranded",
            "reference_genome": "r64",
            "quant_level": "gene",
            "metadata_mode": "manual",
            "adjusted_pvalue": 0.05,
            "min_log2fc": -1.0,
            "max_log2fc": 1.0,
            "metadata_payload": self.meta_payload,
        }
        resp = self._post(body)
        self.assertEqual(resp.status_code, 200)
        data = json.loads(resp.content)
        self.assertIn("job_id", data)
        # Verify input_data_type was persisted
        self.submission.refresh_from_db()
        self.assertEqual(self.submission.input_data_type, "fastq")

    @patch("pipeline.views.api.run_core_pipeline")
    def test_alignment_successful_dispatch(self, mock_task):
        mock_task.apply_async.return_value = MagicMock()
        FileAsset.objects.create(
            session=self.session,
            submission=self.submission,
            file_role=FileAsset.FileRole.ALIGNMENT_BAM,
            local_path="/tmp/s1.bam",
        )
        body = {
            "submission_id": str(self.submission.submission_id),
            "input_data_type": "alignment",
            "library_type": "single",
            "strandedness": "unstranded",
            "reference_genome": "r64",
            "quant_level": "gene",
            "metadata_mode": "manual",
            "adjusted_pvalue": 0.05,
            "min_log2fc": -1.0,
            "max_log2fc": 1.0,
            "metadata_payload": self.meta_payload,
        }
        resp = self._post(body)
        self.assertEqual(resp.status_code, 200)
        self.submission.refresh_from_db()
        self.assertEqual(self.submission.input_data_type, "alignment")

    @patch("pipeline.views.api.run_core_pipeline")
    def test_matrix_successful_dispatch(self, mock_task):
        mock_task.apply_async.return_value = MagicMock()
        FileAsset.objects.create(
            session=self.session,
            submission=self.submission,
            file_role=FileAsset.FileRole.USER_COUNT_MATRIX,
            local_path="/tmp/counts.csv",
        )
        body = {
            "submission_id": str(self.submission.submission_id),
            "input_data_type": "matrix",
            "metadata_mode": "manual",
            "adjusted_pvalue": 0.05,
            "min_log2fc": -1.0,
            "max_log2fc": 1.0,
            "metadata_payload": self.meta_payload,
        }
        resp = self._post(body)
        self.assertEqual(resp.status_code, 200)
        self.submission.refresh_from_db()
        self.assertEqual(self.submission.input_data_type, "matrix")


class ChunkUploadRoutingTest(TestCase):
    """Test that ChunkUploadView routes ALIGNMENT_BAM and USER_COUNT_MATRIX to correct subdirs."""

    def setUp(self):
        self.factory = RequestFactory()
        self.session = Session.objects.create()
        self.submission = AnalysisSubmission.objects.create(session=self.session)
        os.makedirs(self.submission.upload_dir, exist_ok=True)

    def _upload_chunk(self, filename, file_role, content=b"fake data"):
        from django.core.files.uploadedfile import SimpleUploadedFile
        from pipeline.views import ChunkUploadView

        uploaded = SimpleUploadedFile(filename, content)
        req = self.factory.post("/api/upload/chunk", {
            "file": uploaded,
            "filename": filename,
            "chunk_index": "0",
            "total_chunks": "1",
            "submission_id": str(self.submission.submission_id),
            "file_role": file_role,
        })
        req.session_obj = self.session
        return ChunkUploadView.as_view()(req)

    def test_alignment_bam_routes_to_aligned(self):
        resp = self._upload_chunk("sample.bam", "ALIGNMENT_BAM")
        self.assertEqual(resp.status_code, 200)
        expected = os.path.join(self.submission.upload_dir, "aligned", "sample.bam")
        self.assertTrue(os.path.exists(expected))

    def test_user_count_matrix_routes_to_counts(self):
        resp = self._upload_chunk("counts.csv", "USER_COUNT_MATRIX")
        self.assertEqual(resp.status_code, 200)
        expected = os.path.join(self.submission.upload_dir, "counts", "counts.csv")
        self.assertTrue(os.path.exists(expected))

    def test_raw_fastq_routes_to_raw(self):
        resp = self._upload_chunk("s1.fq.gz", "RAW_FASTQ")
        self.assertEqual(resp.status_code, 200)
        expected = os.path.join(self.submission.upload_dir, "raw", "s1.fq.gz")
        self.assertTrue(os.path.exists(expected))


class TaskRouterTest(TestCase):
    """Test task router dispatch and individual route functions."""

    def setUp(self):
        self.session = Session.objects.create()

    def _make_job(self):
        """Create a real AnalysisJob for route functions that update step_progress."""
        return AnalysisJob.objects.create(
            session=self.session,
            module_name="CORE_PIPELINE",
            step_progress={
                "pipeline_steps": [],
                "completed_steps": [],
                "current_step": None,
                "failed_step": None,
            },
        )

    def test_resolve_genome_preindexed(self):
        from pipeline.tasks import _resolve_genome
        idx, fasta, gtf = _resolve_genome("r64", "/tmp", build_hisat2=False)
        self.assertIn("Yeast_sacCer3", idx)
        self.assertIsNotNone(fasta)
        self.assertTrue(gtf.endswith(".gtf"))

    def test_resolve_genome_custom_no_build(self):
        """Custom genome with build_hisat2=False should return None for idx."""
        from pipeline.tasks import _resolve_genome
        with tempfile.TemporaryDirectory() as tmpdir:
            custom_dir = os.path.join(tmpdir, "custom_genome")
            os.makedirs(custom_dir)
            # Create dummy files
            with open(os.path.join(custom_dir, "genome.fa"), "w") as f:
                f.write(">chr1\nACGT\n")
            with open(os.path.join(custom_dir, "genes.gtf"), "w") as f:
                f.write("chr1\ttest\texon\t1\t100\t.\t+\t.\tgene_id \"g1\";\n")

            idx, fasta, gtf = _resolve_genome("custom", tmpdir, build_hisat2=False)
            self.assertIsNone(idx)
            self.assertIsNotNone(fasta)
            self.assertIsNotNone(gtf)

    def test_route_matrix_valid_csv(self):
        """_route_matrix with a valid CSV should produce raw_counts.csv."""
        from pipeline.tasks import _route_matrix

        sub = AnalysisSubmission.objects.create(
            session=self.session,
            input_data_type="matrix",
            metadata_mode="manual",
            metadata_payload={
                "samples": [
                    {"_sample_name": "s1", "condition": "A"},
                    {"_sample_name": "s2", "condition": "B"},
                ],
                "column_mapping": {
                    "primary_group": "condition",
                    "batch_effect": None,
                    "additional_covariates": [],
                },
                "contrasts": [],
            },
        )
        os.makedirs(sub.upload_dir, exist_ok=True)
        counts_path = os.path.join(sub.upload_dir, "user_counts.csv")
        with open(counts_path, "w", newline="") as f:
            writer = csv.writer(f)
            writer.writerow(["gene_id", "s1", "s2"])
            writer.writerow(["gene1", "100", "200"])
            writer.writerow(["gene2", "50", "80"])
            writer.writerow(["gene3", "10", "20"])

        FileAsset.objects.create(
            session=self.session,
            submission=sub,
            file_role=FileAsset.FileRole.USER_COUNT_MATRIX,
            local_path=counts_path,
        )

        with patch("pipeline.stats.run_stage2_stats") as mock_stats:
            mock_stats.return_value = {"deseq2_results": "/tmp/results.csv"}
            result = _route_matrix(sub, self._make_job())

        self.assertIn("count_matrix", result)
        canonical = os.path.join(sub.upload_dir, "counts", "raw_counts.csv")
        self.assertTrue(os.path.exists(canonical))

    def test_route_matrix_non_numeric(self):
        """_route_matrix should raise on non-numeric values."""
        from pipeline.tasks import _route_matrix

        sub = AnalysisSubmission.objects.create(
            session=self.session,
            input_data_type="matrix",
            metadata_mode="manual",
            metadata_payload={
                "samples": [],
                "column_mapping": {"primary_group": "condition"},
                "contrasts": [],
            },
        )
        os.makedirs(sub.upload_dir, exist_ok=True)
        counts_path = os.path.join(sub.upload_dir, "bad_counts.csv")
        with open(counts_path, "w", newline="") as f:
            writer = csv.writer(f)
            writer.writerow(["gene_id", "s1", "s2"])
            writer.writerow(["gene1", "hello", "world"])

        FileAsset.objects.create(
            session=self.session,
            submission=sub,
            file_role=FileAsset.FileRole.USER_COUNT_MATRIX,
            local_path=counts_path,
        )

        with self.assertRaises(ValueError):
            _route_matrix(sub, self._make_job())

    def test_route_matrix_negative_values(self):
        """_route_matrix should raise on negative values."""
        from pipeline.tasks import _route_matrix

        sub = AnalysisSubmission.objects.create(
            session=self.session,
            input_data_type="matrix",
            metadata_mode="manual",
            metadata_payload={
                "samples": [],
                "column_mapping": {"primary_group": "condition"},
                "contrasts": [],
            },
        )
        os.makedirs(sub.upload_dir, exist_ok=True)
        counts_path = os.path.join(sub.upload_dir, "neg_counts.csv")
        with open(counts_path, "w", newline="") as f:
            writer = csv.writer(f)
            writer.writerow(["gene_id", "s1", "s2"])
            writer.writerow(["gene1", "-5", "10"])
            writer.writerow(["gene2", "20", "30"])

        FileAsset.objects.create(
            session=self.session,
            submission=sub,
            file_role=FileAsset.FileRole.USER_COUNT_MATRIX,
            local_path=counts_path,
        )

        with self.assertRaises(ValueError):
            _route_matrix(sub, self._make_job())

    def test_route_matrix_empty_csv(self):
        """_route_matrix should raise on empty matrix."""
        from pipeline.tasks import _route_matrix

        sub = AnalysisSubmission.objects.create(
            session=self.session,
            input_data_type="matrix",
            metadata_mode="manual",
            metadata_payload={
                "samples": [],
                "column_mapping": {"primary_group": "condition"},
                "contrasts": [],
            },
        )
        os.makedirs(sub.upload_dir, exist_ok=True)
        counts_path = os.path.join(sub.upload_dir, "empty.csv")
        with open(counts_path, "w", newline="") as f:
            writer = csv.writer(f)
            writer.writerow(["gene_id"])  # header only, no sample columns

        FileAsset.objects.create(
            session=self.session,
            submission=sub,
            file_role=FileAsset.FileRole.USER_COUNT_MATRIX,
            local_path=counts_path,
        )

        with self.assertRaises(ValueError):
            _route_matrix(sub, self._make_job())

    def test_route_matrix_tsv(self):
        """_route_matrix should handle TSV files."""
        from pipeline.tasks import _route_matrix

        sub = AnalysisSubmission.objects.create(
            session=self.session,
            input_data_type="matrix",
            metadata_mode="manual",
            metadata_payload={
                "samples": [
                    {"_sample_name": "s1", "condition": "A"},
                    {"_sample_name": "s2", "condition": "B"},
                ],
                "column_mapping": {
                    "primary_group": "condition",
                    "batch_effect": None,
                    "additional_covariates": [],
                },
                "contrasts": [],
            },
        )
        os.makedirs(sub.upload_dir, exist_ok=True)
        counts_path = os.path.join(sub.upload_dir, "counts.tsv")
        with open(counts_path, "w", newline="") as f:
            writer = csv.writer(f, delimiter="\t")
            writer.writerow(["gene_id", "s1", "s2"])
            writer.writerow(["gene1", "100", "200"])
            writer.writerow(["gene2", "50", "80"])

        FileAsset.objects.create(
            session=self.session,
            submission=sub,
            file_role=FileAsset.FileRole.USER_COUNT_MATRIX,
            local_path=counts_path,
        )

        with patch("pipeline.stats.run_stage2_stats") as mock_stats:
            mock_stats.return_value = {"deseq2_results": "/tmp/results.csv"}
            result = _route_matrix(sub, self._make_job())

        self.assertIn("count_matrix", result)

    @patch("pipeline.tasks._featurecounts._run")
    def test_route_alignment_calls_featurecounts(self, mock_run):
        """_route_alignment should call featureCounts on BAM files."""
        from pipeline.tasks import _route_alignment

        sub = AnalysisSubmission.objects.create(
            session=self.session,
            input_data_type="alignment",
            reference_genome="r64",
            library_type="single",
            strandedness="unstranded",
            metadata_mode="manual",
            metadata_payload={
                "quant_level": "gene",
                "samples": [
                    {"_sample_name": "s1", "condition": "A"},
                    {"_sample_name": "s2", "condition": "B"},
                ],
                "column_mapping": {
                    "primary_group": "condition",
                    "batch_effect": None,
                    "additional_covariates": [],
                },
                "contrasts": [],
            },
        )
        os.makedirs(sub.upload_dir, exist_ok=True)
        aligned_dir = os.path.join(sub.upload_dir, "aligned")
        os.makedirs(aligned_dir, exist_ok=True)

        # Create dummy BAM files
        bam1 = os.path.join(aligned_dir, "s1.bam")
        bam2 = os.path.join(aligned_dir, "s2.bam")
        for p in (bam1, bam2):
            with open(p, "w") as f:
                f.write("fake")
            with open(p + ".bai", "w") as f:
                f.write("fake")

        FileAsset.objects.create(
            session=self.session, submission=sub,
            file_role=FileAsset.FileRole.ALIGNMENT_BAM, local_path=bam1,
        )
        FileAsset.objects.create(
            session=self.session, submission=sub,
            file_role=FileAsset.FileRole.ALIGNMENT_BAM, local_path=bam2,
        )

        # Mock _run and featurecounts output
        def mock_run_side(cmd, cwd=None):
            # When featureCounts is called, create the output file
            if "featureCounts" in cmd:
                fc_out = os.path.join(sub.upload_dir, "counts", "featurecounts_output.txt")
                with open(fc_out, "w") as f:
                    f.write("# Program:featureCounts\n")
                    f.write(f"Geneid\tChr\tStart\tEnd\tStrand\tLength\t{bam1}\t{bam2}\n")
                    f.write("gene1\tchr1\t1\t100\t+\t100\t50\t60\n")
                    f.write("gene2\tchr1\t200\t300\t+\t100\t30\t40\n")
            return MagicMock()

        mock_run.side_effect = mock_run_side

        with patch("pipeline.stats.run_stage2_stats") as mock_stats:
            mock_stats.return_value = {"deseq2_results": "/tmp/results.csv"}
            result = _route_alignment(sub, self._make_job())

        self.assertIn("count_matrix", result)
        # Verify featureCounts was called
        fc_calls = [c for c in mock_run.call_args_list if "featureCounts" in str(c)]
        self.assertTrue(len(fc_calls) > 0, "featureCounts should have been called")
