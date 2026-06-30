"""Raspberry Pi 4 Model B / Raspberry Pi 5 device presets.

All values are curated LOCAL constants converted from the official Raspberry Pi
mechanical drawings — generation never fetches them at runtime.

Sources (official):
  * Raspberry Pi 4 Model B — "Raspberry Pi 4 Model B mechanical drawing"
    (raspberrypi.com/documentation, mechanical PDF). Board 85 × 56 mm; four
    Ø2.7 mm (M2.5) mounting holes on a 58 × 49 mm rectangle, 3.5 mm in from the
    bottom-left datum.
  * Raspberry Pi 5 — "Raspberry Pi 5 mechanical drawing" (raspberrypi.com
    documentation). Same 85 × 56 mm outline and 58 × 49 mm mounting pattern;
    revised connector layout (no analogue AV jack; adds PCIe FFC, power button,
    fan/JST connector).

Connector ALONG-edge positions are taken to ~±0.5 mm and several are marked
``approximate`` so the enclosure ships REVIEW rather than implying a validated
connector fit. Mounting-hole geometry is exact.
"""
from __future__ import annotations

from app.cad.device_presets.devices import (
    SIDE_X_MAX,
    SIDE_X_MIN,
    SIDE_Y_MIN,
    BoardOutline,
    CableSlot,
    ConnectorCutout,
    CoolingFeature,
    DevicePreset,
    EnclosureDefaults,
    HeaderAccess,
    MountingHole,
)

# Shared 85×56 board outline and the exact 58×49 mounting pattern (both Pi 4 & 5).
_PI_BOARD = BoardOutline(length_mm=85.0, width_mm=56.0, thickness_mm=1.4, corner_radius_mm=3.0)
_PI_MOUNTS = (
    MountingHole(3.5, 3.5, 2.7, "mount_bl"),
    MountingHole(3.5, 52.5, 2.7, "mount_tl"),
    MountingHole(61.5, 3.5, 2.7, "mount_br"),
    MountingHole(61.5, 52.5, 2.7, "mount_tr"),
)
# 40-pin GPIO header along the back long edge (y ≈ 49–54.5, x ≈ 4–55).
_PI_GPIO = HeaderAccess("gpio_40pin", x_mm=3.9, y_mm=49.0, length_mm=51.0, width_mm=5.0, height_mm=8.5)


RASPBERRY_PI_4_MODEL_B = DevicePreset(
    id="raspberry_pi_4_model_b",
    display_name="Raspberry Pi 4 Model B",
    source="Raspberry Pi 4 Model B official mechanical drawing (raspberrypi.com)",
    board=_PI_BOARD,
    mounting_holes=_PI_MOUNTS,
    connectors=(
        # Bottom long edge (y = 0): USB-C power, 2× micro-HDMI, analogue AV jack.
        ConnectorCutout("usb_c_power", SIDE_Y_MIN, along_mm=7.7, width_mm=9.0, height_mm=3.5, kind="port"),
        ConnectorCutout("micro_hdmi_0", SIDE_Y_MIN, along_mm=26.0, width_mm=7.5, height_mm=4.5, kind="port"),
        ConnectorCutout("micro_hdmi_1", SIDE_Y_MIN, along_mm=39.5, width_mm=7.5, height_mm=4.5, kind="port"),
        ConnectorCutout("av_jack", SIDE_Y_MIN, along_mm=53.5, width_mm=7.0, height_mm=6.0, kind="port", approximate=True),
        # Right short edge (x = 85): USB-A ×4 (two stacks) + Ethernet.
        ConnectorCutout("usb_a_2", SIDE_X_MAX, along_mm=9.0, width_mm=15.0, height_mm=16.5, kind="port"),
        ConnectorCutout("usb_a_3", SIDE_X_MAX, along_mm=27.0, width_mm=15.0, height_mm=16.5, kind="port"),
        ConnectorCutout("ethernet", SIDE_X_MAX, along_mm=45.75, width_mm=16.0, height_mm=13.5, kind="port"),
        # Left short edge (x = 0), underside: microSD card slot.
        ConnectorCutout("microsd", SIDE_X_MIN, along_mm=28.0, width_mm=12.0, height_mm=2.5, z_base_mm=-1.5, kind="sd", approximate=True),
    ),
    header=_PI_GPIO,
    cable_slots=(
        CableSlot("csi_camera", SIDE_Y_MIN, along_mm=45.0, width_mm=2.0, height_mm=17.0, approximate=True),
        CableSlot("dsi_display", SIDE_X_MIN, along_mm=15.0, width_mm=2.0, height_mm=17.0, approximate=True),
    ),
    cooling=(CoolingFeature("lid_vents", "vent_slots", note="Lid ventilation slot pattern."),),
    enclosure=EnclosureDefaults(board_clearance_above_mm=18.0),
)


RASPBERRY_PI_5_MODEL_B = DevicePreset(
    id="raspberry_pi_5_model_b",
    display_name="Raspberry Pi 5",
    source="Raspberry Pi 5 official mechanical drawing (raspberrypi.com)",
    board=_PI_BOARD,
    mounting_holes=_PI_MOUNTS,
    connectors=(
        # Bottom long edge (y = 0): USB-C power + 2× micro-HDMI (no AV jack on Pi 5).
        ConnectorCutout("usb_c_power", SIDE_Y_MIN, along_mm=11.2, width_mm=9.0, height_mm=3.5, kind="port"),
        ConnectorCutout("micro_hdmi_0", SIDE_Y_MIN, along_mm=27.5, width_mm=7.5, height_mm=4.5, kind="port"),
        ConnectorCutout("micro_hdmi_1", SIDE_Y_MIN, along_mm=41.0, width_mm=7.5, height_mm=4.5, kind="port"),
        # Right short edge (x = 85): USB-A ×4 + Ethernet (Ethernet/USB swapped vs Pi 4).
        ConnectorCutout("ethernet", SIDE_X_MAX, along_mm=10.25, width_mm=16.0, height_mm=13.5, kind="port"),
        ConnectorCutout("usb_a_3", SIDE_X_MAX, along_mm=29.0, width_mm=15.0, height_mm=16.5, kind="port"),
        ConnectorCutout("usb_a_2", SIDE_X_MAX, along_mm=47.0, width_mm=15.0, height_mm=16.5, kind="port"),
        # Left short edge (x = 0), underside: microSD card slot.
        ConnectorCutout("microsd", SIDE_X_MIN, along_mm=28.0, width_mm=12.0, height_mm=2.5, z_base_mm=-1.5, kind="sd", approximate=True),
        # Power button on the bottom-left corner edge.
        ConnectorCutout("power_button", SIDE_Y_MIN, along_mm=2.5, width_mm=3.5, height_mm=3.0, kind="port", approximate=True),
    ),
    header=_PI_GPIO,
    cable_slots=(
        CableSlot("pcie_ffc", SIDE_X_MAX, along_mm=33.0, width_mm=2.0, height_mm=18.0, approximate=True),
        CableSlot("csi_dsi_0", SIDE_X_MIN, along_mm=20.0, width_mm=2.0, height_mm=17.0, approximate=True),
        CableSlot("csi_dsi_1", SIDE_X_MIN, along_mm=36.0, width_mm=2.0, height_mm=17.0, approximate=True),
    ),
    cooling=(
        CoolingFeature("lid_vents", "vent_slots", note="Lid ventilation slot pattern."),
        CoolingFeature("active_cooler", "fan_clearance",
                       note="Clearance reserved above the board for the official Active Cooler / fan."),
    ),
    enclosure=EnclosureDefaults(board_clearance_above_mm=22.0),
)
