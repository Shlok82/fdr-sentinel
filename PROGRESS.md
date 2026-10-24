# FDR Sentinel: Project Progress & Roadmap Tracker (PROGRESS.md)

> **CRITICAL PROTOCOL NOTE:**  
> **This document must be updated after every completed engineering task, architectural decision, or phase milestone.** Always update the "Last Updated" timestamp, task checkboxes, decisions log, and known issues.

**Project:** FDR Sentinel (SIH Problem Statement 26265)  
**Current Phase:** Phase 0 (Planning, Specifications & Architecture Sign-off)  
**Last Updated:** 2026-10-24  
**Overall Readiness:** 10% (Architecture & Documentation Complete; Implementation Pending Review)

---

## 🗺️ Phased Engineering Roadmap

### Phase 1: Foundation and Schema
- [ ] Define Docker Compose topology (Next.js frontend, FastAPI backend, PostgreSQL 16+).
- [ ] Initialize Python backend environment with `requirements.txt` and Pydantic v2 settings.
- [ ] Initialize Next.js 14 App Router project with TypeScript and Tailwind CSS.
- [ ] Implement SQLAlchemy 2.0 models: `Institution`, `Instrument`, `Document`, `FieldProvenance`.
- [ ] Set up Alembic migration pipeline for reproducible PostgreSQL schema generation.
- [ ] Configure tracked `.env.example` with strict secret segregation.

### Phase 2: Authentication and Roles (RBAC)
- [ ] Implement JWT token authentication service with password hashing (bcrypt).
- [ ] Build FastAPI RBAC dependency interceptor supporting 4 discrete roles:
  - `INSTITUTION_ADMIN`
  - `SCRUTINY_OFFICER`
  - `APPROVER_AUTHORITY`
  - `AUDITOR`
- [ ] Create Next.js auth context, session persistence, and role-based route guards.
- [ ] Seed default prototype user accounts for each role.

### Phase 3: Instrument Lifecycle and Documents
- [ ] Implement multi-step FDR submission endpoint (`POST /api/v1/instruments`).
- [ ] Configure document storage vault with AES-256-GCM envelope encryption.
- [ ] Implement SHA-256 hash calculation on raw uploaded document bytes.
- [ ] Build finite state machine (FSM) enforcing 11 lifecycle states and valid transitions.
- [ ] Build Institution Submission UI with multi-file scan upload and preview.
- [ ] Implement renewal chain linkage (`previous_instrument_id`, `renewal_sequence`).

### Phase 4: Verification and Discrepancy Engine
- [ ] Implement pluggable `VerificationProvider` interface.
- [ ] Build `MockBankProvider` with configurable latency and simulated mismatch responses.
- [ ] Build `ManualConfirmationProvider` with officer document upload and confirmation reference.
- [ ] Scaffold `AccountAggregatorSandboxProvider` stub with consent-flow blueprint.
- [ ] Implement OCR extraction service wrapper (Tesseract/easyOCR) with PDF text parsing.
- [ ] Implement deterministic `DiscrepancyEngine` running checks `DISC-01` through `DISC-08`.
- [ ] Build Scrutiny Officer Workstation UI with side-by-side OCR diff viewer.

### Phase 5: Scheduler, Alerts, and Escalation
- [ ] Integrate APScheduler in FastAPI background context for daily temporal scans.
- [ ] Implement 90/60/30/7-day horizon calculation engine.
- [ ] Implement 3-tier escalation ladder:
  - Tier 1: Institution advisory (90d, 60d)
  - Tier 2: Officer urgent warning (30d, 7d)
  - Tier 3: Regional Director statutory escalation (lapsed/matured)
- [ ] Build notification dispatcher (simulated email, SMS, and in-portal alerts).
- [ ] Build officer notification center in Next.js UI.

### Phase 6: Release Workflow
- [ ] Implement Release Request submission endpoint with statutory compliance checklist.
- [ ] Build Scrutiny Officer recommendation interface with compliance verification.
- [ ] Build Competent Authority dual-key sign-off console.
- [ ] Implement digital release certificate generation with cryptographic signature hash.
- [ ] Ensure non-reversible archival upon transition to `RELEASED`.

### Phase 7: Dashboards and Reports
- [ ] Build Central Executive Dashboard with aggregate metric cards (Active, Expiring, Matured, Released).
- [ ] Build interactive **Maturity Wall** chart grouping instruments by month and region.
- [ ] Implement "Deposit at Risk" exposure calculation (value of expiring instruments lacking renewal).
- [ ] Build institution-specific status portal with real-time discrepancy resolution view.
- [ ] Implement CSV and printable PDF report export for regional compliance audits.

