"""
Tests for WGCNA & Pathway Enrichment module.

Covers:
- Plot serializers: build_module_trait_heatmap, build_pathway_dotplot
- Helper functions: _load_and_validate, _encode_traits, _find_top_module,
  _extract_hub_genes, _significance_labels
- Dispatcher: _dispatch_wgcna (mocked engine)
- Engine: execute_wgcna_and_pathways (mocked PyWGCNA + gseapy)
- Integration: run_tier2_module routes WGCNA correctly
"""

import json
import os
import tempfile
import uuid
from unittest.mock import MagicMock, patch

import numpy as np
import pandas as pd
from django.test import TestCase

from pipeline.models import AnalysisJob, AnalysisSubmission, FileAsset, Session
from pipeline.stats._plots_wgcna import (
    _empty_dotplot,
    _significance_labels,
    build_module_trait_heatmap,
    build_pathway_dotplot,
)


# ── Plot Serializer Tests ────────────────────────────────────


class ModuleTraitHeatmapTest(TestCase):
    """Test build_module_trait_heatmap Plotly serialization."""

    def test_basic_heatmap_structure(self):
        cor_df = pd.DataFrame(
            [[0.85, -0.42], [0.10, 0.73]],
            index=["MEblue", "MEred"],
            columns=["condition_treated", "batch"],
        )
        pval_df = pd.DataFrame(
            [[0.001, 0.12], [0.9, 0.005]],
            index=["MEblue", "MEred"],
            columns=["condition_treated", "batch"],
        )

        result = build_module_trait_heatmap(cor_df, pval_df)

        self.assertIn("data", result)
        self.assertIn("layout", result)
        self.assertEqual(len(result["data"]), 1)
        self.assertEqual(result["data"][0]["type"], "heatmap")

    def test_module_name_stripping(self):
        """ME prefix should be stripped from y-axis labels."""
        cor_df = pd.DataFrame(
            [[0.5]], index=["MEturquoise"], columns=["trait"],
        )
        pval_df = pd.DataFrame(
            [[0.01]], index=["MEturquoise"], columns=["trait"],
        )
        result = build_module_trait_heatmap(cor_df, pval_df)
        self.assertEqual(result["data"][0]["y"], ["turquoise"])

    def test_z_values_match(self):
        cor_df = pd.DataFrame(
            [[0.9, -0.3]], index=["MEblue"], columns=["t1", "t2"],
        )
        pval_df = pd.DataFrame(
            [[0.001, 0.5]], index=["MEblue"], columns=["t1", "t2"],
        )
        result = build_module_trait_heatmap(cor_df, pval_df)
        z = result["data"][0]["z"]
        self.assertAlmostEqual(z[0][0], 0.9)
        self.assertAlmostEqual(z[0][1], -0.3)


class PathwayDotplotTest(TestCase):
    """Test build_pathway_dotplot Plotly serialization."""

    def test_empty_df_returns_placeholder(self):
        result = build_pathway_dotplot(pd.DataFrame())
        self.assertIn("annotations", result["layout"])
        self.assertEqual(result["data"][0]["x"], [])

    def test_none_returns_placeholder(self):
        result = build_pathway_dotplot(None)
        self.assertIn("annotations", result["layout"])

    def test_no_significant_terms_returns_placeholder(self):
        df = pd.DataFrame({
            "Term": ["pathway_a"],
            "Adjusted P-value": [0.5],
            "Combined Score": [10.0],
            "Overlap_count": [3],
            "Overlap_size": [50],
            "Overlap": ["3/50"],
        })
        result = build_pathway_dotplot(df)
        self.assertIn("annotations", result["layout"])

    def test_significant_terms_produce_scatter(self):
        df = pd.DataFrame({
            "Term": ["KEGG Pathway A", "GO Process B"],
            "Adjusted P-value": [0.001, 0.01],
            "Combined Score": [150.0, 80.0],
            "Overlap_count": [5, 3],
            "Overlap_size": [50, 40],
            "Overlap": ["5/50", "3/40"],
        })
        result = build_pathway_dotplot(df)
        self.assertEqual(result["data"][0]["type"], "scatter")
        self.assertEqual(len(result["data"][0]["x"]), 2)

    def test_max_terms_caps_output(self):
        n = 30
        df = pd.DataFrame({
            "Term": [f"term_{i}" for i in range(n)],
            "Adjusted P-value": [0.001] * n,
            "Combined Score": list(range(n)),
            "Overlap_count": [5] * n,
            "Overlap_size": [50] * n,
            "Overlap": ["5/50"] * n,
        })
        result = build_pathway_dotplot(df, max_terms=10)
        self.assertEqual(len(result["data"][0]["x"]), 10)


