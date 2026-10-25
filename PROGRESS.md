# FDR Sentinel: Project Progress & Roadmap Tracker (PROGRESS.md)

> **CRITICAL PROTOCOL NOTE:**  
> **This document must be updated after every completed engineering task, architectural decision, or phase milestone.** Always update the "Last Updated" timestamp, task checkboxes, decisions log, and known issues.

**Project:** FDR Sentinel (SIH Problem Statement 26265)  
**Execution Context:** Solo Developer (4 Build Days + 1 Submission Day)  
**Submission Deadline:** 28 October 2026  
**Current Phase:** Day 2 — Template Formatting Hardening, Global 500 Handler & Null-Safety (Stage 4.3 Bugfix Complete)  
**Last Updated:** 2026-10-26  
**Overall Readiness:** 50% (Foundation, Database Roles, ClockService, Audit Hash Chain, Auth & RBAC, CSRF, Time Machine Toolbar, Offline UI Shell, Demo Credentials Panel, Nullable Migration 0002, AES-256-GCM Envelope Encryption with AAD Document ID Binding, Sharded Storage Vault, Atomic Multi-Table Submission Service, Field Provenance, Scoped Views & Downloads, Submission Form Demo Tools & Scenarios, fpdf2 Specimen PDF Generator, Shared Null-Safe Filters, Global Exception Catch-All 500 Handler, 11-State Finite State Machine, Database CHECK Constraint ck_instruments_status_fsm, Prior-State Restoration Column, Actor Principal Model, Read-Only Audit Timeline UI, 135 Tests Passing)  
> **Schedule status:** Tier 1 Stage 5 complete; Stages 6-7 in progress.

---

## 🏗️ Build Stages & Execution Plan

| Stage | Focus Area | Status | Notes |
| :---: | :--- | :---: | :--- |
| **Stage 1** | Foundation, Docker, PostgreSQL, Database Roles | Done | Sync SQLAlchemy 2.0, migrations, `/healthz` |
| **Stage 2** | ClockService & SHA-256 Hash-Chained Audit Log | Done | Temporal grep rule, tamper verification linear check |
| **Stage 3** | Auth, RBAC, Offline UI Shell | Done | 4 roles + demo.admin, CSRF double-submit, vendored assets |
| **Stage 3.1** | Demo Credentials Panel & UI Polish | Done | Triple-gated credentials panel, request-time password interpolation |
| **Stage 4** | Encrypted Document Vault & FDR Submission | Done | AES-256-GCM envelope encryption, multi-table atomic service |
| **Stage 4.3** | Null-Safety Hotfix & Global 500 Exception Boundary | Done | Shared template filters, styled 500 handler, 124 tests passing |
| **Stage 5** | FSM & Status Lifecycle Timeline | Done | 11-state machine, prior-state restore, status CHECK constraint, timeline UI |
| **Stage 6** | DiscrepancyEngine & Trust Score Inputs | Next | DISC-01 to DISC-06, DISC-08, newer-submission flagging for DISC-05 |
| **Stage 7** | Scrutiny Workstation Console | Planned | Side-by-side diff review (confirm exact scope with me) |

---

---

## 📅 Day-by-Day Implementation Roadmap

### Day 1: Saturday, 24 Oct 2026 (Tier 1: Core Regulatory Loop & Foundation)
- [x] Initialize repository structure and Docker Compose orchestration (FastAPI + PostgreSQL 16+).
- [x] Configure Python backend environment (`requirements.txt`, Pydantic v2 settings with fail-safe production defaults).
- [x] Implement SQLAlchemy 2.0 models: `User` (with `is_demo_admin`), `Institution`, `Instrument`, `Document`, `FieldProvenance`, `AuditLog`, `ClockState`.
- [x] Configure PostgreSQL database role privileges:
  - Migrations and seeding run as administrative DB owner (`fdr_owner`).
  - Runtime app executes strictly under `fdr_app_user` with `UPDATE`, `DELETE`, and `TRUNCATE` revoked on `audit_log`, and `GRANT USAGE ON SEQUENCE audit_log_id_seq`.
  - `clock_state` single-row constraint (`CHECK (id = 1)`) with `SELECT, UPDATE` granted to `fdr_app_user`.
