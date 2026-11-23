"""OEM / VGC variant discovery and selection."""

from __future__ import annotations

import re
from pathlib import Path

from .errors import ToolError


def _variants(folder: Path) -> list[str]:
    if not folder.is_dir():
        return []
    prefix = "dynamic_partitions_op_list_"
    return sorted(path.name[len(prefix) :] for path in folder.glob(f"{prefix}*"))


def oem_variants(package: Path) -> list[str]:
    return _variants(package / "oem")


def vgc_variants(package: Path) -> list[str]:
    return _variants(package / "vgc")


def model_candidates(props: dict[str, str]) -> list[str]:
    """Return likely ``fastboot oem product-model`` values for this OTA.

    ``pre-device`` is the OTA device (for example ``PD2171``) while vivo
    ``hardware-device`` entries carry a market suffix (``PD2171MA``), and the
    phone itself may report either form.  Both forms are accepted.
    """
    tokens: list[str] = []
    for key in ("pre-device", "post-device", "hardware-device"):
        for token in re.split(r"[|,\s]+", props.get(key, "") or ""):
            token = token.strip()
            if not token:
                continue
            for value in (token, re.sub(r"[A-Z]{1,2}$", "", token)):
                if value and value not in tokens and any(ch.isdigit() for ch in value):
                    tokens.append(value)
    return tokens


def choose_oem(
    requested: str,
    package: Path,
    props: dict[str, str],
    device_oem: str | None = None,
) -> tuple[str, str]:
    variants = oem_variants(package)
    if not variants:
        return "none", "OTA has no OEM variants"
    if requested == "none":
        return "none", "oem partition left empty in super"
    if requested != "auto":
        if requested not in variants:
            raise ToolError(
                f"OEM variant {requested} not in OTA. Available: {', '.join(variants)}"
            )
        note = "explicit"
        if device_oem and device_oem != requested:
            note = f"explicit, connected device reports {device_oem}"
        return requested, note

    device_note = ""
    if device_oem:
        if device_oem in variants:
            return device_oem, f"connected device identity {device_oem}"
        device_note = f"connected device identity {device_oem} not in OTA; "

    models = model_candidates(props)
    for model in models:
        for suffix in (
            "_CN-ZH_FULL_SC_NULL",
            "_CN-ZH_FULL_SC_GMS",
            "_CN-ZH_FULL_SC_CT-BMW",
        ):
            candidate = model + suffix
            if candidate in variants:
                return candidate, device_note + f"auto, {model} FULL_SC variant"
    for model in models:
        for variant in variants:
            if variant.startswith(model + "_") and "FULL_SC" in variant:
                return variant, device_note + f"auto, first {model} FULL_SC variant"
    for model in models:
        for variant in variants:
            if variant.startswith(model + "_"):
                return variant, device_note + f"auto, first {model} variant"
    return variants[0], device_note + "auto, first variant"


def choose_vgc(
    requested: str,
    package: Path,
    device_vgc: str | None = None,
) -> tuple[str, str]:
    variants = vgc_variants(package)
    if not variants:
        return "none", "OTA has no vgc variants"
    if requested == "none":
        return "none", "vgc partition omitted from super"
    if requested != "auto":
        if requested not in variants:
            raise ToolError(
                f"VGC variant {requested} not in OTA. Available: {', '.join(variants)}"
            )
        note = "explicit"
        if device_vgc and device_vgc != requested:
            note = f"explicit, connected device reports {device_vgc}"
        return requested, note

    device_note = ""
    if device_vgc:
        if device_vgc in variants:
            return device_vgc, f"connected device identity {device_vgc}"
        matches = [item for item in variants if item.startswith(device_vgc + "_")]
        if not matches:
            matches = [item for item in variants if item.startswith(device_vgc)]
        if matches:
            return matches[0], f"connected device reports {device_vgc}"
        device_note = f"connected device VGC {device_vgc} not in OTA; "

    preferred = [item for item in variants if item.startswith("V000")]
    chosen = preferred[0] if preferred else variants[0]
    return chosen, device_note + f"auto, {chosen}"
