# RNAseek Agents

Custom agents for the RNAseek bioinformatics platform.

## tier2-module

Specialized agent for implementing Tier 2 analytical modules. Scaffolds the module file, wires dispatch into `core.py`, re-exports symbols in `__init__.py`, and writes tests. Knows all RNAseek conventions for shell safety, progress tracking, FileAsset registration, and tenant isolation.

**Use when:** Implementing any of the 12 Tier 2 modules (alt_splicing, rna_editing, time_series, wgcna, gsea, causal_networks, protein_interactions, literature_nlp, survival, tcga_cancer, biomarkers, mofa_diablo).

**File:** `.github/agents/tier2-module.agent.md`
