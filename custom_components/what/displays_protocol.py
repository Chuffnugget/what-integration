"""Validation of the monitor and layout data sent by the PC."""

import math

import re





def validate_displays(data, error):

    def fail():
        raise error("Invalid displays metadata")

    if not isinstance(data, dict):
        fail()

    for key in ("enabled", "control_enabled", "available", "busy", "pending"):

        if type(data.get(key)) is not bool:
            fail()

    if not isinstance(data.get("status"), str) or len(data["status"]) > 2048:
        fail()

    if "layout_editor" in data and type(data["layout_editor"]) is not bool:
        fail()

    monitors, layouts = data.get("monitors"), data.get("layouts")

    if (
        not isinstance(monitors, list)
        or len(monitors) > 32
        or not isinstance(layouts, list)
        or len(layouts) > 32
    ):
        fail()

    ids = set()

    for m in monitors:

        if not isinstance(m, dict):
            fail()

        identifier = m.get("id")

        if (
            not isinstance(identifier, str)
            or not re.fullmatch(r"[0-9a-f]{24}", identifier)
            or identifier in ids
        ):
            fail()

        ids.add(identifier)

        if not isinstance(m.get("name"), str) or not 1 <= len(m["name"]) <= 512:
            fail()

        for key in ("connected", "enabled", "primary"):

            if type(m.get(key)) is not bool:
                fail()

        if m.get("mode") not in ("extended", "mirrored", "disconnected"):
            fail()

        if not isinstance(m.get("group"), str) or not re.fullmatch(r"[0-9a-f]{24}", m["group"]):
            fail()

        for key in ("width", "height", "x", "y", "rotation"):

            if type(m.get(key)) is not int or abs(m[key]) > 65536:
                fail()

        if m["width"] < 0 or m["height"] < 0 or m["rotation"] not in range(1, 5):
            fail()

        if (
            type(m.get("refresh")) not in (int, float)
            or not math.isfinite(m["refresh"])
            or not 0 <= m["refresh"] <= 10000
        ):
            fail()

        modes = m.get("modes", [])

        if not isinstance(modes, list) or len(modes) > 256:
            fail()

        for mode in modes:

            if not isinstance(mode, dict):
                fail()

            for key in ("width", "height"):

                if type(mode.get(key)) is not int or not 1 <= mode[key] <= 32768:
                    fail()

            if (
                type(mode.get("refresh")) not in (int, float)
                or not math.isfinite(mode["refresh"])
                or not 0 < mode["refresh"] <= 10000
            ):
                fail()

    seen = set()

    for layout in layouts:

        if not isinstance(layout, dict):
            fail()

        identifier = layout.get("id")

        if (
            not isinstance(identifier, str)
            or not re.fullmatch(r"[a-f0-9-]{36}", identifier)
            or identifier in seen
        ):
            fail()

        seen.add(identifier)

        if (
            not isinstance(layout.get("name"), str)
            or not 1 <= len(layout["name"]) <= 64
            or type(layout.get("available")) is not bool
        ):
            fail()

    active = data.get("active_layout")

    if not isinstance(active, str) or (active != "" and active not in seen):
        fail()





def validate_layout(rows):

    """Bound untrusted service arguments before forwarding them to Windows."""

    if not isinstance(rows, list) or not 1 <= len(rows) <= 32:
        raise ValueError("Enable 1–32 monitors")

    ids, groups = set(), {}

    result = []

    for row in rows:

        if (
            not isinstance(row, dict)
            or set(row) - {"id", "group", "x", "y", "width", "height", "rotation", "refresh"}
        ):
            raise ValueError("Invalid layout fields")

        for key in ("id", "group"):

            if not isinstance(row.get(key), str) or not re.fullmatch(r"[0-9a-f]{24}", row[key]):
                raise ValueError("Invalid monitor identity")

        if row["id"] in ids:
            raise ValueError("Duplicate monitor")

        ids.add(row["id"])

        for key in ("x", "y", "width", "height", "rotation"):

            if type(row.get(key)) is not int:
                raise ValueError("Use whole-pixel geometry")

        if (
            abs(row["x"]) > 65536
            or abs(row["y"]) > 65536
            or not 1 <= row["width"] <= 32768
            or not 1 <= row["height"] <= 32768
            or row["rotation"] not in range(1, 5)
        ):
            raise ValueError("Geometry outside supported bounds")

        if (
            "refresh" in row
            and (
                type(row["refresh"]) not in (int, float)
                or not math.isfinite(row["refresh"])
                or not 0 <= row["refresh"] <= 10000
            )
        ):
            raise ValueError("Invalid refresh rate")

        geometry = tuple(row[key] for key in ("x", "y", "width", "height"))

        if row["group"] in groups and groups[row["group"]] != geometry:
            raise ValueError("Duplicate displays must share geometry")

        groups[row["group"]] = geometry

        result.append(dict(row))

    if sum(geometry[:2] == (0, 0) for geometry in groups.values()) != 1:
        raise ValueError("Choose exactly one primary desktop group")

    return result
