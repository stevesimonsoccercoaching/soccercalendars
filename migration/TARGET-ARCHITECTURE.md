# Soccer Calendars — target multi-league architecture

**Status:** Proposed design and migration checklist; **no file moves or production changes authorized by this document**.  
**Prepared:** November 18, 2026  
**Repository:** `stevesimonsoccercoaching/soccercalendars`  
**Working branch:** `refactor/multi-league-architecture`  
**Baseline:** [`BASELINE-2026-11-18.md`](BASELINE-2026-11-18.md), source commit `d4e1b51712d24f7c690c3f3bd443517c27044726`.

## 1. Objective

Use one GitHub repository for five or more soccer leagues, league divisions, tournaments, and historical seasons. Preserve the CJSL Fall 2026 schedules and all revision history. Retain explicit cancellation indicators for CJSL and the missing-match **warning, not cancellation** policy for EDP. Keep `main` publishing the current calendars until the redesign is verified. Leave `archive/pre-restructure-2026` untouched.

## 2. Directory contract

A single GotSport event is the smallest independently configured schedule unit:

```text
config/<soccer-year>/<organizer>/<competition>/events/<event-id>/manifest.json
data/<soccer-year>/<organizer>/<competition>/events/<event-id>/<team-slug>.xlsx
docs/<soccer-year>/<organizer>/<competition>/events/<event-id>/<team-slug>.ics
state/<soccer-year>/<organizer>/<competition>/events/<event-id>/revisions.json
state/<soccer-year>/<organizer>/<competition>/events/<event-id>/missing_matches.json  # only where used
```

For the two current events, the new **relative** event directories would be:

```text
2026-27/cjsl/league/events/55206
2026-27/edp/north-atlantic/events/54578
```

Thus the CJSL manifest would be `config/2026-27/cjsl/league/events/55206/manifest.json`, while its spreadsheets, calendar feeds, and revision state would be under their corresponding root directories, with the same relative path.

- `<soccer-year>` is a directory grouping (e.g., `2026-27`) and **does not replace** the event's own `competition.year` and `competition.season` fields.
- `<organizer>` is a stable lowercase identifier (e.g., `cjsl`, `edp`); `<competition>` is the competition/program (e.g., `league`, `north-atlantic`). This also supports future tournaments without assuming that a league and a tournament share the same status rules.
- `<event-id>` is the **actual** GotSport event ID; do not create placeholder events or assume spring and fall use the same event ID.
- A new spring event gets another event directory when the real GotSport ID is known. A combined season stays one event if the provider actually uses one event.
- Team slugs form the stable XLSX and ICS filenames within an event. Preserve existing slugs unless a separately tested migration changes them.
- Other providers can be added later with an explicit provider-specific adapter and validated ID convention; **today's validator is GotSport-specific**, not universally provider-neutral.

**Invariant:** Each event has exactly one canonical manifest. Do not keep both the old and new manifest under `config/` after cutover: recursive discovery would register the event twice. An archival snapshot belongs in the archive branch or another explicitly excluded archive area, not in active manifest discovery.

## 3. Manifest and processing contracts

Retain all current manifest information on relocation, including team names, `short_name`, `slug`, team and export IDs, schedule and export links, `display_names`, `calendar_defaults`, timezone, `event_id`, and any competition-specific policy. Do not silently rewrite `year` or `season` based on the new directory name.

The build dispatcher must select behavior **explicitly**, not by assuming an organizer name or that all schedules expose reliable status:

| Profile to preserve | Current implementation | Required behavior |
|---|---|---|
| Explicit match status (CJSL) | `scripts/build_cjsl_calendars.py` | Existing UID conventions, explicit canceled/rained-out/rescheduled status presentation, existing reminders and revision-state keys |
| Metadata / disappearance warnings (EDP) | `scripts/build_soccer_calendars.py`, `scripts/sync_missing_soccer_matches.py` | Do not infer cancellation when a match disappears; retain last-known match with warning according to `missing_match_policy: preserve-warning`; enforce export safety thresholds |

**Later implementation decision:** Introduce a tested manifest field or central profile registry that identifies the correct builder and policy. Adding a fifth league should ordinarily require a manifest and data, not another copy of the entire workflow. Do **not** consolidate the two existing builders until tests prove that UID, SEQUENCE, LAST-MODIFIED, alarms, descriptions, and cancellation/missing-match semantics remain compatible.

## 4. What the existing code supports and what must change

Audited on `refactor/multi-league-architecture` on November 18, 2026:

