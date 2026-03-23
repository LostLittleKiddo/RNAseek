/**
 * RNAseek – Core Hub
 * Tab navigation, module cards with status, configuration modal,
 * module submission, deconvolution gateway, and plot rendering.
 */
(function () {
    "use strict";

    const CSRF = document.querySelector('meta[name="csrf-token"]').content;
    const hubData = document.getElementById("core-hub-data");
    const JOB_ID = hubData.dataset.jobId;
    const SUBMISSION_ID = hubData.dataset.submissionId || "";

    // Parse server-provided module job statuses
    var moduleJobs = {};
    try { moduleJobs = JSON.parse(hubData.dataset.moduleJobs || "{}"); } catch (_) { }

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

            // Trigger Plotly resize on the currently-visible viz pane when switching to overview tab
            if (target === "overview" && typeof Plotly !== "undefined") {
                var activePane = document.querySelector(".viz-theater-pane.active");
                if (activePane) {
                    var plotEl = activePane.querySelector(".rna-plot-container");
                    if (plotEl && plotEl.data) Plotly.Plots.resize(plotEl);
                }
            }
        });
    });

    // ── Module Status Badges ──
    function applyModuleStatuses() {
        document.querySelectorAll(".rna-module-card[data-module]").forEach(function (card) {
            var mod = card.dataset.module;
            var info = moduleJobs[mod];
            var badge = card.querySelector(".rna-module-status-badge");
            if (!info || !badge) return;

            card.classList.remove("rna-module-running", "rna-module-done", "rna-module-failed");
            badge.className = "rna-module-status-badge";

            if (info.status === "SUCCESS") {
                card.classList.add("rna-module-done");
                badge.classList.add("status-done");
                badge.textContent = "Completed";
            } else if (info.status === "RUNNING" || info.status === "PENDING") {
                card.classList.add("rna-module-running");
                badge.classList.add("status-running");
                badge.textContent = "Running";
            } else if (info.status === "FAILED") {
                card.classList.add("rna-module-failed");
                badge.classList.add("status-failed");
                badge.textContent = "Failed";
            }
        });
    }
    applyModuleStatuses();

    // ── Module Configuration Modal ──
    const modalBackdrop = document.getElementById("module-modal");
    const modalTitle = document.getElementById("modal-module-title");
    const modalBody = document.getElementById("modal-module-body");
    const modalSubmit = document.getElementById("modal-submit");
    const modalClose = document.querySelectorAll(".modal-close-btn");

    let currentModule = null;

    const moduleInputs = {
        wgcna: function () {
            return '<label class="rna-label">Clinical Trait CSV (optional)</label>' +
                '<input type="file" class="rna-input" id="mod-trait-file" accept=".csv">' +
                '<label class="rna-label" style="margin-top:.8rem;">Soft-Power Threshold</label>' +
                '<input type="number" class="rna-input" id="mod-soft-power" value="6" min="1" max="30">';
        },
        gsea: function () {
            return '<label class="rna-label">Gene Set Database</label>' +
                '<select class="rna-select" id="mod-geneset">' +
                '<option value="hallmark">MSigDB Hallmark (H)</option>' +
                '<option value="c2_kegg">C2 KEGG</option>' +
                '<option value="c5_go_bp">C5 GO Biological Process</option>' +
                '<option value="c5_go_mf">C5 GO Molecular Function</option>' +
                '</select>' +
                '<label class="rna-label" style="margin-top:.8rem;">FDR Threshold</label>' +
                '<input type="number" class="rna-input" id="mod-fdr" value="0.25" step="0.05" min="0" max="1">';
        },
        survival: function () {
            return '<label class="rna-label">Time Column</label>' +
                '<input type="text" class="rna-input" id="mod-time-col" placeholder="e.g. OS_months">' +
                '<label class="rna-label" style="margin-top:.8rem;">Censoring Column</label>' +
                '<input type="text" class="rna-input" id="mod-censor-col" placeholder="e.g. vital_status">' +
                '<label class="rna-label" style="margin-top:.8rem;">Genes of Interest (comma-separated)</label>' +
                '<input type="text" class="rna-input" id="mod-genes" placeholder="e.g. TP53, BRCA1">';
        },
        mofa: function () {
            return '<label class="rna-label">Additional Omics Layer (optional CSV)</label>' +
                '<input type="file" class="rna-input" id="mod-omics-file" accept=".csv,.tsv">' +
                '<label class="rna-label" style="margin-top:.8rem;">Number of Factors</label>' +
                '<input type="number" class="rna-input" id="mod-factors" value="10" min="2" max="50">';
        },
        diablo: function () {
            return '<label class="rna-label">Additional Omics Layer (CSV/TSV)</label>' +
                '<input type="file" class="rna-input" id="mod-diablo-file" accept=".csv,.tsv">' +
                '<label class="rna-label" style="margin-top:.8rem;">Number of Components</label>' +
                '<input type="number" class="rna-input" id="mod-components" value="3" min="2" max="20">';
        },
        immune: function () {
            return '<label class="rna-label">Method</label>' +
                '<select class="rna-select" id="mod-immune-method">' +
                '<option value="cibersort">CIBERSORTx</option>' +
                '<option value="xcell">xCell</option>' +
                '<option value="mcp_counter">MCP-counter</option>' +
                '</select>';
        },
    };

    function defaultInputs(modName) {
        return '<p class="rna-text-sm" style="color:var(--rna-grey-500);">No additional configuration needed for <strong>' + modName + '</strong>. Click Run to start.</p>';
    }

    // Module card click: completed modules show results; others open the config modal
    document.querySelectorAll(".rna-module-card[data-module]").forEach(function (card) {
        card.addEventListener("click", function () {
            var mod = card.dataset.module;
            var info = moduleJobs[mod];

            if (info && info.status === "SUCCESS") {
                showResultPanel(mod, info);
                return;
            }

            currentModule = mod;
            var prettyName = card.querySelector("h3").textContent;
            modalTitle.textContent = prettyName;
            var builder = moduleInputs[currentModule];
            modalBody.innerHTML = builder ? builder() : defaultInputs(prettyName);
            modalBackdrop.classList.add("open");
        });
    });

    modalClose.forEach(function (btn) { btn.addEventListener("click", closeModal); });
    modalBackdrop.addEventListener("click", function (e) {
        if (e.target === modalBackdrop) closeModal();
    });

    function closeModal() {
        modalBackdrop.classList.remove("open");
        currentModule = null;
    }

    // ── Result Panel ──
    var resultPanel = document.getElementById("module-result-panel");
    var resultTitle = document.getElementById("result-panel-title");
    var resultBody = document.getElementById("result-panel-body");
    var resultClose = document.getElementById("result-panel-close");

    if (resultClose) {
        resultClose.addEventListener("click", function () {
            resultPanel.style.display = "none";
        });
    }

    function showResultPanel(mod, info) {
        var card = document.querySelector('[data-module="' + mod + '"]');
        var name = card ? card.querySelector("h3").textContent : mod;
        resultTitle.textContent = name + " — Results";

        var payload = info.payload || {};
        var html = '<p class="rna-text-sm rna-text-muted">Job completed at ' + (info.updated_at || "N/A") + '</p>';

        if (payload.summary) {
            html += '<p>' + payload.summary + '</p>';
        }
        if (payload.plot_data) {
            html += '<div class="rna-plot-container" id="module-result-plot" style="min-height: 300px;"></div>';
        }
        if (payload.table_preview) {
            html += '<div style="overflow-x: auto; margin-top: 1rem;">' + payload.table_preview + '</div>';
        }
        if (!payload.summary && !payload.plot_data && !payload.table_preview) {
            html += '<p class="rna-text-sm">Result payload stored. Download or view via API.</p>';
        }

        resultBody.innerHTML = html;
        resultPanel.style.display = "";
        resultPanel.scrollIntoView({ behavior: "smooth", block: "nearest" });
    }

    // ── Module Submission ──
    modalSubmit.addEventListener("click", async function () {
        if (!currentModule) return;
        modalSubmit.disabled = true;
        modalSubmit.innerHTML = '<i class="bi bi-arrow-repeat rna-processing"></i> Running...';

        var payload = { job_id: JOB_ID };

        if (currentModule === "wgcna") {
            payload.soft_power = parseInt(document.getElementById("mod-soft-power")?.value || 6);
        } else if (currentModule === "gsea") {
            payload.gene_set = document.getElementById("mod-geneset")?.value;
            payload.fdr = parseFloat(document.getElementById("mod-fdr")?.value || 0.25);
        } else if (currentModule === "survival") {
            payload.time_col = document.getElementById("mod-time-col")?.value;
            payload.censor_col = document.getElementById("mod-censor-col")?.value;
            payload.genes = document.getElementById("mod-genes")?.value;
        } else if (currentModule === "mofa") {
            payload.n_factors = parseInt(document.getElementById("mod-factors")?.value || 10);
        } else if (currentModule === "diablo") {
            payload.n_components = parseInt(document.getElementById("mod-components")?.value || 3);
        } else if (currentModule === "immune") {
            payload.method = document.getElementById("mod-immune-method")?.value;
        }

        var url = "/api/submissions/" + SUBMISSION_ID + "/modules/" + currentModule + "/run";
        var res = await fetch(url, {
            method: "POST",
            headers: {
                "Content-Type": "application/json",
                "X-CSRFToken": CSRF,
            },
            body: JSON.stringify(payload),
        });

        if (res.ok) {
            var data = await res.json();
            var card = document.querySelector('[data-module="' + currentModule + '"]');
            if (card) {
                card.classList.add("rna-module-running");
                var badge = card.querySelector(".rna-module-status-badge");
                if (badge) { badge.className = "rna-module-status-badge status-running"; badge.textContent = "Running"; }
                pollModuleJob(data.job_id, card, currentModule);
            }
            closeModal();
        }

        modalSubmit.disabled = false;
        modalSubmit.innerHTML = '<i class="bi bi-play-circle"></i> Run Module';
    });

    function pollModuleJob(modJobId, card, moduleName) {
        var iv = setInterval(async function () {
            var res = await fetch("/api/jobs/" + modJobId + "/");
            if (!res.ok) { clearInterval(iv); return; }
            var data = await res.json();
            var badge = card.querySelector(".rna-module-status-badge");
            if (data.status === "SUCCESS") {
                clearInterval(iv);
                card.classList.remove("rna-module-running");
                card.classList.add("rna-module-done");
                if (badge) { badge.className = "rna-module-status-badge status-done"; badge.textContent = "Completed"; }
                moduleJobs[moduleName] = { status: "SUCCESS", payload: data.payload || {}, updated_at: data.updated_at };
            } else if (data.status === "FAILED") {
                clearInterval(iv);
                card.classList.remove("rna-module-running");
                card.classList.add("rna-module-failed");
                if (badge) { badge.className = "rna-module-status-badge status-failed"; badge.textContent = "Failed"; }
                moduleJobs[moduleName] = { status: "FAILED" };
            }
        }, 4000);
    }

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
                document.querySelectorAll("#advanced-spokes .rna-module-card.locked").forEach(function (c) {
                    c.classList.remove("locked");
                });
                deconBtn.innerHTML = '<i class="bi bi-check-circle"></i> Complete';
            } else if (data.status === "FAILED") {
                clearInterval(iv);
                deconBtn.disabled = false;
                deconBtn.innerHTML = '<i class="bi bi-play-circle"></i> Run Deconvolution';
            }
        }, 5000);
    }

    // ── Download links ──
    document.querySelectorAll(".download-btn[data-asset]").forEach(function (btn) {
        btn.addEventListener("click", function () {
            window.open("/api/session/assets?role=" + btn.dataset.asset + "&job_id=" + JOB_ID, "_blank");
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

    function renderPCA(pca) {
        var el = document.getElementById("pca-plot");
        if (!el) return;
        el.innerHTML = "";
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
    }

    function renderUMAP(umap) {
        var el = document.getElementById("umap-plot");
        if (!el) return;
        el.innerHTML = "";
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
    }

    function renderVolcano(v) {
        var el = document.getElementById("volcano-plot");
        if (!el) return;
        el.innerHTML = "";
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
    }

    function renderMA(ma) {
        var el = document.getElementById("ma-plot");
        if (!el) return;
        el.innerHTML = "";
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
    }

    function renderHeatmap(hm) {
        var el = document.getElementById("heatmap-plot");
        if (!el) return;
        el.innerHTML = "";

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
    }

    // ── Fetch job data and render plots ──
    if (typeof Plotly !== "undefined") {
        fetch("/api/jobs/" + JOB_ID + "/", {
            headers: { "X-CSRFToken": CSRF }
        })
            .then(function (r) { return r.json(); })
            .then(function (data) {
                if (data.status !== "SUCCESS" || !data.payload || !data.payload.plot_data) return;
                var pd = data.payload.plot_data;
                if (pd.pca) renderPCA(pd.pca);
                if (pd.umap) renderUMAP(pd.umap);
                if (pd.volcano) renderVolcano(pd.volcano);
                if (pd.ma) renderMA(pd.ma);
                if (pd.heatmap) renderHeatmap(pd.heatmap);
            })
            .catch(function (err) { console.warn("Plot data fetch failed:", err); });
    }
})();
