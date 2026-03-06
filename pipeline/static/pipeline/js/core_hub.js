/**
 * RNAseek – Core Hub
 * Module card clicks, configuration modal, module submission,
 * deconvolution gateway, and spoke-unlock polling.
 */
(function () {
    "use strict";

    const CSRF = document.querySelector('meta[name="csrf-token"]').content;
    const JOB_ID = document.getElementById("core-hub-data").dataset.jobId;

    // ── Module Configuration Modal ──
    const modalBackdrop = document.getElementById("module-modal");
    const modalTitle = document.getElementById("modal-module-title");
    const modalBody = document.getElementById("modal-module-body");
    const modalSubmit = document.getElementById("modal-submit");
    const modalClose = document.querySelectorAll(".modal-close-btn");

    let currentModule = null;

    // Module-specific input templates
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

    // Default (no extra input needed)
    function defaultInputs(modName) {
        return '<p class="rna-text-sm" style="color:var(--rna-grey-500);">No additional configuration needed for <strong>' + modName + '</strong>. Click Run to start.</p>';
    }

    document.querySelectorAll(".rna-module-card[data-module]").forEach(card => {
        card.addEventListener("click", () => {
            currentModule = card.dataset.module;
            const prettyName = card.querySelector("h4").textContent;
            modalTitle.textContent = prettyName;
            const builder = moduleInputs[currentModule];
            modalBody.innerHTML = builder ? builder() : defaultInputs(prettyName);
            modalBackdrop.classList.add("active");
        });
    });

    modalClose.forEach(btn => btn.addEventListener("click", closeModal));
    modalBackdrop.addEventListener("click", e => {
        if (e.target === modalBackdrop) closeModal();
    });

    function closeModal() {
        modalBackdrop.classList.remove("active");
        currentModule = null;
    }

    // ── Module Submission ──
    modalSubmit.addEventListener("click", async () => {
        if (!currentModule) return;
        modalSubmit.disabled = true;
        modalSubmit.innerHTML = '<i class="bi bi-arrow-repeat rna-processing"></i> Running...';

        const payload = { job_id: JOB_ID };

        // Gather module-specific params
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

        const res = await fetch("/api/modules/" + currentModule + "/run", {
            method: "POST",
            headers: {
                "Content-Type": "application/json",
                "X-CSRFToken": CSRF,
            },
            body: JSON.stringify(payload),
        });

        if (res.ok) {
            const data = await res.json();
            const card = document.querySelector('[data-module="' + currentModule + '"]');
            if (card) {
                card.classList.add("rna-module-running");
                pollModuleJob(data.job_id, card);
            }
            closeModal();
        }

        modalSubmit.disabled = false;
        modalSubmit.innerHTML = '<i class="bi bi-play-fill"></i> Run Module';
    });

    function pollModuleJob(modJobId, card) {
        const iv = setInterval(async () => {
            const res = await fetch("/api/jobs/" + modJobId + "/");
            if (!res.ok) { clearInterval(iv); return; }
            const data = await res.json();
            if (data.status === "SUCCESS") {
                clearInterval(iv);
                card.classList.remove("rna-module-running");
                card.classList.add("rna-module-done");
            } else if (data.status === "FAILED") {
                clearInterval(iv);
                card.classList.remove("rna-module-running");
                card.classList.add("rna-module-failed");
            }
        }, 4000);
    }

    // ── Deconvolution Gateway ──
    const deconBtn = document.getElementById("run-deconv");
    if (deconBtn) {
        deconBtn.addEventListener("click", async () => {
            deconBtn.disabled = true;
            deconBtn.innerHTML = '<i class="bi bi-arrow-repeat rna-processing"></i> Running...';

            const atlas = document.getElementById("atlas-select")?.value;
            const hires = document.getElementById("hires-toggle")?.checked;

            const res = await fetch("/api/modules/deconvolution/run", {
                method: "POST",
                headers: {
                    "Content-Type": "application/json",
                    "X-CSRFToken": CSRF,
                },
                body: JSON.stringify({ job_id: JOB_ID, atlas: atlas, high_resolution: hires }),
            });

            if (res.ok) {
                const data = await res.json();
                pollDeconv(data.job_id);
            } else {
                deconBtn.disabled = false;
                deconBtn.innerHTML = '<i class="bi bi-play-fill"></i> Run Deconvolution';
            }
        });
    }

    function pollDeconv(deconvJobId) {
        const iv = setInterval(async () => {
            const res = await fetch("/api/jobs/" + deconvJobId + "/");
            if (!res.ok) { clearInterval(iv); return; }
            const data = await res.json();
            if (data.status === "SUCCESS") {
                clearInterval(iv);
                // Unlock spokes
                document.querySelectorAll(".spoke-card.locked").forEach(c => {
                    c.classList.remove("locked");
                    c.querySelector("a")?.removeAttribute("aria-disabled");
                });
                deconBtn.innerHTML = '<i class="bi bi-check-circle"></i> Complete';
            } else if (data.status === "FAILED") {
                clearInterval(iv);
                deconBtn.disabled = false;
                deconBtn.innerHTML = '<i class="bi bi-play-fill"></i> Run Deconvolution';
            }
        }, 5000);
    }

    // ── Download links ──
    document.querySelectorAll(".download-btn[data-asset]").forEach(btn => {
        btn.addEventListener("click", () => {
            window.open("/api/session/assets?role=" + btn.dataset.asset + "&job_id=" + JOB_ID, "_blank");
        });
    });
})();
