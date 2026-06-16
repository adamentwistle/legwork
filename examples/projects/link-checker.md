---
name: Link Checker
category: personal
status: escalated
energy: medium
description: A CLI that crawls a local site build and reports broken internal links.
repo: ~/code/link-checker
updated: 2026-06-14
autonomy: loop
blocked_on: a human decision on whether the tool may make outbound network requests at all
---

## Vision

- North star: `linkcheck ./build` walks a static site directory, follows every
  internal href, and prints each link that resolves to nothing, with the file
  and line the broken link came from.
- Done means: a single Python file, stdlib only, exits non-zero when any
  internal link is broken, and the tests cover relative links, anchors and the
  root index.
- Guardrails: internal links only by default. Do not follow external URLs
  without an explicit flag, and never send traffic to a third-party host in
  the test suite.
- Escalate when: a change would make the tool fetch external URLs by default,
  send any network traffic off the local machine, or change the exit-code
  contract a CI job would depend on.
- Taste: one broken link per line, file:line first. Quiet on success. Errors
  are one line, never a stack trace.

## Next prompt

```text
DECISION NEEDED.

Attempted: added an optional `--external` flag so linkcheck also validates
outbound URLs by issuing a HEAD request to each one.

Uncertain: even behind a flag, this is the first time the tool sends traffic
off the local machine, which the vision guardrail says to escalate on. It also
needs a rate limit and a timeout before it touches anyone else's server.

Options:
A. Ship `--external` with a fixed 1 request/second limit and a 5s timeout,
   off by default, documented as slow on large sites.
B. Drop external checking and keep the tool strictly internal.
C. Defer until there is a cache so repeated runs do not re-fetch every URL.

Recommendation: A.

Reply with a letter. Once decided, read the last three log entries before
resuming, and run /wrap at the end.
```

## Log

- 2026-06-14: Reviewer escalated: external-URL checking would send traffic off the machine for the first time and needs a human call on rate limits before it ships.
- 2026-06-13: Internal crawler is solid; reports file:line for every dead relative link and anchor. Started on the optional external-URL check.
- 2026-06-07: First version walks a build directory and flags links to files that do not exist. Stdlib only, exits non-zero on any break.
