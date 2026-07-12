# Codex 使用审计器 / Codex Usage Auditor

一个本地优先的 Codex 使用复盘 Skill，用于生成有证据链的 Markdown、HTML、JSON 和本地私密报告。

Local-first Codex usage review Skill with evidence-backed Markdown, HTML, JSON, and private local reports.

| 中文 | English |
|---|---|
| [中文文档](README.zh-CN.md) | [English documentation](README.en.md) |
| [中文 Skill](codex-auditor/SKILL.md) | [English Skill](codex-auditor/SKILL.en.md) |
| [MVP 规格](MVP-SPEC.md) | [MVP public summary](MVP-SPEC.en.md) |

## Quick install

```bash
git clone https://github.com/huangweibin0804-lang/codex-auditor.git /tmp/codex-auditor-repo
mkdir -p ~/.codex/skills
cp -R /tmp/codex-auditor-repo/codex-auditor ~/.codex/skills/codex-auditor
```

Choose `SKILL.md` for Chinese or replace it with `SKILL.en.md` for the English version. See the language-specific README for privacy boundaries, local commands, and validation steps.

## Status

This repository is a public preview. Deterministic audit, bounded rich-analysis flow, local evidence handling, and regression tests are included. External-user repeatability and broader compatibility remain release-gate work.
