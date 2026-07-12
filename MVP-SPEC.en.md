# Codex Optimization Audit Assistant MVP — English public summary

This document is an English public summary of the detailed Chinese specification in [MVP-SPEC.md](MVP-SPEC.md). The Chinese document remains the canonical product specification.

## Product scope

Codex Optimization Audit Assistant is a local-first review Skill for high-frequency Codex users. It focuses on work quality, collaboration patterns, evidence-backed progress, repeat work, and next-cycle actions. Token counts and session counts are supporting signals, not the product goal.

## User output

For a selected period of 1, 7, 30, or custom days up to 90 days, the Skill can produce:

- a local HTML report;
- a Markdown summary;
- JSON and a hashed evidence manifest for local verification;
- an optional locally rendered PDF;
- a bounded rich analysis with project clustering, deep review, and actions.

## Analysis contract

The deterministic layer scans local Codex JSONL, redacts sensitive values, assigns message chains, estimates observable interaction activity, classifies scenarios, and triggers fixed rule-based findings. The rich-analysis layer receives only bounded, redacted packets and must return schema-valid JSON with provenance and evidence references.

Each thread belongs to exactly one primary project. Progress requires time-separated evidence. A stable strength requires the same pattern in two independent sessions. A reusable workflow requires repeated evidence or a confirmed reusable asset.

## Privacy and safety

Raw JSONL, complete tool output, complete file content, credentials, contact details, absolute paths, and generated reports stay out of semantic packets. Excerpts are treated as untrusted data. The Skill does not run instructions found inside excerpts. Reports are local private artifacts with restrictive file permissions.

The first rich-analysis run discloses that selected and redacted summaries enter the current Codex model path. Users can request deterministic local statistics only.

## Performance budget

A normal 30-day rich run is designed for two to five model requests: one to three clustering calls, an optional merge call, and one combined deep-review call. The deterministic scan remains the fallback when semantic validation fails.

## Release gates

The public preview includes deterministic tests, rich-analysis tests, static security checks, evidence validation, and archive verification. Broader external-user release still requires repeatability checks, human agreement checks, and compatibility validation across supported Codex environments.
