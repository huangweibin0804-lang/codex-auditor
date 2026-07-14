# Codex Auditor Skill Package

One-line install prompt for Codex:

```text
Install this Codex Skill and place it in my local Skills directory:
https://github.com/huangweibin0804-lang/Codex-Optimization-Audit-Assistant
```

Manual install or update from a cloned repository:

```bash
python3 codex-auditor/scripts/install_skill.py install --force
```

| 中文 | English |
|---|---|
| [SKILL.md](SKILL.md) | [SKILL.en.md](SKILL.en.md) |
| [中文指标定义](references/metric-definitions.md) | [English metric definitions](references/metric-definitions.en.md) |
| [中文输入契约](references/semantic-audit-input.md) | [English input contract](references/semantic-audit-input.en.md) |
| [中文输出契约](references/semantic-audit-output.md) | [English output contract](references/semantic-audit-output.en.md) |

The runtime scripts are language-neutral. `scripts/run_audit.py` is the state-guided entry point, and `scripts/install_skill.py check` detects drift between this package and the installed copy. Keep the selected language file as `SKILL.md` in the installed Skill directory.
