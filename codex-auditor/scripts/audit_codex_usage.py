#!/usr/bin/env python3
"""Offline Codex JSONL auditor. Uses only the Python standard library."""
from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import html
import json
import math
import os
import re
import sys
from collections import Counter, defaultdict
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Iterable
import unicodedata

ADAPTER_VERSION = "codex_jsonl_adapter_v1"
CHAIN_VERSION = "user_message_chain_v2"
SCENARIO_CLASSIFIER_VERSION = "scenario_classifier_v1"
ACTION_RULES_VERSION = "action_rules_v1"
SEMANTIC_INPUT_SCHEMA_VERSION = "semantic_audit_input_v2"
SEMANTIC_OUTPUT_SCHEMA_VERSION = "semantic_audit_output_v1"
ANALYSIS_PROMPT_VERSION = "rich_audit_prompt_v1"
UTC = dt.timezone.utc
LOCAL_TZ = dt.datetime.now().astimezone().tzinfo or UTC
STRONG_MARKERS = ("新任务", "另一个任务", "换个任务", "换个话题", "现在开始新")
WEAK_MARKER = "接下来做"
CONSTRAINT_MARKERS = ("补充", "必须", "禁止", "不要", "范围", "限制", "验收", "输出格式")
REDIRECT_MARKERS = ("改成", "改为", "换成", "重做", "先别", "停止", "不对")
SECRET_RE = re.compile(r"(?i)\b(?:sk|api[_-]?key|token|secret|password)[_-]?[a-z0-9]{8,}\b")
SENSITIVE_ID_RE = re.compile(r"\b(?:oc|ou|on|cli)_[A-Za-z0-9]{12,}\b")
HIGH_ENTROPY_RE = re.compile(r"\b(?=[A-Za-z0-9]{24,}\b)(?=[A-Za-z0-9]*[a-z])(?=[A-Za-z0-9]*[A-Z])(?=[A-Za-z0-9]*\d)[A-Za-z0-9]{24,}\b")
EMAIL_RE = re.compile(r"\b[^\s@]+@[^\s@]+\.[^\s@]+\b")
PHONE_RE = re.compile(r"(?<!\d)(?:\+?86[- ]?)?1\d{10}(?!\d)")
PATH_RE = re.compile(r"(?:(?:[A-Za-z]:)?/[^\s'\"`<>]{2,}|~/(?:[^\s'\"`<>]+))")
KNOWN_IGNORED_TOP_TYPES = {"session_meta", "turn_context", "world_state", "compacted", "inter_agent_communication_metadata", "response.done"}
KNOWN_IGNORED_EVENT_TYPES = {"token_count", "agent_reasoning", "task_started", "web_search_end", "thread_settings_applied", "turn_aborted", "context_compacted", "sub_agent_activity", "thread_rolled_back", "item_completed", "thread_goal_updated"}
KNOWN_IGNORED_RESPONSE_TYPES = {"reasoning", "function_call", "custom_tool_call", "web_search_call", "image_generation_call", "tool_search_call", "tool_search_output", "agent_message"}


@dataclass
class Event:
    kind: str
    timestamp: dt.datetime | None
    session_id: str
    workspace: str
    source_path: Path
    relative_source_path: str
    line_number: int
    line_bytes: bytes
    text: str = ""
    status: str = ""
    paths: tuple[str, ...] = ()
    chain_id: str = ""


def sha(value: bytes | str, length: int | None = None) -> str:
    raw = value.encode("utf-8") if isinstance(value, str) else value
    value = hashlib.sha256(raw).hexdigest()
    return value[:length] if length else value


def parse_iso(value: Any) -> dt.datetime | None:
    if not isinstance(value, str):
        return None
    try:
        parsed = dt.datetime.fromisoformat(value.replace("Z", "+00:00"))
        return parsed.replace(tzinfo=UTC) if parsed.tzinfo is None else parsed.astimezone(UTC)
    except ValueError:
        return None


def normalize(value: str) -> str:
    value = unicodedata.normalize("NFKC", value).replace("\u200b", "")
    return " ".join(value.split()).strip()


def clean_user_message(value: str) -> str:
    """Remove injected app/file context when an explicit user-request marker exists."""
    for marker in ("## My request for Codex:", "## My request for Codex：", "My request for Codex:"):
        if marker in value:
            value = value.rsplit(marker, 1)[1]
            break
    return value.strip()


def redact(value: str, limit: int = 240) -> str:
    value = SENSITIVE_ID_RE.sub("[敏感标识]", value)
    value = HIGH_ENTROPY_RE.sub("[高熵凭据]", value)
    value = PATH_RE.sub("[路径]", value)
    value = EMAIL_RE.sub("[邮箱]", value)
    value = PHONE_RE.sub("[电话]", value)
    value = SECRET_RE.sub("[敏感凭据]", value)
    return value[:limit]


def marker_at_start(text: str, marker: str) -> bool:
    text = normalize(text)
    if not text.startswith(marker):
        return False
    return len(text) == len(marker) or text[len(marker)] in " ：:,，。"


def bounded_task_complete_time(payload: dict[str, Any], fallback: dt.datetime | None) -> tuple[dt.datetime | None, bool]:
    value = payload.get("completed_at")
    if isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value) and 946684800 <= value <= 4102444800:
        return dt.datetime.fromtimestamp(value, tz=UTC), True
    return fallback, fallback is not None


def extract_text(content: Any) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "\n".join(str(item.get("text", "")) for item in content if isinstance(item, dict))
    return ""


def structured_paths(value: Any, key: str = "") -> list[str]:
    found: list[str] = []
    if isinstance(value, dict):
        for k, v in value.items():
            found.extend(structured_paths(v, k))
    elif isinstance(value, list):
        for item in value:
            found.extend(structured_paths(item, key))
    elif key.lower() in {"path", "file_path", "output_path", "saved_path"} and isinstance(value, str):
        found.append(value)
    return found


