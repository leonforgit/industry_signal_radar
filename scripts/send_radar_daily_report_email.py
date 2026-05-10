#!/usr/bin/env python3
"""Send the latest Radar daily report via SMTP."""

from __future__ import annotations

import argparse
import json
import mimetypes
import os
import re
import shlex
import smtplib
import socket
import ssl
import subprocess
from urllib.parse import urlparse
from dataclasses import dataclass
from email.message import EmailMessage
from email.utils import formataddr
from pathlib import Path
from typing import Any

try:
    from radar_config import DEFAULT_CONFIG_PATH, load_config_section
except ModuleNotFoundError:
    from scripts.radar_config import DEFAULT_CONFIG_PATH, load_config_section


ROOT = Path(__file__).resolve().parent.parent
DEFAULT_CACHE_DIR = Path("~/.codex/state/investment/industry_signal_radar/email_delivery").expanduser()
DEFAULT_STATE_FILE = DEFAULT_CACHE_DIR / "send_state.json"
DEFAULT_REMOTE_SNAPSHOT_JSON = "/opt/industry-signal-radar/output/snapshots/radar_daily_battlecard_snapshot_latest.json"
DEFAULT_REMOTE_REPORT_MD = "/opt/industry-signal-radar/output/reports/radar_daily_battlecard_latest.md"
DEFAULT_REMOTE_REPORT_PDF = "/opt/industry-signal-radar/output/reports/radar_daily_battlecard_latest.pdf"
DEFAULT_REMOTE_KIMI_EDITORIAL_JSON = "/opt/industry-signal-radar/output/reports/radar_kimi_editorial_latest.json"
DEFAULT_REMOTE_HANDOFF_MD = "/opt/industry-signal-radar/output/handoffs/radar_daily_battlecard_handoff_latest.md"
DEFAULT_REMOTE_QUALITY_JSON = "/opt/industry-signal-radar/output/reports/radar_daily_battlecard_quality_latest.json"
DEFAULT_REMOTE_QUALITY_MD = "/opt/industry-signal-radar/output/reports/radar_daily_battlecard_quality_latest.md"
DEFAULT_REMOTE_SOURCE_READINESS_JSON = "/opt/industry-signal-radar/output/reports/radar_source_readiness_latest.json"
DEFAULT_SMTP_ENV_FILE = Path("/opt/quant-runtime/config/paper_trade_daily.env")
REMOTE_ROOT_PREFIX = "/opt/industry-signal-radar/"
LOCAL_REPORT_TRANSPORT = "local"
SSH_REPORT_TRANSPORT = "ssh"
SEND_SKIPPED_GATE = 20


@dataclass
class EmailConfig:
    config_path: Path
    remote_host: str
    remote_snapshot_json_path: str
    remote_report_md_path: str
    remote_report_pdf_path: str
    remote_kimi_editorial_json_path: str
    remote_handoff_md_path: str
    remote_quality_json_path: str
    remote_quality_md_path: str
    remote_source_readiness_json_path: str
    local_cache_dir: Path
    state_file: Path
    email_to: str
    subject_prefix: str
    force: bool
    dry_run: bool
    report_transport: str
    smtp_env_file: Path | None
    smtp_timeout_seconds: float
    attach_report_pdf: bool
    attach_report_md: bool
    attach_handoff_md: bool
    attach_quality_md: bool
    require_report_pdf: bool
    require_quality_pass: bool
    require_source_readiness_pass: bool


@dataclass
class SmtpConfig:
    host: str
    port: int
    username: str
    password: str
    from_addr: str
    from_name: str
    to_addrs: list[str]
    reply_to: str
    starttls: bool
    use_ssl: bool
    force_ipv4: bool
    socks_proxy: str
    timeout_seconds: float


