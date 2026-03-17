"""
Tests for Phase 4 Hub Engine: Multi-Assay Pipeline Tracks.

Covers:
- Model: AssayType choices, assay_type field, new FileRole values
- Views: CorePipelineView assay_type validation and pipeline step selection
- Views: ProcessingView new step definitions
- Tasks: Router dispatch by assay_type (small_rna, chip_seq, methylation)
- Tasks: Shared helpers (_run_fastqc_step, _run_trim_step, _run_multiqc_step)
- Tasks: Track-specific resolvers & tool wrappers (mocked CLI)
- Tasks: _split_chip_samples metadata parsing
"""
import json
import os
import tempfile
from unittest.mock import MagicMock, call, patch

from django.test import RequestFactory, TestCase

from pipeline.models import AnalysisJob, AnalysisSubmission, FileAsset, Session


# ── Model Tests ──────────────────────────────────────────


class AssayTypeModelTest(TestCase):
    """AssayType field on AnalysisSubmission."""

    def setUp(self):
        self.session = Session.objects.create()

    def test_default_is_standard_rna(self):
        sub = AnalysisSubmission.objects.create(session=self.session)
        self.assertEqual(sub.assay_type, "standard_rna")

    def test_choices_list(self):
        choices = dict(AnalysisSubmission.AssayType.choices)
        self.assertIn("standard_rna", choices)
        self.assertIn("small_rna", choices)
        self.assertIn("chip_seq", choices)
        self.assertIn("methylation", choices)

    def test_can_set_small_rna(self):
        sub = AnalysisSubmission.objects.create(
            session=self.session, assay_type="small_rna"
        )
        sub.refresh_from_db()
        self.assertEqual(sub.assay_type, "small_rna")

    def test_can_set_chip_seq(self):
        sub = AnalysisSubmission.objects.create(
            session=self.session, assay_type="chip_seq"
        )
        sub.refresh_from_db()
        self.assertEqual(sub.assay_type, "chip_seq")

    def test_can_set_methylation(self):
        sub = AnalysisSubmission.objects.create(
            session=self.session, assay_type="methylation"
        )
        sub.refresh_from_db()
        self.assertEqual(sub.assay_type, "methylation")


class TrackFileRoleTest(TestCase):
    """New FileRole entries for Track C outputs."""

    def setUp(self):
        self.session = Session.objects.create()
        self.sub = AnalysisSubmission.objects.create(session=self.session)

    def test_peak_file_role(self):
        fa = FileAsset.objects.create(
            session=self.session,
            submission=self.sub,
            file_role=FileAsset.FileRole.PEAK_FILE,
            local_path="/tmp/peaks.narrowPeak",
        )
        fa.refresh_from_db()
        self.assertEqual(fa.file_role, "PEAK_FILE")

    def test_methylation_report_role(self):
        fa = FileAsset.objects.create(
            session=self.session,
            submission=self.sub,
            file_role=FileAsset.FileRole.METHYLATION_REPORT,
            local_path="/tmp/methyl.cov",
        )
        fa.refresh_from_db()
        self.assertEqual(fa.file_role, "METHYLATION_REPORT")


# ── View Validation Tests ─────────────────────────────────