| Component | Existing capability / limitation | Target change |
|---|---|---|
| `scripts/build_cjsl_calendars.py` | Already maps the directory containing each manifest to matching `data/`, `docs/`, and `state/` paths; separate CJSL status behavior | Keep during first migration; update obsolete CLI usage text; dispatch only to appropriate event profile |
| `scripts/build_soccer_calendars.py` | Already mirrors manifest-relative paths; supports EDP preserved-missing state | Keep during first migration; dispatch through shared orchestration |
| `scripts/validate_soccer_manifest.py` | Read-only validation and XLSX inventory; now accepts two GotSport schedule URL formats | Discover and validate every canonical manifest in the consolidated test/build process; expand coverage for new providers only when actually needed |
| `scripts/build_calendar_index.py` | Already finds `config/**/manifest.json`; derives ICS links using matching directory paths | Ensure archive/historical presentation and published links remain correct; test indexing after moves |
| `scripts/report_schedule_changes.py` | Diff layer maps XLSX to corresponding manifest; **current status report** searches only `**/cjsl/*/manifest.json` | Generalize discovery and label each event; avoid treating EDP unknown status as confirmed cancellation; preserve cross-team match deduplication |
| `scripts/sync_missing_soccer_matches.py` | Finds EDP XLSX relative to manifest; compares *same file path* at base and head using `git show` | Handle directory relocation safely: don't interpret a rename as matches disappearing, and don't silently lose missing-match comparisons for the first post-migration update |
| `.github/workflows/build-cjsl-calendars.yml` | Finds only `*/cjsl/*/manifest.json`; will not find new `cjsl/league/events/<id>` folders | Replace league-pattern discovery with a shared manifest inventory and profile dispatch |
| `.github/workflows/build-edp-north-atlantic.yml` | EDP manifest path and XLSX trigger are fixed to the original location | Replace with shared event discovery and path-independent triggers |
| `.github/workflows/test-build-cjsl-calendars.yml` | Fixed old CJSL manifest and specific ICS path / match | Update the fixture path and preserve content assertions; ideally add event-independent regression tests |
| `.github/workflows/test-gotsport-export.yml` | Manual exploratory export test hardcoded to a CJSL event/team | Keep manual; parameterize later if it becomes a maintained test |
| `.github/workflows/watch-sffk-teamapp-calendar.yml` | Independent scheduled Team App/Weave monitor under `state/weave/` | **Out of scope:** preserve its workflow and state path; do not fold it into GotSport processing without separate design |

## 5. Target build, test, and publishing stages

1. **Read-only discovery:** Search canonical `config/**/manifest.json` files, validate the directory contract, detect duplicate event registrations and team slugs **within each event**, and report a full inventory.
2. **Read-only validation:** Validate each registered XLSX against its manifest, then test schedule parser, missing-data thresholds, and all calendar behavior profiles.
3. **Build in a disposable workspace:** Generate event ICS and an index; compare with snapshots/known outputs. The feature branch must never commit or publish generated output.
4. **Production build on `main` only:** A single orchestrated build should avoid separate workflows racing to update the same `docs/index.html` or state. Preserve the standalone Weave watch.
5. **Change reporting:** Report each event independently; distinguish explicit canceled/rescheduled outcomes from missing/export-unknown EDP matches.
6. **Publication:** Only after all gates pass, commit generated `docs/` and `state/` on `main` and publish the intended GitHub Pages files.

During transition, the existing CJSL/EDP workflows remain `main`-only for push triggers and restrict their commit steps to `main`. **Do not enable a new publishing workflow before replacing or disabling overlapping old publishers.**

## 6. Migration integrity gates

Before moving any files:

- Capture current `main` again: it can receive real CJSL schedule updates while the feature branch is under construction.
- Use `migration/BASELINE-2026-11-18.md` to check 37 CJSL + 3 EDP registrations, 40 XLSX, 40 ICS, 225 CJSL + 24 EDP revision records, and zero EDP missing matches **as of the baseline**. Later live counts may legitimately differ; reconcile, don't blindly overwrite.
- Move original XLSX and state files **without changing their content**; confirm Git blob IDs on unchanged files. Do not hand-copy 40 XLSX / 40 ICS one at a time in the GitHub browser; use a controlled bulk move in a development working copy when that phase begins.
- Preserve revision JSON keys and values. The same match should keep its `UID`, `SEQUENCE`, and `LAST-MODIFIED`, unless there is a genuine schedule change. Check event totals, dates, descriptions, reminders, and special statuses.
- Treat the relocation separately from an ordinary schedule update. Because the missing-match detector reads the same path from both Git revisions, the first post-move comparison needs an explicit old-to-new path mapping or a verified new baseline. **Never equate 'comparison skipped' with 'no missing matches'.**
- Compare generated calendars with original versions before and after migration; don't assume matching file counts means matching events.
- Determine GitHub Pages publication settings and the old/new subscription URLs before the production switch. Moving an ICS file changes its URL; an archive branch by itself does not keep a previous calendar subscription updating. Choose either tested old-path compatibility feeds or an explicit resubscription process.
- Re-run the full suite and verify the latest `main` changes before merging the refactor into `main`.

## 7. Sequence of implementation

**Current phase:** Architectural contract documented; no source files moved.

1. Add a **read-only, central manifest discovery and inventory tool** with tests across both existing manifests. No file writes; no publishing. Detect duplicate canonical event registrations and invalid relative paths.
2. Add tested profile-based orchestration for both existing builders; keep their UID/state formats and safety behavior intact.
3. Unify the Actions pre-merge tests and main-only production publish workflow. Ensure the index is produced once and no overlapping publisher remains.
4. Test migrations in isolated working copy: move manifests, XLSX, ICS, and state to mirrored paths; update CJSL integration tests and any remaining path assumptions.
5. Run comparisons of semantic ICS contents, revision states, missing-match safety, and index links against the current baseline and latest `main`.
6. Confirm old subscription URL handling, then merge to `main`; resubscribe if needed and verify actual published calendar URLs.
7. Only after the historical season is final, mark CJSL Fall 2026 archival and tag the final release; continue adding new real events under the same standard.

**Change control:** All redesign work goes to `refactor/multi-league-architecture`, the historical `archive/pre-restructure-2026` branch stays untouched, and `main` keeps its existing production behavior until the tested cutover.
