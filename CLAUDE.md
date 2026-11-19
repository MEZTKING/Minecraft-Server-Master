# Instructions for Claude sessions working on this repository

Read `PROJECT_STATUS.md` first. It is the hand-off document: current state, backlog with
designs, decisions, and open questions.

## Standing rule from the project owner: always update and upload the documents

After **every** completed change (code, fix, feature, or docs), in the same piece of work:

1. Update `PROJECT_STATUS.md`: the feature-status table, the backlog (move or annotate items),
   decisions log, known gaps, and changelog. Also update the "Last updated" date.
2. Update the user docs the change touches (`README.md`, `docs/*.md`). Every user-visible option
   or behaviour must be documented.
3. Run the tests (`python -m pytest tests`; GUI tests: `xvfb-run -a python3 -m unittest tests.test_mcsm`).
4. Commit and push to the working branch (see `PROJECT_STATUS.md` → How to work on it), so the
   documents on GitHub are always current. After pushing, check that the "Build installer"
   GitHub Actions run passes.

New feature requests from the owner go into the `PROJECT_STATUS.md` backlog immediately,
with the request in their words, a proposed design, and open questions, even before work
starts.

## Project conventions

- Runtime code uses the Python standard library only (no new dependencies without asking).
- Store times as timezone-aware UTC; convert at the edges.
- Never store or export player IP addresses or the RCON password; mask secrets in outputs.
- New data sources: reader returns plain data → wire into `mcsm/analyzer.py` → model in
  `mcsm/models.py` → render in the report → fixture + test in `tests/`.
- The GUI (`mcsm/gui.py`) is a thin shell that calls `mcsm/cli.py` `run_*`/`start_*`
  functions; add CLI options first, then GUI fields.