class CorePipelineViewAssayTypeTest(TestCase):
    """CorePipelineView: assay_type validation and pipeline step selection."""

    def setUp(self):
        self.factory = RequestFactory()
        self.session = Session.objects.create()
        self.submission = AnalysisSubmission.objects.create(session=self.session)
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

    def _base_body(self, **overrides):
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
        body.update(overrides)
        return body

    def test_invalid_assay_type_rejected(self):
        body = self._base_body(assay_type="invalid_assay")
        resp = self._post(body)
        self.assertEqual(resp.status_code, 400)
        self.assertIn("assay_type", json.loads(resp.content)["error"])

    def test_assay_type_defaults_to_standard_rna(self):
        """When assay_type is omitted, default is standard_rna."""
        FileAsset.objects.create(
            session=self.session,
            submission=self.submission,
            file_role=FileAsset.FileRole.RAW_FASTQ,
            local_path="/tmp/s1.fq.gz",
        )
        with patch("pipeline.views.api.run_core_pipeline") as mock_task:
            mock_task.apply_async.return_value = MagicMock()
            resp = self._post(self._base_body())

        self.assertEqual(resp.status_code, 200)
        self.submission.refresh_from_db()
        self.assertEqual(self.submission.assay_type, "standard_rna")

    @patch("pipeline.views.api.run_core_pipeline")
    def test_small_rna_pipeline_steps(self, mock_task):
        mock_task.apply_async.return_value = MagicMock()
        FileAsset.objects.create(
            session=self.session,
            submission=self.submission,
            file_role=FileAsset.FileRole.RAW_FASTQ,
            local_path="/tmp/s1.fq.gz",
        )
        resp = self._post(self._base_body(assay_type="small_rna"))
        self.assertEqual(resp.status_code, 200)
        job_id = json.loads(resp.content)["job_id"]
        job = AnalysisJob.objects.get(job_id=job_id)
        steps = job.step_progress["pipeline_steps"]
        self.assertIn("bowtie_mirna", steps)
        self.assertIn("mirna_quantify", steps)
        self.assertIn("deseq2", steps)
        self.assertNotIn("hisat2", steps)
        self.assertNotIn("featurecounts", steps)

    @patch("pipeline.views.api.run_core_pipeline")
    def test_chip_seq_pipeline_steps(self, mock_task):
        mock_task.apply_async.return_value = MagicMock()
        FileAsset.objects.create(
            session=self.session,
            submission=self.submission,
            file_role=FileAsset.FileRole.RAW_FASTQ,
            local_path="/tmp/s1.fq.gz",
        )
        resp = self._post(self._base_body(assay_type="chip_seq"))
        self.assertEqual(resp.status_code, 200)
        job_id = json.loads(resp.content)["job_id"]
        job = AnalysisJob.objects.get(job_id=job_id)
        steps = job.step_progress["pipeline_steps"]
        self.assertIn("bwa_align", steps)
        self.assertIn("macs2_peaks", steps)
        self.assertNotIn("hisat2", steps)
        self.assertNotIn("deseq2", steps)

    @patch("pipeline.views.api.run_core_pipeline")
    def test_methylation_pipeline_steps(self, mock_task):
        mock_task.apply_async.return_value = MagicMock()
        FileAsset.objects.create(
            session=self.session,
            submission=self.submission,
            file_role=FileAsset.FileRole.RAW_FASTQ,
            local_path="/tmp/s1.fq.gz",
        )
        resp = self._post(self._base_body(assay_type="methylation"))
        self.assertEqual(resp.status_code, 200)
        job_id = json.loads(resp.content)["job_id"]
        job = AnalysisJob.objects.get(job_id=job_id)
        steps = job.step_progress["pipeline_steps"]
        self.assertIn("bismark_prep", steps)
        self.assertIn("bismark_align", steps)
        self.assertIn("bismark_extract", steps)
        self.assertNotIn("hisat2", steps)
        self.assertNotIn("deseq2", steps)

    @patch("pipeline.views.api.run_core_pipeline")
    def test_assay_type_persisted(self, mock_task):
        mock_task.apply_async.return_value = MagicMock()
        FileAsset.objects.create(
            session=self.session,
            submission=self.submission,
            file_role=FileAsset.FileRole.RAW_FASTQ,
            local_path="/tmp/s1.fq.gz",
        )
        resp = self._post(self._base_body(assay_type="chip_seq"))
        self.assertEqual(resp.status_code, 200)
        self.submission.refresh_from_db()
        self.assertEqual(self.submission.assay_type, "chip_seq")

    @patch("pipeline.views.api.run_core_pipeline")
    def test_non_fastq_ignores_assay_type(self, mock_task):
        """For alignment entry, assay_type defaults to standard_rna."""
        mock_task.apply_async.return_value = MagicMock()
        FileAsset.objects.create(
            session=self.session,
            submission=self.submission,
            file_role=FileAsset.FileRole.ALIGNMENT_BAM,
            local_path="/tmp/s1.bam",
        )
        body = self._base_body(
            input_data_type="alignment",
            assay_type="chip_seq",  # should be ignored
        )
        resp = self._post(body)
        self.assertEqual(resp.status_code, 200)
        self.submission.refresh_from_db()
        self.assertEqual(self.submission.assay_type, "standard_rna")


class ProcessingViewStepsTest(TestCase):
    """ProcessingView has step definitions for all tracks."""

    def test_standard_rna_steps_exist(self):
        from pipeline.views import ProcessingView

        keys = {s["key"] for s in ProcessingView.PIPELINE_STEPS}
        for expected in ("hisat2", "featurecounts", "multiqc", "deseq2"):
            self.assertIn(expected, keys)

    def test_small_rna_steps_exist(self):
        from pipeline.views import ProcessingView

        keys = {s["key"] for s in ProcessingView.PIPELINE_STEPS}
        self.assertIn("bowtie_mirna", keys)
        self.assertIn("mirna_quantify", keys)

    def test_chip_seq_steps_exist(self):
        from pipeline.views import ProcessingView

        keys = {s["key"] for s in ProcessingView.PIPELINE_STEPS}
        self.assertIn("bwa_align", keys)
        self.assertIn("macs2_peaks", keys)

    def test_methylation_steps_exist(self):
        from pipeline.views import ProcessingView

        keys = {s["key"] for s in ProcessingView.PIPELINE_STEPS}
        self.assertIn("bismark_prep", keys)
        self.assertIn("bismark_align", keys)
        self.assertIn("bismark_extract", keys)