def event_from_line(obj: dict[str, Any], source: Path, relative: str, line_number: int, raw: bytes, session_id: str, cwd: str) -> Event | None:
    top_type, payload = obj.get("type"), obj.get("payload")
    if not isinstance(payload, dict):
        return None
    stamp = parse_iso(obj.get("timestamp"))
    kind = ""
    text = ""
    status = str(payload.get("status", ""))
    if top_type == "event_msg":
        ptype = payload.get("type")
        if ptype == "user_message":
            kind, text = "USER_MESSAGE", clean_user_message(str(payload.get("message", "")))
        elif ptype == "agent_message":
            kind, text = "AGENT_MESSAGE", str(payload.get("message", ""))
        elif ptype == "task_complete":
            kind = "TASK_COMPLETE"
            stamp, valid = bounded_task_complete_time(payload, stamp)
            if not valid:
                status = "invalid_task_complete_timestamp"
        elif ptype == "patch_apply_end":
            kind = "PATCH_RESULT"
            status = "success" if payload.get("success") is True else "failed" if payload.get("success") is False else status
        elif ptype == "image_generation_end":
            kind = "IMAGE_RESULT"
        elif ptype == "mcp_tool_call_end":
            kind = "MCP_TOOL_RESULT"
    elif top_type == "response_item":
        ptype = payload.get("type")
        if ptype == "message" and payload.get("role") == "assistant":
            kind, text = "ASSISTANT_MESSAGE_FALLBACK", extract_text(payload.get("content"))
        elif ptype == "function_call_output":
            kind = "FUNCTION_RESULT"
        elif ptype == "custom_tool_call_output":
            kind = "CUSTOM_TOOL_RESULT"
    if not kind:
        return None
    return Event(kind, stamp, session_id, cwd, source, relative, line_number, raw, text, status, tuple(structured_paths(payload)))


def find_jsonl_files(input_root: Path) -> list[Path]:
    roots = [input_root / "sessions", input_root / "archived_sessions"]
    return sorted(path for root in roots if root.is_dir() for path in root.rglob("*.jsonl") if path.is_file())


def known_ignored_record(obj: dict[str, Any]) -> bool:
    top_type = obj.get("type")
    payload = obj.get("payload")
    payload_type = payload.get("type") if isinstance(payload, dict) else None
    if top_type in KNOWN_IGNORED_TOP_TYPES:
        return True
    if top_type == "event_msg" and payload_type in KNOWN_IGNORED_EVENT_TYPES:
        return True
    if top_type == "response_item":
        if payload_type in KNOWN_IGNORED_RESPONSE_TYPES:
            return True
        if payload_type == "message" and isinstance(payload, dict) and payload.get("role") != "assistant":
            return True
    return False


def load_events(input_root: Path, gaps: Counter[str]) -> list[Event]:
    events: list[Event] = []
    for source in find_jsonl_files(input_root):
        session_id, cwd = sha(str(source), 16), ""
        try:
            lines = source.read_bytes().splitlines()
        except OSError:
            gaps["unreadable_jsonl"] += 1
            continue
        relative = str(source.relative_to(input_root))
        for number, raw in enumerate(lines, 1):
            gaps["_records_total"] += 1
            try:
                obj = json.loads(raw)
            except json.JSONDecodeError:
                gaps["invalid_jsonl_line"] += 1
                gaps["_records_unknown"] += 1
                continue
            if obj.get("type") == "session_meta" and isinstance(obj.get("payload"), dict):
                payload = obj["payload"]
                session_id = str(payload.get("id") or payload.get("session_id") or session_id)
                cwd = str(payload.get("cwd") or cwd)
            event = event_from_line(obj, source, relative, number, raw, session_id, cwd)
            if event is None:
                if known_ignored_record(obj):
                    gaps["_records_known_ignored"] += 1
                else:
                    payload = obj.get("payload")
                    payload_type = payload.get("type") if isinstance(payload, dict) else "-"
                    gaps[f"unknown_{obj.get('type', 'missing_type')}.{payload_type}"] += 1
                    gaps["_records_unknown"] += 1
                continue
            gaps["_records_adapted"] += 1
            if event.status == "invalid_task_complete_timestamp":
                gaps[event.status] += 1
            if event.timestamp is None:
                gaps["missing_event_timestamp"] += 1
                continue
            events.append(event)
    return filter_fallbacks(events, gaps)


def filter_fallbacks(events: list[Event], gaps: Counter[str]) -> list[Event]:
    by_session: dict[str, list[Event]] = defaultdict(list)
    for event in events:
        by_session[event.session_id].append(event)
    output: list[Event] = []
    for session_events in by_session.values():
        agents = [event for event in session_events if event.kind == "AGENT_MESSAGE"]
        fingerprints = {(event.timestamp, sha(normalize(event.text))) for event in agents}
        for event in session_events:
            if event.kind != "ASSISTANT_MESSAGE_FALLBACK":
                output.append(event)
            elif not agents:
                output.append(event)
            elif any(abs((event.timestamp - time).total_seconds()) <= 2 and sha(normalize(event.text)) == digest for time, digest in fingerprints):
                continue
            else:
                gaps["unpaired_assistant_message"] += 1
    return output


def assign_aliases(events: list[Event]) -> dict[str, str]:
    keys = sorted({normalize(event.workspace) or "未声明工作区" for event in events})
    aliases = {key: f"工作区 {alias_suffix(index)}" for index, key in enumerate(keys)}
    for event in events:
        event.workspace = aliases[normalize(event.workspace) or "未声明工作区"]
    return aliases


def alias_suffix(index: int) -> str:
    """Excel-style deterministic labels: A..Z, AA..AZ, BA..."""
    value, chars = index + 1, []
    while value:
        value, remainder = divmod(value - 1, 26)
        chars.append(chr(65 + remainder))
    return "".join(reversed(chars))


def assign_chains(events: list[Event]) -> None:
    sessions: dict[str, list[Event]] = defaultdict(list)
    for event in events:
        sessions[event.session_id].append(event)
    for session, items in sessions.items():
        items.sort(key=lambda item: item.timestamp or dt.datetime.min.replace(tzinfo=UTC))
        chain = 0
        active_chain = ""
        last_user: Event | None = None
        task_times: list[dt.datetime] = []
        for event in items:
            if event.kind == "TASK_COMPLETE" and event.status != "invalid_task_complete_timestamp" and event.timestamp:
                task_times.append(event.timestamp)
            if event.kind != "USER_MESSAGE" or not event.timestamp:
                event.chain_id = active_chain
                continue
            hard = last_user is None or (event.timestamp - last_user.timestamp).total_seconds() > 1800 or any(marker_at_start(event.text, marker) for marker in STRONG_MARKERS)
            soft = marker_at_start(event.text, WEAK_MARKER) and any(0 <= (event.timestamp - when).total_seconds() <= 300 for when in task_times)
            if hard or soft:
                chain += 1
            active_chain = f"{sha(session, 12)}:{chain}"
            event.chain_id = active_chain
            last_user = event


def selected(events: Iterable[Event], start: dt.datetime, end: dt.datetime) -> list[Event]:
    return [event for event in events if event.timestamp and start <= event.timestamp <= end]


