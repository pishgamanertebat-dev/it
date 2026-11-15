# ROC-L8 — Atlas Copco technical documents

## Identity and scope
Atlas Copco ROC L8 is a surface drilling rig. Route ROC-L8, ROC L8 and ROCL8
to this folder. L830 is a model designation printed in D1; it is not proof
that every L830 instruction fits every ROC L8. ROC L8(30)LF is a separate
catalogue variant. L7, L740 and L740CR are not aliases of L8. Never transfer
Komatsu fault codes, specifications, pressures or part names to this rig.
Serial, engine, options and exact revision remain UNKNOWN until evidenced.

## Registered sources and selection
Read document_manifest.json and manual_sections.json as routing metadata.
Use only registered document_id values; never construct an arbitrary PDF path.
- D1 / roc-l8-trainee-9852960001: 369-page multi-model Bosal trainee binder,
  PMI 9852 9600 01; primary technical/troubleshooting source, not a proven
  complete workshop overhaul manual.
- D2 / roc-l8-parts-9853647820d: 1023-page ROC L8 parts book, owner default.
- D3 / roc-l8-lf-parts-9853678620: 1440-page ROC L8(30)LF independent book.
- D4 / roc-l8-cabin-9840042409: 16-sheet Bosal cabin electrical drawing.
- D5 / roc-l8-electrical-9840042418: 47-sheet main electrical drawing;
  refers to D4 for the cabin. D1's 44-sheet drawing remains a separate version.
No document automatically supersedes another. Copyright date is not fit evidence.

## Manual applicability and evidence
D1 is multi-model. PDF 10 is L740/L740CR; 11 is L830. C13/C15 engine data
must remain separate. Pump pages 64/65 differ. PLC 278–291 is L740CR;
292–303 is L830/L630. Hydraulic drawings 304–311 are L7, 312–315 L8.
Use only applicable routed sections and actual pages. UNKNOWN applicability
must be disclosed, never relabelled L8. Shared material can support a general
explanation without an extra serial question. Ask for engine/cabin/plate/options
only when the missing identity affects an exact setting, wiring, repair or order.
Do not combine model-specific values appearing on the same page.
Page 369 is FILEMARKET and excluded from technical retrieval; preserve the PDF.
No runtime OCR or broad automatic OCR. A textless page may be a real diagram.
Metadata finds sheets. Use finish inspect_images=true (or the same option on
technical_render_region for an evidenced crop) to attach actual source pixels
to native model vision. If the provider cannot show the image, disclose that
limitation and do not infer pins from metadata. Inspect the real image before stating pins, wire colours,
connections, pressures, quantities or other sensitive technical facts.

## Parts and independent books
Normal lookup searches D2 only. D3 is selectable explicitly for LF, a requested
comparison, a useful D2 miss or a concrete technical reason. Do not search both
for every question. Do not demand serial/model details for ordinary general
questions. Book default_search_enabled can exclude a book without deleting it.
Keep book_id, document_id, raw PN, normalized PN, Ref (including E01), assembly,
table/continuation/illustration pages, row bbox and revision separate.
Verify the real PDF row before giving an exact PN/Qty. A row bbox identifies a
table row, never the position of that part on an exploded view. VERIFIED_IN_DOCUMENT
does not mean FITS_ACTUAL_MACHINE. An index miss does not prove absence.
The cover string 8992 0074 37 --> has unconfirmed meaning; never parse it as a
machine serial range. D2 WAGON FRAME Ref 4 and D3 Ref 4 differ; never merge them.

## Hermes workflow and Persian answer
Use maintenance_manual_evidence for technical questions, maintenance_partbook_lookup
for identification and technical_render_region for a known region of a registered
source. Preserve document_id through retrieve, finish and rendering. Start with
the smallest relevant section; expand only after a documented gap. Retain existing
Hermes profile permissions and the root web policy where applicable.
Answer in clear Persian: observed symptom, documented operating principle,
practical ordered checks, safe conditions, and remaining uncertainty. Distinguish
documented fact from inference. Do not invent a PN, pressure, pin, torque, setting
or repair instruction. If the source is insufficient, say exactly which evidence
is missing and ask only the necessary question. Source/page provenance must remain
available in tool output and implementation/UAT reports; follow root presentation
rules for operational technician answers.

## Images and same-session delivery
Use E:\KomatsoAI\.venv\Scripts\python.exe and tools/render_page.py only.
Select genuine relevant pages, normally 1–4 images; retain root upper bounds.
Inspect the page before choosing a crop. Do not guess coordinates. Prefer a full
page if location is unknown; use a readable crop for small details when verified.
Final images: E:\KomatsoAI\artifacts\<HERMES_SESSION_ID>.
Temporary files: E:\KomatsoAI\runtime\<HERMES_SESSION_ID>.
Deliver returned MEDIA lines in the same session; use [[as_document]] for fine
text losslessly. Rendering proves image creation, not user receipt. Never modify
the original five PDFs or send operational test messages.

## Remaining source limitations
No complete dedicated engine manual, overhaul tolerances or universal serial/
option applicability has been established. Image metadata may contain UNKNOWN
fields; this is a routing limitation, not evidence that the circuit is absent.
