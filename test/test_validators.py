"""
Tests for pipeline pre-submission validators.

Covers:
- validate_base_fields: input_data_type, assay_type, library_type, strandedness, genome, thresholds
- validate_uploaded_files: FASTQ, BAM, matrix file presence; paired-end even count
- validate_custom_genome: FASTA + GTF/GFF requirements
- validate_small_rna_genome: MIRBASE restriction, custom rejection
- validate_paired_end_matching: R1/R2 stem pairing
- validate_chipseq_metadata: input/control + treatment split
- validate_batch_column: batch column existence, ComBat-seq singleton check
- validate_matrix_content: non-empty, all-numeric, non-negative
- validate_metadata: samples, first column, column_mapping, contrasts
- validate_pipeline_submission: full orchestration
- CorePipelineView integration: returns {error, errors} on 400
"""

import json
import os
import tempfile

from unittest.mock import MagicMock, patch

from django.test import RequestFactory, TestCase

from pipeline.models import AnalysisJob, AnalysisSubmission, FileAsset, Session
from pipeline.validators import (
    validate_base_fields,
    validate_batch_column,
    validate_chipseq_metadata,
    validate_custom_genome,
    validate_matrix_content,
    validate_metadata,
    validate_paired_end_matching,
    validate_pipeline_submission,
    validate_small_rna_genome,
    validate_uploaded_files,
)


