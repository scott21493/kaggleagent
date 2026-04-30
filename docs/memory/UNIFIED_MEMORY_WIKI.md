# Kaggle Agent Arena — Unified Memory Wiki

Status: canonical Phase 0 memory model  
Version: `memory-v2.0`  
Date: 2026-04-30

---

## 1. Decision

There is one memory wiki, not separate codebase and research wikis.

Prior drafts had overlapping `CODEBASE_WIKI_MEMORY.md` and `RESEARCH_WIKI_MEMORY.md` files with duplicated update protocols, memory events, watchlists, and self-improvement rules. That split is leaky. Phase 0 uses a single namespaced memory file plus structured memory proposals.

Canonical file:

```text
docs/memory/UNIFIED_MEMORY_WIKI.md
```

Machine-readable proposals:

```text
memory/proposals/<proposal_id>.json
```

---

## 2. Memory namespaces

```text
invariants/
  Rules that should rarely change.

codebase/
  Architecture, modules, contracts, conventions, known implementation decisions.

research/
  Research hypotheses, method mechanisms, paper digests, fusion results.

experiments/
  Experiment lessons, failure modes, score patterns, cost patterns.

sources/
  Source ledger for papers, docs, Kaggle discussions, public notebooks, and license/rule notes.

self_improvement/
  Harness changes, champion/challenger outcomes, freeze/unfreeze history.
```

---

## 3. Memory invariants

These are not editable by agents without human approval:

1. Real Kaggle submissions require human approval.
2. Unknown competition rule status means blocked.
3. The controller is deterministic.
4. LLMs are provider workers, not orchestration authority.
5. Memory updates are proposals, not auto-merged truth.
6. Protected-file changes require review.
7. Subscription auth is local-only and not used in CI.
8. CI uses stub providers.
9. The Phase 0 fixture has no GPU requirement.
10. Network is denied by default in Phase 0.

---

## 4. Memory update policy

### 4.1 No auto-merge on schema validity

Schema validity only means the proposal is shaped correctly. It does not mean the claim is true.

A memory update may be merged only after:

- schema validation;
- delta review;
- source/evidence review;
- contradiction check;
- confidence check;
- reviewer decision.

### 4.2 Required proposal fields

Every memory proposal must include:

```json
{
  "schema_version": "memory_update.v1",
  "proposal_id": "mem_0001",
  "namespace": "research",
  "operation": "add|modify|deprecate|remove",
  "claim": "...",
  "prior_claim": "...",
  "delta": "...",
  "evidence": [
    {
      "type": "file|scoreboard|trace|external_citation|human_note",
      "ref": "...",
      "quote_or_summary": "..."
    }
  ],
  "confidence": "low|medium|high",
  "expiry_or_revisit": "...",
  "risk": "low|medium|high",
  "review_status": "proposed|accepted|rejected|needs_human"
}
```

### 4.3 Contradiction handling

If a new proposal contradicts an existing claim:

- do not overwrite silently;
- write both claims into the review packet;
- require reviewer decision;
- preserve the rejected claim in history with reason.

### 4.4 Source requirements

Research claims require one of:

- local paper digest ID;
- external citation recorded in source ledger;
- experiment result ID;
- reviewer note;
- human decision.

Score claims require scoreboard evidence.

Security claims require trace/breaker evidence.

Architecture claims require ADR or merged PR evidence.

---

## 5. Memory index

### 5.1 Invariants

| ID | Claim | Evidence | Status |
|---|---|---|---|
| inv-001 | Real Kaggle submissions require human approval. | Phase 0 plan §1.3 | active |
| inv-002 | Unknown rule status means blocked. | Compliance-as-data policy | active |
| inv-003 | Controller remains deterministic. | ADR-0002 | active |
| inv-004 | Research-first is strategy; Phase 0 research is bounded proxy only. | Phase 0 plan §1.4 | active |

### 5.2 Codebase memory

| ID | Claim | Evidence | Status |
|---|---|---|---|
| code-001 | Phase 0 implements only tabular_binary_v1 fixture, not full adapter abstraction. | Phase 0 plan §1.6 | active |
| code-002 | Provider adapters include stubs for CI and real local Codex/Claude for configured machines. | Phase 0 plan §3.3 | active |
| code-003 | On-disk JSON schemas are canonical; inline YAML is illustrative only. | Phase 0 plan §9 | active |

### 5.3 Research memory

| ID | Claim | Evidence | Status |
|---|---|---|---|
| research-001 | Known methods are calibration floors, not the core strategy. | Phase 0 plan §1.5 | active |
| research-002 | Phase 0 research uses local method notes only; no web paper crawling. | Phase 0 plan §6.1 | active |
| research-003 | Fusion proposals must include smallest proxy test and ablation plan. | Fusion schema | active |

### 5.4 Experiment memory

| ID | Claim | Evidence | Status |
|---|---|---|---|
| exp-001 | No experiments have run yet. | Initial state | active |

### 5.5 Source ledger

| Source ID | Type | Title | Status | Notes |
|---|---|---|---|---|
| src-001 | local_fixture | method_note_001.md | pending | Phase 0 local method note |
| src-002 | local_fixture | method_note_002.md | pending | Phase 0 local method note |

### 5.6 Self-improvement memory

| ID | Claim | Evidence | Status |
|---|---|---|---|
| si-001 | Self-improvement patches require champion/challenger evaluation. | Security spec §7 | active |
| si-002 | Freeze triggers exist for score/cost/safety regressions. | Security spec §7.3 | active |

---

## 6. Memory proposal workflow

```text
1. Provider or deterministic scanner proposes memory update.
2. Controller validates memory_update.schema.json.
3. Controller computes delta from prior claims.
4. Controller checks contradiction index.
5. Claude or human reviews if needed.
6. Controller applies accepted update.
7. Controller records memory event.
```

---

## 7. Memory event schema summary

Events:

```text
memory_proposal_created
memory_proposal_validated
memory_contradiction_detected
memory_review_completed
memory_update_applied
memory_update_rejected
```

---

## 8. Practical retrieval rules for agents

Agents should not receive the entire memory wiki by default.

The controller builds compact memory packets:

```text
Task type: research fusion
  include invariants, research claims, relevant source ledger, recent experiment lessons

Task type: implementation
  include invariants, codebase claims, relevant module contracts

Task type: review
  include invariants, task packet, changed files, relevant prior review claims
```

---

## 9. Anti-corruption rules

Do not merge memory updates that:

- lack evidence;
- modify invariants without human approval;
- contradict prior claims without review;
- cite a missing file/schema/source;
- derive a broad conclusion from one failed experiment;
- claim validation is wrong without evidence;
- claim a method works without a score or reproducible test;
- encode model speculation as fact.

---

## 10. End state for Phase 0

At the end of Phase 0, this wiki should contain:

- fixed invariants;
- fixture baseline result;
- research-fusion proxy result;
- failure analysis if fusion did not improve;
- provider version notes;
- self-improvement scan outcome;
- no unreviewed truth claims.

