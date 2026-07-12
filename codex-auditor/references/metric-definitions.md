# Metric definitions

## Evidence classes

- **Fact**: a normalized event that can be located by `evidence_id` in the local manifest.
- **Estimate**: a deterministic calculation over timestamps; it is not actual working time.
- **Rule-based finding**: a fixed threshold triggered by local evidence. It is not a judgment about the user's ability or intent.

The manifest hashes the source bytes observed during the audit. An active session can append after scanning, so a later verification may correctly report `source_changed`; the report remains historical evidence, but no longer claims to be fully reproducible against the mutable source file.

## Time

`event_coverage_minutes` is the number of distinct local calendar minutes that include a user or visible assistant message. `interaction_interval_estimate_minutes` unions only these intervals: user-to-next-assistant (capped at two minutes) and assistant-to-next-user (capped at fifteen minutes). Assistant-to-assistant and tool events do not extend activity.

Preset ranges use local natural days and include today: 1 day starts at today's local midnight, 7 days starts at local midnight six dates ago, and 30 days starts at local midnight twenty-nine dates ago. Custom dates are also interpreted in the local timezone.

## Message chain v2

Start a chain on the first user message, a gap over 30 minutes, or a strong start marker (`新任务`, `另一个任务`, `换个任务`, `换个话题`, `现在开始新`) followed by boundary punctuation or whitespace. Start a soft chain only when `接下来做` has a valid `TASK_COMPLETE` in the preceding five minutes. A task-complete event alone never starts a chain. Unmarked new tasks can remain in a chain.

## Findings

- `R1_CONSTRAINT_ADDITION`: three later user messages in one chain match a fixed constraint marker.
- `R2_REPEAT_TYPED_PATH`: a structured patch/path event repeats at least three times in one workspace in 30 minutes.
- `R3_VERIFIED_FAILURE`: three explicit failures, or a failure rate of at least 20% across five explicit results.
- `R4_SCOPE_REDIRECTION`: two later user messages in one chain match a fixed redirection marker.

Sort findings by fixed score, then count, latest evidence time, and rule id. Output at most three actions.

## Semantic evidence thresholds

- Progress requires two time-separated evidence points for the same behavior dimension. The separation is at least 24 hours or 20% of the audit range, whichever is greater.
- A stable strength requires the same positive pattern in two independent sessions.
- A reusable workflow requires the pattern in two independent sessions or a confirmed reusable asset event.

## Repeatability metrics

Run the same frozen input three times with identical model and version metadata.

- `project_member_overlap`: mean pairwise Jaccard overlap for matched top-three project thread sets; target at least 0.80.
- `core_evidence_overlap`: mean pairwise Jaccard overlap of evidence IDs supporting problems, progress, strengths, workflows, and actions; target at least 0.85.
- `unsupported_fact_count`: number of specific factual claims without valid evidence IDs; target 0 in every run.

For a stratified human review sample of at least 50 threads and 30 semantic claims, require at least 0.80 primary-project assignment agreement and at least 0.90 evidence-support agreement.
