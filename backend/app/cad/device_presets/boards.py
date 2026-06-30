"""Curated local board presets beyond the Raspberry Pi family.

Arduino Uno R3, Arduino Nano, ESP32 DevKit V1, and the Jetson Orin Nano Developer
Kit — board outline, mounting holes, and the main connector cutouts, converted from
each board's published mechanical reference into LOCAL constants (no network/LLM at
runtime). Connector positions are to a tolerance and several are flagged
``approximate`` so an enclosure ships REVIEW rather than implying a validated fit;
board outline and the USB-edge are well known.

Sources (official references):
  * Arduino Uno R3 — Arduino official board mechanical reference (store.arduino.cc).
  * Arduino Nano — Arduino Nano mechanical reference.
  * ESP32 DevKit V1 (DOIT, 30-pin) — common module mechanical drawing; exact
    mounting-hole presence varies by clone, so retention is by corner posts.
  * Jetson Orin Nano Developer Kit — NVIDIA carrier-board mechanical reference.
"""
from __future__ import annotations

from app.cad.device_presets.devices import (
    SIDE_X_MAX,
    SIDE_X_MIN,
    SIDE_Y_MIN,
    BoardOutline,
    ConnectorCutout,
    CoolingFeature,
    DevicePreset,
    EnclosureDefaults,
    MountingHole,
)

# --- Arduino Uno R3 --------------------------------------------------------
# Board 68.6 × 53.4 mm; USB-B + barrel power jack on the left short edge. The Uno's
# four mounting holes sit on the well-known (irregular) R3 shield pattern.
ARDUINO_UNO_R3 = DevicePreset(
    id="arduino_uno_r3",
    display_name="Arduino Uno R3",
    source="Arduino Uno R3 official mechanical reference (store.arduino.cc)",
    board=BoardOutline(length_mm=68.6, width_mm=53.4, thickness_mm=1.6, corner_radius_mm=3.0),
    mounting_holes=(
        MountingHole(13.97, 2.54, 3.2, "mount_1"),
        MountingHole(66.04, 7.62, 3.2, "mount_2"),
        MountingHole(66.04, 35.56, 3.2, "mount_3"),
        MountingHole(15.24, 50.80, 3.2, "mount_4"),
    ),
    connectors=(
        ConnectorCutout("usb_b", SIDE_X_MIN, along_mm=10.5, width_mm=12.0, height_mm=11.0, kind="port"),
        ConnectorCutout("dc_barrel_jack", SIDE_X_MIN, along_mm=41.0, width_mm=9.5, height_mm=11.0, kind="port", approximate=True),
    ),
    cooling=(CoolingFeature("lid_vents", "vent_slots", note="Lid ventilation slots."),),
    enclosure=EnclosureDefaults(board_clearance_above_mm=16.0),
)

# --- Arduino Nano ----------------------------------------------------------
# Tiny 45 × 18 mm board, mini/micro-USB on one short edge, no PCB mounting holes
# (breadboard part) → retention by corner posts.
ARDUINO_NANO = DevicePreset(
    id="arduino_nano",
    display_name="Arduino Nano",
    source="Arduino Nano official mechanical reference",
    board=BoardOutline(length_mm=45.0, width_mm=18.0, thickness_mm=1.6, corner_radius_mm=1.5),
    mounting_holes=(
        MountingHole(3.0, 3.0, 0.0, "retain_bl"),
        MountingHole(3.0, 15.0, 0.0, "retain_tl"),
        MountingHole(42.0, 3.0, 0.0, "retain_br"),
        MountingHole(42.0, 15.0, 0.0, "retain_tr"),
    ),
    connectors=(
        ConnectorCutout("usb", SIDE_X_MIN, along_mm=9.0, width_mm=8.0, height_mm=4.0, kind="port", approximate=True),
    ),
    enclosure=EnclosureDefaults(board_clearance_above_mm=10.0, standoff_screw_diameter_mm=0.0),
)

# --- ESP32 DevKit V1 (30-pin) ----------------------------------------------
# Board ~51.5 × 28.5 mm, micro-USB on one short edge. Mounting-hole presence varies
# by clone, so the board is retained by corner posts (flagged) → REVIEW.
ESP32_DEVKIT_V1 = DevicePreset(
    id="esp32_devkit_v1",
    display_name="ESP32 DevKit V1",
    source="ESP32 DevKit V1 (30-pin) common module mechanical drawing",
    board=BoardOutline(length_mm=51.5, width_mm=28.5, thickness_mm=1.6, corner_radius_mm=1.5),
    mounting_holes=(
        MountingHole(3.0, 3.0, 0.0, "retain_bl"),
        MountingHole(3.0, 25.5, 0.0, "retain_tl"),
        MountingHole(48.5, 3.0, 0.0, "retain_br"),
        MountingHole(48.5, 25.5, 0.0, "retain_tr"),
    ),
    connectors=(
        ConnectorCutout("micro_usb", SIDE_X_MIN, along_mm=14.25, width_mm=9.0, height_mm=4.5, kind="port", approximate=True),
    ),
    enclosure=EnclosureDefaults(board_clearance_above_mm=12.0, standoff_screw_diameter_mm=0.0),
)

