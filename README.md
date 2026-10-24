# FDR Sentinel 🛡️
> **Intelligent Fixed Deposit & Security Deposit Lifecycle Management Portal for AICTE**  
> *Smart India Hackathon (SIH) Entry — Problem Statement 26265 (AICTE, Regulation Bureau, Smart Automation)*

---

## 📌 The Problem
Technical education institutions seeking approval from the All India Council for Technical Education (AICTE) submit security deposits, primarily in the form of Fixed Deposit Receipts (FDRs) or bank guarantees. In traditional manual workflows, managing these deposits across numerous institutions involves:
* **Fragmented Physical & Spreadsheet Records:** High risk of clerical entry errors, misplaced documents, and lack of unified institution-wise tracking.
* **Lapse & Non-Renewal Risks:** Inability to track maturity horizons proactively, leading to expired FDRs without timely renewal or penalty escalation.
* **Verification Bottlenecks:** Difficulty in verifying authenticity with issuing bank branches, and inability to distinguish self-declared college claims from bank-confirmed facts.
* **Absence of Central Executive Visibility:** Regional and central leadership lack an aggregate view of deposits at risk, upcoming maturity tranches, or release liabilities.

---

## 💡 The Solution
**FDR Sentinel** transforms AICTE’s security deposit custody into an automated, tamper-detecting digital workflow:
1. **Digital Ingestion & Discrepancy Engine:** Ingests FDR documents, extracts text layers (with Tesseract OCR fallback), and flags 8 deterministic discrepancies (date inversions, deposit under-funding, cross-college duplicate receipt numbers, quarterly compounding math errors). Flagged submissions are preserved with status `DISCREPANCY_FLAGGED` to retain full audit trails.
2. **Field-Level Provenance:** Explicitly labels every data attribute as `SELF_DECLARED`, `OCR_EXTRACTED`, `MOCK_BANK_CONFIRMED` *(visibly marked as demo/simulated)*, `BANK_CONFIRMED` *(reserved for authorized live channels)*, or `OFFICER_VERIFIED`.
3. **Unified Time Handling & Proactive Escalation:** Driven by an injectable `ClockService` and a single-process scheduler, tracking 90/60/30/7-day horizons with bilingual (English/Hindi) notices, idempotent alert dispatch, and catch-up processing for time warps.
4. **Lineage-Tracked Renewals & Dual-Auth Release:** Maintains parent-child renewal lineage (parent becomes `RENEWED` only when the child reaches `ACTIVE`) and enforces a two-step human-in-the-loop release workflow (allowed from `ACTIVE`, `EXPIRING`, or `MATURED`) with statutory checklists and Ed25519-signed QR certificates.
5. **Executive Visibility & Tamper-Detecting Integrity:** Provides interactive regional maturity walls, "Deposit at Risk" exposure metrics, and a continuous SHA-256 cryptographic audit hash chain that detects unauthorized database modifications.

---

## 🚀 Feature Matrix & Build Priority

| Feature / Capability | Description | Priority Tier | Status |
| :--- | :--- | :---: | :---: |
| **Auth & RBAC Middleware** | 4 discrete roles, JWT sessions, and `is_demo_admin` flag. | **Tier 1** | `Planned` |
| **Unified ClockService** | Injected singleton temporal authority for all logic and queries. | **Tier 1** | `Planned` |
| **Tamper-Detecting Hash Chain** | SHA-256 canonical JSON chain with DB-level privilege revocation. | **Tier 1** | `Planned` |
| **Encrypted Submission Vault** | AES-256-GCM envelope encryption with master KEK. | **Tier 1** | `Planned` |
| **11-State Finite State Machine** | Strict lifecycle transitions and non-destructive discrepancy flagging. | **Tier 1** | `Planned` |
| **Deterministic Discrepancy Rules** | Core checks (DISC-01 to DISC-06, DISC-08) including duplicate isolation. | **Tier 1** | `Planned` |
| **Scrutiny Officer Console** | HTMX-powered review workstation with anomaly indicators. | **Tier 1** | `Planned` |
| **Temporal Alerts & Scheduler** | APScheduler single-process worker, 90/60/30/7-day horizons, catch-up. | **Tier 2** | `Planned` |
| **Demo "Time Machine" Clock** | Dedicated toolbar for `is_demo_admin` when `DEMO_MODE=true`. | **Tier 2** | `Planned` |
| **Maturity Wall & Risk Dashboard** | Executive Chart.js visualization of deposits at risk and monthly tranches. | **Tier 2** | `Planned` |
| **Earned Trust Score Calculator** | Low base (20) + evidence additions - rule deductions (0–100). | **Tier 2** | `Planned` |
| **Bilingual Official Notices** | English and Hindi notice templates for official communication. | **Tier 2** | `Planned` |
| **Renewal Lineage Chain** | Parent-child rollover graph; parent renews only when child is active. | **Tier 3** | `Planned` |
| **Dual-Authorized Release Workflow**| Human-in-the-loop scrutiny checklist + authority sign-off. | **Tier 3** | `Planned` |
| **Audit Verification & Tamper Test**| Linear-time chain verifier with live database tamper test. | **Tier 3** | `Planned` |
| **Seed Script & Deployment** | 50-record synthetic seed dataset, Docker Compose orchestration. | **Tier 3** | `Planned` |
| **Backup Demo Video** | Full 5-minute judge walkthrough recorded as backup deliverable. | **Tier 3** | `Planned` |
| **Text Extraction / OCR Diff Viewer**| Native PDF text layer with Tesseract OCR fallback and visual diff. | **Tier 4** | `Planned` |
| **QR-Verifiable Deposit Certificate**| Ed25519-signed public certificate omitting private bank secrets. | **Tier 4** | `Planned` |
| **Explainable Lapse-Risk Ranking** | Urgency-weighted advisory ranking: `urgency * amount * (1 / trust)`. | **Tier 4** | `Planned` |
| **Compliance Export Reports** | CSV and printable PDF report generation. | **Tier 4** | `Planned` |
| **Interactive OpenAPI 3.1 Docs** | Full interactive REST documentation (`/docs`). | **Tier 4 (MUST)** | `Planned` |
| **Outbound Webhooks & AA Stub** | Webhook notifications & Account Aggregator sandbox blueprint. | **Tier 4 (STRETCH)**| `Planned` |