def interval_metrics(events: list[Event]) -> dict[str, int]:
    anchors = [event for event in events if event.kind in {"USER_MESSAGE", "AGENT_MESSAGE", "ASSISTANT_MESSAGE_FALLBACK"}]
    minute_keys = {(event.timestamp.astimezone().year, event.timestamp.astimezone().month, event.timestamp.astimezone().day, event.timestamp.astimezone().hour, event.timestamp.astimezone().minute) for event in anchors}
    sessions: dict[str, list[Event]] = defaultdict(list)
    for event in anchors:
        sessions[event.session_id].append(event)
    intervals: list[tuple[dt.datetime, dt.datetime]] = []
    for session_anchors in sessions.values():
        session_anchors.sort(key=lambda event: event.timestamp)
        for index, event in enumerate(session_anchors[:-1]):
            nxt = session_anchors[index + 1]
            seconds = (nxt.timestamp - event.timestamp).total_seconds()
            if event.kind == "USER_MESSAGE" and nxt.kind != "USER_MESSAGE":
                intervals.append((event.timestamp, event.timestamp + dt.timedelta(seconds=min(max(seconds, 0), 120))))
            elif event.kind != "USER_MESSAGE" and nxt.kind == "USER_MESSAGE":
                intervals.append((event.timestamp, event.timestamp + dt.timedelta(seconds=min(max(seconds, 0), 900))))
    merged: list[list[dt.datetime]] = []
    for start, end in sorted(intervals):
        if not merged or start > merged[-1][1]:
            merged.append([start, end])
        elif end > merged[-1][1]:
            merged[-1][1] = end
    union_seconds = sum((end - start).total_seconds() for start, end in merged)
    gross_seconds = sum((end - start).total_seconds() for start, end in intervals)
    return {"event_coverage_minutes": len(minute_keys), "interaction_interval_estimate_minutes": round(union_seconds / 60), "overlap_minutes": max(0, round((gross_seconds - union_seconds) / 60))}


def scenario(events: list[Event]) -> tuple[str, str]:
    # User requests are the least contaminated task signal. Agent boilerplate may
    # mention security or configuration even when that is not the user's task.
    source_events = [event for event in events if event.kind == "USER_MESSAGE"] or events
    text = " ".join(normalize(event.text) for event in source_events).lower()
    rules = [("环境配置与排障", ("安装", "认证", "权限", "网络", "配置", "错误")), ("设计与可视化", ("图片", "画布", "图表", "演示")), ("开发与调试", ("patch", "测试", "git", "build", ".py", ".ts")), ("研究与检索", ("搜索", "网页", "资料")), ("写作与内容生产", ("脚本", "改写", "翻译", "文章", "文案")), ("文件与知识管理", ("文档", "表格", "obsidian", "归档")), ("运营协作", ("飞书", "邮件", "日历", "外联"))]
    name, hits = "其他/待确认", 0
    for candidate, tokens in rules:
        score = sum(token in text for token in tokens)
        if score > hits:
            name, hits = candidate, score
    return name, "HIGH" if hits >= 3 else "MEDIUM" if hits >= 1 else "LOW"


def action_findings(events: list[Event]) -> list[dict[str, Any]]:
    findings: list[dict[str, Any]] = []
    chains: dict[str, list[Event]] = defaultdict(list)
    for event in events:
        if event.chain_id:
            chains[event.chain_id].append(event)
    for rule, markers, threshold, action, impact in [("R1_CONSTRAINT_ADDITION", CONSTRAINT_MARKERS, 3, "复杂任务首条消息写清目标、输入、范围、约束、验收。", 2), ("R4_SCOPE_REDIRECTION", REDIRECT_MARKERS, 2, "高成本执行前先确认交付边界。", 2)]:
        matches: list[Event] = []
        for chain in chains.values():
            chain.sort(key=lambda event: event.timestamp)
            baseline_seen, agent_seen, hits = False, False, []
            for event in chain:
                if event.kind == "AGENT_MESSAGE" or event.kind == "ASSISTANT_MESSAGE_FALLBACK":
                    agent_seen = True
                elif event.kind == "USER_MESSAGE":
                    if baseline_seen and agent_seen and any(marker in normalize(event.text) for marker in markers):
                        hits.append(event)
                    baseline_seen = True
            if len(hits) >= threshold:
                matches.extend(hits)
        if matches:
            findings.append(make_finding(rule, matches, impact, 2, action, "复杂任务开始时", "下次同类规则证据应减少。"))
    paths: dict[tuple[str, str], list[Event]] = defaultdict(list)
    for event in events:
        for path in event.paths:
            paths[(event.workspace, normalize(path))].append(event)
    repeated = [event for values in paths.values() for event in values if len(values) >= 3 and (max(item.timestamp for item in values) - min(item.timestamp for item in values)).total_seconds() <= 1800]
    if repeated:
        findings.append(make_finding("R2_REPEAT_TYPED_PATH", repeated, 2, 3, "先确认现状，再集中修改与验证。", "开始修改同一文件前", "短窗重复路径事件应减少。"))
    results = [event for event in events if event.kind in {"PATCH_RESULT", "FUNCTION_RESULT", "CUSTOM_TOOL_RESULT"} and event.status]
    failed = [event for event in results if event.status.lower() in {"failed", "failure", "error"}]
    if len(failed) >= 3 or (len(results) >= 5 and len(failed) / len(results) >= .2):
        findings.append(make_finding("R3_VERIFIED_FAILURE", failed, 3, 3, "执行前定义最小验证命令和失败处理步骤。", "开始有风险的执行前", "可验证失败率应下降。"))
    return sorted(findings, key=lambda item: (-item["score"], -item["count"], item["latest"], item["rule_id"]))[:3]


def make_finding(rule: str, evidence: list[Event], impact: int, level: int, change: str, trigger: str, signal: str) -> dict[str, Any]:
    ids = [evidence_id(event) for event in evidence]
    return {"rule_id": rule, "count": len(evidence), "score": impact * level + min(len(evidence), 3), "latest": max(event.timestamp for event in evidence).isoformat(), "behavior_change": change, "trigger": trigger, "check_signal": signal, "evidence_ids": ids}


def evidence_id(event: Event) -> str:
    summary = redact(normalize(event.text))
    return "ev_" + sha(f"{event.session_id}|{event.timestamp.isoformat()}|{event.kind}|{summary}", 16)


def manifest(events: list[Event]) -> list[dict[str, Any]]:
    file_hashes: dict[Path, str] = {}
    output = []
    for event in events:
        if event.source_path not in file_hashes:
            file_hashes[event.source_path] = "sha256:" + sha(event.source_path.read_bytes())
        output.append({"evidence_id": evidence_id(event), "adapter_version": ADAPTER_VERSION, "source_session_hash": "s_" + sha(event.session_id, 12), "source_jsonl_relative_path": event.relative_source_path, "source_jsonl_sha256": file_hashes[event.source_path], "source_line_number": event.line_number, "source_line_sha256": "sha256:" + sha(event.line_bytes), "event_timestamp": event.timestamp.astimezone().isoformat(), "event_type": event.kind, "summary_hash": "sha256:" + sha(redact(normalize(event.text)))})
    return output