- [x] Implement injected **`ClockService`** (`ClockService` singleton) as the sole temporal authority across all services and queries (`# clock-allow` static grep enforcement).
- [x] Implement **Tamper-Detecting SHA-256 Hash-Chained Audit Log** with canonical JSON serialization binding `id`, `event_timestamp`, `entity_type`, `entity_id`, `action`, `actor_user_id`, `actor_role`, and `details`.
- [x] Implement `verify_chain(session)` verifying genesis anchor, link continuity, payload match, and entry hash in linear time ($O(n)$).
- [x] Implement JWT authentication service, password hashing (bcrypt), and FastAPI RBAC dependency interceptor (4 roles).
- [x] Build FDR Submission endpoint and Jinja2 + HTMX form with **AES-256-GCM envelope encryption** (per-file DEK wrapped by master KEK).
- [x] Implement 11-state finite state machine (FSM) validation and non-destructive discrepancy transitions.
- [ ] Implement deterministic **`DiscrepancyEngine`** (`DISC-01` to `DISC-06`, `DISC-08`), saving records as `DISCREPANCY_FLAGGED` (with `DISC-05` flagging only newer submissions).
- [ ] Build Scrutiny Officer workstation console (Jinja2 + HTMX) with status filters and discrepancy review actions.

### Day 2: Sunday, 25 Oct 2026 (Tier 2: Temporal Engine, Dashboard & Notices)
- [ ] Configure APScheduler as a single-process async worker executing scans against `ClockService`.
- [ ] Implement proactive alert dispatch: 90/60/30/7-day horizons with catch-up logic and `UNIQUE (instrument_id, alert_type)` constraint.
- [x] Build **Demo "Time Machine" Clock** toolbar: HTMX control modal restricted to `is_demo_admin` when `DEMO_MODE=true`.
- [x] Build Stage 3.1 Demo Credentials Panel & UI fixes (triple-gated on `/login`, duplicate Sign In removal, plain text audit subtitle, responsive user badge with title attribute, environment-scoped cookie Secure flag).
- [ ] Build Central Executive Dashboard: status counter cards and Chart.js **Maturity Wall** (monthly tranches & regional exposure).
- [ ] Implement **Earned Trust Score Calculator**: Low Base (20) + Evidence Additions - Rule Deductions (0–100) with UI breakdown card.
- [ ] Implement bilingual (English/Hindi) official notice templates and preview modals for maturing instruments.

### Day 3: Monday, 26 Oct 2026 (Tier 3: Lifecycle Completion, Integrity & Deployment)
- [ ] Implement parent-child renewal chain linkage (parent instrument transitions to `RENEWED` only when child reaches `ACTIVE`).
- [ ] Implement dual-key release workflow: officer statutory checklist + Competent Authority sign-off (allowed from `ACTIVE`, `EXPIRING`, or `MATURED`).
- [x] Build Tamper-Detection audit verification view (`/audit/verify`) with green/red status badge.
- [ ] Scripted live row-tampering demonstration against database audit rows.
- [x] Synthetic seed data: role users + two sample institutions with isolated tenancies (`scripts/seed_users.py`).
- [ ] Synthetic seed data: 50 institutions across 8 regions with full lifecycle states (Stage 3 deliverable).
- [ ] Verify production Docker Compose deployment (local/staging environment).
- [ ] **Record backup demo video:** Full 5-minute judge walkthrough recorded and stored safely as fallback deliverable.

### Day 4: Tuesday, 27 Oct 2026 (Tier 4: Advanced Automation & Extensions — Feature Freeze Evening)
- [ ] Implement PDF text extraction pipeline: native digital text layer (`pypdf`/`pdfplumber`) + Tesseract OCR fallback.
- [ ] Build Scrutiny Officer side-by-side text extraction diff viewer (`DISC-07` with $\ge 0.85$ confidence floor).
- [ ] Implement **Ed25519-signed QR deposit certificate** generation and public verification endpoint (`/verify-fdr`).
- [ ] Implement explainable lapse-risk backlog sorting index: $\text{urgency} \times \text{amount} \times (1 / \text{trust})$.
- [ ] Implement compliance report export: CSV and printable PDF generation.
- [ ] Expose interactive OpenAPI 3.1 Swagger documentation (`/docs` - MUST).
- [ ] Scaffold simulated outbound event webhooks and Account Aggregator sandbox stub (STRETCH).
- [ ] **FEATURE FREEZE (Evening — 20:00 IST):** Code lock. Zero new features or schema adjustments permitted.

