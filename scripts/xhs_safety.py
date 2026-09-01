#!/usr/bin/env python3
"""Small, shared stop-state for Xiaohongshu sessions.

This module deliberately does not try to predict a platform threshold.  Its
only job is to make a detected safety challenge (or an uncertain write state)
durable, so a later ``--resume`` cannot silently send another request.
"""

from __future__ import annotations

import json
import os
import re
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Mapping
from urllib.parse import unquote


SAFETY_STATE_FILENAME = "xhs_safety_state.json"
MANUAL_REVERIFY_ACTION = "manual_complete_platform_verification_then_start_new_session"


class SafetyHaltedError(RuntimeError):
    """Raised before any Xiaohongshu action when a shared session is halted."""


class UnsafePrivateRuntimeAccessError(RuntimeError):
    """Raised before a browser starts when legacy private-runtime access is requested."""


def reject_unsafe_private_runtime(operation: str) -> None:
    """Disable the runtime-module probing associated with Xiaohongshu 300031."""
    label = str(operation or "小红书账号操作").strip()
    raise UnsafePrivateRuntimeAccessError(
        f"安全停止：{label}依赖的小红书内部模块探测已禁用。"
        "当前版本不会打开浏览器、不会注册自定义网页模块，也不会改用其他私有接口。"
    )


def utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def default_safety_state_path(anchor: Path | str) -> Path:
    """Place the shared state next to the output artifact, never in a browser profile."""
    return Path(anchor).expanduser().resolve().parent / SAFETY_STATE_FILENAME


def resolve_safety_state_path(
    value: str | Path | None,
    anchor: Path | str,
    predecessors: Iterable[Path | str] = (),
) -> Path:
    """Resolve one state file for a run without silently splitting its safety boundary.

    A downstream stage inherits the state next to an existing input artifact.
    When inputs come from more than one active session, fail closed and require
    an explicit ``--safety-state`` instead of arbitrarily choosing one.
    """
    if value:
        return Path(value).expanduser().resolve()

    upstream_states: list[Path] = []
    for predecessor in predecessors:
        candidate = default_safety_state_path(predecessor)
        if candidate.exists() and candidate not in upstream_states:
            upstream_states.append(candidate)
    if not upstream_states:
        return default_safety_state_path(anchor)
    if len(upstream_states) == 1:
        return upstream_states[0]

    halted = [path for path in upstream_states if is_security_halted(load_safety_state(path))]
    if halted:
        # Returning a halted state makes the caller stop before any network or
        # browser operation, even when another input was still active.
        return halted[0]
    rendered = ', '.join(str(path) for path in upstream_states)
    raise SafetyHaltedError(
        '输入来自多个不同的小红书会话，已停止以免混合状态：'
        f'{rendered}。请明确传入同一个 --safety-state。'
    )


_SIMPLE_SECRET_KEY = r"(?:xsec[_-]token|xsec[_-]source|signature|sign|web[_-]session)"
_AUTHORIZATION_KEY = r"authorization"
_COOKIE_KEY = r"(?:set[-_]cookie|cookie)"
_XHS_A1_COOKIE_KEY = r"a1"
_PERSISTED_ERROR_KEYS = frozenset({
    "error",
    "errors",
    "event",
    "events",
    "halt",
    "message",
    "messages",
    "safety_halt",
    "stderr",
    "stdout",
})


