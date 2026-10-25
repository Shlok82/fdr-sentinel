/**
 * app/static/js/fdr_form.js
 * FDR Sentinel Submission Form Client-Side UX & Demo Scenario Controller
 * Zero external CDN dependencies.
 */
document.addEventListener("DOMContentLoaded", function () {
    const fileInput = document.getElementById("scan_file");
    const dropzone = document.getElementById("upload-dropzone");
    const fileDisplay = document.getElementById("selected-file-display");
    const fileNameEl = document.getElementById("selected-file-name");
    const fileMetaEl = document.getElementById("selected-file-meta");
    const removeBtn = document.getElementById("remove-file-btn");
    const clientErrorEl = document.getElementById("client-file-error");
    const announcer = document.getElementById("file-status-announcer");
    const submitBtn = document.getElementById("submit-fdr-btn");
    const fdrForm = document.getElementById("fdr-submission-form");

    if (!fileInput || !dropzone) {
        return;
    }

    const maxMb = parseInt(dropzone.getAttribute("data-max-mb") || "10", 10);
    const maxSizeBytes = maxMb * 1024 * 1024;
    const allowedExts = [".pdf", ".png", ".jpg", ".jpeg"];

    function formatBytes(bytes) {
        if (bytes < 1024 * 1024) {
            return (bytes / 1024).toFixed(1) + " KB";
        }
        return (bytes / (1024 * 1024)).toFixed(2) + " MB";
    }

    function announce(msg) {
        if (announcer) {
            announcer.textContent = msg;
        }
    }

    function showError(msg) {
        if (clientErrorEl) {
            clientErrorEl.textContent = msg;
            clientErrorEl.classList.remove("hidden");
        }
        announce(msg);
    }

    function clearError() {
        if (clientErrorEl) {
            clientErrorEl.textContent = "";
            clientErrorEl.classList.add("hidden");
        }
    }

    function handleFile(file) {
        clearError();

        if (!file) {
            if (fileDisplay) fileDisplay.classList.add("hidden");
            return;
        }

        const nameLower = (file.name || "").toLowerCase();
        const hasValidExt = allowedExts.some(ext => nameLower.endsWith(ext));

        if (!hasValidExt) {
            showError("Invalid file type. Only standard PDF documents, PNG images, and JPEG photos are accepted.");
            fileInput.value = "";
            if (fileDisplay) fileDisplay.classList.add("hidden");
            return;
        }

        if (file.size > maxSizeBytes) {
            showError("File size exceeds maximum permitted limit of " + maxMb + " MB.");
            fileInput.value = "";
            if (fileDisplay) fileDisplay.classList.add("hidden");
            return;
        }

        // File is valid
        if (fileNameEl) fileNameEl.textContent = file.name;
        if (fileMetaEl) fileMetaEl.textContent = (file.type || "Document") + " • " + formatBytes(file.size);
        if (fileDisplay) fileDisplay.classList.remove("hidden");
        announce("Selected document: " + file.name + ", " + formatBytes(file.size));
    }

    // File input change
    fileInput.addEventListener("change", function () {
        if (fileInput.files && fileInput.files.length > 0) {
            handleFile(fileInput.files[0]);
        } else {
            handleFile(null);
        }
    });

    // Remove file button
    if (removeBtn) {
        removeBtn.addEventListener("click", function () {
            fileInput.value = "";
            handleFile(null);
            announce("Document removed. Please select or drop a new document.");
        });
    }

    // Drag & Drop handlers
    ["dragenter", "dragover"].forEach(eventName => {
        dropzone.addEventListener(eventName, function (e) {
            e.preventDefault();
            e.stopPropagation();
            dropzone.classList.add("border-aicte-500", "bg-aicte-50/20");
        });
    });

    ["dragleave", "dragend"].forEach(eventName => {
        dropzone.addEventListener(eventName, function (e) {
            e.preventDefault();
            e.stopPropagation();
            dropzone.classList.remove("border-aicte-500", "bg-aicte-50/20");
        });
    });

    dropzone.addEventListener("drop", function (e) {
        e.preventDefault();
        e.stopPropagation();
        dropzone.classList.remove("border-aicte-500", "bg-aicte-50/20");

        if (e.dataTransfer && e.dataTransfer.files && e.dataTransfer.files.length > 0) {
            fileInput.files = e.dataTransfer.files;
            handleFile(fileInput.files[0]);
        }
    });

    // Form submit double-click prevention
    if (fdrForm && submitBtn) {
        fdrForm.addEventListener("submit", function () {
            submitBtn.disabled = true;
            submitBtn.textContent = "Submitting...";
            submitBtn.classList.add("opacity-75", "cursor-not-allowed");
        });
    }

    // --- Demo Scenarios Controller (when DEMO_MODE=true) ---
    const demoPanel = document.getElementById("demo-scenarios-panel");
    const scenarioSelect = document.getElementById("demo-scenario-select");
    const fillBtn = document.getElementById("demo-fill-btn");
    const attachBtn = document.getElementById("demo-attach-btn");
    const downloadLink = document.getElementById("demo-download-link");
    const descEl = document.getElementById("demo-scenario-desc");

    if (demoPanel && scenarioSelect && fillBtn && attachBtn) {
        scenarioSelect.addEventListener("change", function () {
            const key = scenarioSelect.value;
            if (downloadLink) {
                downloadLink.href = "/demo/fdr-sample/" + key + ".pdf";
            }
            // Update description preview
            fetch("/demo/fdr-scenarios/" + key)
                .then(res => res.json())
                .then(data => {
                    if (descEl) {
                        let text = data.description || "";
                        if (data.note) {
                            text += " (" + data.note + ")";
                        }
                        descEl.textContent = text;
                    }
                })
                .catch(() => {});
        });

        fillBtn.addEventListener("click", function () {
            const key = scenarioSelect.value;
            const originalText = fillBtn.textContent;
            fillBtn.disabled = true;
            fillBtn.textContent = "Filling...";

            fetch("/demo/fdr-scenarios/" + key)
                .then(res => {
                    if (!res.ok) throw new Error("Failed to load scenario data");
                    return res.json();
                })
                .then(data => {
                    const fields = [
                        "fdr_number", "bank_name", "bank_branch", "ifsc_code",
                        "principal_amount", "maturity_amount", "interest_rate_percent",
                        "tenure_months", "issue_date", "maturity_date", "joint_account_holder"
                    ];
                    fields.forEach(field => {
                        const input = document.getElementById(field);
                        if (input) {
                            input.value = data[field] || "";
                        }
                    });

                    if (descEl) {
                        let text = data.description || "";
                        if (data.note) {
                            text += " (" + data.note + ")";
                        }
                        descEl.textContent = text;
                    }
                    announce("Scenario fields populated: " + data.title);
                })
                .catch(err => {
                    alert("Error loading scenario: " + err.message);
                })
                .finally(() => {
                    fillBtn.disabled = false;
                    fillBtn.textContent = originalText;
                });
        });

        attachBtn.addEventListener("click", function () {
            const key = scenarioSelect.value;
            const originalText = attachBtn.textContent;
            attachBtn.disabled = true;
            attachBtn.textContent = "Attaching...";

            fetch("/demo/fdr-sample/" + key + ".pdf")
                .then(res => {
                    if (!res.ok) throw new Error("Failed to fetch sample document");
                    return res.blob();
                })
                .then(blob => {
                    const fileName = "sample_" + key + ".pdf";
                    const file = new File([blob], fileName, { type: "application/pdf" });
                    const dt = new DataTransfer();
                    dt.items.add(file);
                    fileInput.files = dt.files;
                    handleFile(file);
                    announce("Sample document attached: " + fileName);
                })
                .catch(err => {
                    alert("Error attaching document: " + err.message);
                })
                .finally(() => {
                    attachBtn.disabled = false;
                    attachBtn.textContent = originalText;
                });
        });
    }
});
