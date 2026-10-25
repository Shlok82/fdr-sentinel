<div align="center">
  <img src="app/static/images/logo_wordmark.png" alt="FDR Sentinel Wordmark Logo" width="360" />
  <p><strong>Intelligent Fixed Deposit & Security Deposit Lifecycle Management Portal for AICTE</strong><br/>
  <em>Smart India Hackathon (SIH) Entry — Problem Statement 26265 (AICTE, Regulation Bureau, Smart Automation)</em></p>
</div>

---

## 📌 The Problem
Technical education institutions seeking approval from the All India Council for Technical Education (AICTE) submit security deposits, primarily in the form of Fixed Deposit Receipts (FDRs) or bank guarantees. In traditional manual workflows, managing these deposits across numerous institutions involves:
* **Fragmented Physical & Spreadsheet Records:** High risk of clerical entry errors, misplaced documents, and lack of unified institution-wise tracking.
* **Lapse & Non-Renewal Risks:** Inability to track maturity horizons proactively, leading to expired FDRs without timely renewal or penalty escalation.
* **Verification Bottlenecks:** Difficulty in verifying authenticity with issuing bank branches, and inability to distinguish self-declared college claims from bank-confirmed facts.
* **Absence of Central Executive Visibility:** Regional and central leadership lack an aggregate view of deposits at risk, upcoming maturity tranches, or release liabilities.

---

## 💡 The Solution (Target Design)
**FDR Sentinel** transforms AICTE’s security deposit custody into an automated, tamper-detecting digital workflow:
1. **Digital Ingestion & Discrepancy Engine:** Ingests FDR documents and evaluates deterministic discrepancies (7 core discrepancy rules ship first in Stage 6: date inversions, deposit under-funding, cross-college duplicate receipt numbers, math errors; `DISC-07` text extraction diff and OCR fallback arrive in Tier 4). Flagged submissions are preserved with status `DISCREPANCY_FLAGGED` to retain full audit trails.
2. **Field-Level Provenance:** Explicitly labels every data attribute as `SELF_DECLARED`, `OCR_EXTRACTED` *(Tier 4)*, `MOCK_BANK_CONFIRMED` *(visibly marked as demo/simulated)*, `BANK_CONFIRMED` *(reserved for authorized live channels)*, or `OFFICER_VERIFIED`.
3. **Unified Time Handling & Proactive Escalation:** Driven by an injectable `ClockService` and a single-process scheduler, tracking 90/60/30/7-day horizons with bilingual (English/Hindi) notices, idempotent alert dispatch, and catch-up processing for time warps.
4. **Lineage-Tracked Renewals & Dual-Auth Release:** Maintains parent-child renewal lineage (parent becomes `RENEWED` only when the child reaches `ACTIVE`) and enforces a two-step human-in-the-loop release workflow (allowed from `ACTIVE`, `EXPIRING`, or `MATURED`) with statutory checklists and Ed25519-signed QR certificates *(part of the target vision)*.
5. **Executive Visibility & Tamper-Detecting Integrity:** Provides interactive regional maturity walls, "Deposit at Risk" exposure metrics, and a continuous SHA-256 cryptographic audit hash chain that detects unauthorized database modifications.

---

## 🚀 Feature Matrix & Build Priority

