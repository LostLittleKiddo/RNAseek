/**
 * RNAseek – Setup Wizard (Step 1/2/3)
 * Handles library type selection, file selection, chunked upload,
 * genome selection (incl. custom genome), metadata mapping,
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
    const pairedEndTip = document.getElementById("paired-end-tip");
    const customGenomeSection = document.getElementById("custom-genome-section");
    const customGenomeName = document.getElementById("custom-genome-name");
    const customGenomeFasta = document.getElementById("custom-genome-fasta");
    const customGenomeAnnotation = document.getElementById("custom-genome-annotation");

    let selectedFiles = [];
    let uploadedFiles = [];
    let customGenomeFiles = { fasta: null, annotation: null };

    // ── Library Type Selection ──
    const libRadios = document.querySelectorAll('input[name="library_type"]');
    const ltCards = document.querySelectorAll(".library-type-card");

    libRadios.forEach(radio => {
        radio.addEventListener("change", () => {
            ltCards.forEach(c => c.classList.remove("selected"));
            radio.closest(".library-type-card").classList.add("selected");
            if (radio.value === "paired") {
                pairedEndTip.classList.add("visible");
            } else {
                pairedEndTip.classList.remove("visible");
            }
            validateStep1();
        });
    });

    function getLibraryType() {
        const checked = document.querySelector('input[name="library_type"]:checked');
        return checked ? checked.value : null;
    }

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

    function validateStep1() {
        const hasLibType = getLibraryType() !== null;
        const hasFiles = selectedFiles.length > 0;
        step1Next.disabled = !(hasLibType && hasFiles);
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
            validateStep1();
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

        validateStep1();
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

    // ── Genome Selection + Custom Genome ──
    genomeSelect.addEventListener("change", () => {
        const isCustom = genomeSelect.value === "custom";
        if (isCustom) {
            customGenomeSection.classList.add("visible");
        } else {
            customGenomeSection.classList.remove("visible");
        }
        validateStep2();
    });

    customGenomeName.addEventListener("input", validateStep2);
    customGenomeFasta.addEventListener("change", () => {
        customGenomeFiles.fasta = customGenomeFasta.files[0] || null;
        validateStep2();
    });
    customGenomeAnnotation.addEventListener("change", () => {
        customGenomeFiles.annotation = customGenomeAnnotation.files[0] || null;
        validateStep2();
    });

    function validateStep2() {
        if (!genomeSelect.value) {
            step2Next.disabled = true;
            return;
        }
        if (genomeSelect.value === "custom") {
            const nameOk = customGenomeName.value.trim().length > 0;
            const fastaOk = customGenomeFiles.fasta !== null;
            const annotOk = customGenomeFiles.annotation !== null;
            step2Next.disabled = !(nameOk && fastaOk && annotOk);
        } else {
            step2Next.disabled = false;
        }
    }

    step2Next.addEventListener("click", async () => {
        // If custom genome, upload the files first
        if (genomeSelect.value === "custom") {
            step2Next.disabled = true;
            step2Next.innerHTML = '<i class="bi bi-arrow-repeat rna-processing"></i> Uploading genome...';

            const filesToUpload = [
                { file: customGenomeFiles.fasta, role: "CUSTOM_GENOME_FASTA" },
                { file: customGenomeFiles.annotation, role: "CUSTOM_GENOME_ANNOTATION" },
            ];

            for (const item of filesToUpload) {
                const totalChunks = Math.ceil(item.file.size / CHUNK_SIZE);
                for (let i = 0; i < totalChunks; i++) {
                    const start = i * CHUNK_SIZE;
                    const end = Math.min(start + CHUNK_SIZE, item.file.size);
                    const chunk = item.file.slice(start, end);

                    const fd = new FormData();
                    fd.append("file", chunk);
                    fd.append("filename", item.file.name);
                    fd.append("chunk_index", i);
                    fd.append("total_chunks", totalChunks);
                    fd.append("file_role", item.role);

                    const res = await fetch("/api/upload/chunk", {
                        method: "POST",
                        headers: { "X-CSRFToken": CSRF },
                        body: fd,
                    });

                    if (!res.ok) {
                        step2Next.innerHTML = 'Next: Metadata <i class="bi bi-arrow-right"></i>';
                        step2Next.disabled = false;
                        return;
                    }
                }
            }

            step2Next.innerHTML = 'Next: Metadata <i class="bi bi-arrow-right"></i>';
            step2Next.disabled = false;
        }

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
            library_type: getLibraryType(),
        };

        if (genomeSelect.value === "custom") {
            payload.custom_genome_name = customGenomeName.value.trim();
        }

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