def claim_items_html(claims: list[dict[str, Any]], categories: set[str] | None = None) -> str:
    selected = [claim for claim in claims if categories is None or claim.get("category") in categories]
    if not selected:
        return "<p class='empty'>信息不足，未生成该维度结论。</p>"
    return "<ul class='claim-list'>" + "".join(
        f"<li><p>{html.escape(str(claim.get('text', '')))}</p></li>"
        for claim in selected
    ) + "</ul>"


def report_html(report: dict[str, Any]) -> str:
    metrics, findings = report["metrics"], report["findings"]
    maximum_hour = max(report["hourly_activity"].values(), default=1) or 1
    maximum_day = max(report["daily_activity"].values(), default=1) or 1
    maximum_scenario = max(report["scenario_distribution"].values(), default=1) or 1
    heatmap = "".join(
        f"<div class='heat-cell' title='{int(hour):02d}:00 · {count} 个活跃分钟'>"
        f"<span class='heat-hour'>{int(hour):02d}</span><span class='heat-track'><i style='height:{count / maximum_hour * 100:.1f}%'></i></span>"
        f"<strong>{count}</strong></div>"
        for hour, count in report["hourly_activity"].items()
    )
    trend = "".join(
        f"<div class='bar-row'><span>{html.escape(day[5:])}</span><div class='bar-track'><i style='width:{count / maximum_day * 100:.1f}%'></i></div><strong>{count} mins</strong></div>"
        for day, count in report["daily_activity"].items()
    ) or "<p class='empty'>无时间锚点。</p>"
    scenarios = "".join(
        f"<div class='scenario-row'><div><strong>{html.escape(name)}</strong><span>{count} 个工作区</span></div>"
        f"<div class='scenario-track'><i style='width:{count / maximum_scenario * 100:.1f}%'></i></div></div>"
        for name, count in sorted(report["scenario_distribution"].items(), key=lambda item: (-item[1], item[0]))
    ) or "<p class='empty'>其他/待确认：0</p>"
    workspaces = "".join(
        f"<article class='project-card'><div class='project-index'>{index:02d}</div><div class='project-copy'>"
        f"<div class='eyebrow'>{html.escape(item['scenario'])} · {html.escape(item['confidence'])}</div>"
        f"<h3>{html.escape(item['alias'])}</h3><p>{html.escape(item['time_range'])}</p>"
        f"<div class='project-stats'><span><b>{item['event_count']}</b> 可定位事件</span>"
        f"<span><b>{item['confirmed_artifact_events']}</b> 已确认产物事件</span></div></div></article>"
        for index, item in enumerate(report["workspaces"][:3], 1)
    ) or "<p class='empty'>无可定位工作区。</p>"
    actions = "".join(
        f"<article class='action-card'><div class='action-number'>{index}</div><div><div class='action-rule'>{html.escape(item['rule_id'])}</div>"
        f"<h3>{html.escape(item['behavior_change'])}</h3><p><b>触发条件</b> {html.escape(item['trigger'])}</p>"
        f"<p><b>下次检查</b> {html.escape(item['check_signal'])}</p></div></article>"
        for index, item in enumerate(findings, 1)
    ) or "<div class='empty-dark'>本周期没有足够高置信度的信息，未生成行动建议。</div>"
    semantic = report.get("semantic_analysis") if report.get("semantic_status") == "complete" else None
    hero_summary = f"在 {report['workspace_count']} 个工作区中记录了 {report['event_count']} 个可定位事件。本报告将事实、估算与规则发现分层呈现。"
    project_heading = "主要工作区"
    project_note = "工作区仅作为本地分析容器；富分析模式可在此上方归纳跨会话项目主题。"
    project_metric = report["workspace_count"]
    rich_review_section = ""
    patterns_section = ""
    if semantic:
        core = semantic.get("core_judgment", {})
        hero_summary = html.escape(str(core.get("text", hero_summary)))
        project_heading = "项目版图"
        project_note = "项目主题由当前 Codex 模型归纳；每条会话只分配给一个主项目，投入占比不重复计算。"
        assignments = report.get("semantic_projects", [])
        project_metric = len(assignments)
        workspaces = "".join(
            f"<article class='project-card'><div class='project-index'>{index:02d}</div><div class='project-copy'>"
            f"<div class='eyebrow'>模型归纳 · {html.escape(str(item.get('confidence', 'LOW')))}</div>"
            f"<h3>{html.escape(str(item.get('name', '未命名项目')))}</h3><p>{html.escape(str(item.get('summary', '')))}</p>"
            f"<div class='project-stats'><span><b>{item.get('interaction_activity_estimate', 0)}</b> 估算分钟</span>"
            f"<span><b>{item.get('interaction_share', 0) * 100:.1f}%</b> 交互占比</span><span><b>{len(item.get('member_thread_refs', []))}</b> 会话</span></div></div></article>"
            for index, item in enumerate(assignments[:6], 1)
        ) or "<p class='empty'>富分析未形成可验证项目。</p>"
        rich_cards = []
        for project in semantic.get("projects", []):
            claims = project.get("claims", [])
            rich_cards.append(
                f"<article class='review-card'><div class='eyebrow'>深度项目复盘 · {html.escape(str(project.get('confidence', 'LOW')))}</div>"
                f"<h3>{html.escape(str(project.get('name', '未命名项目')))}</h3>"
                f"<div class='review-grid'><section><h4>做了什么</h4>{claim_items_html(claims, {'goal', 'work', 'artifact', 'turning_point'})}</section>"
                f"<section><h4>卡点是什么</h4>{claim_items_html(claims, {'problem', 'prompt_review'})}</section>"
                f"<section><h4>优化方案</h4>{claim_items_html(claims, {'next_step'})}</section></div></article>"
            )
        rich_review_section = "<section class='shell section' id='reviews'><div class='section-head'><div><div class='eyebrow'>Project review</div><h2>主要项目复盘</h2></div><p class='section-note'>当前相关文件用于辅助理解项目背景，不等同本周期确认产物。</p></div><div class='review-list'>" + "".join(rich_cards) + "</div></section>"
        patterns = semantic.get("work_patterns", {})
        patterns_section = (
            "<section class='shell section' id='patterns'><div class='section-head'><div><div class='eyebrow'>Work patterns</div><h2>工作模式</h2></div></div>"
            "<div class='analytics-grid'><article class='panel'><h3>主要问题</h3>" + claim_items_html(patterns.get("problems", [])) + "</article>"
            "<article class='panel'><h3>本周期进步</h3>" + claim_items_html(patterns.get("progress", [])) + "</article>"
            "<article class='panel'><h3>稳定优势</h3>" + claim_items_html(patterns.get("stable_strengths", [])) + "</article>"
            "<article class='panel'><h3>可复用工作流</h3>" + claim_items_html(patterns.get("reusable_workflows", [])) + "</article></div></section>"
        )
        semantic_actions = semantic.get("actions", [])
        actions = "".join(
            f"<article class='action-card'><div class='action-number'>{index}</div><div><div class='action-rule'>建议 · 模型归纳</div>"
            f"<h3>{html.escape(str(item.get('behavior_change', '')))}</h3><p><b>触发条件</b> {html.escape(str(item.get('trigger_condition', '')))}</p>"
            f"<p><b>下次检查</b> {html.escape(str(item.get('next_review_signal', '')))}</p></div></article>"
            for index, item in enumerate(semantic_actions, 1)
        ) or actions
    gaps = html.escape(json.dumps(report["data_gaps"], ensure_ascii=False, indent=2))
    quality = report.get("data_quality", {})
    analysis_versions = html.escape(json.dumps(report.get("analysis_metadata", {}), ensure_ascii=False, indent=2))
    report_id = html.escape(report["report_id"])
    theme_css = """
    @theme {
      --color-ink: #0a1217; --color-paper: #ffffff; --color-frost: #e4eff7;
      --color-stone: #85898b; --color-obsidian: #000000; --color-lime: #cdfe00;
      --radius-card: 24px; --radius-pill: 9999px; --spacing-section: 64px;
    }
    :root {
      --color-ink:#0a1217; --color-paper:#ffffff; --color-frost:#e4eff7;
      --color-stone:#85898b; --color-obsidian:#000000; --color-lime:#cdfe00;
      --font-display:"Iowan Old Style","Baskerville","Times New Roman",serif;
      --font-sans:-apple-system,BlinkMacSystemFont,"Segoe UI","PingFang SC","Hiragino Sans GB","Microsoft YaHei",sans-serif;
      --text-caption:13px; --text-body:16px; --text-heading:20px; --text-display:72px;
      --radius-card:24px; --radius-pill:9999px; --space-1:8px; --space-2:16px;
      --space-3:24px; --space-4:32px; --space-section:64px; --page-max:1200px; --control-height:44px;
    }
    *{box-sizing:border-box} html{scroll-behavior:smooth} body{margin:0;background:var(--color-paper);color:var(--color-ink);font-family:var(--font-sans);font-size:var(--text-body);line-height:1.5;font-feature-settings:"lnum" 1,"tnum" 1} a{color:inherit;text-decoration:none} button{font:inherit}
    .shell{width:min(calc(100% - 40px),var(--page-max));margin:0 auto}.topbar{min-height:80px;display:grid;grid-template-columns:1fr auto 1fr;align-items:center;gap:24px}.brand{font-weight:500}.nav{display:flex;gap:24px;font-size:14px;font-weight:450}.top-actions{display:flex;justify-content:flex-end;gap:8px}.pill{height:var(--control-height);display:inline-flex;align-items:center;justify-content:center;padding:0 18px;border:1px solid var(--color-ink);border-radius:var(--radius-pill);background:transparent;font-size:14px;font-weight:450;cursor:pointer}.pill-primary{background:var(--color-ink);color:var(--color-paper)}
    .hero{padding:88px 0 72px;text-align:center}.hero-kicker{font-size:13px;color:var(--color-stone);margin:0 0 20px}.hero h1{max-width:980px;margin:0 auto;font-family:var(--font-display);font-size:clamp(54px,7vw,var(--text-display));font-weight:400;line-height:1.1;letter-spacing:-.03em}.hero-summary{max-width:680px;margin:28px auto 0;font-size:16px}.metrics{display:grid;grid-template-columns:repeat(5,1fr);gap:16px}.metric{min-height:176px;padding:24px;border-radius:var(--radius-card);background:var(--color-frost);display:flex;flex-direction:column;justify-content:space-between}.metric:nth-child(2){background:var(--color-ink);color:var(--color-paper)}.metric-label{font-size:13px;color:var(--color-stone)}.metric:nth-child(2) .metric-label{color:#b7bdc1}.metric strong{font-family:var(--font-display);font-size:46px;font-weight:400;line-height:1}.metric small{font-size:13px;color:var(--color-stone)}
    .section{padding-top:var(--space-section)}.section-head{display:flex;align-items:end;justify-content:space-between;gap:24px;margin-bottom:24px}.eyebrow{font-size:13px;color:var(--color-stone)}h2{margin:5px 0 0;font-size:32px;line-height:1.15;font-weight:500;letter-spacing:-.02em}.section-note{max-width:470px;margin:0;color:var(--color-stone);font-size:14px}.analytics-grid{display:grid;grid-template-columns:1.15fr .85fr;gap:16px}.panel{border-radius:var(--radius-card);background:var(--color-frost);padding:32px;min-width:0}.panel-dark{background:var(--color-ink);color:var(--color-paper)}.panel h3{margin:0 0 24px;font-size:20px;font-weight:500}.bar-row{display:grid;grid-template-columns:48px 1fr max-content;align-items:center;gap:12px;margin:10px 0;font-size:13px}.bar-track,.scenario-track{height:8px;border-radius:var(--radius-pill);background:var(--color-paper);overflow:hidden}.bar-track i,.scenario-track i{display:block;height:100%;border-radius:inherit;background:var(--color-ink)}.bar-row strong{text-align:right;white-space:nowrap}.scenario-row{margin:18px 0}.scenario-row>div:first-child{display:flex;justify-content:space-between;gap:16px;margin-bottom:8px}.scenario-row span{color:var(--color-stone);font-size:13px}.scenario-method{margin-top:28px;padding-top:24px;border-top:1px solid #cbd8e1}.scenario-panel{grid-column:1 / -1}.hours{display:grid;grid-template-columns:repeat(12,1fr);gap:8px}.heat-cell{min-width:0;text-align:center;font-size:12px}.heat-hour{display:block;color:#b7bdc1}.heat-track{display:block;position:relative;height:76px;margin:8px 0;border-radius:var(--radius-pill);overflow:hidden;background:#253038}.heat-track i{position:absolute;left:0;right:0;bottom:0;background:var(--color-lime);min-height:2px}.heat-cell strong{font-size:11px;font-weight:450}
    .project-list,.review-list{display:grid;gap:16px}.project-card{display:grid;grid-template-columns:88px 1fr;min-height:210px;border-radius:var(--radius-card);overflow:hidden;background:var(--color-frost)}.project-index{display:flex;align-items:flex-end;padding:24px;background:var(--color-ink);color:var(--color-lime);font-family:var(--font-display);font-size:34px}.project-copy{padding:32px}.project-copy h3{margin:6px 0 10px;font-size:24px}.project-copy p{margin:0;color:var(--color-stone)}.project-stats{display:flex;flex-wrap:wrap;gap:24px;margin-top:32px;font-size:14px}.project-stats b{font-size:20px}.review-card{padding:32px;border-radius:var(--radius-card);background:var(--color-frost)}.review-card h3{font-size:28px;margin:8px 0 24px}.review-grid{display:grid;grid-template-columns:1fr;gap:16px}.review-grid section{padding:20px;border-radius:20px;background:var(--color-paper)}.review-grid h4{margin:0 0 16px}.claim-list{list-style:none;padding:0;margin:0}.claim-list li{padding:12px 0;border-top:1px solid #d3dfe7}.claim-list li:first-child{border-top:0}.claim-list p{margin:5px 0}
    .actions{border-radius:var(--radius-card);background:var(--color-ink);color:var(--color-paper);padding:40px}.actions-head{display:flex;justify-content:space-between;align-items:end;gap:24px;margin-bottom:32px}.lime-pill{height:var(--control-height);display:inline-flex;align-items:center;padding:0 18px;border-radius:var(--radius-pill);background:var(--color-lime);color:var(--color-ink);font-size:14px;font-weight:450}.action-list{display:grid;grid-template-columns:repeat(3,1fr);gap:12px}.action-card{padding:24px;border-radius:var(--radius-card);background:rgba(255,255,255,.08)}.action-number{width:36px;height:36px;border-radius:50%;display:grid;place-items:center;background:var(--color-lime);color:var(--color-ink);font-weight:500;margin-bottom:32px}.action-rule{font-size:12px;color:#b7bdc1}.action-card h3{font-size:20px;line-height:1.25;margin:8px 0 24px}.action-card p{font-size:14px;color:#d6dcdf}.action-card p b{display:block;color:var(--color-paper)}.action-card small{font-size:12px;color:#b7bdc1}.empty,.empty-dark{padding:24px;border-radius:var(--radius-card);color:var(--color-stone)}.empty-dark{background:rgba(255,255,255,.08);color:#d6dcdf}
    details{border-radius:var(--radius-card);background:var(--color-frost);padding:24px}summary{cursor:pointer;font-weight:500}pre{white-space:pre-wrap;overflow:auto;margin:20px 0 0;padding:20px;border-radius:16px;background:var(--color-paper);font:12px/1.5 ui-monospace,SFMono-Regular,Menlo,monospace}.method-copy{color:var(--color-stone);font-size:14px}.footer{display:flex;justify-content:space-between;gap:24px;padding:64px 0 32px;font-size:13px;color:var(--color-stone)}
    @media(max-width:900px){.topbar{grid-template-columns:1fr auto}.nav{display:none}.metrics{grid-template-columns:repeat(2,1fr)}.analytics-grid,.review-grid{grid-template-columns:1fr}.action-list{grid-template-columns:1fr}.metric:last-child{grid-column:1/-1}.section-head,.actions-head{align-items:flex-start;flex-direction:column}.hours{grid-template-columns:repeat(8,1fr)}}
    @media(max-width:560px){.shell{width:min(calc(100% - 24px),var(--page-max))}.top-actions .pill:first-child{display:none}.hero{padding:56px 0}.metrics{grid-template-columns:1fr}.metric:last-child{grid-column:auto}.panel,.actions{padding:24px}.hours{grid-template-columns:repeat(6,1fr)}.project-card{grid-template-columns:1fr}.project-index{min-height:72px;align-items:center}.project-stats{flex-direction:column;gap:8px}.footer{flex-direction:column}}
    @media print{.top-actions,.nav{display:none}.panel,.project-card,.review-card,.actions{break-inside:avoid}.review-card{padding:24px}.review-card h3{font-size:24px;margin:6px 0 16px}.review-grid{gap:12px}.review-grid section{padding:16px}.claim-list li{padding:8px 0}.section-head{break-after:avoid}#projects,#reviews,#patterns,#actions{break-before:page}body{print-color-adjust:exact;-webkit-print-color-adjust:exact}}
    """
    return f"""<!doctype html><html lang='zh-CN'><head><meta charset='utf-8'><meta name='viewport' content='width=device-width,initial-scale=1'><meta name='color-scheme' content='light'><title>Codex 周期复盘 · {html.escape(report['range'])}</title><style>{theme_css}</style></head><body>
    <header class='shell topbar'><a class='brand' href='#top'>Codex Review</a><nav class='nav' aria-label='报告导航'><a href='#overview'>总览</a><a href='#activity'>时间</a><a href='#projects'>项目</a><a href='#actions'>行动</a></nav><div class='top-actions'><a class='pill' href='./{report_id}.md'>Markdown</a><a class='pill pill-primary' href='./{report_id}.pdf' download='Codex审查报告-{html.escape(report["range"].replace(" ", "")).replace("至", "-")}.pdf'>下载 PDF</a></div></header>
    <main id='top'><section class='shell hero'><p class='hero-kicker'>{html.escape(report['range'])} · 本地私密报告</p><h1>你的 Codex 周期工作复盘</h1><p class='hero-summary'>{hero_summary}</p></section>
    <section class='shell metrics' id='overview'><article class='metric'><span class='metric-label'>事件覆盖</span><strong>{metrics['event_coverage_minutes']}</strong><small>去重自然分钟</small></article><article class='metric'><span class='metric-label'>交互活动估算</span><strong>{metrics['interaction_interval_estimate_minutes']}</strong><small>分钟 · 非真实工时</small></article><article class='metric'><span class='metric-label'>{'项目' if semantic else '工作区'}</span><strong>{project_metric}</strong><small>{'模型归纳，唯一归属' if semantic else '本地主题聚类'}</small></article><article class='metric'><span class='metric-label'>可定位事件</span><strong>{report['event_count']}</strong><small>本地事件记录</small></article><article class='metric'><span class='metric-label'>已确认产物</span><strong>{report['confirmed_artifact_events']}</strong><small>补丁与明确写入事件</small></article></section>
    <section class='shell section' id='activity'><div class='section-head'><div><div class='eyebrow'>Activity map</div><h2>时间与精力分布</h2></div><p class='section-note'>小时图仅统计可观察消息锚点；工具等待、Token 事件和后台过程不延长活动时间。</p></div><div class='analytics-grid'><article class='panel'><h3>活跃趋势</h3>{trend}</article><article class='panel panel-dark'><h3>24 小时活跃图</h3><div class='hours'>{heatmap}</div></article><article class='panel scenario-panel'><h3>场景分布</h3>{scenarios}<div class='scenario-method'><p class='method-copy'>事件覆盖分钟数和交互间隔估算分钟数均来自本地 JSONL 时间锚点，用于观察 Codex 协作活动，不等同真实工作时长。</p><div class='project-stats'><span><b>{metrics['overlap_minutes']}</b> 并行重叠分钟</span><span><b>{len(report['daily_activity'])}</b> 活跃日</span></div></div></article></div></section>
    <section class='shell section' id='projects'><div class='section-head'><div><div class='eyebrow'>Project map</div><h2>{project_heading}</h2></div><p class='section-note'>{project_note}</p></div><div class='project-list'>{workspaces}</div></section>{rich_review_section}{patterns_section}
    <section class='shell section' id='actions'><div class='actions'><div class='actions-head'><div><div class='eyebrow'>Next cycle</div><h2>下周期行动</h2></div><span class='lime-pill'>最多 3 项 · 可验证</span></div><div class='action-list'>{actions}</div></div></section>
    <section class='shell section'><details><summary>查看数据缺口与隐私说明</summary><p class='method-copy'>未显式标记的新任务可能保留在同一消息链中；相关诊断只表示会话内后续补充或重定向模式。审计详情保存在同目录 manifest 文件中。</p><h3>解析覆盖</h3><p class='method-copy'>已适配或按版本规则明确忽略 {quality.get('recognized_records', 0)} / {quality.get('total_records', 0)} 条记录，结构识别率 {quality.get('structural_parse_rate', 0) * 100:.1f}%。真正未知事件 {quality.get('unknown_records', 0)} 条。</p><h3>数据缺口</h3><pre>{gaps}</pre><h3>分析版本</h3><pre>{analysis_versions}</pre></details></section></main>
    <footer class='shell footer'><span>Codex Review · 本地私密报告</span><span>{html.escape(report['adapter_version'])} · {html.escape(report['chain_version'])}</span></footer></body></html>""".replace("证据", "相关信号").replace("evidence", "").replace("Evidence", "")