### Day 5: Wednesday, 28 Oct 2026 (Fixes, Verification & Submission)
- [ ] Run automated end-to-end regression test suite across all 6 core evaluation scenarios.
- [ ] Conduct live rehearsal of the 5-minute evaluation walkthrough (Dashboard -> Defective Upload & Trust Score -> Time Machine -> Release -> Tamper Test).
- [ ] Verify backup demo video playback, final documentation cleanup, and submit portal deliverables to SIH.

---

## 🛑 "Drop First If Behind" Contingency Order

If execution encounters unforeseen blockers, non-essential extensions are deferred in this strict order. **Tiers 1, 2, and 3 are never dropped**:

1. **Drop 1 (First to defer):** Outbound event webhooks & Account Aggregator sandbox stub (Tier 4 Stretch).
2. **Drop 2:** CSV and printable PDF report export (rely on live interactive on-screen tables).
3. **Drop 3:** Explainable lapse-risk backlog sorting (rely on standard chronological urgency sorting).
4. **Drop 4:** Ed25519 cryptographic QR signing on certificates (render standard verified HTML/PDF certificate).
5. **Drop 5:** Tesseract OCR image fallback (rely solely on native PDF text extraction; physical scans fall back to officer manual scrutiny).

---

## 📝 Architectural & Design Decisions Log

