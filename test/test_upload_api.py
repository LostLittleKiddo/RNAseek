"""
Tests for Frontend Upload API Endpoints.

Covers:
- CreateSubmissionView: creates submission and returns UUID
- DeleteSubmissionView: deletes submission, files on disk, and FileAsset rows
- ChunkUploadView: single-chunk and multi-chunk uploads for all file roles
- FASTA extension validation: only .fa, .fasta, .fa.gz, .fasta.gz, .fa.zip, .fasta.zip
- Single-end FASTQ upload flow
- Paired-end FASTQ upload flow
- BAM upload flow
- Matrix (CSV/TSV) upload flow
- CSV metadata upload flow
- Custom genome FASTA + annotation upload flow
- FileAssetDeleteView: individual file deletion
- Out-of-order chunk uploads (concurrent upload simulation)
- Temporary buffer cleanup after merge
"""
import json
import os
import shutil
import tempfile

from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import RequestFactory, TestCase

from pipeline.models import AnalysisSubmission, FileAsset, Session
from pipeline.views import (
    ChunkUploadView,
    CreateSubmissionView,
    DeleteSubmissionView,
    FileAssetDeleteView,
)
from pipeline.views.api import UPLOAD_BUFFER_ROOT


class CreateSubmissionTest(TestCase):
    """Test CreateSubmissionView."""

    def setUp(self):
        self.factory = RequestFactory()
        self.session = Session.objects.create()

    def _post(self):
        req = self.factory.post("/api/submission/create")
        req.session_obj = self.session
        return CreateSubmissionView.as_view()(req)

    def test_creates_submission(self):
        resp = self._post()
        self.assertEqual(resp.status_code, 200)
        data = json.loads(resp.content)
        self.assertIn("submission_id", data)
        self.assertTrue(
            AnalysisSubmission.objects.filter(submission_id=data["submission_id"]).exists()
        )

    def test_creates_upload_directory(self):
        resp = self._post()
        data = json.loads(resp.content)
        sub = AnalysisSubmission.objects.get(submission_id=data["submission_id"])
        self.assertTrue(os.path.isdir(sub.upload_dir))
        # Cleanup
        shutil.rmtree(sub.upload_dir, ignore_errors=True)

    def test_submission_belongs_to_session(self):
        resp = self._post()
        data = json.loads(resp.content)
        sub = AnalysisSubmission.objects.get(submission_id=data["submission_id"])
        self.assertEqual(sub.session_id, self.session.session_id)
        shutil.rmtree(sub.upload_dir, ignore_errors=True)