class SignificanceLabelsTest(TestCase):
    """Test _significance_labels helper."""

    def test_star_thresholds(self):
        cor = np.array([[0.9, 0.5, 0.3, 0.1]])
        pval = np.array([[0.0005, 0.005, 0.03, 0.1]])
        labels = _significance_labels(cor, pval)
        self.assertIn("***", labels[0][0])
        self.assertIn("**", labels[0][1])
        self.assertIn("*", labels[0][2])
        self.assertIn("ns", labels[0][3])


# ── Engine Helper Tests ────────────────────────────────────


class LoadAndValidateTest(TestCase):
    """Test _load_and_validate from _module_wgcna."""

    def test_shared_samples_are_filtered(self):
        from pipeline.tasks._module_wgcna import _load_and_validate

        with tempfile.TemporaryDirectory() as tmpdir:
            # Matrix: genes x samples (S1, S2, S3)
            matrix = pd.DataFrame(
                [[10, 20, 30], [40, 50, 60]],
                index=["geneA", "geneB"],
                columns=["S1", "S2", "S3"],
            )
            meta = pd.DataFrame(
                {"condition": ["ctrl", "trt"]},
                index=["S1", "S2"],
            )
            mp = os.path.join(tmpdir, "counts.csv")
            mdp = os.path.join(tmpdir, "meta.csv")
            matrix.to_csv(mp)
            meta.to_csv(mdp)

            counts, traits = _load_and_validate(mp, mdp)
            self.assertEqual(list(counts.columns), ["S1", "S2"])
            self.assertEqual(list(traits.index), ["S1", "S2"])

    def test_no_overlap_raises(self):
        from pipeline.tasks._module_wgcna import _load_and_validate

        with tempfile.TemporaryDirectory() as tmpdir:
            matrix = pd.DataFrame(
                [[10]], index=["g1"], columns=["A"],
            )
            meta = pd.DataFrame({"x": [1]}, index=["Z"])
            mp = os.path.join(tmpdir, "counts.csv")
            mdp = os.path.join(tmpdir, "meta.csv")
            matrix.to_csv(mp)
            meta.to_csv(mdp)

            with self.assertRaises(ValueError):
                _load_and_validate(mp, mdp)


class EncodeTraitsTest(TestCase):
    """Test _encode_traits from _module_wgcna."""

    def test_numeric_passthrough(self):
        from pipeline.tasks._module_wgcna import _encode_traits

        df = pd.DataFrame({"age": [25, 30, 35]}, index=["S1", "S2", "S3"])
        result = _encode_traits(df)
        self.assertIn("age", result.columns)
        self.assertEqual(len(result.columns), 1)

    def test_categorical_one_hot(self):
        from pipeline.tasks._module_wgcna import _encode_traits

        df = pd.DataFrame(
            {"condition": ["ctrl", "trt", "ctrl"]},
            index=["S1", "S2", "S3"],
        )
        result = _encode_traits(df)
        self.assertTrue(len(result.columns) >= 2)
        # All values should be 0.0 or 1.0
        for col in result.columns:
            self.assertTrue(set(result[col].unique()).issubset({0.0, 1.0}))

    def test_mixed_columns(self):
        from pipeline.tasks._module_wgcna import _encode_traits

        df = pd.DataFrame(
            {"age": [25, 30], "group": ["A", "B"]},
            index=["S1", "S2"],
        )
        result = _encode_traits(df)
        self.assertIn("age", result.columns)
        self.assertTrue(any("group" in c for c in result.columns))


