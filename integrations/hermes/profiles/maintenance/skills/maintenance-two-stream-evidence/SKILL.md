---
name: maintenance-two-stream-evidence
description: Parallel manual evidence and fleet history for a specific machine repair fault when both sources can help.
platforms: [windows]
metadata:
  hermes:
    tags: [maintenance, troubleshooting, fleet, delegation]
---

# Two-stream repair evidence

Use this only for a specific fleet unit's technical fault when manual evidence
and real fleet history can be investigated independently. For greetings,
clarifications, model selection, and one-lookup specifications, work directly.
The Maintenance parent owns the user's question, safety, evidence conflicts,
missing-information requests, and final Bale reply.

First follow `E:\KomatsoAI\AGENTS.md`. For a named fleet unit, obtain
`machine_context` first as that file requires. Pass the exact user question,
canonical machine code, verified model if known, reported symptom/error, and
relevant machine context explicitly to **each** child. A child has no prior
conversation history. If model identity is unknown or provisional, say so;
do not select a manual by guesswork.

Call `delegate_task` **once** with a `tasks` array of exactly two entries:

1. **Technical / Manual Evidence** — Load the relevant device `AGENTS.md`
   before device sources. Inspect the correct Shop Manual / Parts Book and
   project-approved web sources only when appropriate. Return documented
   causes, diagnostic checks/tests, supported values/specifications, and useful
   diagram/manual pages. Label documented facts separately from inference.
   If the model is unverified, report the missing identity instead of using an
   assumed manual.
2. **Fleet / Maintenance History** — Use the approved fleet tools as relevant:
   `machine_context`, `fleet_today`, `machine_timeline`, and
   `maintenance_history`. Use `service_history` only when the existing root
   `AGENTS.md` permits it. Return current context, related recorded failures
   and repairs, recent events, recurring patterns, and source freshness.
   Distinguish recorded facts from possible links. No record means no record
   was found, not that an event did not happen.

Make each task goal and context self-contained. Both are **read-only
investigations**: no user messages, Work Orders, DB/Excel/file writes,
repairs writes, file deletion, cron, or other messaging side effects. Never
invent measurements, test results, or repair outcomes. Do not request nested
delegation. Do not set separate completion groups; the two results should
arrive together. Hermes v0.21.4 runs top-level model delegation in the
background and resumes the parent with the consolidated result. Do not send
an interim user answer while the pair runs.

After both results arrive, reconcile them against `AGENTS.md`, identify
missing evidence and conflicts, then send one practical final answer. Cite
manual pages and fleet records where available, and keep observed facts,
recorded work, and diagnostic inference distinct.