class DeleteSubmissionTest(TestCase):
    """Test DeleteSubmissionView — page reload cleanup."""

    def setUp(self):
        self.factory = RequestFactory()
        self.session = Session.objects.create()

    def _delete(self, body):
        req = self.factory.post(
            "/api/submission/delete",
            data=json.dumps(body),
            content_type="application/json",
        )
        req.session_obj = self.session
        return DeleteSubmissionView.as_view()(req)

    def test_deletes_submission(self):
        sub = AnalysisSubmission.objects.create(session=self.session)
        os.makedirs(sub.upload_dir, exist_ok=True)
        resp = self._delete({"submission_id": str(sub.submission_id)})
        self.assertEqual(resp.status_code, 200)
        self.assertFalse(
            AnalysisSubmission.objects.filter(submission_id=sub.submission_id).exists()
        )

    def test_deletes_files_on_disk(self):
        sub = AnalysisSubmission.objects.create(session=self.session)
        raw_dir = os.path.join(sub.upload_dir, "raw")
        os.makedirs(raw_dir, exist_ok=True)
        fpath = os.path.join(raw_dir, "sample.fq.gz")
        with open(fpath, "wb") as f:
            f.write(b"fake fastq data")
        FileAsset.objects.create(
            session=self.session,
            submission=sub,
            file_role=FileAsset.FileRole.RAW_FASTQ,
            local_path=fpath,
            is_user_uploaded=True,
        )
        resp = self._delete({"submission_id": str(sub.submission_id)})
        self.assertEqual(resp.status_code, 200)
        self.assertFalse(os.path.isfile(fpath))
        self.assertFalse(os.path.isdir(sub.upload_dir))

    def test_deletes_file_assets(self):
        sub = AnalysisSubmission.objects.create(session=self.session)
        os.makedirs(sub.upload_dir, exist_ok=True)
        FileAsset.objects.create(
            session=self.session,
            submission=sub,
            file_role=FileAsset.FileRole.RAW_FASTQ,
            local_path="/tmp/nonexistent.fq.gz",
            is_user_uploaded=True,
        )
        asset_count_before = FileAsset.objects.filter(submission=sub).count()
        self.assertEqual(asset_count_before, 1)
        resp = self._delete({"submission_id": str(sub.submission_id)})
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(FileAsset.objects.filter(submission=sub).count(), 0)

    def test_missing_submission_id(self):
        resp = self._delete({})
        self.assertEqual(resp.status_code, 400)

    def test_invalid_submission_id(self):
        resp = self._delete({"submission_id": "00000000-0000-0000-0000-000000000000"})
        self.assertEqual(resp.status_code, 404)

    def test_cannot_delete_other_sessions_submission(self):
        other_session = Session.objects.create()
        sub = AnalysisSubmission.objects.create(session=other_session)
        os.makedirs(sub.upload_dir, exist_ok=True)
        resp = self._delete({"submission_id": str(sub.submission_id)})
        self.assertEqual(resp.status_code, 404)
        # Submission should still exist
        self.assertTrue(
            AnalysisSubmission.objects.filter(submission_id=sub.submission_id).exists()
        )
        shutil.rmtree(sub.upload_dir, ignore_errors=True)

    def test_deletes_multiple_files(self):
        sub = AnalysisSubmission.objects.create(session=self.session)
        raw_dir = os.path.join(sub.upload_dir, "raw")
        os.makedirs(raw_dir, exist_ok=True)
        for i in range(3):
            fpath = os.path.join(raw_dir, f"sample_{i}.fq.gz")
            with open(fpath, "wb") as f:
                f.write(b"data")
            FileAsset.objects.create(
                session=self.session,
                submission=sub,
                file_role=FileAsset.FileRole.RAW_FASTQ,
                local_path=fpath,
                is_user_uploaded=True,
            )
        resp = self._delete({"submission_id": str(sub.submission_id)})
        self.assertEqual(resp.status_code, 200)
        self.assertFalse(os.path.isdir(sub.upload_dir))
        self.assertEqual(FileAsset.objects.filter(submission=sub).count(), 0)


