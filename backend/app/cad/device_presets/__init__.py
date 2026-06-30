"""Deterministic device enclosure preset library.

Local, curated single-board-computer presets (board outline, mounting holes,
connector cutouts, cooling) used to build accurate enclosures WITHOUT any LLM /
network call. Look up a preset by id or detect one from a prompt.
"""
from __future__ import annotations

import re

from app.cad.device_presets.boards import (
    ARDUINO_NANO,
    ARDUINO_UNO_R3,
    ESP32_DEVKIT_V1,
    JETSON_NANO_DEVKIT,
    JETSON_ORIN_NANO,
)
from app.cad.device_presets.devices import DevicePreset
from app.cad.device_presets.raspberry_pi import (
    RASPBERRY_PI_4_MODEL_B,
    RASPBERRY_PI_5_MODEL_B,
)

DEVICE_PRESETS: dict[str, DevicePreset] = {
    p.id: p for p in (
        RASPBERRY_PI_4_MODEL_B, RASPBERRY_PI_5_MODEL_B,
        ARDUINO_UNO_R3, ARDUINO_NANO, ESP32_DEVKIT_V1,
        JETSON_ORIN_NANO, JETSON_NANO_DEVKIT,
    )
}

# Map a preset id -> the registered enclosure object_type. The two Raspberry Pi
# boards keep dedicated object types (backward-compat); every other board uses the
# generic, preset-driven ``board_enclosure`` template.
PRESET_OBJECT_TYPE = {
    RASPBERRY_PI_4_MODEL_B.id: "rpi4_enclosure",
    RASPBERRY_PI_5_MODEL_B.id: "rpi5_enclosure",
}
OBJECT_TYPE_PRESET = {v: k for k, v in PRESET_OBJECT_TYPE.items()}


def object_type_for_preset(preset_id: str) -> str:
    """The registered enclosure object_type that builds a given board preset."""
    return PRESET_OBJECT_TYPE.get(preset_id, "board_enclosure")


def get_preset(preset_id: str) -> DevicePreset | None:
    return DEVICE_PRESETS.get(preset_id)


def preset_for_object_type(object_type: str | None) -> DevicePreset | None:
    pid = OBJECT_TYPE_PRESET.get(object_type or "")
    return DEVICE_PRESETS.get(pid) if pid else None


# Pi 5 must be matched before the generic "raspberry pi" / "pi 4" patterns.
_PI5 = re.compile(r"\b(raspberry\s*pi\s*5|rpi\s*5|pi\s*5)\b", re.I)
_PI4 = re.compile(r"\b(raspberry\s*pi\s*4|rpi\s*4|pi\s*4)\b", re.I)
_PI_GENERIC = re.compile(r"\b(raspberry\s*pi|rpi)\b", re.I)
# Other boards (most specific first). Jetson Orin (incl. "Orin Nano Super", "Orin
# NX") maps to the Orin Nano carrier; plain "Jetson Nano" maps to the original
# Nano dev kit. Typo-tolerant on the brand/board words.
_BOARD_PATTERNS = [
    (re.compile(r"\b(jetson\s*)?orin\s*(nano|nx)(\s*super)?\b", re.I), JETSON_ORIN_NANO),
    (re.compile(r"\bjetson\s*nano\b", re.I), JETSON_NANO_DEVKIT),
    (re.compile(r"\bjetson\b", re.I), JETSON_ORIN_NANO),
    (re.compile(r"\besp[\s-]*32\s*dev[\s-]*kit\b|\besp[\s-]*32\b", re.I), ESP32_DEVKIT_V1),
    (re.compile(r"\barduino\s*uno\b", re.I), ARDUINO_UNO_R3),
    (re.compile(r"\barduino\s*nano\b", re.I), ARDUINO_NANO),
]


def detect_device_preset(prompt: str | None) -> DevicePreset | None:
    """Return the board preset a prompt asks for (Raspberry Pi 4/5, Arduino Uno/Nano,
    ESP32 DevKit, Jetson Orin Nano), else None.

    A bare "Raspberry Pi" (no model number) defaults to the Pi 4 Model B — the most
    common board — and the assumption is surfaced by the builder."""
    t = prompt or ""
    if _PI5.search(t):
        return RASPBERRY_PI_5_MODEL_B
    if _PI4.search(t):
        return RASPBERRY_PI_4_MODEL_B
    for pat, preset in _BOARD_PATTERNS:
        if pat.search(t):
            return preset
    if _PI_GENERIC.search(t):
        return RASPBERRY_PI_4_MODEL_B
    return None


__all__ = [
    "DEVICE_PRESETS",
    "PRESET_OBJECT_TYPE",
    "OBJECT_TYPE_PRESET",
    "RASPBERRY_PI_4_MODEL_B",
    "RASPBERRY_PI_5_MODEL_B",
    "ARDUINO_UNO_R3",
    "ARDUINO_NANO",
    "ESP32_DEVKIT_V1",
    "JETSON_ORIN_NANO",
    "JETSON_NANO_DEVKIT",
    "object_type_for_preset",
    "get_preset",
    "preset_for_object_type",
    "detect_device_preset",
]