# ── Task Router Dispatch Tests ────────────────────────────


class TaskRouterAssayDispatchTest(TestCase):
    """run_core_pipeline dispatches to correct route based on assay_type."""

    def setUp(self):
        self.session = Session.objects.create()

    def _make_sub(self, assay_type="standard_rna", input_data_type="fastq"):
        return AnalysisSubmission.objects.create(
            session=self.session,
            input_data_type=input_data_type,
            assay_type=assay_type,
        )

    def _make_job(self):
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

    @patch("pipeline.tasks._route_fastq")
    @patch("pipeline.tasks._route_small_rna")
    @patch("pipeline.tasks._route_chip_seq")
    @patch("pipeline.tasks._route_methylation")
    def test_standard_rna_dispatches_to_route_fastq(
        self, mock_meth, mock_chip, mock_srna, mock_fastq
    ):
        mock_fastq.return_value = {"count_matrix": "/tmp/c.csv"}
        sub = self._make_sub("standard_rna")
        job = self._make_job()

        from pipeline.tasks import run_core_pipeline

        run_core_pipeline.apply(
            args=[str(self.session.session_id), str(sub.submission_id)],
            task_id=str(job.job_id),
        )

        mock_fastq.assert_called_once()
        mock_srna.assert_not_called()
        mock_chip.assert_not_called()
        mock_meth.assert_not_called()

    @patch("pipeline.tasks._route_fastq")
    @patch("pipeline.tasks._route_small_rna")
    @patch("pipeline.tasks._route_chip_seq")
    @patch("pipeline.tasks._route_methylation")
    def test_small_rna_dispatches_to_route_small_rna(
        self, mock_meth, mock_chip, mock_srna, mock_fastq
    ):
        mock_srna.return_value = {"count_matrix": "/tmp/c.csv"}
        sub = self._make_sub("small_rna")
        job = self._make_job()

        from pipeline.tasks import run_core_pipeline

        run_core_pipeline.apply(
            args=[str(self.session.session_id), str(sub.submission_id)],
            task_id=str(job.job_id),
        )

        mock_srna.assert_called_once()
        mock_fastq.assert_not_called()
        mock_chip.assert_not_called()
        mock_meth.assert_not_called()

    @patch("pipeline.tasks._route_fastq")
    @patch("pipeline.tasks._route_small_rna")
    @patch("pipeline.tasks._route_chip_seq")
    @patch("pipeline.tasks._route_methylation")
    def test_chip_seq_dispatches_to_route_chip_seq(
        self, mock_meth, mock_chip, mock_srna, mock_fastq
    ):
        mock_chip.return_value = {"peaks_dir": "/tmp/peaks"}
        sub = self._make_sub("chip_seq")
        job = self._make_job()

        from pipeline.tasks import run_core_pipeline

        run_core_pipeline.apply(
            args=[str(self.session.session_id), str(sub.submission_id)],
            task_id=str(job.job_id),
        )

        mock_chip.assert_called_once()
        mock_fastq.assert_not_called()
        mock_srna.assert_not_called()
        mock_meth.assert_not_called()

    @patch("pipeline.tasks._route_fastq")
    @patch("pipeline.tasks._route_small_rna")
    @patch("pipeline.tasks._route_chip_seq")
    @patch("pipeline.tasks._route_methylation")
    def test_methylation_dispatches_to_route_methylation(
        self, mock_meth, mock_chip, mock_srna, mock_fastq
    ):
        mock_meth.return_value = {"methyl_dir": "/tmp/methyl"}
        sub = self._make_sub("methylation")
        job = self._make_job()

        from pipeline.tasks import run_core_pipeline

        run_core_pipeline.apply(
            args=[str(self.session.session_id), str(sub.submission_id)],
            task_id=str(job.job_id),
        )

        mock_meth.assert_called_once()
        mock_fastq.assert_not_called()
        mock_srna.assert_not_called()
        mock_chip.assert_not_called()


# ── Track B: Small RNA Route Tests ────────────────────────