| Feature / Capability | Description | Priority Tier | Status |
| :--- | :--- | :---: | :---: |
| **Auth & RBAC Middleware** | 4 discrete roles, JWT sessions, and `is_demo_admin` flag. | **Tier 1** | `Done` |
| **Unified ClockService** | Injected singleton temporal authority for all logic and queries. | **Tier 1** | `Done` |
| **Tamper-Detecting Hash Chain** | SHA-256 canonical JSON chain with DB-level privilege revocation. | **Tier 1** | `Done` |
| **Encrypted Submission Vault** | AES-256-GCM envelope encryption with master KEK. | **Tier 1** | `Done` |
| **11-State Finite State Machine** | Strict lifecycle transitions, prior-state restoration, and DB CHECK constraint. | **Tier 1** | `Done` |
| **Deterministic Discrepancy Rules** | Core checks (DISC-01 to DISC-06, DISC-08) including duplicate isolation. | **Tier 1** | `Planned` |
| **Scrutiny Officer Console** | HTMX-powered review workstation with anomaly indicators. | **Tier 1** | `Planned` |
| **Temporal Alerts & Scheduler** | APScheduler single-process worker, 90/60/30/7-day horizons, catch-up. | **Tier 2** | `Planned` |
| **Demo "Time Machine" Clock** | Dedicated toolbar for `is_demo_admin` when `DEMO_MODE=true`. | **Tier 2** | `Done` (clock control; scheduler integration pending) |
| **Maturity Wall & Risk Dashboard** | Executive Chart.js visualization of deposits at risk and monthly tranches. | **Tier 2** | `Planned` |
| **Earned Trust Score Calculator** | Low base (20) + evidence additions - rule deductions (0–100). | **Tier 2** | `Planned` |
| **Bilingual Official Notices** | English and Hindi notice templates for official communication. | **Tier 2** | `Planned` |
| **Renewal Lineage Chain** | Parent-child rollover graph; parent renews only when child is active. | **Tier 3** | `Planned` |
| **Dual-Authorized Release Workflow**| Human-in-the-loop scrutiny checklist + authority sign-off. | **Tier 3** | `Planned` |
| **Audit Verification & Tamper Test**| Linear-time chain verifier with live database tamper test. | **Tier 3** | `Partial` (verification page and automated tamper tests exist; scripted live tamper demo not done) |
| **Seed Script & Deployment** | Multi-institution synthetic seed dataset, Docker Compose orchestration. | **Tier 3** | `Partial` (role users and two sample institutions exist; the 50-institution seed and deployment verification in scripts/verify_deployment.sh do not exist) |
| **Backup Demo Video** | Full 5-minute judge walkthrough recorded as backup deliverable. | **Tier 3** | `Planned` |
| **Text Extraction / OCR Diff Viewer**| Native PDF text layer with Tesseract OCR fallback and visual diff. | **Tier 4** | `Planned` |
| **QR-Verifiable Deposit Certificate**| Ed25519-signed public certificate omitting private bank secrets. | **Tier 4** | `Planned` |
| **Explainable Lapse-Risk Ranking** | Urgency-weighted advisory ranking: `urgency * amount * (1 / trust)`. | **Tier 4** | `Planned` |
| **Compliance Export Reports** | CSV and printable PDF report generation. | **Tier 4** | `Planned` |
| **Interactive OpenAPI 3.1 Docs** | Full interactive REST documentation (`/docs`). | **Tier 4 (MUST)** | `Available` (auto-generated; note that most routes are HTML pages, OpenAPI 3.1.0) |
| **Outbound Webhooks & AA Stub** | Webhook notifications & Account Aggregator sandbox blueprint. | **Tier 4 (STRETCH)**| `Planned` |

Sections above describe the target design; this table shows what is implemented today.

---

## 🏗️ Architecture

```mermaid
graph TD
    subgraph Users [Client Surfaces]
        IU[Institution Admin<br/>Browser / Mobile]
        SO[AICTE Scrutiny Officer<br/>Workstation (planned)]
        CA[Competent Authority<br/>Dashboard (planned)]
        AU[Vigilance / Auditor<br/>Console]
    end

    subgraph Unified Application [Single FastAPI Server-Rendered Stack]
        HTTP_Router[HTTP Request Router]
        Jinja_Views[Jinja2 HTML Templates & HTMX Swaps]
        Chart_Canvas[Chart.js Maturity Wall Canvas (planned)]
        API_Endpoints[FastAPI REST API /docs - OpenAPI 3.1.0]
        RBAC_Middleware[RBAC & JWT Claims Interceptor]
        Clock[ClockService - Unified Temporal Source]
        FSM[Instrument FSM - 11-State Lifecycle Engine]
        DocVault[Document Vault - AES-256-GCM Envelope Encryption]
        RuleEngine[Deterministic Discrepancy Engine (planned)]
        TrustScore[Earned Trust Score Calculator (planned)]
        ProvEngine[Provenance Tracking Subsystem]
        Chainer[SHA-256 Canonical Hash Logger]
        Scheduler[APScheduler Single Process (planned)]
    end

    subgraph Verification [Pluggable Verification Providers (planned)]
        V_Mock[Mock Bank Provider - Demo (planned)]
        V_Manual[Manual Officer Confirmation (planned)]
        V_AA[Account Aggregator Sandbox Stub (planned)]
    end

    subgraph Storage [Persistence Layer]
        PG[(PostgreSQL 16+<br/>Append-Only Audit Log, Institutions, Instruments)]
        Vault[(Encrypted Document Vault Storage<br/>Local File Shards + Envelope Encryption)]
    end

    Users --> HTTP_Router
    HTTP_Router --> Jinja_Views
    HTTP_Router --> API_Endpoints
    Jinja_Views --> Chart_Canvas

    Jinja_Views --> RBAC_Middleware
    API_Endpoints --> RBAC_Middleware

    RBAC_Middleware --> Clock
    RBAC_Middleware --> FSM
    RBAC_Middleware --> DocVault
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
    FSM --> PG
    ProvEngine --> PG
    API_Endpoints --> PG
    Jinja_Views --> PG
    DocVault --> Vault
```

