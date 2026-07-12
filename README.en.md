# Codex Usage Auditor

A local-first Codex usage review Skill. It reads local Codex session logs and produces Markdown, HTML, JSON, and evidence manifests while keeping observable facts, deterministic activity estimates, rule-based findings, and next actions distinct.

## Install

```bash
git clone https://github.com/huangweibin0804-lang/codex-auditor.git /tmp/codex-auditor-repo
mkdir -p ~/.codex/skills
cp -R /tmp/codex-auditor-repo/codex-auditor ~/.codex/skills/codex-auditor
```

The default entry point is the Chinese Skill. For the English version, copy or rename `codex-auditor/SKILL.en.md` to `SKILL.md` in the installed directory.

After installation, send this request in Codex:

```text
Use the Codex Usage Auditor to review my recent 7 days of Codex activity.
```

You can also request 1, 7, or 30 days, or a custom range up to 90 days.

## Local run and tests

```bash
cd codex-auditor
python3 scripts/audit_codex_usage.py --days 7
python3 scripts/test_auditor.py
python3 scripts/test_rich_audit.py
python3 scripts/validate_skill_security.py .
```

Reports are written to `~/.codex/reports/`. PDF rendering is optional and requires Playwright:

```bash
npm install playwright
node scripts/render_report_pdf.js ~/.codex/reports/<report-id>.html ~/.codex/reports/<report-id>.pdf
```

## Privacy boundary

- Deterministic metrics, log scanning, redaction, manifests, and report rendering run locally.
- The Skill does not upload raw JSONL to the Skill author or an analytics service.
- Rich analysis sends selected and redacted historical summaries through the current Codex model path. Say “只做本地统计” to use deterministic local statistics only.
- Reports use the term “Codex interaction activity estimate”; this does not represent true working hours, project completion, or a complete reconstruction of task boundaries.

## Repository layout

```text
codex-auditor/
├── SKILL.md                 # Chinese Skill entry point
├── SKILL.en.md              # English Skill translation
├── agents/openai.yaml       # Codex agent metadata
├── references/              # Metrics, contracts, and visual guidance
└── scripts/                 # Audit, rich analysis, validators, and tests
```

See [MVP-SPEC.en.md](MVP-SPEC.en.md) for the English public summary and [MVP-SPEC.md](MVP-SPEC.md) for the detailed Chinese specification.

## Current status

This is a public preview. Deterministic auditing, a budgeted rich-analysis flow, local evidence handling, and regression tests are included. External-user repeatability and broader environment compatibility remain release-gate work.