class SmallRNARouteTest(TestCase):
    """_route_small_rna: Bowtie + miRBase pipeline."""

    def setUp(self):
        self.session = Session.objects.create()
        self.sub = AnalysisSubmission.objects.create(
            session=self.session,
            input_data_type="fastq",
            assay_type="small_rna",
            library_type="single",
            reference_genome="hg38",
        )
        os.makedirs(self.sub.upload_dir, exist_ok=True)
        # Create a dummy FASTQ
        fq_dir = os.path.join(self.sub.upload_dir, "raw")
        os.makedirs(fq_dir, exist_ok=True)
        self.fq_path = os.path.join(fq_dir, "s1.fq.gz")
        with open(self.fq_path, "w") as f:
            f.write("@read1\nACGT\n+\nIIII\n")
        FileAsset.objects.create(
            session=self.session,
            submission=self.sub,
            file_role=FileAsset.FileRole.RAW_FASTQ,
            local_path=self.fq_path,
        )

    def _make_job(self):
        return AnalysisJob.objects.create(
            session=self.session,
            module_name="CORE_PIPELINE",
            step_progress={
                "pipeline_steps": ["fastqc", "trimmomatic", "bowtie_mirna",
                                   "mirna_quantify", "multiqc", "deseq2"],
                "completed_steps": [],
                "current_step": None,
                "failed_step": None,
            },
        )

    @patch("pipeline.tasks._track_mirna._run_multiqc_step")
    @patch("pipeline.tasks._track_mirna._mirna_counts_from_bams", return_value="/tmp/counts.csv")
    @patch("pipeline.tasks._track_mirna._run_bowtie_mirna", return_value=["/tmp/s1.bam"])
    @patch("pipeline.tasks._track_mirna._run_trim_step", return_value=[("/tmp/s1_trimmed.fq.gz", "s1")])
    @patch("pipeline.tasks._track_mirna._run_fastqc_step")
    @patch("pipeline.tasks._track_mirna._resolve_mirbase", return_value="/idx/mirbase")
    @patch("pipeline.stats.run_stage2_stats", return_value={"deseq2_results": "/tmp/r"})
    def test_route_calls_correct_tools(
        self, mock_stats, mock_mirbase, mock_fastqc, mock_trim,
        mock_bowtie, mock_counts, mock_multiqc,
    ):
        from pipeline.tasks import _route_small_rna

        job = self._make_job()
        result = _route_small_rna(self.sub, job)

        mock_fastqc.assert_called_once()
        mock_trim.assert_called_once()
        mock_bowtie.assert_called_once()
        mock_counts.assert_called_once()
        mock_multiqc.assert_called_once()
        mock_stats.assert_called_once()

        self.assertIn("count_matrix", result)

    @patch("pipeline.tasks._track_mirna._run_multiqc_step")
    @patch("pipeline.tasks._track_mirna._mirna_counts_from_bams", return_value="/tmp/counts.csv")
    @patch("pipeline.tasks._track_mirna._run_bowtie_mirna", return_value=["/tmp/s1.bam"])
    @patch("pipeline.tasks._track_mirna._run_trim_step", return_value=[("/tmp/s1_trimmed.fq.gz", "s1")])
    @patch("pipeline.tasks._track_mirna._run_fastqc_step")
    @patch("pipeline.tasks._track_mirna._resolve_mirbase", return_value="/idx/mirbase")
    @patch("pipeline.stats.run_stage2_stats", return_value={"deseq2_results": "/tmp/r"})
    def test_trim_uses_minlen_18(
        self, mock_stats, mock_mirbase, mock_fastqc, mock_trim,
        mock_bowtie, mock_counts, mock_multiqc,
    ):
        from pipeline.tasks import _route_small_rna

        job = self._make_job()
        _route_small_rna(self.sub, job)

        # _run_trim_step should be called with min_len=18
        _, kwargs = mock_trim.call_args
        self.assertEqual(kwargs.get("min_len"), 18)

    @patch("pipeline.tasks._helpers._run")
    @patch("pipeline.tasks._track_mirna._run")
    def test_bowtie_uses_mirna_flags(self, mock_run, mock_helpers_run):
        """_run_bowtie_mirna generates bowtie commands with miRNA flags."""
        from pipeline.tasks import _run_bowtie_mirna

        mock_run.return_value = MagicMock(stdout="")
        mock_helpers_run.return_value = MagicMock(stdout="")

        with tempfile.TemporaryDirectory() as tmpdir:
            fq = os.path.join(tmpdir, "s1_trimmed.fq.gz")
            with open(fq, "w") as f:
                f.write("")

            # Create dummy SAM so os.remove doesn't fail
            sam = os.path.join(tmpdir, "s1.sam")
            with open(sam, "w") as f:
                f.write("")

            with patch("os.remove"):
                _run_bowtie_mirna(
                    [(fq, "s1")], "/idx/mirbase", tmpdir, "single"
                )

        bowtie_cmds = [
            c.args[0] for c in mock_run.call_args_list
            if "bowtie" in c.args[0] and "build" not in c.args[0]
        ]
        self.assertTrue(len(bowtie_cmds) > 0)
        cmd = bowtie_cmds[0]
        self.assertIn("-v 1", cmd)
        self.assertIn("--best", cmd)
        self.assertIn("--norc", cmd)