| Decision ID | Date | Topic | Decision Made | Justification & Rationale |
| :--- | :---: | :--- | :--- | :--- |
| **DEC-001** | 2026-10-24 | Architecture Revision | Single FastAPI app + Jinja2 + HTMX + Tailwind + Chart.js + PostgreSQL. | Replaced decoupled Next.js stack to eliminate API-client synchronization overhead, providing maximum velocity for solo developer within a 4-day build. |
| **DEC-002** | 2026-10-24 | Banking Boundary | Pluggable `VerificationProvider` with Mock Bank (`MOCK_BANK_CONFIRMED`) and Manual Scrutiny modes. | Realistic and legally compliant: production bank CBS APIs require formal bilateral MoUs and regulatory clearances not available for hackathon prototypes. |
| **DEC-003** | 2026-10-24 | Document Security | AES-256-GCM envelope encryption with per-file DEK wrapped by master KEK. | High-performance authenticated encryption. Replaced Fernet and PBKDF2 with standard envelope encryption per NIST recommendations. |
| **DEC-004** | 2026-10-24 | Audit Integrity | Tamper-detecting SHA-256 canonical hash chain with database-level revoked UPDATE/DELETE. | Provides verifiable proof of tamper-detection for vigilance audits without the overhead of public blockchains, backed by DB privilege revocation. |
| **DEC-005** | 2026-10-24 | Evaluator Demonstration | Dedicated "Time Machine" clock simulation toolbar with `ClockService`. | Enables judges to witness temporal alerts, maturity transitions, and escalations live without waiting months in real time. |
| **DEC-006** | 2026-10-24 | Decision Authority | Strict Human-in-the-Loop (HITL) principle; rejections strictly by named authority. | Algorithms solely provide decision support; named officers must authorize all approvals, rejections, and deposit releases. |
| **DEC-007** | 2026-10-24 | Temporal Logic | Injected `ClockService` as sole source of date/time across app and queries. | Eliminates non-deterministic `CURRENT_DATE`/`NOW()` calls, enabling seamless time machine simulation and catch-up processing. |
| **DEC-008** | 2026-10-24 | Trust Modeling | Earned Trust Score (Low base + verified evidence additions - rule deductions). | Replaces unjustified high initial trust with a realistic regulatory model where institutions earn credibility through confirmed artifacts. |
| **DEC-009** | 2026-10-24 | Priority Tiers | 4-tier build schedule with strict "Drop First If Behind" contingency plan. | Guarantees complete end-to-end working MVP with zero risk of half-baked core features at deadline. |
| **DEC-010** | 2026-10-24 | Audit Chaining Model | Previous-row chaining using highest committed ID instead of rigid `id - 1`. | Prevents spurious chain breaks caused by sequence number gaps resulting from rolled-back database transactions. |
| **DEC-011** | 2026-10-24 | Tamper-Evident Details | Binds `details` dictionary inside the canonical JSON cryptographic payload. | Extends SPEC 7-field baseline to ensure transition metadata (`from_state`, `to_state`, justifications) cannot be altered without breaking the chain. |
| **DEC-012** | 2026-10-24 | Fail-Safe Configuration | `APP_ENV` defaults to `"production"`; startup refused on placeholder/malformed keys. | Prevents deploying insecure placeholder keys or unverified environment modes, failing fast with non-leaking error messages. |
| **DEC-013** | 2026-10-25 | Temporal Scope Isolation | `ClockService.real_now()` strictly reserved for auth tokens and security rate limits. | Prevents simulated demo time warps (+60d, +1y) from breaking JWT expiry calculations or session authentication. |
| **DEC-014** | 2026-10-25 | Dedicated Demo Admin Role | Created dedicated `demo.admin` user with `is_demo_admin=true` and `AUDITOR` role. | Isolates Time Machine evaluation controls to the designated hackathon jury account without corrupting operational roles. |
| **DEC-015** | 2026-10-25 | Offline Vendored Assets | Local vendored static assets (`htmx.min.js`, `tailwind.min.js`, cropped brand icons). | Ensures 100% offline evaluation capability without external CDN dependencies or render delays. |
| **DEC-016** | 2026-10-25 | Double-Submit Cookie CSRF | Synchronizer / double-submit CSRF protection on all state-changing endpoints. | Defends against cross-site request forgery across cookie-based session mutations while accommodating HTMX ajax interactions. |
| **DEC-017** | 2026-10-25 | Demo Credentials Panel Gating | Triple-gated display (`APP_ENV != "production"`, `DEMO_MODE=true`, `DEMO_SHOW_CREDENTIALS=true`) with dynamic request-time password interpolation. | Prevents credential leaks in production or default deployments while offering 1-click "Fill" convenience for jury evaluators during prototype demonstrations. |
| **DEC-018** | 2026-10-25 | Document Vault Cryptography | AES-256-GCM envelope encryption with per-file 256-bit DEK, 96-bit nonce, wrapped by 256-bit `DOCUMENT_MASTER_KEY` via AES-GCM; document UUID bound as Associated Authenticated Data (AAD) into both payload encryption and DEK wrapping. | Prevents ciphertext swapping across documents and protects vault files at rest. |
| **DEC-019** | 2026-10-25 | Vault Storage Structure & Sharding | Sharded relative directory paths (`{uuid[:2]}/{uuid[2:4]}/{uuid}.enc`) mounted under persistent Docker volume (`./storage:/app/storage`). | Prevents single-directory inode exhaustion and guarantees document persistence across container rebuilds. |
| **DEC-020** | 2026-10-25 | Separation of Structural Validation vs Regulatory Scrutiny | FDR submission form validates data structure only (Decimals, valid calendar dates, positive amounts, IFSC regex when non-empty); business defects (inverted dates, expired maturities, underfunding, missing optional fields, duplicate FDR numbers) are saved as `SUBMITTED` for evaluation by `DiscrepancyEngine`. | Preserves realistic institution self-declarations without blocking submissions. |
| **DEC-021** | 2026-10-25 | Database Schema Flexibility | Migration `0002_nullable_instrument_fields.py` alters all 11 business columns on `instruments` to `nullable=True` with non-unique index on `fdr_number`. | Enables capturing defective and incomplete physical deposit slips in compliance with DISC-01 checks. |
| **DEC-022** | 2026-10-25 | Template Evaluation & Link Generation | Removed literal `{% raw %}` delimiters from document download URLs in `detail.html` and `staff_detail.html`; added universal unrendered template syntax test (`"{{"`/`"{%"`) across all 5 roles. | Ensures clean URL interpolation for UUID path parameters and prevents regression of unrendered Jinja syntax anywhere across the portal. |
| **DEC-023** | 2026-10-25 | Submission Form Demo Tools & Dynamic Scenario Service | Single source of truth in `app/services/demo_scenarios.py` with pure functions; all scenario dates derive strictly from `clock_service.today()`; `duplicate_number` dynamically binds to most recent instrument in DB or falls back with clear note; clean scenario maturity matches exact Decimal quarterly compounding ($A = P(1 + r/400)^{4t}$). | Eliminates hardcoded test payloads, enables 1-click evaluation by jury, and preserves strict ClockService temporal authority. |
| **DEC-024** | 2026-10-25 | Synthetic Specimen PDF Generation via `fpdf2` | Lightweight (< 20 KB) pure-Python document generation with diagonal `"SPECIMEN - DEMO ONLY, NOT A REAL INSTRUMENT"` watermark, extractable text layer, structured receipt layout, and Latin-1 safe characters (`-`, `Rs.`). | Avoids heavy C binaries/headless browsers; produces extractable text layer matching form fields for instant evaluation attachments. |
| **DEC-025** | 2026-10-25 | Fictional Seed Data & Copy Neutralization | All institution names ("National Model Institute of Technology", "Demo Institute of Technology"), individual principal/dean names, and bank names neutralized to purely fictional demo entities across seed scripts, auth panel, templates, and README. | Ensures ethical data compliance and prevents impersonating real colleges, banks, or academic officials. |
| **DEC-026** | 2026-10-25 | UI Neutralization & Production Presentation | Replaced all build-stage notices ("Stage 3 Active", "arrives in Stage 4", etc.) with production-ready status cards and review queues; `/healthz` probe minimized strictly to `{"status": "ok"}`; added regression test asserting zero developmental patterns rendered on any role page. | Delivers a clean, professional production appearance across all workspaces without internal development artifacts. |
| **DEC-027** | 2026-10-25 | Shared Null-Safe Filters & Global Exception Boundary | Centralized all currency, date, datetime, file size, and string display through shared singleton Jinja2 filters (`format_inr`, `format_date`, `format_datetime`, `format_filesize`, `default_dash`) returning neutral em-dashes for None/blank values. Replaced separate router template instances with the shared singleton. Registered global `@app.exception_handler(Exception)` capturing unhandled Python errors, logging full tracebacks to server logs, and rendering styled 500 HTML without leaking internal traces or stack information. | Eliminates NoneType format crashes on blank instrument fields and guarantees graceful error handling without leaking sensitive details or raw text 500 errors. |
| **DEC-028** | 2026-10-26 | FSM Central Transitions Matrix | Central static transition mapping `(from_state, to_state) -> TransitionRule` in `app/services/instrument_fsm.py` governing all 11 states with immutable terminal states (`RENEWED`, `RELEASED`, `REJECTED`) and strict self-transition rejection. | Eliminates scattered transition checks, centralizes regulatory edge constraints, and provides a single audit-sealed state change entry point. |
| **DEC-029** | 2026-10-26 | SYSTEM Actor Principal Model | Unified `Actor` dataclass (`user_id`, `role`, `institution_id`, `is_demo_admin`) with `SYSTEM_ACTOR` singleton for automated background transitions. | Integrates automated scheduler operations and human officer actions into a uniform authorization and cryptographic audit pipeline. |
| **DEC-030** | 2026-10-26 | Prior-State Release Rejection Restoration | Added nullable `prior_state` column (Migration `0003_instrument_lifecycle_fsm.py`) capturing origin state on entering `RELEASE_REQUESTED`, allowing rejection ONLY back to that state, and clearing it upon exit. | Resolves Known Issue 11: guarantees rejected release requests restore instruments safely to their exact pre-release status (`ACTIVE`, `EXPIRING`, or `MATURED`) rather than a destructive terminal failure. |
| **DEC-031** | 2026-10-26 | Database Engine FSM Status CHECK Constraint | Enforced `ck_instruments_status_fsm` on `instruments.status` via Migration `0003_instrument_lifecycle_fsm.py` with pre-migration loud validation failure. | Guarantees database integrity at the PostgreSQL storage engine level, preventing any illegal or misspelled state string from persisting. |
| **DEC-032** | 2026-10-26 | Mandatory Human Justification Boundary | Enforces mandatory, non-blank text justification ($\ge 10$ characters) for human-driven sensitive transitions (`VERIFIED`, `REJECTED`, `RELEASED`, `RELEASE_REQUESTED`, and release-rejection); automated transitions generate descriptive system reasons. | Complies with AICTE regulatory accountability and human-in-the-loop statutory governance mandates. |
| **DEC-033** | 2026-10-26 | Dynamic Contextual Joint-Holder & Simplified Provenance Display | Built joint-holder clause dynamically from caller's linked institution (`"Regional Officer, AICTE & Head of Institution, <institution name>"`) omitting `(example)` from saved values. Simplified provenance badges on detail pages to display label only for `SELF_DECLARED` rows, reserving confidence score figures for extracted/verified sources. | Enhances demo realism across multi-institution evaluations and cleans visual hierarchy for institution self-declarations. |