def parse_args() -> argparse.Namespace:
    bootstrap = argparse.ArgumentParser(add_help=False)
    bootstrap.add_argument("--config", type=Path, default=DEFAULT_CONFIG_PATH)
    bootstrap_args, remaining = bootstrap.parse_known_args()
    defaults = load_config_section(bootstrap_args.config, "email_delivery")
    default_cache_dir = Path(str(defaults.get("local_cache_dir", DEFAULT_CACHE_DIR))).expanduser()
    default_state_file = Path(str(defaults.get("state_file", default_cache_dir / DEFAULT_STATE_FILE.name))).expanduser()

    parser = argparse.ArgumentParser(description=__doc__, parents=[bootstrap])
    parser.add_argument("--remote-host", default=str(defaults.get("remote_host") or ""))
    parser.add_argument("--remote-snapshot-json-path", default=str(defaults.get("remote_snapshot_json_path", DEFAULT_REMOTE_SNAPSHOT_JSON)))
    parser.add_argument("--remote-report-md-path", default=str(defaults.get("remote_report_md_path", DEFAULT_REMOTE_REPORT_MD)))
    parser.add_argument("--remote-report-pdf-path", default=str(defaults.get("remote_report_pdf_path", DEFAULT_REMOTE_REPORT_PDF)))
    parser.add_argument(
        "--remote-kimi-editorial-json-path",
        default=str(defaults.get("remote_kimi_editorial_json_path", DEFAULT_REMOTE_KIMI_EDITORIAL_JSON)),
    )
    parser.add_argument("--remote-handoff-md-path", default=str(defaults.get("remote_handoff_md_path", DEFAULT_REMOTE_HANDOFF_MD)))
    parser.add_argument("--remote-quality-json-path", default=str(defaults.get("remote_quality_json_path", DEFAULT_REMOTE_QUALITY_JSON)))
    parser.add_argument("--remote-quality-md-path", default=str(defaults.get("remote_quality_md_path", DEFAULT_REMOTE_QUALITY_MD)))
    parser.add_argument(
        "--remote-source-readiness-json-path",
        default=str(defaults.get("remote_source_readiness_json_path", DEFAULT_REMOTE_SOURCE_READINESS_JSON)),
    )
    parser.add_argument("--local-cache-dir", type=Path, default=default_cache_dir)
    parser.add_argument("--state-file", type=Path, default=default_state_file)
    parser.add_argument("--email-to", default=str(defaults.get("email_to") or ""))
    parser.add_argument("--subject-prefix", default=str(defaults.get("subject_prefix", "Radar 投资机会日报")))
    parser.add_argument("--force", action="store_true", help="Send even if this exact report was already sent.")
    parser.add_argument("--dry-run", action="store_true", help="Render subject/body locally without sending.")
    parser.add_argument(
        "--report-transport",
        choices=[LOCAL_REPORT_TRANSPORT, SSH_REPORT_TRANSPORT],
        default=str(defaults.get("report_transport", LOCAL_REPORT_TRANSPORT)),
        help="Read local files directly or fetch them over SSH.",
    )
    parser.add_argument(
        "--smtp-env-file",
        type=Path,
        default=Path(str(defaults.get("smtp_env_file", DEFAULT_SMTP_ENV_FILE))).expanduser(),
        help="Environment file that stores SMTP credentials.",
    )
    parser.add_argument(
        "--smtp-timeout-seconds",
        type=float,
        default=float(defaults.get("smtp_timeout_seconds", 20.0)),
        help="Socket timeout for SMTP delivery.",
    )
    parser.add_argument(
        "--skip-quality-gate",
        action="store_true",
        help="Send even when radar_report_quality status is not pass.",
    )
    return parser.parse_args(remaining)