---

## 🏗️ Architecture

```mermaid
graph TD
    subgraph Users [Client Surfaces]
        IU[Institution Admin<br/>Browser / Mobile]
        SO[AICTE Scrutiny Officer<br/>Workstation]
        CA[Competent Authority<br/>Dashboard]
        AU[Vigilance / Auditor<br/>Console]
    end

    subgraph Unified Application [Single FastAPI Server-Rendered Stack]
        HTTP_Router[HTTP Request Router]
        Jinja_Views[Jinja2 HTML Templates & HTMX Swaps]
        Chart_Canvas[Chart.js Maturity Wall Canvas]
        API_Endpoints[FastAPI REST API /docs - MUST]
        RBAC_Middleware[RBAC & JWT Claims Interceptor]
        Clock[ClockService - Unified Temporal Source]
        RuleEngine[Deterministic Discrepancy Engine]
        TrustScore[Earned Trust Score Calculator]
        ProvEngine[Provenance Tracking Subsystem]
        Chainer[SHA-256 Canonical Hash Logger]
        Scheduler[APScheduler Single Process]
    end

    subgraph Verification [Pluggable Verification Providers]
        V_Mock[Mock Bank Provider - Demo]
        V_Manual[Manual Officer Confirmation]
        V_AA[Account Aggregator Sandbox Stub]
    end

    subgraph Storage [Persistence Layer]
        PG[(PostgreSQL 16+<br/>Append-Only Audit Log, Institutions, Instruments)]
        Vault[(Encrypted Document Vault<br/>AES-256-GCM Envelope Encryption)]
    end

    Users --> HTTP_Router
    HTTP_Router --> Jinja_Views
    HTTP_Router --> API_Endpoints
    Jinja_Views --> Chart_Canvas

    Jinja_Views --> RBAC_Middleware
    API_Endpoints --> RBAC_Middleware

    RBAC_Middleware --> Clock
    RBAC_Middleware --> RuleEngine
    RBAC_Middleware --> TrustScore
    RBAC_Middleware --> ProvEngine
    RBAC_Middleware --> Chainer
    Scheduler --> Clock
    Scheduler --> RuleEngine
    RuleEngine --> V_Mock
    RuleEngine --> V_Manual
    RuleEngine --> V_AA
    Chainer --> PG
    ProvEngine --> PG
    API_Endpoints --> PG
    Jinja_Views --> PG
    API_Endpoints --> Vault
```

---

## 🛠️ Tech Stack
* **Full-Stack Application:** Single FastAPI application (Python 3.11+, Pydantic v2, SQLAlchemy 2.0 Async, PyJWT, Cryptography) serving both HTML and REST endpoints.
* **UI & Interactivity:** Jinja2 templates + HTMX (dynamic, SPA-like partial DOM updates without client build pipelines) + Tailwind CSS + Lucide Icons.
* **Visualizations:** Chart.js (rendered on native HTML5 canvas for the Maturity Wall and regional risk exposure).
* **Database:** PostgreSQL 16+ (Relational schema, JSONB metadata, revoked UPDATE/DELETE on audit table for application role).
* **Scheduler:** APScheduler 3.x (Single-process async worker executing scans against `ClockService`).
* **Security & Crypto:** `cryptography` (AES-256-GCM envelope encryption with master KEK, Ed25519 certificate signatures, SHA-256 canonical hash chains).
* **Document Processing:** PDF digital text-layer extraction (`pypdf` / `pdfplumber`) with Tesseract OCR fallback for scanned images.
* **Containerization:** Docker & Docker Compose (single application container + PostgreSQL container).