def redact_sensitive_text(value: object) -> str:
    """Remove Xiaohongshu/session credentials from error text in common formats."""
    text = " ".join(str(value or "").split())
    for _ in range(3):
        decoded = unquote(text)
        if decoded == text:
            break
        text = decoded

    text = re.sub(
        r"(https?://[^\s?#]+(?:/[^\s?#]*)?)\?[^\s#]+",
        r"\1?<redacted_query>",
        text,
        flags=re.IGNORECASE,
    )
    text = re.sub(
        rf"([\"']?{_SIMPLE_SECRET_KEY}[\"']?\s*:\s*)([\"'])[^\"']*\2",
        r"\1\2<redacted>\2",
        text,
        flags=re.IGNORECASE,
    )
    text = re.sub(
        rf"((?:--)?{_SIMPLE_SECRET_KEY}\s*(?:=|:|\s)\s*)([\"'])[^\"']*\2",
        r"\1\2<redacted>\2",
        text,
        flags=re.IGNORECASE,
    )
    text = re.sub(
        rf"((?<![\w-])(?:--)?{_SIMPLE_SECRET_KEY}(?![\w-])\s*(?:=|:|\s)\s*)[^\s&,;}}\]]+",
        r"\1<redacted>",
        text,
        flags=re.IGNORECASE,
    )
    text = re.sub(
        rf"([\"']?{_AUTHORIZATION_KEY}[\"']?\s*:\s*)([\"'])[^\"']*\2",
        r"\1\2<redacted>\2",
        text,
        flags=re.IGNORECASE,
    )
    text = re.sub(
        rf"((?<![\w-])(?:--)?{_AUTHORIZATION_KEY}(?![\w-])\s*(?:=|:|\s)\s*)(?:(?:bearer|basic)\s+)?[^\s&,;}}\]]+",
        r"\1<redacted>",
        text,
        flags=re.IGNORECASE,
    )
    text = re.sub(
        rf"([\"']?{_COOKIE_KEY}[\"']?\s*:\s*)([\"'])[^\"']*\2",
        r"\1\2<redacted>\2",
        text,
        flags=re.IGNORECASE,
    )
    text = re.sub(
        rf"((?<![\w-])(?:--)?{_COOKIE_KEY}(?![\w-])\s*(?:=|:|\s)\s*).*$",
        r"\1<redacted>",
        text,
        flags=re.IGNORECASE,
    )
    text = re.sub(
        rf"([\"']?{_XHS_A1_COOKIE_KEY}[\"']?\s*:\s*)([\"'])[^\"']*\2",
        r"\1\2<redacted>\2",
        text,
        flags=re.IGNORECASE,
    )
    text = re.sub(
        rf"((?<![\w-]){_XHS_A1_COOKIE_KEY}(?![\w-])\s*(?:=|:)\s*)([\"'])[^\"']*\2",
        r"\1\2<redacted>\2",
        text,
        flags=re.IGNORECASE,
    )
    text = re.sub(
        rf"((?<![\w-]){_XHS_A1_COOKIE_KEY}(?![\w-])\s*(?:=|:)\s*)[^\s&,;}}\]]+",
        r"\1<redacted>",
        text,
        flags=re.IGNORECASE,
    )
    return text


def redact_persisted_errors(value: Any, *, _error_context: bool = False) -> Any:
    """Return a JSON-compatible copy with every persisted error string redacted."""
    if isinstance(value, Mapping):
        return {
            key: redact_persisted_errors(
                child,
                _error_context=(
                    _error_context
                    or str(key).strip().lower() in _PERSISTED_ERROR_KEYS
                ),
            )
            for key, child in value.items()
        }
    if isinstance(value, list):
        return [
            redact_persisted_errors(child, _error_context=_error_context)
            for child in value
        ]
    if isinstance(value, tuple):
        return [
            redact_persisted_errors(child, _error_context=_error_context)
            for child in value
        ]
    if _error_context and isinstance(value, str):
        return redact_sensitive_text(value)
    return value


def _redact_message(value: object) -> str:
    return redact_sensitive_text(value)[:500]


