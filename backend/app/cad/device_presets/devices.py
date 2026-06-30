"""Data models for deterministic single-board-computer / device enclosure presets.

A :class:`DevicePreset` is a fully local, curated description of a board (outline,
mounting holes, connector cutouts, keep-outs, cooling) plus sensible enclosure
defaults. It is converted from the official mechanical drawings into Python
constants (see ``raspberry_pi.py``) — generation NEVER calls a network/LLM. All
dimensions are millimetres in a BOARD-LOCAL frame:

    origin = board bottom-left corner (when viewed from the top),
    +X along the long (85 mm) edge, +Y along the short (56 mm) edge,
    z = 0 at the board's lower face, +Z up.

Connector cutouts are placed on a board EDGE (``side``) at a position ALONG that
edge; the enclosure builder maps board-local coordinates into the shell walls.
"""
from __future__ import annotations

from dataclasses import dataclass, field

# A board edge a connector sits on (board-local). The enclosure maps these to the
# four side walls of the shell.
SIDE_X_MIN = "x_min"   # left short edge  (x = 0)
SIDE_X_MAX = "x_max"   # right short edge (x = board_length)
SIDE_Y_MIN = "y_min"   # front long edge  (y = 0)
SIDE_Y_MAX = "y_max"   # back long edge   (y = board_width)
SIDE_TOP = "top"       # access from the lid (z up)
SIDE_BOTTOM = "bottom"  # access from underneath (z down)


@dataclass(frozen=True)
class BoardOutline:
    length_mm: float          # X extent (Pi: 85)
    width_mm: float           # Y extent (Pi: 56)
    thickness_mm: float = 1.4
    corner_radius_mm: float = 3.0


@dataclass(frozen=True)
class MountingHole:
    x_mm: float
    y_mm: float
    diameter_mm: float = 2.7      # M2.5 clearance
    name: str = "mount"


@dataclass(frozen=True)
class ConnectorCutout:
    """A port opening in a side wall.

    ``along_mm`` is the cutout CENTER measured along the named edge (X for the
    long y_* edges, Y for the short x_* edges). ``width_mm`` is the opening size
    along the edge; ``height_mm`` is the opening height; ``z_base_mm`` is the
    bottom of the opening above the board's lower face (defaults to sitting just
    below the board). ``approximate`` flags a position/size taken to a tolerance
    that should be reviewed against the real connector.
    """
    name: str
    side: str
    along_mm: float
    width_mm: float
    height_mm: float
    z_base_mm: float = 0.0
    kind: str = "port"           # port | sd | ffc | header | fan
    approximate: bool = False


@dataclass(frozen=True)
class KeepoutZone:
    """A volume above the board that must stay clear (tall components, HAT stack)."""
    name: str
    x_mm: float
    y_mm: float
    length_mm: float
    width_mm: float
    height_mm: float


@dataclass(frozen=True)
class HeaderAccess:
    """The 40-pin GPIO header (or similar) — the enclosure either opens a slot in
    the lid above it or covers it (recorded as an assumption)."""
    name: str
    x_mm: float
    y_mm: float
    length_mm: float
    width_mm: float
    height_mm: float = 8.5


@dataclass(frozen=True)
class CableSlot:
    """A ribbon-cable / FFC access slot (CSI/DSI/PCIe) — narrow opening in a wall
    or lid."""
    name: str
    side: str
    along_mm: float
    width_mm: float
    height_mm: float = 3.0
    approximate: bool = True


@dataclass(frozen=True)
class CoolingFeature:
    """A ventilation/fan feature: a slot pattern on a wall/lid, or fan clearance."""
    name: str
    kind: str                    # vent_slots | fan_clearance
    side: str = SIDE_TOP
    note: str = ""


@dataclass(frozen=True)
class EnclosureDefaults:
    wall_thickness_mm: float = 2.5
    board_clearance_below_mm: float = 3.0   # standoff height under the board
    board_clearance_above_mm: float = 16.0  # space for the tallest connector/HAT
    lid_clearance_mm: float = 0.4           # gap between lid and shell
    standoff_outer_diameter_mm: float = 6.0
    standoff_screw_diameter_mm: float = 2.5
    connector_clearance_mm: float = 0.75    # margin added around each port opening
    lid_type: str = "removable"             # removable | snap_fit | screw


@dataclass(frozen=True)
class DevicePreset:
    id: str
    display_name: str
    source: str                              # citation to the official drawing
    board: BoardOutline
    mounting_holes: tuple[MountingHole, ...]
    connectors: tuple[ConnectorCutout, ...]
    header: HeaderAccess | None = None
    cable_slots: tuple[CableSlot, ...] = field(default_factory=tuple)
    keepouts: tuple[KeepoutZone, ...] = field(default_factory=tuple)
    cooling: tuple[CoolingFeature, ...] = field(default_factory=tuple)
    enclosure: EnclosureDefaults = field(default_factory=EnclosureDefaults)

    # --- convenience for the validator / builder -------------------------------
    @property
    def micro_hdmi_count(self) -> int:
        return sum(1 for c in self.connectors if "hdmi" in c.name.lower())

    def has_connector(self, *keywords: str) -> bool:
        kws = [k.lower() for k in keywords]
        return any(any(k in c.name.lower() or k in c.kind.lower() for k in kws)
                   for c in self.connectors)

    @property
    def required_port_names(self) -> tuple[str, ...]:
        return tuple(c.name for c in self.connectors if c.kind == "port")