# ── Track C: ChIP-seq Route Tests ─────────────────────────


class ChIPSeqRouteTest(TestCase):
    """_route_chip_seq: BWA + MACS2 pipeline."""

    def setUp(self):
        self.session = Session.objects.create()
        self.sub = AnalysisSubmission.objects.create(
            session=self.session,
            input_data_type="fastq",
            assay_type="chip_seq",
            library_type="single",
            reference_genome="hg38",
            metadata_payload={
                "samples": [
                    {"_sample_name": "ip1", "condition": "IP"},
                    {"_sample_name": "input1", "condition": "Input"},
                ],
                "column_mapping": {"primary_group": "condition"},
                "contrasts": [],
            },
        )
        os.makedirs(self.sub.upload_dir, exist_ok=True)
        fq_dir = os.path.join(self.sub.upload_dir, "raw")
        os.makedirs(fq_dir, exist_ok=True)
        for name in ("ip1.fq.gz", "input1.fq.gz"):
            path = os.path.join(fq_dir, name)
            with open(path, "w") as f:
                f.write("@read1\nACGT\n+\nIIII\n")
            FileAsset.objects.create(
                session=self.session,
                submission=self.sub,
                file_role=FileAsset.FileRole.RAW_FASTQ,
                local_path=path,
            )

    def _make_job(self):
        return AnalysisJob.objects.create(
            session=self.session,
            module_name="CORE_PIPELINE",
            step_progress={
                "pipeline_steps": ["fastqc", "trimmomatic", "bwa_align",
                                   "macs2_peaks", "multiqc"],
                "completed_steps": [],
                "current_step": None,
                "failed_step": None,
            },
        )

    @patch("pipeline.tasks._helpers._run")
    @patch("pipeline.tasks._track_chipseq._run")
    @patch("pipeline.tasks._track_chipseq._resolve_genome", return_value=("/idx/ht2", "/ref/genome.fa", "/ref/genes.gtf"))
    @patch("pipeline.tasks._track_chipseq._resolve_bwa_index", return_value="/ref/genome.fa")
    def test_route_calls_bwa_and_macs2(self, mock_bwa_idx, mock_genome, mock_run, mock_helpers_run):
        from pipeline.tasks import _route_chip_seq

        mock_run.return_value = MagicMock(stdout="")
        mock_helpers_run.return_value = MagicMock(stdout="")

        # Create dummy peak file so glob in _run_macs2 finds it
        peaks_dir = os.path.join(self.sub.upload_dir, "peaks")
        os.makedirs(peaks_dir, exist_ok=True)
        peak_file = os.path.join(peaks_dir, "chip_peaks_narrowPeak")
        with open(peak_file, "w") as f:
            f.write("chr1\t100\t200\n")

        job = self._make_job()

        with patch("glob.glob", return_value=[peak_file]):
            result = _route_chip_seq(self.sub, job)

        cmd_strings = [c.args[0] for c in mock_run.call_args_list]
        cmd_strings += [c.args[0] for c in mock_helpers_run.call_args_list]
        has_bwa = any("bwa mem" in c for c in cmd_strings)
        has_macs2 = any("macs2 callpeak" in c for c in cmd_strings)
        has_multiqc = any("multiqc" in c for c in cmd_strings)

        self.assertTrue(has_bwa, "Missing bwa mem call")
        self.assertTrue(has_macs2, "Missing macs2 callpeak call")
        self.assertTrue(has_multiqc, "Missing multiqc call")

        # No DESeq2 for ChIP-seq
        self.assertNotIn("deseq2_results", result)
        self.assertIn("peaks_dir", result)

    @patch("pipeline.tasks._helpers._run")
    @patch("pipeline.tasks._track_chipseq._run")
    @patch("pipeline.tasks._track_chipseq._resolve_genome", return_value=("/idx/ht2", "/ref/genome.fa", "/ref/genes.gtf"))
    @patch("pipeline.tasks._track_chipseq._resolve_bwa_index", return_value="/ref/genome.fa")
    def test_chip_registers_peak_file_assets(self, mock_bwa_idx, mock_genome, mock_run, mock_helpers_run):
        from pipeline.tasks import _route_chip_seq

        mock_run.return_value = MagicMock(stdout="")
        mock_helpers_run.return_value = MagicMock(stdout="")

        peaks_dir = os.path.join(self.sub.upload_dir, "peaks")
        os.makedirs(peaks_dir, exist_ok=True)
        peak_file = os.path.join(peaks_dir, "chip_peaks_narrowPeak")
        with open(peak_file, "w") as f:
            f.write("chr1\t100\t200\n")

        job = self._make_job()
        with patch("glob.glob", return_value=[peak_file]):
            _route_chip_seq(self.sub, job)

        peak_assets = FileAsset.objects.filter(
            submission=self.sub,
            file_role=FileAsset.FileRole.PEAK_FILE,
        )
        self.assertTrue(peak_assets.exists())


