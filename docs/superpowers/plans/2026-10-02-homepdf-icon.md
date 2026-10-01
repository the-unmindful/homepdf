# HomePDF Windows icon implementation plan

Goal: apply the user-specified SnagItOpen icon design to HomePDF, with a white H on deep green. The user explicitly requested the existing design with only glyph and palette changes.

1. Reuse SnagItOpen's WPF icon renderer, preserving rounded-square geometry, gradient, border, glyph font/weight/height, centering, shadow, frame sizes and ICO format. Set the glyph to H and palette to deep green.
2. Generate app.ico and a PNG preview. Check all eight frames and compare visual geometry to the reference.
3. Bundle app.ico as both the executable icon and a Qt runtime resource. Ship a current-user Start menu shortcut helper with the portable package.
4. Rebuild using the installed project-local dependencies; keep previous packages in the existing build archive. Verify executable icon resources, runtime icon, ZIP integrity and PDF smoke checks.
5. Create HomePDF.lnk in the current user's Start menu, verify target/icon paths, then update the existing GitHub repository and current portable download.

The work runs inline in the current session. SnagItOpen is a read-only reference.
