#!/usr/bin/env python3
from __future__ import annotations
import datetime as dt
import importlib.util
import json
import stat
import sys
import tempfile
from pathlib import Path

MODULE = Path(__file__).with_name("audit_codex_usage.py")
spec = importlib.util.spec_from_file_location("auditor", MODULE)
auditor = importlib.util.module_from_spec(spec)
assert spec and spec.loader
sys.modules[spec.name] = auditor
spec.loader.exec_module(auditor)

VERIFY_MODULE = Path(__file__).with_name("verify_manifest.py")
verify_spec = importlib.util.spec_from_file_location("manifest_verifier", VERIFY_MODULE)
verifier = importlib.util.module_from_spec(verify_spec)
assert verify_spec and verify_spec.loader
sys.modules[verify_spec.name] = verifier
verify_spec.loader.exec_module(verifier)

def line(stamp, kind, payload):
    return json.dumps({"timestamp": stamp, "type": kind, "payload": payload}, ensure_ascii=False)

def main():
    assert auditor.alias_suffix(0) == "A"
    assert auditor.alias_suffix(25) == "Z"
    assert auditor.alias_suffix(26) == "AA"
    assert auditor.clean_user_message("<appshot>noise</appshot>\n## My request for Codex:\n真正请求") == "真正请求"
    assert "fdOAYPESkaBZne18muSiVcErBVUQfioT" not in auditor.redact("secret fdOAYPESkaBZne18muSiVcErBVUQfioT", 500)
    assert "oc_06da37ede8954b6bf77c0176ca6ac02f" not in auditor.redact("chat oc_06da37ede8954b6bf77c0176ca6ac02f", 500)
    with tempfile.TemporaryDirectory() as temp:
        root, out = Path(temp) / "input", Path(temp) / "out"
        source = root / "sessions" / "case.jsonl"
        source.parent.mkdir(parents=True)
        rows = [
            line("2026-07-01T00:00:00Z", "session_meta", {"id": "session-1", "cwd": "/private/project"}),
            line("2026-07-01T00:00:01Z", "event_msg", {"type": "user_message", "message": "完成这个需求"}),
            line("2026-07-01T00:00:02Z", "event_msg", {"type": "task_complete", "completed_at": 1782864002}),
            line("2026-07-01T00:01:00Z", "event_msg", {"type": "user_message", "message": "补充一个条件"}),
            line("2026-07-01T00:07:00Z", "event_msg", {"type": "user_message", "message": "新任务：写测试"}),
            line("2026-07-01T00:07:30Z", "event_msg", {"type": "task_complete", "completed_at": 1782864450}),
            line("2026-07-01T00:08:00Z", "event_msg", {"type": "user_message", "message": "接下来做：整理文档"}),
            line("2026-07-01T00:20:00Z", "event_msg", {"type": "user_message", "message": "接下来做：没有回合结束"}),
            line("2026-07-01T00:21:00Z", "event_msg", {"type": "task_complete", "completed_at": True}),
            line("not-a-date", "event_msg", {"type": "task_complete", "completed_at": "bad"}),
        ]
        source.write_text("\n".join(rows) + "\n", encoding="utf-8")
        gaps = auditor.Counter()
        events = auditor.load_events(root, gaps)
        auditor.assign_aliases(events)
        auditor.assign_chains(events)
        users = [event for event in events if event.kind == "USER_MESSAGE"]
        assert users[0].chain_id == users[1].chain_id, "constraint addition must stay in chain"
        assert users[2].chain_id != users[1].chain_id, "strong marker must hard split"
        assert users[3].chain_id != users[2].chain_id, "weak marker with task complete must split"
        assert users[4].chain_id == users[3].chain_id, "weak marker without task complete must stay"
        assert gaps["invalid_task_complete_timestamp"] == 1
        start = dt.datetime(2026, 7, 1, tzinfo=dt.timezone.utc)
        paths = auditor.run(root, out, start, start + dt.timedelta(days=1))
        manifest = json.loads(paths["manifest"].read_text(encoding="utf-8"))
        assert manifest and all("source_line_sha256" in item and "source_jsonl_sha256" in item for item in manifest)
        assert verifier.verify(paths["manifest"], root) == []
        source.write_text(source.read_text(encoding="utf-8") + "\n", encoding="utf-8")
        assert any("source_changed" in error for error in verifier.verify(paths["manifest"], root))
        report_html = paths["html"].read_text(encoding="utf-8")
        assert "/private/project" not in report_html
        assert "--color-ink:#0a1217" in report_html
        assert "--color-lime:#cdfe00" in report_html
        assert "--radius-card:24px" in report_html
        assert "@theme" in report_html
        assert "https://" not in report_html and "http://" not in report_html
        assert "下载 PDF" in report_html
        assert ".pdf" in report_html
        assert "mins" in report_html
        assert "统计口径" not in report_html
        assert "证据" not in report_html
        assert "本地私密报告" in report_html
        assert report_html.index("这段时间的复盘") < report_html.index("数据概览")
        assert all(label in report_html for label in ("你主要在做什么", "你最明显的进展是什么", "最需要先解决什么"))
        assert "conclusion-panel" in report_html
        assert "margin-ornaments" in report_html and "margin-network" in report_html and "margin-mark" in report_html
        assert "openai-blossom" in report_html and "<ellipse" not in report_html
        assert "network-line" in report_html and "network-cursor" in report_html
        assert "margin-route" not in report_html and "margin-dot-field" not in report_html and "radial-gradient" not in report_html
        assert "@media(max-width:1360px){.margin-ornaments{display:none}}" in report_html
        assert report_html.index("接下来只做这三件事") < report_html.index("展开查看活动数据")
        assert "scan-line" not in report_html and "prefers-reduced-motion" in report_html
        assert "activity-details" in report_html
        assert stat.S_IMODE(out.stat().st_mode) == 0o700
        assert all(stat.S_IMODE(path.stat().st_mode) == 0o600 for path in paths.values())
        report_json = json.loads(paths["json"].read_text(encoding="utf-8"))
        assert report_json["data_quality"]["structural_parse_rate"] >= 0.9
        metadata = report_json["analysis_metadata"]
        assert metadata["semantic_input_schema_version"] == "semantic_audit_input_v2"
        assert metadata["analysis_prompt_version"] == "rich_audit_prompt_v1"
        report_markdown = paths["markdown"].read_text(encoding="utf-8")
        assert report_markdown.index("## 三条结论") < report_markdown.index("## 数据概览")
    print("auditor tests passed")

if __name__ == "__main__":
    main()
