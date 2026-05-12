#!/usr/bin/env python3
"""Smoke-test remote command quoting for Radar upstream bridge."""

from __future__ import annotations

import shlex

from radar_upstream_bridge import build_ssh_command, quote_remote_command, scp_option_args


def assert_equal(label: str, actual: object, expected: object) -> None:
    if actual != expected:
        raise AssertionError(f"{label}: expected {expected!r}, got {actual!r}")


def main() -> int:
    dangerous_company = "测试科技; touch /tmp/radar_poc $(id) `id`"
    remote_args = [
        "/opt/radar/.venv/bin/python",
        "/opt/radar/scripts/run_company_discovery.py",
        "--company",
        dangerous_company,
        "--article-limit",
        "8",
    ]
    expected_remote = quote_remote_command(remote_args)
    command = build_ssh_command(
        "radar-runtime.example",
        "-p 2222 -o BatchMode=yes",
        remote_args,
    )

    assert_equal(
        "ssh argv",
        command,
        [
            "ssh",
            "-p",
            "2222",
            "-o",
            "BatchMode=yes",
            "radar-runtime.example",
            expected_remote,
        ],
    )
    assert_equal("remote shell round-trip", shlex.split(command[-1]), remote_args)
    assert_equal("scp port conversion", scp_option_args("-p 2222 -o BatchMode=yes"), ["-P", "2222", "-o", "BatchMode=yes"])
    print("radar_upstream_bridge_smoke_ok")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