def build_config(args: argparse.Namespace) -> EmailConfig:
    defaults = load_config_section(Path(args.config), "email_delivery")
    cfg = EmailConfig(
        config_path=Path(args.config),
        remote_host=str(args.remote_host),
        remote_snapshot_json_path=str(args.remote_snapshot_json_path),
        remote_report_md_path=str(args.remote_report_md_path),
        remote_report_pdf_path=str(args.remote_report_pdf_path),
        remote_kimi_editorial_json_path=str(args.remote_kimi_editorial_json_path),
        remote_handoff_md_path=str(args.remote_handoff_md_path),
        remote_quality_json_path=str(args.remote_quality_json_path),
        remote_quality_md_path=str(args.remote_quality_md_path),
        remote_source_readiness_json_path=str(args.remote_source_readiness_json_path),
        local_cache_dir=Path(args.local_cache_dir).expanduser(),
        state_file=Path(args.state_file).expanduser(),
        email_to=str(args.email_to).strip(),
        subject_prefix=str(args.subject_prefix).strip() or "Radar 投资机会日报",
        force=bool(args.force),
        dry_run=bool(args.dry_run),
        report_transport=str(args.report_transport),
        smtp_env_file=Path(args.smtp_env_file).expanduser() if args.smtp_env_file else None,
        smtp_timeout_seconds=float(args.smtp_timeout_seconds),
        attach_report_pdf=bool(defaults.get("attach_report_pdf", True)),
        attach_report_md=bool(defaults.get("attach_report_md", True)),
        attach_handoff_md=bool(defaults.get("attach_handoff_md", True)),
        attach_quality_md=bool(defaults.get("attach_quality_md", True)),
        require_report_pdf=bool(defaults.get("require_report_pdf", False)),
        require_quality_pass=not bool(args.skip_quality_gate) and bool(defaults.get("require_quality_pass", True)),
        require_source_readiness_pass=bool(defaults.get("require_source_readiness_pass", True)),
    )
    if cfg.report_transport not in {LOCAL_REPORT_TRANSPORT, SSH_REPORT_TRANSPORT}:
        raise SystemExit(f"Unsupported report transport: {cfg.report_transport}")
    if cfg.report_transport == SSH_REPORT_TRANSPORT and not cfg.remote_host.strip():
        raise SystemExit("Missing remote host. Pass --remote-host or set email_delivery.remote_host in a private config.")
    return cfg


def run_remote_text(host: str, remote_path: str) -> str:
    completed = subprocess.run(
        ["ssh", host, f"cat {shlex.quote(remote_path)}"],
        capture_output=True,
        text=True,
        check=False,
    )
    if completed.returncode != 0:
        raise RuntimeError(completed.stderr.strip() or completed.stdout.strip() or f"Failed to fetch {remote_path}")
    return completed.stdout


def copy_remote_file(host: str, remote_path: str, local_path: Path) -> None:
    local_path.parent.mkdir(parents=True, exist_ok=True)
    completed = subprocess.run(
        ["scp", f"{host}:{remote_path}", str(local_path)],
        capture_output=True,
        text=True,
        check=False,
    )
    if completed.returncode != 0:
        raise RuntimeError(completed.stderr.strip() or completed.stdout.strip() or f"Failed to copy {remote_path}")


def read_local_text(path: str) -> str:
    local_path = resolve_local_path(path)
    if not local_path.exists():
        raise RuntimeError(f"Local file does not exist: {local_path}")
    return local_path.read_text(encoding="utf-8")


def resolve_local_path(path: str) -> Path:
    local_path = Path(path).expanduser()
    if local_path.exists():
        return local_path
    raw = str(path).strip()
    if raw.startswith(REMOTE_ROOT_PREFIX):
        relative = raw[len(REMOTE_ROOT_PREFIX) :]
        candidate = ROOT / relative
        if candidate.exists():
            return candidate
    return local_path


def read_json(cfg: EmailConfig, source_path: str) -> dict[str, Any]:
    raw = read_text(cfg, source_path)
    payload = json.loads(raw)
    if not isinstance(payload, dict):
        raise SystemExit(f"{source_path} is not a JSON object.")
    return payload


def read_text(cfg: EmailConfig, source_path: str) -> str:
    if cfg.report_transport == LOCAL_REPORT_TRANSPORT:
        return read_local_text(source_path)
    return run_remote_text(cfg.remote_host, source_path)