class _ValidatorTestBase(TestCase):
    """Shared setup for validator tests."""

    def setUp(self):
        self.session = Session.objects.create()
        self.submission = AnalysisSubmission.objects.create(session=self.session)
        self.meta_payload = {
            "samples": [
                {"_sample_name": "s1", "condition": "A"},
                {"_sample_name": "s2", "condition": "B"},
            ],
            "column_mapping": {"primary_group": "condition"},
            "contrasts": [],
        }

    def _base_body(self, **overrides):
        body = {
            "submission_id": str(self.submission.submission_id),
            "input_data_type": "fastq",
            "assay_type": "standard_rna",
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
        body.update(overrides)
        return body


# ── Base Field Validation ──


class BaseFieldValidationTest(_ValidatorTestBase):

    def test_valid_fastq_body(self):
        errs = validate_base_fields(self._base_body(), self.submission)
        self.assertEqual(errs, [])

    def test_invalid_input_data_type(self):
        errs = validate_base_fields(
            self._base_body(input_data_type="invalid"), self.submission
        )
        self.assertTrue(any("input_data_type" in e for e in errs))

    def test_invalid_assay_type_fastq(self):
        errs = validate_base_fields(
            self._base_body(assay_type="invalid_assay"), self.submission
        )
        self.assertTrue(any("assay_type" in e for e in errs))

    def test_assay_type_ignored_for_matrix(self):
        errs = validate_base_fields(
            self._base_body(
                input_data_type="matrix",
                assay_type="invalid",
                library_type="",
                reference_genome="",
            ),
            self.submission,
        )
        self.assertFalse(any("assay_type" in e for e in errs))

    def test_invalid_library_type(self):
        errs = validate_base_fields(
            self._base_body(library_type="triple"), self.submission
        )
        self.assertTrue(any("library_type" in e for e in errs))

    def test_missing_reference_genome(self):
        errs = validate_base_fields(
            self._base_body(reference_genome=""), self.submission
        )
        self.assertTrue(any("genome" in e.lower() for e in errs))

    def test_invalid_adjusted_pvalue(self):
        errs = validate_base_fields(
            self._base_body(adjusted_pvalue=0), self.submission
        )
        self.assertTrue(any("adjusted_pvalue" in e for e in errs))

    def test_pvalue_above_1(self):
        errs = validate_base_fields(
            self._base_body(adjusted_pvalue=1.5), self.submission
        )
        self.assertTrue(any("adjusted_pvalue" in e for e in errs))


# ── File Validation ──


class UploadedFilesValidationTest(_ValidatorTestBase):

    def test_fastq_no_files(self):
        errs = validate_uploaded_files(self._base_body(), self.submission)
        self.assertTrue(any("FASTQ" in e for e in errs))

    def test_fastq_with_files(self):
        FileAsset.objects.create(
            session=self.session,
            submission=self.submission,
            file_role=FileAsset.FileRole.RAW_FASTQ,
            local_path="/tmp/test.fq.gz",
        )
        FileAsset.objects.create(
            session=self.session,
            submission=self.submission,
            file_role=FileAsset.FileRole.RAW_FASTQ,
            local_path="/tmp/test2.fq.gz",
        )
        errs = validate_uploaded_files(self._base_body(), self.submission)
        self.assertEqual(errs, [])

    def test_paired_end_odd_count(self):
        FileAsset.objects.create(
            session=self.session,
            submission=self.submission,
            file_role=FileAsset.FileRole.RAW_FASTQ,
            local_path="/tmp/s1_R1.fq.gz",
        )
        errs = validate_uploaded_files(
            self._base_body(library_type="paired"), self.submission
        )
        self.assertTrue(any("even" in e.lower() for e in errs))

    def test_bam_no_files(self):
        errs = validate_uploaded_files(
            self._base_body(input_data_type="alignment"), self.submission
        )
        self.assertTrue(any("BAM" in e for e in errs))

    def test_matrix_no_files(self):
        errs = validate_uploaded_files(
            self._base_body(input_data_type="matrix"), self.submission
        )
        self.assertTrue(any("matrix" in e.lower() for e in errs))


# ── Custom Genome Validation ──


class CustomGenomeValidationTest(_ValidatorTestBase):

    def test_no_error_when_not_custom(self):
        errs = validate_custom_genome(self._base_body(), self.submission)
        self.assertEqual(errs, [])

    def test_missing_custom_name(self):
        errs = validate_custom_genome(
            self._base_body(reference_genome="custom", custom_genome_name=""),
            self.submission,
        )
        self.assertTrue(any("name" in e.lower() for e in errs))

    def test_missing_fasta_and_annotation(self):
        errs = validate_custom_genome(
            self._base_body(reference_genome="custom", custom_genome_name="MyGenome"),
            self.submission,
        )
        self.assertTrue(any("FASTA" in e for e in errs))

    def test_alignment_only_needs_annotation(self):
        FileAsset.objects.create(
            session=self.session,
            submission=self.submission,
            file_role=FileAsset.FileRole.CUSTOM_GENOME_ANNOTATION,
            local_path="/tmp/test.gtf",
        )
        errs = validate_custom_genome(
            self._base_body(
                input_data_type="alignment",
                reference_genome="custom",
                custom_genome_name="MyGenome",
            ),
            self.submission,
        )
        self.assertEqual(errs, [])


# ── Small RNA Genome ──


class SmallRnaGenomeTest(_ValidatorTestBase):

    def test_valid_mirbase_genome(self):
        errs = validate_small_rna_genome(
            self._base_body(assay_type="small_rna", reference_genome="hg38"),
            self.submission,
        )
        self.assertEqual(errs, [])

    def test_custom_genome_rejected(self):
        errs = validate_small_rna_genome(
            self._base_body(assay_type="small_rna", reference_genome="custom"),
            self.submission,
        )
        self.assertTrue(any("Custom" in e for e in errs))

    def test_unsupported_genome_rejected(self):
        errs = validate_small_rna_genome(
            self._base_body(assay_type="small_rna", reference_genome="susScr11"),
            self.submission,
        )
        self.assertTrue(any("miRBase" in e for e in errs))

    def test_skipped_for_standard_rna(self):
        errs = validate_small_rna_genome(
            self._base_body(assay_type="standard_rna", reference_genome="custom"),
            self.submission,
        )
        self.assertEqual(errs, [])


# ── Paired-End Matching ──


class PairedEndMatchingTest(_ValidatorTestBase):

    def _add_files(self, names):
        for name in names:
            FileAsset.objects.create(
                session=self.session,
                submission=self.submission,
                file_role=FileAsset.FileRole.RAW_FASTQ,
                local_path=f"/tmp/{name}",
            )

    def test_matched_pairs_pass(self):
        self._add_files(["s1_R1.fq.gz", "s1_R2.fq.gz", "s2_R1.fq.gz", "s2_R2.fq.gz"])
        errs = validate_paired_end_matching(
            self._base_body(library_type="paired"), self.submission
        )
        self.assertEqual(errs, [])

    def test_missing_r2_detected(self):
        self._add_files(["s1_R1.fq.gz", "s1_R2.fq.gz", "s2_R1.fq.gz"])
        errs = validate_paired_end_matching(
            self._base_body(library_type="paired"), self.submission
        )
        self.assertTrue(any("Missing R2" in e for e in errs))

    def test_unmatched_naming_detected(self):
        self._add_files(["sampleA.fq.gz", "sampleB.fq.gz"])
        errs = validate_paired_end_matching(
            self._base_body(library_type="paired"), self.submission
        )
        self.assertTrue(any("naming" in e.lower() for e in errs))

    def test_skipped_for_single_end(self):
        self._add_files(["s1.fq.gz"])
        errs = validate_paired_end_matching(
            self._base_body(library_type="single"), self.submission
        )
        self.assertEqual(errs, [])


# ── ChIP-seq Metadata ──


class ChipSeqMetadataTest(_ValidatorTestBase):

    def test_valid_chipseq_metadata(self):
        body = self._base_body(
            assay_type="chip_seq",
            metadata_payload={
                "samples": [
                    {"_sample_name": "ip1", "condition": "treated"},
                    {"_sample_name": "ip2", "condition": "treated"},
                    {"_sample_name": "ctrl", "condition": "input"},
                ],
                "column_mapping": {"primary_group": "condition"},
                "contrasts": [],
            },
        )
        errs = validate_chipseq_metadata(body, self.submission)
        self.assertEqual(errs, [])

    def test_missing_control(self):
        body = self._base_body(
            assay_type="chip_seq",
            metadata_payload={
                "samples": [
                    {"_sample_name": "ip1", "condition": "treated"},
                    {"_sample_name": "ip2", "condition": "treated"},
                ],
                "column_mapping": {"primary_group": "condition"},
                "contrasts": [],
            },
        )
        errs = validate_chipseq_metadata(body, self.submission)
        self.assertTrue(any("input" in e.lower() for e in errs))

    def test_all_input_no_treatment(self):
        body = self._base_body(
            assay_type="chip_seq",
            metadata_payload={
                "samples": [
                    {"_sample_name": "c1", "condition": "input"},
                    {"_sample_name": "c2", "condition": "Input"},
                ],
                "column_mapping": {"primary_group": "condition"},
                "contrasts": [],
            },
        )
        errs = validate_chipseq_metadata(body, self.submission)
        self.assertTrue(any("treatment" in e.lower() for e in errs))

    def test_skipped_for_standard_rna(self):
        errs = validate_chipseq_metadata(self._base_body(), self.submission)
        self.assertEqual(errs, [])


# ── Batch Column Validation ──


class BatchColumnTest(_ValidatorTestBase):

    def test_no_batch_column_is_fine(self):
        errs = validate_batch_column(self._base_body(), self.submission)
        self.assertEqual(errs, [])

    def test_batch_column_exists(self):
        body = self._base_body(
            metadata_payload={
                "samples": [
                    {"_sample_name": "s1", "condition": "A", "batch": "1"},
                    {"_sample_name": "s2", "condition": "B", "batch": "1"},
                ],
                "column_mapping": {"primary_group": "condition", "batch_effect": "batch"},
                "contrasts": [],
            },
        )
        errs = validate_batch_column(body, self.submission)
        self.assertEqual(errs, [])

    def test_batch_column_missing(self):
        body = self._base_body(
            metadata_payload={
                "samples": [
                    {"_sample_name": "s1", "condition": "A"},
                    {"_sample_name": "s2", "condition": "B"},
                ],
                "column_mapping": {"primary_group": "condition", "batch_effect": "batch"},
                "contrasts": [],
            },
        )
        errs = validate_batch_column(body, self.submission)
        self.assertTrue(any("not found" in e.lower() for e in errs))

    def test_singleton_batches_detected(self):
        body = self._base_body(
            metadata_payload={
                "samples": [
                    {"_sample_name": "s1", "condition": "A", "batch": "1"},
                    {"_sample_name": "s2", "condition": "B", "batch": "1"},
                    {"_sample_name": "s3", "condition": "A", "batch": "2"},
                    {"_sample_name": "s4", "condition": "B", "batch": "3"},
                ],
                "column_mapping": {"primary_group": "condition", "batch_effect": "batch"},
                "contrasts": [],
            },
        )
        errs = validate_batch_column(body, self.submission)
        self.assertTrue(any("ComBat" in e for e in errs))
        self.assertTrue(any("2" in e and "3" in e for e in errs))

    def test_all_batches_paired_pass(self):
        body = self._base_body(
            metadata_payload={
                "samples": [
                    {"_sample_name": "s1", "condition": "A", "batch": "1"},
                    {"_sample_name": "s2", "condition": "B", "batch": "1"},
                    {"_sample_name": "s3", "condition": "A", "batch": "2"},
                    {"_sample_name": "s4", "condition": "B", "batch": "2"},
                ],
                "column_mapping": {"primary_group": "condition", "batch_effect": "batch"},
                "contrasts": [],
            },
        )
        errs = validate_batch_column(body, self.submission)
        self.assertEqual(errs, [])


# ── Matrix Content Validation ──


class MatrixContentTest(_ValidatorTestBase):

    def _write_matrix(self, content):
        """Write matrix content to a temp file and register as FileAsset."""
        fd, path = tempfile.mkstemp(suffix=".csv")
        with os.fdopen(fd, "w") as f:
            f.write(content)
        FileAsset.objects.create(
            session=self.session,
            submission=self.submission,
            file_role=FileAsset.FileRole.USER_COUNT_MATRIX,
            local_path=path,
        )
        return path

    def test_valid_matrix(self):
        path = self._write_matrix("gene,s1,s2\nGENE1,10,20\nGENE2,0,5\n")
        try:
            errs = validate_matrix_content(
                self._base_body(input_data_type="matrix"), self.submission
            )
            self.assertEqual(errs, [])
        finally:
            os.unlink(path)

    def test_non_numeric_rejected(self):
        path = self._write_matrix("gene,s1,s2\nGENE1,abc,20\n")
        try:
            errs = validate_matrix_content(
                self._base_body(input_data_type="matrix"), self.submission
            )
            self.assertTrue(any("non-numeric" in e.lower() for e in errs))
        finally:
            os.unlink(path)

    def test_negative_rejected(self):
        path = self._write_matrix("gene,s1,s2\nGENE1,-5,20\n")
        try:
            errs = validate_matrix_content(
                self._base_body(input_data_type="matrix"), self.submission
            )
            self.assertTrue(any("negative" in e.lower() for e in errs))
        finally:
            os.unlink(path)

    def test_empty_matrix_rejected(self):
        path = self._write_matrix("gene,s1,s2\n")
        try:
            errs = validate_matrix_content(
                self._base_body(input_data_type="matrix"), self.submission
            )
            self.assertTrue(any("empty" in e.lower() for e in errs))
        finally:
            os.unlink(path)

    def test_single_column_rejected(self):
        path = self._write_matrix("gene\nGENE1\n")
        try:
            errs = validate_matrix_content(
                self._base_body(input_data_type="matrix"), self.submission
            )
            self.assertTrue(any("2 columns" in e for e in errs))
        finally:
            os.unlink(path)

    def test_skipped_for_fastq(self):
        errs = validate_matrix_content(self._base_body(), self.submission)
        self.assertEqual(errs, [])


# ── Metadata Validation ──


class MetadataValidationTest(_ValidatorTestBase):

    def test_empty_samples_rejected(self):
        body = self._base_body(
            metadata_payload={
                "samples": [],
                "column_mapping": {"primary_group": "condition"},
                "contrasts": [],
            },
        )
        errs = validate_metadata(body, self.submission)
        self.assertTrue(any("sample" in e.lower() for e in errs))

    def test_missing_primary_group(self):
        body = self._base_body(
            metadata_payload={
                "samples": [{"_sample_name": "s1", "condition": "A"}],
                "column_mapping": {"primary_group": ""},
                "contrasts": [],
            },
        )
        errs = validate_metadata(body, self.submission)
        self.assertTrue(any("primary group" in e.lower() for e in errs))

    def test_same_contrast_levels(self):
        body = self._base_body(
            metadata_payload={
                "samples": [{"_sample_name": "s1", "condition": "A"}],
                "column_mapping": {"primary_group": "condition"},
                "contrasts": [["A", "A"]],
            },
        )
        errs = validate_metadata(body, self.submission)
        self.assertTrue(any("different" in e.lower() for e in errs))


# ── Full Orchestrator ──


class PipelineSubmissionOrchestratorTest(_ValidatorTestBase):

    def test_valid_fastq_submission(self):
        FileAsset.objects.create(
            session=self.session,
            submission=self.submission,
            file_role=FileAsset.FileRole.RAW_FASTQ,
            local_path="/tmp/s1.fq.gz",
        )
        FileAsset.objects.create(
            session=self.session,
            submission=self.submission,
            file_role=FileAsset.FileRole.RAW_FASTQ,
            local_path="/tmp/s2.fq.gz",
        )
        errors, warnings = validate_pipeline_submission(
            self._base_body(), self.submission
        )
        self.assertEqual(errors, [])

    def test_chipseq_without_controls_fails(self):
        FileAsset.objects.create(
            session=self.session,
            submission=self.submission,
            file_role=FileAsset.FileRole.RAW_FASTQ,
            local_path="/tmp/ip1.fq.gz",
        )
        body = self._base_body(
            assay_type="chip_seq",
            metadata_payload={
                "samples": [
                    {"_sample_name": "ip1", "condition": "treated"},
                    {"_sample_name": "ip2", "condition": "treated"},
                ],
                "column_mapping": {"primary_group": "condition"},
                "contrasts": [],
            },
        )
        errors, _ = validate_pipeline_submission(body, self.submission)
        self.assertTrue(any("input" in e.lower() for e in errors))

    def test_custom_genome_generates_warning(self):
        import tempfile, os
        # Create a real FASTA file for the header check
        fasta_fd, fasta_path = tempfile.mkstemp(suffix=".fa")
        os.write(fasta_fd, b">chr1\nATCG\n")
        os.close(fasta_fd)
        self.addCleanup(os.unlink, fasta_path)

        FileAsset.objects.create(
            session=self.session,
            submission=self.submission,
            file_role=FileAsset.FileRole.RAW_FASTQ,
            local_path="/tmp/s1.fq.gz",
        )
        FileAsset.objects.create(
            session=self.session,
            submission=self.submission,
            file_role=FileAsset.FileRole.RAW_FASTQ,
            local_path="/tmp/s2.fq.gz",
        )
        FileAsset.objects.create(
            session=self.session,
            submission=self.submission,
            file_role=FileAsset.FileRole.CUSTOM_GENOME_FASTA,
            local_path=fasta_path,
        )
        FileAsset.objects.create(
            session=self.session,
            submission=self.submission,
            file_role=FileAsset.FileRole.CUSTOM_GENOME_ANNOTATION,
            local_path="/tmp/genome.gtf",
        )
        body = self._base_body(
            reference_genome="custom",
            custom_genome_name="MyBug",
        )
        errors, warnings = validate_pipeline_submission(body, self.submission)
        self.assertEqual(errors, [])
        self.assertTrue(any("index build" in w.lower() for w in warnings))

    def test_batch_singleton_detected(self):
        FileAsset.objects.create(
            session=self.session,
            submission=self.submission,
            file_role=FileAsset.FileRole.RAW_FASTQ,
            local_path="/tmp/s1.fq.gz",
        )
        body = self._base_body(
            metadata_payload={
                "samples": [
                    {"_sample_name": "s1", "condition": "A", "batch": "1"},
                    {"_sample_name": "s2", "condition": "B", "batch": "2"},
                ],
                "column_mapping": {
                    "primary_group": "condition",
                    "batch_effect": "batch",
                },
                "contrasts": [],
            },
        )
        errors, _ = validate_pipeline_submission(body, self.submission)
        self.assertTrue(any("ComBat" in e for e in errors))


# ── CorePipelineView Integration ──


class CorePipelineViewValidationIntegrationTest(_ValidatorTestBase):

    def _post(self, body):
        from pipeline.views import CorePipelineView

        req = RequestFactory().post(
            "/api/pipeline/core",
            data=json.dumps(body),
            content_type="application/json",
        )
        req.session_obj = self.session
        return CorePipelineView.as_view()(req)

    def test_400_returns_errors_array(self):
        body = self._base_body(input_data_type="invalid")
        resp = self._post(body)
        self.assertEqual(resp.status_code, 400)
        data = json.loads(resp.content)
        self.assertIn("error", data)
        self.assertIn("errors", data)
        self.assertIsInstance(data["errors"], list)

    def test_400_first_error_matches(self):
        body = self._base_body(input_data_type="invalid")
        resp = self._post(body)
        data = json.loads(resp.content)
        self.assertEqual(data["error"], data["errors"][0])

    @patch("pipeline.views.api.run_core_pipeline")
    def test_200_returns_warnings_for_custom_genome(self, mock_task):
        import tempfile, os
        mock_task.apply_async.return_value = MagicMock()
        # Create a real FASTA file for the header check
        fasta_fd, fasta_path = tempfile.mkstemp(suffix=".fa")
        os.write(fasta_fd, b">chr1\nATCG\n")
        os.close(fasta_fd)
        self.addCleanup(os.unlink, fasta_path)

        FileAsset.objects.create(
            session=self.session,
            submission=self.submission,
            file_role=FileAsset.FileRole.RAW_FASTQ,
            local_path="/tmp/s1.fq.gz",
        )
        FileAsset.objects.create(
            session=self.session,
            submission=self.submission,
            file_role=FileAsset.FileRole.RAW_FASTQ,
            local_path="/tmp/s2.fq.gz",
        )
        FileAsset.objects.create(
            session=self.session,
            submission=self.submission,
            file_role=FileAsset.FileRole.CUSTOM_GENOME_FASTA,
            local_path=fasta_path,
        )
        FileAsset.objects.create(
            session=self.session,
            submission=self.submission,
            file_role=FileAsset.FileRole.CUSTOM_GENOME_ANNOTATION,
            local_path="/tmp/g.gtf",
        )
        body = self._base_body(
            reference_genome="custom",
            custom_genome_name="TestOrg",
        )
        resp = self._post(body)
        self.assertEqual(resp.status_code, 200)
        data = json.loads(resp.content)
        self.assertIn("warnings", data)
        self.assertTrue(len(data["warnings"]) > 0)

    @patch("pipeline.views.api.run_core_pipeline")
    def test_chipseq_missing_control_returns_400(self, mock_task):
        FileAsset.objects.create(
            session=self.session,
            submission=self.submission,
            file_role=FileAsset.FileRole.RAW_FASTQ,
            local_path="/tmp/ip1.fq.gz",
        )
        body = self._base_body(
            assay_type="chip_seq",
            metadata_payload={
                "samples": [
                    {"_sample_name": "ip1", "condition": "treated"},
                ],
                "column_mapping": {"primary_group": "condition"},
                "contrasts": [],
            },
        )
        resp = self._post(body)
        self.assertEqual(resp.status_code, 400)
        data = json.loads(resp.content)
        self.assertTrue(any("input" in e.lower() for e in data["errors"]))

    def test_backward_compat_error_field(self):
        """Existing tests expect data['error'] as a string."""
        body = self._base_body(assay_type="invalid_assay")
        resp = self._post(body)
        self.assertEqual(resp.status_code, 400)
        data = json.loads(resp.content)
        self.assertIn("assay_type", data["error"])