---

## 🛠️ Tech Stack
* **Full-Stack Application:** Single FastAPI application (Python 3.11, Pydantic v2, synchronous SQLAlchemy 2.0 with `psycopg`, PyJWT, Cryptography, Bcrypt) serving both HTML views and REST endpoints.
* **UI & Interactivity:** Jinja2 templates + HTMX (dynamic, SPA-like partial DOM updates without client build pipelines) + Tailwind CSS + Lucide Icons. Local vendored static assets (`htmx.min.js`, `tailwind.min.js`) ensure no third-party CDN dependencies for current pages (vendored assets).
* **Visualizations:** Chart.js (rendered on native HTML5 canvas for the Maturity Wall and regional risk exposure; planned).
* **Database & Roles:** PostgreSQL 16+ with strict role segregation: administrative migrations and seeding run under `fdr_owner`; application runtime executes strictly under `fdr_app_user` with `UPDATE`, `DELETE`, and `TRUNCATE` revoked on `audit_log`. Separate `fdr_sentinel_test` database for automated tests.
* **Scheduler:** APScheduler 3.x (Single-process worker executing scans against `ClockService`; planned).
* **Security & Crypto:** `cryptography` (AES-256-GCM envelope encryption with master KEK, SHA-256 canonical hash chains, Bcrypt password hashing with constant-time dummy timing mitigation, double-submit cookie CSRF; Ed25519 certificate signatures planned).
* **Document Processing:** Local encrypted document vault; PDF digital text-layer extraction (`pypdf` / `pdfplumber`) with Tesseract OCR fallback for scanned images (planned for Tier 4).
* **Containerization:** Docker & Docker Compose (single application container `fdr_sentinel_web` + PostgreSQL container `fdr_sentinel_db`).

---

## 📁 Project Structure