### Phase 8: Differentiating Features
- [ ] Implement **Explainable Trust Score** calculation (0–100) with visual breakdown card.
- [ ] Implement **Explainable Lapse-Risk Prioritization** index for scrutiny backlog sorting.
- [ ] Implement **Tamper-Evident Hash Chain** for audit log entries (`SHA-256` linking).
- [ ] Build `/api/v1/audit/verify` endpoint with green/red verification badge in UI.
- [ ] Implement **QR-Verifiable Deposit Certificate** generation and public validation page.
- [ ] Create bilingual (English/Hindi) official notice letter templates.
- [ ] Implement **Demo "Time Machine" Clock** allowing manual date shifting (+30d, +60d, +1y).
- [ ] Expose OpenAPI 3.1 Swagger docs (`/docs`) and simulated webhook endpoints.

### Phase 9: Testing and Seed Data Generation
- [ ] Create synthetic seed dataset of 50 technical institutions across 8 AICTE regions.
- [ ] Seed instruments across all lifecycle states, including edge-case anomalies.
- [ ] Implement automated test suite covering the 6 mandatory evaluation scenarios:
  - Clean new submission
  - Validity tracking
  - Approaching maturity
  - Renewal chain rollover
  - Discrepancy & anomaly identification
  - Dual-authorized release
- [ ] Conduct end-to-end integration testing in local Docker Compose environment.

### Phase 10: Demo Polish and Submission
- [ ] Verify 5-minute judge walkthrough against live seed data and Time Machine.
- [ ] Conduct UI styling polish, accessibility check, and responsive layout audit.
- [ ] Complete `README.md` with final run instructions and architecture artifacts.
- [ ] Package final demo deliverables and presentation deck.

---

## 📝 Architectural & Design Decisions Log

| Decision ID | Date | Topic | Decision Made | Justification & Rationale |
| :--- | :---: | :--- | :--- | :--- |
| **DEC-001** | 2026-10-24 | Core Tech Stack | Next.js (Frontend) + FastAPI (Backend) + PostgreSQL (DB). | Provides optimal developer velocity, native async Python performance for OCR/data processing, robust relational integrity, and standard hackathon extensibility. |
| **DEC-002** | 2026-10-24 | Banking Integration Boundary | Pluggable `VerificationProvider` with Mock Bank and Manual Scrutiny modes. | Realistic and legally compliant: production bank CBS APIs require formal bilateral MoUs and regulatory clearances not available for hackathon prototypes. |
| **DEC-003** | 2026-10-24 | Document Security | AES-256-GCM envelope encryption for stored PDF scans. | Ensures uploaded financial documents cannot be read directly from raw storage volumes, aligning with DPDP Act principles. |
| **DEC-004** | 2026-10-24 | Integrity Verification | Cryptographic SHA-256 hash chain for audit records. | Delivers verifiable proof of tamper-resistance for vigilance audits without the overhead and gas costs of a public blockchain. |
| **DEC-005** | 2026-10-24 | Evaluator Demonstration | Dedicated "Time Machine" clock simulation toolbar. | Enables judges to witness temporal alerts, maturity transitions, and escalations live without waiting months in real time. |
| **DEC-006** | 2026-10-24 | Decision Authority | Strict Human-in-the-Loop (HITL) principle. | Algorithms solely provide decision support; named officers must authorize all approvals, rejections, and deposit releases. |

---

## ❓ Open Questions & Clarifications

1. **AICTE Regional Office Hierarchy:** Does AICTE require approval routing directly from Regional Office Scrutiny Officers to Central Headquarters (New Delhi) for releases exceeding ₹50 Lakhs? *(Assumed standard dual-level sign-off for prototype).*
2. **Fixed Deposit Tenures:** What is the prevailing AICTE Approval Process Handbook norm for FDR tenure (typically 8 or 10 years, or matched to course lifespan)? *(Assumed configurable tenure defaulting to 8 years / 96 months).*
3. **Public Certificate Verification:** Should public QR verification display the bank branch and IFSC, or only institution name, deposit status, and validity period? *(Assumed privacy-preserving display omitting raw bank account numbers).*

---

## 🎯 Demo-Readiness Checklist

- [ ] All 4 mock user roles seeded and functional (`institution`, `scrutiny`, `approver`, `auditor`).
- [ ] Executive Dashboard renders live Maturity Wall and Deposit at Risk counters.
- [ ] Discrepancy Engine triggers correctly on dirty test upload (inverted date & duplicate FDR).
- [ ] Side-by-side OCR extraction diff renders clearly on Scrutiny Officer console.
- [ ] Demo Time Machine button (+60 Days) successfully shifts system date and fires APScheduler.
- [ ] Bilingual (English/Hindi) notice preview modal displays correctly.
- [ ] Renewal chain clearly displays parent-child lineage and sequence numbering.
- [ ] Competent Authority release workflow produces downloadable QR-verifiable certificate.
- [ ] Cryptographic hash chain verification endpoint returns verified status across all blocks.
- [ ] All 6 evaluation scenarios pass end-to-end without unhandled exceptions.

---

## ⚠️ Known Issues & Technical Limitations

* **Issue 1:** No live core banking integration (mocked by design due to regulatory/statutory constraints).
* **Issue 2:** OCR accuracy is sensitive to low-resolution scan quality; fallbacks to manual officer verification are in place.
* **Issue 3:** Time Machine clock shifting is strictly isolated to the prototype test harness and is disabled in production configurations.
