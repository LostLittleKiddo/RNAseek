/* ═══════════════════════════════════════════════════════════════════
   RNAseek – Pipeline Setup Wizard
   Multi-step wizard controller for Core Pipeline submission.
   ═══════════════════════════════════════════════════════════════════ */
(function () {
    "use strict";

    /* ─── Configuration ──────────────────────────────────────────── */
    const CSRF = document.querySelector('meta[name="csrf-token"]')?.content || "";
    const IS_PRODUCTION_META = document.querySelector('meta[name="rnaseek-is-production"]')?.content;
    const CHUNK_SIZE = 5 * 1024 * 1024;   // 5 MB per chunk
    const MAX_RETRIES = 3;
    const UPLOAD_TIMEOUT = 120_000;       // 2 min per chunk
    const FILE_SIZE_WARN = 10 * 1024 * 1024 * 1024; // 10 GB

    /* ─── State ──────────────────────────────────────────────────── */
    let submissionId = null;
    let inputDataType = "fastq";
    let assayType = "standard_rna";
    let libraryType = "single";
    let metadataMode = "upload";

    // FASTQ
    let selectedFiles = [];
    let uploadedFiles = [];

    // BAM
    let selectedBamFiles = [];
    let uploadedBamFiles = [];

    // Matrix
    let matrixFile = null;
    let parsedMatrixData = null;

    // CSV metadata
    let csvFile = null;
    let parsedCsvData = null;

    // Manual metadata
    let manualColumns = ["condition"];
    let columnSelectableValues = { condition: [] };

    // Column mapping / contrasts
    let columnMapping = { primary_group: "", batch_effect: "", covariates: [] };
    let contrasts = [];

    // Custom genome
    let customGenomeFiles = { fasta: null, annotation: null };

    // Wizard
    let currentStep = 1;
    const TOTAL_STEPS = 5;
    let isSubmitting = false;

    /* ─── Background Upload Tracker ──────────────────────────────── */
    // Map: filename → { status, controller, assetId, progress, error }
    // status: "pending" | "uploading" | "done" | "failed" | "removing"
    let uploadTracker = {};
    let backgroundUploadPromise = null;

    /* ─── DOM helpers ────────────────────────────────────────────── */
    const $ = (id) => document.getElementById(id);
    const $$ = (sel) => document.querySelectorAll(sel);

    /* ─── DOM references ─────────────────────────────────────────── */
    const toastContainer = $("toast-container");
    const wizardBack = $("wizard-back");
    const wizardNext = $("wizard-next");
    const wizardNavInfo = $("wizard-nav-info");

    const entryPointGroup = $("entry-point-group");
    const assayTypeSection = $("assay-type-section");

    const dropZone = $("drop-zone");
    const fastqInput = $("fastq-input");
    const filePills = $("file-pills");
    const fileList = $("file-list");
    const uploadProgressArea = $("upload-progress-area");
    const pairValidation = $("pair-validation");
    const pairedEndTip = $("paired-end-tip");

    const bamDropZone = $("bam-drop-zone");
    const bamInput = $("bam-input");
    const bamFilePills = $("bam-file-pills");
    const bamFileList = $("bam-file-list");
    const bamUploadProgressArea = $("bam-upload-progress-area");

    const matrixDropZone = $("matrix-drop-zone");
    const matrixInput = $("matrix-input");
    const matrixFileName = $("matrix-file-name");
    const matrixPreviewArea = $("matrix-preview-area");
    const matrixValidation = $("matrix-validation");

    const genomeSelect = $("genome-select");
    const customGenomeSection = $("custom-genome-section");
    const customGenomeName = $("custom-genome-name");
    const customGenomeFasta = $("custom-genome-fasta");
    const customGenomeAnnotation = $("custom-genome-annotation");
    const fastaFileLabel = $("fasta-file-label");
    const annotationFileLabel = $("annotation-file-label");
    const quantLevel = $("quant-level");

    const metaToggle = $("meta-toggle");
    const metaUploadPanel = $("meta-upload-panel");
    const metaManualPanel = $("meta-manual-panel");
    const csvDropZone = $("csv-drop-zone");
    const csvInput = $("csv-input");
    const csvFileName = $("csv-file-name");
    const csvPreviewArea = $("csv-preview-area");
    const csvViewerSection = $("csv-viewer-section");
    const csvViewerInfo = $("csv-viewer-info");
    const csvViewerTable = $("csv-viewer-table");
    const manualMetadataPanel = $("manual-metadata-panel");
    const columnNameInput = $("column-name-input");
    const addColumnBtn = $("add-column-btn");
    const columnChips = $("column-chips");
    const conditionTargetColumn = $("condition-target-column");
    const conditionValueInput = $("condition-value-input");
    const addConditionBtn = $("add-condition-btn");
    const conditionChips = $("condition-chips");
    const metadataHeaderRow = $("metadata-header-row");
    const metadataBody = $("metadata-body");
    const noFilesHint = $("no-files-hint");
    const metaPlaceholderCard = $("meta-placeholder-card");

    const columnMappingSection = $("column-mapping-section");
    const primaryGroupSelect = $("primary-group-select");
    const batchEffectSelect = $("batch-effect-select");
    const covariatesList = $("covariates-list");
    const columnValidationMsg = $("column-validation-msg");
    const contrastSection = $("contrast-section");
    const contrastList = $("contrast-list");
    const addContrastBtn = $("add-contrast-btn");

    const bannerInfo = $("banner-info");
    const IS_PRODUCTION = IS_PRODUCTION_META === "1";

    const adjPvalue = $("adj-pvalue");
    const minLog2fc = $("min-log2fc");
    const maxLog2fc = $("max-log2fc");
    const fcPreview = $("fc-preview");
    const pvalPreview = $("pval-preview");

    const valName = $("val-name");
    const valLibrary = $("val-library");
    const valFiles = $("val-files");
    const valGenome = $("val-genome");
    const valMetadata = $("val-metadata");
    const valMapping = $("val-mapping");
    const submitBtn = $("submit-pipeline");

    const uploadModalBackdrop = $("upload-modal-backdrop");
    const uploadModalBody = $("upload-modal-body");
    const submissionNameInput = $("submission-name");

    const fileMgmtPanel = $("file-mgmt-panel");
    const step2Graphic = $("step2-graphic");
    const fileMgmtList = $("file-mgmt-list");
    const fileMgmtCount = $("file-mgmt-count");

    /* ═══════════════════════════════════════════════════════════════════
       TOAST NOTIFICATION SYSTEM
       ═══════════════════════════════════════════════════════════════════ */

    function showToast(type, title, message, duration) {
        if (duration === undefined) duration = 5000;
        const icons = {
            error: "bi-x-circle-fill",
            success: "bi-check-circle-fill",
            warning: "bi-exclamation-triangle-fill",
            info: "bi-info-circle-fill",
        };
        const toast = document.createElement("div");
        toast.className = "rna-toast toast-" + type;
        toast.innerHTML =
            '<span class="toast-icon toast-' + type + '-icon"><i class="bi ' + (icons[type] || icons.info) + '"></i></span>' +
            '<div class="toast-body">' +
            '<p class="toast-title">' + escapeHtml(title) + '</p>' +
            '<p class="toast-message">' + escapeHtml(message) + '</p>' +
            '</div>' +
            '<button class="toast-close" aria-label="Close">&times;</button>';
        toast.querySelector(".toast-close").addEventListener("click", function () { dismissToast(toast); });
        toastContainer.appendChild(toast);
        if (duration > 0) setTimeout(function () { dismissToast(toast); }, duration);
        return toast;
    }

    function dismissToast(el) {
        if (!el || !el.parentNode) return;
        el.classList.add("toast-exit");
        el.addEventListener("animationend", function () { el.remove(); });
    }

    /* ═══════════════════════════════════════════════════════════════════
       WIZARD NAVIGATION
       ═══════════════════════════════════════════════════════════════════ */

    function getEffectiveSteps() {
        // Matrix mode skips step 3 (Reference Genome)
        return inputDataType === "matrix" ? [1, 2, 4, 5] : [1, 2, 3, 4, 5];
    }

    function goToStep(step) {
        var steps = getEffectiveSteps();
        if (steps.indexOf(step) === -1) return;

        $$(".wizard-step").forEach(function (el) { el.classList.remove("active"); });
        var target = $("wizard-step-" + step);
        if (target) target.classList.add("active");

        currentStep = step;
        updateWizardProgress();
        updateWizardNav();
        updateBannerInfo();
        window.scrollTo({ top: 0, behavior: "smooth" });

        if (step === 4) syncMetadataView();
        if (step === 5) validateAll();
    }

    function updateWizardProgress() {
        var steps = getEffectiveSteps();
        $$(".wizard-step-ind").forEach(function (ind) {
            var s = parseInt(ind.dataset.wstep);
            ind.classList.remove("active", "completed", "skipped");
            if (s === currentStep) {
                ind.classList.add("active");
            } else if (steps.indexOf(s) !== -1 && s < currentStep) {
                ind.classList.add("completed");
            } else if (steps.indexOf(s) === -1) {
                ind.classList.add("skipped");
            }
        });

        var lines = [].slice.call($$(".wizard-step-line"));
        lines.forEach(function (line, i) {
            line.classList.remove("completed");
            if (i + 1 < currentStep) line.classList.add("completed");
        });
    }

    function initStepNavigation() {
        $("wizard-progress").addEventListener("click", function (e) {
            var ind = e.target.closest(".wizard-step-ind");
            if (!ind || !ind.classList.contains("completed")) return;
            var step = parseInt(ind.dataset.wstep);
            if (!isNaN(step)) goToStep(step);
        });
    }

    function updateWizardNav() {
        var steps = getEffectiveSteps();
        var idx = steps.indexOf(currentStep);
        wizardBack.style.visibility = idx === 0 ? "hidden" : "visible";

        if (idx === steps.length - 1) {
            wizardNext.style.display = "none";
            if (submitBtn) submitBtn.style.display = "";
        } else {
            wizardNext.style.display = "";
            wizardNext.innerHTML = 'Next <i class="bi bi-arrow-right"></i>';
            if (submitBtn) submitBtn.style.display = "none";
        }
    }

    function nextStep() {
        var errors = validateCurrentStep();
        if (errors.length > 0) {
            errors.forEach(function (msg) { showToast("error", "Required", msg); });
            return;
        }
        // Trigger background upload when leaving Step 2
        if (currentStep === 2) {
            startBackgroundUploads();
        }
        var steps = getEffectiveSteps();
        var idx = steps.indexOf(currentStep);
        if (idx < steps.length - 1) goToStep(steps[idx + 1]);
    }

    function prevStep() {
        var steps = getEffectiveSteps();
        var idx = steps.indexOf(currentStep);
        if (idx > 0) goToStep(steps[idx - 1]);
    }

    function validateCurrentStep() {
        var errors = [];
        switch (currentStep) {
            case 1:
                if (!submissionNameInput.value.trim())
                    errors.push("Please enter a submission name.");
                break;

            case 2:
                if (inputDataType === "fastq") {
                    if (!libraryType) errors.push("Please select a library type (Single-End or Paired-End).");
                    /* Req 3: block paired-end for small RNA */
                    if (assayType === "small_rna" && libraryType === "paired")
                        errors.push("Small RNA / miRNA requires Single-End reads. Paired-End is not supported.");
                    if (selectedFiles.length === 0 && uploadedFiles.length === 0)
                        errors.push("Please upload at least one FASTQ file.");
                    /* Req 1: minimum sample count for FASTQ */
                    var fileCount = selectedFiles.length + uploadedFiles.length;
                    if (libraryType === "paired") {
                        var pairCount = Math.floor(fileCount / 2);
                        if (pairCount < 2 && fileCount > 0)
                            errors.push("At least 2 paired-end samples (2 R1/R2 pairs) are required for differential analysis.");
                    } else {
                        if (fileCount > 0 && fileCount < 2)
                            errors.push("At least 2 FASTQ files (samples) are required for differential analysis.");
                    }
                    if (libraryType === "paired" && selectedFiles.length > 0) {
                        var peR1 = [], peR2 = [], peUnpaired = [];
                        selectedFiles.forEach(function (f) {
                            if (/_R1[._]|_1\.(fq|fastq)\.gz$/i.test(f.name)) peR1.push(f.name);
                            else if (/_R2[._]|_2\.(fq|fastq)\.gz$/i.test(f.name)) peR2.push(f.name);
                            else peUnpaired.push(f.name);
                        });
                        if (peUnpaired.length > 0)
                            errors.push(peUnpaired.length + " file(s) don't match _R1/_R2 naming convention: " + peUnpaired.join(", "));
                        else if (peR1.length !== peR2.length)
                            errors.push("Unequal pairs: " + peR1.length + " R1 and " + peR2.length + " R2 files.");
                    }
                } else if (inputDataType === "alignment") {
                    if (selectedBamFiles.length === 0 && uploadedBamFiles.length === 0)
                        errors.push("Please upload at least one BAM/CRAM file.");
                    /* Req 1: minimum sample count for alignment */
                    var bamCount = selectedBamFiles.length + uploadedBamFiles.length;
                    if (bamCount > 0 && bamCount < 2)
                        errors.push("At least 2 BAM/CRAM files (samples) are required for differential analysis.");
                } else if (inputDataType === "matrix") {
                    if (!matrixFile && !parsedMatrixData)
                        errors.push("Please upload a count matrix file.");
                    if (parsedMatrixData) {
                        var mv = validateMatrixData();
                        if (!mv.valid) errors.push(mv.message);
                    }
                }
                break;

            case 3:
                if (!isGenomeValid())
                    errors.push("Please select a reference genome or configure a custom genome.");
                break;

            case 4:
                if (!isMetadataValid())
                    errors.push("Please configure your metadata (upload a CSV or build manually).");
                if (isMetadataValid() && !isMappingValid())
                    errors.push("Please assign the primary group column in Column Roles.");
                /* Enforce contrast completion when contrast section is visible */
                if (contrastSection.style.display !== "none") {
                    var incomplete = contrasts.filter(function (c) { return !c[0] || !c[1]; });
                    if (contrasts.length === 0 || incomplete.length > 0)
                        errors.push("Please complete all pairwise comparisons in Define Comparisons.");
                }
                /* Req 2: contrast values must exist in primary group column */
                if (contrastSection.style.display !== "none" && isMetadataValid() && primaryGroupSelect.value) {
                    var samples4 = getActiveMetadataSamples();
                    var pg4 = primaryGroupSelect.value;
                    var groupValues = {};
                    for (var si = 0; si < samples4.length; si++) {
                        var gv = (samples4[si][pg4] || "").trim();
                        if (gv) groupValues[gv] = true;
                    }
                    for (var ci2 = 0; ci2 < contrasts.length; ci2++) {
                        var cTarget = (contrasts[ci2][0] || "").trim();
                        var cRef = (contrasts[ci2][1] || "").trim();
                        if (cTarget && !(cTarget in groupValues))
                            errors.push("Contrast target '" + cTarget + "' does not exist in the '" + pg4 + "' column of your metadata.");
                        if (cRef && !(cRef in groupValues))
                            errors.push("Contrast reference '" + cRef + "' does not exist in the '" + pg4 + "' column of your metadata.");
                    }
                }
                /* Validate CSV has a 'sample' column when in upload mode */
                if (metadataMode === "upload" && parsedCsvData) {
                    var hasSampleCol = parsedCsvData.meta.fields.some(function (f) {
                        return f.toLowerCase() === "sample";
                    });
                    if (!hasSampleCol)
                        errors.push("CSV must have a column named 'sample' to match uploaded file names.");
                }
                /* ChIP-seq: require input/control and treatment samples */
                if (inputDataType === "fastq" && assayType === "chip_seq" && isMetadataValid()) {
                    var chipErrors = validateChipSeqMetadata();
                    chipErrors.forEach(function (e) { errors.push(e); });
                }
                /* Batch correction: verify batch column exists in samples */
                if (isMetadataValid()) {
                    var batchErrors = validateBatchColumn();
                    batchErrors.forEach(function (e) { errors.push(e); });
                }
                /* Req 7: sanitized sample names */
                if (isMetadataValid()) {
                    var sNameSamples = getActiveMetadataSamples();
                    var sampleColName = "sample";
                    if (sNameSamples.length > 0 && parsedCsvData && parsedCsvData.meta.fields) {
                        var found = parsedCsvData.meta.fields.find(function (f) { return f.toLowerCase() === "sample"; });
                        if (found) sampleColName = found;
                    }
                    var badNames = [];
                    for (var sni = 0; sni < sNameSamples.length; sni++) {
                        var sn = (sNameSamples[sni][sampleColName] || "").trim();
                        if (sn && !SAFE_NAME_RE.test(sn)) badNames.push(sn);
                    }
                    if (badNames.length > 0)
                        errors.push("Sample names must contain only letters, digits, hyphens, or underscores. Invalid: " + badNames.slice(0, 5).join(", ") + (badNames.length > 5 ? " (and " + (badNames.length - 5) + " more)" : ""));
                }
                /* Req 6: matrix header/metadata match */
                if (inputDataType === "matrix" && parsedMatrixData && isMetadataValid()) {
                    var matHeaders = parsedMatrixData.meta.fields.slice(1).sort();
                    var metaSamples6 = getActiveMetadataSamples();
                    var metaCol6 = "sample";
                    if (metaSamples6.length > 0 && parsedCsvData && parsedCsvData.meta.fields) {
                        var f6 = parsedCsvData.meta.fields.find(function (f) { return f.toLowerCase() === "sample"; });
                        if (f6) metaCol6 = f6;
                    }
                    var metaNames6 = metaSamples6.map(function (r) { return (r[metaCol6] || "").trim(); }).filter(Boolean).sort();
                    var inMatOnly = matHeaders.filter(function (h) { return metaNames6.indexOf(h) === -1; });
                    var inMetaOnly = metaNames6.filter(function (h) { return matHeaders.indexOf(h) === -1; });
                    if (inMatOnly.length > 0 || inMetaOnly.length > 0) {
                        var parts = [];
                        if (inMatOnly.length > 0) parts.push("in matrix but not metadata: " + inMatOnly.slice(0, 5).join(", "));
                        if (inMetaOnly.length > 0) parts.push("in metadata but not matrix: " + inMetaOnly.slice(0, 5).join(", "));
                        errors.push("Matrix column headers and metadata sample names must match exactly. Mismatched — " + parts.join("; ") + ".");
                    }
                }
                break;

            case 5:
                {
                    var pval = parseFloat(adjPvalue.value);
                    if (isNaN(pval) || pval <= 0 || pval > 1)
                        errors.push("Adjusted P-value must be between 0 (exclusive) and 1.");
                    var minFC = parseFloat(minLog2fc.value);
                    var maxFC = parseFloat(maxLog2fc.value);
                    if (!isNaN(minFC) && !isNaN(maxFC) && minFC >= maxFC)
                        errors.push("Min Log2FC must be less than Max Log2FC.");
                }
                break;
        }
        return errors;
    }

    /* ═══════════════════════════════════════════════════════════════════
       ENTRY POINT
       ═══════════════════════════════════════════════════════════════════ */

    function initEntryPoints() {
        entryPointGroup.addEventListener("click", function (e) {
            var card = e.target.closest(".radio-card");
            if (!card || !card.closest("#entry-point-group")) return;
            var radio = card.querySelector('input[name="input_data_type"]');
            if (!radio) return;
            entryPointGroup.querySelectorAll(".radio-card").forEach(function (c) {
                c.classList.remove("selected");
            });
            card.classList.add("selected");
            radio.checked = true;
            inputDataType = radio.value;
            applyEntryPointVisibility();
        });
    }

    function applyEntryPointVisibility() {
        $("col-fastq").style.display = inputDataType === "fastq" ? "" : "none";
        $("col-alignment").style.display = inputDataType === "alignment" ? "" : "none";
        $("col-matrix").style.display = inputDataType === "matrix" ? "" : "none";

        assayTypeSection.style.display = inputDataType === "fastq" ? "" : "none";

        if (valGenome) valGenome.style.display = inputDataType === "matrix" ? "none" : "";
        if (valLibrary) valLibrary.style.display = inputDataType === "matrix" ? "none" : "";

        var fl = $("fasta-label");
        if (fl) {
            if (inputDataType === "alignment") {
                fl.innerHTML = 'Reference FASTA <span class="rna-text-muted">(optional for BAM)</span>';
            } else {
                fl.innerHTML = 'Reference FASTA <span class="required">*</span>';
            }
        }

        updateWizardProgress();
        updateWizardNav();
        renderFileManagementPanel();
        applyAssayVisibility();
    }

    /* ═══════════════════════════════════════════════════════════════════
       ASSAY TYPE
       ═══════════════════════════════════════════════════════════════════ */

    function initAssayType() {
        var group = $("assay-type-group");
        if (!group) return;
        group.addEventListener("click", function (e) {
            var card = e.target.closest(".radio-card");
            if (!card || !card.closest("#assay-type-group")) return;
            var radio = card.querySelector('input[name="assay_type"]');
            if (!radio) return;
            group.querySelectorAll(".radio-card").forEach(function (c) {
                c.classList.remove("selected");
            });
            card.classList.add("selected");
            radio.checked = true;
            assayType = radio.value;
            /* Req 3: Small RNA forces Single-End library type */
            if (assayType === "small_rna" && libraryType === "paired") {
                libraryType = "single";
                var singleRadio = document.querySelector('input[name="library_type"][value="single"]');
                if (singleRadio) {
                    singleRadio.checked = true;
                    $$('#col-fastq .library-type-card').forEach(function (c) { c.classList.remove('selected'); });
                    singleRadio.closest('.library-type-card').classList.add('selected');
                }
                if (pairedEndTip) pairedEndTip.classList.remove('visible');
                showToast('info', 'Library Type', 'Small RNA assay requires Single-End reads. Library type has been set to Single-End.');
            }
            applyAssayVisibility();
        });
    }

    /* ── Genomes that have a miRBase index (small RNA only) ── */
    var MIRBASE_GENOMES = ["hg38", "mm39", "mm10", "rn7", "danRer11", "galGal6", "dm6", "wbcel235", "araTha"];

    /* ── Assay-aware tooltip / warning text ── */
    var GENOME_TOOLTIPS = {
        standard_rna: "Select the reference genome matching your organism. HISAT2 uses pre-built indices for splice-aware alignment. Choose \u201cCustom\u201d to upload your own.",
        small_rna: "Select the organism for miRBase alignment. Only organisms with pre-built miRBase indices are available. Custom genomes are not supported for small RNA.",
        chip_seq: "Select the reference genome matching your organism. BWA MEM uses pre-built indices for gapped alignment. Choose \u201cCustom\u201d to upload your own.",
        methylation: "Select the reference genome matching your organism. Bismark uses pre-built bisulfite-converted indices. Choose \u201cCustom\u201d to upload your own.",
    };
    var CUSTOM_GENOME_WARNINGS = {
        standard_rna: "Building a <strong>HISAT2</strong> index can take <strong>30 min to several hours</strong> depending on genome size.",
        chip_seq: "Building a <strong>BWA</strong> index can take <strong>30 min to several hours</strong> depending on genome size.",
        methylation: "Running <strong>Bismark genome preparation</strong> can take <strong>30 min to several hours</strong> depending on genome size.",
    };

    /**
     * Show/hide UI elements that depend on the selected assay type.
     *
     * Strandedness:  only standard_rna uses it
     * Quant level:   only standard_rna and chip_seq use featureCounts
     * Genome filter: small_rna restricts to MIRBASE_GENOMES; no custom genome
     * Custom genome: not available for small_rna
     */
    function applyAssayVisibility() {
        /* ── Step 2: Strandedness ── */
        var strandSection = $("strandedness-section");
        if (strandSection) {
            strandSection.style.display = (assayType === "standard_rna") ? "" : "none";
            // Reset to unstranded when hidden
            if (assayType !== "standard_rna") {
                var strandSel = $("strandedness");
                if (strandSel) strandSel.value = "unstranded";
            }
        }

        /* ── Step 3: Quant Level ── */
        var quantSection = $("quant-level-section");
        if (quantSection) {
            var showQuant = (assayType === "standard_rna" || assayType === "chip_seq");
            quantSection.style.display = showQuant ? "" : "none";
            if (!showQuant && quantLevel) quantLevel.value = "gene";
        }

        /* ── Step 3: Genome dropdown filtering ── */
        if (genomeSelect) {
            var options = genomeSelect.querySelectorAll("option");
            options.forEach(function (opt) {
                if (!opt.value) return; // placeholder
                if (assayType === "small_rna") {
                    if (opt.value === "custom") {
                        opt.disabled = true;
                        opt.style.display = "none";
                    } else if (MIRBASE_GENOMES.indexOf(opt.value) === -1 && opt.value !== "") {
                        opt.disabled = true;
                        opt.style.opacity = "0.4";
                        opt.style.display = "";
                    } else {
                        opt.disabled = false;
                        opt.style.opacity = "";
                        opt.style.display = "";
                    }
                } else {
                    opt.disabled = false;
                    opt.style.opacity = "";
                    opt.style.display = "";
                }
            });
            // Also hide/show the Custom optgroup
            var customOptgroup = genomeSelect.querySelector('optgroup[label="Custom"]');
            if (customOptgroup) customOptgroup.style.display = (assayType === "small_rna") ? "none" : "";

            // Reset selection if current value is now disabled
            var selected = genomeSelect.options[genomeSelect.selectedIndex];
            if (selected && selected.disabled) {
                genomeSelect.value = "";
                customGenomeSection.classList.remove("visible");
            }
        }

        /* ── Step 3: Custom genome section ── */
        if (customGenomeSection) {
            if (assayType === "small_rna") {
                customGenomeSection.classList.remove("visible");
            }
        }

        /* ── Step 3: Custom genome warning text ── */
        var warningEl = $("custom-genome-warning");
        if (warningEl) {
            var warningSpan = warningEl.querySelector("span");
            if (warningSpan && CUSTOM_GENOME_WARNINGS[assayType]) {
                warningSpan.innerHTML = "<strong>Attention:</strong> " + CUSTOM_GENOME_WARNINGS[assayType];
            }
        }

        /* ── Step 3: Genome tooltip ── */
        var tooltipEl = $("genome-tooltip-text");
        if (tooltipEl && GENOME_TOOLTIPS[assayType]) {
            tooltipEl.textContent = GENOME_TOOLTIPS[assayType];
        }
    }

    /* ═══════════════════════════════════════════════════════════════════
       LIBRARY TYPE
       ═══════════════════════════════════════════════════════════════════ */

    function initLibraryType() {
        $$('input[name="library_type"]').forEach(function (radio) {
            radio.addEventListener("change", function () {
                libraryType = radio.value;
                $$("#col-fastq .library-type-card").forEach(function (c) {
                    c.classList.remove("selected");
                });
                radio.closest(".library-type-card").classList.add("selected");

                if (pairedEndTip) pairedEndTip.classList.toggle("visible", libraryType === "paired");
                if (selectedFiles.length > 0) validatePairedEnd();
                if (metadataMode === "manual") rebuildMetadataTable();
                if (parsedCsvData) renderCsvViewer();
            });
        });

        $$('input[name="library_type_alignment"]').forEach(function (radio) {
            radio.addEventListener("change", function () {
                $$("#col-alignment .library-type-card").forEach(function (c) {
                    c.classList.remove("selected");
                });
                radio.closest(".library-type-card").classList.add("selected");
            });
        });
    }

    /* ═══════════════════════════════════════════════════════════════════
       PARSE OVERLAY HELPER
       ═══════════════════════════════════════════════════════════════════ */

    function showParseOverlay(dropZoneEl) {
        removeParseOverlay(dropZoneEl);
        var overlay = document.createElement("div");
        overlay.className = "parse-overlay";
        overlay.innerHTML = '<div class="parse-spinner"></div><span class="parse-overlay-text">Parsing\u2026</span>';
        dropZoneEl.appendChild(overlay);
    }

    function removeParseOverlay(dropZoneEl) {
        var existing = dropZoneEl.querySelector(".parse-overlay");
        if (existing) existing.remove();
    }

    /* ═══════════════════════════════════════════════════════════════════
       FASTQ FILE UPLOAD
       ═══════════════════════════════════════════════════════════════════ */

    function warnLargeFiles(files) {
        var large = files.filter(function (f) { return f.size > FILE_SIZE_WARN; });
        if (large.length > 0) {
            var names = large.map(function (f) { return f.name; }).join(", ");
            showToast("warning", "Large File Warning",
                large.length + " file(s) exceed 10 GB and may take a long time to upload: " + names);
        }
    }

    function initFastqUpload() {
        dropZone.addEventListener("click", function () { fastqInput.click(); });
        dropZone.addEventListener("keydown", function (e) {
            if (e.key === "Enter" || e.key === " ") { e.preventDefault(); fastqInput.click(); }
        });
        dropZone.addEventListener("dragover", function (e) {
            e.preventDefault(); dropZone.classList.add("drag-over");
        });
        dropZone.addEventListener("dragleave", function () {
            dropZone.classList.remove("drag-over");
        });
        dropZone.addEventListener("drop", function (e) {
            e.preventDefault(); dropZone.classList.remove("drag-over");
            addFiles(e.dataTransfer.files);
        });
        fastqInput.addEventListener("change", function () {
            addFiles(fastqInput.files); fastqInput.value = "";
        });
    }

    function addFiles(fileListObj) {
        var allFiles = [].slice.call(fileListObj);
        var newFiles = allFiles.filter(function (f) {
            var n = f.name.toLowerCase();
            return n.endsWith(".fq.gz") || n.endsWith(".fastq.gz");
        });
        var rejected = allFiles.length - newFiles.length;
        if (newFiles.length === 0) {
            showToast("warning", "Invalid Files", "Only .fq.gz or .fastq.gz files are accepted." + (rejected > 0 ? " " + rejected + " file(s) rejected." : ""));
            return;
        }
        if (rejected > 0) {
            showToast("warning", "Files Filtered", rejected + " non-FASTQ file(s) were ignored. Only .fq.gz / .fastq.gz accepted.");
        }
        warnLargeFiles(newFiles);
        var existing = {};
        selectedFiles.forEach(function (f) { existing[f.name] = true; });
        newFiles.forEach(function (f) {
            if (!existing[f.name]) {
                selectedFiles.push(f);
                existing[f.name] = true;
                // Mark as pending in tracker (not uploading yet)
                if (!uploadTracker[f.name]) {
                    uploadTracker[f.name] = { status: "pending", controller: null, assetId: null, progress: 0, error: null };
                }
            }
        });
        renderFilePills();
        renderFileManagementPanel();
        validatePairedEnd();
        if (metadataMode === "manual") rebuildMetadataTable();
        if (parsedCsvData) renderCsvViewer();
    }

    async function removeFile(index) {
        var file = selectedFiles[index];
        if (!file) return;
        var fname = file.name;
        var tracker = uploadTracker[fname];

        if (tracker) {
            tracker.status = "removing";
            renderFileManagementPanel();

            // Abort in-flight upload
            if (tracker.controller) {
                tracker.controller.abort();
                tracker.controller = null;
            }

            // Delete from backend if already uploaded
            if (tracker.assetId) {
                try {
                    await fetch("/api/files/" + tracker.assetId + "/", {
                        method: "DELETE",
                        headers: { "X-CSRFToken": CSRF },
                    });
                } catch (_e) { /* best-effort */ }
            }

            delete uploadTracker[fname];
        }

        // Remove from uploaded list
        var upIdx = uploadedFiles.indexOf(fname);
        if (upIdx !== -1) uploadedFiles.splice(upIdx, 1);

        selectedFiles.splice(index, 1);
        renderFilePills();
        renderFileManagementPanel();
        validatePairedEnd();
        if (metadataMode === "manual") rebuildMetadataTable();
        if (parsedCsvData) renderCsvViewer();
    }

    function renderFilePills() {
        // File pills removed from UI
    }

    function formatFileSize(bytes) {
        if (bytes === 0) return '0 B';
        var k = 1024;
        var sizes = ['B', 'KB', 'MB', 'GB'];
        var i = Math.floor(Math.log(bytes) / Math.log(k));
        return parseFloat((bytes / Math.pow(k, i)).toFixed(1)) + ' ' + sizes[i];
    }

    function renderFileManagementPanel() {
        if (!fileMgmtPanel || !fileMgmtList) return;

        var files = [];
        if (inputDataType === "fastq") {
            files = selectedFiles;
        } else if (inputDataType === "alignment") {
            files = selectedBamFiles;
        } else if (inputDataType === "matrix" && matrixFile) {
            files = [matrixFile];
        }

        if (files.length === 0) {
            fileMgmtPanel.style.display = "none";
            if (step2Graphic) step2Graphic.style.display = "";
            return;
        }

        fileMgmtPanel.style.display = "";
        if (step2Graphic) step2Graphic.style.display = "none";
        if (fileMgmtCount) fileMgmtCount.textContent = files.length + " file" + (files.length !== 1 ? "s" : "");

        var icon = "bi-file-earmark-zip";
        if (inputDataType === "alignment") icon = "bi-file-earmark-binary";
        if (inputDataType === "matrix") icon = "bi-file-earmark-spreadsheet";

        fileMgmtList.innerHTML = files.map(function (f, i) {
            var tracker = uploadTracker[f.name];
            var statusClass = "";
            var statusBadge = "";
            var progressBar = "";
            var isRemoving = false;

            if (tracker) {
                if (tracker.status === "removing") {
                    statusClass = " file-mgmt-removing";
                    isRemoving = true;
                    statusBadge = '<span class="file-mgmt-status file-mgmt-status-removing"><i class="bi bi-arrow-repeat rna-processing"></i></span>';
                } else if (tracker.status === "uploading") {
                    statusClass = " file-mgmt-uploading";
                    statusBadge = '<span class="file-mgmt-status file-mgmt-status-uploading">' + tracker.progress + '%</span>';
                    progressBar = '<div class="file-mgmt-progress"><div class="file-mgmt-progress-bar" style="width:' + tracker.progress + '%"></div></div>';
                } else if (tracker.status === "done") {
                    statusBadge = '<span class="file-mgmt-status file-mgmt-status-done"><i class="bi bi-check-circle-fill"></i></span>';
                } else if (tracker.status === "failed") {
                    statusBadge = '<span class="file-mgmt-status file-mgmt-status-failed"><i class="bi bi-exclamation-circle"></i></span>';
                } else if (tracker.status === "pending") {
                    statusBadge = '<span class="file-mgmt-status file-mgmt-status-pending"><i class="bi bi-clock"></i></span>';
                }
            }

            return '<div class="file-mgmt-row' + statusClass + '" data-filename="' + escapeHtml(f.name) + '">' +
                '<div class="file-mgmt-icon"><i class="bi ' + icon + '"></i></div>' +
                '<div class="file-mgmt-info">' +
                '<div class="file-mgmt-name">' + escapeHtml(f.name) + '</div>' +
                '<div class="file-mgmt-size">' + formatFileSize(f.size) + '</div>' +
                progressBar +
                '</div>' +
                statusBadge +
                (isRemoving ? '' : '<button type="button" class="file-mgmt-remove" data-idx="' + i + '" title="Remove">' +
                    '<i class="bi bi-trash"></i></button>') +
                '</div>';
        }).join("");

        fileMgmtList.querySelectorAll(".file-mgmt-remove").forEach(function (btn) {
            btn.addEventListener("click", function () {
                var idx = parseInt(btn.dataset.idx);
                if (inputDataType === "fastq") {
                    removeFile(idx);
                } else if (inputDataType === "alignment") {
                    removeBamFile(idx);
                } else if (inputDataType === "matrix") {
                    matrixFile = null;
                    parsedMatrixData = null;
                    matrixFileName.style.display = "none";
                    matrixPreviewArea.style.display = "none";
                    matrixValidation.style.display = "none";
                    renderFileManagementPanel();
                }
            });
        });
    }

    function validatePairedEnd() {
        if (libraryType !== "paired" || selectedFiles.length === 0) {
            pairValidation.style.display = "none";
            return;
        }
        var r1 = [], r2 = [], unpaired = [];
        selectedFiles.forEach(function (f) {
            if (/_R1[._]|_1\.(fq|fastq)\.gz$/i.test(f.name)) r1.push(f.name);
            else if (/_R2[._]|_2\.(fq|fastq)\.gz$/i.test(f.name)) r2.push(f.name);
            else unpaired.push(f.name);
        });
        pairValidation.style.display = "";
        if (unpaired.length > 0) {
            pairValidation.className = "validation-msg error";
            pairValidation.textContent = unpaired.length + " file(s) don't match _R1/_R2 naming: " + unpaired.join(", ");
        } else if (r1.length !== r2.length) {
            pairValidation.className = "validation-msg error";
            pairValidation.textContent = "Unequal pairs: " + r1.length + " R1, " + r2.length + " R2 files.";
        } else {
            pairValidation.className = "validation-msg success";
            pairValidation.textContent = r1.length + " paired sample(s) detected.";
        }
    }

    async function uploadFastqFiles() {
        // Called as part of startBackgroundUploads — uploads all pending FASTQ files
        if (selectedFiles.length === 0) return;
        await ensureSubmission();

        for (var i = 0; i < selectedFiles.length; i++) {
            var file = selectedFiles[i];
            var tracker = uploadTracker[file.name];
            if (!tracker || tracker.status !== "pending") continue;

            tracker.status = "uploading";
            tracker.controller = new AbortController();
            tracker.progress = 0;
            renderFileManagementPanel();

            var totalChunks = Math.ceil(file.size / CHUNK_SIZE);
            var success = true;

            for (var c = 0; c < totalChunks; c++) {
                // Check if aborted before each chunk
                if (tracker.controller.signal.aborted) {
                    success = false;
                    break;
                }
                var ok = await uploadChunkWithRetry(file, c, totalChunks, "RAW_FASTQ", tracker.controller);
                if (!ok) {
                    success = false;
                    if (!tracker.controller.signal.aborted) {
                        showToast("error", "Upload Failed", "Failed to upload " + file.name);
                    }
                    break;
                }
                tracker.progress = Math.round(((c + 1) / totalChunks) * 100);
                renderFileManagementPanel();
            }

            // Re-check tracker still exists (file may have been removed)
            if (!uploadTracker[file.name]) continue;

            if (success) {
                tracker.status = "done";
                tracker.controller = null;
                uploadedFiles.push(file.name);
                // Read asset_id from last chunk response
            } else if (!tracker.controller || !tracker.controller.signal.aborted) {
                tracker.status = "failed";
                tracker.controller = null;
            }
            renderFileManagementPanel();
        }
    }

    /* ─── Background Upload Orchestrator ─────────────────────────── */

    function startBackgroundUploads() {
        if (backgroundUploadPromise) return; // already running

        backgroundUploadPromise = (async function () {
            try {
                if (inputDataType === "fastq") {
                    await uploadFastqFiles();
                } else if (inputDataType === "alignment") {
                    await uploadBamFiles();
                }
                // Matrix uploads are small and happen at submit time
            } catch (err) {
                // Errors are handled per-file
            } finally {
                backgroundUploadPromise = null;
            }
        })();
    }

    function areUploadsComplete() {
        var files = inputDataType === "fastq" ? selectedFiles :
            inputDataType === "alignment" ? selectedBamFiles : [];
        for (var i = 0; i < files.length; i++) {
            var t = uploadTracker[files[i].name];
            if (t && (t.status === "pending" || t.status === "uploading")) return false;
        }
        return true;
    }

    function getUploadProgress() {
        var files = inputDataType === "fastq" ? selectedFiles :
            inputDataType === "alignment" ? selectedBamFiles : [];
        if (files.length === 0) return 100;
        var total = 0;
        for (var i = 0; i < files.length; i++) {
            var t = uploadTracker[files[i].name];
            if (!t || t.status === "done") total += 100;
            else if (t.status === "uploading") total += t.progress;
            // pending = 0, failed = 0
        }
        return Math.round(total / files.length);
    }

    /* ═══════════════════════════════════════════════════════════════════
       BAM FILE UPLOAD
       ═══════════════════════════════════════════════════════════════════ */

    function initBamUpload() {
        bamDropZone.addEventListener("click", function () { bamInput.click(); });
        bamDropZone.addEventListener("keydown", function (e) {
            if (e.key === "Enter" || e.key === " ") { e.preventDefault(); bamInput.click(); }
        });
        bamDropZone.addEventListener("dragover", function (e) {
            e.preventDefault(); bamDropZone.classList.add("drag-over");
        });
        bamDropZone.addEventListener("dragleave", function () {
            bamDropZone.classList.remove("drag-over");
        });
        bamDropZone.addEventListener("drop", function (e) {
            e.preventDefault(); bamDropZone.classList.remove("drag-over");
            addBamFiles(e.dataTransfer.files);
        });
        bamInput.addEventListener("change", function () {
            addBamFiles(bamInput.files); bamInput.value = "";
        });
    }

    function addBamFiles(fileListObj) {
        var newFiles = [].slice.call(fileListObj).filter(function (f) {
            var n = f.name.toLowerCase();
            return n.endsWith(".bam") || n.endsWith(".cram");
        });
        if (newFiles.length === 0) {
            showToast("warning", "Invalid Files", "Please select .bam or .cram files.");
            return;
        }
        warnLargeFiles(newFiles);
        var existing = {};
        selectedBamFiles.forEach(function (f) { existing[f.name] = true; });
        newFiles.forEach(function (f) {
            if (!existing[f.name]) {
                selectedBamFiles.push(f);
                existing[f.name] = true;
                if (!uploadTracker[f.name]) {
                    uploadTracker[f.name] = { status: "pending", controller: null, assetId: null, progress: 0, error: null };
                }
            }
        });
        renderBamFilePills();
        renderFileManagementPanel();
        if (metadataMode === "manual") rebuildMetadataTable();
        if (parsedCsvData) renderCsvViewer();
    }

    async function removeBamFile(index) {
        var file = selectedBamFiles[index];
        if (!file) return;
        var fname = file.name;
        var tracker = uploadTracker[fname];

        if (tracker) {
            tracker.status = "removing";
            renderFileManagementPanel();

            if (tracker.controller) {
                tracker.controller.abort();
                tracker.controller = null;
            }

            if (tracker.assetId) {
                try {
                    await fetch("/api/files/" + tracker.assetId + "/", {
                        method: "DELETE",
                        headers: { "X-CSRFToken": CSRF },
                    });
                } catch (_e) { /* best-effort */ }
            }

            delete uploadTracker[fname];
        }

        var upIdx = uploadedBamFiles.indexOf(fname);
        if (upIdx !== -1) uploadedBamFiles.splice(upIdx, 1);

        selectedBamFiles.splice(index, 1);
        renderBamFilePills();
        renderFileManagementPanel();
        if (metadataMode === "manual") rebuildMetadataTable();
        if (parsedCsvData) renderCsvViewer();
    }

    function renderBamFilePills() {
        // BAM file pills removed from UI
    }

    async function uploadBamFiles() {
        if (selectedBamFiles.length === 0) return;
        await ensureSubmission();

        for (var i = 0; i < selectedBamFiles.length; i++) {
            var file = selectedBamFiles[i];
            var tracker = uploadTracker[file.name];
            if (!tracker || tracker.status !== "pending") continue;

            tracker.status = "uploading";
            tracker.controller = new AbortController();
            tracker.progress = 0;
            renderFileManagementPanel();

            var totalChunks = Math.ceil(file.size / CHUNK_SIZE);
            var success = true;

            for (var c = 0; c < totalChunks; c++) {
                if (tracker.controller.signal.aborted) {
                    success = false;
                    break;
                }
                var ok = await uploadChunkWithRetry(file, c, totalChunks, "ALIGNMENT_BAM", tracker.controller);
                if (!ok) {
                    success = false;
                    if (!tracker.controller.signal.aborted) {
                        showToast("error", "Upload Failed", "Failed to upload " + file.name);
                    }
                    break;
                }
                tracker.progress = Math.round(((c + 1) / totalChunks) * 100);
                renderFileManagementPanel();
            }

            if (!uploadTracker[file.name]) continue;

            if (success) {
                tracker.status = "done";
                tracker.controller = null;
                uploadedBamFiles.push(file.name);
            } else if (!tracker.controller || !tracker.controller.signal.aborted) {
                tracker.status = "failed";
                tracker.controller = null;
            }
            renderFileManagementPanel();
        }
    }

    /* ═══════════════════════════════════════════════════════════════════
       COUNT MATRIX UPLOAD
       ═══════════════════════════════════════════════════════════════════ */

    function initMatrixUpload() {
        matrixDropZone.addEventListener("click", function () { matrixInput.click(); });
        matrixDropZone.addEventListener("keydown", function (e) {
            if (e.key === "Enter" || e.key === " ") { e.preventDefault(); matrixInput.click(); }
        });
        matrixDropZone.addEventListener("dragover", function (e) {
            e.preventDefault(); matrixDropZone.classList.add("drag-over");
        });
        matrixDropZone.addEventListener("dragleave", function () {
            matrixDropZone.classList.remove("drag-over");
        });
        matrixDropZone.addEventListener("drop", function (e) {
            e.preventDefault(); matrixDropZone.classList.remove("drag-over");
            if (e.dataTransfer.files.length) setMatrixFile(e.dataTransfer.files[0]);
        });
        matrixInput.addEventListener("change", function () {
            if (matrixInput.files.length) setMatrixFile(matrixInput.files[0]);
            matrixInput.value = "";
        });
    }

    function setMatrixFile(file) {
        matrixFile = file;
        matrixFileName.style.display = "";
        matrixFileName.textContent = "Selected: " + file.name;
        if (file.size > FILE_SIZE_WARN) {
            showToast("warning", "Large File Warning", file.name + " exceeds 10 GB and may take a long time to upload.");
        }
        showParseOverlay(matrixDropZone);

        var reader = new FileReader();
        reader.onload = function (e) {
            removeParseOverlay(matrixDropZone);
            var delimiter = file.name.toLowerCase().endsWith(".tsv") ? "\t" : ",";
            parsedMatrixData = Papa.parse(e.target.result, {
                header: true, skipEmptyLines: true, delimiter: delimiter,
            });
            var v = validateMatrixData();
            matrixValidation.style.display = "";
            matrixValidation.className = "validation-msg " + (v.valid ? "success" : "error");
            matrixValidation.textContent = v.message;

            if (v.valid) {
                renderMatrixPreview();
                renderFileManagementPanel();
                if (metadataMode === "manual") rebuildMetadataTable();
                if (parsedCsvData) renderCsvViewer();
            }
        };
        reader.readAsText(file);
    }

    /* ── Shared regex for safe names (sample names, custom genome name) ── */
    var SAFE_NAME_RE = /^[a-zA-Z0-9_-]+$/;

    function validateMatrixData() {
        if (!parsedMatrixData || !parsedMatrixData.data || parsedMatrixData.data.length === 0)
            return { valid: false, message: "Matrix is empty or could not be parsed." };

        var fields = parsedMatrixData.meta.fields;
        if (fields.length < 2)
            return { valid: false, message: "Matrix must have at least 2 columns (gene ID + 1 sample)." };

        /* Req 1 (matrix): need ≥ 3 columns (gene ID + ≥ 2 samples) */
        if (fields.length < 3)
            return { valid: false, message: "Matrix must have at least 3 columns (gene ID + 2 or more samples). DESeq2 requires ≥ 2 samples." };

        var sampleCols = fields.slice(1);
        var bad = 0;
        var negative = false;
        var hasMissing = false;
        var rows = parsedMatrixData.data.slice(0, 20);

        /* Req 4: unique gene IDs */
        var geneIdCol = fields[0];
        var geneIds = {};
        var duplicateGenes = [];
        for (var gi = 0; gi < parsedMatrixData.data.length; gi++) {
            var gid = (parsedMatrixData.data[gi][geneIdCol] || "").trim();
            if (gid in geneIds) {
                if (duplicateGenes.indexOf(gid) === -1) duplicateGenes.push(gid);
            } else {
                geneIds[gid] = true;
            }
        }
        if (duplicateGenes.length > 0)
            return { valid: false, message: "Duplicate gene IDs found: " + duplicateGenes.slice(0, 5).join(", ") + (duplicateGenes.length > 5 ? " (and " + (duplicateGenes.length - 5) + " more)" : "") + ". Each gene ID must be unique." };

        for (var ri = 0; ri < rows.length; ri++) {
            for (var ci = 0; ci < sampleCols.length; ci++) {
                var val = rows[ri][sampleCols[ci]];
                /* Req 5: no missing data */
                if (val === "" || val === undefined || val === null) {
                    hasMissing = true;
                } else {
                    var valStr = String(val).trim().toLowerCase();
                    if (valStr === "" || valStr === "na" || valStr === "nan" || valStr === "null") {
                        hasMissing = true;
                    } else {
                        var num = Number(val);
                        if (isNaN(num)) { bad++; }
                        else if (!Number.isInteger(num)) { bad++; }
                        else if (num < 0) { negative = true; }
                    }
                }
            }
        }
        if (hasMissing)
            return { valid: false, message: "Count matrix contains empty or missing values (NA/NaN/null). All cells must have integer counts." };
        if (bad > 0)
            return { valid: false, message: "Found non-integer values. Please upload raw integer counts (not TPM/FPKM)." };
        if (negative)
            return { valid: false, message: "Count matrix contains negative values. Only non-negative raw counts are accepted." };

        return { valid: true, message: "Valid matrix: " + parsedMatrixData.data.length + " genes \u00d7 " + sampleCols.length + " samples." };
    }

    function renderMatrixPreview() {
        if (!parsedMatrixData) return;
        matrixPreviewArea.style.display = "";
        var fields = parsedMatrixData.meta.fields;
        var rows = parsedMatrixData.data.slice(0, 5);
        var html = '<div class="csv-viewer-table-wrap"><table>';
        html += "<thead><tr>" + fields.map(function (f) { return "<th>" + escapeHtml(f) + "</th>"; }).join("") + "</tr></thead>";
        html += "<tbody>";
        rows.forEach(function (row) {
            html += "<tr>" + fields.map(function (f) { return "<td>" + escapeHtml(String(row[f] || "")) + "</td>"; }).join("") + "</tr>";
        });
        html += "</tbody></table></div>";
        if (parsedMatrixData.data.length > 5)
            html += '<p class="rna-text-xs rna-text-muted rna-mt-1">Showing 5 of ' + parsedMatrixData.data.length + ' rows.</p>';
        matrixPreviewArea.innerHTML = html;
    }

    async function uploadMatrixFile() {
        if (!matrixFile) return true;
        await ensureSubmission();
        var totalChunks = Math.ceil(matrixFile.size / CHUNK_SIZE);
        for (var c = 0; c < totalChunks; c++) {
            if (!(await uploadChunkWithRetry(matrixFile, c, totalChunks, "USER_COUNT_MATRIX")))
                return false;
        }
        return true;
    }

    /* ═══════════════════════════════════════════════════════════════════
       CHUNK UPLOAD ENGINE
       ═══════════════════════════════════════════════════════════════════ */

    async function ensureSubmission() {
        if (submissionId) return submissionId;
        var res = await fetch("/api/submission/create", {
            method: "POST",
            headers: { "X-CSRFToken": CSRF },
        });
        if (!res.ok) throw new Error("Failed to create submission");
        var data = await res.json();
        submissionId = data.submission_id;
        updateBannerInfo();
        return submissionId;
    }

    async function uploadChunkWithRetry(file, chunkIndex, totalChunks, fileRole, fileController) {
        for (var attempt = 0; attempt < MAX_RETRIES; attempt++) {
            try {
                var start = chunkIndex * CHUNK_SIZE;
                var end = Math.min(start + CHUNK_SIZE, file.size);
                var chunk = file.slice(start, end);
                var fd = new FormData();
                fd.append("file", chunk);
                fd.append("filename", file.name);
                fd.append("chunk_index", chunkIndex);
                fd.append("total_chunks", totalChunks);
                fd.append("submission_id", submissionId);
                fd.append("file_role", fileRole);

                // Use the per-file controller if provided, with a timeout fallback
                var controller = fileController || new AbortController();
                var timeoutId = setTimeout(function () { controller.abort(); }, UPLOAD_TIMEOUT);

                var res = await fetch("/api/upload/chunk", {
                    method: "POST",
                    headers: { "X-CSRFToken": CSRF },
                    body: fd,
                    signal: controller.signal,
                });

                clearTimeout(timeoutId);

                if (!res.ok) throw new Error("HTTP " + res.status);

                var data = await res.json();

                // Capture asset_id from final chunk response
                if (data.complete && data.asset_id) {
                    var tracker = uploadTracker[file.name];
                    if (tracker) tracker.assetId = data.asset_id;
                }

                return true;
            } catch (_err) {
                // If the file-level controller was aborted, don't retry
                if (fileController && fileController.signal.aborted) return false;
                if (attempt === MAX_RETRIES - 1) return false;
                await new Promise(function (r) { setTimeout(r, 1000 * (attempt + 1)); });
            }
        }
        return false;
    }

    /* ═══════════════════════════════════════════════════════════════════
       REFERENCE GENOME
       ═══════════════════════════════════════════════════════════════════ */

    function initGenome() {
        genomeSelect.addEventListener("change", function () {
            customGenomeSection.classList.toggle("visible", genomeSelect.value === "custom");
        });
        customGenomeFasta.addEventListener("change", function () {
            if (customGenomeFasta.files.length) {
                var file = customGenomeFasta.files[0];
                var name = file.name.toLowerCase();
                var validFasta = name.endsWith(".fa") || name.endsWith(".fasta") ||
                    name.endsWith(".fa.gz") || name.endsWith(".fasta.gz") ||
                    name.endsWith(".fa.zip") || name.endsWith(".fasta.zip");
                if (!validFasta) {
                    showToast("error", "Invalid File", "Only .fa, .fasta, .fa.gz, .fasta.gz, .fa.zip, or .fasta.zip files are accepted.");
                    customGenomeFasta.value = "";
                    customGenomeFiles.fasta = null;
                    fastaFileLabel.textContent = "No file chosen";
                    return;
                }
                customGenomeFiles.fasta = file;
                fastaFileLabel.textContent = file.name;
                if (file.size > FILE_SIZE_WARN) {
                    showToast("warning", "Large File Warning", file.name + " exceeds 10 GB and may take a long time to upload.");
                }
            }
        });
        customGenomeAnnotation.addEventListener("change", function () {
            if (customGenomeAnnotation.files.length) {
                customGenomeFiles.annotation = customGenomeAnnotation.files[0];
                annotationFileLabel.textContent = customGenomeAnnotation.files[0].name;
                if (customGenomeAnnotation.files[0].size > FILE_SIZE_WARN) {
                    showToast("warning", "Large File Warning", customGenomeAnnotation.files[0].name + " exceeds 10 GB and may take a long time to upload.");
                }
            }
        });
    }

    function isGenomeValid() {
        if (inputDataType === "matrix") return true;
        var val = genomeSelect.value;
        if (!val) return false;
        if (assayType === "small_rna") {
            if (val === "custom") return false;
            if (MIRBASE_GENOMES.indexOf(val) === -1) return false;
        }
        if (val === "custom") {
            if (!customGenomeName.value.trim()) return false;
            if (inputDataType === "fastq" && (!customGenomeFiles.fasta || !customGenomeFiles.annotation)) return false;
            if (inputDataType === "alignment" && !customGenomeFiles.annotation) return false;
        }
        return true;
    }

    async function uploadCustomGenomeFiles() {
        if (genomeSelect.value !== "custom") return true;
        await ensureSubmission();
        if (customGenomeFiles.fasta) {
            var tc = Math.ceil(customGenomeFiles.fasta.size / CHUNK_SIZE);
            for (var c = 0; c < tc; c++) {
                if (!(await uploadChunkWithRetry(customGenomeFiles.fasta, c, tc, "CUSTOM_GENOME_FASTA")))
                    return false;
            }
        }
        if (customGenomeFiles.annotation) {
            var tc2 = Math.ceil(customGenomeFiles.annotation.size / CHUNK_SIZE);
            for (var c2 = 0; c2 < tc2; c2++) {
                if (!(await uploadChunkWithRetry(customGenomeFiles.annotation, c2, tc2, "CUSTOM_GENOME_ANNOTATION")))
                    return false;
            }
        }
        return true;
    }

    /* ═══════════════════════════════════════════════════════════════════
       METADATA TOGGLE
       ═══════════════════════════════════════════════════════════════════ */

    function initMetadataToggle() {
        var cards = metaToggle.querySelectorAll(".library-type-card");
        cards.forEach(function (card) {
            card.addEventListener("click", function () {
                var radio = card.querySelector('input[type="radio"]');
                if (!radio) return;
                var newMode = radio.value;
                if (newMode === metadataMode) return; // no change
                radio.checked = true;
                cards.forEach(function (c) { c.classList.remove("selected"); });
                card.classList.add("selected");

                // Reset data from previous mode to prevent conflicts
                if (metadataMode === "upload") {
                    // Leaving CSV mode: clear CSV data
                    csvFile = null;
                    parsedCsvData = null;
                    if (csvFileName) { csvFileName.style.display = "none"; csvFileName.innerHTML = ""; }
                    if (csvInput) csvInput.value = "";
                    if (csvViewerTable) csvViewerTable.innerHTML = "";
                } else if (metadataMode === "manual") {
                    // Leaving manual mode: reset to defaults
                    manualColumns = ["condition"];
                    columnSelectableValues = { condition: [] };
                    if (metadataBody) metadataBody.innerHTML = "";
                    renderColumnChips();
                    renderConditionChips();
                    updateConditionTargetDropdown();
                }
                // Reset shared state
                columnMapping = { primary_group: "", batch_effect: "", covariates: [] };
                contrasts = [];
                if (primaryGroupSelect) primaryGroupSelect.value = "";
                if (batchEffectSelect) batchEffectSelect.value = "";
                if (contrastSection) contrastSection.style.display = "none";
                if (contrastList) contrastList.innerHTML = "";
                if (columnValidationMsg) columnValidationMsg.style.display = "none";

                metadataMode = newMode;
                syncMetadataView();
            });
        });
    }

    /** Single source of truth for Step 4 right-panel visibility.
     *  Called on mode switch, CSV load, and manual table rebuild. */
    function syncMetadataView() {
        var isUpload = metadataMode === "upload";
        var isManual = metadataMode === "manual";
        var hasCsvData = !!(parsedCsvData && parsedCsvData.data.length > 0);
        var hasSamples = extractSampleNames().length > 0;

        /* Left panel: only one mode's controls visible */
        metaUploadPanel.style.display = isUpload ? "" : "none";
        metaManualPanel.style.display = isManual ? "" : "none";

        /* Right panel: hide everything first */
        csvViewerSection.style.display = "none";
        manualMetadataPanel.style.display = "none";
        if (metaPlaceholderCard) metaPlaceholderCard.style.display = "none";

        if (isUpload) {
            if (hasCsvData) {
                csvViewerSection.style.display = "";
                showRolesContrastRow();
            } else {
                if (metaPlaceholderCard) metaPlaceholderCard.style.display = "";
                hideRolesContrastRow();
            }
        } else if (isManual) {
            manualMetadataPanel.style.display = "";
            rebuildMetadataTable();
            if (hasSamples) {
                showRolesContrastRow();
            } else {
                if (metaPlaceholderCard) metaPlaceholderCard.style.display = "none";
                hideRolesContrastRow();
            }
        }
    }

    /* ═══════════════════════════════════════════════════════════════════
       CSV METADATA UPLOAD & PREVIEW
       ═══════════════════════════════════════════════════════════════════ */

    function initCsvUpload() {
        csvDropZone.addEventListener("click", function () { csvInput.click(); });
        csvDropZone.addEventListener("keydown", function (e) {
            if (e.key === "Enter" || e.key === " ") { e.preventDefault(); csvInput.click(); }
        });
        csvDropZone.addEventListener("dragover", function (e) {
            e.preventDefault(); csvDropZone.classList.add("drag-over");
        });
        csvDropZone.addEventListener("dragleave", function () { csvDropZone.classList.remove("drag-over"); });
        csvDropZone.addEventListener("drop", function (e) {
            e.preventDefault(); csvDropZone.classList.remove("drag-over");
            if (e.dataTransfer.files.length) setCsvFile(e.dataTransfer.files[0]);
        });
        csvInput.addEventListener("change", function () {
            if (csvInput.files.length) setCsvFile(csvInput.files[0]);
            csvInput.value = "";
        });

        var downloadTemplateBtn = $("download-csv-template");
        if (downloadTemplateBtn) {
            downloadTemplateBtn.addEventListener("click", function () {
                var samples = extractSampleNames();
                var rows = [["sample", "condition"]];
                for (var i = 0; i < samples.length; i++) {
                    rows.push([samples[i], ""]);
                }
                var csvContent = rows.map(function (r) { return r.join(","); }).join("\n");
                var blob = new Blob([csvContent], { type: "text/csv;charset=utf-8;" });
                var url = URL.createObjectURL(blob);
                var a = document.createElement("a");
                a.href = url;
                a.download = "metadata_template.csv";
                document.body.appendChild(a);
                a.click();
                document.body.removeChild(a);
                URL.revokeObjectURL(url);
            });
        }
    }

    function clearCsvFile() {
        csvFile = null;
        parsedCsvData = null;
        csvFileName.style.display = "none";
        csvInput.value = "";
        if (csvViewerTable) csvViewerTable.innerHTML = "";
        syncMetadataView();
    }

    function setCsvFile(file) {
        /* Auto-clear previous CSV (UI + best-effort server deletion) */
        if (csvFile) {
            var oldName = csvFile.name;
            var oldTracker = uploadTracker[oldName];
            if (oldTracker && oldTracker.assetId) {
                fetch("/api/files/" + oldTracker.assetId + "/", {
                    method: "DELETE",
                    headers: { "X-CSRFToken": CSRF },
                }).catch(function () { /* best-effort */ });
                delete uploadTracker[oldName];
            }
            csvFile = null;
            parsedCsvData = null;
            csvInput.value = "";
            if (csvViewerTable) csvViewerTable.innerHTML = "";
        }

        csvFile = file;
        csvFileName.style.display = "";
        csvFileName.innerHTML = '<i class="bi bi-file-earmark-check"></i> <span>' + escapeHtml(file.name) + '</span>' +
            ' <button type="button" class="btn-csv-remove" id="csv-remove-btn" title="Remove CSV">' +
            '<svg class="csv-trash-icon" xmlns="http://www.w3.org/2000/svg" width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">' +
            '<polyline points="3 6 5 6 21 6"/><path d="M19 6l-1 14a2 2 0 0 1-2 2H8a2 2 0 0 1-2-2L5 6"/>' +
            '<path d="M10 11v6"/><path d="M14 11v6"/><path d="M9 6V4a1 1 0 0 1 1-1h4a1 1 0 0 1 1 1v2"/>' +
            '</svg></button>';
        var removeBtn = $("csv-remove-btn");
        if (removeBtn) removeBtn.addEventListener("click", clearCsvFile);
        showParseOverlay(csvDropZone);

        var reader = new FileReader();
        reader.onload = function (e) {
            removeParseOverlay(csvDropZone);
            var delimiter = file.name.toLowerCase().endsWith(".tsv") ? "\t" : ",";
            parsedCsvData = Papa.parse(e.target.result, {
                header: true, skipEmptyLines: true, delimiter: delimiter,
            });
            if (parsedCsvData.errors.length > 0) {
                showToast("error", "CSV Parse Error", parsedCsvData.errors[0].message);
                return;
            }
            /* Token 3: Auto-strip FASTQ-related extensions from sample column values */
            var sampleCol = parsedCsvData.meta.fields.find(function (f) {
                return f.toLowerCase() === "sample";
            });

            if (!sampleCol) {
                showToast("warning", "Missing 'sample' Column",
                    "Your CSV does not have a column named 'sample'. " +
                    "The first column must be named 'sample' to match uploaded file names.");
            } else {
                parsedCsvData.data.forEach(function (row) {
                    if (row[sampleCol] && /\.(fastq|fq)(\.gz)?$/i.test(row[sampleCol])) {
                        row[sampleCol] = row[sampleCol].replace(/\.(fastq|fq)(\.gz)?$/i, "");
                    }
                });
            }
            renderCsvViewer();
            updateColumnMappingOptions();
            syncMetadataView();
        };
        reader.readAsText(file);
    }

    function renderCsvPreview() {
        /* Preview now handled by the right-panel CSV viewer table */
    }

    function getFilteredCsvRows() {
        if (!parsedCsvData) return [];
        var sampleNames = extractSampleNames();
        if (sampleNames.length === 0) return parsedCsvData.data;
        var sampleCol = parsedCsvData.meta.fields.find(function (f) {
            return f.toLowerCase() === "sample";
        });
        if (!sampleCol) return parsedCsvData.data;
        return parsedCsvData.data.filter(function (row) {
            return sampleNames.indexOf(row[sampleCol]) !== -1;
        });
    }

    function renderCsvViewer() {
        if (!parsedCsvData) return;
        var sampleNames = extractSampleNames();
        var filtered = getFilteredCsvRows();
        var total = parsedCsvData.data.length;
        var matched = filtered.length;

        /* Token 2: Show validation error when uploaded files exist but 0 CSV rows match */
        if (sampleNames.length > 0 && matched === 0) {
            showToast("error", "No Matches Found",
                "None of the sample names in your CSV matched the uploaded file names. " +
                "Please verify that the 'sample' column values match your file names (without extensions).");
        }

        if (sampleNames.length > 0) {
            csvViewerInfo.innerHTML =
                '<span class="matched-badge"><i class="bi bi-check-circle"></i> ' + matched + ' matched</span>' +
                '<span class="total-badge">' + total + ' total rows</span>' +
                (matched < total ? '<span class="rna-text-xs rna-text-muted">' + (total - matched) + ' unmatched</span>' : '');
        } else {
            csvViewerInfo.innerHTML =
                '<span class="total-badge">' + total + ' rows loaded</span>' +
                '<span class="rna-text-xs rna-text-muted">Upload files to match samples</span>';
        }

        /* Build full data table in right panel */
        var fields = parsedCsvData.meta.fields;
        var displayRows = filtered.length > 0 ? filtered : parsedCsvData.data;
        var html = '<table>';
        html += '<thead><tr>' + fields.map(function (f) { return '<th>' + escapeHtml(f) + '</th>'; }).join('') + '</tr></thead>';
        html += '<tbody>';
        displayRows.forEach(function (row) {
            html += '<tr>' + fields.map(function (f) { return '<td>' + escapeHtml(String(row[f] || '')) + '</td>'; }).join('') + '</tr>';
        });
        html += '</tbody></table>';
        if (csvViewerTable) csvViewerTable.innerHTML = html;
    }

    async function uploadCsvFile() {
        if (!csvFile) return true;
        await ensureSubmission();
        var tc = Math.ceil(csvFile.size / CHUNK_SIZE);
        for (var c = 0; c < tc; c++) {
            if (!(await uploadChunkWithRetry(csvFile, c, tc, "METADATA_CSV")))
                return false;
        }
        return true;
    }

    /* ═══════════════════════════════════════════════════════════════════
       MANUAL METADATA BUILDER
       ═══════════════════════════════════════════════════════════════════ */

    function initManualMetadata() {
        addColumnBtn.addEventListener("click", function () {
            var name = columnNameInput.value.trim().toLowerCase().replace(/\s+/g, "_");
            if (!name) return;
            if (manualColumns.indexOf(name) !== -1) {
                showToast("warning", "Duplicate Column", 'Column "' + name + '" already exists.');
                return;
            }
            manualColumns.push(name);
            columnSelectableValues[name] = [];
            columnNameInput.value = "";
            renderColumnChips();
            updateConditionTargetDropdown();
            rebuildMetadataTable();
            updateColumnMappingOptions();
        });
        columnNameInput.addEventListener("keydown", function (e) {
            if (e.key === "Enter") { e.preventDefault(); addColumnBtn.click(); }
        });

        addConditionBtn.addEventListener("click", function () {
            var col = conditionTargetColumn.value;
            var val = conditionValueInput.value.trim();
            if (!col || !val) return;
            if (!columnSelectableValues[col]) columnSelectableValues[col] = [];
            if (columnSelectableValues[col].indexOf(val) !== -1) {
                showToast("warning", "Duplicate Value", 'Value "' + val + '" already exists for "' + col + '".');
                return;
            }
            columnSelectableValues[col].push(val);
            conditionValueInput.value = "";
            renderConditionChips();
            rebuildMetadataTable();
        });
        conditionValueInput.addEventListener("keydown", function (e) {
            if (e.key === "Enter") { e.preventDefault(); addConditionBtn.click(); }
        });

        renderColumnChips();
        updateConditionTargetDropdown();
    }

    function renderColumnChips() {
        columnChips.innerHTML = manualColumns.map(function (col) {
            var isProtected = col === "condition";
            return '<span class="group-chip ' + (isProtected ? "protected" : "") + '">' +
                escapeHtml(col) +
                (isProtected ? "" : ' <span class="remove-group" data-col="' + escapeHtml(col) + '">&times;</span>') +
                '</span>';
        }).join("");

        columnChips.querySelectorAll(".remove-group").forEach(function (btn) {
            btn.addEventListener("click", function () {
                var col = btn.dataset.col;
                manualColumns = manualColumns.filter(function (c) { return c !== col; });
                delete columnSelectableValues[col];
                renderColumnChips();
                updateConditionTargetDropdown();
                rebuildMetadataTable();
                updateColumnMappingOptions();
            });
        });
    }

    function renderConditionChips() {
        var html = "";
        for (var col in columnSelectableValues) {
            if (!columnSelectableValues.hasOwnProperty(col)) continue;
            columnSelectableValues[col].forEach(function (val) {
                html += '<span class="group-chip condition-chip">' +
                    '<span class="chip-col-label">' + escapeHtml(col) + ':</span> ' + escapeHtml(val) +
                    ' <span class="remove-group" data-col="' + escapeHtml(col) + '" data-val="' + escapeHtml(val) + '">&times;</span>' +
                    '</span>';
            });
        }
        conditionChips.innerHTML = html;
        conditionChips.querySelectorAll(".remove-group").forEach(function (btn) {
            btn.addEventListener("click", function () {
                var col = btn.dataset.col;
                var val = btn.dataset.val;
                if (columnSelectableValues[col]) {
                    columnSelectableValues[col] = columnSelectableValues[col].filter(function (v) { return v !== val; });
                }
                renderConditionChips();
                rebuildMetadataTable();
                var pgCol = primaryGroupSelect.value;
                if (pgCol) asyncCheckContrastVisibility(pgCol);
            });
        });
    }

    function updateConditionTargetDropdown() {
        conditionTargetColumn.innerHTML = manualColumns.map(function (col) {
            return '<option value="' + escapeHtml(col) + '">' + escapeHtml(col) + '</option>';
        }).join("");
    }

    function rebuildMetadataTable() {
        var samples = extractSampleNames();

        metadataHeaderRow.innerHTML = "<th>Sample Name</th>" +
            manualColumns.map(function (col) { return "<th>" + escapeHtml(col) + "</th>"; }).join("");

        if (samples.length === 0) {
            metadataBody.innerHTML = "";
            noFilesHint.style.display = "";
            return;
        }
        noFilesHint.style.display = "none";

        // Preserve existing cell values
        var existing = {};
        metadataBody.querySelectorAll("tr").forEach(function (tr) {
            var sn = tr.dataset.sample;
            if (!sn) return;
            tr.querySelectorAll("[data-col]").forEach(function (el) {
                if (!existing[sn]) existing[sn] = {};
                existing[sn][el.dataset.col] = el.value;
            });
        });

        metadataBody.innerHTML = samples.map(function (sample) {
            var row = '<tr data-sample="' + escapeHtml(sample) + '"><td>' + escapeHtml(sample) + '</td>';
            manualColumns.forEach(function (col) {
                var prev = (existing[sample] && existing[sample][col]) || "";
                var vals = columnSelectableValues[col] || [];
                if (vals.length > 0) {
                    row += '<td><select class="rna-input rna-select meta-cell-input" data-col="' + escapeHtml(col) + '">' +
                        '<option value="">--</option>' +
                        vals.map(function (v) {
                            return '<option value="' + escapeHtml(v) + '"' + (v === prev ? ' selected' : '') + '>' + escapeHtml(v) + '</option>';
                        }).join("") +
                        '</select></td>';
                } else {
                    row += '<td><input type="text" class="rna-input meta-cell-input" data-col="' + escapeHtml(col) + '" value="' + escapeHtml(prev) + '"></td>';
                }
            });
            row += '</tr>';
            return row;
        }).join("");

        // Attach async change listeners for metadata cells
        metadataBody.querySelectorAll(".meta-cell-input").forEach(function (el) {
            el.addEventListener("change", onManualMetadataChange);
            if (el.tagName === "INPUT") {
                el.addEventListener("input", onManualMetadataChange);
            }
        });

        showRolesContrastRow();
        updateColumnMappingOptions();
    }

    /* Async handler: re-evaluate contrast section when manual metadata changes */
    function onManualMetadataChange() {
        updateColumnMappingOptions();
        var col = primaryGroupSelect.value;
        if (col) asyncCheckContrastVisibility(col);
    }

    /* ═══════════════════════════════════════════════════════════════════
       SAMPLE NAME EXTRACTION
       ═══════════════════════════════════════════════════════════════════ */

    function extractSampleNames() {
        if (inputDataType === "fastq") {
            var names = {};
            selectedFiles.forEach(function (f) {
                var stem;
                if (libraryType === "paired") {
                    var m = f.name.match(/^(.+?)(?:_R[12]|_[12])\.(?:fq|fastq)\.gz$/i);
                    stem = m ? m[1] : f.name.replace(/\.(?:fq|fastq)\.gz$/i, "");
                } else {
                    stem = f.name.replace(/\.(?:fq|fastq)\.gz$/i, "");
                }
                names[stem] = true;
            });
            return Object.keys(names);
        }
        if (inputDataType === "alignment") {
            return selectedBamFiles.map(function (f) {
                return f.name.replace(/\.(?:bam|cram)$/i, "");
            });
        }
        if (inputDataType === "matrix" && parsedMatrixData) {
            return parsedMatrixData.meta.fields.slice(1);
        }
        return [];
    }

    /* ═══════════════════════════════════════════════════════════════════
       COLUMN MAPPING (Roles)
       ═══════════════════════════════════════════════════════════════════ */

    function showRolesContrastRow() {
        columnMappingSection.style.display = "";
        updateColumnMappingOptions();
    }

    function hideRolesContrastRow() {
        columnMappingSection.style.display = "none";
        contrastSection.style.display = "none";
    }

    function updateColumnMappingOptions() {
        var columns = getMetadataColumns();
        var pgVal = primaryGroupSelect.value;
        var beVal = batchEffectSelect.value;

        primaryGroupSelect.innerHTML = '<option value="">-- Select primary group column --</option>' +
            columns.map(function (c) {
                return '<option value="' + escapeHtml(c) + '"' + (c === pgVal ? ' selected' : '') + '>' + escapeHtml(c) + '</option>';
            }).join("");

        var exclude = [primaryGroupSelect.value].filter(Boolean);
        batchEffectSelect.innerHTML = '<option value="">None</option>' +
            columns.filter(function (c) { return exclude.indexOf(c) === -1; }).map(function (c) {
                return '<option value="' + escapeHtml(c) + '"' + (c === beVal ? ' selected' : '') + '>' + escapeHtml(c) + '</option>';
            }).join("");

        var selectedCovs = [].slice.call(covariatesList.querySelectorAll("input:checked")).map(function (x) { return x.value; });
        var excludeAll = [primaryGroupSelect.value, batchEffectSelect.value].filter(Boolean);
        covariatesList.innerHTML = columns.filter(function (c) { return excludeAll.indexOf(c) === -1; }).map(function (c) {
            return '<label class="covariate-check-label"><input type="checkbox" value="' + escapeHtml(c) + '"' +
                (selectedCovs.indexOf(c) !== -1 ? ' checked' : '') + '> ' + escapeHtml(c) + '</label>';
        }).join("");
    }

    function getMetadataColumns() {
        if (metadataMode === "upload" && parsedCsvData) {
            return parsedCsvData.meta.fields.filter(function (f) { return f.toLowerCase() !== "sample"; });
        }
        if (metadataMode === "manual") return manualColumns.slice();
        return [];
    }

    function initColumnMapping() {
        primaryGroupSelect.addEventListener("change", function () {
            columnMapping.primary_group = primaryGroupSelect.value;
            onPrimaryGroupChange();
            updateColumnMappingOptions();
            validateColumnSelection();
        });
        batchEffectSelect.addEventListener("change", function () {
            columnMapping.batch_effect = batchEffectSelect.value;
            updateColumnMappingOptions();
        });
    }

    function onPrimaryGroupChange() {
        var col = primaryGroupSelect.value;
        if (!col) { contrastSection.style.display = "none"; return; }
        asyncCheckContrastVisibility(col);
    }

    /* ─── Async Contrast Visibility Check ─────────────────── */
    let _contrastCheckPending = null;

    function asyncCheckContrastVisibility(col) {
        if (_contrastCheckPending) clearTimeout(_contrastCheckPending);
        _contrastCheckPending = setTimeout(function () {
            _contrastCheckPending = null;
            var values = getUniqueColumnValues(col);
            if (values.length > 2) {
                contrastSection.style.display = "";
                if (contrasts.length === 0 && values.length >= 2)
                    contrasts = [[values[1] || "", values[0] || ""]];
                renderContrastRows(values);
            } else {
                contrastSection.style.display = "none";
                contrasts = [];
            }
        }, 150);
    }

    function getUniqueColumnValues(colName) {
        var seen = {};
        var result = [];
        if (metadataMode === "upload" && parsedCsvData) {
            getFilteredCsvRows().forEach(function (row) {
                if (row[colName] && !seen[row[colName]]) { seen[row[colName]] = true; result.push(row[colName]); }
            });
        } else if (metadataMode === "manual") {
            (columnSelectableValues[colName] || []).forEach(function (v) {
                if (!seen[v]) { seen[v] = true; result.push(v); }
            });
            metadataBody.querySelectorAll('[data-col="' + colName + '"]').forEach(function (el) {
                if (el.value && !seen[el.value]) { seen[el.value] = true; result.push(el.value); }
            });
        }
        return result;
    }

    function validateColumnSelection() {
        var col = primaryGroupSelect.value;
        if (!col) { columnValidationMsg.style.display = "none"; return; }
        var values = getUniqueColumnValues(col);
        columnValidationMsg.style.display = "";
        if (values.length < 2) {
            columnValidationMsg.className = "validation-msg error rna-mt-2";
            columnValidationMsg.textContent = '"' + col + '" has fewer than 2 distinct values. DESeq2 needs at least 2 groups.';
        } else {
            columnValidationMsg.className = "validation-msg success rna-mt-2";
            columnValidationMsg.textContent = values.length + " groups: " + values.join(", ");
        }
    }

    /* ═══════════════════════════════════════════════════════════════════
       CONTRAST BUILDER
       ═══════════════════════════════════════════════════════════════════ */

    function initContrasts() {
        addContrastBtn.addEventListener("click", function () {
            contrasts.push(["", ""]);
            renderContrastRows(getUniqueColumnValues(primaryGroupSelect.value));
        });
    }

    function renderContrastRows(values) {
        if (!values) values = getUniqueColumnValues(primaryGroupSelect.value);
        var opts = values.map(function (v) { return '<option value="' + escapeHtml(v) + '">' + escapeHtml(v) + '</option>'; }).join("");

        contrastList.innerHTML = contrasts.map(function (pair, i) {
            return '<div class="contrast-row">' +
                '<span class="contrast-label">Target:</span>' +
                '<select class="rna-input rna-select contrast-select contrast-target" data-idx="' + i + '"><option value="">--</option>' + opts + '</select>' +
                '<span class="contrast-vs">vs</span>' +
                '<span class="contrast-label">Ref:</span>' +
                '<select class="rna-input rna-select contrast-select contrast-ref" data-idx="' + i + '"><option value="">--</option>' + opts + '</select>' +
                '<button type="button" class="btn-rna btn-rna-danger btn-rna-sm contrast-remove" data-idx="' + i + '"><i class="bi bi-trash"></i></button>' +
                '</div>';
        }).join("");

        contrastList.querySelectorAll(".contrast-target").forEach(function (sel) {
            var idx = parseInt(sel.dataset.idx);
            if (contrasts[idx]) sel.value = contrasts[idx][0];
            sel.addEventListener("change", function () { contrasts[parseInt(sel.dataset.idx)][0] = sel.value; });
        });
        contrastList.querySelectorAll(".contrast-ref").forEach(function (sel) {
            var idx = parseInt(sel.dataset.idx);
            if (contrasts[idx]) sel.value = contrasts[idx][1];
            sel.addEventListener("change", function () { contrasts[parseInt(sel.dataset.idx)][1] = sel.value; });
        });
        contrastList.querySelectorAll(".contrast-remove").forEach(function (btn) {
            btn.addEventListener("click", function () {
                contrasts.splice(parseInt(btn.dataset.idx), 1);
                renderContrastRows(values);
            });
        });
    }

    /* ═══════════════════════════════════════════════════════════════════
       THRESHOLDS
       ═══════════════════════════════════════════════════════════════════ */

    function initThresholds() {
        var update = function () { updateThresholdPreview(); };
        adjPvalue.addEventListener("input", update);
        minLog2fc.addEventListener("input", update);
        maxLog2fc.addEventListener("input", update);
        update();
    }

    function updateThresholdPreview() {
        var fc = Math.max(
            Math.abs(parseFloat(minLog2fc.value) || 1),
            Math.abs(parseFloat(maxLog2fc.value) || 1)
        );
        fcPreview.textContent = fc.toFixed(1);
        pvalPreview.textContent = parseFloat(adjPvalue.value) || 0.05;
    }

    /* ═══════════════════════════════════════════════════════════════════
       VALIDATION (Step 5 Summary)
       ═══════════════════════════════════════════════════════════════════ */

    /* ── Track-Specific Validators ── */

    function validateChipSeqMetadata() {
        var errs = [];
        var samples = getActiveMetadataSamples();
        if (samples.length === 0) return errs;
        var pg = primaryGroupSelect.value;
        if (!pg) return errs;
        var hasControl = false, hasTreatment = false;
        for (var i = 0; i < samples.length; i++) {
            var val = (samples[i][pg] || "").trim().toLowerCase();
            if (val === "input") hasControl = true;
            else if (val) hasTreatment = true;
        }
        if (!hasControl)
            errs.push("ChIP-seq requires at least one sample labeled 'input' (case-insensitive) as control.");
        if (!hasTreatment)
            errs.push("ChIP-seq requires at least one non-input (treatment/IP) sample.");
        return errs;
    }

    function validateBatchColumn() {
        var errs = [];
        var batchCol = batchEffectSelect ? batchEffectSelect.value : "";
        if (!batchCol) return errs;
        var samples = getActiveMetadataSamples();
        if (samples.length === 0) return errs;
        // Check column exists in first sample
        var firstRow = samples[0];
        var available = Object.keys(firstRow).map(function (k) { return k.trim().toLowerCase(); });
        if (available.indexOf(batchCol.trim().toLowerCase()) === -1) {
            errs.push("Batch correction column '" + batchCol + "' not found in metadata.");
            return errs;
        }
        // Check each batch has >= 2 samples (ComBat-seq requirement)
        var batchCounts = {};
        for (var i = 0; i < samples.length; i++) {
            var val = (samples[i][batchCol] || "").toString().trim();
            if (val) batchCounts[val] = (batchCounts[val] || 0) + 1;
        }
        var singletons = Object.keys(batchCounts).filter(function (b) { return batchCounts[b] < 2; });
        if (singletons.length > 0)
            errs.push("ComBat-seq requires \u2265 2 samples per batch. Singleton batch(es): " + singletons.join(", ") + ". Merge them or remove the batch column.");
        return errs;
    }

    function getActiveMetadataSamples() {
        if (metadataMode === "upload" && parsedCsvData) {
            var filtered = getFilteredCsvRows();
            return filtered.length > 0 ? filtered : parsedCsvData.data;
        }
        return getMetadataRows();
    }

    /**
     * Cross-cutting pre-submission validation.
     * Runs all track-specific and dependency checks before allowing POST.
     * Returns an array of error strings (empty = pass).
     */
    function validatePreSubmission() {
        var errs = [];

        /* 1. Matrix: content sanity (already checked in step 2, double-check) */
        if (inputDataType === "matrix" && parsedMatrixData) {
            var mv = validateMatrixData();
            if (!mv.valid) errs.push(mv.message);
        }

        /* 2. Paired-end file matching */
        if (inputDataType === "fastq" && libraryType === "paired") {
            var allFiles = selectedFiles.concat(uploadedFiles.map(function (n) { return { name: n }; }));
            var r1 = [], r2 = [], unmatched = [];
            allFiles.forEach(function (f) {
                if (/_R1[._]|_1\.(fq|fastq)\.gz$/i.test(f.name)) r1.push(f.name);
                else if (/_R2[._]|_2\.(fq|fastq)\.gz$/i.test(f.name)) r2.push(f.name);
                else unmatched.push(f.name);
            });
            if (unmatched.length > 0)
                errs.push(unmatched.length + " file(s) don't match _R1/_R2 naming: " + unmatched.join(", "));
            if (r1.length !== r2.length)
                errs.push("Unequal pairs: " + r1.length + " R1 and " + r2.length + " R2 files.");
        }

        /* 3. Small RNA genome restriction + library type conflict */
        if (inputDataType === "fastq" && assayType === "small_rna") {
            if (libraryType === "paired")
                errs.push("Small RNA / miRNA requires Single-End reads. Paired-End is not supported.");
            var gen = genomeSelect.value;
            if (gen === "custom")
                errs.push("Custom genomes are not supported for Small RNA / miRNA.");
            else if (gen && MIRBASE_GENOMES.indexOf(gen) === -1)
                errs.push("Genome '" + gen + "' does not have a miRBase index.");
        }

        /* 4. ChIP-seq input/control split */
        if (inputDataType === "fastq" && assayType === "chip_seq") {
            var chipErrs = validateChipSeqMetadata();
            chipErrs.forEach(function (e) { errs.push(e); });
        }

        /* 5. Custom genome files present + sanitized name */
        if (genomeSelect.value === "custom" && inputDataType !== "matrix") {
            var cgName = customGenomeName.value.trim();
            if (!cgName)
                errs.push("Custom genome name is required.");
            else if (!SAFE_NAME_RE.test(cgName))
                errs.push("Custom genome name must contain only letters, digits, hyphens, or underscores.");
            if (inputDataType === "fastq") {
                if (!customGenomeFiles.fasta) errs.push("Custom genome requires a FASTA file.");
                if (!customGenomeFiles.annotation) errs.push("Custom genome requires a GTF/GFF annotation file.");
            } else if (inputDataType === "alignment") {
                if (!customGenomeFiles.annotation) errs.push("Custom genome requires a GTF/GFF annotation file.");
            }
        }

        /* 6. Batch column validation */
        var batchErrs = validateBatchColumn();
        batchErrs.forEach(function (e) { errs.push(e); });

        /* 7. Metadata present and mapped */
        if (!isMetadataValid()) errs.push("Metadata is not configured.");
        if (isMetadataValid() && !isMappingValid()) errs.push("Primary group column not assigned.");

        /* 8. Contrast level validity (pre-submission double-check) */
        if (isMetadataValid() && primaryGroupSelect.value && contrasts.length > 0) {
            var psSamples = getActiveMetadataSamples();
            var psPg = primaryGroupSelect.value;
            var psGroupVals = {};
            for (var psi = 0; psi < psSamples.length; psi++) {
                var psv = (psSamples[psi][psPg] || "").trim();
                if (psv) psGroupVals[psv] = true;
            }
            for (var pci = 0; pci < contrasts.length; pci++) {
                var pcT = (contrasts[pci][0] || "").trim();
                var pcR = (contrasts[pci][1] || "").trim();
                if (pcT && !(pcT in psGroupVals))
                    errs.push("Contrast target '" + pcT + "' does not exist in the '" + psPg + "' column.");
                if (pcR && !(pcR in psGroupVals))
                    errs.push("Contrast reference '" + pcR + "' does not exist in the '" + psPg + "' column.");
            }
        }

        /* 9. Minimum sample count (pre-submission catch-all) */
        if (inputDataType === "fastq") {
            var psFileCount = selectedFiles.length + uploadedFiles.length;
            var psMinSamples = (libraryType === "paired") ? Math.floor(psFileCount / 2) : psFileCount;
            if (psMinSamples > 0 && psMinSamples < 2)
                errs.push("At least 2 samples are required for differential expression analysis.");
        } else if (inputDataType === "alignment") {
            var psBamCount = selectedBamFiles.length + uploadedBamFiles.length;
            if (psBamCount > 0 && psBamCount < 2)
                errs.push("At least 2 BAM/CRAM files (samples) are required for differential analysis.");
        } else if (inputDataType === "matrix" && parsedMatrixData) {
            if (parsedMatrixData.meta.fields.length < 3)
                errs.push("Matrix must have at least 3 columns (gene ID + 2 or more samples).");
        }

        return errs;
    }

    function validateAll() {
        var setValid = function (el, valid) {
            if (!el) return;
            el.classList.remove("valid", "invalid");
            el.classList.add(valid ? "valid" : "invalid");
            var icon = el.querySelector("i");
            if (icon) icon.className = "bi " + (valid ? "bi-check-circle-fill" : "bi-x-circle");
        };

        var nameValid = !!submissionNameInput.value.trim();
        setValid(valName, nameValid);

        var libValid = inputDataType === "matrix" || !!libraryType;
        setValid(valLibrary, libValid);
        if (valLibrary) valLibrary.style.display = inputDataType === "matrix" ? "none" : "";

        var filesValid = false;
        if (inputDataType === "fastq") filesValid = selectedFiles.length > 0 || uploadedFiles.length > 0;
        else if (inputDataType === "alignment") filesValid = selectedBamFiles.length > 0 || uploadedBamFiles.length > 0;
        else if (inputDataType === "matrix") filesValid = !!parsedMatrixData;
        setValid(valFiles, filesValid);

        var genValid = isGenomeValid();
        setValid(valGenome, genValid);
        if (valGenome) valGenome.style.display = inputDataType === "matrix" ? "none" : "";

        var metaValid = isMetadataValid();
        setValid(valMetadata, metaValid);

        var mapValid = isMappingValid();
        setValid(valMapping, mapValid);

        submitBtn.disabled = !(nameValid && libValid && filesValid && genValid && metaValid && mapValid);
    }

    function isMetadataValid() {
        if (metadataMode === "upload") {
            return !!(parsedCsvData && parsedCsvData.data.length > 0);
        }
        var rows = getMetadataRows();
        return rows.length > 0 && rows.some(function (r) {
            return Object.keys(r).some(function (k) { return k !== "sample" && r[k]; });
        });
    }

    function isMappingValid() {
        return !!primaryGroupSelect.value;
    }

    function getMetadataRows() {
        var rows = [];
        metadataBody.querySelectorAll("tr").forEach(function (tr) {
            var sample = tr.dataset.sample;
            if (!sample) return;
            var row = { sample: sample };
            tr.querySelectorAll("[data-col]").forEach(function (el) {
                row[el.dataset.col] = el.value || "";
            });
            rows.push(row);
        });
        return rows;
    }

    /* ═══════════════════════════════════════════════════════════════════
       METADATA PAYLOAD BUILDER
       ═══════════════════════════════════════════════════════════════════ */

    function buildMetadataPayload() {
        var samples;
        if (metadataMode === "upload" && parsedCsvData) {
            samples = getFilteredCsvRows();
            if (samples.length === 0) samples = parsedCsvData.data;
        } else {
            samples = getMetadataRows();
        }

        var covariates = [].slice.call(covariatesList.querySelectorAll("input:checked"))
            .map(function (x) { return x.value; });

        var mapping = { primary_group: primaryGroupSelect.value };
        if (batchEffectSelect.value) mapping.batch_effect = batchEffectSelect.value;
        if (covariates.length > 0) mapping.additional_covariates = covariates;

        return {
            samples: samples,
            column_mapping: mapping,
            contrasts: contrasts.filter(function (c) { return c[0] && c[1]; }),
            quant_level: quantLevel.value,
        };
    }

    /* ═══════════════════════════════════════════════════════════════════
       SUBMIT PIPELINE
       ═══════════════════════════════════════════════════════════════════ */

    /* ─── Upload Modal Helpers ──────────────────────────────────── */

    function showUploadModal() {
        if (!uploadModalBackdrop) return;
        uploadModalBackdrop.classList.add("open");
    }

    function hideUploadModal() {
        if (!uploadModalBackdrop) return;
        uploadModalBackdrop.classList.remove("open");
    }

    function setModalStep(stepId, state, detail) {
        var el = uploadModalBody.querySelector('[data-step="' + stepId + '"]');
        if (!el) return;
        var icon = el.querySelector(".ums-icon");
        var detailEl = el.querySelector(".ums-detail");
        icon.className = "ums-icon " + state;
        var iconMap = { pending: "bi-circle", active: "bi-arrow-repeat rna-processing", done: "bi-check-circle-fill", error: "bi-x-circle-fill" };
        icon.innerHTML = '<i class="bi ' + (iconMap[state] || iconMap.pending) + '"></i>';
        if (detail && detailEl) detailEl.textContent = detail;
    }

    function renderUploadModalSteps() {
        var steps = [
            { id: "create", label: "Creating submission session" },
            { id: "files", label: "Uploading data files" },
        ];
        if (metadataMode === "upload" && csvFile) steps.push({ id: "csv", label: "Uploading metadata CSV" });
        if (genomeSelect.value === "custom") steps.push({ id: "genome", label: "Uploading custom genome files" });
        steps.push({ id: "submit", label: "Submitting pipeline job" });

        uploadModalBody.innerHTML = steps.map(function (s) {
            return '<div class="upload-modal-step" data-step="' + s.id + '">' +
                '<span class="ums-icon pending"><i class="bi bi-circle"></i></span>' +
                '<span class="ums-label">' + escapeHtml(s.label) + '</span>' +
                '<span class="ums-detail"></span></div>';
        }).join("");
    }

    function updateModalUploadProgress() {
        var pct = getUploadProgress();
        var el = uploadModalBody.querySelector('[data-step="files"]');
        if (!el) return;
        var detailEl = el.querySelector(".ums-detail");
        if (detailEl) detailEl.textContent = pct + "% complete";

        // Update or create progress bar in modal
        var progBar = el.querySelector(".upload-modal-progress-bar");
        if (!progBar) {
            var wrap = document.createElement("div");
            wrap.className = "upload-modal-progress";
            wrap.innerHTML = '<div class="rna-progress" style="height:4px;margin-top:6px;"><div class="rna-progress-bar upload-modal-progress-bar" style="width:0%"></div></div>';
            el.appendChild(wrap);
            progBar = wrap.querySelector(".upload-modal-progress-bar");
        }
        progBar.style.width = pct + "%";
    }

    async function waitForUploads() {
        // If uploads haven't started yet, start them now
        if (!backgroundUploadPromise) {
            startBackgroundUploads();
        }

        // Poll until complete
        while (!areUploadsComplete()) {
            updateModalUploadProgress();
            await new Promise(function (r) { setTimeout(r, 500); });
        }
        updateModalUploadProgress();
    }

    async function submitPipeline() {
        if (isSubmitting) return;

        /* ── Pre-submission validation gate ── */
        var preErrors = validatePreSubmission();
        if (preErrors.length > 0) {
            preErrors.forEach(function (msg) { showToast("error", "Validation Error", msg); });
            return;
        }

        isSubmitting = true;
        submitBtn.disabled = true;
        submitBtn.innerHTML = '<i class="bi bi-arrow-repeat rna-processing"></i> Launching\u2026';

        renderUploadModalSteps();
        showUploadModal();

        try {
            // Step: Create submission session
            setModalStep("create", "active");
            await ensureSubmission();
            setModalStep("create", "done", "Session " + submissionId.substring(0, 8) + "\u2026");

            // Step: Wait for background file uploads to finish
            setModalStep("files", "active");

            if (!areUploadsComplete()) {
                // Files are still uploading in the background — show progress
                setModalStep("files", "active", "Waiting for uploads\u2026");
                await waitForUploads();
            }

            // Check for any failed uploads
            var hasFailed = false;
            var files = inputDataType === "fastq" ? selectedFiles :
                inputDataType === "alignment" ? selectedBamFiles : [];
            for (var fi = 0; fi < files.length; fi++) {
                var t = uploadTracker[files[fi].name];
                if (t && t.status === "failed") { hasFailed = true; break; }
            }
            if (hasFailed) throw new Error("Some file uploads failed. Please remove failed files and try again.");

            // Handle matrix upload (small, done inline)
            if (inputDataType === "matrix" && matrixFile) {
                if (!(await uploadMatrixFile())) throw new Error("Matrix file upload failed.");
            }
            setModalStep("files", "done");

            // Step: Upload CSV if applicable
            if (metadataMode === "upload" && csvFile) {
                setModalStep("csv", "active");
                if (!(await uploadCsvFile())) throw new Error("Metadata CSV upload failed.");
                setModalStep("csv", "done");
            }

            // Step: Upload custom genome if applicable
            if (genomeSelect.value === "custom") {
                setModalStep("genome", "active");
                if (!(await uploadCustomGenomeFiles())) throw new Error("Custom genome file upload failed.");
                setModalStep("genome", "done");
            }

            // Step: Submit pipeline
            setModalStep("submit", "active");

            var strandVal = inputDataType === "alignment"
                ? ($("strandedness-alignment") || {}).value || "unstranded"
                : ($("strandedness") || {}).value || "unstranded";

            var libType = inputDataType === "alignment"
                ? (document.querySelector('input[name="library_type_alignment"]:checked') || {}).value || "single"
                : libraryType;

            var payload = {
                submission_id: submissionId,
                submission_name: submissionNameInput.value.trim(),
                input_data_type: inputDataType,
                assay_type: inputDataType === "fastq" ? assayType : "standard_rna",
                library_type: libType,
                strandedness: strandVal,
                reference_genome: inputDataType === "matrix" ? "" : genomeSelect.value,
                custom_genome_name: genomeSelect.value === "custom" ? customGenomeName.value.trim() : "",
                quant_level: quantLevel.value,
                metadata_mode: metadataMode,
                adjusted_pvalue: parseFloat(adjPvalue.value) || 0.05,
                min_log2fc: parseFloat(minLog2fc.value) || -1.0,
                max_log2fc: parseFloat(maxLog2fc.value) || 1.0,
                metadata_payload: buildMetadataPayload(),
            };

            var res = await fetch("/api/pipeline/core", {
                method: "POST",
                headers: { "X-CSRFToken": CSRF, "Content-Type": "application/json" },
                body: JSON.stringify(payload),
            });

            var data = await res.json();
            if (!res.ok) {
                /* Backend may return {errors: [...]} array or single {error: "..."}. */
                if (data.errors && data.errors.length > 0) {
                    data.errors.forEach(function (msg) { showToast("error", "Validation Error", msg); });
                    throw new Error(data.errors[0]);
                }
                throw new Error(data.error || "Pipeline submission failed.");
            }
            /* Surface non-blocking warnings from backend */
            if (data.warnings && data.warnings.length > 0) {
                data.warnings.forEach(function (msg) { showToast("warning", "Notice", msg, 8000); });
            }

            setModalStep("submit", "done");

            // Show queued state
            uploadModalBody.innerHTML =
                '<div class="upload-modal-queued">' +
                '<div class="umq-icon"><i class="bi bi-check-circle-fill"></i></div>' +
                '<h4>Job Queued!</h4>' +
                '<p>Redirecting to processing view\u2026</p></div>';

            setTimeout(function () {
                window.location.href = "/processing/" + data.job_id + "/";
            }, 1800);

        } catch (err) {
            hideUploadModal();
            showToast("error", "Submission Error", err.message);
            submitBtn.disabled = false;
            submitBtn.innerHTML = '<i class="bi bi-rocket-takeoff"></i> Submit &amp; Launch';
            isSubmitting = false;
        }
    }

    /* ═══════════════════════════════════════════════════════════════════
       UTILITIES
       ═══════════════════════════════════════════════════════════════════ */

    function escapeHtml(str) {
        var div = document.createElement("div");
        div.appendChild(document.createTextNode(String(str)));
        return div.innerHTML;
    }

    /* ═══════════════════════════════════════════════════════════════════
       INITIALIZATION
       ═══════════════════════════════════════════════════════════════════ */

    /** Token 6: Reset all form state to clean defaults on page load/reload. */
    function resetFormState() {
        submissionId = null;
        inputDataType = "fastq";
        assayType = "standard_rna";
        libraryType = "single";
        metadataMode = "upload";

        selectedFiles = [];
        uploadedFiles = [];
        selectedBamFiles = [];
        uploadedBamFiles = [];
        matrixFile = null;
        parsedMatrixData = null;
        csvFile = null;
        parsedCsvData = null;
        manualColumns = ["condition"];
        columnSelectableValues = { condition: [] };
        columnMapping = { primary_group: "", batch_effect: "", covariates: [] };
        contrasts = [];
        customGenomeFiles = { fasta: null, annotation: null };
        currentStep = 1;
        isSubmitting = false;
        uploadTracker = {};
        backgroundUploadPromise = null;

        /* Clear DOM artefacts */
        if (csvFileName) { csvFileName.style.display = "none"; csvFileName.innerHTML = ""; }
        if (csvInput) csvInput.value = "";
        if (csvViewerTable) csvViewerTable.innerHTML = "";
        if (csvViewerSection) csvViewerSection.style.display = "none";
        if (contrastSection) contrastSection.style.display = "none";
        if (contrastList) contrastList.innerHTML = "";
        if (columnMappingSection) columnMappingSection.style.display = "none";
        if (submissionNameInput) submissionNameInput.value = "";
        if (filePills) filePills.innerHTML = "";
        if (fileList) fileList.innerHTML = "";
        if (bamFilePills) bamFilePills.innerHTML = "";
        if (bamFileList) bamFileList.innerHTML = "";
        if (matrixFileName) { matrixFileName.style.display = "none"; matrixFileName.innerHTML = ""; }
        if (columnValidationMsg) columnValidationMsg.style.display = "none";
    }

    /* ─── Tooltip Fixed-Position Helper ──────────────────────── */
    function initTooltipPositioning() {
        /* Move the tooltip bubble to <body> on hover so it escapes
           any overflow:hidden / zoom containers and always renders
           on top at true viewport coordinates. */
        var activeClone = null;
        var activeTip = null;

        function showTooltip(tip) {
            var tipText = tip.querySelector(".tip-text");
            if (!tipText) return;
            hideTooltip();

            activeClone = tipText.cloneNode(true);
            activeClone.classList.add("tip-text-clone");
            activeClone.style.position = "fixed";
            activeClone.style.opacity = "1";
            activeClone.style.visibility = "visible";
            activeClone.style.pointerEvents = "none";
            activeClone.style.zIndex = "100000";
            document.body.appendChild(activeClone);
            activeTip = tip;

            var rect = tip.getBoundingClientRect();
            var tipW = 270;
            var left = rect.right + 10;
            var top = rect.top + rect.height / 2;

            if (left + tipW > window.innerWidth - 8) {
                left = rect.left - tipW - 10;
                activeClone.classList.add("tip-flip-left");
            } else {
                activeClone.classList.remove("tip-flip-left");
            }
            activeClone.style.left = left + "px";
            activeClone.style.top = top + "px";
            activeClone.style.transform = "translateY(-50%)";
        }

        function hideTooltip() {
            if (activeClone) {
                activeClone.remove();
                activeClone = null;
                activeTip = null;
            }
        }

        document.addEventListener("mouseenter", function (e) {
            var tip = e.target.closest(".input-tip");
            if (tip) showTooltip(tip);
        }, true);

        document.addEventListener("mouseleave", function (e) {
            var tip = e.target.closest(".input-tip");
            if (tip && tip === activeTip) hideTooltip();
        }, true);
    }

    function init() {
        resetFormState();
        initEntryPoints();
        initAssayType();
        initLibraryType();
        initFastqUpload();
        initBamUpload();
        initMatrixUpload();
        initGenome();
        initMetadataToggle();
        initCsvUpload();
        initManualMetadata();
        initColumnMapping();
        initContrasts();
        initThresholds();
        initStepNavigation();
        applyAssayVisibility();

        wizardNext.addEventListener("click", nextStep);
        wizardBack.addEventListener("click", prevStep);
        submitBtn.addEventListener("click", submitPipeline);

        initTooltipPositioning();

        applyEntryPointVisibility();
        updateWizardProgress();
        updateWizardNav();
        updateBannerInfo();
    }

    /* Handle bfcache (back/forward) restoration */
    window.addEventListener("pageshow", function (e) {
        if (e.persisted) resetFormState();
    });

    /* ─── Cleanup on page unload ───────────────────────── */
    // When the user reloads or navigates away mid-upload, delete the
    // submission and all its uploaded files on the server.
    window.addEventListener("beforeunload", function () {
        if (!submissionId || isSubmitting) return; // nothing to clean up, or already submitted
        var payload = JSON.stringify({ submission_id: submissionId });
        navigator.sendBeacon(
            "/api/submission/delete",
            new Blob([payload], { type: "application/json" })
        );
    });

    /* ─── Banner Info (Dev/Prod) ────────────────────────── */

    function updateBannerInfo() {
        if (!bannerInfo) return;

        if (!IS_PRODUCTION) {
            // Dev: always show Submission ID if available
            if (submissionId) {
                bannerInfo.style.display = "";
                bannerInfo.innerHTML = '<span class="banner-tag"><i class="bi bi-bug"></i> ID: ' + escapeHtml(submissionId.substring(0, 8)) + '\u2026</span>';
            } else {
                bannerInfo.style.display = "";
                bannerInfo.innerHTML = '<span class="banner-tag"><i class="bi bi-bug"></i> Dev Mode</span>';
            }
        } else {
            // Prod: show Submission Name only after Step 1
            if (currentStep > 1 && submissionNameInput.value.trim()) {
                bannerInfo.style.display = "";
                bannerInfo.innerHTML = '<span class="banner-tag"><i class="bi bi-journal-text"></i> ' + escapeHtml(submissionNameInput.value.trim()) + '</span>';
            } else {
                bannerInfo.style.display = "none";
            }
        }
    }

    if (document.readyState === "loading") {
        document.addEventListener("DOMContentLoaded", init);
    } else {
        init();
    }
})();