```text
FDR-SENTINEL/
├── .dockerignore                   # Docker build exclusions
├── .env.example                    # Sample environment configuration template
├── .gitattributes                  # Git repository attributes
├── .gitignore                      # Tracked exclusions (python, secrets, volumes)
├── alembic.ini                     # Alembic migration configuration
├── docker-compose.yml              # Container orchestration (web + db services)
├── Dockerfile                      # Application container definition (Python 3.11)
├── PROGRESS.md                     # Roadmap, phase tracker, & decisions log
├── README.md                       # System overview, architecture, & setup guide
├── requirements.txt                # Python package dependencies
├── SPEC.md                         # Detailed system specification with MVP Addendum
├── alembic/                        # Database migration scripts
│   ├── env.py                      # Migration runtime environment (uses fdr_owner)
│   ├── script.py.mako              # Migration template
│   └── versions/                   # Versioned migration revisions (0001, 0002, 0003)
├── app/                            # Unified FastAPI Application
│   ├── core/                       # Security, config, dependencies, database session
│   ├── models/                     # SQLAlchemy ORM models (User, Institution, Instrument, Document, Audit, Clock, etc.)
│   ├── routers/                    # Web routers (auth, demo, health, pages, rest, audit)
│   ├── schemas/                    # Pydantic v2 validation contracts
│   ├── services/                   # Application domain services
│   │   ├── audit_service.py        # Cryptographic SHA-256 hash-chain append & verify
│   │   ├── clock_service.py        # Injected singleton temporal authority
│   │   ├── demo_documents.py       # Built-in synthetic PDF specimen generator
│   │   ├── demo_scenarios.py       # Submission presets for evaluation testing
│   │   ├── document_service.py     # AES-256-GCM envelope encryption vault
│   │   ├── instrument_fsm.py       # 11-state lifecycle FSM & state transitions
│   │   ├── submission_service.py   # Multi-table atomic submission ingestion
│   │   ├── discrepancy_engine.py   # (planned) Deterministic discrepancy checks
│   │   ├── trust_score.py          # (planned) Earned Trust Score calculator
│   │   └── scheduler.py            # (planned) APScheduler single-process worker
│   ├── static/                     # Vendored and local static assets
│   │   ├── images/                 # App logos and wordmark brand assets
│   │   ├── js/                     # Client-side helper scripts (fdr_form.js)
│   │   ├── vendor/                 # Vendored offline JS/CSS (htmx.min.js, tailwind.min.js)
│   │   ├── favicon.ico             # Application favicon
│   │   └── favicon.png             # Application favicon PNG
│   └── templates/                  # Jinja2 templates
│       ├── auth/                   # Login view & demo credentials drawer
│       ├── components/             # Reusable UI components & modals
│       ├── errors/                 # Styled 400, 403, 404, 500 error templates
│       ├── institution/            # FDR submission form & institution dashboard
│       ├── scrutiny/               # Staff detail & audit timeline views
│       ├── partials/               # HTMX partial fragments
│       └── base.html               # Master layout shell with Time Machine toolbar
├── images/                         # Source branding graphics
│   ├── logo.png                    # Brand shield icon
│   └── logo+wordmark.png           # Master brand graphic
├── scripts/                        # Administrative and maintenance scripts
│   ├── entrypoint.sh               # Container startup script (migrations + single worker)
│   ├── init-db.sql                 # PostgreSQL user and role initialization
│   ├── prepare_branding.py         # Image cropping and branding generator
│   ├── seed_users.py               # Idempotent demo user and institution seeder
│   └── verify_stage1.py            # Foundation validation script
├── storage/                        # Encrypted document vault storage (local volume)
└── tests/                          # Automated test suite (137 passed across 13 modules)
```

---

## 💻 Setup and Run Instructions

### 1. Prerequisites
* **Docker Desktop** (with Linux containers enabled)
* **Git** and PowerShell (or bash)
* **Python 3.11+** (for generating secrets locally if desired)

### 2. Step-by-Step Setup from a Fresh Clone

#### Step A: Copy Environment Configuration
```powershell
# Clone and enter the repository directory
cd FDR-SENTINEL

# Create local environment configuration file
cp .env.example .env
```

#### Step B: Generate Cryptographic Secrets and Configure `.env`
Generate cryptographically strong values for `SECRET_KEY` and `DOCUMENT_MASTER_KEY` using Python:
```powershell
# Generate SECRET_KEY (48+ random urlsafe characters)
python -c "import secrets;print(secrets.token_urlsafe(48))"

# Generate DOCUMENT_MASTER_KEY (base64-encoded 32 random bytes for AES-256-GCM)
python -c "import os,base64;print(base64.b64encode(os.urandom(32)).decode())"
```
Open `.env` and paste these generated keys into `SECRET_KEY` and `DOCUMENT_MASTER_KEY`.

Set `SEED_PASSWORD` in `.env` to a secure string of at least 12 characters. The same password will be used for all seeded demo accounts.

For demo evaluations, ensure the following are enabled in `.env`:
```ini
DEMO_MODE=true
DEMO_SHOW_CREDENTIALS=true
```
*(When enabled alongside `APP_ENV != "production"`, this renders the interactive demo credentials drawer on `/login` and equips `is_demo_admin` users with the Demo Time Machine toolbar.)*

#### Step C: Build and Launch the Application Stack
```powershell
# Start PostgreSQL and FastAPI containers in detached mode with build
docker compose up -d --build

# Verify services are healthy
docker compose ps
```
> [!NOTE]
> **Automatic Database Migrations:** On container startup, `scripts/entrypoint.sh` automatically waits for PostgreSQL to become healthy, then runs `alembic upgrade head` under the administrative `fdr_owner` role before starting the web server. Manual migration execution is not required.

