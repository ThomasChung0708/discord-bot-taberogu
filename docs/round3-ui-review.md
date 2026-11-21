# Round 3 Step B · critique and polish

Scope: public list/detail, map/index, standalone login, admin workbench. Two independent read-only assessments reviewed PRODUCT.md, DESIGN.md and the actual templates/CSS/JavaScript. Assessment A used impeccable's critique rubric; Assessment B ran its detector once. User authorized fixing findings, so no additional prioritization interview was needed.

## Assessment A — before polish, source evidence only

These scores describe the first implementation, before the fixes below. They are not rendered or post-polish scores.

| Heuristic | Public | Map | Login | Admin |
|---|---:|---:|---:|---:|
| System status | 3 | 2 | 3 | 2 |
| Real-world match | 4 | 3 | 4 | 3 |
| Control / freedom | 3 | 2 | 3 | 2 |
| Consistency | 4 | 3 | 4 | 3 |
| Error prevention | 3 | 3 | 3 | 2 |
| Recognition | 3 | 3 | 4 | 3 |
| Efficiency | 3 | 2 | 3 | 3 |
| Aesthetic / minimalism | 4 | 3 | 4 | 3 |
| Error recovery | 3 | 2 | 2 | 2 |
| Help | 2 | 2 | 2 | 3 |
| Total / 40 | 32 | 25 | 32 | 26 |

Design specificity: strong. Paper, vermilion stamp, Mincho type, fine rules and menu prices serve a friends' restaurant notebook. The admin needed interaction precision rather than further ornament.

Cognitive load: public and login low; map moderate because map and list are independent; admin moderate because it exposes structured budgets and several maintenance directions. Fieldsets, price guidance and explicit operation names clarify those choices.

Persona findings: mobile travelers needed a persistent detail Close and a jump to the map index; keyboard users needed distinct maintenance button names; frequent maintainers needed draft preservation and feedback visible near their working position. The emotional low point was uncertainty after saving and potential loss of an unfinished note.

## Assessment B — detector

One `detect --json --changed` run returned complete JSON, exit code 1, five warnings (all reported line 0):

- `cream-palette`: all four templates. Intentional: the user's explicit warm-paper direction.
- `broken-image`: admin template. Intentional: the hidden preview receives a validated source before being shown.

No detector rerun. These contextual exceptions do not prove visual quality.

## Polish applied to each surface

- Public: sticky Close in the full-screen sheet; correct recommender separator; visible image-failure placeholder; valid phrasing content inside row buttons; reserved image aspect boxes. Complete detail hours, tags, comments and links remain available. Native modal isolation plus explicit Tab cycling, Escape and focus return are implemented.
- Map: explicit local Select action plus separate links to public notes; selected-shop card before the map; phone jump to index; clear empty search; direct shared-map fallback. Initial query scrolling is separated from later local selection. No coordinates are invented.
- Login: password error association, localized recovery/progress, protected in-flight field and restored focus after incorrect password. Titles and navigation language follow the shared toggle.
- Admin: pending comment/file drafts participate in navigation guards and survive unrelated saves; all editor controls lock during mutations. Sticky save/maintenance status, distinct bulk action labels, range guidance/validation, and validity reset on shop changes. Read-only legacy notes are omitted from editor payloads so concurrent additions survive. Numeric blank budgets retain existing API semantics, stated beside the fields.
- Shared: document titles, navigation labels, language pressed states, text selection, caret, scrollbars, focus and reduced motion use the same tokens. Creator fields stay private; public comments show dates only. Generated legacy append headers are redacted only in the public projection; original DB/admin text is preserved.

Independent source rechecks confirmed the original draft/race issues were addressed and caught the final map scroll, validation-reset and empty-status defects, which were then fixed.

## Verification and limits