class SplitChipSamplesTest(TestCase):
    """_split_chip_samples separates treatment/control by metadata."""

    def setUp(self):
        self.session = Session.objects.create()

    def test_input_samples_become_control(self):
        from pipeline.tasks import _split_chip_samples

        sub = AnalysisSubmission.objects.create(
            session=self.session,
            metadata_payload={
                "samples": [
                    {"_sample_name": "ip1", "condition": "H3K4me3"},
                    {"_sample_name": "input1", "condition": "Input"},
                ],
                "column_mapping": {"primary_group": "condition"},
            },
        )
        bam_files = ["/tmp/ip1_trimmed.bam", "/tmp/input1_trimmed.bam"]
        trimmed_files = [("/tmp/ip1_trimmed.fq.gz", "ip1"), ("/tmp/input1_trimmed.fq.gz", "input1")]

        treatment, control = _split_chip_samples(bam_files, trimmed_files, sub, "single")
        self.assertEqual(len(treatment), 1)
        self.assertEqual(len(control), 1)
        self.assertIn("/tmp/input1_trimmed.bam", control)

    def test_no_treatment_raises(self):
        from pipeline.tasks import _split_chip_samples

        sub = AnalysisSubmission.objects.create(
            session=self.session,
            metadata_payload={
                "samples": [
                    {"_sample_name": "input1", "condition": "Input"},
                ],
                "column_mapping": {"primary_group": "condition"},
            },
        )
        bam_files = ["/tmp/input1_trimmed.bam"]
        trimmed_files = [("/tmp/input1_trimmed.fq.gz", "input1")]

        with self.assertRaises(RuntimeError):
            _split_chip_samples(bam_files, trimmed_files, sub, "single")

    def test_no_control_is_allowed(self):
        from pipeline.tasks import _split_chip_samples

        sub = AnalysisSubmission.objects.create(
            session=self.session,
            metadata_payload={
                "samples": [
                    {"_sample_name": "ip1", "condition": "H3K4me3"},
                ],
                "column_mapping": {"primary_group": "condition"},
            },
        )
        bam_files = ["/tmp/ip1_trimmed.bam"]
        trimmed_files = [("/tmp/ip1_trimmed.fq.gz", "ip1")]

        treatment, control = _split_chip_samples(bam_files, trimmed_files, sub, "single")
        self.assertEqual(len(treatment), 1)
        self.assertEqual(len(control), 0)


# ── Track C: DNA Methylation Route Tests ──────────────────