> [!IMPORTANT]
> **Single-Process Worker Architecture:** The web service (`fdr_sentinel_web`) deliberately runs Uvicorn as a single-process worker (`uvicorn app.main:app --host 0.0.0.0 --port 8000`). This is a deliberate design requirement: `ClockService` caches its temporal offset in process memory, and the temporal alert scheduler runs within the single process; multi-worker processes would cause split-brain clock states across worker forks.

The portal endpoints will be available at:
* **Web Portal & Login:** [http://localhost:8000/login](http://localhost:8000/login)
* **Interactive API Documentation (OpenAPI 3.1.0):** [http://localhost:8000/docs](http://localhost:8000/docs)
* **Health Check Probe:** [http://localhost:8000/healthz](http://localhost:8000/healthz)
* **Cryptographic Audit Verification:** [http://localhost:8000/audit/verify](http://localhost:8000/audit/verify)

#### Step D: Seed the Database with Demo Accounts
Run the idempotent user and institution seeding script inside the running web container:
```powershell
docker compose exec web python scripts/seed_users.py
```
> [!NOTE]
> The seed script automatically reads `SEED_PASSWORD` from the container's environment (loaded by Docker Compose from `.env`), ensuring passwords never need to be passed as command-line arguments.

---

### 3. Demo Evaluation User Credentials

| Username | Role | Demo Title & Context | Password | Implemented Capabilities & Audit Visibility |
| :--- | :--- | :--- | :--- | :--- |
| `demo.admin` | `AUDITOR` (`is_demo_admin=true`) | Demo Administrator (jury account) | *as configured in .env (SEED_PASSWORD)* | Clock control via Demo Time Machine toolbar, inspect full cryptographic audit log, verify chain integrity at `/audit/verify` |
| `scrutiny.officer` | `SCRUTINY_OFFICER` | Scrutiny Officer (demo) | *as configured in .env (SEED_PASSWORD)* | Authenticated staff navigation, view audit status page; *(planned: scrutiny console, discrepancy review, side-by-side extraction viewer)* |
| `approver.authority` | `APPROVER_AUTHORITY` | Competent Authority (demo) | *as configured in .env (SEED_PASSWORD)* | Authenticated staff navigation; *(planned: release authorization dashboard, statutory checklist sign-off)* |
| `auditor` | `AUDITOR` | Vigilance Auditor (demo) | *as configured in .env (SEED_PASSWORD)* | Full audit trail inspection, cryptographic chain verification (`/audit/verify`) with linear-time genesis and link continuity checks |
| `inst.admin` | `INSTITUTION_ADMIN` | Institution Administrator (demo, fictional institution: National Model Institute of Technology) | *as configured in .env (SEED_PASSWORD)* | College FDR portal, instrument submission with client & server validation, envelope-encrypted document upload & scoped download, read-only audit timeline |
| `inst.admin2` | `INSTITUTION_ADMIN` | Institution Administrator (demo, fictional institution: Demo Institute of Technology) | *as configured in .env (SEED_PASSWORD)* | Multi-tenant tenancy isolation (scoped strictly to own institution; cross-institution requests return 404) |

---

### 4. Running the Test Suite
The automated test suite executes strictly against the isolated test database (`fdr_sentinel_test`), never modifying development records:
```powershell
docker compose exec web pytest -q
```
*Current test suite: **137 passed** (including static temporal integrity checks, constant-time dummy timing mitigation, CSRF double-submit tests, envelope encryption validation, 11-state FSM transitions, and institution tenancy scoping).*

---

## 🎬 5-Minute Evaluation Walkthrough
> **Target demonstration flow:** Steps become available as the corresponding features land (see the Feature Matrix above).

1. **Executive Command:** Open Dashboard as Scrutiny Officer to review active FDR status counters, regional distribution, and the interactive **Maturity Wall** displaying expiring deposits for the upcoming quarter based on the loaded seed dataset.
2. **Discrepancy Identification & Earned Trust:** Switch to Institution portal. Upload an FDR with inverted dates and a duplicate receipt number. The system preserves the record as `DISCREPANCY_FLAGGED` without crashing. Reveal the **Explainable Trust Score**: Base (20) + Valid PDF (15) - Chronological Inversion DISC-02 (40) - Duplicate Submission DISC-05 (50) = 0/100 (clamped). Note that the incumbent college's existing deposit remains untouched in `ACTIVE` status.
3. **Demo Time Machine Clock & Catch-Up Alerts:** Advance the injected application clock by 60 days via the Demo Time Machine toolbar (available to `is_demo_admin` when `DEMO_MODE=true`). Watch the single-process scheduler fire live: status shifts from `ACTIVE` to `EXPIRING`, catch-up evaluation dispatches bilingual English/Hindi notices, and an urgent Level 2 escalation appears on the officer backlog.
4. **Renewal Lineage & Dual-Authorized Release:** Demonstrate parent-child renewal lineage, where parent FDR becomes `RENEWED` only when child reaches `ACTIVE`. Switch to Competent Authority account. Open a release request filed from a `MATURED` instrument. Inspect the officer's statutory checklist and evidence. Click **Authorize Release** to produce an Ed25519-signed QR certificate.
5. **Tamper-Detection Live Test:** Open Auditor view. Click **Verify Chain Integrity**; show green confirmation. Run a test script that directly modifies a database audit row; re-run verification; watch the indicator turn RED, pinpointing the exact compromised block ID.

---

## ⚖️ Limitations & Honest Disclosures
* **Simulated Data:** All institutional names, FDR numbers, and rupee values in the prototype are synthetically generated for demonstration.
* **Mocked Banking APIs:** There is **no live connection to commercial bank CBS or production banking networks**. Real bank verification requires formal bilateral MoUs and regulatory clearances; the prototype employs a simulated mock bank provider and manual confirmation workflows (planned).
* **No Direct Fund Disbursements:** The portal governs legal custody and regulatory authorization; it does not process direct wire transfers or NEFT/RTGS payouts.
* **Assumptions to Verify:** Specific business rules (such as deposit slabs per discipline, joint-account wording, regional office boundaries, and penal withholding rules) represent **assumptions to verify** against the prevailing AICTE Approval Process Handbook (APH).
* **Regulatory Design Alignment:** The system architecture is designed to reflect the principles of India’s **Digital Personal Data Protection (DPDP) Act 2023**, but carries no formal statutory or security certifications.
* **Demo Gating:** The demo credentials drawer on the `/login` page and the Demo Time Machine toolbar are strictly gated behind `DEMO_MODE=true` and `APP_ENV != "production"`. In production, these features are deactivated by default.
* **Stateless JWT Sessions:** Session logout functions via client-side cookie deletion. Because JWTs are stateless and signature-verified without a server-side Redis denylist, previously issued tokens remain cryptographically valid until their expiration timestamp.
* **Authentication Rate-Limiting:** Rate-limiting on `/login` to mitigate brute-force credential stuffing is planned and not yet active.
* **Document Master Key Hierarchy:** Uploaded documents are protected via AES-256-GCM envelope encryption using a single global Document Master Key (`DOCUMENT_MASTER_KEY`). Per-institution Key Encryption Keys (KEKs) and an automated key-rotation pipeline are not yet implemented.

---

## 🎥 Demo Video
> [Demo video link will be added here prior to submission]

---

## 📸 Screenshots
* **Login Page with Demo Credentials Drawer:** `[Screenshot placeholder: /login with demo credentials panel expanded]`
* **Institution FDR Submission Form:** `[Screenshot placeholder: /institution/submit with drag-and-drop vault & specimen generator]`
* **Scrutiny Officer Console (Planned):** `[Screenshot placeholder: /scrutiny review workstation and anomaly indicators]`
* **Cryptographic Audit Verification Page:** `[Screenshot placeholder: /audit/verify with SHA-256 chain integrity status]`

---

## 👥 Team & License
* **Developer:** *Solo Developer Build (SIH Entry 2026)*
* **Problem Statement:** 26265 (AICTE, Regulation Bureau, Smart Automation)
* **License:** License file to be added before submission
