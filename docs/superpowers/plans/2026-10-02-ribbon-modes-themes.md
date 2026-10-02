# Reader ribbon, modes, themes and Windows launch follow-up

Scope: implement the user's specific ribbon design and repair the conflicting portable Windows registrations. Keep the existing stack and tools; add no runtime dependencies.

1. Keep Reader and Text controls visible before Tools. Preserve the chosen page layout across View, Select Text and Extracted Text; default new documents to Continuous.
2. Share page selection between Single and Continuous, remove the drag frame after release, transform rotated word coordinates, and preserve search across all six mode combinations.
3. Use native outline icons for navigation, fits, actual size, search, theme and tools. Navigation uses a page list; Tools uses a wrench; secondary chevrons and checked states describe pane behavior. Visible dropdown arrows expose available choices.
4. Wrap into two aligned rows when needed. Preserve readable selectors when manually opening panes at narrow widths; collapse the opposite pane when necessary. Keep both panes and a comfortable document viewport at normal desktop width.
5. Apply persisted System/Light/Dark palettes to existing widgets and icons. Preserve white PDF pages. Avoid reapplying an unchanged global stylesheet or palette.
6. Point Start and the canonical HomePdf.Document handler at the extracted portable executable. Remove HomePDF-owned duplicate PDF menu bindings, retain legacy compatibility commands, and preserve protected Windows default-choice hashes and other applications.
7. Verify source regressions, independent review, native screenshots and the rebuilt portable executable; repair the actual user's shortcuts/registrations and restart the correct build gracefully.

Complete: 81 tests passed, including real Windows worker processes, all reader/text/search combinations, rotated selection, narrow pane opening/dragging and themes. Independent review caught stale zoom/page state, rotated selection and narrow pane clipping; each has a regression fix. The packaged reader passed 100%/150% smoke checks. Live Shell enumeration now reports one HomePDF handler; the effective PDF default, Start catalog and actual running process all use the new portable executable. Protected default-choice records were compared before/after and are unchanged. Details are recorded in reader-quality-validation.md.