---

## ❓ Open Questions & Clarifications

1. **Approval Process Handbook (APH) Deposit Slabs:** *[ASSUMPTION TO VERIFY - APH Norm]* Confirm exact deposit slabs per technical education program (e.g. ₹35 Lakhs for Engineering, ₹15 Lakhs for Management) against the current 2024–2027 APH edition.
2. **Joint-Holder Mandate:** *[ASSUMPTION TO VERIFY - APH Norm]* Confirm exact mandated string for joint FDR holding with AICTE Regional Officers.
3. **AICTE Regional Office Hierarchy:** *[ASSUMPTION TO VERIFY - APH Norm]* Confirm whether regional approvals require dual sign-off within the Regional Office or central escalation to New Delhi for high-value deposits.

---

## 🎯 Demo-Readiness Checklist

- [x] All 4 user roles seeded and functional (`institution`, `scrutiny`, `approver`, `auditor`).
- [x] `is_demo_admin` account created and gated by `DEMO_MODE=true`.
- [ ] Executive Dashboard renders live Maturity Wall (Chart.js) and Deposit at Risk counters from seed dataset.
- [ ] Discrepancy Engine saves defective test upload as `DISCREPANCY_FLAGGED` without touching incumbent deposit.
- [ ] Side-by-side text extraction diff renders clearly on Scrutiny Officer console.
- [ ] Demo Time Machine button (+60 Days) successfully shifts `ClockService` and fires single-process scheduler with catch-up alerts.
- [ ] Bilingual (English/Hindi) notice preview modal displays correctly.
- [ ] Renewal chain clearly displays parent-child lineage (parent transitions to `RENEWED` only when child reaches `ACTIVE`).
- [ ] Competent Authority release workflow enforces statutory checklist and produces Ed25519-signed QR certificate.
- [x] Tamper-detection test: modifying a database row causes `/audit/verify` to turn RED with exact block ID.
- [ ] All 6 evaluation scenarios pass end-to-end without unhandled exceptions.
- [ ] Backup 5-minute walkthrough video recorded and stored locally.

