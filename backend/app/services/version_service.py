"""Design version history: an immutable snapshot after every successful edit,
so the studio can show history and restore an earlier version.

Snapshotting is hooked into the 4 edit-mutation entry points in
``design_service`` (``regenerate_design``, ``modify_design``,
``apply_spec_edit``, ``apply_plan_edit``) — the complete set of paths that can
change a design's buildable state after creation. Initial generation is never
snapshotted directly; instead, the FIRST edit ever applied to a design also
captures the PRE-edit state as version 1 ("Initial generation"), via
:func:`ensure_baseline`, so history always has a true baseline to restore to.
A design nobody has ever edited has no version rows — there is nothing to
show history for yet.

Restoring replays a snapshot through the SAME apply_spec_edit / apply_plan_edit
validation pipeline as any other edit (see :func:`restore_version`), so
restored geometry is guaranteed real and valid, never a raw unvalidated write
of old JSON back onto the row.
"""
from __future__ import annotations

from sqlalchemy.orm import Session

from app.models import Design, DesignVersion


def _round(v):
    try:
        return round(float(v), 4)
    except (TypeError, ValueError):
        return v


def diff_spec_json(old: dict | None, new: dict | None) -> list[dict]:
    """Coarse old/new field diff between two DesignSpec JSON payloads, for the
    edit-response / version-history "what changed" display. Best-effort: scalar
    top-level fields are compared directly; dimensions/holes are compared by
    key/index so a single changed dimension or hole shows as one entry rather
    than the whole block changing."""
    old = old or {}
    new = new or {}
    out: list[dict] = []

    for key in ("object_type", "fillet_radius", "chamfer_size", "manufacturing_method", "material"):
        ov, nv = old.get(key), new.get(key)
        if _round(ov) != _round(nv):
            out.append({"field": key, "old": ov, "new": nv})

    old_dims = old.get("dimensions") or {}
    new_dims = new.get("dimensions") or {}
    for key in sorted(set(old_dims) | set(new_dims)):
        ov, nv = old_dims.get(key), new_dims.get(key)
        if _round(ov) != _round(nv):
            out.append({"field": f"dimensions.{key}", "old": ov, "new": nv})

    old_holes = old.get("holes") or []
    new_holes = new.get("holes") or []
    if len(old_holes) != len(new_holes):
        out.append({"field": "holes.count", "old": len(old_holes), "new": len(new_holes)})
    for i in range(min(len(old_holes), len(new_holes))):
        oh, nh = old_holes[i] or {}, new_holes[i] or {}
        for k in ("diameter", "x", "y", "hole_type"):
            if _round(oh.get(k)) != _round(nh.get(k)):
                out.append({"field": f"holes[{i}].{k}", "old": oh.get(k), "new": nh.get(k)})

    return out


def diff_plan_json(old: dict | None, new: dict | None) -> list[dict]:
    """Coarse old/new diff between two CadPlan feature-graph JSON payloads:
    added/removed features by id, and per-param changes on features present in
    both. Best-effort, mirrors :func:`diff_spec_json` for CadPlan-built parts."""
    old = old or {}
    new = new or {}
    old_feats = {f.get("id"): f for f in (old.get("features") or [])}
    new_feats = {f.get("id"): f for f in (new.get("features") or [])}
    out: list[dict] = []
    for fid in sorted(set(old_feats) - set(new_feats)):
        out.append({"field": f"feature[{fid}]", "old": "present", "new": "removed"})
    for fid in sorted(set(new_feats) - set(old_feats)):
        out.append({"field": f"feature[{fid}]", "old": "absent", "new": "added"})
    for fid in sorted(set(old_feats) & set(new_feats)):
        op = old_feats[fid].get("params") or {}
        np = new_feats[fid].get("params") or {}
        for k in sorted(set(op) | set(np)):
            ov, nv = op.get(k), np.get(k)
            if _round(ov) != _round(nv):
                out.append({"field": f"feature[{fid}].{k}", "old": ov, "new": nv})
    return out


def _next_version_number(db: Session, design_id: str) -> int:
    last = (
        db.query(DesignVersion)
        .filter(DesignVersion.design_id == design_id)
        .order_by(DesignVersion.version_number.desc())
        .first()
    )
    return (last.version_number + 1) if last else 1


