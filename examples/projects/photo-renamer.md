---
name: Photo Renamer
category: personal
status: icebox
energy: medium
description: A CLI that renames image files in place from their EXIF capture date.
repo: ~/code/photo-renamer
updated: 2026-05-09
---

## Next prompt

```text
Reopen only if the stdlib gains EXIF parsing, or you decide a third-party
dependency is acceptable for this one. If so, re-read the last three log
entries first, then restart from the dry-run renamer and add real EXIF date
extraction before anything else.
```

## Log

- 2026-05-09: Shelved. Reading EXIF dates cleanly needs a third-party library, which breaks the stdlib-only rule this set of tools holds to. Parked rather than pull in a dependency for a tool used once a year.
- 2026-05-08: Dry-run renamer works against a hardcoded date: it prints the old and new names without touching disk. The EXIF read is still stubbed.
- 2026-05-06: Sketched the plan: read the capture date, format as YYYYMMDD-HHMMSS, rename in place, refuse to overwrite an existing file.
