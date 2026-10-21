# House rules

Rules every worker, orchestrator and planning session follows, whatever
the repo. Copy this file to `house-rules.md` (same folder) and make it
yours, or let `/onboard` write it from your answers. Only the list items
above the `Pull request reviews` section are rules: `/fanout` pastes them
into every worker's brief, `/ship` and `/bugbot` follow them for commits,
PRs and replies, and the roadmap-planning skill follows them for its
design doc PR and commits. The items in that section at the end are not
rules and are not pasted into briefs: they say who reviews your pull
requests, if anyone, and only the steps that request reviewers read them.

- Everything is authored as me. No attribution trailer, no co-author line, no mention of an AI agent in commits, pull requests, comments or documents.
- Work lands on a branch through a pull request. Never commit to or push main.
- Plain, direct wording with proper capitalisation.
- Mark every reported result OBSERVED, STALE or NOT RUN. A number with no command behind it is not a result.
- When escalating, lead with the decision and its consequence in plain terms, then the evidence.
- External systems (chat, issue trackers, docs, customer-facing tools): read freely. Write when I ask for it or the task clearly calls for it, and show me the exact change before anything reaches other people.

## Pull request reviews

Who to ask for a review, per repo or per owner. `/ship`, `/bugbot`,
`/fanout` and its workers, orchestrators and the roadmap-planning skill
read this section whenever they open a pull request or end a review loop.
Leave it out, or leave a repo out, if you want no one asked.

One list item per repo (`owner/repo`) or per owner (every repo of that
GitHub user or org). Each says who to request: GitHub logins or
`org/team` names, or the reviewers the repo's AGENTS.md or CONTRIBUTING
names. And when: when the PR opens, or after the review bot (Bugbot, for
example) is clear on the current head. On a repo with no review bot,
"after the review bot is clear" means once the PR is otherwise ready. A
repo's item beats its owner's.

A repo with no item, under its own name or its owner's: request nobody
and leave reviews to you. The PR's author is never requested, even when an
item names them. Replace the two items below with your own, or delete
them.

- `your-org/your-repo`: request `your-teammate` and `your-org/your-team` when the PR opens.
- `your-org`: request the reviewers the repo's AGENTS.md or CONTRIBUTING names, after the review bot is clear on the current head.