def _snapshot_payload(design: Design) -> tuple[dict | None, dict | None]:
    """(spec_snapshot, plan_snapshot) for the design's CURRENT persisted state.
    Exactly one is populated, matching ``design.route``."""
    if design.spec_json:
        return dict(design.spec_json), None
    plan = (design.semantic_json or {}).get("cad_plan")
    return None, (dict(plan) if plan else None)


def _create_version(
    db: Session, design: Design, *, edit_kind: str, summary: str, diff: list[dict] | None
) -> DesignVersion:
    spec_snapshot, plan_snapshot = _snapshot_payload(design)
    version = DesignVersion(
        design_id=design.id,
        version_number=_next_version_number(db, design.id),
        edit_kind=edit_kind,
        summary=summary,
        spec_snapshot=spec_snapshot,
        plan_snapshot=plan_snapshot,
        spec_hash=design.spec_hash,
        diff_json=diff or None,
    )
    db.add(version)
    db.commit()
    db.refresh(version)
    return version


def ensure_baseline(db: Session, design: Design) -> None:
    """Capture the design's CURRENT state as version 1 the first time it is
    ever edited. Call this BEFORE applying an edit's mutation — no-op once any
    version already exists, or if there's nothing buildable yet to snapshot."""
    exists = db.query(DesignVersion.id).filter(DesignVersion.design_id == design.id).first()
    if exists:
        return
    if not design.spec_json and not (design.semantic_json or {}).get("cad_plan"):
        return
    _create_version(db, design, edit_kind="create", summary="Initial generation", diff=None)


def snapshot(
    db: Session, design: Design, *, edit_kind: str, summary: str, diff: list[dict] | None = None
) -> DesignVersion:
    """Capture the design's CURRENT (post-edit, already-committed) state as a
    new version. Call this AFTER an edit function's own db.commit()."""
    return _create_version(db, design, edit_kind=edit_kind, summary=summary, diff=diff)


def list_versions(db: Session, design_id: str) -> list[DesignVersion]:
    return (
        db.query(DesignVersion)
        .filter(DesignVersion.design_id == design_id)
        .order_by(DesignVersion.version_number.desc())
        .all()
    )


def get_latest_version(db: Session, design_id: str) -> DesignVersion | None:
    return (
        db.query(DesignVersion)
        .filter(DesignVersion.design_id == design_id)
        .order_by(DesignVersion.version_number.desc())
        .first()
    )


def get_version(db: Session, design_id: str, version_id: str) -> DesignVersion | None:
    return (
        db.query(DesignVersion)
        .filter(DesignVersion.design_id == design_id, DesignVersion.id == version_id)
        .first()
    )


class VersionRestoreError(Exception):
    """A version's snapshot can't be restored (malformed / no longer valid)."""


def restore_version(db: Session, design: Design, version: DesignVersion) -> Design:
    """Restore an earlier version by replaying its snapshot through the SAME
    validation/regeneration pipeline as any other edit — never a raw write of
    old JSON back onto the row. Raises ``VersionRestoreError`` (never applies a
    partial change) if the snapshot no longer validates; raises
    ``design_service.CriticalEditRejected`` if it validates but builds
    critically-failed geometry."""
    from app.services import design_service

    if version.spec_snapshot is not None:
        from app.schemas.design_spec import DesignSpec

        try:
            new_spec = DesignSpec(**version.spec_snapshot)
        except Exception as exc:  # noqa: BLE001
            raise VersionRestoreError(
                f"Version {version.version_number} is no longer valid: {exc}"
            ) from exc
        return design_service.apply_spec_edit(
            db, design, new_spec,
            note=f"Restored version {version.version_number}",
            guard_critical=True,
            edit_kind="restore",
        )

    if version.plan_snapshot is not None:
        from app.cad.plan.schema import CadPlan

        try:
            new_plan = CadPlan(**version.plan_snapshot)
        except Exception as exc:  # noqa: BLE001
            raise VersionRestoreError(
                f"Version {version.version_number} is no longer valid: {exc}"
            ) from exc
        return design_service.apply_plan_edit(
            db, design, new_plan,
            note=f"Restored version {version.version_number}",
            guard_critical=True,
            edit_kind="restore",
        )

    raise VersionRestoreError(f"Version {version.version_number} has no buildable snapshot.")