- `python -m py_compile admin_app.py`: passed.
- `python -m unittest discover -s tests -v`: 50 tests passed.
- `node --check` on all five JavaScript files: passed.
- `node tests/test_round3_frontend.js`: passed; in-memory DOM tests cover draft cancellation/preservation, omission of legacy comments from the payload, zero budget, pending-save lock, range rejection and validity reset. This is not a browser test.
- HTML parser check: unique IDs and explicit labels on all form controls.
- Calculated text contrast against paper/page: ink 12.21/13.54, muted 5.46/6.06, vermilion 6.00/6.65, matcha 5.90/6.54. Control edge contrast 3.41/3.78.
- `git diff --check`: passed.
- Impeccable shot failed at 1440×900 and 390×844 with a closed browser connection (os error 10054). Not retried. Browser-tool fallback reported its access host unavailable. No screenshots, live overlay, rendered overflow checks or browser keyboard verification were obtained. No post-polish visual score is claimed.
- Launcher fallback used direct PRODUCT/DESIGN/craft-floor reads. This review is stored here instead of a CLI critique snapshot; trend was not read. No live overlay server was started. The isolated preview server was stopped, its fixture DB/files removed, and no real database, uploads or environment file was modified.

Questions skipped: fixes were explicitly authorized in the task; no unresolved product decision required another interview.


## Follow-up fixes

- Removed public author aliases from serialization and the shared comment renderer. Public comment metadata contains only dates; authenticated admin comments still show created_by. The existing public-only legacy-header redaction regex is unchanged.
- All four templates now load through an import-time asset rewrite in admin_app.py. CSS, JavaScript and favicon references receive the first eight hex characters of their file's SHA-256 content hash. Static responses send Cache-Control: no-cache and retain nosniff and ETag conditional responses.
- Round 1, Round 2 and Round 3 fixtures now use system temporary directories, including their nested per-test directories. Round 1 also mocks dotenv loading, matching the later suites.
- When both budgets are absent, public list rows show a single muted localized budget message; detail shows one budget fact. Recorded lunch/dinner prices remain unchanged.
- Re-ran python -m py_compile admin_app.py, python -m unittest discover -s tests -v (50 passed), node tests/test_round3_frontend.js and syntax checks for all five JavaScript files. Asset tests cover all four pages, exact content hashes, HTTP 200, no-cache/nosniff and ETag 304. Frontend tests cover public date-only metadata, admin creators, missing budgets and recorded budgets.
- One detector pass for this follow-up reported the same five documented contextual warnings: four warm-paper palettes and the hidden admin image preview. No new rendered browser verification was performed in this follow-up; the user supplied desktop/mobile browser findings. No real DB, uploads or .env were accessed, and the Discord bot was not started.
- No .ui-preview directory remains. Removed the empty screenshot workspace and Python bytecode caches after verification; PRODUCT.md, DESIGN.md and this review remain.

## REQUEST_CHANGES corrections

- RestaurantPayload.comments defaults to None; update_restaurant uses SQL COALESCE to preserve current legacy text when omitted/null and retains explicit text exactly, including whitespace and empty strings. The admin payload omits comments, and Discord /edit_restaurant passes None when comments are unchanged.
- Replaced the exact-source payload assertions with backend behavior tests for byte-identical preservation, concurrent append preservation in both legacy comments and comment_items, and explicit replacement text. Node tests assert the editor payload has no comments key; offline command tests verify the other production caller.
- Shared requests check HTTP success before parsing successful JSON. Non-JSON HTTP errors and malformed successful JSON show a status-based error; JSON API detail and session/network handling remain covered. Rejected link URLs render a plain span. Added a short explanation of the editor selection version and busy guards.
- Verification: python -m py_compile admin_app.py db.py; python -m unittest discover -s tests -v (53 passed); node tests/test_round3_frontend.js (all passed). No live browser verification was performed. Only isolated temporary DB/upload fixtures were used; no real DB, uploads or .env were accessed, no packages were installed, and the Discord bot was not started. No commit or push was made.