def build_attachment_path(cfg: EmailConfig, source_path: str, target_name: str) -> Path:
    if cfg.report_transport == LOCAL_REPORT_TRANSPORT:
        attachment_path = resolve_local_path(source_path)
        if not attachment_path.exists():
            raise RuntimeError(f"Attachment file does not exist: {attachment_path}")
        return attachment_path
    cfg.local_cache_dir.mkdir(parents=True, exist_ok=True)
    attachment_path = cfg.local_cache_dir / target_name
    copy_remote_file(cfg.remote_host, source_path, attachment_path)
    return attachment_path


def load_state(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    payload = json.loads(path.read_text(encoding="utf-8"))
    return payload if isinstance(payload, dict) else {}


def write_state(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def build_subject(prefix: str, report_date: str) -> str:
    return f"{prefix} {report_date}".strip()


def should_skip_send(state: dict[str, Any], report_date: str, generated_at: str, force: bool) -> bool:
    if force:
        return False
    return (
        str(state.get("last_report_date") or "") == report_date
        and str(state.get("last_generated_at") or "") == generated_at
    )


def load_env_file(path: Path) -> dict[str, str]:
    if not path.exists():
        raise RuntimeError(f"SMTP env file does not exist: {path}")
    env: dict[str, str] = {}
    for line_number, raw_line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        stripped = raw_line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        if stripped.startswith("export "):
            stripped = stripped[len("export ") :].strip()
        try:
            tokens = shlex.split(stripped, comments=True, posix=True)
        except ValueError as exc:
            raise RuntimeError(f"Invalid SMTP env line {line_number}: {exc}") from exc
        if not tokens:
            continue
        if len(tokens) != 1 or "=" not in tokens[0]:
            raise RuntimeError(f"Invalid SMTP env line {line_number}: {raw_line}")
        key, value = tokens[0].split("=", 1)
        env[key.strip()] = value
    return env


def env_flag(value: str, default: bool) -> bool:
    normalized = str(value).strip().lower()
    if not normalized:
        return default
    if normalized in {"1", "true", "yes", "on"}:
        return True
    if normalized in {"0", "false", "no", "off"}:
        return False
    return default


def parse_email_list(value: str) -> list[str]:
    normalized = value.replace(";", ",")
    return [item.strip() for item in normalized.split(",") if item.strip()]


def build_smtp_config(cfg: EmailConfig) -> SmtpConfig:
    env_from_file = load_env_file(cfg.smtp_env_file) if cfg.smtp_env_file else {}
    effective_env = dict(env_from_file)
    effective_env.update(os.environ)

    host = str(effective_env.get("SMTP_HOST", "")).strip()
    from_addr = str(effective_env.get("SMTP_FROM", "")).strip()
    username = str(effective_env.get("SMTP_USERNAME", "")).strip()
    password = str(effective_env.get("SMTP_PASSWORD", "")).strip()
    to_addrs = parse_email_list(str(effective_env.get("SMTP_TO", cfg.email_to)).strip())
    from_name = str(effective_env.get("SMTP_FROM_NAME", "")).strip()
    reply_to = str(effective_env.get("SMTP_REPLY_TO", "")).strip()
    use_ssl = env_flag(str(effective_env.get("SMTP_USE_SSL", "")), default=False)
    force_ipv4 = env_flag(str(effective_env.get("SMTP_FORCE_IPV4", "")), default=False)
    socks_proxy = str(effective_env.get("SMTP_SOCKS_PROXY", "")).strip()
    default_starttls = not use_ssl
    starttls = env_flag(str(effective_env.get("SMTP_STARTTLS", "")), default=default_starttls)
    timeout_raw = str(effective_env.get("SMTP_TIMEOUT_SECONDS", cfg.smtp_timeout_seconds)).strip()
    port_raw = str(effective_env.get("SMTP_PORT", "465" if use_ssl else "587")).strip()

    if not host:
        raise SystemExit("Missing SMTP host. Set SMTP_HOST in the SMTP env file or shell environment.")
    if not from_addr:
        raise SystemExit("Missing SMTP sender. Set SMTP_FROM in the SMTP env file or shell environment.")
    if not to_addrs:
        raise SystemExit("Missing SMTP recipient. Set SMTP_TO or email_delivery.email_to.")
    if not username:
        raise SystemExit("Missing SMTP username. Set SMTP_USERNAME in the SMTP env file or shell environment.")
    if not password:
        raise SystemExit("Missing SMTP password. Set SMTP_PASSWORD in the SMTP env file or shell environment.")
    try:
        port = int(port_raw)
    except ValueError as exc:
        raise SystemExit(f"Invalid SMTP port: {port_raw}") from exc
    try:
        timeout_seconds = float(timeout_raw)
    except ValueError as exc:
        raise SystemExit(f"Invalid SMTP timeout: {timeout_raw}") from exc
    if use_ssl and starttls:
        raise SystemExit("SMTP_USE_SSL and SMTP_STARTTLS cannot both be enabled.")

    return SmtpConfig(
        host=host,
        port=port,
        username=username,
        password=password,
        from_addr=from_addr,
        from_name=from_name,
        to_addrs=to_addrs,
        reply_to=reply_to,
        starttls=starttls,
        use_ssl=use_ssl,
        force_ipv4=force_ipv4,
        socks_proxy=socks_proxy,
        timeout_seconds=timeout_seconds,
    )


def create_socks_connection(proxy_url: str, host: str, port: int, timeout: float) -> socket.socket:
    try:
        import socks  # type: ignore[import-untyped]
    except ImportError as exc:
        raise RuntimeError("SMTP_SOCKS_PROXY is set, but PySocks is not installed in this Python environment.") from exc

    parsed = urlparse(proxy_url)
    scheme = parsed.scheme.lower()
    if scheme not in {"socks5", "socks5h", "socks4", "socks4a"}:
        raise RuntimeError(f"Unsupported SMTP_SOCKS_PROXY scheme: {parsed.scheme}")
    if not parsed.hostname or not parsed.port:
        raise RuntimeError("SMTP_SOCKS_PROXY must include host and port, for example socks5h://127.0.0.1:10808")
    proxy_type = socks.SOCKS5 if scheme in {"socks5", "socks5h"} else socks.SOCKS4
    rdns = scheme.endswith("h") or scheme.endswith("a")
    return socks.create_connection(
        (host, port),
        timeout=timeout,
        proxy_type=proxy_type,
        proxy_addr=parsed.hostname,
        proxy_port=int(parsed.port),
        proxy_username=parsed.username,
        proxy_password=parsed.password,
        proxy_rdns=rdns,
    )


def create_ipv4_connection(host: str, port: int, timeout: float, source_address: tuple[str, int] | None = None) -> socket.socket:
    last_error: OSError | None = None
    for family, socktype, proto, _canonname, sockaddr in socket.getaddrinfo(host, port, socket.AF_INET, socket.SOCK_STREAM):
        sock = socket.socket(family, socktype, proto)
        sock.settimeout(timeout)
        try:
            if source_address:
                sock.bind(source_address)
            sock.connect(sockaddr)
            return sock
        except OSError as exc:
            last_error = exc
            sock.close()
    if last_error is not None:
        raise last_error
    raise OSError(f"No IPv4 SMTP address found for {host}:{port}")


class SMTPIPv4(smtplib.SMTP):
    def _get_socket(self, host: str, port: int, timeout: float) -> socket.socket:
        return create_ipv4_connection(host, port, timeout, self.source_address)


class SMTPSSLIPv4(smtplib.SMTP_SSL):
    def _get_socket(self, host: str, port: int, timeout: float) -> socket.socket:
        raw_socket = create_ipv4_connection(host, port, timeout, self.source_address)
        return self.context.wrap_socket(raw_socket, server_hostname=self._host)


class SMTPSocks(smtplib.SMTP):
    proxy_url = ""

    def _get_socket(self, host: str, port: int, timeout: float) -> socket.socket:
        return create_socks_connection(self.proxy_url, host, port, timeout)


class SMTPSSLSocks(smtplib.SMTP_SSL):
    proxy_url = ""

    def _get_socket(self, host: str, port: int, timeout: float) -> socket.socket:
        raw_socket = create_socks_connection(self.proxy_url, host, port, timeout)
        return self.context.wrap_socket(raw_socket, server_hostname=self._host)


def send_smtp_mail(cfg: EmailConfig, subject: str, body: str, attachments: list[Path]) -> None:
    smtp_cfg = build_smtp_config(cfg)
    message = EmailMessage()
    message["Subject"] = subject
    message["From"] = formataddr((smtp_cfg.from_name, smtp_cfg.from_addr)) if smtp_cfg.from_name else smtp_cfg.from_addr
    message["To"] = ", ".join(smtp_cfg.to_addrs)
    if smtp_cfg.reply_to:
        message["Reply-To"] = smtp_cfg.reply_to
    message.set_content(body)

    for attachment in attachments:
        mime_type, _ = mimetypes.guess_type(str(attachment))
        maintype, subtype = ("application", "octet-stream")
        if mime_type:
            maintype, subtype = mime_type.split("/", 1)
        message.add_attachment(
            attachment.read_bytes(),
            maintype=maintype,
            subtype=subtype,
            filename=attachment.name,
        )

    smtp_context = ssl.create_default_context()
    if smtp_cfg.socks_proxy:
        smtp_client_cls = SMTPSSLSocks if smtp_cfg.use_ssl else SMTPSocks
        smtp_client_cls.proxy_url = smtp_cfg.socks_proxy
    elif smtp_cfg.force_ipv4:
        smtp_client_cls = SMTPSSLIPv4 if smtp_cfg.use_ssl else SMTPIPv4
    else:
        smtp_client_cls = smtplib.SMTP_SSL if smtp_cfg.use_ssl else smtplib.SMTP
    with smtp_client_cls(smtp_cfg.host, smtp_cfg.port, timeout=smtp_cfg.timeout_seconds) as client:
        client.ehlo()
        if smtp_cfg.starttls:
            client.starttls(context=smtp_context)
            client.ehlo()
        client.login(smtp_cfg.username, smtp_cfg.password)
        client.send_message(message)


def queue_summary_text(quality_payload: dict[str, Any]) -> str:
    queue_counts = quality_payload.get("queue_counts") or {}
    immediate = int(queue_counts.get("immediate_research") or 0)
    thesis = int(queue_counts.get("thesis_watch") or 0)
    risk = int(queue_counts.get("risk_review") or 0)
    return f"Immediate Research {immediate} / Thesis Watch {thesis} / Risk Review {risk}"


def market_sentiment_summary_text(snapshot: dict[str, Any]) -> str:
    context = snapshot.get("market_sentiment_context") or {}
    if not isinstance(context, dict) or not context:
        return "市场情绪：暂无 sidecar"
    return (
        "市场情绪：资金流 {flow_score}（{flow_label}） / 事件 {event_score}（{event_label}） / 综合 {composite_score}（{composite_label}）"
    ).format(
        flow_score=int(context.get("market_flow_score") or 0),
        flow_label=str(context.get("market_flow_label") or "未标注"),
        event_score=int(context.get("market_event_score") or 0),
        event_label=str(context.get("market_event_label") or "未标注"),
        composite_score=int(context.get("market_composite_score") or 0),
        composite_label=str(context.get("market_composite_label") or "未标注"),
    )


def build_email_body(
    *,
    snapshot: dict[str, Any],
    quality_payload: dict[str, Any],
    source_readiness_payload: dict[str, Any],
    kimi_editorial_payload: dict[str, Any],
) -> str:
    report_date = str(snapshot.get("event_window_end_date") or snapshot.get("report_date") or snapshot.get("as_of_date") or "")
    market_sample_date = str(snapshot.get("market_sample_date") or snapshot.get("as_of_date") or "")
    generated_at = str(snapshot.get("generated_at") or "")
    run_id = str(snapshot.get("run_id") or snapshot.get("radar_run_id") or "")
    quality_status = str(quality_payload.get("status") or "unknown")
    freshness_lag = quality_payload.get("freshness_lag_days")
    top_names = " / ".join(str(x) for x in (quality_payload.get("top_opportunities") or []))
    readiness_status = str(source_readiness_payload.get("status") or "unknown")
    expected_sample_date = str(source_readiness_payload.get("expected_sample_date") or "")
    kimi_headline = ""
    kimi_status = str(kimi_editorial_payload.get("status") or "")
    if kimi_status == "pass":
        kimi_headline = str(kimi_editorial_payload.get("headline") or "").strip()
    editorial_line = f"PM 编辑摘要: {kimi_headline}\n" if kimi_headline else ""
    return (
        f"Radar 投资机会日报 {report_date}\n\n"
        f"运行批次: {run_id}\n"
        f"样本生成时间: {generated_at}\n"
        f"事件窗口日期: {report_date}\n"
        f"市场样本日期: {market_sample_date}\n"
        f"质量门禁: {quality_status}\n"
        f"源数据就绪: {readiness_status}\n"
        f"期望样本日期: {expected_sample_date}\n"
        f"Freshness lag: {freshness_lag}\n"
        f"研究分流: {queue_summary_text(quality_payload)}\n"
        f"{market_sentiment_summary_text(snapshot)}\n"
        f"{editorial_line}"
        f"Top Opportunities: {top_names or '无'}\n\n"
        "完整日报见 PDF 附件。\n"
    )


def extract_report_run_id(report_text: str) -> str:
    patterns = (
        r'codex_output_run_id:\s*"([^"]+)"',
        r"codex_output_run_id:\s*'([^']+)'",
        r"运行批次[：:]\s*`([^`]+)`",
    )
    for pattern in patterns:
        match = re.search(pattern, report_text)
        if match:
            return match.group(1).strip()
    return ""


def main() -> int:
    cfg = build_config(parse_args())
    if not cfg.email_to:
        raise SystemExit("Missing email recipient. Set email_delivery.email_to or pass --email-to.")

    snapshot = read_json(cfg, cfg.remote_snapshot_json_path)
    quality_payload = read_json(cfg, cfg.remote_quality_json_path)
    source_readiness_payload = read_json(cfg, cfg.remote_source_readiness_json_path)
    kimi_editorial_payload = read_json(cfg, cfg.remote_kimi_editorial_json_path)
    report_text = read_text(cfg, cfg.remote_report_md_path)
    report_date = str(snapshot.get("as_of_date") or "").strip()
    generated_at = str(snapshot.get("generated_at") or "").strip()
    if not report_date or not generated_at:
        raise SystemExit("Radar snapshot is missing as_of_date or generated_at.")

    snapshot_run_id = str(snapshot.get("run_id") or snapshot.get("radar_run_id") or "").strip()
    quality_run_id = str(quality_payload.get("run_id") or "").strip()
    report_run_id = extract_report_run_id(report_text)
    editorial_status = str(kimi_editorial_payload.get("status") or "").strip()
    editorial_run_id = str(kimi_editorial_payload.get("run_id") or kimi_editorial_payload.get("radar_run_id") or "").strip()
    inconsistent = [
        f"quality={quality_run_id}" if quality_run_id and quality_run_id != snapshot_run_id else "",
        f"report={report_run_id}" if report_run_id and report_run_id != snapshot_run_id else "",
        f"kimi_editorial={editorial_run_id}" if editorial_status == "pass" and editorial_run_id and editorial_run_id != snapshot_run_id else "",
    ]
    inconsistent = [item for item in inconsistent if item]
    if not snapshot_run_id or not quality_run_id or not report_run_id:
        raise SystemExit(
            "Radar report bundle missing run_id: "
            f"snapshot={snapshot_run_id or 'missing'} quality={quality_run_id or 'missing'} report={report_run_id or 'missing'}"
        )
    if inconsistent:
        raise SystemExit("Radar report bundle run_id mismatch: " + " / ".join(inconsistent) + f" / snapshot={snapshot_run_id}")
    if editorial_status == "pass" and not editorial_run_id:
        raise SystemExit("Radar Kimi editorial status=pass but run_id is missing.")

    if cfg.require_quality_pass and str(quality_payload.get("status") or "") != "pass" and not cfg.force:
        print(
            json.dumps(
                {
                    "status": "skip_quality_gate",
                    "quality_status": str(quality_payload.get("status") or "unknown"),
                    "report_date": report_date,
                    "generated_at": generated_at,
                },
                ensure_ascii=False,
            )
        )
        return SEND_SKIPPED_GATE

    source_readiness_status = str(source_readiness_payload.get("status") or "")
    source_readiness_blockers = [
        str(item)
        for item in (source_readiness_payload.get("blockers") or [])
        if str(item).strip()
    ]
    source_readiness_blocks_delivery = source_readiness_status == "fail" or bool(source_readiness_blockers)
    if cfg.require_source_readiness_pass and source_readiness_blocks_delivery and not cfg.force:
        print(
            json.dumps(
                {
                    "status": "skip_source_readiness_gate",
                    "source_readiness_status": source_readiness_status or "unknown",
                    "source_readiness_blockers": source_readiness_blockers[:8],
                    "report_date": report_date,
                    "generated_at": generated_at,
                },
                ensure_ascii=False,
            )
        )
        return SEND_SKIPPED_GATE

    state = load_state(cfg.state_file)
    if should_skip_send(state, report_date, generated_at, cfg.force):
        print(f"EMAIL_SKIP report_date={report_date} generated_at={generated_at}")
        return 0

    subject = build_subject(cfg.subject_prefix, report_date)
    body = build_email_body(
        snapshot=snapshot,
        quality_payload=quality_payload,
        source_readiness_payload=source_readiness_payload,
        kimi_editorial_payload=kimi_editorial_payload,
    )

    attachments: list[Path] = []
    if cfg.attach_report_pdf:
        pdf_path = build_attachment_path(cfg, cfg.remote_report_pdf_path, f"radar_daily_report_{report_date}.pdf")
        attachments.append(pdf_path)
    elif cfg.require_report_pdf:
        raise SystemExit("Radar daily report PDF is required but attach_report_pdf is disabled.")
    if cfg.attach_report_md and not cfg.attach_report_pdf:
        attachments.append(build_attachment_path(cfg, cfg.remote_report_md_path, f"radar_daily_report_{report_date}.md"))
    if cfg.attach_handoff_md:
        attachments.append(build_attachment_path(cfg, cfg.remote_handoff_md_path, f"radar_research_handoff_{report_date}.md"))
    if cfg.attach_quality_md:
        attachments.append(build_attachment_path(cfg, cfg.remote_quality_md_path, f"radar_report_quality_{report_date}.md"))

    if cfg.dry_run:
        print(
            json.dumps(
                {
                    "subject": subject,
                    "email_to": cfg.email_to,
                    "report_date": report_date,
                    "generated_at": generated_at,
                    "quality_status": str(quality_payload.get("status") or "unknown"),
                    "queue_summary": queue_summary_text(quality_payload),
                    "attachments": [str(path) for path in attachments],
                },
                ensure_ascii=False,
                indent=2,
            )
        )
        return 0

    send_smtp_mail(cfg, subject, body, attachments)
    write_state(
        cfg.state_file,
        {
            "last_report_date": report_date,
            "last_generated_at": generated_at,
            "last_subject": subject,
            "last_email_to": cfg.email_to,
            "last_quality_status": str(quality_payload.get("status") or "unknown"),
            "last_sent_at": datetime_now_iso(),
        },
    )
    print(f"EMAIL_SENT report_date={report_date} subject={subject}")
    return 0


def datetime_now_iso() -> str:
    from datetime import datetime, timezone

    return datetime.now(timezone.utc).isoformat(timespec="seconds")


if __name__ == "__main__":
    raise SystemExit(main())