def report_markdown(report: dict[str, Any]) -> str:
    lines = ["# Codex 使用审计", "", f"> 本地私密报告 · {report['range']}", "", "## 总览", "", f"- 事件覆盖分钟数：{report['metrics']['event_coverage_minutes']}", f"- Codex 交互活动估算分钟数：{report['metrics']['interaction_interval_estimate_minutes']}", f"- 并行重叠分钟数：{report['metrics']['overlap_minutes']}", f"- 工作区：{report['workspace_count']}", f"- 可定位事件：{report['event_count']}", f"- 已确认产物事件：{report['confirmed_artifact_events']}", "", "## 场景分布", ""]
    lines.extend(f"- {name}：{count}" for name, count in sorted(report["scenario_distribution"].items(), key=lambda item: (-item[1], item[0])))
    lines.extend(["", "## 下周期行动", ""])
    if report["findings"]:
        for item in report["findings"]:
            lines.extend([f"- `{item['rule_id']}`：{item['behavior_change']}", f"  - 触发条件：{item['trigger']}", f"  - 下次检查信号：{item['check_signal']}"])
    else:
        lines.append("- 本周期没有足够高置信度的信息，未生成行动建议。")
    if report.get("semantic_status") == "complete" and report.get("semantic_analysis"):
        semantic = report["semantic_analysis"]
        core = semantic.get("core_judgment", {})
        lines.extend(["", "## 富分析核心判断", "", f"- 推断：{core.get('text', '')}", "", "## 语义项目", ""])
        deep_by_id = {item.get("project_id"): item for item in semantic.get("projects", [])}
        for project in report.get("semantic_projects", []):
            lines.extend([f"### {project.get('name', '未命名项目')}", "", f"- 交互活动估算：{project.get('interaction_activity_estimate', 0)} 分钟", f"- 会话数：{len(project.get('member_thread_refs', []))}"])
            claims = deep_by_id.get(project.get("project_id"), {}).get("claims", [])
            for claim in claims:
                lines.append(f"- {claim.get('text', '')}")
            lines.append("")
        patterns = semantic.get("work_patterns", {})
        lines.extend(["## 工作模式", ""])
        for title, key in (("主要问题", "problems"), ("本周期进步", "progress"), ("稳定优势", "stable_strengths"), ("可复用工作流", "reusable_workflows")):
            lines.append(f"### {title}")
            values = patterns.get(key, [])
            lines.extend(f"- {item.get('text', '')}" for item in values)
            if not values:
                lines.append("- 信息不足，未生成该维度结论。")
            lines.append("")
    quality = report.get("data_quality", {})
    lines.extend(["", "## 口径", "", "- 时间指标是可观察 Codex 协作活动的估算，不等同真实工时。", "- 未显式标记的新任务可能保留在同一消息链中。", f"- JSONL 结构识别率：{quality.get('structural_parse_rate', 0) * 100:.1f}%（未知记录 {quality.get('unknown_records', 0)} 条）。", "- 详细审计信息保存在同目录的本地 manifest 文件中。", "", "## 分析版本", "", "```json", json.dumps(report.get("analysis_metadata", {}), ensure_ascii=False, indent=2), "```"])
    return "\n".join(lines).replace("证据", "相关信号") + "\n"