class FindTopModuleTest(TestCase):
    """Test _find_top_module from _module_wgcna."""

    def test_selects_lowest_pvalue(self):
        from pipeline.tasks._module_wgcna import _find_top_module

        cor_df = pd.DataFrame(
            [[0.9, 0.1], [0.2, 0.8]],
            index=["MEblue", "MEred"],
            columns=["trait_a", "trait_b"],
        )
        pval_df = pd.DataFrame(
            [[0.05, 0.5], [0.8, 0.001]],
            index=["MEblue", "MEred"],
            columns=["trait_a", "trait_b"],
        )
        module, trait, pval = _find_top_module(cor_df, pval_df)
        self.assertEqual(module, "red")
        self.assertEqual(trait, "trait_b")
        self.assertAlmostEqual(pval, 0.001)

    def test_grey_excluded(self):
        from pipeline.tasks._module_wgcna import _find_top_module

        cor_df = pd.DataFrame(
            [[0.99], [0.2]],
            index=["MEgrey", "MEblue"],
            columns=["trait"],
        )
        pval_df = pd.DataFrame(
            [[1e-10], [0.01]],
            index=["MEgrey", "MEblue"],
            columns=["trait"],
        )
        module, trait, pval = _find_top_module(cor_df, pval_df)
        self.assertEqual(module, "blue")

    def test_all_grey_raises(self):
        from pipeline.tasks._module_wgcna import _find_top_module

        cor_df = pd.DataFrame(
            [[0.5]], index=["MEgrey"], columns=["trait"],
        )
        pval_df = pd.DataFrame(
            [[0.01]], index=["MEgrey"], columns=["trait"],
        )
        with self.assertRaises(ValueError):
            _find_top_module(cor_df, pval_df)


class ExtractHubGenesTest(TestCase):
    """Test _extract_hub_genes from _module_wgcna."""

    def test_returns_correct_count(self):
        from pipeline.tasks._module_wgcna import _extract_hub_genes

        n_samples, n_genes = 10, 20
        rng = np.random.default_rng(42)

        # Build mock wgcna_obj with AnnData-like attributes
        wgcna_obj = MagicMock()

        # datExpr: samples x genes
        expr_data = rng.normal(size=(n_samples, n_genes))
        gene_names = [f"gene_{i}" for i in range(n_genes)]
        sample_names = [f"S{i}" for i in range(n_samples)]

        wgcna_obj.datExpr.X = expr_data
        wgcna_obj.datExpr.obs_names = sample_names
        wgcna_obj.datExpr.var_names = gene_names

        # Module eigengene
        me_values = rng.normal(size=n_samples)
        wgcna_obj.datME = pd.DataFrame(
            {"MEblue": me_values}, index=sample_names,
        )

        gene_module_df = pd.DataFrame({
            "gene": gene_names,
            "module": ["blue"] * n_genes,
        })

        hub = _extract_hub_genes(wgcna_obj, gene_module_df, "blue", 5)
        self.assertEqual(len(hub), 5)
        # All returned genes should be in the original gene list
        for g in hub:
            self.assertIn(g, gene_names)

    def test_empty_module_raises(self):
        from pipeline.tasks._module_wgcna import _extract_hub_genes

        wgcna_obj = MagicMock()
        gene_module_df = pd.DataFrame({"gene": ["g1"], "module": ["red"]})

        with self.assertRaises(ValueError):
            _extract_hub_genes(wgcna_obj, gene_module_df, "blue", 5)


# ── Dispatcher Tests ─────────────────────────────────────────


class DispatchWgcnaTest(TestCase):
    """Test _dispatch_wgcna resolves FileAssets and delegates."""

    def setUp(self):
        self.session = Session.objects.create()
        self.submission = AnalysisSubmission.objects.create(
            session=self.session,
        )
        self.job = AnalysisJob.objects.create(
            session=self.session,
            module_name="WGCNA",
        )

        # Create required FileAssets
        self.matrix_asset = FileAsset.objects.create(
            session=self.session,
            submission=self.submission,
            file_role=FileAsset.FileRole.NORMALIZED_COUNTS,
            local_path="/tmp/normalized_counts.csv",
            is_user_uploaded=False,
        )
        self.meta_asset = FileAsset.objects.create(
            session=self.session,
            submission=self.submission,
            file_role=FileAsset.FileRole.METADATA_CSV,
            local_path="/tmp/metadata.csv",
            is_user_uploaded=True,
        )

    @patch("pipeline.tasks._module_wgcna.execute_wgcna_and_pathways")
    def test_dispatch_calls_engine_with_correct_paths(self, mock_engine):
        from pipeline.tasks.core import _dispatch_wgcna

        mock_engine.return_value = {"plot_data": {}, "hub_genes": []}

        _dispatch_wgcna(
            self.job,
            str(self.session.session_id),
            str(self.submission.submission_id),
        )

        mock_engine.assert_called_once()
        call_kwargs = mock_engine.call_args
        self.assertEqual(
            call_kwargs.kwargs["matrix_path"],
            "/tmp/normalized_counts.csv",
        )
        self.assertEqual(
            call_kwargs.kwargs["metadata_path"],
            "/tmp/metadata.csv",
        )

    def test_missing_normalized_counts_raises(self):
        from pipeline.tasks.core import _dispatch_wgcna

        self.matrix_asset.delete()
        with self.assertRaises(FileAsset.DoesNotExist):
            _dispatch_wgcna(
                self.job,
                str(self.session.session_id),
                str(self.submission.submission_id),
            )


