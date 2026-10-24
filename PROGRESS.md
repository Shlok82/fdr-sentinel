# FDR Sentinel: Project Progress & Roadmap Tracker (PROGRESS.md)

> **CRITICAL PROTOCOL NOTE:**  
> **This document must be updated after every completed engineering task, architectural decision, or phase milestone.** Always update the "Last Updated" timestamp, task checkboxes, decisions log, and known issues.

**Project:** FDR Sentinel (SIH Problem Statement 26265)  
**Execution Context:** Solo Developer (4 Build Days + 1 Submission Day)  
**Submission Deadline:** 28 October 2026  
**Current Phase:** Day 1 — Foundation, ClockService & Cryptographic Audit Log (Stage 2 Complete)  
**Last Updated:** 2026-10-24  
**Overall Readiness:** 25% (Foundation, Database Roles, ClockService, Audit Hash Chain & 45 Tests Passing)

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
- [ ] Implement JWT authentication service, password hashing (bcrypt), and FastAPI RBAC dependency interceptor (4 roles).
- [ ] Build FDR Submission endpoint and Jinja2 + HTMX form with **AES-256-GCM envelope encryption** (per-file DEK wrapped by master KEK).
- [ ] Implement 11-state finite state machine (FSM) validation and non-destructive discrepancy transitions.
- [ ] Implement deterministic **`DiscrepancyEngine`** (`DISC-01` to `DISC-06`, `DISC-08`), saving records as `DISCREPANCY_FLAGGED` (with `DISC-05` flagging only newer submissions).
- [ ] Build Scrutiny Officer workstation console (Jinja2 + HTMX) with status filters and discrepancy review actions.

### Day 2: Sunday, 25 Oct 2026 (Tier 2: Temporal Engine, Dashboard & Notices)
- [ ] Configure APScheduler as a single-process async worker executing scans against `ClockService`.
- [ ] Implement proactive alert dispatch: 90/60/30/7-day horizons with catch-up logic and `UNIQUE (instrument_id, alert_type)` constraint.
- [ ] Build **Demo "Time Machine" Clock** toolbar: HTMX control modal restricted to `is_demo_admin` when `DEMO_MODE=true`.
- [ ] Build Central Executive Dashboard: status counter cards and Chart.js **Maturity Wall** (monthly tranches & regional exposure).
- [ ] Implement **Earned Trust Score Calculator**: Low Base (20) + Evidence Additions - Rule Deductions (0–100) with UI breakdown card.
- [ ] Implement bilingual (English/Hindi) official notice templates and preview modals for maturing instruments.

### Day 3: Monday, 26 Oct 2026 (Tier 3: Lifecycle Completion, Integrity & Deployment)
- [ ] Implement parent-child renewal chain linkage (parent instrument transitions to `RENEWED` only when child reaches `ACTIVE`).
- [ ] Implement dual-key release workflow: officer statutory checklist + Competent Authority sign-off (allowed from `ACTIVE`, `EXPIRING`, or `MATURED`).
- [ ] Build Tamper-Detection audit verification view (`/audit/verify`) with green/red status badge and live row-tampering demonstration test.
- [ ] Write synthetic seed data generator script creating **50 realistic technical institutions** across 8 AICTE regions with varied lifecycle states.
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

---

## ❓ Open Questions & Clarifications

1. **Approval Process Handbook (APH) Deposit Slabs:** *[ASSUMPTION TO VERIFY - APH Norm]* Confirm exact deposit slabs per technical education program (e.g. ₹35 Lakhs for Engineering, ₹15 Lakhs for Management) against the current 2024–2027 APH edition.
2. **Joint-Holder Mandate:** *[ASSUMPTION TO VERIFY - APH Norm]* Confirm exact mandated string for joint FDR holding with AICTE Regional Officers.
3. **AICTE Regional Office Hierarchy:** *[ASSUMPTION TO VERIFY - APH Norm]* Confirm whether regional approvals require dual sign-off within the Regional Office or central escalation to New Delhi for high-value deposits.

---

## 🎯 Demo-Readiness Checklist

- [ ] All 4 user roles seeded and functional (`institution`, `scrutiny`, `approver`, `auditor`).
- [ ] `is_demo_admin` account created and gated by `DEMO_MODE=true`.
- [ ] Executive Dashboard renders live Maturity Wall (Chart.js) and Deposit at Risk counters from seed dataset.
- [ ] Discrepancy Engine saves defective test upload as `DISCREPANCY_FLAGGED` without touching incumbent deposit.
- [ ] Side-by-side text extraction diff renders clearly on Scrutiny Officer console.
- [ ] Demo Time Machine button (+60 Days) successfully shifts `ClockService` and fires single-process scheduler with catch-up alerts.
- [ ] Bilingual (English/Hindi) notice preview modal displays correctly.
- [ ] Renewal chain clearly displays parent-child lineage (parent transitions to `RENEWED` only when child reaches `ACTIVE`).
- [ ] Competent Authority release workflow enforces statutory checklist and produces Ed25519-signed QR certificate.
- [ ] Tamper-detection test: modifying a database row causes `/audit/verify` to turn RED with exact block ID.
- [ ] All 6 evaluation scenarios pass end-to-end without unhandled exceptions.
- [ ] Backup 5-minute walkthrough video recorded and stored locally.

---

## ⚠️ Known Issues & Technical Limitations

* **Issue 1:** No live core banking integration (mocked by design due to regulatory/statutory constraints; marked `MOCK_BANK_CONFIRMED`).
* **Issue 2:** Scan quality degradation on poor physical copies gracefully falls back to manual officer verification.
* **Issue 3:** Time Machine clock shifting is strictly isolated to `DEMO_MODE=true` with `is_demo_admin=true` and is disabled in production.
* **Issue 4 (Tail-Truncation Limitation):** In a hash-chain log, deleting trailing rows leaves a mathematically valid shorter chain. Periodic signed head-hash checkpoints (SPEC §9.4) will be added in Tier 3/4 to detect any history truncation.