# --- Jetson Orin Nano Developer Kit ----------------------------------------
# Carrier board ~103 × 90.5 mm; dense rear I/O (DC jack, USB-C, 2× USB-A, HDMI,
# Ethernet). Connector positions are approximate → REVIEW.
JETSON_ORIN_NANO = DevicePreset(
    id="jetson_orin_nano_devkit",
    display_name="Jetson Orin Nano Developer Kit",
    source="NVIDIA Jetson Orin Nano Developer Kit carrier mechanical reference",
    board=BoardOutline(length_mm=103.0, width_mm=90.5, thickness_mm=1.6, corner_radius_mm=3.0),
    mounting_holes=(
        MountingHole(4.0, 4.0, 3.2, "mount_bl"),
        MountingHole(4.0, 86.5, 3.2, "mount_tl"),
        MountingHole(99.0, 4.0, 3.2, "mount_br"),
        MountingHole(99.0, 86.5, 3.2, "mount_tr"),
    ),
    connectors=(
        ConnectorCutout("dc_jack", SIDE_X_MAX, along_mm=12.0, width_mm=10.0, height_mm=11.0, kind="port", approximate=True),
        ConnectorCutout("usb_c", SIDE_X_MAX, along_mm=28.0, width_mm=9.5, height_mm=4.0, kind="port", approximate=True),
        ConnectorCutout("hdmi", SIDE_X_MAX, along_mm=44.0, width_mm=16.0, height_mm=7.0, kind="port", approximate=True),
        ConnectorCutout("usb_a_stack", SIDE_X_MAX, along_mm=66.0, width_mm=16.0, height_mm=16.0, kind="port", approximate=True),
        ConnectorCutout("ethernet", SIDE_X_MAX, along_mm=84.0, width_mm=16.0, height_mm=13.5, kind="port", approximate=True),
        ConnectorCutout("microsd", SIDE_Y_MIN, along_mm=52.0, width_mm=12.0, height_mm=2.5, z_base_mm=-1.5, kind="sd", approximate=True),
    ),
    cooling=(
        CoolingFeature("lid_vents", "vent_slots", note="Lid ventilation slots."),
        CoolingFeature("module_fan", "fan_clearance",
                       note="Clearance reserved for the module heatsink/fan."),
    ),
    enclosure=EnclosureDefaults(board_clearance_above_mm=30.0),
)


# --- Jetson Nano Developer Kit (original, B01) -----------------------------
# Carrier board 100 × 79 mm; rear I/O: barrel jack, HDMI, DisplayPort, 4× USB-A,
# Gigabit Ethernet, micro-USB. Connector positions are approximate → REVIEW.
JETSON_NANO_DEVKIT = DevicePreset(
    id="jetson_nano_developer_kit",
    display_name="Jetson Nano Developer Kit",
    source="NVIDIA Jetson Nano Developer Kit (B01) carrier mechanical reference",
    board=BoardOutline(length_mm=100.0, width_mm=79.0, thickness_mm=1.6, corner_radius_mm=3.0),
    mounting_holes=(
        MountingHole(4.0, 4.0, 3.2, "mount_bl"),
        MountingHole(4.0, 75.0, 3.2, "mount_tl"),
        MountingHole(86.0, 4.0, 3.2, "mount_br"),
        MountingHole(86.0, 75.0, 3.2, "mount_tr"),
    ),
    connectors=(
        ConnectorCutout("dc_jack", SIDE_X_MAX, along_mm=10.0, width_mm=10.0, height_mm=11.0, kind="port", approximate=True),
        ConnectorCutout("hdmi", SIDE_X_MAX, along_mm=26.0, width_mm=16.0, height_mm=7.0, kind="port", approximate=True),
        ConnectorCutout("displayport", SIDE_X_MAX, along_mm=42.0, width_mm=16.0, height_mm=7.0, kind="port", approximate=True),
        ConnectorCutout("usb_a_stack", SIDE_X_MAX, along_mm=60.0, width_mm=16.0, height_mm=16.0, kind="port", approximate=True),
        ConnectorCutout("ethernet", SIDE_X_MAX, along_mm=74.0, width_mm=16.0, height_mm=13.5, kind="port", approximate=True),
        ConnectorCutout("micro_usb", SIDE_Y_MIN, along_mm=20.0, width_mm=8.0, height_mm=4.0, kind="port", approximate=True),
    ),
    cooling=(
        CoolingFeature("lid_vents", "vent_slots", note="Lid ventilation slots."),
        CoolingFeature("module_fan", "fan_clearance",
                       note="Clearance reserved for the module heatsink/fan."),
    ),
    enclosure=EnclosureDefaults(board_clearance_above_mm=30.0),
)