class ChunkUploadSingleEndTest(TestCase):
    """Test single-end FASTQ upload via ChunkUploadView."""

    def setUp(self):
        self.factory = RequestFactory()
        self.session = Session.objects.create()
        self.submission = AnalysisSubmission.objects.create(session=self.session)
        os.makedirs(self.submission.upload_dir, exist_ok=True)

    def tearDown(self):
        shutil.rmtree(self.submission.upload_dir, ignore_errors=True)

    def _upload(self, filename, file_role, content=b"fake", chunk_index=0, total_chunks=1):
        uploaded = SimpleUploadedFile(filename, content)
        req = self.factory.post("/api/upload/chunk", {
            "file": uploaded,
            "filename": filename,
            "chunk_index": str(chunk_index),
            "total_chunks": str(total_chunks),
            "submission_id": str(self.submission.submission_id),
            "file_role": file_role,
        })
        req.session_obj = self.session
        return ChunkUploadView.as_view()(req)

    def test_single_fastq_upload(self):
        resp = self._upload("sample1.fq.gz", "RAW_FASTQ")
        self.assertEqual(resp.status_code, 200)
        data = json.loads(resp.content)
        self.assertTrue(data["complete"])
        self.assertIn("asset_id", data)
        expected = os.path.join(self.submission.upload_dir, "raw", "sample1.fq.gz")
        self.assertTrue(os.path.exists(expected))

    def test_multiple_single_end_files(self):
        files = ["ctrl_1.fq.gz", "ctrl_2.fq.gz", "treat_1.fq.gz", "treat_2.fq.gz"]
        for fname in files:
            resp = self._upload(fname, "RAW_FASTQ", content=b"@SEQ\nACGT\n+\nIIII\n")
            self.assertEqual(resp.status_code, 200)
            self.assertTrue(json.loads(resp.content)["complete"])
        assets = FileAsset.objects.filter(
            submission=self.submission, file_role=FileAsset.FileRole.RAW_FASTQ
        )
        self.assertEqual(assets.count(), 4)

    def test_creates_file_asset_on_final_chunk(self):
        # First chunk — no asset yet
        resp1 = self._upload("big.fq.gz", "RAW_FASTQ", content=b"chunk1", chunk_index=0, total_chunks=2)
        data1 = json.loads(resp1.content)
        self.assertFalse(data1["complete"])
        self.assertNotIn("asset_id", data1)

        # Second (final) chunk — asset created
        resp2 = self._upload("big.fq.gz", "RAW_FASTQ", content=b"chunk2", chunk_index=1, total_chunks=2)
        data2 = json.loads(resp2.content)
        self.assertTrue(data2["complete"])
        self.assertIn("asset_id", data2)

    def test_multi_chunk_file_concatenation(self):
        part1 = b"AAAA"
        part2 = b"BBBB"
        self._upload("concat.fq.gz", "RAW_FASTQ", content=part1, chunk_index=0, total_chunks=2)
        self._upload("concat.fq.gz", "RAW_FASTQ", content=part2, chunk_index=1, total_chunks=2)
        fpath = os.path.join(self.submission.upload_dir, "raw", "concat.fq.gz")
        with open(fpath, "rb") as f:
            self.assertEqual(f.read(), part1 + part2)

    def test_out_of_order_chunks_merged_correctly(self):
        """Chunks arriving in reverse order should still produce correct file."""
        parts = [b"AAAA", b"BBBB", b"CCCC"]
        # Send chunk 2, then 0, then 1
        self._upload("ooo.fq.gz", "RAW_FASTQ", content=parts[2], chunk_index=2, total_chunks=3)
        self._upload("ooo.fq.gz", "RAW_FASTQ", content=parts[0], chunk_index=0, total_chunks=3)
        resp = self._upload("ooo.fq.gz", "RAW_FASTQ", content=parts[1], chunk_index=1, total_chunks=3)
        data = json.loads(resp.content)
        self.assertTrue(data["complete"])
        self.assertIn("asset_id", data)
        fpath = os.path.join(self.submission.upload_dir, "raw", "ooo.fq.gz")
        with open(fpath, "rb") as f:
            self.assertEqual(f.read(), b"AAAA" + b"BBBB" + b"CCCC")

    def test_buffer_directory_cleaned_after_merge(self):
        """Temporary buffer dir on local SSD should be removed after merge."""
        safe_name = "cleanup.fq.gz"
        buffer_dir = os.path.join(
            UPLOAD_BUFFER_ROOT,
            f"{self.submission.submission_id}_{safe_name}",
        )
        self._upload(safe_name, "RAW_FASTQ", content=b"data", chunk_index=0, total_chunks=1)
        self.assertFalse(os.path.isdir(buffer_dir))


