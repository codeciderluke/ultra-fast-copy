"""Default configuration values and the speed presets."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from .. import APP_SLUG
from ..core.models import (
    ConflictPolicy,
    ScanMode,
    SpeedPreset,
    SymlinkPolicy,
    TransferOptions,
    VerifyMode,
)
from ..utils.system import app_data_dir

CONFIG_FILENAME = "config.toml"


def config_path() -> Path:
    """%APPDATA%\\UltraFastCopy\\config.toml"""
    return app_data_dir(APP_SLUG) / CONFIG_FILENAME


DEFAULT_CONFIG: dict[str, dict[str, Any]] = {
    "transfer": {
        "workers": 0,  # 0 -> auto tune from the source/destination pair
        "buffer_size": "4MiB",
        "large_file_buffer_size": "8MiB",
        "verify": VerifyMode.SIZE.value,
        "conflict": ConflictPolicy.SKIP.value,
        "retry_count": 3,
        "prescan": True,
        "checkpoint": True,
        "preserve_times": True,
        "preserve_permissions": False,
        "include_hidden": True,
        "include_system": False,
        "symlink_policy": SymlinkPolicy.SKIP.value,
        "bandwidth_limit": "",
    },
    "ui": {
        "theme": "dark",
        "language": "ko",
        "show_advanced_options": False,
        "preset": SpeedPreset.BALANCED.value,
        "confirm_on_exit": True,
    },
    "logging": {
        "level": "INFO",
        "retention_days": 30,
    },
}

DEFAULT_CONFIG_TOML = """# Ultra Fast Copy configuration
# %APPDATA%\\UltraFastCopy\\config.toml
# Command line arguments take precedence over everything in this file.

[transfer]
workers = 0                  # 0 = auto tune from the source and destination devices
buffer_size = "4MiB"
large_file_buffer_size = "8MiB"
verify = "size"              # none | size | mtime_size | xxhash | sha256
conflict = "skip"            # skip | overwrite | overwrite_if_newer | overwrite_if_different | rename | ask
retry_count = 3
prescan = true               # false = start copying while still scanning
checkpoint = true            # write a resume checkpoint
preserve_times = true
preserve_permissions = false
include_hidden = true
include_system = false
symlink_policy = "skip"      # skip | copy_link | follow
bandwidth_limit = ""         # e.g. "10MiB" for 10 MiB/s; empty = unlimited

[ui]
theme = "dark"
language = "ko"
show_advanced_options = false
preset = "balanced"          # fast | balanced | safe | custom
confirm_on_exit = true

[logging]
level = "INFO"               # DEBUG | INFO | WARNING | ERROR
retention_days = 30
"""


def preset_options(preset: SpeedPreset, base: TransferOptions | None = None) -> TransferOptions:
    """Apply a speed preset on top of `base`.

    Fast trades verification for throughput; Safe hashes every file and keeps a
    checkpoint. Balanced is the default because it catches truncation without
    reading everything twice.
    """
    from dataclasses import replace

    options = base or TransferOptions()
    match preset:
        case SpeedPreset.FAST:
            return replace(
                options,
                verify=VerifyMode.SIZE,
                workers=None,
                preserve_times=False,
                preserve_permissions=False,
                use_checkpoint=False,
                scan_mode=ScanMode.STREAMING,
            )
        case SpeedPreset.BALANCED:
            return replace(
                options,
                verify=VerifyMode.MTIME_SIZE,
                workers=None,
                preserve_times=True,
                use_checkpoint=True,
                scan_mode=ScanMode.PRESCAN,
            )
        case SpeedPreset.SAFE:
            return replace(
                options,
                verify=VerifyMode.XXHASH,
                workers=None,
                preserve_times=True,
                preserve_permissions=True,
                use_checkpoint=True,
                scan_mode=ScanMode.PRESCAN,
                retry_count=5,
            )
        case _:
            return options


def detect_preset(options: TransferOptions) -> SpeedPreset:
    """Reverse lookup so the GUI can show which preset the options match."""
    for preset in (SpeedPreset.FAST, SpeedPreset.BALANCED, SpeedPreset.SAFE):
        candidate = preset_options(preset)
        if (
            candidate.verify is options.verify
            and candidate.scan_mode is options.scan_mode
            and candidate.use_checkpoint == options.use_checkpoint
            and candidate.preserve_times == options.preserve_times
        ):
            return preset
    return SpeedPreset.CUSTOM