def atomic_write_json(path: Path | str, data: Any) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    try:
        with temporary.open("w", encoding="utf-8") as handle:
            handle.write(json.dumps(data, ensure_ascii=False, indent=2) + "\n")
            handle.flush()
            os.fsync(handle.fileno())
        temporary.chmod(0o600)
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def load_safety_state(path: Path | str) -> dict[str, Any] | None:
    state_path = Path(path)
    if not state_path.exists():
        return None
    try:
        payload = json.loads(state_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise SafetyHaltedError(f"安全状态文件无法读取，已停止以免重复访问：{state_path}") from exc
    if not isinstance(payload, dict):
        raise SafetyHaltedError(f"安全状态文件格式错误，已停止以免重复访问：{state_path}")
    return payload


def is_security_halted(state: Mapping[str, Any] | None) -> bool:
    return bool(state and (state.get("state") == "security_halted" or state.get("security_halted") is True))


def _new_state(stage: str, policy: Mapping[str, Any] | None) -> dict[str, Any]:
    now = utc_now()
    return {
        "schema_version": 1,
        "session_id": str(uuid.uuid4()),
        "state": "active",
        "security_halted": False,
        "created_at": now,
        "updated_at": now,
        "last_stage": stage,
        "policy": dict(policy or {}),
        "halt": None,
        "checkpoints": [{"at": now, "stage": stage, "event": "session_started"}],
    }


def ensure_active_session(
    path: Path | str,
    *,
    stage: str,
    policy: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Create or reopen an active state; a halted state is intentionally irreversible."""
    state_path = Path(path)
    state = load_safety_state(state_path)
    if is_security_halted(state):
        halt = state.get("halt") if isinstance(state, dict) else {}
        reason = _redact_message((halt or {}).get("message") or "此前会话已触发安全停机")
        raise SafetyHaltedError(
            f"此前小红书会话已安全停机，不能用 --resume 继续：{reason}。"
            f"请先由用户在平台内完成处理，再使用新的安全状态文件开启新会话。"
        )
    if state is None:
        state = _new_state(stage, policy)
    else:
        state["state"] = "active"
        state["security_halted"] = False
        state["updated_at"] = utc_now()
        state["last_stage"] = stage
        existing_policy = state.get("policy")
        if not isinstance(existing_policy, dict):
            existing_policy = {}
        existing_policy.update(dict(policy or {}))
        state["policy"] = existing_policy
        checkpoints = state.get("checkpoints")
        if not isinstance(checkpoints, list):
            checkpoints = []
        checkpoints.append({"at": state["updated_at"], "stage": stage, "event": "operation_started"})
        state["checkpoints"] = checkpoints
    atomic_write_json(state_path, state)
    return state


def mark_security_halted(
    path: Path | str,
    *,
    stage: str,
    reason_code: str,
    message: object,
) -> dict[str, Any]:
    """Persist the first stop reason before the caller returns or raises."""
    state_path = Path(path)
    state = load_safety_state(state_path) or _new_state(stage, None)
    now = utc_now()
    state["state"] = "security_halted"
    state["security_halted"] = True
    state["updated_at"] = now
    state["last_stage"] = stage
    state["halt"] = {
        "at": now,
        "stage": stage,
        "reason_code": str(reason_code or "security_challenge"),
        "message": _redact_message(message),
        "next_action": MANUAL_REVERIFY_ACTION,
    }
    checkpoints = state.get("checkpoints")
    if not isinstance(checkpoints, list):
        checkpoints = []
    checkpoints.append({
        "at": now,
        "stage": stage,
        "event": "security_halted",
        "reason_code": state["halt"]["reason_code"],
    })
    state["checkpoints"] = checkpoints
    atomic_write_json(state_path, state)
    return state


def classify_safety_error(error: object) -> tuple[str, str] | None:
    """Return a stable reason code only for clear safety / uncertain-state signals."""
    raw_text = " ".join(str(error or "").split())
    text = _redact_message(raw_text)
    lowered = raw_text.lower()
    markers = (
        ("security_challenge", ("safety_breaker", "securitychallengeerror", "安全验证", "异常访问", "访问异常", "当前请求异常", "访问过于频繁", "操作过于频繁", "请求过于频繁", "网络环境存在风险", "当前环境存在风险", "拖动滑块", "captcha", "security verification", "abnormal access", "too many requests", "/website-login/error", "http 403", "http 412", "http 429", "http 461", "300031", "code=300012", "code 300012")),
        ("page_binding_lost", (
            "executepagebindingerror", "page binding", "current page is not xiaohongshu.com",
            "arc worker runtime marker", "arc tab runtime marker", "arc worker expected url no longer matches",
            "arc 中未找到符合", "arc 中找到多个符合", "state bridge is missing",
            "browser job state bridge disappeared",
        )),
        ("login_required", ("looks logged out", "登录后推荐", "手机号登录", "扫码登录", "验证码登录")),
        ("uncertain_write_state", ("high_risk_state_uncertain",)),
    )
    for code, values in markers:
        if any(marker in lowered for marker in values):
            return code, text
    return None


def halt_if_safety_error(path: Path | str, *, stage: str, error: object) -> bool:
    classified = classify_safety_error(error)
    if not classified:
        return False
    reason_code, message = classified
    mark_security_halted(path, stage=stage, reason_code=reason_code, message=message)
    return True