def semantic_preview(events: list[Event]) -> list[dict[str, Any]]:
    grouped: dict[str, list[Event]] = defaultdict(list)
    for event in events:
        if event.kind == "USER_MESSAGE":
            grouped[event.workspace].append(event)
    result = []
    for workspace in sorted(grouped)[:3]:
        for event in grouped[workspace][:20]:
            result.append({"workspace_alias": workspace, "time": event.timestamp.isoformat(), "request_summary": redact(normalize(event.text), 240), "scenario": scenario([event])[0], "confidence": scenario([event])[1]})
    return result[:20]


def report_dimensions(events: list[Event], workspace_events: dict[str, list[Event]]) -> tuple[dict[str, int], dict[str, int], list[dict[str, Any]]]:
    anchors = [event for event in events if event.kind in {"USER_MESSAGE", "AGENT_MESSAGE", "ASSISTANT_MESSAGE_FALLBACK"}]
    daily_minutes: dict[str, set[tuple[int, int]]] = defaultdict(set)
    hourly_minutes: dict[int, set[tuple[str, int]]] = defaultdict(set)
    for event in anchors:
        local = event.timestamp.astimezone()
        daily_minutes[local.date().isoformat()].add((local.hour, local.minute))
        hourly_minutes[local.hour].add((local.date().isoformat(), local.minute))
    daily = {day: len(minutes) for day, minutes in sorted(daily_minutes.items())}
    hourly = {hour: len(hourly_minutes[hour]) for hour in range(24)}
    summaries = []
    for alias, items in workspace_events.items():
        label, confidence = scenario(items)
        artifacts = sum(event.kind in {"PATCH_RESULT", "IMAGE_RESULT"} and bool(event.paths) for event in items)
        summaries.append({"alias": alias, "scenario": label, "confidence": confidence, "time_range": f"{min(event.timestamp for event in items).astimezone().date()} 至 {max(event.timestamp for event in items).astimezone().date()}", "confirmed_artifact_events": artifacts, "event_count": len(items)})
    return daily, hourly, sorted(summaries, key=lambda item: (-item["event_count"], item["alias"]))


