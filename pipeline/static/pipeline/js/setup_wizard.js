/**
 * RNAseek – Setup Wizard (Step 1/2/3)
 * Handles file selection, chunked upload, genome selection, metadata mapping,
 * and pipeline submission.
 */
(function () {
    "use strict";

    const CHUNK_SIZE = 5 * 1024 * 1024; // 5 MB
    const CSRF = document.querySelector('meta[name="csrf-token"]').content;

    // ── Elements ──
    const dropZone = document.getElementById("drop-zone");
    const fastqInput = document.getElementById("fastq-input");
    const filePills = document.getElementById("file-pills");
    const fileList = document.getElementById("file-list");
    const uploadArea = document.getElementById("upload-progress-area");
    const step1Next = document.getElementById("step1-next");
    const step2Back = document.getElementById("step2-back");
    const step2Next = document.getElementById("step2-next");
    const step3Back = document.getElementById("step3-back");
    const submitBtn = document.getElementById("submit-pipeline");
    const genomeSelect = document.getElementById("genome-select");
    const metadataBody = document.getElementById("metadata-body");

    let selectedFiles = [];
    let uploadedFiles = [];

    // ── Wizard Navigation ──
    function showStep(n) {
        document.querySelectorAll(".wizard-step").forEach(el => el.classList.remove("active"));
        document.getElementById("step-" + n).classList.add("active");
        document.querySelectorAll("#wizard-steps .rna-step").forEach(el => {
            const s = parseInt(el.dataset.step);
            el.classList.remove("active", "completed");
            if (s < n) el.classList.add("completed");
            else if (s === n) el.classList.add("active");
        });
    }

    // ── Drop Zone ──
    dropZone.addEventListener("click", () => fastqInput.click());

    dropZone.addEventListener("dragover", e => {
        e.preventDefault();
        dropZone.classList.add("drag-over");
    });

    dropZone.addEventListener("dragleave", () => dropZone.classList.remove("drag-over"));

    dropZone.addEventListener("drop", e => {
        e.preventDefault();
        dropZone.classList.remove("drag-over");
        handleFiles(e.dataTransfer.files);
    });

    fastqInput.addEventListener("change", () => handleFiles(fastqInput.files));

    function handleFiles(fileListObj) {
        for (const file of fileListObj) {
            if (!selectedFiles.some(f => f.name === file.name)) {
                selectedFiles.push(file);
            }
        }
        renderFilePills();
    }

    function renderFilePills() {
        filePills.innerHTML = "";
        if (selectedFiles.length === 0) {
            fileList.style.display = "none";
            dropZone.classList.remove("has-files");
            step1Next.disabled = true;
            return;
        }
        fileList.style.display = "block";
        dropZone.classList.add("has-files");

        selectedFiles.forEach((file, idx) => {
            const pill = document.createElement("span");
            pill.className = "file-pill";
            pill.innerHTML =
                '<i class="bi bi-file-earmark-zip"></i> ' +
                file.name +
                ' <span class="remove-file" data-idx="' + idx + '">&times;</span>';
            filePills.appendChild(pill);
        });

        filePills.querySelectorAll(".remove-file").forEach(btn => {
            btn.addEventListener("click", e => {
                e.stopPropagation();
                selectedFiles.splice(parseInt(btn.dataset.idx), 1);
                renderFilePills();
            });
        });

        step1Next.disabled = false;
    }

    // ── Step navigation handlers ──
    step1Next.addEventListener("click", async () => {
        step1Next.disabled = true;
        step1Next.innerHTML = '<i class="bi bi-arrow-repeat rna-processing"></i> Uploading...';
        uploadArea.innerHTML = "";

        for (const file of selectedFiles) {
            const totalChunks = Math.ceil(file.size / CHUNK_SIZE);
            const barId = "prog-" + file.name.replace(/\W/g, "_");

            uploadArea.insertAdjacentHTML("beforeend",
                '<div style="margin-bottom: .5rem;">' +
                '<div class="rna-text-xs" style="margin-bottom: .2rem;">' + file.name + '</div>' +
                '<div class="rna-progress"><div class="rna-progress-bar animated" id="' + barId + '" style="width:0%"></div></div>' +
                '</div>'
            );

            const bar = document.getElementById(barId);

            for (let i = 0; i < totalChunks; i++) {
                const start = i * CHUNK_SIZE;
                const end = Math.min(start + CHUNK_SIZE, file.size);
                const chunk = file.slice(start, end);

                const fd = new FormData();
                fd.append("file", chunk);
                fd.append("filename", file.name);
                fd.append("chunk_index", i);
                fd.append("total_chunks", totalChunks);

                const res = await fetch("/api/upload/chunk", {
                    method: "POST",
                    headers: { "X-CSRFToken": CSRF },
                    body: fd,
                });

                if (!res.ok) {
                    step1Next.innerHTML = 'Next: Select Reference <i class="bi bi-arrow-right"></i>';
                    step1Next.disabled = false;
                    return;
                }

                const pct = Math.round(((i + 1) / totalChunks) * 100);
                bar.style.width = pct + "%";
            }
            bar.classList.remove("animated");
            uploadedFiles.push(file.name);
        }

        step1Next.innerHTML = 'Next: Select Reference <i class="bi bi-arrow-right"></i>';
        step1Next.disabled = false;
        showStep(2);
    });

    step2Back.addEventListener("click", () => showStep(1));

    genomeSelect.addEventListener("change", () => {
        step2Next.disabled = !genomeSelect.value;
    });

    step2Next.addEventListener("click", () => {
        buildMetadataTable();
        showStep(3);
    });

    step3Back.addEventListener("click", () => showStep(2));

    // ── Metadata Table ──
    function buildMetadataTable() {
        metadataBody.innerHTML = "";
        const names = uploadedFiles.length ? uploadedFiles : selectedFiles.map(f => f.name);
        names.forEach(name => {
            const tr = document.createElement("tr");
            tr.innerHTML =
                '<td><code style="font-size:.78rem;">' + name + '</code></td>' +
                '<td><input type="text" class="rna-input condition-input" placeholder="e.g. Control" data-file="' + name + '"></td>' +
                '<td><input type="text" class="rna-input" placeholder="Optional"></td>' +
                '<td><input type="text" class="rna-input" placeholder="Optional"></td>';
            metadataBody.appendChild(tr);
        });

        metadataBody.addEventListener("input", validateMetadata);
        validateMetadata();
    }

    function validateMetadata() {
        const inputs = metadataBody.querySelectorAll(".condition-input");
        const allFilled = Array.from(inputs).every(inp => inp.value.trim() !== "");
        submitBtn.disabled = !allFilled;
    }

    // ── Submit Pipeline ──
    submitBtn.addEventListener("click", async () => {
        submitBtn.disabled = true;
        submitBtn.innerHTML = '<i class="bi bi-arrow-repeat rna-processing"></i> Submitting...';

        const rows = metadataBody.querySelectorAll("tr");
        const mapping = [];
        rows.forEach(row => {
            const cells = row.querySelectorAll("input");
            mapping.push({
                filename: cells[0].dataset.file,
                condition: cells[0].value.trim(),
                batch_id: cells[1].value.trim() || null,
                timepoint: cells[2].value.trim() || null,
            });
        });

        const payload = {
            metadata_mapping: mapping,
            reference_genome: genomeSelect.value,
            paired_end: document.getElementById("paired-end-toggle").checked,
        };

        const res = await fetch("/api/pipeline/core", {
            method: "POST",
            headers: {
                "Content-Type": "application/json",
                "X-CSRFToken": CSRF,
            },
            body: JSON.stringify(payload),
        });

        if (res.ok) {
            const data = await res.json();
            window.location.href = "/processing/" + data.job_id + "/";
        } else {
            submitBtn.disabled = false;
            submitBtn.innerHTML = '<i class="bi bi-rocket-takeoff"></i> Submit & Run Pipeline';
        }
    });
})();
