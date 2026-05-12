from __future__ import annotations

import json
import shlex
import subprocess
import sys
from pathlib import Path
from typing import Any

from radar_config import load_config_section, load_workspace_config


ROOT = Path(__file__).resolve().parent.parent


def load_deployment_config(config_path: Path | None = None) -> dict[str, Any]:
    payload = load_workspace_config(config_path)
    deployment = payload.get("deployment", {})
    if not isinstance(deployment, dict):
        return {}
    resolved = dict(deployment)
    remote_root = str(resolved.get("remote_root") or "").strip()
    if remote_root and Path(remote_root).exists():
        resolved["_local_deployment"] = True
        resolved["server_host"] = ""
    return resolved


def quote_remote_command(remote_command: list[str]) -> str:
    return " ".join(shlex.quote(str(part)) for part in remote_command)


def build_ssh_command(host: str, ssh_options: str, remote_command: list[str]) -> list[str]:
    command = ["ssh"]
    if ssh_options.strip():
        command.extend(shlex.split(ssh_options))
    command.append(host)
    if remote_command:
        command.append(quote_remote_command(remote_command))
    return command


def scp_option_args(ssh_options: str) -> list[str]:
    args: list[str] = []
    tokens = shlex.split(ssh_options) if ssh_options.strip() else []
    index = 0
    while index < len(tokens):
        token = tokens[index]
        if token == "-p":
            args.append("-P")
            if index + 1 < len(tokens):
                args.append(tokens[index + 1])
                index += 2
                continue
        elif token.startswith("-p") and len(token) > 2:
            args.append("-P" + token[2:])
            index += 1
            continue
        args.append(token)
        index += 1
    return args


def build_scp_command(ssh_options: str, source: str, destination: str) -> list[str]:
    return ["scp", *scp_option_args(ssh_options), source, destination]


def run_command_json(command: list[str], *, timeout_seconds: int, input_text: str | None = None) -> tuple[dict[str, Any] | None, str]:
    try:
        result = subprocess.run(
            command,
            input=input_text,
            check=False,
            capture_output=True,
            text=True,
            timeout=max(timeout_seconds, 30),
        )
    except subprocess.TimeoutExpired as exc:
        details: list[str] = []
        for label, stream in (("stdout", exc.stdout), ("stderr", exc.stderr)):
            if not stream:
                continue
            text = stream.decode("utf-8", errors="replace") if isinstance(stream, bytes) else str(stream)
            text = text.strip()
            if text:
                details.append(f"{label}_tail={text[-500:]}")
        suffix = " | " + " | ".join(details) if details else ""
        return None, f"timeout_after={max(timeout_seconds, 30)}s{suffix}"
    if result.returncode != 0:
        message = result.stderr.strip() or result.stdout.strip() or f"command exited with {result.returncode}"
        return None, message
    stdout = result.stdout.strip()
    if not stdout:
        return {}, ""
    try:
        return json.loads(stdout), ""
    except json.JSONDecodeError:
        lines = [line for line in stdout.splitlines() if line.strip()]
        for line in reversed(lines):
            try:
                return json.loads(line), ""
            except json.JSONDecodeError:
                continue
        decoder = json.JSONDecoder()
        for index, char in enumerate(stdout):
            if char not in "[{":
                continue
            try:
                payload, end = decoder.raw_decode(stdout[index:])
            except json.JSONDecodeError:
                continue
            trailing = stdout[index + end :].strip()
            if not trailing:
                return payload, ""
        return None, f"invalid_json:{stdout[:200]}"


def run_local_python(
    script_path: Path,
    args: list[str],
    *,
    timeout_seconds: int,
    python_bin: str | None = None,
) -> tuple[dict[str, Any] | None, str]:
    command = [python_bin or sys.executable, str(script_path), *args]
    return run_command_json(command, timeout_seconds=timeout_seconds)


def run_remote_python(
    *,
    host: str,
    ssh_options: str,
    python_bin: str,
    script_path: str,
    script_args: list[str],
    timeout_seconds: int,
) -> tuple[dict[str, Any] | None, str]:
    command = build_ssh_command(host, ssh_options, [python_bin, script_path, *script_args])
    return run_command_json(command, timeout_seconds=timeout_seconds)


def run_remote_inline_python(
    *,
    host: str,
    ssh_options: str,
    script: str,
    timeout_seconds: int,
) -> tuple[dict[str, Any] | None, str]:
    command = build_ssh_command(host, ssh_options, ["python3", "-"])
    return run_command_json(command, timeout_seconds=timeout_seconds, input_text=script)


def resolve_runtime_section(config_path: Path | None, section: str) -> dict[str, Any]:
    value = load_config_section(config_path, section)
    return value if isinstance(value, dict) else {}