def run(input_root: Path, output_dir: Path, start: dt.datetime, end: dt.datetime, preview: bool = False) -> dict[str, Path]:
    gaps: Counter[str] = Counter()
    all_events = load_events(input_root, gaps)
    assign_aliases(all_events)
    assign_chains(all_events)
    events = selected(all_events, start, end)
    metrics = interval_metrics(events)
    report_id = f"codex-audit-{end.astimezone(LOCAL_TZ).date().isoformat()}-{sha(f'{start.isoformat()}|{end.isoformat()}', 8)}"
    workspace_events: dict[str, list[Event]] = defaultdict(list)
    for event in events:
        workspace_events[event.workspace].append(event)
    scenario_distribution = Counter(scenario(items)[0] for items in workspace_events.values())
    artifacts = [event for event in events if event.kind in {"PATCH_RESULT", "IMAGE_RESULT"} and event.paths]
    daily, hourly, workspaces = report_dimensions(events, workspace_events)
    total_records = gaps.get("_records_total", 0)
    recognized_records = gaps.get("_records_adapted", 0) + gaps.get("_records_known_ignored", 0)
    public_gaps = {key: value for key, value in sorted(gaps.items()) if not key.startswith("_")}
    report = {
        "report_id": report_id,
        "range": f"{start.astimezone(LOCAL_TZ).date().isoformat()} 至 {end.astimezone(LOCAL_TZ).date().isoformat()}",
        "privacy_label": "本地私密报告",
        "adapter_version": ADAPTER_VERSION,
        "chain_version": CHAIN_VERSION,
        "analysis_metadata": {
            "model_id": None,
            "analysis_prompt_version": ANALYSIS_PROMPT_VERSION,
            "semantic_input_schema_version": SEMANTIC_INPUT_SCHEMA_VERSION,
            "semantic_output_schema_version": SEMANTIC_OUTPUT_SCHEMA_VERSION,
            "scenario_classifier_version": SCENARIO_CLASSIFIER_VERSION,
            "action_rules_version": ACTION_RULES_VERSION,
        },
        "metrics": metrics,
        "workspace_count": len(workspace_events),
        "event_count": len(events),
        "confirmed_artifact_events": len(artifacts),
        "daily_activity": daily,
        "hourly_activity": hourly,
        "scenario_distribution": dict(sorted(scenario_distribution.items())),
        "workspaces": workspaces,
        "data_quality": {"total_records": total_records, "adapted_records": gaps.get("_records_adapted", 0), "known_ignored_records": gaps.get("_records_known_ignored", 0), "recognized_records": recognized_records, "unknown_records": gaps.get("_records_unknown", 0), "structural_parse_rate": round(recognized_records / total_records, 4) if total_records else 1.0},
        "data_gaps": public_gaps,
        "findings": action_findings(events),
    }
    secure_directory(output_dir)
    paths = {"json": output_dir / f"{report_id}.json", "markdown": output_dir / f"{report_id}.md", "html": output_dir / f"{report_id}.html", "manifest": output_dir / f"{report_id}.audit-manifest.json"}
    secure_write_text(paths["json"], json.dumps(report, ensure_ascii=False, indent=2) + "\n")
    secure_write_text(paths["markdown"], report_markdown(report))
    secure_write_text(paths["html"], report_html(report))
    secure_write_text(paths["manifest"], json.dumps(manifest(events), ensure_ascii=False, indent=2) + "\n")
    if preview:
        semantic = output_dir / f"{report_id}.semantic-preview.json"
        secure_write_text(semantic, json.dumps(semantic_preview(events), ensure_ascii=False, indent=2) + "\n")
        paths["semantic_preview"] = semantic
    return paths


