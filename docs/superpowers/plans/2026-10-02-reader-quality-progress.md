# Execution ledger — plan: docs/superpowers/plans/2026-10-02-reader-quality.md

Branch: codex/performance-ux. Base: c2a0d5a. Isolated checkout: temp/homepdf-updates. Baseline: 4 unittest tests passed.

User authorization: create a branch, create a plan and execute it all. The prior review and lightweight scope define the design; redundant approval gates are superseded by that instruction.

Pre-flight: Task 1 unique publication is consumed by Task 5 staging. Task 4 raw pixels are consumed by the reader in Tasks 2/3. Task 5 safe bootstrap is consumed by Task 6 startup and Task 8 frozen smoke tests. All public toolkit signatures remain stable.

Ruling: execute inline with one fresh final reviewer. No per-task implementer agents. The Windows environment has no Bash command on PATH; preserve equivalent task/test/commit records in this tracked ledger instead of depending on shell-only skill scripts. Cost if wrong: bookkeeping tooling differs; test evidence and Git history remain reproducible.
Task 1: complete. Eight preservation/collision tests failed RED, then all 12 suite tests passed GREEN. Ruling: duplicate selections use independent page copies so bookmarks resolve to the first occurrence; cost if wrong: duplicate-page navigation needs further fixture coverage.
Task 2: complete. Six real-widget regression tests failed RED and passed GREEN; full suite 18/18. Viewer attachment, locked/unreadable state, zoom anchor and continuous Fit Page are corrected.