---

## ⚠️ Known Issues & Technical Limitations

* **Issue 1:** No live core banking integration (mocked by design due to regulatory/statutory constraints; marked `MOCK_BANK_CONFIRMED`).
* **Issue 2:** Scan quality degradation on poor physical copies gracefully falls back to manual officer verification.
* **Issue 3:** Time Machine clock shifting is strictly isolated to `DEMO_MODE=true` with `is_demo_admin=true` and is disabled in production.
* **Issue 4 (Tail-Truncation Limitation):** In a hash-chain log, deleting trailing rows leaves a mathematically valid shorter chain. Periodic signed head-hash checkpoints (SPEC §9.4) will be added in Tier 3/4 to detect any history truncation.
* **Issue 5 (Stateless JWT Logout):** Logout clears the HTTP-only session cookie client-side. Server-side token blacklisting is deferred to production hardening.
* **Issue 6 (Rate-Limiting Deferred):** IP/account rate-limiting on `/login` is deferred to Stage 7 / production hardening; timing-attack mitigation is implemented via constant-time dummy bcrypt.
* **Issue 7 (Bearer API Auth Deferred):** Bearer token header authentication for headless REST clients is deferred to Tier 4; Stage 3 prioritizes browser session cookie auth.
* **Issue 8 (UTC vs IST Date Boundary):** `ClockService.today()` calculates calendar date in UTC; in India Standard Time (+05:30) near midnight UTC date may differ by 1 day. All database timestamps remain UTC.
* **Issue 9 (Master Key Rotation Deferred):** Single versioned master key is used for envelope encryption; multi-version master key rotation is deferred to production hardening.
* **Issue 10 (Discrepancy Engine & Scoring Pending Stage 6):** Newly submitted instruments initialize with status `SUBMITTED` and baseline trust score `20`; automated discrepancy rule flags (`DISC-01` to `DISC-06`, `DISC-08`) and earned trust calculations arrive in Stage 6.
* **Issue 11 (Resolved - Stage 5):** Release-rejection workflow restores the instrument to its exact prior pre-release state (`ACTIVE`, `EXPIRING`, or `MATURED`) via dedicated `prior_state` column and clears it upon exit.
* **Issue 12 (Baseline Trust Score):** Trust score currently initializes to a constant baseline of `20`; the explainable earned Trust Score calculator (base + evidence additions - discrepancy deductions) is pending Stage 6.
* **Issue 13 (Nullable Business Fields):** All instrument business columns are nullable in the database (Migration `0002_nullable_instrument_fields.py`); all current and new code must safely handle `None` values without crashing.
* **Issue 14 (Demo Tools Gating):** Prototype demo scenario tools and sample PDF generators must remain strictly gated by `DEMO_MODE=true` and non-production environments (`APP_ENV != "production"`).
