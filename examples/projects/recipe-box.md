---
name: Recipe Box
category: work
status: review
energy: medium
description: A static-site generator that turns markdown recipes into a browsable site.
repo: ~/code/recipe-box
updated: 2026-06-02
---

## Next prompt

```text
Read README.md and the last three log entries in
legwork/projects/recipe-box.md before you start.

Task: the search box on the generated index page does not match on
ingredients, only on titles. Make the client-side filter also match any
ingredient line, case-insensitive, and keep matching titles ranked first.

Done when: typing "basil" on the index page shows every recipe with basil in
its ingredients, title matches still appear at the top, and the build script
runs clean on the sample recipes in examples/.

Model: sonnet

Final step: run /wrap to update the tracker and mint the next prompt.
```

## Log

- 2026-06-02: Built the ingredient-search change; left it in review for a manual look before it ships to the live site.
- 2026-05-30: Tag pages generate from frontmatter tags. Index lists newest first.
- 2026-05-25: Markdown to HTML conversion working; one page per recipe plus an index.
