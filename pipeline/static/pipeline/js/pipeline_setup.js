/**
 * RNAseek – Core Pipeline Setup (Single Page)
 *
 * Handles:
 * - Submission creation (unique UUID per analysis)
 * - Library type + strandedness selection
 * - FASTQ chunked upload with per-file progress
 * - Paired-end pair validation
 * - Reference genome selection (incl. custom genome upload)
 * - Metadata: CSV upload with in-browser PapaParse parsing, or manual table
 *   with dynamically added columns (Age, Sex, Batch, etc.)
 * - Column role assignment (primary_group, batch_effect, additional_covariates)
 * - Column validation (missing values, zero variance)
 * - Dynamic contrast builder for multi-group comparisons (>2 groups)
 * - Significance threshold live preview
 * - Full form validation + pipeline submission
 */
(function () {
    "use strict";

    const CHUNK_SIZE = 5 * 1024 * 1024; // 5 MB
    const CSRF = document.querySelector('meta[name="csrf-token"]').content;

    // ── State ──────────────────────────────────────────────────
    let submissionId = null;
    let inputDataType = "fastq";          // "fastq" | "alignment" | "matrix"
    let assayType = "standard_rna";       // "standard_rna" | "small_rna" | "chip_seq" | "methylation"
    let selectedFiles = [];
    let uploadedFiles = [];
    let isUploading = false;
    let csvFile = null;
    let customGenomeFiles = { fasta: null, annotation: null };

    // Alignment entry state
    let selectedBamFiles = [];
    let uploadedBamFiles = [];

    // Matrix entry state
    let matrixFile = null;
    let parsedMatrixData = null;          // { headers: string[], rows: object[] }

    // Metadata state (new)
    let parsedCsvData = null;           // { headers: string[], rows: object[] }
    let manualColumns = ["condition"];   // Default column for manual mode
    let columnSelectableValues = {};      // Per-column predefined values, e.g. { condition: ["Control","Treatment"] }
    let columnMapping = {
        primary_group: null,
        batch_effect: null,
        additional_covariates: [],
    };
    let contrasts = [];                 // e.g. [["Drug_A","Control"], ["Drug_B","Control"]]

    // ── DOM References (existing) ─────────────────────────────
    const dropZone = document.getElementById("drop-zone");
    const fastqInput = document.getElementById("fastq-input");
    const filePills = document.getElementById("file-pills");
    const fileList = document.getElementById("file-list");
    const uploadArea = document.getElementById("upload-progress-area");
    const pairValidation = document.getElementById("pair-validation");

    const genomeSelect = document.getElementById("genome-select");
    const customGenomeSection = document.getElementById("custom-genome-section");
    const customGenomeName = document.getElementById("custom-genome-name");
    const customGenomeFasta = document.getElementById("custom-genome-fasta");
    const customGenomeAnnotation = document.getElementById("custom-genome-annotation");

    const metaToggle = document.getElementById("meta-toggle");
    const metaUploadPanel = document.getElementById("meta-upload-panel");
    const metaManualPanel = document.getElementById("meta-manual-panel");
    const csvDropZone = document.getElementById("csv-drop-zone");
    const csvInput = document.getElementById("csv-input");
    const csvFileName = document.getElementById("csv-file-name");
    const manualPanel = document.getElementById("manual-metadata-panel");

    const adjPvalue = document.getElementById("adj-pvalue");
    const minLog2fc = document.getElementById("min-log2fc");
    const maxLog2fc = document.getElementById("max-log2fc");
    const fcPreview = document.getElementById("fc-preview");
    const pvalPreview = document.getElementById("pval-preview");

    const submitBtn = document.getElementById("submit-pipeline");
    const pairedEndTip = document.getElementById("paired-end-tip");
    const quantLevel = document.getElementById("quant-level");

    // Validation indicators (existing)
    const valLibrary = document.getElementById("val-library");
    const valFiles = document.getElementById("val-files");
    const valGenome = document.getElementById("val-genome");
    const valMetadata = document.getElementById("val-metadata");

    // ── DOM References (new) ──────────────────────────────────
    const csvPreviewArea = document.getElementById("csv-preview-area");
    const columnNameInput = document.getElementById("column-name-input");
    const addColumnBtn = document.getElementById("add-column-btn");
    const columnChips = document.getElementById("column-chips");
    const metadataBody = document.getElementById("metadata-body");
    const metadataHeaderRow = document.getElementById("metadata-header-row");
    const noFilesHint = document.getElementById("no-files-hint");

    // Condition builder DOM
    const conditionValueInput = document.getElementById("condition-value-input");
    const addConditionValueBtn = document.getElementById("add-condition-btn");
    const conditionChipsEl = document.getElementById("condition-chips");
    const conditionTargetSelect = document.getElementById("condition-target-column");

    // Roles + Contrast row
    const rolesContrastRow = document.getElementById("roles-contrast-row");

    // CSV Viewer section (below the 4-card grid)
    const csvViewerSection = document.getElementById("csv-viewer-section");
    const csvViewerInfo = document.getElementById("csv-viewer-info");
    const csvViewerTable = document.getElementById("csv-viewer-table");

    const columnMappingSection = document.getElementById("column-mapping-section");
    const primaryGroupSelect = document.getElementById("primary-group-select");
    const batchEffectSelect = document.getElementById("batch-effect-select");
    const covariatesList = document.getElementById("covariates-list");
    const columnValidationMsg = document.getElementById("column-validation-msg");
    const valMapping = document.getElementById("val-mapping");

    const contrastSection = document.getElementById("contrast-section");
    const contrastList = document.getElementById("contrast-list");
    const addContrastBtn = document.getElementById("add-contrast-btn");

    // ── DOM References (entry point + alignment + matrix) ─────
    const entryPointGroup = document.getElementById("entry-point-group");
    const colFastq = document.getElementById("col-fastq");
    const colAlignment = document.getElementById("col-alignment");
    const colMatrix = document.getElementById("col-matrix");
    const colGenome = document.getElementById("col-genome");
    const colMetadata = document.getElementById("col-metadata");
    const colThresholds = document.getElementById("col-thresholds");

    // Assay type DOM
    const assayTypeSection = document.getElementById("assay-type-section");
    const assayHelpText = document.getElementById("assay-help-text");

    // Alignment entry DOM
    const bamDropZone = document.getElementById("bam-drop-zone");
    const bamInput = document.getElementById("bam-input");
    const bamFilePills = document.getElementById("bam-file-pills");
    const bamFileList = document.getElementById("bam-file-list");
    const bamUploadArea = document.getElementById("bam-upload-progress-area");
    const strandednessAlignment = document.getElementById("strandedness-alignment");

    // Matrix entry DOM
    const matrixDropZone = document.getElementById("matrix-drop-zone");
    const matrixInput = document.getElementById("matrix-input");
    const matrixFileName = document.getElementById("matrix-file-name");
    const matrixPreviewArea = document.getElementById("matrix-preview-area");
    const matrixValidation = document.getElementById("matrix-validation");

    // ════════════════════════════════════════════════════════════
    //  1. SUBMISSION CREATION
    // ════════════════════════════════════════════════════════════

    async function ensureSubmission() {
        if (submissionId) return submissionId;
        const res = await fetch("/api/submission/create", {
            method: "POST",
            headers: { "X-CSRFToken": CSRF },
        });
        if (!res.ok) throw new Error("Failed to create submission");
        const data = await res.json();
        submissionId = data.submission_id;
        return submissionId;
    }

    // ════════════════════════════════════════════════════════════
    //  1b. ENTRY POINT SWITCHING
    // ════════════════════════════════════════════════════════════

    var epRadios = document.querySelectorAll('input[name="input_data_type"]');
    var epCards = document.querySelectorAll(".entry-point-card");

    epRadios.forEach(function (radio) {
        radio.addEventListener("change", function () {
            inputDataType = radio.value;
            epCards.forEach(function (c) { c.classList.remove("selected"); });
            radio.closest(".entry-point-card").classList.add("selected");
            resetMetadataState();
            applyEntryPointVisibility();
            validateAll();
        });
    });

    // ── Assay Type Selection ──
    var atRadios = document.querySelectorAll('input[name="assay_type"]');
    var atCards = document.querySelectorAll("#assay-type-group .entry-point-card");

    atRadios.forEach(function (radio) {
        radio.addEventListener("change", function () {
            assayType = radio.value;
            atCards.forEach(function (c) { c.classList.remove("selected"); });
            radio.closest(".entry-point-card").classList.add("selected");
            updateAssayHelpText();
            validateAll();
        });
    });

    function updateAssayHelpText() {
        if (!assayHelpText) return;
        if (assayType === "chip_seq") {
            assayHelpText.style.display = "";
            assayHelpText.innerHTML =
                '<div style="background: rgba(9,153,152,0.08); border: 1px solid rgba(9,153,152,0.25); border-radius: 8px; padding: 0.75rem 1rem;">' +
                '<p style="margin: 0 0 .4rem; font-size: .84rem; font-weight: 600; color: var(--rna-navy);"><i class="bi bi-info-circle"></i> ChIP-seq Metadata Tips</p>' +
                '<ul style="margin: 0; padding-left: 1.2rem; font-size: .82rem; line-height: 1.6; color: var(--rna-grey-700);">' +
                '<li>Label control/input samples as <strong>&ldquo;input&rdquo;</strong> in the condition column. All other samples are treated as IP (treatment).</li>' +
                '<li>Define contrasts between your treatment conditions (e.g. DrugA vs. Control) for differential binding analysis.</li>' +
                '<li>The pipeline will generate a consensus peak count matrix and run DESeq2 for differential binding.</li>' +
                '</ul></div>';
        } else if (assayType === "methylation") {
            assayHelpText.style.display = "";
            assayHelpText.innerHTML =
                '<div style="background: rgba(9,153,152,0.08); border: 1px solid rgba(9,153,152,0.25); border-radius: 8px; padding: 0.75rem 1rem;">' +
                '<p style="margin: 0 0 .4rem; font-size: .84rem; font-weight: 600; color: var(--rna-navy);"><i class="bi bi-info-circle"></i> DNA Methylation Metadata Tips</p>' +
                '<ul style="margin: 0; padding-left: 1.2rem; font-size: .82rem; line-height: 1.6; color: var(--rna-grey-700);">' +
                '<li>Define treatment vs. control groups in your condition column for differential methylation.</li>' +
                '<li>The pipeline runs Bismark for methylation extraction, then methylKit (via R) for differential methylation analysis.</li>' +
                '<li>PCA, volcano, and MA plots will be generated from differentially methylated regions.</li>' +
                '</ul></div>';
        } else {
            assayHelpText.style.display = "none";
            assayHelpText.innerHTML = "";
        }
    }

    /**
     * Reset metadata-related state when switching entry points.
     * Prevents stale data from bleeding across workflows.
     */
    function resetMetadataState() {
        // Reset CSV metadata
        parsedCsvData = null;
        csvFile = null;
        if (csvPreviewArea) csvPreviewArea.style.display = "none";
        if (csvFileName) { csvFileName.style.display = "none"; csvFileName.innerHTML = ""; }
        if (csvDropZone) csvDropZone.classList.remove("has-files");
        if (csvViewerSection) csvViewerSection.style.display = "none";

        // Reset manual columns to default
        manualColumns = ["condition"];
        columnSelectableValues = {};

        // Reset column mapping & contrasts
        columnMapping = { primary_group: null, batch_effect: null, additional_covariates: [] };
        contrasts = [];

        // Reset metadata toggle to upload mode
        metaToggle.querySelectorAll(".meta-toggle-btn").forEach(function (b) {
            b.classList.toggle("active", b.dataset.mode === "upload");
        });
        metaUploadPanel.style.display = "";
        metaManualPanel.style.display = "none";
        if (manualPanel) manualPanel.style.display = "none";

        // Clear manual table
        if (metadataBody) metadataBody.innerHTML = "";
        if (noFilesHint) noFilesHint.style.display = "";

        // Hide mapping & contrast panels, clear contrast DOM
        columnMappingSection.style.display = "none";
        contrastSection.style.display = "none";
        rolesContrastRow.style.display = "none";
        if (contrastList) contrastList.innerHTML = "";

        // Rebuild chips
        renderColumnChips();
        renderConditionChips();
        syncConditionTargetDropdown();
    }

    function applyEntryPointVisibility() {
        // Column visibility
        colFastq.style.display = inputDataType === "fastq" ? "" : "none";
        colAlignment.style.display = inputDataType === "alignment" ? "" : "none";
        colMatrix.style.display = inputDataType === "matrix" ? "" : "none";
        colGenome.style.display = inputDataType === "matrix" ? "none" : "";

        // Assay type section visibility (only for FASTQ)
        if (assayTypeSection) {
            assayTypeSection.style.display = inputDataType === "fastq" ? "" : "none";
        }

        // Center 3 cards in matrix mode
        document.querySelector(".setup-grid").classList.toggle("matrix-mode", inputDataType === "matrix");

        // Validation summary items
        valLibrary.style.display = inputDataType === "fastq" ? "" : "none";
        valGenome.style.display = inputDataType === "matrix" ? "none" : "";

        // Update file validation label
        if (inputDataType === "fastq") {
            valFiles.querySelector("i").nextSibling.textContent = " FASTQ files uploaded";
        } else if (inputDataType === "alignment") {
            valFiles.querySelector("i").nextSibling.textContent = " BAM/CRAM files uploaded";
        } else {
            valFiles.querySelector("i").nextSibling.textContent = " Count matrix uploaded";
        }

        // Manual metadata table hint
        if (noFilesHint) {
            if (inputDataType === "fastq") {
                noFilesHint.innerHTML = '<i class="bi bi-arrow-up"></i> Upload FASTQ files above to populate this table.';
            } else if (inputDataType === "alignment") {
                noFilesHint.innerHTML = '<i class="bi bi-arrow-up"></i> Upload BAM files above to populate this table.';
            } else {
                noFilesHint.innerHTML = '<i class="bi bi-arrow-up"></i> Upload a count matrix above — sample names will be extracted from column headers.';
            }
        }

        // Rebuild manual metadata table if mode is manual
        if (getMetadataMode() === "manual") rebuildMetadataTable();
    }

    // ════════════════════════════════════════════════════════════
    //  2. LIBRARY TYPE SELECTION
    // ════════════════════════════════════════════════════════════

    const libRadios = document.querySelectorAll('input[name="library_type"]');
    const ltCards = document.querySelectorAll(".library-type-card");

    libRadios.forEach(function (radio) {
        radio.addEventListener("change", function () {
            ltCards.forEach(function (c) { c.classList.remove("selected"); });
            radio.closest(".library-type-card").classList.add("selected");
            pairedEndTip.classList.toggle("visible", radio.value === "paired");
            validatePairs();
            rebuildMetadataTable();
            validateAll();
        });
    });

    function getLibraryType() {
        var checked = document.querySelector('input[name="library_type"]:checked');
        return checked ? checked.value : null;
    }

    // ════════════════════════════════════════════════════════════
    //  3. FASTQ FILE SELECTION + CHUNKED UPLOAD
    // ════════════════════════════════════════════════════════════

    dropZone.addEventListener("click", function () { fastqInput.click(); });

    dropZone.addEventListener("dragover", function (e) {
        e.preventDefault();
        dropZone.classList.add("drag-over");
    });

    dropZone.addEventListener("dragleave", function () {
        dropZone.classList.remove("drag-over");
    });

    dropZone.addEventListener("drop", function (e) {
        e.preventDefault();
        dropZone.classList.remove("drag-over");
        addFiles(e.dataTransfer.files);
    });

    fastqInput.addEventListener("change", function () {
        addFiles(fastqInput.files);
        fastqInput.value = "";
    });

    function addFiles(fileListObj) {
        for (var i = 0; i < fileListObj.length; i++) {
            var file = fileListObj[i];
            if (!selectedFiles.some(function (f) { return f.name === file.name; })) {
                selectedFiles.push(file);
            }
        }
        renderFilePills();
        validatePairs();
        rebuildMetadataTable();
        validateAll();
    }

    function renderFilePills() {
        filePills.innerHTML = "";
        if (selectedFiles.length === 0) {
            fileList.style.display = "none";
            dropZone.classList.remove("has-files");
            return;
        }
        fileList.style.display = "block";
        dropZone.classList.add("has-files");

        selectedFiles.forEach(function (file, idx) {
            var pill = document.createElement("span");
            pill.className = "file-pill";
            pill.innerHTML =
                '<i class="bi bi-file-earmark-zip"></i> ' +
                escapeHtml(file.name) +
                ' <span class="remove-file" data-idx="' + idx + '">&times;</span>';
            filePills.appendChild(pill);
        });

        filePills.querySelectorAll(".remove-file").forEach(function (btn) {
            btn.addEventListener("click", function (e) {
                e.stopPropagation();
                var idx = parseInt(btn.dataset.idx, 10);
                selectedFiles.splice(idx, 1);
                uploadedFiles = uploadedFiles.filter(function (n) {
                    return selectedFiles.some(function (f) { return f.name === n; });
                });
                renderFilePills();
                validatePairs();
                rebuildMetadataTable();
                validateAll();
            });
        });
    }

    // ── Paired-end validation ──
    function validatePairs() {
        if (getLibraryType() !== "paired" || selectedFiles.length === 0) {
            pairValidation.style.display = "none";
            return;
        }

        var names = selectedFiles.map(function (f) { return f.name; });
        var pairMap = {};
        var re = /^(.+?)(?:_R([12])|_([12]))\.(?:fq|fastq)\.gz$/i;

        names.forEach(function (name) {
            var m = re.exec(name);
            if (m) {
                var prefix = m[1];
                var readNum = m[2] || m[3];
                if (!pairMap[prefix]) pairMap[prefix] = {};
                pairMap[prefix][readNum] = name;
            }
        });

        var unpaired = [];
        for (var prefix in pairMap) {
            if (!pairMap[prefix]["1"] || !pairMap[prefix]["2"]) {
                unpaired.push(prefix);
            }
        }

        var matchedNames = new Set();
        for (var p in pairMap) {
            Object.values(pairMap[p]).forEach(function (n) { matchedNames.add(n); });
        }
        var unmatched = names.filter(function (n) { return !matchedNames.has(n); });

        if (unpaired.length > 0 || unmatched.length > 0) {
            var msg = '<i class="bi bi-exclamation-triangle"></i> ';
            if (unpaired.length > 0) {
                msg += "Missing pair partner for: " + unpaired.join(", ") + ". ";
            }
            if (unmatched.length > 0) {
                msg += "Could not parse read direction from: " + unmatched.join(", ") + ".";
            }
            pairValidation.className = "validation-msg error";
            pairValidation.innerHTML = msg;
            pairValidation.style.display = "block";
        } else if (Object.keys(pairMap).length > 0) {
            pairValidation.className = "validation-msg success";
            pairValidation.innerHTML =
                '<i class="bi bi-check-circle"></i> ' +
                Object.keys(pairMap).length + " paired sample(s) detected.";
            pairValidation.style.display = "block";
        } else {
            pairValidation.style.display = "none";
        }
    }

    /**
     * Upload all selected FASTQ files via chunked upload.
     */
    async function uploadFastqFiles() {
        if (isUploading) return false;
        isUploading = true;
        uploadArea.innerHTML = "";

        await ensureSubmission();

        var toUpload = selectedFiles.filter(function (f) {
            return !uploadedFiles.includes(f.name);
        });
        if (toUpload.length === 0) {
            isUploading = false;
            return true;
        }

        for (var fi = 0; fi < toUpload.length; fi++) {
            var file = toUpload[fi];
            var totalChunks = Math.ceil(file.size / CHUNK_SIZE);
            var barId = "prog-" + file.name.replace(/\W/g, "_");

            uploadArea.insertAdjacentHTML("beforeend",
                '<div style="margin-bottom:.4rem;">' +
                '<div class="rna-text-xs" style="margin-bottom:.15rem;">' + escapeHtml(file.name) + '</div>' +
                '<div class="rna-progress"><div class="rna-progress-bar animated" id="' + barId + '" style="width:0%"></div></div>' +
                '</div>'
            );

            var bar = document.getElementById(barId);
            var success = true;

            for (var i = 0; i < totalChunks; i++) {
                var start = i * CHUNK_SIZE;
                var end = Math.min(start + CHUNK_SIZE, file.size);
                var chunk = file.slice(start, end);

                var fd = new FormData();
                fd.append("file", chunk);
                fd.append("filename", file.name);
                fd.append("chunk_index", i);
                fd.append("total_chunks", totalChunks);
                fd.append("submission_id", submissionId);

                var res = await fetch("/api/upload/chunk", {
                    method: "POST",
                    headers: { "X-CSRFToken": CSRF },
                    body: fd,
                });

                if (!res.ok) {
                    success = false;
                    break;
                }

                bar.style.width = Math.round(((i + 1) / totalChunks) * 100) + "%";
            }

            if (success) {
                bar.classList.remove("animated");
                uploadedFiles.push(file.name);
            } else {
                bar.style.background = "var(--rna-accent-red)";
                bar.classList.remove("animated");
            }
        }

        isUploading = false;
        rebuildMetadataTable();
        validateAll();
        return uploadedFiles.length === selectedFiles.length;
    }

    // ════════════════════════════════════════════════════════════
    //  3b. BAM/CRAM FILE SELECTION + CHUNKED UPLOAD (Alignment entry)
    // ════════════════════════════════════════════════════════════

    bamDropZone.addEventListener("click", function () { bamInput.click(); });
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

    // Alignment library type card selection
    var alignLibRadios = document.querySelectorAll('input[name="library_type_alignment"]');
    var alignLtCards = document.querySelectorAll("#col-alignment .library-type-card");
    alignLibRadios.forEach(function (radio) {
        radio.addEventListener("change", function () {
            alignLtCards.forEach(function (c) { c.classList.remove("selected"); });
            radio.closest(".library-type-card").classList.add("selected");
        });
    });
    // Set initial selected state
    (function () {
        var checked = document.querySelector('input[name="library_type_alignment"]:checked');
        if (checked) checked.closest(".library-type-card").classList.add("selected");
    })();

    function addBamFiles(fileListObj) {
        for (var i = 0; i < fileListObj.length; i++) {
            var file = fileListObj[i];
            if (!selectedBamFiles.some(function (f) { return f.name === file.name; })) {
                selectedBamFiles.push(file);
            }
        }
        renderBamFilePills();
        rebuildMetadataTable();
        validateAll();
    }

    function renderBamFilePills() {
        bamFilePills.innerHTML = "";
        if (selectedBamFiles.length === 0) {
            bamFileList.style.display = "none";
            bamDropZone.classList.remove("has-files");
            return;
        }
        bamFileList.style.display = "block";
        bamDropZone.classList.add("has-files");

        selectedBamFiles.forEach(function (file, idx) {
            var pill = document.createElement("span");
            pill.className = "file-pill";
            pill.innerHTML =
                '<i class="bi bi-file-earmark-binary"></i> ' +
                escapeHtml(file.name) +
                ' <span class="remove-file" data-idx="' + idx + '">&times;</span>';
            bamFilePills.appendChild(pill);
        });

        bamFilePills.querySelectorAll(".remove-file").forEach(function (btn) {
            btn.addEventListener("click", function (e) {
                e.stopPropagation();
                var idx = parseInt(btn.dataset.idx, 10);
                selectedBamFiles.splice(idx, 1);
                uploadedBamFiles = uploadedBamFiles.filter(function (n) {
                    return selectedBamFiles.some(function (f) { return f.name === n; });
                });
                renderBamFilePills();
                rebuildMetadataTable();
                validateAll();
            });
        });
    }

    async function uploadBamFiles() {
        if (isUploading) return false;
        isUploading = true;
        bamUploadArea.innerHTML = "";
        await ensureSubmission();

        var toUpload = selectedBamFiles.filter(function (f) {
            return !uploadedBamFiles.includes(f.name);
        });
        if (toUpload.length === 0) { isUploading = false; return true; }

        for (var fi = 0; fi < toUpload.length; fi++) {
            var file = toUpload[fi];
            var totalChunks = Math.ceil(file.size / CHUNK_SIZE);
            var barId = "bam-prog-" + file.name.replace(/\W/g, "_");

            bamUploadArea.insertAdjacentHTML("beforeend",
                '<div style="margin-bottom:.4rem;">' +
                '<div class="rna-text-xs" style="margin-bottom:.15rem;">' + escapeHtml(file.name) + '</div>' +
                '<div class="rna-progress"><div class="rna-progress-bar animated" id="' + barId + '" style="width:0%"></div></div>' +
                '</div>'
            );
            var bar = document.getElementById(barId);
            var success = true;

            for (var i = 0; i < totalChunks; i++) {
                var start = i * CHUNK_SIZE;
                var end = Math.min(start + CHUNK_SIZE, file.size);
                var chunk = file.slice(start, end);

                var fd = new FormData();
                fd.append("file", chunk);
                fd.append("filename", file.name);
                fd.append("chunk_index", i);
                fd.append("total_chunks", totalChunks);
                fd.append("submission_id", submissionId);
                fd.append("file_role", "ALIGNMENT_BAM");

                var res = await fetch("/api/upload/chunk", {
                    method: "POST",
                    headers: { "X-CSRFToken": CSRF },
                    body: fd,
                });

                if (!res.ok) { success = false; break; }
                bar.style.width = Math.round(((i + 1) / totalChunks) * 100) + "%";
            }

            if (success) {
                bar.classList.remove("animated");
                uploadedBamFiles.push(file.name);
            } else {
                bar.style.background = "var(--rna-accent-red)";
                bar.classList.remove("animated");
            }
        }

        isUploading = false;
        validateAll();
        return uploadedBamFiles.length === selectedBamFiles.length;
    }

    // ════════════════════════════════════════════════════════════
    //  3c. COUNT MATRIX UPLOAD + VALIDATION (Matrix entry)
    // ════════════════════════════════════════════════════════════

    matrixDropZone.addEventListener("click", function () { matrixInput.click(); });
    matrixDropZone.addEventListener("dragover", function (e) {
        e.preventDefault(); matrixDropZone.classList.add("drag-over");
    });
    matrixDropZone.addEventListener("dragleave", function () {
        matrixDropZone.classList.remove("drag-over");
    });
    matrixDropZone.addEventListener("drop", function (e) {
        e.preventDefault(); matrixDropZone.classList.remove("drag-over");
        if (e.dataTransfer.files.length > 0) setMatrixFile(e.dataTransfer.files[0]);
    });
    matrixInput.addEventListener("change", function () {
        if (matrixInput.files.length > 0) setMatrixFile(matrixInput.files[0]);
    });

    function setMatrixFile(file) {
        matrixFile = file;
        matrixFileName.style.display = "block";
        matrixFileName.innerHTML =
            '<i class="bi bi-file-earmark-check" style="color:var(--rna-accent-green);"></i> ' +
            escapeHtml(file.name);
        matrixDropZone.classList.add("has-files");

        // Detect separator
        var sep = file.name.endsWith(".tsv") ? "\t" : ",";

        Papa.parse(file, {
            header: true,
            skipEmptyLines: true,
            delimiter: sep === "\t" ? "\t" : undefined,
            complete: function (results) {
                if (results.errors.length > 0 && results.data.length === 0) {
                    matrixPreviewArea.style.display = "block";
                    matrixPreviewArea.innerHTML =
                        '<div class="validation-msg error"><i class="bi bi-exclamation-triangle"></i> ' +
                        'Could not parse file: ' + escapeHtml(results.errors[0].message) + '</div>';
                    parsedMatrixData = null;
                    validateAll();
                    return;
                }

                parsedMatrixData = {
                    headers: results.meta.fields,
                    rows: results.data,
                };

                // Pre-flight validation
                var msgs = validateMatrixData(parsedMatrixData);
                if (msgs.length > 0) {
                    matrixValidation.className = "validation-msg error";
                    matrixValidation.innerHTML =
                        '<i class="bi bi-exclamation-triangle"></i> ' + msgs.join("<br>");
                    matrixValidation.style.display = "block";
                } else {
                    matrixValidation.className = "validation-msg success";
                    matrixValidation.innerHTML =
                        '<i class="bi bi-check-circle"></i> Matrix validated: ' +
                        parsedMatrixData.rows.length + ' genes, ' +
                        (parsedMatrixData.headers.length - 1) + ' samples.';
                    matrixValidation.style.display = "block";
                }

                renderMatrixPreview();
                rebuildMetadataTable();
                updateColumnMappingOptions();
                validateAll();
            },
        });
    }

    function validateMatrixData(data) {
        var msgs = [];
        if (!data || !data.rows.length) {
            msgs.push("Count matrix is empty.");
            return msgs;
        }
        if (data.headers.length < 2) {
            msgs.push("Count matrix must have at least one gene ID column and one sample column.");
            return msgs;
        }

        // Check first 50 rows for non-numeric sample columns
        var sampleCols = data.headers.slice(1);
        var checkRows = data.rows.slice(0, 50);
        var hasNonNumeric = false;
        var hasNegative = false;
        var hasFloat = false;

        checkRows.forEach(function (row) {
            sampleCols.forEach(function (col) {
                var val = row[col];
                if (val === undefined || val === null || val === "") return;
                var num = Number(val);
                if (isNaN(num)) hasNonNumeric = true;
                if (num < 0) hasNegative = true;
                if (num % 1 !== 0) hasFloat = true;
            });
        });

        if (hasNonNumeric) msgs.push("Some sample columns contain non-numeric values. Raw count matrices should contain only integers.");
        if (hasNegative) msgs.push("Some values are negative. Raw counts should be non-negative integers.");
        if (hasFloat) msgs.push("Some values are floating-point. This may indicate normalized data (TPM/FPKM). DESeq2 requires raw integer counts.");

        return msgs;
    }

    function renderMatrixPreview() {
        if (!parsedMatrixData || !parsedMatrixData.rows.length) {
            matrixPreviewArea.style.display = "none";
            return;
        }
        matrixPreviewArea.style.display = "block";
        var headers = parsedMatrixData.headers;
        var previewRows = parsedMatrixData.rows.slice(0, 5);

        var html = '<div class="csv-example">';
        html += '<div class="csv-example-header">';
        html += '<span><i class="bi bi-grid-3x3" style="color:var(--rna-accent-green);"></i> ';
        html += parsedMatrixData.rows.length + ' genes &times; ' + (headers.length - 1) + ' samples</span></div>';
        html += '<table><thead><tr>';
        headers.forEach(function (h) { html += '<th>' + escapeHtml(h) + '</th>'; });
        html += '</tr></thead><tbody>';
        previewRows.forEach(function (row) {
            html += '<tr>';
            headers.forEach(function (h) { html += '<td>' + escapeHtml(row[h] || '') + '</td>'; });
            html += '</tr>';
        });
        if (parsedMatrixData.rows.length > 5) {
            html += '<tr><td colspan="' + headers.length + '" ' +
                'style="text-align:center;color:var(--rna-grey-500);font-style:italic;">' +
                '... ' + (parsedMatrixData.rows.length - 5) + ' more rows</td></tr>';
        }
        html += '</tbody></table></div>';
        matrixPreviewArea.innerHTML = html;
    }

    async function uploadMatrixFile() {
        if (!matrixFile) return true;
        await ensureSubmission();

        var totalChunks = Math.ceil(matrixFile.size / CHUNK_SIZE);
        for (var i = 0; i < totalChunks; i++) {
            var start = i * CHUNK_SIZE;
            var end = Math.min(start + CHUNK_SIZE, matrixFile.size);
            var chunk = matrixFile.slice(start, end);

            var fd = new FormData();
            fd.append("file", chunk);
            fd.append("filename", matrixFile.name);
            fd.append("chunk_index", i);
            fd.append("total_chunks", totalChunks);
            fd.append("submission_id", submissionId);
            fd.append("file_role", "USER_COUNT_MATRIX");

            var res = await fetch("/api/upload/chunk", {
                method: "POST",
                headers: { "X-CSRFToken": CSRF },
                body: fd,
            });
            if (!res.ok) return false;
        }
        return true;
    }

    // ════════════════════════════════════════════════════════════
    //  4. REFERENCE GENOME
    // ════════════════════════════════════════════════════════════

    genomeSelect.addEventListener("change", function () {
        var isCustom = genomeSelect.value === "custom";
        customGenomeSection.classList.toggle("visible", isCustom);
        validateAll();
    });

    customGenomeName.addEventListener("input", validateAll);

    customGenomeFasta.addEventListener("change", function () {
        customGenomeFiles.fasta = customGenomeFasta.files[0] || null;
        var label = document.getElementById("fasta-file-label");
        if (label) label.textContent = customGenomeFiles.fasta ? customGenomeFiles.fasta.name : "No file chosen";
        validateAll();
    });

    customGenomeAnnotation.addEventListener("change", function () {
        customGenomeFiles.annotation = customGenomeAnnotation.files[0] || null;
        var label = document.getElementById("annotation-file-label");
        if (label) label.textContent = customGenomeFiles.annotation ? customGenomeFiles.annotation.name : "No file chosen";
        validateAll();
    });

    /**
     * Upload custom genome files (FASTA + GTF) via chunked upload.
     */
    async function uploadCustomGenome() {
        await ensureSubmission();

        var filesToUpload = [
            { file: customGenomeFiles.fasta, role: "CUSTOM_GENOME_FASTA" },
            { file: customGenomeFiles.annotation, role: "CUSTOM_GENOME_ANNOTATION" },
        ];

        for (var fi = 0; fi < filesToUpload.length; fi++) {
            var item = filesToUpload[fi];
            var totalChunks = Math.ceil(item.file.size / CHUNK_SIZE);
            for (var i = 0; i < totalChunks; i++) {
                var start = i * CHUNK_SIZE;
                var end = Math.min(start + CHUNK_SIZE, item.file.size);
                var chunk = item.file.slice(start, end);

                var fd = new FormData();
                fd.append("file", chunk);
                fd.append("filename", item.file.name);
                fd.append("chunk_index", i);
                fd.append("total_chunks", totalChunks);
                fd.append("submission_id", submissionId);
                fd.append("file_role", item.role);

                var res = await fetch("/api/upload/chunk", {
                    method: "POST",
                    headers: { "X-CSRFToken": CSRF },
                    body: fd,
                });

                if (!res.ok) return false;
            }
        }
        return true;
    }

    // ════════════════════════════════════════════════════════════
    //  5. METADATA MODE + CSV PARSING (PapaParse)
    // ════════════════════════════════════════════════════════════

    function getMetadataMode() {
        var active = metaToggle.querySelector(".meta-toggle-btn.active");
        return active ? active.dataset.mode : "upload";
    }

    metaToggle.querySelectorAll(".meta-toggle-btn").forEach(function (btn) {
        btn.addEventListener("click", function () {
            metaToggle.querySelectorAll(".meta-toggle-btn").forEach(function (b) {
                b.classList.remove("active");
            });
            btn.classList.add("active");

            var mode = btn.dataset.mode;
            metaUploadPanel.style.display = mode === "upload" ? "" : "none";
            metaManualPanel.style.display = mode === "manual" ? "" : "none";
            manualPanel.style.display = mode === "manual" ? "" : "none";

            // Reset parsed data and contrasts when switching modes
            if (mode === "upload") {
                parsedCsvData = null;
                csvPreviewArea.style.display = "none";
            }
            if (mode === "manual") {
                rebuildMetadataTable();
            }

            // Reset column mapping when mode changes
            columnMapping = { primary_group: null, batch_effect: null, additional_covariates: [] };
            contrasts = [];
            updateColumnMappingOptions();
            validateAll();
        });
    });

    // ── CSV Drop Zone ──
    csvDropZone.addEventListener("click", function () { csvInput.click(); });
    csvDropZone.addEventListener("dragover", function (e) {
        e.preventDefault();
        csvDropZone.classList.add("drag-over");
    });
    csvDropZone.addEventListener("dragleave", function () {
        csvDropZone.classList.remove("drag-over");
    });
    csvDropZone.addEventListener("drop", function (e) {
        e.preventDefault();
        csvDropZone.classList.remove("drag-over");
        if (e.dataTransfer.files.length > 0) setCsvFile(e.dataTransfer.files[0]);
    });
    csvInput.addEventListener("change", function () {
        if (csvInput.files.length > 0) setCsvFile(csvInput.files[0]);
    });

    /**
     * Set a CSV file and parse it in-browser with PapaParse.
     * Extracts column headers instantly for the mapping UI.
     */
    function setCsvFile(file) {
        csvFile = file;
        csvFileName.style.display = "block";
        csvFileName.innerHTML =
            '<i class="bi bi-file-earmark-check" style="color:var(--rna-accent-green);"></i> ' +
            escapeHtml(file.name);
        csvDropZone.classList.add("has-files");

        // Parse CSV in browser using PapaParse
        Papa.parse(file, {
            header: true,
            skipEmptyLines: true,
            dynamicTyping: false,
            complete: function (results) {
                if (results.errors.length > 0 && results.data.length === 0) {
                    csvPreviewArea.style.display = "block";
                    csvPreviewArea.innerHTML =
                        '<div class="validation-msg error"><i class="bi bi-exclamation-triangle"></i> ' +
                        'Could not parse CSV: ' + escapeHtml(results.errors[0].message) + '</div>';
                    parsedCsvData = null;
                    updateColumnMappingOptions();
                    validateAll();
                    return;
                }

                // Validate that the first column is named "sample"
                var firstCol = (results.meta.fields && results.meta.fields[0] || "").trim().toLowerCase();
                if (firstCol !== "sample") {
                    csvPreviewArea.style.display = "block";
                    csvPreviewArea.innerHTML =
                        '<div class="validation-msg error"><i class="bi bi-exclamation-triangle"></i> ' +
                        'The first column of the metadata CSV must be named <strong>"sample"</strong>. ' +
                        'Found: <strong>"' + escapeHtml(results.meta.fields[0] || "") + '"</strong>.</div>';
                    parsedCsvData = null;
                    updateColumnMappingOptions();
                    validateAll();
                    return;
                }

                parsedCsvData = {
                    headers: results.meta.fields,
                    rows: results.data,
                };
                renderCsvPreview();
                columnMapping = { primary_group: null, batch_effect: null, additional_covariates: [] };
                contrasts = [];
                updateColumnMappingOptions();
                validateAll();
            },
        });
    }

    /**
     * Render a compact summary in the card (no full table inside the card).
     * Shows sample match status.
     */
    function renderCsvPreview() {
        if (!parsedCsvData || !parsedCsvData.rows.length) {
            csvPreviewArea.style.display = "none";
            return;
        }

        csvPreviewArea.style.display = "block";
        var headers = parsedCsvData.headers;
        var rows = parsedCsvData.rows;
        var sampleNames = getSampleNames();
        var filteredRows = getFilteredCsvRows();

        // Check for unmatched samples (uploaded files with no metadata row)
        var unmatchedSamples = [];
        if (sampleNames.length > 0) {
            var sampleCol = headers[0];
            var metaIds = new Set();
            filteredRows.forEach(function (row) {
                metaIds.add((row[sampleCol] || "").trim());
            });
            sampleNames.forEach(function (name) {
                var stem = stripExtension(name);
                if (!metaIds.has(name) && !metaIds.has(stem)) {
                    unmatchedSamples.push(name);
                }
            });
        }

        var msgHtml;
        if (unmatchedSamples.length > 0) {
            msgHtml =
                '<div class="validation-msg error">' +
                '<i class="bi bi-exclamation-triangle"></i> ' +
                filteredRows.length + ' of ' + sampleNames.length +
                ' samples matched. <strong>Unmatched samples:</strong> ' +
                unmatchedSamples.map(escapeHtml).join(', ') +
                '. Ensure the <code>sample</code> column contains the filename stem ' +
                '(without <code>.fastq.gz</code> / <code>.fq.gz</code> extension).' +
                '</div>';
        } else if (sampleNames.length > 0) {
            msgHtml =
                '<div class="validation-msg success">' +
                '<i class="bi bi-check-circle"></i> All ' + sampleNames.length +
                ' samples matched. ' + headers.length + ' columns detected. ' +
                'See <strong>Metadata Preview</strong> below.' +
                '</div>';
        } else {
            msgHtml =
                '<div class="validation-msg success">' +
                '<i class="bi bi-check-circle"></i> Parsed: ' +
                headers.length + ' columns, ' + rows.length + ' rows. ' +
                'Upload files to verify sample matching. See <strong>Metadata Preview</strong> below.' +
                '</div>';
        }

        csvPreviewArea.innerHTML = msgHtml;

        // Render the full viewer section below the 4-card grid
        renderCsvViewer();
    }

    /**
     * Strip common FASTQ / BAM / matrix extensions from a filename to get
     * the canonical sample stem used for matching against metadata.
     */
    function stripExtension(name) {
        return name
            .replace(/\.(fq|fastq)(\.gz)?$/i, "")
            .replace(/\.(bam|cram)$/i, "")
            .replace(/\.(csv|tsv|txt)$/i, "");
    }

    /**
     * Match sample names from uploaded files against metadata CSV rows.
     * Returns filtered rows where the sample column value matches an uploaded sample.
     *
     * Matching rules (strict):
     *   - Exact match: metadata value === sample name from getSampleNames()
     *   - Stem match:  metadata value === filename stripped of extension
     *   - For paired-end FASTQ, getSampleNames() already returns the prefix
     *     (before _R1/_R2), so both the prefix and the prefix with extension
     *     stripped will be checked.
     *
     * If no files are uploaded yet, returns all rows (preview mode).
     */
    function getFilteredCsvRows() {
        if (!parsedCsvData || !parsedCsvData.rows.length) return [];

        var sampleNames = getSampleNames();
        if (!sampleNames.length) return parsedCsvData.rows;

        var sampleCol = parsedCsvData.headers[0];

        // Build a lookup set of acceptable sample identifiers
        var acceptedIds = new Set();
        sampleNames.forEach(function (name) {
            acceptedIds.add(name);
            acceptedIds.add(stripExtension(name));
        });

        return parsedCsvData.rows.filter(function (row) {
            var metaId = (row[sampleCol] || "").trim();
            if (!metaId) return false;
            return acceptedIds.has(metaId);
        });
    }

    /**
     * Render the full-width metadata viewer section below the 4-card grid.
     * Shows matched rows (10 visible, scrollable) and an info bar.
     */
    function renderCsvViewer() {
        if (!csvViewerSection) return;
        if (!parsedCsvData || !parsedCsvData.rows.length || getMetadataMode() !== "upload") {
            csvViewerSection.style.display = "none";
            return;
        }

        var filteredRows = getFilteredCsvRows();
        var totalRows = parsedCsvData.rows.length;
        var headers = parsedCsvData.headers;
        var sampleNames = getSampleNames();

        csvViewerSection.style.display = "";

        // Info bar
        var infoHtml = '<span><i class="bi bi-file-earmark-spreadsheet"></i> ' +
            escapeHtml(csvFile ? csvFile.name : "metadata.csv") + '</span>';
        if (sampleNames.length > 0) {
            infoHtml += '<span class="matched-badge"><i class="bi bi-funnel"></i> ' +
                filteredRows.length + ' matched</span>';
        }
        infoHtml += '<span class="total-badge">' + totalRows + ' total rows &middot; ' +
            headers.length + ' columns</span>';
        csvViewerInfo.innerHTML = infoHtml;

        // Table
        var html = '<table><thead><tr>';
        headers.forEach(function (h) {
            html += '<th>' + escapeHtml(h) + '</th>';
        });
        html += '</tr></thead><tbody>';

        filteredRows.forEach(function (row) {
            html += '<tr>';
            headers.forEach(function (h) {
                html += '<td title="' + escapeHtml(row[h] || '') + '">' +
                    escapeHtml(row[h] || '') + '</td>';
            });
            html += '</tr>';
        });

        if (filteredRows.length === 0) {
            html += '<tr><td colspan="' + headers.length +
                '" style="text-align:center;color:var(--rna-grey-500);font-style:italic;padding:1.5rem;">' +
                'No matching samples found. Upload files above or check that the first CSV column ' +
                'contains sample identifiers matching your filenames.</td></tr>';
        }

        html += '</tbody></table>';
        csvViewerTable.innerHTML = html;
    }

    /**
     * Upload the metadata CSV via chunked upload.
     */
    async function uploadCsvFile() {
        if (!csvFile) return true;
        await ensureSubmission();

        var totalChunks = Math.ceil(csvFile.size / CHUNK_SIZE);
        for (var i = 0; i < totalChunks; i++) {
            var start = i * CHUNK_SIZE;
            var end = Math.min(start + CHUNK_SIZE, csvFile.size);
            var chunk = csvFile.slice(start, end);

            var fd = new FormData();
            fd.append("file", chunk);
            fd.append("filename", csvFile.name);
            fd.append("chunk_index", i);
            fd.append("total_chunks", totalChunks);
            fd.append("submission_id", submissionId);
            fd.append("file_role", "METADATA_CSV");

            var res = await fetch("/api/upload/chunk", {
                method: "POST",
                headers: { "X-CSRFToken": CSRF },
                body: fd,
            });

            if (!res.ok) return false;
        }
        return true;
    }

    // ════════════════════════════════════════════════════════════
    //  6. MANUAL METADATA BUILDER (Dynamic Columns)
    // ════════════════════════════════════════════════════════════

    // Add a new column to the manual metadata table
    addColumnBtn.addEventListener("click", function () {
        var name = columnNameInput.value.trim();
        if (!name) return;
        // Prevent duplicates (case-insensitive check)
        var lower = name.toLowerCase();
        if (manualColumns.some(function (c) { return c.toLowerCase() === lower; })) return;
        manualColumns.push(name);
        columnNameInput.value = "";
        renderColumnChips();
        syncConditionTargetDropdown();
        rebuildMetadataTable();
        updateColumnMappingOptions();
        validateAll();
    });

    columnNameInput.addEventListener("keydown", function (e) {
        if (e.key === "Enter") {
            e.preventDefault();
            addColumnBtn.click();
        }
    });

    // ── Condition Value Management (per-column) ──
    if (addConditionValueBtn && conditionValueInput && conditionTargetSelect) {
        addConditionValueBtn.addEventListener("click", function () {
            var targetCol = conditionTargetSelect.value;
            if (!targetCol) return;
            var val = conditionValueInput.value.trim();
            if (!val) return;
            if (!columnSelectableValues[targetCol]) columnSelectableValues[targetCol] = [];
            var existing = columnSelectableValues[targetCol];
            if (existing.some(function (v) { return v.toLowerCase() === val.toLowerCase(); })) return;
            existing.push(val);
            conditionValueInput.value = "";
            renderConditionChips();
            rebuildMetadataTable();
            validateAll();
        });

        conditionValueInput.addEventListener("keydown", function (e) {
            if (e.key === "Enter") {
                e.preventDefault();
                addConditionValueBtn.click();
            }
        });
    }

    /**
     * Sync the condition target column dropdown with manualColumns.
     */
    function syncConditionTargetDropdown() {
        if (!conditionTargetSelect) return;
        var prev = conditionTargetSelect.value;
        conditionTargetSelect.innerHTML = "";
        manualColumns.forEach(function (c) {
            var opt = document.createElement("option");
            opt.value = c;
            opt.textContent = c;
            conditionTargetSelect.appendChild(opt);
        });
        if (manualColumns.includes(prev)) {
            conditionTargetSelect.value = prev;
        }
        // Re-render chips to match the new selection
        renderConditionChips();
    }

    // Update chips when user switches the target column dropdown
    if (conditionTargetSelect) {
        conditionTargetSelect.addEventListener("change", function () {
            renderConditionChips();
        });
    }

    function renderConditionChips() {
        conditionChipsEl.innerHTML = "";
        var selectedCol = conditionTargetSelect ? conditionTargetSelect.value : null;
        var allEntries = [];

        if (selectedCol) {
            // Show only chips for the currently selected target column
            var vals = columnSelectableValues[selectedCol] || [];
            vals.forEach(function (val, idx) {
                allEntries.push({ col: selectedCol, val: val, idx: idx });
            });
        } else {
            // Fallback: show all if no column is selected
            Object.keys(columnSelectableValues).forEach(function (col) {
                columnSelectableValues[col].forEach(function (val, idx) {
                    allEntries.push({ col: col, val: val, idx: idx });
                });
            });
        }

        allEntries.forEach(function (entry) {
            var chip = document.createElement("span");
            chip.className = "group-chip condition-chip";
            chip.innerHTML =
                escapeHtml(entry.val) +
                ' <span class="chip-col-label">(' + escapeHtml(entry.col) + ')</span>' +
                ' <span class="remove-group" data-col="' + escapeHtml(entry.col) +
                '" data-idx="' + entry.idx + '">&times;</span>';
            conditionChipsEl.appendChild(chip);
        });

        conditionChipsEl.querySelectorAll(".remove-group").forEach(function (btn) {
            btn.addEventListener("click", function () {
                var col = btn.dataset.col;
                var removedIdx = parseInt(btn.dataset.idx, 10);
                if (columnSelectableValues[col]) {
                    columnSelectableValues[col].splice(removedIdx, 1);
                    if (columnSelectableValues[col].length === 0) {
                        delete columnSelectableValues[col];
                    }
                }
                renderConditionChips();
                rebuildMetadataTable();
                validateAll();
            });
        });
    }

    function renderColumnChips() {
        columnChips.innerHTML = "";
        manualColumns.forEach(function (name, idx) {
            var chip = document.createElement("span");
            chip.className = "group-chip";
            var isProtected = name.toLowerCase() === "condition";
            if (isProtected) {
                chip.classList.add("protected");
                chip.innerHTML = escapeHtml(name);
            } else {
                chip.innerHTML =
                    escapeHtml(name) +
                    ' <span class="remove-group" data-idx="' + idx + '">&times;</span>';
            }
            columnChips.appendChild(chip);
        });

        columnChips.querySelectorAll(".remove-group").forEach(function (btn) {
            btn.addEventListener("click", function () {
                var removedIdx = parseInt(btn.dataset.idx, 10);
                var removed = manualColumns[removedIdx];
                manualColumns.splice(removedIdx, 1);
                // Clear mapping references to the removed column
                if (columnMapping.primary_group === removed) columnMapping.primary_group = null;
                if (columnMapping.batch_effect === removed) columnMapping.batch_effect = null;
                columnMapping.additional_covariates = columnMapping.additional_covariates
                    .filter(function (c) { return c !== removed; });
                // Also remove selectable values for the removed column
                delete columnSelectableValues[removed];
                renderColumnChips();
                syncConditionTargetDropdown();
                renderConditionChips();
                rebuildMetadataTable();
                updateColumnMappingOptions();
                validateAll();
            });
        });
    }

    /**
     * Extract unique sample names from uploaded/selected files.
     * Paired-end ⇒ one row per sample prefix.
     * Alignment entry ⇒ BAM filenames (sans extension).
     * Matrix entry ⇒ column headers from count matrix (excluding first col).
     */
    function getSampleNames() {
        if (inputDataType === "matrix") {
            if (parsedMatrixData && parsedMatrixData.headers.length > 1) {
                return parsedMatrixData.headers.slice(1);
            }
            return [];
        }

        if (inputDataType === "alignment") {
            var bamNames = uploadedBamFiles.length > 0
                ? uploadedBamFiles
                : selectedBamFiles.map(function (f) { return f.name; });
            return bamNames.map(function (n) {
                return n.replace(/\.(bam|cram)$/i, "");
            });
        }

        // FASTQ entry
        var names = uploadedFiles.length > 0
            ? uploadedFiles
            : selectedFiles.map(function (f) { return f.name; });

        if (getLibraryType() === "paired") {
            var prefixes = new Set();
            var re = /^(.+?)(?:_R[12]|_[12])\.(?:fq|fastq)\.gz$/i;
            names.forEach(function (n) {
                var m = re.exec(n);
                if (m) prefixes.add(m[1]);
                else prefixes.add(n);
            });
            return Array.from(prefixes);
        }

        return names;
    }

    /**
     * Rebuild the manual metadata table with dynamic columns.
     * Each sample gets a row, each user-defined column gets a text input cell.
     */
    function rebuildMetadataTable() {
        if (getMetadataMode() !== "manual") return;

        var samples = getSampleNames();
        var cols = manualColumns;

        // Build dynamic header row
        metadataHeaderRow.innerHTML = "<th>Sample Name</th>";
        cols.forEach(function (col) {
            metadataHeaderRow.innerHTML += '<th>' + escapeHtml(col) + '</th>';
        });

        // Preserve existing cell values before rebuilding
        var prevValues = {};
        metadataBody.querySelectorAll("tr").forEach(function (tr) {
            var sampleCode = tr.querySelector("code");
            if (!sampleCode) return;
            var sampleName = sampleCode.textContent;
            prevValues[sampleName] = {};
            tr.querySelectorAll(".meta-cell-input").forEach(function (inp) {
                prevValues[sampleName][inp.dataset.column] = inp.value;
            });
        });

        metadataBody.innerHTML = "";

        if (samples.length === 0) {
            noFilesHint.style.display = "";
            return;
        }
        noFilesHint.style.display = "none";

        samples.forEach(function (name) {
            var tr = document.createElement("tr");

            // Sample Name (read-only)
            var tdName = document.createElement("td");
            tdName.innerHTML = '<code style="font-size:.75rem;">' + escapeHtml(name) + '</code>';
            tr.appendChild(tdName);

            // Dynamic column cells
            cols.forEach(function (col) {
                var td = document.createElement("td");
                var selectableVals = columnSelectableValues[col];
                var hasSelectable = selectableVals && selectableVals.length > 0;

                if (hasSelectable) {
                    var select = document.createElement("select");
                    select.className = "rna-input rna-select meta-cell-input";
                    select.dataset.sample = name;
                    select.dataset.column = col;
                    var emptyOpt = document.createElement("option");
                    emptyOpt.value = "";
                    emptyOpt.textContent = "-- Select --";
                    select.appendChild(emptyOpt);
                    selectableVals.forEach(function (v) {
                        var opt = document.createElement("option");
                        opt.value = v;
                        opt.textContent = v;
                        select.appendChild(opt);
                    });
                    if (prevValues[name] && prevValues[name][col] !== undefined) {
                        select.value = prevValues[name][col];
                    }
                    select.addEventListener("change", function () {
                        updateColumnMappingOptions();
                        validateAll();
                    });
                    td.appendChild(select);
                } else {
                    var input = document.createElement("input");
                    input.type = "text";
                    input.className = "rna-input meta-cell-input";
                    input.placeholder = col;
                    input.dataset.sample = name;
                    input.dataset.column = col;
                    if (prevValues[name] && prevValues[name][col] !== undefined) {
                        input.value = prevValues[name][col];
                    }
                    input.addEventListener("input", function () {
                        updateColumnMappingOptions();
                        validateAll();
                    });
                    td.appendChild(input);
                }
                tr.appendChild(td);
            });

            metadataBody.appendChild(tr);
        });

        updateColumnMappingOptions();
    }

    // ════════════════════════════════════════════════════════════
    //  7. COLUMN ROLE ASSIGNMENT (Mapping UI)
    // ════════════════════════════════════════════════════════════

    /**
     * Get the list of mappable column names from current metadata.
     * For CSV: all headers except the first (sample ID) column.
     * For manual: all user-defined columns.
     */
    function getMetadataColumns() {
        if (getMetadataMode() === "upload" && parsedCsvData) {
            // First column is the sample identifier – skip it
            return parsedCsvData.headers.slice(1);
        }
        if (getMetadataMode() === "manual") {
            return manualColumns.slice();
        }
        return [];
    }

    /**
     * Get all metadata rows as an array of objects.
     * For CSV: directly from parsed data.
     * For manual: read from the editable table inputs.
     */
    function getMetadataRows() {
        if (getMetadataMode() === "upload" && parsedCsvData) {
            return getFilteredCsvRows();
        }
        if (getMetadataMode() === "manual") {
            var rows = [];
            metadataBody.querySelectorAll("tr").forEach(function (tr) {
                var row = {};
                var code = tr.querySelector("code");
                if (code) row["_sample_name"] = code.textContent;
                tr.querySelectorAll(".meta-cell-input").forEach(function (inp) {
                    row[inp.dataset.column] = inp.value.trim();
                });
                rows.push(row);
            });
            return rows;
        }
        return [];
    }

    /**
     * Refresh all column mapping dropdown options based on current metadata columns.
     * Called whenever metadata changes (CSV parsed, columns added/removed, table edited).
     */
    function updateColumnMappingOptions() {
        var cols = getMetadataColumns();
        if (cols.length === 0) {
            columnMappingSection.style.display = "none";
            contrastSection.style.display = "none";
            rolesContrastRow.style.display = "none";
            return;
        }
        columnMappingSection.style.display = "";
        rolesContrastRow.style.display = "";

        // ── Primary Group dropdown ──
        var prevPrimary = columnMapping.primary_group;
        primaryGroupSelect.innerHTML = '<option value="">-- Select primary group column --</option>';
        cols.forEach(function (c) {
            var opt = document.createElement("option");
            opt.value = c;
            opt.textContent = c;
            primaryGroupSelect.appendChild(opt);
        });
        if (prevPrimary && cols.includes(prevPrimary)) {
            primaryGroupSelect.value = prevPrimary;
        } else {
            columnMapping.primary_group = null;
        }

        // ── Batch Effect dropdown ──
        var prevBatch = columnMapping.batch_effect;
        batchEffectSelect.innerHTML = '<option value="">None</option>';
        cols.forEach(function (c) {
            var opt = document.createElement("option");
            opt.value = c;
            opt.textContent = c;
            batchEffectSelect.appendChild(opt);
        });
        if (prevBatch && cols.includes(prevBatch)) {
            batchEffectSelect.value = prevBatch;
        } else {
            columnMapping.batch_effect = null;
        }

        // ── Additional Covariates checkboxes ──
        covariatesList.innerHTML = "";
        cols.forEach(function (c) {
            var label = document.createElement("label");
            label.className = "covariate-check-label";
            var cb = document.createElement("input");
            cb.type = "checkbox";
            cb.value = c;
            cb.checked = columnMapping.additional_covariates.includes(c);
            cb.addEventListener("change", function () {
                syncCovariatesFromCheckboxes();
                validateColumnSelection();
                validateAll();
            });
            label.appendChild(cb);
            label.appendChild(document.createTextNode(" " + c));
            covariatesList.appendChild(label);
        });

        onPrimaryGroupChange();
        validateColumnSelection();
    }

    primaryGroupSelect.addEventListener("change", function () {
        columnMapping.primary_group = primaryGroupSelect.value || null;
        onPrimaryGroupChange();
        validateColumnSelection();
        validateAll();
    });

    batchEffectSelect.addEventListener("change", function () {
        columnMapping.batch_effect = batchEffectSelect.value || null;
        validateColumnSelection();
        validateAll();
    });

    function syncCovariatesFromCheckboxes() {
        columnMapping.additional_covariates = [];
        covariatesList.querySelectorAll("input[type=checkbox]:checked").forEach(function (cb) {
            columnMapping.additional_covariates.push(cb.value);
        });
    }

    /**
     * When the primary_group column changes, check the number of unique values.
     * If >2, show the contrast builder so users can define pairwise comparisons.
     */
    function onPrimaryGroupChange() {
        if (!columnMapping.primary_group) {
            contrastSection.style.display = "none";
            contrasts = [];
            return;
        }

        var uniqueValues = getUniqueColumnValues(columnMapping.primary_group);

        if (uniqueValues.length > 2) {
            contrastSection.style.display = "";
            // Auto-seed one contrast if empty
            if (contrasts.length === 0 && uniqueValues.length >= 2) {
                contrasts.push([uniqueValues[1], uniqueValues[0]]);
            }
            renderContrastRows(uniqueValues);
        } else {
            contrastSection.style.display = "none";
            contrasts = [];
        }
    }

    /**
     * Extract sorted unique non-empty values from a metadata column.
     */
    function getUniqueColumnValues(colName) {
        var rows = getMetadataRows();
        var values = new Set();
        rows.forEach(function (r) {
            var v = r[colName];
            if (v && String(v).trim()) values.add(String(v).trim());
        });
        return Array.from(values).sort();
    }

    /**
     * Validate all assigned columns for missing values (NAs) and zero variance.
     * Show per-column error messages.
     */
    function validateColumnSelection() {
        var msgs = [];
        var rows = getMetadataRows();
        if (rows.length === 0) {
            columnValidationMsg.style.display = "none";
            return;
        }

        // Gather all assigned columns
        var assignedCols = [];
        if (columnMapping.primary_group) assignedCols.push(columnMapping.primary_group);
        if (columnMapping.batch_effect) assignedCols.push(columnMapping.batch_effect);
        columnMapping.additional_covariates.forEach(function (c) { assignedCols.push(c); });

        assignedCols.forEach(function (col) {
            var values = rows.map(function (r) { return (r[col] || "").trim(); });
            var hasNA = values.some(function (v) {
                return v === "" || v.toUpperCase() === "NA";
            });
            var uniqueNonEmpty = new Set(values.filter(function (v) {
                return v !== "" && v.toUpperCase() !== "NA";
            }));
            var zeroVar = uniqueNonEmpty.size <= 1;

            if (hasNA) {
                msgs.push(
                    '<i class="bi bi-exclamation-triangle"></i> Column <strong>' +
                    escapeHtml(col) + '</strong> contains missing values (empty or NA).'
                );
            }
            if (zeroVar) {
                msgs.push(
                    '<i class="bi bi-exclamation-triangle"></i> Column <strong>' +
                    escapeHtml(col) + '</strong> has zero variance (only one unique value).'
                );
            }
        });

        // Check for duplicate role assignment
        var seen = new Set();
        assignedCols.forEach(function (c) {
            if (seen.has(c)) {
                msgs.push(
                    '<i class="bi bi-exclamation-triangle"></i> Column <strong>' +
                    escapeHtml(c) + '</strong> is assigned to multiple roles.'
                );
            }
            seen.add(c);
        });

        if (msgs.length > 0) {
            columnValidationMsg.className = "validation-msg error";
            columnValidationMsg.innerHTML = msgs.join("<br>");
            columnValidationMsg.style.display = "block";
        } else if (assignedCols.length > 0) {
            columnValidationMsg.className = "validation-msg success";
            columnValidationMsg.innerHTML =
                '<i class="bi bi-check-circle"></i> All selected columns pass validation.';
            columnValidationMsg.style.display = "block";
        } else {
            columnValidationMsg.style.display = "none";
        }
    }

    // ════════════════════════════════════════════════════════════
    //  8. DYNAMIC CONTRAST BUILDER
    // ════════════════════════════════════════════════════════════

    addContrastBtn.addEventListener("click", function () {
        var values = getUniqueColumnValues(columnMapping.primary_group);
        if (values.length >= 2) {
            contrasts.push([values[1] || values[0], values[0]]);
            renderContrastRows(values);
        }
    });

    /**
     * Render contrast definition rows. Each row has a Target (numerator)
     * dropdown, a "vs" label, a Reference (denominator) dropdown, and
     * a remove button.
     */
    function renderContrastRows(uniqueValues) {
        contrastList.innerHTML = "";

        contrasts.forEach(function (pair, idx) {
            var row = document.createElement("div");
            row.className = "contrast-row";

            // ── Target (numerator) dropdown ──
            var targetLabel = document.createElement("span");
            targetLabel.className = "contrast-label";
            targetLabel.textContent = "Target:";

            var targetSel = document.createElement("select");
            targetSel.className = "rna-input rna-select contrast-select";
            uniqueValues.forEach(function (v) {
                var opt = document.createElement("option");
                opt.value = v;
                opt.textContent = v;
                if (v === pair[0]) opt.selected = true;
                targetSel.appendChild(opt);
            });
            targetSel.addEventListener("change", function () {
                contrasts[idx][0] = targetSel.value;
            });

            // ── "vs" label ──
            var vsLabel = document.createElement("span");
            vsLabel.className = "contrast-vs";
            vsLabel.textContent = "vs";

            // ── Reference (denominator) dropdown ──
            var refLabel = document.createElement("span");
            refLabel.className = "contrast-label";
            refLabel.textContent = "Ref:";

            var refSel = document.createElement("select");
            refSel.className = "rna-input rna-select contrast-select";
            uniqueValues.forEach(function (v) {
                var opt = document.createElement("option");
                opt.value = v;
                opt.textContent = v;
                if (v === pair[1]) opt.selected = true;
                refSel.appendChild(opt);
            });
            refSel.addEventListener("change", function () {
                contrasts[idx][1] = refSel.value;
            });

            // ── Remove button ──
            var removeBtn = document.createElement("button");
            removeBtn.type = "button";
            removeBtn.className = "btn-rna btn-rna-danger btn-rna-sm contrast-remove";
            removeBtn.innerHTML = '<i class="bi bi-trash"></i>';
            removeBtn.addEventListener("click", function () {
                contrasts.splice(idx, 1);
                renderContrastRows(uniqueValues);
            });

            row.appendChild(targetLabel);
            row.appendChild(targetSel);
            row.appendChild(vsLabel);
            row.appendChild(refLabel);
            row.appendChild(refSel);
            row.appendChild(removeBtn);
            contrastList.appendChild(row);
        });
    }

    // ════════════════════════════════════════════════════════════
    //  9. THRESHOLD LIVE PREVIEW
    // ════════════════════════════════════════════════════════════

    function updateThresholdPreview() {
        var maxVal = Math.max(
            Math.abs(parseFloat(minLog2fc.value) || 1),
            Math.abs(parseFloat(maxLog2fc.value) || 1)
        );
        fcPreview.textContent = maxVal.toFixed(1);
        pvalPreview.textContent = parseFloat(adjPvalue.value || 0.05).toFixed(2);
    }

    adjPvalue.addEventListener("input", updateThresholdPreview);
    minLog2fc.addEventListener("input", updateThresholdPreview);
    maxLog2fc.addEventListener("input", updateThresholdPreview);

    // ════════════════════════════════════════════════════════════
    //  9b. CARD LOCK / UNLOCK (LEFT-TO-RIGHT PROGRESSION)
    // ════════════════════════════════════════════════════════════

    /**
     * Enforce left-to-right card progression:
     *   FASTQ mode:     Upload → Genome → Metadata → Thresholds
     *   Alignment mode:  Upload → Genome → Metadata → Thresholds
     *   Matrix mode:     Upload → Metadata → Thresholds  (genome hidden)
     *
     * A card is unlocked only when the previous card's required input is satisfied.
     */
    function updateCardLocks() {
        // Step 1: Is the upload column satisfied?
        var uploadDone = false;
        if (inputDataType === "fastq") {
            uploadDone = getLibraryType() !== null && selectedFiles.length > 0;
        } else if (inputDataType === "alignment") {
            uploadDone = selectedBamFiles.length > 0;
        } else {
            uploadDone = parsedMatrixData !== null && parsedMatrixData.rows.length > 0;
        }

        // Step 2: Is the genome column satisfied? (skipped in matrix mode)
        var genomeDone = inputDataType === "matrix" ? true : isGenomeValid();

        // Step 3: Is metadata satisfied?
        var metadataDone = isMetadataValid();

        // Apply lock states
        if (colGenome) colGenome.classList.toggle("locked", !uploadDone);
        if (colMetadata) colMetadata.classList.toggle("locked", !(uploadDone && genomeDone));
        if (colThresholds) colThresholds.classList.toggle("locked", !(uploadDone && genomeDone && metadataDone));
    }

    // ════════════════════════════════════════════════════════════
    //  10. FORM VALIDATION
    // ════════════════════════════════════════════════════════════

    function validateAll() {
        var isFilesValid;
        if (inputDataType === "fastq") {
            isFilesValid = selectedFiles.length > 0;
        } else if (inputDataType === "alignment") {
            isFilesValid = selectedBamFiles.length > 0;
        } else {
            isFilesValid = parsedMatrixData !== null &&
                parsedMatrixData.rows.length > 0 &&
                validateMatrixData(parsedMatrixData).length === 0;
        }

        var checks = {
            files: isFilesValid,
            metadata: isMetadataValid(),
            mapping: isMappingValid(),
        };

        // Conditional checks
        if (inputDataType === "fastq") {
            checks.library = getLibraryType() !== null;
            checks.genome = isGenomeValid();
        } else if (inputDataType === "alignment") {
            checks.genome = isGenomeValid();
        }

        setValIndicator(valFiles, checks.files);
        setValIndicator(valMetadata, checks.metadata);
        setValIndicator(valMapping, checks.mapping);

        if (inputDataType === "fastq") {
            setValIndicator(valLibrary, checks.library);
            setValIndicator(valGenome, checks.genome);
        } else if (inputDataType === "alignment") {
            setValIndicator(valGenome, checks.genome);
        }

        var allValid = Object.values(checks).every(Boolean);
        submitBtn.disabled = !allValid;

        // Update card lock/blur states (left-to-right progression)
        updateCardLocks();

        // Re-render the CSV viewer and preview whenever validation runs
        // (files/metadata may have changed affecting sample matching)
        renderCsvPreview();
    }

    function setValIndicator(el, valid) {
        el.classList.toggle("valid", valid);
        el.classList.toggle("invalid", !valid);
    }

    function isGenomeValid() {
        if (!genomeSelect.value) return false;
        if (genomeSelect.value === "custom") {
            return (
                customGenomeName.value.trim().length > 0 &&
                customGenomeFiles.fasta !== null &&
                customGenomeFiles.annotation !== null
            );
        }
        return true;
    }

    /**
     * Metadata is "valid" if data has been provided (CSV parsed or manual table populated).
     * For upload mode: every uploaded sample must have a matching row in the CSV.
     * For matrix entry: additionally cross-validate sample names in metadata vs count matrix.
     */
    function isMetadataValid() {
        var mode = getMetadataMode();
        if (mode === "upload") {
            if (!parsedCsvData || parsedCsvData.rows.length === 0) return false;

            var sampleNames = getSampleNames();
            if (sampleNames.length > 0) {
                var filteredRows = getFilteredCsvRows();
                var sampleCol = parsedCsvData.headers[0];
                var metaIds = new Set();
                filteredRows.forEach(function (row) {
                    metaIds.add((row[sampleCol] || "").trim());
                });
                // Every uploaded sample must have a metadata row
                var allMatched = sampleNames.every(function (name) {
                    return metaIds.has(name) || metaIds.has(stripExtension(name));
                });
                if (!allMatched) return false;
            }

            // For matrix entry: each metadata row's sample ID must appear in count matrix columns
            if (inputDataType === "matrix" && parsedMatrixData) {
                var matrixSamples = new Set(parsedMatrixData.headers.slice(1));
                var col = parsedCsvData.headers[0];
                var allFound = parsedCsvData.rows.every(function (row) {
                    return matrixSamples.has((row[col] || "").trim());
                });
                if (!allFound) return false;
            }
            return true;
        }
        // Manual mode: need at least one column and table cells with data
        if (manualColumns.length === 0) return false;
        var rows = metadataBody.querySelectorAll("tr");
        if (rows.length === 0) {
            if (inputDataType === "fastq") return selectedFiles.length === 0;
            if (inputDataType === "alignment") return selectedBamFiles.length === 0;
            if (inputDataType === "matrix") return !parsedMatrixData;
            return true;
        }
        var hasValue = false;
        rows.forEach(function (tr) {
            tr.querySelectorAll(".meta-cell-input").forEach(function (inp) {
                if (inp.value.trim()) hasValue = true;
            });
        });
        return hasValue;
    }

    /**
     * Column mapping is "valid" when:
     *  1. A primary_group column is assigned.
     *  2. No assigned column has NAs or zero variance.
     *  3. No column is assigned to multiple roles.
     *  4. If >2 groups, at least one contrast is defined.
     */
    function isMappingValid() {
        if (!columnMapping.primary_group) return false;

        // Collect all assigned columns and check for overlapping roles
        var assigned = [columnMapping.primary_group];
        if (columnMapping.batch_effect) {
            if (assigned.includes(columnMapping.batch_effect)) return false;
            assigned.push(columnMapping.batch_effect);
        }
        for (var i = 0; i < columnMapping.additional_covariates.length; i++) {
            var c = columnMapping.additional_covariates[i];
            if (assigned.includes(c)) return false;
            assigned.push(c);
        }

        // Validate each assigned column for NAs and zero variance
        var rows = getMetadataRows();
        if (rows.length === 0) return false;

        for (var j = 0; j < assigned.length; j++) {
            var col = assigned[j];
            var values = rows.map(function (r) { return (r[col] || "").trim(); });
            var hasNA = values.some(function (v) {
                return v === "" || v.toUpperCase() === "NA";
            });
            if (hasNA) return false;
            var unique = new Set(values.filter(function (v) {
                return v !== "" && v.toUpperCase() !== "NA";
            }));
            if (unique.size <= 1) return false;
        }

        // If primary_group has >2 levels, at least one contrast must be defined
        var uniqueGroups = getUniqueColumnValues(columnMapping.primary_group);
        if (uniqueGroups.length > 2 && contrasts.length === 0) return false;

        // Each contrast must have distinct target and reference
        for (var k = 0; k < contrasts.length; k++) {
            if (contrasts[k][0] === contrasts[k][1]) return false;
        }

        return true;
    }

    // ════════════════════════════════════════════════════════════
    //  11. PIPELINE SUBMISSION
    // ════════════════════════════════════════════════════════════

    submitBtn.addEventListener("click", async function () {
        submitBtn.disabled = true;
        submitBtn.innerHTML = '<i class="bi bi-arrow-repeat rna-processing"></i> Uploading files...';

        try {
            // 1. Upload data files based on entry point
            if (inputDataType === "fastq") {
                var fastqOk = await uploadFastqFiles();
                if (!fastqOk) { resetSubmitBtn(); return; }
            } else if (inputDataType === "alignment") {
                var bamOk = await uploadBamFiles();
                if (!bamOk) { resetSubmitBtn(); return; }
            } else if (inputDataType === "matrix") {
                var matrixOk = await uploadMatrixFile();
                if (!matrixOk) { resetSubmitBtn(); return; }
            }

            // 2. Upload custom genome if needed (fastq & alignment only)
            if (inputDataType !== "matrix" && genomeSelect.value === "custom") {
                submitBtn.innerHTML = '<i class="bi bi-arrow-repeat rna-processing"></i> Uploading genome...';
                var genomeOk = await uploadCustomGenome();
                if (!genomeOk) { resetSubmitBtn(); return; }
            }

            // 3. CSV metadata is parsed client-side by PapaParse and included
            //    in the metadata_payload — no file upload needed.

            // 4. Build and send the pipeline payload
            submitBtn.innerHTML = '<i class="bi bi-arrow-repeat rna-processing"></i> Starting pipeline...';

            var payload = {
                submission_id: submissionId,
                input_data_type: inputDataType,
                metadata_mode: getMetadataMode(),
                adjusted_pvalue: parseFloat(adjPvalue.value) || 0.05,
                min_log2fc: parseFloat(minLog2fc.value) || -1.0,
                max_log2fc: parseFloat(maxLog2fc.value) || 1.0,
                metadata_payload: buildMetadataPayload(),
            };

            // Add fields specific to entry points
            if (inputDataType === "fastq") {
                payload.library_type = getLibraryType();
                payload.strandedness = document.getElementById("strandedness").value;
                payload.reference_genome = genomeSelect.value;
                payload.quant_level = quantLevel.value;
                payload.assay_type = assayType;
                if (genomeSelect.value === "custom") {
                    payload.custom_genome_name = customGenomeName.value.trim();
                }
            } else if (inputDataType === "alignment") {
                var alignLibChecked = document.querySelector('input[name="library_type_alignment"]:checked');
                payload.library_type = alignLibChecked ? alignLibChecked.value : "single";
                payload.strandedness = strandednessAlignment.value;
                payload.reference_genome = genomeSelect.value;
                payload.quant_level = quantLevel.value;
                if (genomeSelect.value === "custom") {
                    payload.custom_genome_name = customGenomeName.value.trim();
                }
            }
            // matrix entry: no library_type, strandedness, genome needed

            var res = await fetch("/api/pipeline/core", {
                method: "POST",
                headers: {
                    "Content-Type": "application/json",
                    "X-CSRFToken": CSRF,
                },
                body: JSON.stringify(payload),
            });

            if (res.ok) {
                var data = await res.json();
                window.location.href = "/processing/" + data.job_id + "/";
            } else {
                var errData = null;
                try { errData = await res.json(); } catch (e) { /* ignore */ }
                var errMsg = (errData && errData.error) ? errData.error : "Pipeline submission failed.";
                alert(errMsg);
                resetSubmitBtn();
            }
        } catch (err) {
            alert("Network error: " + err.message);
            resetSubmitBtn();
        }
    });

    function resetSubmitBtn() {
        submitBtn.disabled = false;
        submitBtn.innerHTML = '<i class="bi bi-rocket-takeoff"></i> Start Pipeline';
    }

    /**
     * Build the complete metadata payload for the backend.
     * Includes sample data, column mapping, and contrasts.
     */
    function buildMetadataPayload() {
        var mode = getMetadataMode();
        var samples;

        if (mode === "upload" && parsedCsvData) {
            samples = getFilteredCsvRows();
        } else {
            samples = getMetadataRows();
        }

        return {
            samples: samples,
            column_mapping: {
                primary_group: columnMapping.primary_group,
                batch_effect: columnMapping.batch_effect || null,
                additional_covariates: columnMapping.additional_covariates.slice(),
            },
            contrasts: contrasts.length > 0 ? contrasts.map(function (c) { return c.slice(); }) : [],
        };
    }

    // ════════════════════════════════════════════════════════════
    //  12. UTILITIES
    // ════════════════════════════════════════════════════════════

    function escapeHtml(str) {
        var div = document.createElement("div");
        div.appendChild(document.createTextNode(str));
        return div.innerHTML;
    }

    // ── Initialize ──
    updateThresholdPreview();
    renderColumnChips();
    syncConditionTargetDropdown();
    renderConditionChips();
    applyEntryPointVisibility();
    updateCardLocks();
    updateAssayHelpText();
})();
