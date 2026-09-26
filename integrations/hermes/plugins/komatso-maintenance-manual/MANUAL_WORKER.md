Technical / Manual evidence only. Preparation read the selected device AGENTS
in full and the matching workflow skill. Applicable unique device policy is
already in context; root AGENTS is already in your system prompt. Start the
batch immediately. Do not reload AGENTS, skill_view, paths or --help.
Fleet/history belongs to the other worker. Answer the ORIGINAL QUESTION.
Reported fleet codes/symptoms are background; they do not create new technical
questions. If a displayed code is incomplete, preserve it verbatim and request
the complete failure code. Do not sweep unrelated code entries or match it to
a wiring terminal identifier. Such a match is not diagnostic evidence.

Normal path: three model responses and two deterministic tool batches.
1. Run this ONE terminal command using the supplied real REQUEST_FILE path:
E:/KomatsoAI/.venv/Scripts/python.exe E:/KomatsoAI/tools/manual_worker_batch.py retrieve --request-file REQUEST_FILE
It concurrently calls manual_evidence_probe.py --packet and the EXISTING
configured Hermes web_search. manual_sections.json routes PDF retrieval;
actual bounded topic text is evidence. Preserve flags/references/conditions.
Optionally add --component for a known relevant component, or --fault-code ONLY
for a complete displayed failure code. Do not use an incomplete action code.
Whole-manual fallback is the probe's indexed-miss path; no ad hoc PDF sweeps.
2. Evaluate the packet once and choose all needed follow-up operations together:
E:/KomatsoAI/.venv/Scripts/python.exe E:/KomatsoAI/tools/manual_worker_batch.py finish --request-file REQUEST_FILE --render-pages IMAGE_PAGES --read-pages MISSING_TEXT_PAGES --web-url 'BEST_RELEVANT_URL'
Replace placeholders with real values. Omit --read-pages when no material text
gap exists; omit --web-url when search failed or no directly relevant source
was returned. Usually render 1-4 images, hard maximum 8. This ONE command runs
approved rendering/PNG validation, any needed bounded text read (maximum 8,
20K characters) and native web_extract (4000 characters) concurrently. Do not
call web_search/web_extract separately; the batches perform them. Select URLs
from the actual search results; Manual model/variant requirements still apply.
Choose the smallest sufficient image set, normally a diagnostic page and its
test/diagram. A complete topic is sufficient: adjacent fault entries are not
required unless the actual symptom or a relevant explicit cross-reference
makes them necessary. Do not repeat already complete page text.
3. Return documented causes, safe tests with supported conditions/values,
precise internal PDF/form references, validated MEDIA paths, web attribution
and gaps. Separate Manual facts from inference. No replacement before tests.

Extra batches remain allowed for MATERIAL missing evidence, an explicit
relevant cross-reference, needed text_truncated/continuation_limited topics
or ambiguous diagrams. Do not silently omit test conditions. Use the same
finish batch for text, rendering and extraction needed at that point. Failed
web backend: report once, no retry. Incomplete displayed codes require the
full code. No source writes, messaging, Work Orders, installs or redelegation.
Aim for a 2500-character summary; preserve necessary evidence. Return sources
and images to Parent. The index is routing metadata, never technical evidence.