class ChunkUploadPairedEndTest(TestCase):
    """Test paired-end FASTQ upload via ChunkUploadView."""

    def setUp(self):
        self.factory = RequestFactory()
        self.session = Session.objects.create()
        self.submission = AnalysisSubmission.objects.create(session=self.session)
        os.makedirs(self.submission.upload_dir, exist_ok=True)

    def tearDown(self):
        shutil.rmtree(self.submission.upload_dir, ignore_errors=True)

    def _upload(self, filename, content=b"fake"):
        uploaded = SimpleUploadedFile(filename, content)
        req = self.factory.post("/api/upload/chunk", {
            "file": uploaded,
            "filename": filename,
            "chunk_index": "0",
            "total_chunks": "1",
            "submission_id": str(self.submission.submission_id),
            "file_role": "RAW_FASTQ",
        })
        req.session_obj = self.session
        return ChunkUploadView.as_view()(req)

    def test_paired_end_r1_r2_upload(self):
        resp1 = self._upload("sample_R1.fq.gz")
        resp2 = self._upload("sample_R2.fq.gz")
        self.assertEqual(resp1.status_code, 200)
        self.assertEqual(resp2.status_code, 200)
        assets = FileAsset.objects.filter(
            submission=self.submission, file_role=FileAsset.FileRole.RAW_FASTQ
        )
        self.assertEqual(assets.count(), 2)

    def test_multiple_paired_samples(self):
        pairs = [
            ("ctrl_R1.fq.gz", "ctrl_R2.fq.gz"),
            ("treat_R1.fq.gz", "treat_R2.fq.gz"),
            ("ko_R1.fq.gz", "ko_R2.fq.gz"),
        ]
        for r1, r2 in pairs:
            self._upload(r1)
            self._upload(r2)
        assets = FileAsset.objects.filter(
            submission=self.submission, file_role=FileAsset.FileRole.RAW_FASTQ
        )
        self.assertEqual(assets.count(), 6)

    def test_paired_files_stored_in_raw_dir(self):
        self._upload("sample_R1.fastq.gz")
        self._upload("sample_R2.fastq.gz")
        raw_dir = os.path.join(self.submission.upload_dir, "raw")
        self.assertTrue(os.path.exists(os.path.join(raw_dir, "sample_R1.fastq.gz")))
        self.assertTrue(os.path.exists(os.path.join(raw_dir, "sample_R2.fastq.gz")))


class ChunkUploadBAMTest(TestCase):
    """Test BAM upload via ChunkUploadView."""

    def setUp(self):
        self.factory = RequestFactory()
        self.session = Session.objects.create()
        self.submission = AnalysisSubmission.objects.create(session=self.session)
        os.makedirs(self.submission.upload_dir, exist_ok=True)

    def tearDown(self):
        shutil.rmtree(self.submission.upload_dir, ignore_errors=True)

    def _upload(self, filename, file_role="ALIGNMENT_BAM", content=b"fake bam"):
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

    def test_bam_routes_to_aligned_dir(self):
        resp = self._upload("sample.bam")
        self.assertEqual(resp.status_code, 200)
        expected = os.path.join(self.submission.upload_dir, "aligned", "sample.bam")
        self.assertTrue(os.path.exists(expected))

    def test_multiple_bam_uploads(self):
        for name in ["s1.bam", "s2.bam", "s3.bam"]:
            resp = self._upload(name)
            self.assertEqual(resp.status_code, 200)
        assets = FileAsset.objects.filter(
            submission=self.submission, file_role=FileAsset.FileRole.ALIGNMENT_BAM
        )
        self.assertEqual(assets.count(), 3)

    def test_bam_asset_created(self):
        resp = self._upload("my_sample.bam")
        data = json.loads(resp.content)
        self.assertTrue(data["complete"])
        self.assertIn("asset_id", data)
        asset = FileAsset.objects.get(id=data["asset_id"])
        self.assertEqual(asset.file_role, "ALIGNMENT_BAM")