---

## 📁 Project Structure (Planned)

```text
FDR-SENTINEL/
├── .gitignore                      # Tracked exclusions (python, secrets, volumes)
├── README.md                       # High-level overview, architecture, & disclosures
├── SPEC.md                         # Detailed system specification with MVP Addendum
├── PROGRESS.md                     # Day-by-day roadmap, phase tracker, & decisions log
├── docker-compose.yml              # Container orchestration (FastAPI + PostgreSQL)
├── app/                            # Unified FastAPI Application
│   ├── api/                        # REST endpoints (auth, instruments, scrutiny, audit, demo)
│   ├── core/                       # Security, AES-256-GCM envelope crypto, ClockService
│   ├── models/                     # SQLAlchemy ORM models (User, Institution, Instrument, Audit)
│   ├── schemas/                    # Pydantic v2 validation contracts
│   ├── services/                   # Discrepancy engine, trust score, hash chainer, scheduler
│   ├── providers/                  # Pluggable verification providers (Mock, Manual, AA)
│   ├── templates/                  # Jinja2 HTML templates (Dashboard, Scrutiny, Audit, Time Machine)
│   └── static/                     # HTMX, Chart.js, Tailwind CSS, custom assets
├── tests/                          # Automated test suite (6 evaluation scenarios)
├── scripts/                        # Seed data generator (~50 institutions) & DB migrations
├── storage/                        # (Local Only) Encrypted document vault
└── requirements.txt
```

---

## 💻 Setup and Run Instructions
> **Notice:** The setup, environment configuration, database migration, and startup commands will be written here once implementation begins. No application code or dependencies have been installed yet.

---

## 🎬 5-Minute Evaluation Walkthrough
1. **Executive Command:** Open Dashboard as Scrutiny Officer to review active FDR status counters, regional distribution, and the interactive **Maturity Wall** displaying expiring deposits for the upcoming quarter based on the loaded seed dataset.
2. **Discrepancy Identification & Earned Trust:** Switch to Institution portal. Upload an FDR with inverted dates and a duplicate receipt number. The system preserves the record as `DISCREPANCY_FLAGGED` without crashing. Reveal the **Explainable Trust Score**: Base (20) + Valid PDF (15) - Chronological Inversion DISC-02 (40) - Duplicate Submission DISC-05 (50) = 0/100 (clamped). Note that the incumbent college's existing deposit remains untouched in `ACTIVE` status.
3. **Demo Time Machine Clock & Catch-Up Alerts:** Advance the injected application clock by 60 days via the Demo Time Machine toolbar (available to `is_demo_admin` when `DEMO_MODE=true`). Watch the single-process scheduler fire live: status shifts from `ACTIVE` to `EXPIRING`, catch-up evaluation dispatches bilingual English/Hindi notices, and an urgent Tier 2 escalation appears on the officer backlog.
4. **Renewal Lineage & Dual-Authorized Release:** Demonstrate parent-child renewal lineage, where parent FDR becomes `RENEWED` only when child reaches `ACTIVE`. Switch to Competent Authority account. Open a release request filed from a `MATURED` instrument. Inspect the officer's statutory checklist and evidence. Click **Authorize Release** to produce an Ed25519-signed QR certificate.
5. **Tamper-Detection Live Test:** Open Auditor view. Click **Verify Chain Integrity**; show green confirmation. Run a test script that directly modifies a database audit row; re-run verification; watch the indicator turn RED, pinpointing the exact compromised block ID.

---

## ⚖️ Limitations & Honest Disclosures
* **Simulated Data:** All institutional names, FDR numbers, and rupee values in the prototype are synthetically generated for demonstration.
* **Mocked Banking APIs:** There is **no live connection to commercial bank CBS or production banking networks**. Real bank verification requires formal bilateral MoUs and regulatory clearances; the prototype employs a simulated mock bank provider and manual confirmation workflows.
* **No Direct Fund Disbursements:** The portal governs legal custody and regulatory authorization; it does not process direct wire transfers or NEFT/RTGS payouts.
* **Assumptions to Verify:** Specific business rules (such as deposit slabs per discipline, joint-account wording, regional office boundaries, and penal withholding rules) represent **assumptions to verify** against the prevailing AICTE Approval Process Handbook (APH).
* **Regulatory Design Alignment:** The system architecture is designed to reflect the principles of India’s **Digital Personal Data Protection (DPDP) Act 2023**, but carries no formal statutory or security certifications.

---

## 👥 Team & License
* **Developer:** *Solo Developer Build (SIH Entry 2026)*
* **Problem Statement:** 26265 (AICTE, Regulation Bureau, Smart Automation)
* **License:** *To be specified upon formal repository submission (e.g., MIT License)*
