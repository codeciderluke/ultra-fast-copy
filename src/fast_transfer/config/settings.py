"""Load and save `config.toml`, and turn it into `TransferOptions`.

Precedence: CLI arguments > config file > defaults. A malformed config falls back
to defaults and reports why, rather than blocking the app.
"""

from __future__ import annotations

import tomllib
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any

from ..core.models import (
    ConflictPolicy,
    ScanMode,
    SpeedPreset,
    SymlinkPolicy,
    TransferOptions,
    VerifyMode,
)
from ..utils.formatting import parse_size
from .defaults import DEFAULT_CONFIG, DEFAULT_CONFIG_TOML, config_path


@dataclass(slots=True)
class UISettings:
    """Everything the GUI remembers between runs."""

    theme: str = "dark"
    language: str = "ko"
    show_advanced_options: bool = False
    preset: SpeedPreset = SpeedPreset.BALANCED
    confirm_on_exit: bool = True
    last_source: str = ""
    last_destination: str = ""
    window_geometry: str = ""


@dataclass(slots=True)
class LoggingSettings:
    level: str = "INFO"
    retention_days: int = 30


@dataclass(slots=True)
class AppSettings:
    """The whole config file, parsed."""

    transfer: TransferOptions = field(default_factory=TransferOptions)
    ui: UISettings = field(default_factory=UISettings)
    logging: LoggingSettings = field(default_factory=LoggingSettings)
    source_path: Path | None = None
    load_errors: tuple[str, ...] = ()

    def with_overrides(self, **overrides: Any) -> AppSettings:
        """Apply non-None CLI overrides onto the transfer options."""
        clean = {k: v for k, v in overrides.items() if v is not None}
        if not clean:
            return self
        return replace(self, transfer=replace(self.transfer, **clean))


def load_settings(path: Path | None = None) -> AppSettings:
    """Read the config file. Missing file -> defaults, no error."""
    target = path or config_path()
    if not target.exists():
        return AppSettings(source_path=target)

    try:
        with target.open("rb") as handle:
            data = tomllib.load(handle)
    except (OSError, tomllib.TOMLDecodeError) as exc:
        return AppSettings(
            source_path=target,
            load_errors=(f"Could not read {target}: {exc}. Using defaults.",),
        )

    errors: list[str] = []
    transfer = _parse_transfer(data.get("transfer", {}), errors)
    ui = _parse_ui(data.get("ui", {}), errors)
    logging_settings = _parse_logging(data.get("logging", {}), errors)
    return AppSettings(
        transfer=transfer,
        ui=ui,
        logging=logging_settings,
        source_path=target,
        load_errors=tuple(errors),
    )


def _parse_transfer(section: dict[str, Any], errors: list[str]) -> TransferOptions:
    defaults = DEFAULT_CONFIG["transfer"]
    options = TransferOptions()

    workers = section.get("workers", defaults["workers"])
    if isinstance(workers, int) and workers > 0:
        options.workers = workers

    options.buffer_size = _size(section, "buffer_size", defaults["buffer_size"], errors)
    options.large_file_buffer_size = _size(
        section, "large_file_buffer_size", defaults["large_file_buffer_size"], errors
    )
    options.verify = _enum(VerifyMode, section.get("verify"), options.verify, "verify", errors)
    options.conflict = _enum(
        ConflictPolicy, section.get("conflict"), options.conflict, "conflict", errors
    )
    options.symlink_policy = _enum(
        SymlinkPolicy, section.get("symlink_policy"), options.symlink_policy, "symlink_policy", errors
    )
    options.retry_count = _int(section, "retry_count", options.retry_count, errors)
    options.scan_mode = (
        ScanMode.PRESCAN if section.get("prescan", True) else ScanMode.STREAMING
    )
    options.use_checkpoint = bool(section.get("checkpoint", True))
    options.preserve_times = bool(section.get("preserve_times", True))
    options.preserve_permissions = bool(section.get("preserve_permissions", False))
    options.include_hidden = bool(section.get("include_hidden", True))
    options.include_system = bool(section.get("include_system", False))

    limit = section.get("bandwidth_limit", "")
    if limit:
        try:
            options.bandwidth_limit = parse_size(limit)
        except ValueError as exc:
            errors.append(f"bandwidth_limit: {exc}")

    return options