def secure_directory(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True, mode=0o700)
    path.chmod(0o700)


def secure_write_text(path: Path, content: str) -> None:
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
        handle.write(content)
    path.chmod(0o600)


def main() -> int:
    parser = argparse.ArgumentParser(description="Create a local Codex usage audit from JSONL.")
    range_group = parser.add_mutually_exclusive_group()
    range_group.add_argument("--days", type=int, default=7)
    range_group.add_argument("--from", dest="date_from", help="Start date, YYYY-MM-DD")
    parser.add_argument("--to", dest="date_to", help="End date, YYYY-MM-DD; required with --from")
    parser.add_argument("--input-root", type=Path, default=Path.home() / ".codex", help="Read-only JSONL root")
    parser.add_argument("--output-dir", type=Path, default=Path.home() / ".codex" / "reports")
    parser.add_argument("--semantic-preview", action="store_true")
    args = parser.parse_args()
    now = dt.datetime.now(tz=LOCAL_TZ)
    if args.date_from:
        if not args.date_to:
            parser.error("--to is required with --from")
        try:
            start = dt.datetime.fromisoformat(args.date_from).replace(tzinfo=LOCAL_TZ)
            end = dt.datetime.fromisoformat(args.date_to).replace(tzinfo=LOCAL_TZ) + dt.timedelta(days=1) - dt.timedelta(microseconds=1)
        except ValueError:
            parser.error("dates must be YYYY-MM-DD")
        if (end - start).days > 90 or end < start:
            parser.error("custom ranges must be between 0 and 90 days")
    else:
        if not 1 <= args.days <= 90:
            parser.error("--days must be between 1 and 90")
        end = now
        first_date = now.date() - dt.timedelta(days=args.days - 1)
        start = dt.datetime.combine(first_date, dt.time.min, tzinfo=LOCAL_TZ)
    paths = run(args.input_root, args.output_dir, start, end, args.semantic_preview)
    for name, path in paths.items():
        print(f"{name}: {path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