class ChunkUploadMatrixTest(TestCase):
    """Test count matrix upload via ChunkUploadView."""

    def setUp(self):
        self.factory = RequestFactory()
        self.session = Session.objects.create()
        self.submission = AnalysisSubmission.objects.create(session=self.session)
        os.makedirs(self.submission.upload_dir, exist_ok=True)

    def tearDown(self):
        shutil.rmtree(self.submission.upload_dir, ignore_errors=True)

    def _upload(self, filename, content):
        uploaded = SimpleUploadedFile(filename, content)
        req = self.factory.post("/api/upload/chunk", {
            "file": uploaded,
            "filename": filename,
            "chunk_index": "0",
            "total_chunks": "1",
            "submission_id": str(self.submission.submission_id),
            "file_role": "USER_COUNT_MATRIX",
        })
        req.session_obj = self.session
        return ChunkUploadView.as_view()(req)

    def test_csv_matrix_routes_to_counts(self):
        csv_content = b"gene_id,s1,s2\ngene1,100,200\n"
        resp = self._upload("counts.csv", csv_content)
        self.assertEqual(resp.status_code, 200)
        expected = os.path.join(self.submission.upload_dir, "counts", "counts.csv")
        self.assertTrue(os.path.exists(expected))

    def test_tsv_matrix_routes_to_counts(self):
        tsv_content = b"gene_id\ts1\ts2\ngene1\t100\t200\n"
        resp = self._upload("counts.tsv", tsv_content)
        self.assertEqual(resp.status_code, 200)
        expected = os.path.join(self.submission.upload_dir, "counts", "counts.tsv")
        self.assertTrue(os.path.exists(expected))

    def test_matrix_asset_created(self):
        content = b"gene_id,s1\ngene1,50\n"
        resp = self._upload("my_matrix.csv", content)
        data = json.loads(resp.content)
        self.assertTrue(data["complete"])
        asset = FileAsset.objects.get(id=data["asset_id"])
        self.assertEqual(asset.file_role, "USER_COUNT_MATRIX")


class ChunkUploadCSVMetadataTest(TestCase):
    """Test CSV metadata upload via ChunkUploadView."""

    def setUp(self):
        self.factory = RequestFactory()
        self.session = Session.objects.create()
        self.submission = AnalysisSubmission.objects.create(session=self.session)
        os.makedirs(self.submission.upload_dir, exist_ok=True)

    def tearDown(self):
        shutil.rmtree(self.submission.upload_dir, ignore_errors=True)

    def _upload(self, filename, content):
        uploaded = SimpleUploadedFile(filename, content)
        req = self.factory.post("/api/upload/chunk", {
            "file": uploaded,
            "filename": filename,
            "chunk_index": "0",
            "total_chunks": "1",
            "submission_id": str(self.submission.submission_id),
            "file_role": "METADATA_CSV",
        })
        req.session_obj = self.session
        return ChunkUploadView.as_view()(req)

    def test_metadata_csv_routes_to_metadata_dir(self):
        csv = b"sample,condition\ns1,A\ns2,B\n"
        resp = self._upload("metadata.csv", csv)
        self.assertEqual(resp.status_code, 200)
        expected = os.path.join(self.submission.upload_dir, "metadata", "metadata.csv")
        self.assertTrue(os.path.exists(expected))

    def test_metadata_asset_created(self):
        csv = b"sample,condition,batch\ns1,A,1\ns2,B,2\n"
        resp = self._upload("metadata.csv", csv)
        data = json.loads(resp.content)
        asset = FileAsset.objects.get(id=data["asset_id"])
        self.assertEqual(asset.file_role, "METADATA_CSV")