def _parse_ui(section: dict[str, Any], errors: list[str]) -> UISettings:
    ui = UISettings()
    ui.theme = str(section.get("theme", ui.theme))
    ui.language = str(section.get("language", ui.language))
    ui.show_advanced_options = bool(section.get("show_advanced_options", False))
    ui.confirm_on_exit = bool(section.get("confirm_on_exit", True))
    ui.preset = _enum(SpeedPreset, section.get("preset"), ui.preset, "preset", errors)
    ui.last_source = str(section.get("last_source", ""))
    ui.last_destination = str(section.get("last_destination", ""))
    ui.window_geometry = str(section.get("window_geometry", ""))
    return ui


def _parse_logging(section: dict[str, Any], errors: list[str]) -> LoggingSettings:
    settings = LoggingSettings()
    level = str(section.get("level", settings.level)).upper()
    if level in ("DEBUG", "INFO", "WARNING", "ERROR"):
        settings.level = level
    else:
        errors.append(f"logging.level: unknown level '{level}'. Using INFO.")
    settings.retention_days = _int(section, "retention_days", settings.retention_days, errors)
    return settings


def _size(section: dict[str, Any], key: str, fallback: Any, errors: list[str]) -> int:
    raw = section.get(key, fallback)
    try:
        return parse_size(raw)
    except (ValueError, TypeError) as exc:
        errors.append(f"{key}: {exc}")
        return parse_size(fallback)


def _int(section: dict[str, Any], key: str, fallback: int, errors: list[str]) -> int:
    value = section.get(key, fallback)
    if isinstance(value, int) and not isinstance(value, bool) and value >= 0:
        return value
    if key in section:
        errors.append(f"{key}: expected a non-negative integer, got {value!r}.")
    return fallback


def _enum(enum_type: Any, raw: Any, fallback: Any, key: str, errors: list[str]) -> Any:
    if raw is None:
        return fallback
    try:
        return enum_type(str(raw))
    except ValueError:
        allowed = ", ".join(member.value for member in enum_type)
        errors.append(f"{key}: '{raw}' is not valid. Expected one of: {allowed}.")
        return fallback


def save_settings(settings: AppSettings, path: Path | None = None) -> Path:
    """Write the config back out as TOML."""
    target = path or settings.source_path or config_path()
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(_render_toml(settings), encoding="utf-8")
    return target


def _render_toml(settings: AppSettings) -> str:
    transfer = settings.transfer
    ui = settings.ui
    log = settings.logging
    limit = f"{transfer.bandwidth_limit}" if transfer.bandwidth_limit else ""
    return f"""# Ultra Fast Copy configuration
# Command line arguments take precedence over this file.

[transfer]
workers = {transfer.workers or 0}
buffer_size = "{transfer.buffer_size}"
large_file_buffer_size = "{transfer.large_file_buffer_size}"
verify = "{transfer.verify.value}"
conflict = "{transfer.conflict.value}"
retry_count = {transfer.retry_count}
prescan = {_toml_bool(transfer.scan_mode is ScanMode.PRESCAN)}
checkpoint = {_toml_bool(transfer.use_checkpoint)}
preserve_times = {_toml_bool(transfer.preserve_times)}
preserve_permissions = {_toml_bool(transfer.preserve_permissions)}
include_hidden = {_toml_bool(transfer.include_hidden)}
include_system = {_toml_bool(transfer.include_system)}
symlink_policy = "{transfer.symlink_policy.value}"
bandwidth_limit = "{limit}"

[ui]
theme = "{ui.theme}"
language = "{ui.language}"
show_advanced_options = {_toml_bool(ui.show_advanced_options)}
preset = "{ui.preset.value}"
confirm_on_exit = {_toml_bool(ui.confirm_on_exit)}
last_source = "{_escape(ui.last_source)}"
last_destination = "{_escape(ui.last_destination)}"
window_geometry = "{ui.window_geometry}"

[logging]
level = "{log.level}"
retention_days = {log.retention_days}
"""


def _toml_bool(value: bool) -> str:
    return "true" if value else "false"


def _escape(text: str) -> str:
    return text.replace("\\", "\\\\").replace('"', '\\"')


def write_default_config(path: Path | None = None) -> Path:
    """Drop a commented starter config for the user to edit."""
    target = path or config_path()
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(DEFAULT_CONFIG_TOML, encoding="utf-8")
    return target
