/**
 * RNAseek – Core Hub
 * Master-Detail module UI with state machine, D&D uploads,
 * tab navigation, deconvolution gateway, and plot rendering.
 */
(function () {
    "use strict";

    const CSRF = document.querySelector('meta[name="csrf-token"]').content;
    const hubData = document.getElementById("core-hub-data");
    const JOB_ID = hubData.dataset.jobId;
    const SUBMISSION_ID = hubData.dataset.submissionId || "";

    // Parse server-provided module job history (arrays per module)
    var moduleJobsRaw = {};
    try { moduleJobsRaw = JSON.parse(hubData.dataset.moduleJobs || "{}"); } catch (_) { }

    // Parse BAM file names available in this submission's Hub
    var BAM_FILES = [];
    try { BAM_FILES = JSON.parse(hubData.dataset.bamFiles || "[]"); } catch (_) { }

    // Parse sample IDs from Stage 1 metadata for module forms (e.g. Time Series)
    var SAMPLE_IDS = [];
    try { SAMPLE_IDS = JSON.parse(hubData.dataset.sampleIds || "[]"); } catch (_) { }

    // ── Tab Navigation ──
    var tabs = document.querySelectorAll(".rna-tab");
    var panels = document.querySelectorAll(".rna-tab-panel");

    tabs.forEach(function (tab) {
        tab.addEventListener("click", function () {
            var target = tab.dataset.tab;
            tabs.forEach(function (t) { t.classList.remove("active"); });
            panels.forEach(function (p) { p.classList.remove("active"); });
            tab.classList.add("active");
            document.getElementById("tab-" + target).classList.add("active");

            if (target === "overview" && typeof Plotly !== "undefined") {
                var activePane = document.querySelector(".viz-theater-pane.active");
                if (activePane) {
                    var plotEl = activePane.querySelector(".rna-plot-container");
                    if (plotEl && plotEl.data) Plotly.Plots.resize(plotEl);
                }
            }
        });
    });

    // ═══════════════════════════════════════════════════════════════════
    // MODULE HUB – Master-Detail State Machine
    // ═══════════════════════════════════════════════════════════════════

    var ModuleHub = {
        selectedModule: null,
        detailView: "empty",       // "empty" | "history" | "form" | "result"
        moduleHistory: {},          // { MODULE_NAME: [ {job_id, status, payload, ...}, ... ] }
        activePolls: {},            // { job_id: intervalId }
        pendingFiles: {},           // { dropzoneId: File }

        // ── Initialize ──
        init: function () {
            this.parseInitialData();
            this.applyMasterBadges();
            this.bindMasterPane();
            this.startActivePolls();
        },

        parseInitialData: function () {
            for (var mod in moduleJobsRaw) {
                var raw = moduleJobsRaw[mod];
                this.moduleHistory[mod] = Array.isArray(raw) ? raw : [raw];
            }
        },

        // ── Master Pane Badges ──
        applyMasterBadges: function () {
            var self = this;
            document.querySelectorAll(".md-module-item[data-module]").forEach(function (item) {
                var mod = item.dataset.module;
                var badge = item.querySelector(".md-module-badge");
                if (!badge) return;
                var history = self.moduleHistory[mod];
                if (!history || history.length === 0) return;
                var latest = history[0];
                self.setBadge(badge, latest.status);
            });
        },

        setBadge: function (badge, status) {
            badge.className = "md-module-badge";
            if (status === "SUCCESS") {
                badge.classList.add("badge-done");
                badge.textContent = "Done";
            } else if (status === "RUNNING" || status === "PENDING") {
                badge.classList.add("badge-running");
                badge.textContent = "Running";
            } else if (status === "FAILED") {
                badge.classList.add("badge-failed");
                badge.textContent = "Failed";
            }
        },

        bindMasterPane: function () {
            var self = this;
            document.querySelectorAll(".md-module-item[data-module]").forEach(function (item) {
                item.addEventListener("click", function () {
                    self.selectModule(item.dataset.module);
                });
            });
        },

        // ── Module Selection (Step 1 & 2) ──
        selectModule: function (moduleName) {
            this.selectedModule = moduleName;
            this.highlightMaster(moduleName);
            var history = this.moduleHistory[moduleName];
            if (history && history.length > 0) {
                this.showHistoryList(moduleName);
            } else {
                this.showNewRunForm(moduleName);
            }
        },

        highlightMaster: function (moduleName) {
            document.querySelectorAll(".md-module-item").forEach(function (item) {
                item.classList.toggle("active", item.dataset.module === moduleName);
            });
        },

        // ── Render Helpers ──
        setDetail: function (html) {
            var empty = document.getElementById("md-empty-state");
            var content = document.getElementById("md-detail-content");
            empty.style.display = "none";
            content.style.display = "";
            content.innerHTML = html;
        },

        getModuleTitle: function (mod) {
            var item = document.querySelector('.md-module-item[data-module="' + mod + '"]');
            return item ? item.querySelector("h4").textContent : mod;
        },

        getModuleDescription: function (mod) {
            var descs = {
                SPLICING: 'Powered by IsoformSwitchAnalyzeR, this module maps condition data to your aligned BAMs and reference GTF to uncover alternative splicing events.',
                RNA_EDITING: 'Utilizing REDItools2, this tool scans your aligned BAM files against the reference genome to detect whole-transcriptome or localized RNA editing events (like A-to-I).',
                TIME_SERIES: 'Using ImpulseDE2 on normalized expression data, this tracks and identifies dynamic gene regulatory patterns across specific timepoints.',
                WGCNA: 'Leveraging PyWGCNA, it clusters normalized expression data into co-expression modules and correlates them with clinical traits based on a chosen soft-power threshold.',
                PATHWAY: 'Powered by gseapy, it identifies enriched gene sets (e.g., Hallmark, KEGG, GO, Reactome) from your final DEG table using an FDR threshold.',
                NETWORKS: 'Built on arboreto (GRNBoost2), it infers regulatory networks from normalized expression, integrating STRING confidence thresholds for targeted transcription factors.',
                LIT_MINING: 'Connects your final DEG table to context keywords via the INDRA Bio API to mine known biomedical relationships from PubMed.',
                SURVIVAL: 'Uses the lifelines package to run Kaplan-Meier survival analysis, comparing specific genes of interest against clinical survival data.',
                TCGA: 'Queries TCGAbiolinks to place your normalized expression data in the context of specific public TCGA cohorts (like BRCA or LUAD).',
                BIOMARKER: 'Queries the MarkerDB API with your final DEG table and disease context to identify potential clinical biomarkers.',
                MOFA: 'Uses mofapy2 on normalized expression and secondary omics matrices to extract multi-omics factors of variation.',
                DIABLO: 'Applies mixOmics supervised integration to link your normalized expression to a secondary omics matrix over specified components to find discriminative multi-omics signatures.'
            };
            return descs[mod] || '';
        },

        // ── History List View (Step 2b) ──
        showHistoryList: function (moduleName) {
            var self = this;
            this.detailView = "history";
            var title = this.getModuleTitle(moduleName);
            var history = this.moduleHistory[moduleName] || [];

            var html = '<div class="md-detail-header"><h3>' + this.escHtml(title) + ' — Run History</h3></div>';
            html += '<div class="md-detail-body">';
            html += '<div class="md-history-toolbar">';
            html += '<h4>' + history.length + ' run' + (history.length !== 1 ? 's' : '') + '</h4>';
            html += '<button class="btn-rna btn-rna-primary btn-rna-sm" id="md-new-run-btn">';
            html += '<i class="bi bi-plus-circle"></i> New Run</button></div>';
            html += '<div class="md-history-list">';

            for (var i = 0; i < history.length; i++) {
                var entry = history[i];
                var statusClass = "entry-" + (entry.status === "SUCCESS" ? "completed" : entry.status === "FAILED" ? "failed" : "running");
                var badgeClass = entry.status === "SUCCESS" ? "hb-completed" : entry.status === "FAILED" ? "hb-failed" : entry.status === "RUNNING" ? "hb-running" : "hb-pending";
                var badgeLabel = entry.status === "SUCCESS" ? "Completed" : entry.status === "FAILED" ? "Failed" : entry.status === "RUNNING" ? "Processing" : "Pending";
                var dateStr = entry.created_at ? new Date(entry.created_at).toLocaleString() : (entry.updated_at ? new Date(entry.updated_at).toLocaleString() : "Unknown");
                var clickable = entry.status === "SUCCESS" ? ' data-view-result="' + i + '"' : '';

                html += '<div class="md-history-entry ' + statusClass + '"' + clickable + '>';
                html += '<div class="md-history-entry-info">';
                html += '<div class="entry-label">Run #' + (history.length - i) + (entry.job_id ? ' &middot; ' + entry.job_id.substring(0, 8) : '') + '</div>';
                html += '<div class="entry-date">' + this.escHtml(dateStr) + '</div>';
                html += '</div>';
                html += '<span class="md-history-badge ' + badgeClass + '">' + badgeLabel + '</span>';
                html += '</div>';
            }

            html += '</div></div>';
            this.setDetail(html);

            // Bind events
            var newRunBtn = document.getElementById("md-new-run-btn");
            if (newRunBtn) {
                newRunBtn.addEventListener("click", function () {
                    self.showNewRunForm(moduleName);
                });
            }
            document.querySelectorAll("[data-view-result]").forEach(function (el) {
                el.addEventListener("click", function () {
                    var idx = parseInt(el.dataset.viewResult);
                    self.showResult(moduleName, history[idx]);
                });
            });
        },

        // ── New Run Form (Step 3) ──
        showNewRunForm: function (moduleName) {
            var self = this;
            this.detailView = "form";
            this.pendingFiles = {};
            var title = this.getModuleTitle(moduleName);
            var hasHistory = (this.moduleHistory[moduleName] || []).length > 0;

            var html = '<div class="md-detail-header"><h3>' + this.escHtml(title) + ' — New Run</h3></div>';
            html += '<div class="md-detail-body">';

            if (hasHistory) {
                html += '<button class="btn-rna-back" id="md-back-to-history">';
                html += '<i class="bi bi-arrow-left"></i> Back to History</button>';
                html += '<div style="margin-top:.75rem;"></div>';
            }

            var desc = this.getModuleDescription(moduleName);
            if (desc) {
                html += '<p class="rna-text-sm rna-text-muted md-module-desc" style="margin:0 0 1.25rem; line-height:1.6;">' + desc + '</p>';
            }

            html += this.buildForm(moduleName);

            html += '<div class="md-form-footer">';
            html += '<button class="btn-rna btn-rna-primary btn-rna-sm" id="md-submit-run">';
            html += '<i class="bi bi-play-circle"></i> Run Module</button>';
            html += '</div></div>';

            this.setDetail(html);
            this.bindFormInteractions(moduleName);

            // Bind back button
            var backBtn = document.getElementById("md-back-to-history");
            if (backBtn) {
                backBtn.addEventListener("click", function () {
                    self.showHistoryList(moduleName);
                });
            }

            // Bind submit
            var submitBtn = document.getElementById("md-submit-run");
            submitBtn.addEventListener("click", function () {
                submitBtn.disabled = true;
                submitBtn.innerHTML = '<i class="bi bi-arrow-repeat rna-processing"></i> Submitting...';
                self.submitRun(moduleName).then(function () {
                    // submission handler manages the view transition
                }).catch(function () {
                    submitBtn.disabled = false;
                    submitBtn.innerHTML = '<i class="bi bi-play-circle"></i> Run Module';
                });
            });
        },

        // ── Form Builders per Module ──
        buildForm: function (mod) {
            switch (mod) {
                case "SPLICING": return this.formSplicing();
                case "RNA_EDITING": return this.formRnaEditing();
                case "TIME_SERIES": return this.formTimeSeries();
                case "WGCNA": return this.formWgcna();
                case "PATHWAY": return this.formPathway();
                case "NETWORKS": return this.formNetworks();
                case "LIT_MINING": return this.formLitMining();
                case "SURVIVAL": return this.formSurvival();
                case "TCGA": return this.formTcga();
                case "BIOMARKER": return this.formBiomarker();
                case "MOFA": return this.formMofa();
                case "DIABLO": return this.formDiablo();
                default: return '<p class="rna-text-sm rna-text-muted">No configuration needed. Click Run to start.</p>';
            }
        },

        formSplicing: function () {
            var html = '';

            // ── Input mode toggle (radio-card style) ──
            html += '<div class="md-form-section">';
            html += '<label class="rna-label">Input Mode</label>';
            html += '<div class="splicing-mode-group" id="splicing-mode-group">';
            html += '<label class="splicing-mode-card active" data-mode="manual">';
            html += '<input type="radio" name="splicing_mode" value="manual" checked>';
            html += '<div class="splicing-mode-icon"><i class="bi bi-table"></i></div>';
            html += '<div class="splicing-mode-content">';
            html += '<span class="splicing-mode-title">Manual Edit</span>';
            html += '<span class="splicing-mode-desc">Assign conditions to BAM files interactively</span>';
            html += '</div></label>';
            html += '<label class="splicing-mode-card" data-mode="csv">';
            html += '<input type="radio" name="splicing_mode" value="csv">';
            html += '<div class="splicing-mode-icon"><i class="bi bi-file-earmark-spreadsheet"></i></div>';
            html += '<div class="splicing-mode-content">';
            html += '<span class="splicing-mode-title">Upload CSV</span>';
            html += '<span class="splicing-mode-desc">CSV with <code>file_name</code> and <code>condition</code> columns</span>';
            html += '</div></label>';
            html += '</div></div>';

            // ── Mode A: CSV Upload ──
            html += '<div class="md-form-section splicing-panel" id="splicing-csv-panel" style="display:none;">';
            html += '<label class="rna-label">Condition CSV</label>';
            html += '<p class="rna-text-sm rna-text-muted" style="margin:0 0 .5rem;">CSV must contain two columns: <strong>file_name</strong> (matching your BAM files) and <strong>condition</strong> (e.g. Tumor, Normal).</p>';
            html += this.buildDropzone("splicing-csv", ".csv,.tsv", "Drop a condition CSV here or click to browse");

            // Downloadable template CSV pre-populated with actual BAM names
            if (BAM_FILES.length > 0) {
                html += '<div style="margin-top:.75rem; display:flex; align-items:center; gap:.75rem; justify-content:flex-end;">';
                html += '<span class="rna-text-sm rna-text-muted">Pre-filled with your ' + BAM_FILES.length + ' BAM file(s) — just edit the <strong>condition</strong> column.</span>';
                html += '<button class="btn-rna btn-rna-teal btn-rna-sm" id="splicing-download-template">';
                html += '<i class="bi bi-download"></i> Download Template CSV</button>';
                html += '</div>';
            }

            html += '<div class="md-form-section" style="margin-top:.75rem;">';
            html += '<p class="rna-text-sm rna-text-muted" style="margin:0 0 .35rem;">Expected format:</p>';
            html += '<table class="md-example-table"><thead><tr><th>file_name</th><th>condition</th></tr></thead>';
            html += '<tbody>';
            if (BAM_FILES.length > 0) {
                for (var k = 0; k < Math.min(BAM_FILES.length, 4); k++) {
                    html += '<tr><td>' + this.escHtml(BAM_FILES[k]) + '</td>';
                    html += '<td class="rna-text-muted"><em>EDIT_ME</em></td></tr>';
                }
                if (BAM_FILES.length > 4) {
                    html += '<tr><td colspan="2" class="rna-text-muted" style="text-align:center;">... ' + (BAM_FILES.length - 4) + ' more file(s)</td></tr>';
                }
            } else {
                html += '<tr><td>sample_01.bam</td><td>Tumor</td></tr>';
                html += '<tr><td>sample_02.bam</td><td>Normal</td></tr>';
            }
            html += '</tbody></table>';
            html += '</div></div>';

            // ── Mode B: Manual Builder ──
            html += '<div class="md-form-section splicing-panel" id="splicing-manual-panel">';
            html += '<label class="rna-label">Assign Conditions to BAM Files</label>';
            if (BAM_FILES.length === 0) {
                html += '<p class="rna-text-sm rna-text-muted">No aligned BAM files found in this submission. Run the core pipeline first or switch to CSV upload mode.</p>';
            } else {
                html += '<p class="rna-text-sm rna-text-muted" style="margin:0 0 .5rem;">Assign an experimental condition (e.g. Treated, Control) to each BAM file.</p>';
                html += '<div class="md-builder-table-wrap">';
                html += '<table class="md-builder-table" id="splicing-bam-table">';
                html += '<thead><tr><th>BAM File</th><th>Condition</th></tr></thead>';
                html += '<tbody>';
                for (var i = 0; i < BAM_FILES.length; i++) {
                    html += '<tr>';
                    html += '<td class="splicing-bam-name">' + this.escHtml(BAM_FILES[i]) + '</td>';
                    html += '<td><input type="text" class="rna-input splicing-condition-input" placeholder="e.g. Treated" data-bam="' + this.escAttr(BAM_FILES[i]) + '"></td>';
                    html += '</tr>';
                }
                html += '</tbody></table></div>';
            }
            html += '</div>';

            return html;
        },

        formRnaEditing: function () {
            return '<div class="md-form-section">' +
                '<label style="display:flex; align-items:center; gap:.5rem; cursor:pointer;">' +
                '<input type="checkbox" id="mod-whole-txome"> ' +
                '<span class="rna-label" style="margin:0;">Whole Transcriptome (skip BED file)</span></label>' +
                '</div>' +
                '<div class="md-form-section" id="bed-upload-section">' +
                '<label class="rna-label">Target Regions (BED file)</label>' +
                this.buildDropzone("bed-file", ".bed", "Drop a BED file here or click to browse") +
                '<div style="margin-top:.75rem;">' +
                '<p class="rna-text-sm rna-text-muted" style="margin:0 0 .35rem;">Example BED format (tab-separated, 0-based coordinates):</p>' +
                '<table class="md-example-table"><thead><tr>' +
                '<th>chrom</th><th>chromStart</th><th>chromEnd</th><th>name</th></tr></thead>' +
                '<tbody>' +
                '<tr><td>chr1</td><td>1267700</td><td>1268200</td><td>ADAR_target_1</td></tr>' +
                '<tr><td>chr1</td><td>6529400</td><td>6529900</td><td>ADAR_target_2</td></tr>' +
                '<tr><td>chr12</td><td>57480100</td><td>57480600</td><td>APOBEC1_site</td></tr>' +
                '</tbody></table>' +
                '</div>' +
                '</div>';
        },

        formTimeSeries: function () {
            var html = '';

            // ── Time Unit (button group styled like splicing-mode-cards) ──
            html += '<div class="md-form-section">';
            html += '<label class="rna-label">Time Unit</label>';
            html += '<input type="hidden" id="mod-time-unit" value="hours">';
            html += '<div class="splicing-mode-group" id="time-unit-group" style="grid-template-columns:1fr 1fr 1fr;">';
            html += '<label class="splicing-mode-card" data-unit="minutes">';
            html += '<div class="splicing-mode-icon"><i class="bi bi-stopwatch"></i></div>';
            html += '<div class="splicing-mode-content">';
            html += '<span class="splicing-mode-title">Minutes</span>';
            html += '</div></label>';
            html += '<label class="splicing-mode-card active" data-unit="hours">';
            html += '<div class="splicing-mode-icon"><i class="bi bi-clock"></i></div>';
            html += '<div class="splicing-mode-content">';
            html += '<span class="splicing-mode-title">Hours</span>';
            html += '</div></label>';
            html += '<label class="splicing-mode-card" data-unit="days">';
            html += '<div class="splicing-mode-icon"><i class="bi bi-calendar3"></i></div>';
            html += '<div class="splicing-mode-content">';
            html += '<span class="splicing-mode-title">Days</span>';
            html += '</div></label>';
            html += '</div></div>';

            // ── Input mode tabs ──
            html += '<div class="md-form-section">';
            html += '<label class="rna-label">Sample &rarr; Timepoint &amp; Condition Mapping</label>';
            html += '<p class="rna-text-sm rna-text-muted" style="margin:0 0 .5rem;">';
            html += 'Map each sample to a numeric timepoint and an experimental condition. ';
            html += 'If you have a single condition (simple longitudinal), leave Condition as <strong>Control</strong> for all rows.';
            html += '</p>';

            html += '<div class="md-data-tabs" data-tab-group="ts-mapping">';
            html += '<button class="md-data-tab" data-dtab="upload">Upload CSV</button>';
            html += '<button class="md-data-tab active" data-dtab="builder">Manual Builder</button>';
            html += '</div>';

            // ── Tab 1: Upload CSV ──
            html += '<div class="md-data-panel" data-dtpanel="upload" data-dtgroup="ts-mapping">';
            html += '<p class="rna-text-sm rna-text-muted" style="margin:0 0 .5rem;">CSV must contain columns: <strong>Sample_ID</strong>, <strong>Timepoint</strong>, <strong>Condition</strong>.</p>';
            html += this.buildDropzone("ts-mapping-csv", ".csv,.tsv", "Drop a time-series mapping CSV here or click to browse");
            html += '<div class="md-form-section" style="margin-top:.75rem;">';
            html += '<p class="rna-text-sm rna-text-muted" style="margin:0 0 .35rem;">Expected CSV format:</p>';
            html += '<table class="md-example-table"><thead><tr><th>Sample_ID</th><th>Timepoint</th><th>Condition</th></tr></thead>';
            html += '<tbody>';
            if (SAMPLE_IDS.length > 0) {
                for (var k = 0; k < Math.min(SAMPLE_IDS.length, 4); k++) {
                    html += '<tr><td>' + this.escHtml(SAMPLE_IDS[k]) + '</td>';
                    html += '<td class="rna-text-muted"><em>0</em></td>';
                    html += '<td class="rna-text-muted"><em>Control</em></td></tr>';
                }
                if (SAMPLE_IDS.length > 4) {
                    html += '<tr><td colspan="3" class="rna-text-muted" style="text-align:center;">... ' + (SAMPLE_IDS.length - 4) + ' more sample(s)</td></tr>';
                }
            } else {
                html += '<tr><td>Sample_1</td><td>0</td><td>Control</td></tr>';
                html += '<tr><td>Sample_2</td><td>6</td><td>Control</td></tr>';
                html += '<tr><td>Sample_3</td><td>12</td><td>Treatment</td></tr>';
            }
            html += '</tbody></table></div>';

            // Download template CSV pre-populated with sample IDs
            if (SAMPLE_IDS.length > 0) {
                html += '<div style="margin-top:.75rem; display:flex; align-items:center; gap:.75rem; justify-content:flex-end;">';
                html += '<span class="rna-text-sm rna-text-muted">Pre-filled with your ' + SAMPLE_IDS.length + ' sample(s).</span>';
                html += '<button class="btn-rna btn-rna-teal btn-rna-sm" id="ts-download-template">';
                html += '<i class="bi bi-download"></i> Download Template CSV</button>';
                html += '</div>';
            }
            html += '</div>';

            // ── Tab 2: Manual Builder (pre-populated with sample IDs) ──
            html += '<div class="md-data-panel active" data-dtpanel="builder" data-dtgroup="ts-mapping">';
            html += '<div class="md-builder-table-wrap">';
            html += '<table class="md-builder-table" id="ts-builder-table">';
            html += '<thead><tr><th>Sample_ID</th><th>Timepoint</th><th>Condition</th>';
            html += '<th class="row-delete"></th></tr></thead><tbody>';

            if (SAMPLE_IDS.length > 0) {
                for (var i = 0; i < SAMPLE_IDS.length; i++) {
                    html += '<tr>';
                    html += '<td><input type="text" value="' + this.escAttr(SAMPLE_IDS[i]) + '" readonly style="background:#f8f9fa; color:#555;"></td>';
                    html += '<td><input type="number" placeholder="e.g. 0" step="any"></td>';
                    html += '<td><input type="text" placeholder="e.g. Control"></td>';
                    html += '<td class="row-delete"><button title="Remove row">&times;</button></td>';
                    html += '</tr>';
                }
            } else {
                for (var j = 0; j < 3; j++) {
                    html += '<tr>';
                    html += '<td><input type="text" placeholder="Sample_ID"></td>';
                    html += '<td><input type="number" placeholder="e.g. 0" step="any"></td>';
                    html += '<td><input type="text" placeholder="e.g. Control"></td>';
                    html += '<td class="row-delete"><button title="Remove row">&times;</button></td>';
                    html += '</tr>';
                }
            }
            html += '</tbody></table></div>';
            html += '<div class="md-builder-controls" style="margin-top:.5rem;">';
            html += '<button class="md-builder-btn" id="ts-add-row"><i class="bi bi-plus"></i> Add Row</button>';
            html += '</div>';
            html += '</div>';

            html += '</div>';
            return html;
        },

        formWgcna: function () {
            return '<div class="md-form-section">' +
                '<label class="rna-label">Soft-Power Threshold</label>' +
                '<input type="number" class="rna-input" id="mod-soft-power" value="6" min="1" max="30">' +
                '</div>' +
                '<div class="md-form-section">' +
                '<label class="rna-label">Clinical Traits</label>' +
                this.buildDataInputTabs("wgcna-traits",
                    ["Sample_ID", "Trait_1", "Trait_2"],
                    [["Sample_1", "1.5", "0"], ["Sample_2", "3.2", "1"], ["Sample_3", "0.8", "1"]]) +
                '</div>';
        },

        formPathway: function () {
            return '<div class="md-form-section">' +
                '<label class="rna-label">Gene Set Database</label>' +
                '<select class="rna-input rna-select" id="mod-geneset">' +
                '<option value="hallmark">MSigDB Hallmark (H)</option>' +
                '<option value="c2_kegg">C2 KEGG</option>' +
                '<option value="c5_go_bp">C5 GO Biological Process</option>' +
                '<option value="c5_go_mf">C5 GO Molecular Function</option>' +
                '<option value="reactome">Reactome</option>' +
                '<option value="pathbank">PathBank</option>' +
                '</select></div>' +
                '<div class="md-form-section">' +
                '<label class="rna-label">FDR Threshold</label>' +
                '<input type="number" class="rna-input" id="mod-fdr" value="0.25" step="0.05" min="0" max="1">' +
                '</div>';
        },

        formNetworks: function () {
            return '<div class="md-form-section">' +
                '<label class="rna-label">Transcription Factors (one per line)</label>' +
                '<textarea class="rna-input" id="mod-tf-list" rows="4" placeholder="TP53&#10;MYC&#10;BRCA1"></textarea>' +
                '</div>' +
                '<div class="md-form-section">' +
                '<label class="rna-label">STRING Confidence Threshold</label>' +
                '<input type="number" class="rna-input" id="mod-confidence" value="0.7" step="0.05" min="0.15" max="1">' +
                '</div>';
        },

        formLitMining: function () {
            return '<div class="md-form-section">' +
                '<label class="rna-label">Context Keywords</label>' +
                '<input type="text" class="rna-input" id="mod-keywords" placeholder="e.g. apoptosis, p53 pathway, breast cancer">' +
                '</div>';
        },

        formSurvival: function () {
            return '<div class="md-form-section">' +
                '<label class="rna-label">Genes of Interest (comma-separated)</label>' +
                '<input type="text" class="rna-input" id="mod-genes" placeholder="e.g. TP53, BRCA1, EGFR">' +
                '</div>' +
                '<div class="md-form-section">' +
                '<label class="rna-label">Clinical Survival Data</label>' +
                this.buildDataInputTabs("survival-data",
                    ["Sample_ID", "Time_Days", "Event_Occurred"],
                    [["Sample_1", "365", "1"], ["Sample_2", "180", "0"], ["Sample_3", "730", "1"]]) +
                '</div>';
        },

        formTcga: function () {
            return '<div class="md-form-section">' +
                '<label class="rna-label">Target TCGA Cohort</label>' +
                '<select class="rna-input rna-select" id="mod-cohort">' +
                '<option value="BRCA">BRCA — Breast Cancer</option>' +
                '<option value="LUAD">LUAD — Lung Adenocarcinoma</option>' +
                '<option value="COAD">COAD — Colon Adenocarcinoma</option>' +
                '<option value="PRAD">PRAD — Prostate Adenocarcinoma</option>' +
                '<option value="LIHC">LIHC — Liver Hepatocellular</option>' +
                '<option value="KIRC">KIRC — Kidney Renal Clear Cell</option>' +
                '<option value="GBM">GBM — Glioblastoma</option>' +
                '<option value="OV">OV — Ovarian Serous</option>' +
                '</select></div>';
        },

        formBiomarker: function () {
            return '<div class="md-form-section">' +
                '<label class="rna-label">Disease Context</label>' +
                '<input type="text" class="rna-input" id="mod-disease" placeholder="e.g. Breast Cancer, Alzheimer\'s disease">' +
                '</div>';
        },

        formMofa: function () {
            return '<div class="md-form-section">' +
                '<label class="rna-label">Number of Factors</label>' +
                '<input type="number" class="rna-input" id="mod-factors" value="10" min="2" max="50">' +
                '</div>' +
                '<div class="md-form-section">' +
                '<label class="rna-label">Secondary Omics Matrix</label>' +
                this.buildDataInputTabs("mofa-omics",
                    ["Sample_ID", "Feature_1", "Feature_2"],
                    [["Sample_1", "2.3", "0.5"], ["Sample_2", "1.1", "3.7"], ["Sample_3", "4.0", "1.2"]]) +
                '</div>';
        },

        formDiablo: function () {
            return '<div class="md-form-section">' +
                '<label class="rna-label">Number of Components</label>' +
                '<input type="number" class="rna-input" id="mod-components" value="3" min="2" max="20">' +
                '</div>' +
                '<div class="md-form-section">' +
                '<label class="rna-label">Secondary Omics Matrix</label>' +
                this.buildDataInputTabs("diablo-omics",
                    ["Sample_ID", "Feature_1", "Feature_2"],
                    [["Sample_1", "0.9", "2.1"], ["Sample_2", "1.7", "0.4"], ["Sample_3", "3.3", "1.8"]]) +
                '</div>';
        },

        // ── Reusable: Dropzone HTML ──
        buildDropzone: function (id, accept, label) {
            return '<div class="md-dropzone" id="dz-' + id + '" data-accept="' + accept + '">' +
                '<i class="bi bi-cloud-arrow-up"></i>' +
                '<span>' + label + '</span>' +
                '<span class="dz-hint">Accepted: ' + this.escHtml(accept) + '</span>' +
                '<span class="dz-filename" style="display:none;"></span>' +
                '<input type="file" accept="' + accept + '">' +
                '</div>' +
                '<div class="md-upload-progress" id="progress-' + id + '">' +
                '<div class="md-upload-bar"><div class="md-upload-bar-fill"></div></div>' +
                '<div class="md-upload-label">0%</div></div>';
        },

        // ── Reusable: Data Input Tabs (Upload CSV / Example / Manual Builder) ──
        buildDataInputTabs: function (prefix, columns, exampleRows) {
            var html = '<div class="md-data-tabs" data-tab-group="' + prefix + '">';
            html += '<button class="md-data-tab active" data-dtab="upload">Upload CSV</button>';
            html += '<button class="md-data-tab" data-dtab="example">Example Format</button>';
            html += '<button class="md-data-tab" data-dtab="builder">Manual Builder</button>';
            html += '</div>';

            // Upload panel
            html += '<div class="md-data-panel active" data-dtpanel="upload" data-dtgroup="' + prefix + '">';
            html += this.buildDropzone(prefix + "-csv", ".csv,.tsv", "Drop a CSV file here or click to browse");
            html += '</div>';

            // Example panel
            html += '<div class="md-data-panel" data-dtpanel="example" data-dtgroup="' + prefix + '">';
            html += '<p class="rna-text-sm rna-text-muted" style="margin:0 0 .5rem;">Your CSV should follow this format:</p>';
            html += '<table class="md-example-table"><thead><tr>';
            for (var c = 0; c < columns.length; c++) {
                html += '<th>' + this.escHtml(columns[c]) + '</th>';
            }
            html += '</tr></thead><tbody>';
            for (var r = 0; r < exampleRows.length; r++) {
                html += '<tr>';
                for (var d = 0; d < exampleRows[r].length; d++) {
                    html += '<td>' + this.escHtml(exampleRows[r][d]) + '</td>';
                }
                html += '</tr>';
            }
            html += '</tbody></table>';
            html += '<p class="md-example-note">First row must be column headers. Values separated by commas or tabs.</p>';
            html += '</div>';

            // Manual Builder panel
            html += '<div class="md-data-panel" data-dtpanel="builder" data-dtgroup="' + prefix + '">';
            html += '<div class="md-builder-controls">';
            html += '<button class="md-builder-btn" data-builder-action="add-row" data-builder="' + prefix + '"><i class="bi bi-plus"></i> Add Row</button>';
            html += '<button class="md-builder-btn" data-builder-action="add-col" data-builder="' + prefix + '"><i class="bi bi-plus"></i> Add Column</button>';
            html += '</div>';
            html += '<div class="md-builder-table-wrap">';
            html += '<table class="md-builder-table" id="builder-' + prefix + '">';
            html += '<thead><tr>';
            for (var h = 0; h < columns.length; h++) {
                html += '<th><input type="text" value="' + this.escAttr(columns[h]) + '"></th>';
            }
            html += '<th class="row-delete"></th></tr></thead><tbody>';
            // 3 empty starter rows
            for (var s = 0; s < 3; s++) {
                html += '<tr>';
                for (var e = 0; e < columns.length; e++) {
                    html += '<td><input type="text" placeholder=""></td>';
                }
                html += '<td class="row-delete"><button title="Remove row">&times;</button></td></tr>';
            }
            html += '</tbody></table></div></div>';

            return html;
        },

        // ── Bind Form Interactions ──
        bindFormInteractions: function (mod) {
            var self = this;

            // SPLICING: toggle between CSV upload and Manual Builder modes
            if (mod === "SPLICING") {
                var modeCards = document.querySelectorAll("#splicing-mode-group .splicing-mode-card");
                var csvPanel = document.getElementById("splicing-csv-panel");
                var manualPanel = document.getElementById("splicing-manual-panel");

                modeCards.forEach(function (card) {
                    card.addEventListener("click", function () {
                        modeCards.forEach(function (c) { c.classList.remove("active"); });
                        card.classList.add("active");
                        var mode = card.dataset.mode;
                        if (mode === "csv") {
                            csvPanel.style.display = "";
                            manualPanel.style.display = "none";
                        } else {
                            csvPanel.style.display = "none";
                            manualPanel.style.display = "";
                        }
                    });
                });

                // Download template CSV pre-populated with BAM file names
                var dlBtn = document.getElementById("splicing-download-template");
                if (dlBtn) {
                    dlBtn.addEventListener("click", function () {
                        var csvContent = "file_name,condition\n";
                        for (var b = 0; b < BAM_FILES.length; b++) {
                            csvContent += BAM_FILES[b] + ",EDIT_ME\n";
                        }
                        var blob = new Blob([csvContent], { type: "text/csv;charset=utf-8;" });
                        var url = URL.createObjectURL(blob);
                        var link = document.createElement("a");
                        link.href = url;
                        link.download = "alt_splicing_conditions.csv";
                        link.click();
                        URL.revokeObjectURL(url);
                    });
                }
            }

            // RNA_EDITING: toggle BED upload based on checkbox
            if (mod === "RNA_EDITING") {
                var cb = document.getElementById("mod-whole-txome");
                var sec = document.getElementById("bed-upload-section");
                if (cb && sec) {
                    cb.addEventListener("change", function () {
                        sec.style.display = cb.checked ? "none" : "";
                    });
                }
            }

            // TIME_SERIES: time-unit button group toggle, add-row, and template download
            if (mod === "TIME_SERIES") {
                var tuCards = document.querySelectorAll("#time-unit-group .splicing-mode-card");
                var tuHidden = document.getElementById("mod-time-unit");
                tuCards.forEach(function (card) {
                    card.addEventListener("click", function () {
                        tuCards.forEach(function (c) { c.classList.remove("active"); });
                        card.classList.add("active");
                        if (tuHidden) tuHidden.value = card.dataset.unit;
                    });
                });
                var tsAddBtn = document.getElementById("ts-add-row");
                if (tsAddBtn) {
                    tsAddBtn.addEventListener("click", function () {
                        var table = document.getElementById("ts-builder-table");
                        if (!table) return;
                        var row = table.tBodies[0].insertRow();
                        row.innerHTML = '<td><input type="text" placeholder="Sample_ID"></td>' +
                            '<td><input type="number" placeholder="e.g. 0" step="any"></td>' +
                            '<td><input type="text" placeholder="e.g. Control"></td>' +
                            '<td class="row-delete"><button title="Remove row">&times;</button></td>';
                    });
                }
                var tsDlBtn = document.getElementById("ts-download-template");
                if (tsDlBtn) {
                    tsDlBtn.addEventListener("click", function () {
                        var csvContent = "Sample_ID,Timepoint,Condition\n";
                        for (var s = 0; s < SAMPLE_IDS.length; s++) {
                            csvContent += SAMPLE_IDS[s] + ",,Control\n";
                        }
                        var blob = new Blob([csvContent], { type: "text/csv;charset=utf-8;" });
                        var url = URL.createObjectURL(blob);
                        var link = document.createElement("a");
                        link.href = url;
                        link.download = "timeseries_mapping.csv";
                        link.click();
                        URL.revokeObjectURL(url);
                    });
                }
            }

            // Bind all dropzones in the current detail pane
            document.querySelectorAll("#md-detail-content .md-dropzone").forEach(function (dz) {
                self.initDropzone(dz);
            });

            // Bind data-input tabs
            document.querySelectorAll("#md-detail-content .md-data-tab").forEach(function (tab) {
                tab.addEventListener("click", function () {
                    var group = tab.closest(".md-data-tabs").dataset.tabGroup;
                    tab.closest(".md-data-tabs").querySelectorAll(".md-data-tab").forEach(function (t) {
                        t.classList.remove("active");
                    });
                    tab.classList.add("active");
                    document.querySelectorAll('[data-dtgroup="' + group + '"]').forEach(function (p) {
                        p.classList.remove("active");
                    });
                    var target = document.querySelector('[data-dtpanel="' + tab.dataset.dtab + '"][data-dtgroup="' + group + '"]');
                    if (target) target.classList.add("active");
                });
            });

            // Bind builder buttons
            document.querySelectorAll("#md-detail-content [data-builder-action]").forEach(function (btn) {
                btn.addEventListener("click", function () {
                    var tableId = "builder-" + btn.dataset.builder;
                    var table = document.getElementById(tableId);
                    if (!table) return;
                    if (btn.dataset.builderAction === "add-row") {
                        self.builderAddRow(table);
                    } else if (btn.dataset.builderAction === "add-col") {
                        self.builderAddCol(table);
                    }
                });
            });

            // Bind row delete buttons via delegation
            document.querySelectorAll("#md-detail-content .md-builder-table").forEach(function (table) {
                table.addEventListener("click", function (e) {
                    var delBtn = e.target.closest(".row-delete button");
                    if (!delBtn) return;
                    var row = delBtn.closest("tr");
                    if (row && table.tBodies[0].rows.length > 1) {
                        row.remove();
                    }
                });
            });
        },

        // ── Drag & Drop ──
        initDropzone: function (dz) {
            var self = this;
            var fileInput = dz.querySelector('input[type="file"]');
            var acceptStr = dz.dataset.accept || "";

            function handleFiles(files) {
                if (!files || files.length === 0) return;
                var file = files[0];
                // Validate extension
                if (acceptStr) {
                    var exts = acceptStr.split(",").map(function (e) { return e.trim().toLowerCase(); });
                    var fname = file.name.toLowerCase();
                    var valid = exts.some(function (ext) { return fname.endsWith(ext); });
                    if (!valid) {
                        self.showToast("Invalid file type. Accepted: " + acceptStr, "error");
                        return;
                    }
                }
                dz.classList.add("has-file");
                var nameEl = dz.querySelector(".dz-filename");
                if (nameEl) {
                    nameEl.textContent = file.name + " (" + self.formatSize(file.size) + ")";
                    nameEl.style.display = "";
                }
                self.pendingFiles[dz.id] = file;
                self.simulateUploadProgress(dz.id);
            }

            dz.addEventListener("dragover", function (e) {
                e.preventDefault();
                e.stopPropagation();
                dz.classList.add("drag-over");
            });
            dz.addEventListener("dragleave", function (e) {
                e.preventDefault();
                e.stopPropagation();
                dz.classList.remove("drag-over");
            });
            dz.addEventListener("drop", function (e) {
                e.preventDefault();
                e.stopPropagation();
                dz.classList.remove("drag-over");
                handleFiles(e.dataTransfer.files);
            });
            if (fileInput) {
                fileInput.addEventListener("change", function () {
                    handleFiles(fileInput.files);
                });
            }
        },

        simulateUploadProgress: function (dzId) {
            var progressEl = document.getElementById("progress-" + dzId.replace("dz-", ""));
            if (!progressEl) return;
            progressEl.classList.add("active");
            var fill = progressEl.querySelector(".md-upload-bar-fill");
            var label = progressEl.querySelector(".md-upload-label");
            var pct = 0;
            var iv = setInterval(function () {
                pct += Math.random() * 25 + 10;
                if (pct >= 100) {
                    pct = 100;
                    clearInterval(iv);
                }
                fill.style.width = Math.round(pct) + "%";
                label.textContent = Math.round(pct) + "%";
            }, 150);
        },

        // ── Table Builder Logic ──
        builderAddRow: function (table) {
            var colCount = table.tHead.rows[0].cells.length - 1; // minus delete col
            var row = table.tBodies[0].insertRow();
            for (var i = 0; i < colCount; i++) {
                var td = row.insertCell();
                td.innerHTML = '<input type="text" placeholder="">';
            }
            var del = row.insertCell();
            del.className = "row-delete";
            del.innerHTML = '<button title="Remove row">&times;</button>';
        },

        builderAddCol: function (table) {
            // Add header
            var headerRow = table.tHead.rows[0];
            var delHeader = headerRow.cells[headerRow.cells.length - 1];
            var newTh = document.createElement("th");
            newTh.innerHTML = '<input type="text" value="New_Col">';
            headerRow.insertBefore(newTh, delHeader);
            // Add cells to all body rows
            for (var r = 0; r < table.tBodies[0].rows.length; r++) {
                var row = table.tBodies[0].rows[r];
                var delCell = row.cells[row.cells.length - 1];
                var td = document.createElement("td");
                td.innerHTML = '<input type="text" placeholder="">';
                row.insertBefore(td, delCell);
            }
        },

        serializeBuilder: function (tableId) {
            var table = document.getElementById(tableId);
            if (!table) return null;
            var headers = [];
            var headerCells = table.tHead.rows[0].cells;
            for (var h = 0; h < headerCells.length - 1; h++) {
                var input = headerCells[h].querySelector("input");
                headers.push(input ? input.value.trim() : "");
            }
            var rows = [];
            for (var r = 0; r < table.tBodies[0].rows.length; r++) {
                var row = table.tBodies[0].rows[r];
                var rowData = {};
                var hasData = false;
                for (var c = 0; c < row.cells.length - 1; c++) {
                    var inp = row.cells[c].querySelector("input");
                    var val = inp ? inp.value.trim() : "";
                    rowData[headers[c]] = val;
                    if (val) hasData = true;
                }
                if (hasData) rows.push(rowData);
            }
            return rows.length > 0 ? { headers: headers, rows: rows } : null;
        },

        // ── Collect Form Params ──
        collectParams: function (mod) {
            var self = this;
            var params = { job_id: JOB_ID };

            switch (mod) {
                case "SPLICING":
                    var splicingMode = document.querySelector('input[name="splicing_mode"]:checked');
                    params.input_mode = splicingMode ? splicingMode.value : "manual";
                    if (params.input_mode === "csv") {
                        params.csv_data = this.readPendingFileContent("dz-splicing-csv");
                    } else {
                        var assignments = [];
                        document.querySelectorAll("#splicing-bam-table .splicing-condition-input").forEach(function (inp) {
                            var cond = inp.value.trim();
                            if (cond) {
                                assignments.push({
                                    file_name: inp.dataset.bam,
                                    condition: cond,
                                });
                            }
                        });
                        params.sample_conditions = assignments;
                    }
                    break;
                case "RNA_EDITING":
                    params.whole_transcriptome = this.checked("mod-whole-txome");
                    if (!params.whole_transcriptome) {
                        params.bed_data = this.readPendingFileContent("dz-bed-file");
                    }
                    break;
                case "TIME_SERIES":
                    params.time_unit = this.val("mod-time-unit");
                    // Check which input tab is active
                    var tsActiveTab = document.querySelector('[data-tab-group="ts-mapping"] .md-data-tab.active');
                    var tsMode = tsActiveTab ? tsActiveTab.dataset.dtab : "builder";
                    if (tsMode === "upload") {
                        params.mapping_data = { mode: "csv", content: this.readPendingFileContent("dz-ts-mapping-csv") };
                    } else {
                        // Serialize from the manual builder table
                        var tsTable = document.getElementById("ts-builder-table");
                        var tsRows = [];
                        if (tsTable) {
                            var bodyRows = tsTable.tBodies[0].rows;
                            for (var r = 0; r < bodyRows.length; r++) {
                                var cells = bodyRows[r].cells;
                                var sampleId = cells[0].querySelector("input").value.trim();
                                var timepoint = cells[1].querySelector("input").value.trim();
                                var condition = cells[2].querySelector("input").value.trim();
                                if (sampleId && timepoint !== "") {
                                    tsRows.push({
                                        Sample_ID: sampleId,
                                        Timepoint: parseFloat(timepoint),
                                        Condition: condition || "Control",
                                    });
                                }
                            }
                        }
                        params.mapping_data = { mode: "manual", rows: tsRows };
                    }
                    break;
                case "WGCNA":
                    params.soft_power = parseInt(this.val("mod-soft-power") || "6");
                    params.traits_data = this.collectDataInput("wgcna-traits");
                    break;
                case "PATHWAY":
                    params.gene_set = this.val("mod-geneset");
                    params.fdr = parseFloat(this.val("mod-fdr") || "0.25");
                    break;
                case "NETWORKS":
                    params.tf_list = this.val("mod-tf-list");
                    params.confidence = parseFloat(this.val("mod-confidence") || "0.7");
                    break;
                case "LIT_MINING":
                    params.keywords = this.val("mod-keywords");
                    break;
                case "SURVIVAL":
                    params.genes = this.val("mod-genes");
                    params.survival_data = this.collectDataInput("survival-data");
                    break;
                case "TCGA":
                    params.cohort = this.val("mod-cohort");
                    break;
                case "BIOMARKER":
                    params.disease_context = this.val("mod-disease");
                    break;
                case "MOFA":
                    params.n_factors = parseInt(this.val("mod-factors") || "10");
                    params.omics_data = this.collectDataInput("mofa-omics");
                    break;
                case "DIABLO":
                    params.n_components = parseInt(this.val("mod-components") || "3");
                    params.omics_data = this.collectDataInput("diablo-omics");
                    break;
            }
            return params;
        },

        collectDataInput: function (prefix) {
            // Check which data tab is active
            var activeTab = document.querySelector('[data-tab-group="' + prefix + '"] .md-data-tab.active');
            if (!activeTab) return null;
            var mode = activeTab.dataset.dtab;
            if (mode === "upload") {
                return { mode: "csv", content: this.readPendingFileContent("dz-" + prefix + "-csv") };
            } else if (mode === "builder") {
                var data = this.serializeBuilder("builder-" + prefix);
                return data ? { mode: "manual", data: data } : null;
            }
            return null;
        },

        readPendingFileContent: function (dzId) {
            // For pending files, content will be sent asynchronously; return the file reference
            var file = this.pendingFiles[dzId];
            return file ? { filename: file.name, pending: true } : null;
        },

        val: function (id) {
            var el = document.getElementById(id);
            return el ? el.value.trim() : "";
        },

        checked: function (id) {
            var el = document.getElementById(id);
            return el ? el.checked : false;
        },

        // ── Submit Run (Step 3→4) ──
        submitRun: function (moduleName) {
            var self = this;
            var params = this.collectParams(moduleName);

            // If there are pending files, read them first
            var filePromises = [];
            var fileParams = {};

            // Check for file data in params
            function scanForFiles(obj, path) {
                for (var key in obj) {
                    if (obj[key] && typeof obj[key] === "object") {
                        if (obj[key].pending && obj[key].filename) {
                            var dzId = null;
                            // Find the corresponding dropzone
                            for (var fid in self.pendingFiles) {
                                if (self.pendingFiles[fid].name === obj[key].filename) {
                                    dzId = fid;
                                    break;
                                }
                            }
                            if (dzId) {
                                (function (k, fKey) {
                                    filePromises.push(
                                        self.readFileAsText(self.pendingFiles[fKey]).then(function (text) {
                                            fileParams[k] = text;
                                        })
                                    );
                                })(path ? path + "." + key : key, dzId);
                            }
                        } else if (obj[key].mode === "csv" && obj[key].content && obj[key].content.pending) {
                            var dzKey = null;
                            for (var fk in self.pendingFiles) {
                                if (self.pendingFiles[fk].name === obj[key].content.filename) {
                                    dzKey = fk;
                                    break;
                                }
                            }
                            if (dzKey) {
                                (function (k, fKey) {
                                    filePromises.push(
                                        self.readFileAsText(self.pendingFiles[fKey]).then(function (text) {
                                            fileParams[k] = text;
                                        })
                                    );
                                })(path ? path + "." + key : key, dzKey);
                            }
                        }
                    }
                }
            }
            scanForFiles(params, "");

            return Promise.all(filePromises).then(function () {
                // Replace file references with actual content
                for (var fp in fileParams) {
                    var parts = fp.split(".");
                    var target = params;
                    for (var p = 0; p < parts.length - 1; p++) {
                        target = target[parts[p]];
                    }
                    var lastKey = parts[parts.length - 1];
                    if (target[lastKey] && target[lastKey].mode === "csv") {
                        target[lastKey].content = fileParams[fp];
                    } else {
                        target[lastKey] = fileParams[fp];
                    }
                }

                var url = "/api/submissions/" + SUBMISSION_ID + "/modules/" + moduleName + "/run";
                return fetch(url, {
                    method: "POST",
                    headers: {
                        "Content-Type": "application/json",
                        "X-CSRFToken": CSRF,
                    },
                    body: JSON.stringify(params),
                });
            }).then(function (res) {
                if (!res.ok) throw new Error("Submission failed");
                return res.json();
            }).then(function (data) {
                // Step 4: Add to history and show history list
                var newEntry = {
                    job_id: data.job_id,
                    status: data.status || "PENDING",
                    payload: {},
                    updated_at: new Date().toISOString(),
                    created_at: new Date().toISOString(),
                };
                if (!self.moduleHistory[moduleName]) {
                    self.moduleHistory[moduleName] = [];
                }
                self.moduleHistory[moduleName].unshift(newEntry);
                self.applyMasterBadges();
                self.showHistoryList(moduleName);
                self.pollJob(data.job_id, moduleName);
            });
        },

        readFileAsText: function (file) {
            return new Promise(function (resolve, reject) {
                var reader = new FileReader();
                reader.onload = function () { resolve(reader.result); };
                reader.onerror = function () { reject(reader.error); };
                reader.readAsText(file);
            });
        },

        // ── Poll Job (Step 5) ──
        pollJob: function (jobId, moduleName) {
            var self = this;
            if (this.activePolls[jobId]) return;
            this.activePolls[jobId] = setInterval(function () {
                fetch("/api/jobs/" + jobId + "/", {
                    headers: { "X-CSRFToken": CSRF },
                }).then(function (res) {
                    if (!res.ok) throw new Error("Poll failed");
                    return res.json();
                }).then(function (data) {
                    var history = self.moduleHistory[moduleName] || [];
                    for (var i = 0; i < history.length; i++) {
                        if (history[i].job_id === jobId) {
                            history[i].status = data.status;
                            if (data.payload) history[i].payload = data.payload;
                            if (data.updated_at) history[i].updated_at = data.updated_at;
                            break;
                        }
                    }

                    if (data.status === "SUCCESS" || data.status === "FAILED") {
                        clearInterval(self.activePolls[jobId]);
                        delete self.activePolls[jobId];
                        self.applyMasterBadges();

                        if (data.status === "SUCCESS") {
                            self.showToast(self.getModuleTitle(moduleName) + " completed!", "success");
                            if ("Notification" in window && Notification.permission === "granted") {
                                new Notification("RNAseek", { body: self.getModuleTitle(moduleName) + " analysis complete!" });
                            }
                        } else {
                            self.showToast(self.getModuleTitle(moduleName) + " failed.", "error");
                        }

                        // Refresh history list if currently viewing this module
                        if (self.selectedModule === moduleName && self.detailView === "history") {
                            self.showHistoryList(moduleName);
                        }
                    }
                }).catch(function () {
                    // Silent fail, will retry
                });
            }, 4000);
        },

        startActivePolls: function () {
            for (var mod in this.moduleHistory) {
                var history = this.moduleHistory[mod];
                for (var i = 0; i < history.length; i++) {
                    if (history[i].status === "RUNNING" || history[i].status === "PENDING") {
                        this.pollJob(history[i].job_id, mod);
                    }
                }
            }
        },

        // ── Result View (Step 6) ──
        showResult: function (moduleName, entry) {
            var self = this;
            this.detailView = "result";
            var title = this.getModuleTitle(moduleName);
            var payload = entry.payload || {};
            var dateStr = entry.updated_at ? new Date(entry.updated_at).toLocaleString() : "N/A";

            var html = '<div class="md-detail-header"><h3>' + this.escHtml(title) + ' — Results</h3></div>';
            html += '<div class="md-detail-body">';

            html += '<div class="md-result-header">';
            html += '<div><h4>Run ' + this.escHtml(entry.job_id ? entry.job_id.substring(0, 8) : "") + '</h4>';
            html += '<div class="md-result-meta">Completed ' + this.escHtml(dateStr) + '</div></div>';
            html += '<div style="display:flex; gap:.5rem;">';
            html += '<button class="btn-rna-back" id="md-back-to-history-r"><i class="bi bi-arrow-left"></i> History</button>';
            html += '<button class="btn-rna-download" id="md-download-result"><i class="bi bi-download"></i> Download Result</button>';
            html += '</div></div>';

            // Render payload content
            if (payload.summary) {
                html += '<div class="md-result-section"><h5>Summary</h5>';
                html += '<p class="rna-text-sm">' + this.escHtml(payload.summary) + '</p></div>';
            }

            if (payload.plot_data) {
                html += '<div class="md-result-section"><h5>Visualizations</h5>';
                var plotKeys = Object.keys(payload.plot_data);
                for (var p = 0; p < plotKeys.length; p++) {
                    html += '<div class="rna-plot-container" id="mod-plot-' + plotKeys[p] + '" style="min-height:350px; margin-bottom:1rem;"></div>';
                }
                html += '</div>';
            }

            if (payload.table_preview) {
                html += '<div class="md-result-section"><h5>Data Preview</h5>';
                html += '<div style="overflow-x:auto;">' + payload.table_preview + '</div></div>';
            }

            if (payload.editing_stats) {
                var es = payload.editing_stats;
                html += '<div class="md-result-section"><h5>Editing Statistics</h5>';
                html += '<div style="display:flex; flex-wrap:wrap; gap:1rem;">';
                html += '<div class="md-stat-card"><span class="md-stat-value">' + es.total_sites + '</span><span class="md-stat-label">Total Sites</span></div>';
                html += '<div class="md-stat-card"><span class="md-stat-value">' + (es.avg_editing_freq != null ? es.avg_editing_freq.toFixed(3) : "—") + '</span><span class="md-stat-label">Avg Frequency</span></div>';
                html += '<div class="md-stat-card"><span class="md-stat-value">' + (es.a_to_i_count || 0) + '</span><span class="md-stat-label">A→I (AG) Sites</span></div>';
                html += '<div class="md-stat-card"><span class="md-stat-value">' + (es.c_to_u_count || 0) + '</span><span class="md-stat-label">C→U (TC) Sites</span></div>';
                html += '<div class="md-stat-card"><span class="md-stat-value">' + (es.avg_coverage != null ? es.avg_coverage.toFixed(0) : "—") + '</span><span class="md-stat-label">Avg Coverage</span></div>';
                html += '</div></div>';
            }

            if (payload.hub_genes) {
                html += '<div class="md-result-section"><h5>Hub Genes</h5>';
                html += '<p class="rna-text-sm">' + payload.hub_genes.join(", ") + '</p></div>';
            }

            if (payload.enrichment_summary && payload.enrichment_summary.length > 0) {
                html += '<div class="md-result-section"><h5>Enrichment Summary</h5>';
                html += '<div style="overflow-x:auto;"><table class="md-example-table"><thead><tr>';
                html += '<th>Term</th><th>Overlap</th><th>Adj. P-value</th></tr></thead><tbody>';
                for (var e = 0; e < Math.min(payload.enrichment_summary.length, 10); e++) {
                    var row = payload.enrichment_summary[e];
                    html += '<tr><td>' + this.escHtml(row.Term || "") + '</td>';
                    html += '<td>' + this.escHtml(row.Overlap || "") + '</td>';
                    html += '<td>' + (row["Adjusted P-value"] != null ? row["Adjusted P-value"].toExponential(2) : "") + '</td></tr>';
                }
                html += '</tbody></table></div></div>';
            }

            if (!payload.summary && !payload.plot_data && !payload.table_preview && !payload.hub_genes) {
                html += '<p class="rna-text-sm rna-text-muted">Result payload stored. Download to inspect.</p>';
            }

            html += '</div>';
            this.setDetail(html);

            // Render Plotly plots
            if (payload.plot_data && typeof Plotly !== "undefined") {
                setTimeout(function () {
                    for (var key in payload.plot_data) {
                        var plotEl = document.getElementById("mod-plot-" + key);
                        var pd = payload.plot_data[key];
                        if (plotEl && pd && pd.data) {
                            Plotly.newPlot(plotEl, pd.data, pd.layout || {}, { responsive: true, displayModeBar: true });
                        }
                    }
                }, 100);
            }

            // Bind back button
            var backBtn = document.getElementById("md-back-to-history-r");
            if (backBtn) {
                backBtn.addEventListener("click", function () {
                    self.showHistoryList(moduleName);
                });
            }

            // Bind download
            var dlBtn = document.getElementById("md-download-result");
            if (dlBtn) {
                dlBtn.addEventListener("click", function () {
                    self.downloadResult(moduleName, entry);
                });
            }
        },

        // ── Download Result ──
        downloadResult: function (moduleName, entry) {
            var payload = entry.payload || {};
            var json = JSON.stringify(payload, null, 2);
            var blob = new Blob([json], { type: "application/json" });
            var url = URL.createObjectURL(blob);
            var a = document.createElement("a");
            a.href = url;
            a.download = moduleName.toLowerCase() + "_" + (entry.job_id || "result").substring(0, 8) + "_results.json";
            document.body.appendChild(a);
            a.click();
            document.body.removeChild(a);
            URL.revokeObjectURL(url);
        },

        // ── Toast Notifications ──
        showToast: function (msg, type) {
            var container = document.getElementById("rna-toast-container");
            if (!container) return;
            var toast = document.createElement("div");
            toast.className = "rna-toast" + (type === "error" ? " toast-error" : "");
            var icon = type === "error" ? "bi-exclamation-triangle" : "bi-check-circle";
            toast.innerHTML = '<i class="bi ' + icon + '" style="color:' + (type === "error" ? "var(--rna-accent-red)" : "var(--rna-accent-green)") + '"></i>' +
                '<span class="toast-msg">' + this.escHtml(msg) + '</span>' +
                '<button class="toast-close">&times;</button>';
            container.appendChild(toast);

            var closeBtn = toast.querySelector(".toast-close");
            closeBtn.addEventListener("click", function () {
                self.dismissToast(toast);
            });

            var self = this;
            setTimeout(function () {
                self.dismissToast(toast);
            }, 6000);
        },

        dismissToast: function (toast) {
            if (!toast || !toast.parentNode) return;
            toast.style.animation = "rna-toast-out .3s ease forwards";
            setTimeout(function () {
                if (toast.parentNode) toast.parentNode.removeChild(toast);
            }, 300);
        },

        // ── Utilities ──
        escHtml: function (str) {
            var div = document.createElement("div");
            div.appendChild(document.createTextNode(str || ""));
            return div.innerHTML;
        },

        escAttr: function (str) {
            return (str || "").replace(/&/g, "&amp;").replace(/"/g, "&quot;").replace(/'/g, "&#39;").replace(/</g, "&lt;").replace(/>/g, "&gt;");
        },

        formatSize: function (bytes) {
            if (bytes < 1024) return bytes + " B";
            if (bytes < 1048576) return (bytes / 1024).toFixed(1) + " KB";
            return (bytes / 1048576).toFixed(1) + " MB";
        },
    };

    ModuleHub.init();

    // Request notification permission for completion alerts
    if ("Notification" in window && Notification.permission === "default") {
        Notification.requestPermission();
    }

    // ── Single-Cell Master-Detail Navigation ──
    (function () {
        var spokeItems = document.querySelectorAll("#sc-master-list .md-module-item[data-sc-spoke]");
        var detailPanels = {
            DECONV: document.getElementById("sc-detail-content"),
            TRAJECTORY: document.getElementById("sc-detail-trajectory"),
            SPATIAL: document.getElementById("sc-detail-spatial"),
            AUTOCORRELATION: document.getElementById("sc-detail-autocorrelation"),
        };

        spokeItems.forEach(function (item) {
            item.addEventListener("click", function () {
                if (item.classList.contains("locked")) return;
                spokeItems.forEach(function (i) { i.classList.remove("active"); });
                item.classList.add("active");
                var spoke = item.dataset.scSpoke;
                Object.keys(detailPanels).forEach(function (key) {
                    if (detailPanels[key]) {
                        detailPanels[key].style.display = key === spoke ? "" : "none";
                    }
                });
            });
        });
    })();

    // ── Deconvolution Gateway ──
    var deconBtn = document.getElementById("run-deconv-btn");
    if (deconBtn) {
        deconBtn.addEventListener("click", async function () {
            deconBtn.disabled = true;
            deconBtn.innerHTML = '<i class="bi bi-arrow-repeat rna-processing"></i> Running...';

            var atlas = document.getElementById("atlas-select")?.value;
            var hires = document.getElementById("hires-toggle")?.checked;

            var res = await fetch("/api/modules/deconvolution/run", {
                method: "POST",
                headers: {
                    "Content-Type": "application/json",
                    "X-CSRFToken": CSRF,
                },
                body: JSON.stringify({ job_id: JOB_ID, atlas: atlas, high_resolution: hires }),
            });

            if (res.ok) {
                var data = await res.json();
                pollDeconv(data.job_id);
            } else {
                deconBtn.disabled = false;
                deconBtn.innerHTML = '<i class="bi bi-play-circle"></i> Run Deconvolution';
            }
        });
    }

    function pollDeconv(deconvJobId) {
        var iv = setInterval(async function () {
            var res = await fetch("/api/jobs/" + deconvJobId + "/");
            if (!res.ok) { clearInterval(iv); return; }
            var data = await res.json();
            if (data.status === "SUCCESS") {
                clearInterval(iv);
                // Unlock spoke cards in master list
                document.querySelectorAll("#sc-master-list .md-module-item.locked").forEach(function (c) {
                    c.classList.remove("locked");
                });
                // Update deconv badge
                var badge = document.getElementById("sc-deconv-badge");
                if (badge) {
                    badge.textContent = "Done";
                    badge.className = "md-module-badge badge-done";
                }
                deconBtn.innerHTML = '<i class="bi bi-check-circle"></i> Complete';
            } else if (data.status === "FAILED") {
                clearInterval(iv);
                deconBtn.disabled = false;
                deconBtn.innerHTML = '<i class="bi bi-play-circle"></i> Run Deconvolution';
            }
        }, 5000);
    }

    // ── Download links (fetch as blob to avoid about:blank tab) ──
    document.querySelectorAll(".download-btn[data-asset]").forEach(function (btn) {
        btn.addEventListener("click", function () {
            var asset = btn.dataset.asset;
            btn.disabled = true;
            btn.classList.add("rna-processing");
            fetch("/api/session/assets?role=" + asset + "&job_id=" + JOB_ID, {
                headers: { "X-CSRFToken": CSRF }
            })
                .then(function (resp) {
                    if (!resp.ok) throw new Error("Download failed: " + resp.status);
                    var cd = resp.headers.get("Content-Disposition") || "";
                    var match = cd.match(/filename[^;=\n]*=['"]?([^'"\n;]+)/i);
                    var filename = match ? match[1] : asset.toLowerCase() + "_download";
                    return resp.blob().then(function (blob) { return { blob: blob, filename: filename }; });
                })
                .then(function (result) {
                    var url = URL.createObjectURL(result.blob);
                    var a = document.createElement("a");
                    a.href = url;
                    a.download = result.filename;
                    document.body.appendChild(a);
                    a.click();
                    document.body.removeChild(a);
                    URL.revokeObjectURL(url);
                })
                .catch(function (err) {
                    console.error("Asset download error:", err);
                })
                .finally(function () {
                    btn.disabled = false;
                    btn.classList.remove("rna-processing");
                });
        });
    });

    // ── Visualization Theater – Pill Navigation ──
    var vizPills = document.querySelectorAll("#viz-pills .viz-pill");
    var vizPanes = document.querySelectorAll(".viz-theater-pane");

    vizPills.forEach(function (pill) {
        pill.addEventListener("click", function () {
            var target = pill.dataset.viz;
            vizPills.forEach(function (p) { p.classList.remove("active"); });
            vizPanes.forEach(function (p) { p.classList.remove("active"); });
            pill.classList.add("active");
            document.getElementById("viz-pane-" + target).classList.add("active");

            // Resize the now-visible Plotly chart so it fills the container
            if (typeof Plotly !== "undefined") {
                var plotEl = document.getElementById("viz-pane-" + target).querySelector(".rna-plot-container");
                if (plotEl && plotEl.data) Plotly.Plots.resize(plotEl);
            }
        });
    });

    // ── Download Interactive Figure (standalone HTML with tooltips) ──
    document.querySelectorAll(".btn-rna-figure[data-figure]").forEach(function (btn) {
        btn.addEventListener("click", function () {
            var plotId = btn.dataset.figure;
            var el = document.getElementById(plotId);
            if (!el || !el.data || typeof Plotly === "undefined") return;

            var plotData = JSON.stringify(el.data);
            var plotLayout = JSON.stringify(el.layout);
            var safeName = plotId.replace(/[^a-z0-9_-]/gi, "_");

            var html = '<!DOCTYPE html>\n<html lang="en"><head>' +
                '<meta charset="utf-8">' +
                '<title>RNAseek — ' + safeName + '</title>' +
                '<script src="https://cdn.plot.ly/plotly-2.35.2.min.js"><\/script>' +
                '<style>body{margin:0;font-family:system-ui,sans-serif;background:#fafafa}' +
                '#plot{width:100vw;height:100vh}</style>' +
                '</head><body><div id="plot"></div><script>' +
                'Plotly.newPlot("plot",' + plotData + ',' + plotLayout +
                ',{responsive:true,displayModeBar:true});' +
                '<\/script></body></html>';

            var blob = new Blob([html], { type: "text/html" });
            var url = URL.createObjectURL(blob);
            var a = document.createElement("a");
            a.href = url;
            a.download = safeName + "_interactive.html";
            document.body.appendChild(a);
            a.click();
            document.body.removeChild(a);
            URL.revokeObjectURL(url);
        });
    });

    // ── Plot rendering ──
    var plotLayout = {
        paper_bgcolor: "transparent",
        plot_bgcolor: "transparent",
        font: { family: "Inter, system-ui, sans-serif", size: 12, color: "#334155" },
        margin: { l: 55, r: 20, t: 35, b: 50 },
        hovermode: "closest",
    };
    var plotConfig = { responsive: true, displayModeBar: true, modeBarButtonsToRemove: ["lasso2d", "select2d"] };

    function uniqueGroups(groups) {
        var seen = {};
        return groups.filter(function (g) { if (seen[g]) return false; seen[g] = true; return true; });
    }

    var COLORS = ["#059a98", "#e74c3c", "#f39c12", "#8e44ad", "#2ecc71", "#3498db", "#e67e22", "#1abc9c", "#c0392b", "#9b59b6"];

    /** Dismiss the loading overlay inside a plot container with a fade-out. */
    function dismissSpinner(el) {
        var loader = el.querySelector(".rna-plot-loading");
        if (!loader) return;
        loader.classList.add("fade-out");
        setTimeout(function () { loader.remove(); }, 320);
    }

    function renderPCA(pca) {
        var el = document.getElementById("pca-plot");
        if (!el) return;
        // Preserve spinner, clear only the placeholder text
        var placeholder = el.querySelector(":scope > span");
        if (placeholder) placeholder.remove();
        var groups = uniqueGroups(pca.groups);
        var traces = groups.map(function (g, gi) {
            var idx = [];
            pca.groups.forEach(function (v, i) { if (v === g) idx.push(i); });
            return {
                x: idx.map(function (i) { return pca.x[i]; }),
                y: idx.map(function (i) { return pca.y[i]; }),
                text: idx.map(function (i) { return pca.samples[i]; }),
                mode: "markers", type: "scatter", name: g,
                marker: { size: 10, color: COLORS[gi % COLORS.length], opacity: 0.85 },
                hovertemplate: "%{text}<br>PC1: %{x:.2f}<br>PC2: %{y:.2f}<extra>" + g + "</extra>",
            };
        });
        var layout = Object.assign({}, plotLayout, {
            title: { text: "PCA", font: { size: 14 } },
            xaxis: { title: "PC1 (" + pca.var_explained[0].toFixed(1) + "%)", zeroline: false },
            yaxis: { title: "PC2 (" + pca.var_explained[1].toFixed(1) + "%)", zeroline: false },
            legend: { orientation: "h", y: -0.18 },
        });
        Plotly.newPlot(el, traces, layout, plotConfig);
        el.classList.add("has-plot");
        dismissSpinner(el);
    }

    function renderUMAP(umap) {
        var el = document.getElementById("umap-plot");
        if (!el) return;
        var placeholder = el.querySelector(":scope > span");
        if (placeholder) placeholder.remove();
        var groups = uniqueGroups(umap.groups);
        var traces = groups.map(function (g, gi) {
            var idx = [];
            umap.groups.forEach(function (v, i) { if (v === g) idx.push(i); });
            return {
                x: idx.map(function (i) { return umap.x[i]; }),
                y: idx.map(function (i) { return umap.y[i]; }),
                text: idx.map(function (i) { return umap.samples[i]; }),
                mode: "markers", type: "scatter", name: g,
                marker: { size: 10, color: COLORS[gi % COLORS.length], opacity: 0.85 },
                hovertemplate: "%{text}<br>UMAP1: %{x:.2f}<br>UMAP2: %{y:.2f}<extra>" + g + "</extra>",
            };
        });
        var layout = Object.assign({}, plotLayout, {
            title: { text: "UMAP", font: { size: 14 } },
            xaxis: { title: "UMAP 1", zeroline: false },
            yaxis: { title: "UMAP 2", zeroline: false },
            legend: { orientation: "h", y: -0.18 },
        });
        Plotly.newPlot(el, traces, layout, plotConfig);
        el.classList.add("has-plot");
        dismissSpinner(el);
    }

    function renderVolcano(v) {
        var el = document.getElementById("volcano-plot");
        if (!el) return;
        var placeholder = el.querySelector(":scope > span");
        if (placeholder) placeholder.remove();
        var cats = { up: { x: [], y: [], t: [] }, down: { x: [], y: [], t: [] }, ns: { x: [], y: [], t: [] } };
        for (var i = 0; i < v.log2fc.length; i++) {
            var c = v.categories[i];
            cats[c].x.push(v.log2fc[i]);
            cats[c].y.push(v.neg_log10_padj[i]);
            cats[c].t.push(v.genes[i]);
        }
        var traces = [
            { x: cats.ns.x, y: cats.ns.y, text: cats.ns.t, mode: "markers", type: "scatter", name: "Not Sig.", marker: { size: 4, color: "#94a3b8", opacity: 0.4 }, hovertemplate: "%{text}<br>log2FC: %{x:.2f}<br>-log10(padj): %{y:.2f}<extra></extra>" },
            { x: cats.up.x, y: cats.up.y, text: cats.up.t, mode: "markers", type: "scatter", name: "Up", marker: { size: 6, color: "#e74c3c", opacity: 0.7 }, hovertemplate: "%{text}<br>log2FC: %{x:.2f}<br>-log10(padj): %{y:.2f}<extra>Up</extra>" },
            { x: cats.down.x, y: cats.down.y, text: cats.down.t, mode: "markers", type: "scatter", name: "Down", marker: { size: 6, color: "#3498db", opacity: 0.7 }, hovertemplate: "%{text}<br>log2FC: %{x:.2f}<br>-log10(padj): %{y:.2f}<extra>Down</extra>" },
        ];
        var padjLine = -Math.log10(v.thresholds.padj);
        var layout = Object.assign({}, plotLayout, {
            title: { text: "Volcano Plot", font: { size: 14 } },
            xaxis: { title: "log2 Fold Change", zeroline: true },
            yaxis: { title: "-log10(padj)", zeroline: false },
            legend: { orientation: "h", y: -0.18 },
            shapes: [
                { type: "line", x0: v.thresholds.log2fc_down, x1: v.thresholds.log2fc_down, yref: "paper", y0: 0, y1: 1, line: { dash: "dash", color: "#94a3b8", width: 1 } },
                { type: "line", x0: v.thresholds.log2fc_up, x1: v.thresholds.log2fc_up, yref: "paper", y0: 0, y1: 1, line: { dash: "dash", color: "#94a3b8", width: 1 } },
                { type: "line", xref: "paper", x0: 0, x1: 1, y0: padjLine, y1: padjLine, line: { dash: "dash", color: "#94a3b8", width: 1 } },
            ],
        });
        Plotly.newPlot(el, traces, layout, plotConfig);
        el.classList.add("has-plot");
        dismissSpinner(el);
    }

    function renderMA(ma) {
        var el = document.getElementById("ma-plot");
        if (!el) return;
        var placeholder = el.querySelector(":scope > span");
        if (placeholder) placeholder.remove();
        var sig = { x: [], y: [], t: [] };
        var ns = { x: [], y: [], t: [] };
        for (var i = 0; i < ma.log_base_mean.length; i++) {
            var bucket = ma.significant[i] ? sig : ns;
            bucket.x.push(ma.log_base_mean[i]);
            bucket.y.push(ma.log2fc[i]);
            bucket.t.push(ma.genes[i]);
        }
        var traces = [
            { x: ns.x, y: ns.y, text: ns.t, mode: "markers", type: "scatter", name: "Not Sig.", marker: { size: 4, color: "#94a3b8", opacity: 0.4 }, hovertemplate: "%{text}<br>log10(baseMean): %{x:.2f}<br>log2FC: %{y:.2f}<extra></extra>" },
            { x: sig.x, y: sig.y, text: sig.t, mode: "markers", type: "scatter", name: "Significant", marker: { size: 6, color: "#e74c3c", opacity: 0.7 }, hovertemplate: "%{text}<br>log10(baseMean): %{x:.2f}<br>log2FC: %{y:.2f}<extra>Sig</extra>" },
        ];
        var layout = Object.assign({}, plotLayout, {
            title: { text: "MA Plot", font: { size: 14 } },
            xaxis: { title: "log10(Base Mean)", zeroline: false },
            yaxis: { title: "log2 Fold Change", zeroline: true },
            legend: { orientation: "h", y: -0.18 },
        });
        Plotly.newPlot(el, traces, layout, plotConfig);
        el.classList.add("has-plot");
        dismissSpinner(el);
    }

    function renderHeatmap(hm) {
        var el = document.getElementById("heatmap-plot");
        if (!el) return;
        var placeholder = el.querySelector(":scope > span");
        if (placeholder) placeholder.remove();

        // Dynamically scale height so rows aren't squished
        var dynamicHeight = Math.max(500, hm.genes.length * 15 + 150);
        el.style.height = dynamicHeight + "px";

        var hoverText = [];
        for (var r = 0; r < hm.genes.length; r++) {
            var row = [];
            for (var c = 0; c < hm.samples.length; c++) {
                row.push(hm.genes[r] + "<br>Sample: " + hm.samples[c] +
                    " (" + hm.groups[c] + ")" +
                    "<br>Z-score: " + hm.z_scores[r][c].toFixed(2));
            }
            hoverText.push(row);
        }

        var trace = {
            z: hm.z_scores,
            x: hm.samples,
            y: hm.genes,
            type: "heatmap",
            colorscale: [
                [0, "#3498db"],
                [0.5, "#f8f9fa"],
                [1, "#e74c3c"]
            ],
            zmid: 0,
            hoverinfo: "text",
            text: hoverText,
            colorbar: {
                title: "Z-score",
                titleside: "right",
                thickness: 15,
                len: 0.6,
            },
        };

        var groupColors = {};
        var gi = 0;
        hm.groups.forEach(function (g) {
            if (!groupColors[g]) { groupColors[g] = COLORS[gi % COLORS.length]; gi++; }
        });

        var annotations = hm.samples.map(function (s, i) {
            return {
                x: s, y: hm.genes.length, xref: "x", yref: "y",
                text: hm.groups[i], showarrow: false,
                font: { size: 9, color: groupColors[hm.groups[i]] },
                yshift: 12,
            };
        });

        var hmLayout = Object.assign({}, plotLayout, {
            title: { text: "Top " + hm.genes.length + " DEGs — Z-score Heatmap", font: { size: 14 } },
            height: dynamicHeight,
            xaxis: { title: "", tickangle: -45, tickfont: { size: 10 } },
            yaxis: { title: "", autorange: "reversed", tickfont: { size: 9 }, dtick: 1 },
            margin: { l: 120, r: 60, t: 50, b: 80 },
            annotations: annotations,
        });

        Plotly.newPlot(el, [trace], hmLayout, plotConfig);
        el.classList.add("has-plot");
        dismissSpinner(el);
    }

    // ── Fetch job data and lazy-render plots via IntersectionObserver ──
    if (typeof Plotly !== "undefined") {
        fetch("/api/jobs/" + JOB_ID + "/", {
            headers: { "X-CSRFToken": CSRF }
        })
            .then(function (r) { return r.json(); })
            .then(function (data) {
                if (data.status !== "SUCCESS" || !data.payload || !data.payload.plot_data) return;
                var pd = data.payload.plot_data;

                // Map plot element IDs to their render functions + data
                var plotQueue = [];
                if (pd.pca) plotQueue.push({ id: "pca-plot", render: renderPCA, data: pd.pca });
                if (pd.umap) plotQueue.push({ id: "umap-plot", render: renderUMAP, data: pd.umap });
                if (pd.volcano) plotQueue.push({ id: "volcano-plot", render: renderVolcano, data: pd.volcano });
                if (pd.ma) plotQueue.push({ id: "ma-plot", render: renderMA, data: pd.ma });
                if (pd.heatmap) plotQueue.push({ id: "heatmap-plot", render: renderHeatmap, data: pd.heatmap });

                if (!plotQueue.length) return;

                // Use IntersectionObserver to lazy-render plots only when visible
                var rendered = {};
                var observer = new IntersectionObserver(function (entries) {
                    entries.forEach(function (entry) {
                        if (!entry.isIntersecting) return;
                        var elId = entry.target.id;
                        if (rendered[elId]) return;
                        rendered[elId] = true;
                        observer.unobserve(entry.target);

                        var item = plotQueue.find(function (q) { return q.id === elId; });
                        if (item) item.render(item.data);
                    });
                }, { rootMargin: "200px" });

                plotQueue.forEach(function (item) {
                    var el = document.getElementById(item.id);
                    if (el) observer.observe(el);
                });
            })
            .catch(function (err) { console.warn("Plot data fetch failed:", err); });
    }
})();