# ── Integration: run_tier2_module routes WGCNA ──────────────


class Tier2WgcnaRoutingTest(TestCase):
    """Test that run_tier2_module correctly routes WGCNA module."""

    def setUp(self):
        self.session = Session.objects.create()
        self.submission = AnalysisSubmission.objects.create(
            session=self.session,
        )

        # Core job that completed successfully
        self.core_job = AnalysisJob.objects.create(
            session=self.session,
            module_name="CORE_PIPELINE",
            status=AnalysisJob.Status.SUCCESS,
        )

        # Tier 2 job
        self.tier2_job = AnalysisJob.objects.create(
            session=self.session,
            module_name="WGCNA",
            step_progress={
                "pipeline_steps": ["WGCNA"],
                "completed_steps": [],
                "current_step": None,
                "failed_step": None,
            },
        )

        # Required FileAssets
        FileAsset.objects.create(
            session=self.session,
            submission=self.submission,
            file_role=FileAsset.FileRole.NORMALIZED_COUNTS,
            local_path="/tmp/norm.csv",
            is_user_uploaded=False,
        )
        FileAsset.objects.create(
            session=self.session,
            submission=self.submission,
            file_role=FileAsset.FileRole.METADATA_CSV,
            local_path="/tmp/meta.csv",
            is_user_uploaded=True,
        )

    @patch("pipeline.tasks._module_wgcna.execute_wgcna_and_pathways")
    @patch("pipeline.tasks._helpers._emit_progress")
    def test_wgcna_module_dispatches_to_engine(self, mock_emit, mock_engine):
        """run_tier2_module with module_name='WGCNA' calls the engine."""
        from pipeline.tasks.core import run_tier2_module

        mock_engine.return_value = {
            "plot_data": {"module_trait_heatmap": {}, "pathway_dotplot": {}},
            "hub_genes": ["TP53", "BRCA1"],
            "top_module": "blue",
            "top_trait": "condition_treated",
            "top_module_pvalue": 0.001,
            "enrichment_summary": [],
            "module_gene_counts": {"blue": 100, "red": 50},
        }

        # Use .apply() with task_id so self.request.id matches our job
        run_tier2_module.apply(
            args=[
                str(self.session.session_id),
                str(self.core_job.job_id),
                "WGCNA",
            ],
            task_id=str(self.tier2_job.job_id),
        )

        mock_engine.assert_called_once()

        # Verify job status updated to SUCCESS
        self.tier2_job.refresh_from_db()
        self.assertEqual(self.tier2_job.status, AnalysisJob.Status.SUCCESS)
        self.assertIn("hub_genes", self.tier2_job.result_payload)

    @patch("pipeline.tasks._helpers._emit_progress")
    def test_wgcna_failure_sets_failed_status(self, mock_emit):
        """If the WGCNA engine raises, job should be FAILED."""
        from pipeline.tasks.core import run_tier2_module

        # Engine will fail because the CSV files don't exist.
        # .apply() catches the exception and returns an EagerResult.
        result = run_tier2_module.apply(
            args=[
                str(self.session.session_id),
                str(self.core_job.job_id),
                "WGCNA",
            ],
            task_id=str(self.tier2_job.job_id),
        )

        self.tier2_job.refresh_from_db()
        self.assertEqual(self.tier2_job.status, AnalysisJob.Status.FAILED)

    @patch("pipeline.tasks._helpers._emit_progress")
    def test_non_wgcna_module_still_uses_placeholder(self, mock_emit):
        """Non-WGCNA modules should still use the placeholder flow."""
        from pipeline.tasks.core import run_tier2_module

        other_job = AnalysisJob.objects.create(
            session=self.session,
            module_name="PATHWAY",
            step_progress={
                "pipeline_steps": ["PATHWAY"],
                "completed_steps": [],
                "current_step": None,
                "failed_step": None,
            },
        )

        run_tier2_module.apply(
            args=[
                str(self.session.session_id),
                str(self.core_job.job_id),
                "PATHWAY",
            ],
            task_id=str(other_job.job_id),
        )

        other_job.refresh_from_db()
        self.assertEqual(other_job.status, AnalysisJob.Status.SUCCESS)
        self.assertIn("module", other_job.result_payload)
        self.assertEqual(other_job.result_payload["module"], "PATHWAY")