class MethylationRouteTest(TestCase):
    """_route_methylation: Bismark pipeline."""

    def setUp(self):
        self.session = Session.objects.create()
        self.sub = AnalysisSubmission.objects.create(
            session=self.session,
            input_data_type="fastq",
            assay_type="methylation",
            library_type="single",
            reference_genome="hg38",
        )
        os.makedirs(self.sub.upload_dir, exist_ok=True)
        fq_dir = os.path.join(self.sub.upload_dir, "raw")
        os.makedirs(fq_dir, exist_ok=True)
        self.fq_path = os.path.join(fq_dir, "s1.fq.gz")
        with open(self.fq_path, "w") as f:
            f.write("@read1\nACGT\n+\nIIII\n")
        FileAsset.objects.create(
            session=self.session,
            submission=self.sub,
            file_role=FileAsset.FileRole.RAW_FASTQ,
            local_path=self.fq_path,
        )

    def _make_job(self):
        return AnalysisJob.objects.create(
            session=self.session,
            module_name="CORE_PIPELINE",
            step_progress={
                "pipeline_steps": ["fastqc", "trimmomatic", "bismark_prep",
                                   "bismark_align", "bismark_extract", "multiqc"],
                "completed_steps": [],
                "current_step": None,
                "failed_step": None,
            },
        )

    @patch("pipeline.tasks._helpers._run")
    @patch("pipeline.tasks._track_methyl._run")
    @patch("pipeline.tasks._track_methyl._resolve_genome", return_value=("/idx/ht2", "/ref/genome.fa", "/ref/genes.gtf"))
    @patch("pipeline.tasks._track_methyl._resolve_bismark_genome", return_value="/ref")
    def test_route_calls_bismark_tools(self, mock_bis_genome, mock_genome, mock_run, mock_helpers_run):
        from pipeline.tasks import _route_methylation

        mock_run.return_value = MagicMock(stdout="")
        mock_helpers_run.return_value = MagicMock(stdout="")

        # Create dummy report file
        methyl_dir = os.path.join(self.sub.upload_dir, "methylation")
        os.makedirs(methyl_dir, exist_ok=True)
        report_file = os.path.join(methyl_dir, "s1.CpG_report.txt")
        with open(report_file, "w") as f:
            f.write("chr1\t100\t+\t10\t5\tCG\n")

        job = self._make_job()
        with patch("glob.glob", return_value=[report_file]):
            result = _route_methylation(self.sub, job)

        cmd_strings = [c.args[0] for c in mock_run.call_args_list]
        cmd_strings += [c.args[0] for c in mock_helpers_run.call_args_list]
        has_bismark = any("bismark " in c and "--genome" in c for c in cmd_strings)
        has_extract = any("bismark_methylation_extractor" in c for c in cmd_strings)
        has_multiqc = any("multiqc" in c for c in cmd_strings)

        self.assertTrue(has_bismark, "Missing bismark alignment call")
        self.assertTrue(has_extract, "Missing bismark_methylation_extractor call")
        self.assertTrue(has_multiqc, "Missing multiqc call")

        # No DESeq2 for methylation
        self.assertNotIn("deseq2_results", result)
        self.assertIn("methyl_dir", result)

    @patch("pipeline.tasks._helpers._run")
    @patch("pipeline.tasks._track_methyl._run")
    @patch("pipeline.tasks._track_methyl._resolve_genome", return_value=("/idx/ht2", "/ref/genome.fa", "/ref/genes.gtf"))
    @patch("pipeline.tasks._track_methyl._resolve_bismark_genome", return_value="/ref")
    def test_methylation_registers_report_assets(self, mock_bis, mock_genome, mock_run, mock_helpers_run):
        from pipeline.tasks import _route_methylation

        mock_run.return_value = MagicMock(stdout="")
        mock_helpers_run.return_value = MagicMock(stdout="")

        methyl_dir = os.path.join(self.sub.upload_dir, "methylation")
        os.makedirs(methyl_dir, exist_ok=True)
        report_file = os.path.join(methyl_dir, "s1.CpG_report.txt")
        with open(report_file, "w") as f:
            f.write("chr1\t100\t+\t10\t5\tCG\n")

        job = self._make_job()
        with patch("glob.glob", return_value=[report_file]):
            _route_methylation(self.sub, job)

        report_assets = FileAsset.objects.filter(
            submission=self.sub,
            file_role=FileAsset.FileRole.METHYLATION_REPORT,
        )
        self.assertTrue(report_assets.exists())


# ── Shared Helper Tests ──────────────────────────────────


class SharedHelperTest(TestCase):
    """Shared step helpers are used across all tracks."""

    def setUp(self):
        self.session = Session.objects.create()

    def _make_job(self):
        return AnalysisJob.objects.create(
            session=self.session,
            module_name="CORE_PIPELINE",
            step_progress={
                "pipeline_steps": ["fastqc", "trimmomatic", "multiqc"],
                "completed_steps": [],
                "current_step": None,
                "failed_step": None,
            },
        )

    @patch("pipeline.tasks._helpers._run")
    def test_run_fastqc_step(self, mock_run):
        from pipeline.tasks import _run_fastqc_step

        job = self._make_job()
        _run_fastqc_step(job, ["/tmp/s1.fq.gz"], "/tmp/qc")
        mock_run.assert_called_once()
        self.assertIn("fastqc", mock_run.call_args.args[0])

        job.refresh_from_db()
        self.assertIn("fastqc", job.step_progress.get("completed_steps", []))

    @patch("pipeline.tasks._helpers._run")
    def test_run_trim_step_single(self, mock_run):
        from pipeline.tasks import _run_trim_step

        job = self._make_job()
        result = _run_trim_step(
            job, ["/tmp/s1.fq.gz"], "/tmp/trimmed", "single", min_len=18
        )
        self.assertTrue(len(result) > 0)
        trim_cmd = mock_run.call_args.args[0]
        self.assertIn("MINLEN:18", trim_cmd)
        self.assertIn("trimmomatic SE", trim_cmd)

    @patch("pipeline.tasks._helpers._run")
    def test_run_trim_step_paired(self, mock_run):
        from pipeline.tasks import _run_trim_step

        job = self._make_job()
        result = _run_trim_step(
            job,
            ["/tmp/s1_R1.fq.gz", "/tmp/s1_R2.fq.gz"],
            "/tmp/trimmed",
            "paired",
        )
        self.assertTrue(len(result) > 0)
        trim_cmd = mock_run.call_args.args[0]
        self.assertIn("trimmomatic PE", trim_cmd)
        self.assertIn("MINLEN:36", trim_cmd)

    @patch("pipeline.tasks._helpers._run")
    def test_run_multiqc_step(self, mock_run):
        from pipeline.tasks import _run_multiqc_step

        job = self._make_job()
        _run_multiqc_step(job, "/tmp/work", "/tmp/qc")
        mock_run.assert_called_once()
        self.assertIn("multiqc", mock_run.call_args.args[0])

    @patch("pipeline.tasks._helpers._run")
    def test_sort_and_index_bam(self, mock_run):
        from pipeline.tasks import _sort_and_index_bam

        result = _sort_and_index_bam("/tmp/aligned.sam", "/tmp/sorted.bam")
        self.assertEqual(result, "/tmp/sorted.bam")
        self.assertEqual(mock_run.call_count, 2)  # sort + index