class ChunkUploadCustomGenomeTest(TestCase):
    """Test custom genome FASTA and annotation upload via ChunkUploadView."""

    def setUp(self):
        self.factory = RequestFactory()
        self.session = Session.objects.create()
        self.submission = AnalysisSubmission.objects.create(session=self.session)
        os.makedirs(self.submission.upload_dir, exist_ok=True)

    def tearDown(self):
        shutil.rmtree(self.submission.upload_dir, ignore_errors=True)

    def _upload(self, filename, file_role, content=b"fake genome data"):
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

    # ── Valid FASTA extensions ──

    def test_fasta_fa_accepted(self):
        resp = self._upload("genome.fa", "CUSTOM_GENOME_FASTA")
        self.assertEqual(resp.status_code, 200)

    def test_fasta_fasta_accepted(self):
        resp = self._upload("genome.fasta", "CUSTOM_GENOME_FASTA")
        self.assertEqual(resp.status_code, 200)

    def test_fasta_fa_gz_accepted(self):
        resp = self._upload("genome.fa.gz", "CUSTOM_GENOME_FASTA")
        self.assertEqual(resp.status_code, 200)

    def test_fasta_fasta_gz_accepted(self):
        resp = self._upload("genome.fasta.gz", "CUSTOM_GENOME_FASTA")
        self.assertEqual(resp.status_code, 200)

    def test_fasta_fa_zip_accepted(self):
        resp = self._upload("genome.fa.zip", "CUSTOM_GENOME_FASTA")
        self.assertEqual(resp.status_code, 200)

    def test_fasta_fasta_zip_accepted(self):
        resp = self._upload("genome.fasta.zip", "CUSTOM_GENOME_FASTA")
        self.assertEqual(resp.status_code, 200)

    # ── Invalid FASTA extensions ──

    def test_fasta_fna_rejected(self):
        resp = self._upload("genome.fna", "CUSTOM_GENOME_FASTA")
        self.assertEqual(resp.status_code, 400)
        self.assertIn("error", json.loads(resp.content))

    def test_fasta_fna_gz_rejected(self):
        resp = self._upload("genome.fna.gz", "CUSTOM_GENOME_FASTA")
        self.assertEqual(resp.status_code, 400)

    def test_fasta_txt_rejected(self):
        resp = self._upload("genome.txt", "CUSTOM_GENOME_FASTA")
        self.assertEqual(resp.status_code, 400)

    def test_fasta_fastq_rejected(self):
        resp = self._upload("reads.fastq", "CUSTOM_GENOME_FASTA")
        self.assertEqual(resp.status_code, 400)

    def test_fasta_gz_only_rejected(self):
        resp = self._upload("genome.gz", "CUSTOM_GENOME_FASTA")
        self.assertEqual(resp.status_code, 400)

    def test_fasta_zip_only_rejected(self):
        resp = self._upload("genome.zip", "CUSTOM_GENOME_FASTA")
        self.assertEqual(resp.status_code, 400)

    def test_fasta_bam_rejected(self):
        resp = self._upload("genome.bam", "CUSTOM_GENOME_FASTA")
        self.assertEqual(resp.status_code, 400)

    # ── FASTA routes to custom_genome dir ──

    def test_fasta_routes_to_custom_genome_dir(self):
        self._upload("my_genome.fa", "CUSTOM_GENOME_FASTA")
        expected = os.path.join(self.submission.upload_dir, "custom_genome", "my_genome.fa")
        self.assertTrue(os.path.exists(expected))

    # ── Annotation upload ──

    def test_annotation_gtf_accepted(self):
        resp = self._upload("genes.gtf", "CUSTOM_GENOME_ANNOTATION")
        self.assertEqual(resp.status_code, 200)

    def test_annotation_routes_to_custom_genome_dir(self):
        self._upload("genes.gff", "CUSTOM_GENOME_ANNOTATION")
        expected = os.path.join(self.submission.upload_dir, "custom_genome", "genes.gff")
        self.assertTrue(os.path.exists(expected))

    # ── Combined FASTA + annotation ──

    def test_both_fasta_and_annotation_upload(self):
        resp1 = self._upload("genome.fa", "CUSTOM_GENOME_FASTA")
        resp2 = self._upload("genes.gtf", "CUSTOM_GENOME_ANNOTATION")
        self.assertEqual(resp1.status_code, 200)
        self.assertEqual(resp2.status_code, 200)
        fasta_assets = FileAsset.objects.filter(
            submission=self.submission,
            file_role=FileAsset.FileRole.CUSTOM_GENOME_FASTA,
        )
        annot_assets = FileAsset.objects.filter(
            submission=self.submission,
            file_role=FileAsset.FileRole.CUSTOM_GENOME_ANNOTATION,
        )
        self.assertEqual(fasta_assets.count(), 1)
        self.assertEqual(annot_assets.count(), 1)


