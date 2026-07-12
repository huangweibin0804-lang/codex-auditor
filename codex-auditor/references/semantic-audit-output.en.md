# SemanticAuditOutput v1

Require schema-valid JSON. Reject unknown top-level fields.

## Required metadata

```text
model_id
analysis_prompt_version
semantic_input_schema_version
semantic_output_schema_version
adapter_version
message_chain_version
scenario_classifier_version
action_rules_version
```

## Project contract

Each project contains a stable local `project_id`, a user-facing name, member `thread_ref` values, a confidence label, interaction share, evidence IDs, and a summary. Every thread has exactly one primary project.

## Claim classes

- `fact`: requires at least one local evidence ID.
- `estimate`: requires a deterministic metric field and its metric version.
- `inference`: requires at least two supporting evidence IDs or one evidence ID plus one deterministic finding ID; include confidence.
- `recommendation`: requires a referenced fact, estimate, or inference.

## Positive findings

- `progress`: require an earlier and later evidence ID for the same behavior dimension. Evidence timestamps must differ by at least the greater of 24 hours or 20% of the audit range. If the condition is not met, omit progress.
- `stable_strength`: require the same positive pattern in at least two independent sessions. If the condition is not met, describe only a single observed example.
- `reusable_workflow`: require the pattern in at least two independent sessions or one confirmed reusable asset such as a Skill, script, or template. Current related files alone are insufficient.

## Actions

Return zero to three actions. Each action contains:

```text
behavior_change
trigger_condition
next_review_signal
supporting_evidence_ids
```

## Validation

- Reject factual claims without evidence IDs.
- Reject project completion claims derived only from `TASK_COMPLETE`.
- Reject personality, intelligence, or ability labels.
- Reject claims that hide semantic coverage or data gaps.
- Allow one repair attempt, then degrade to the deterministic report.
