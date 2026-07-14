# Codex Optimization Audit Assistant

A local-first Codex workflow review Skill for sessions, projects, rework patterns, and next-cycle actions, with evidence-backed local reports.

## Simplest install: send the GitHub link to Codex

Start a new Codex task and send this entire prompt:

```text
Install this Codex Skill and place it in my local Skills directory:
https://github.com/huangweibin0804-lang/Codex-Optimization-Audit-Assistant
```

This path is intended for Codex clients that can read a GitHub repository and write to the local Skills directory. If the client asks for a path or permission confirmation, approve it when appropriate.

After installation, send:

```text
Use the Codex Optimization Audit Assistant to review my recent 7 days of Codex activity.
```

## What it does

- reviews 1, 7, 30, or custom periods up to 90 days;
- separates observable facts, deterministic activity estimates, rule-based findings, and actions;
- identifies projects, rework, added constraints, failures, and scope redirections;
- uses one state-guided entry point for clustering, deep review, validation, and fallback;
- leads with one core judgment, main focus areas, the largest friction point, and three actions;
- produces local HTML, Markdown, JSON, evidence manifests, and optional PDF;
- uses selected and redacted summaries for rich analysis while preserving evidence and version metadata.

## Privacy boundary

- Raw Codex JSONL, complete tool output, complete file content, credentials, and contact details stay out of rich-analysis input.
- Deterministic scanning, redaction, manifests, and report rendering run locally.
- Rich-analysis summaries enter the current Codex model path; say “只做本地统计” for deterministic local statistics only.
- “Codex interaction activity estimate” does not represent true working hours or project completion.

## Manual setup

When the current client cannot install automatically:

```bash
git clone https://github.com/huangweibin0804-lang/Codex-Optimization-Audit-Assistant.git /tmp/codex-auditor-repo
python3 /tmp/codex-auditor-repo/codex-auditor/scripts/install_skill.py install
```

The default entry point is the Chinese Skill. For English, copy `SKILL.en.md` to `SKILL.md` in the installed directory.

## Local run and tests

```bash
cd codex-auditor
python3 scripts/run_audit.py start --mode local --days 7
python3 scripts/test_auditor.py
python3 scripts/test_rich_audit.py
python3 scripts/test_workflow.py
python3 scripts/validate_skill_security.py .
```

Check and update the installed copy after pulling repository changes:

```bash
python3 scripts/install_skill.py check
python3 scripts/install_skill.py install --force
```

## Report reading order

The first screen is a review verdict card with one core judgment, the main focus areas, the largest friction point, and analysis status. Three prioritized actions follow immediately. Detailed activity charts remain available in a collapsed section.

PDF rendering is optional and requires Playwright:

```bash
npm install playwright
node scripts/render_report_pdf.js ~/.codex/reports/<report-id>.html ~/.codex/reports/<report-id>.pdf
```

## Current status

This is a public preview. Deterministic auditing, a budgeted rich-analysis flow, local evidence handling, and automated tests are included. External-user repeatability and broader environment compatibility remain release-gate work.

## License

No license has been specified yet. Confirm the intended permission scope before modifying, redistributing, or using this project commercially.