class ChunkUploadValidationTest(TestCase):
    """Test ChunkUploadView input validation."""

    def setUp(self):
        self.factory = RequestFactory()
        self.session = Session.objects.create()
        self.submission = AnalysisSubmission.objects.create(session=self.session)
        os.makedirs(self.submission.upload_dir, exist_ok=True)

    def tearDown(self):
        shutil.rmtree(self.submission.upload_dir, ignore_errors=True)

    def _upload(self, **kwargs):
        data = {
            "file": SimpleUploadedFile("test.fq.gz", b"data"),
            "filename": "test.fq.gz",
            "chunk_index": "0",
            "total_chunks": "1",
            "submission_id": str(self.submission.submission_id),
            "file_role": "RAW_FASTQ",
        }
        data.update(kwargs)
        req = self.factory.post("/api/upload/chunk", data)
        req.session_obj = self.session
        return ChunkUploadView.as_view()(req)

    def test_missing_file(self):
        req = self.factory.post("/api/upload/chunk", {
            "filename": "test.fq.gz",
            "chunk_index": "0",
            "total_chunks": "1",
            "submission_id": str(self.submission.submission_id),
            "file_role": "RAW_FASTQ",
        })
        req.session_obj = self.session
        resp = ChunkUploadView.as_view()(req)
        self.assertEqual(resp.status_code, 400)

    def test_missing_submission_id(self):
        resp = self._upload(submission_id="")
        self.assertEqual(resp.status_code, 400)

    def test_invalid_submission_id(self):
        resp = self._upload(submission_id="00000000-0000-0000-0000-000000000000")
        self.assertEqual(resp.status_code, 400)

    def test_invalid_file_role(self):
        resp = self._upload(file_role="BOGUS_ROLE")
        self.assertEqual(resp.status_code, 400)

    def test_other_sessions_submission_rejected(self):
        other_session = Session.objects.create()
        other_sub = AnalysisSubmission.objects.create(session=other_session)
        resp = self._upload(submission_id=str(other_sub.submission_id))
        self.assertEqual(resp.status_code, 400)

    def test_path_traversal_sanitized(self):
        resp = self._upload(filename="../../etc/passwd")
        self.assertEqual(resp.status_code, 200)
        # Should not create file outside upload_dir
        self.assertFalse(os.path.exists("/etc/passwd_upload"))
        # File should be stored under raw/passwd
        expected = os.path.join(self.submission.upload_dir, "raw", "passwd")
        self.assertTrue(os.path.exists(expected))


class FileAssetDeleteTest(TestCase):
    """Test FileAssetDeleteView — individual file removal."""

    def setUp(self):
        self.factory = RequestFactory()
        self.session = Session.objects.create()
        self.submission = AnalysisSubmission.objects.create(session=self.session)

    def _delete(self, asset_id):
        req = self.factory.delete(f"/api/files/{asset_id}/")
        req.session_obj = self.session
        return FileAssetDeleteView.as_view()(req, asset_id=asset_id)

    def test_deletes_asset_and_file(self):
        with tempfile.NamedTemporaryFile(delete=False, suffix=".fq.gz") as f:
            f.write(b"data")
            fpath = f.name
        asset = FileAsset.objects.create(
            session=self.session,
            submission=self.submission,
            file_role=FileAsset.FileRole.RAW_FASTQ,
            local_path=fpath,
            is_user_uploaded=True,
        )
        resp = self._delete(str(asset.id))
        self.assertEqual(resp.status_code, 200)
        self.assertFalse(FileAsset.objects.filter(id=asset.id).exists())
        self.assertFalse(os.path.exists(fpath))

    def test_cannot_delete_other_sessions_asset(self):
        other_session = Session.objects.create()
        asset = FileAsset.objects.create(
            session=other_session,
            file_role=FileAsset.FileRole.RAW_FASTQ,
            local_path="/tmp/fake.fq.gz",
            is_user_uploaded=True,
        )
        resp = self._delete(str(asset.id))
        self.assertEqual(resp.status_code, 404)

    def test_nonexistent_asset(self):
        resp = self._delete("00000000-0000-0000-0000-000000000000")
        self.assertEqual(resp.status_code, 404)