# ── Genome Resolver Tests ─────────────────────────────────


class GenomeResolverTest(TestCase):
    """Track-specific genome resolvers."""

    def test_mirbase_species_map_coverage(self):
        from pipeline.tasks import _MIRBASE_SPECIES_MAP

        self.assertIn("hg38", _MIRBASE_SPECIES_MAP)
        self.assertIn("mm39", _MIRBASE_SPECIES_MAP)
        self.assertIn("dm6", _MIRBASE_SPECIES_MAP)
        self.assertEqual(_MIRBASE_SPECIES_MAP["hg38"], "hsa")

    def test_macs2_genome_size_coverage(self):
        from pipeline.tasks import _MACS2_GENOME_SIZE

        self.assertIn("hg38", _MACS2_GENOME_SIZE)
        self.assertIn("mm39", _MACS2_GENOME_SIZE)
        self.assertEqual(_MACS2_GENOME_SIZE["hg38"], "hs")

    def test_resolve_mirbase_unknown_genome_raises(self):
        from pipeline.tasks import _resolve_mirbase

        with self.assertRaises(ValueError):
            _resolve_mirbase("unknown_genome_xyz")

    @patch("pipeline.tasks._genome._run")
    def test_resolve_bwa_index_builds_if_missing(self, mock_run):
        from pipeline.tasks import _resolve_bwa_index

        with tempfile.TemporaryDirectory() as tmpdir:
            fasta = os.path.join(tmpdir, "genome.fa")
            with open(fasta, "w") as f:
                f.write(">chr1\nACGT\n")

            result = _resolve_bwa_index(fasta)
            self.assertEqual(result, fasta)
            mock_run.assert_called_once()
            self.assertIn("bwa index", mock_run.call_args.args[0])

    @patch("pipeline.tasks._genome._run")
    def test_resolve_bwa_index_skips_if_exists(self, mock_run):
        from pipeline.tasks import _resolve_bwa_index

        with tempfile.TemporaryDirectory() as tmpdir:
            fasta = os.path.join(tmpdir, "genome.fa")
            bwt = fasta + ".bwt"
            with open(fasta, "w") as f:
                f.write(">chr1\nACGT\n")
            with open(bwt, "w") as f:
                f.write("index")

            result = _resolve_bwa_index(fasta)
            self.assertEqual(result, fasta)
            mock_run.assert_not_called()

    @patch("pipeline.tasks._genome._run")
    def test_resolve_bismark_genome_builds_if_missing(self, mock_run):
        from pipeline.tasks import _resolve_bismark_genome

        with tempfile.TemporaryDirectory() as tmpdir:
            result = _resolve_bismark_genome(tmpdir)
            self.assertEqual(result, tmpdir)
            mock_run.assert_called_once()
            self.assertIn("bismark_genome_preparation", mock_run.call_args.args[0])

    @patch("pipeline.tasks._genome._run")
    def test_resolve_bismark_genome_skips_if_exists(self, mock_run):
        from pipeline.tasks import _resolve_bismark_genome

        with tempfile.TemporaryDirectory() as tmpdir:
            os.makedirs(os.path.join(tmpdir, "Bisulfite_Genome"))
            result = _resolve_bismark_genome(tmpdir)
            self.assertEqual(result, tmpdir)
            mock_run.assert_not_called()
