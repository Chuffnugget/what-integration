"""Validation of the iCUE Automation state sent by the PC."""
import re


def validate_automation(data, error):
    def fail():
        raise error("Invalid iCUE automation metadata")

    def text(value, limit=256):
        return isinstance(value, str) and 0 < len(value) <= limit

    if (
        not isinstance(data, dict)
        or any(type(data.get(k)) is not bool for k in ("enabled", "connected", "control_enabled"))
    ):
        fail()
    if not text(data.get("status"), 2000):
        fail()
    if "lighting" in data:
        validate_lighting(data["lighting"], error)
    controls = data.get("controls")
    if not isinstance(controls, list) or len(controls) > 193:
        fail()
    seen = set()
    for c in controls:
        if (
            not isinstance(c, dict)
            or not text(c.get("id"))
            or not re.fullmatch(r"profile|(?:cooling|eq|dpi)_[a-f0-9]{24}", c["id"])
        ):
            fail()
        if (
            c["id"] in seen
            or not text(c.get("name"))
            or not text(c.get("device"))
            or type(c.get("confirmed")) is not bool
        ):
            fail()
        seen.add(c["id"])
        choices = c.get("options")
        if not isinstance(choices, list) or not 1 <= len(choices) <= 256:
            fail()
        ids = set()
        for choice in choices:
            if (
                not isinstance(choice, dict)
                or not text(choice.get("id"))
                or not text(choice.get("name"))
                or choice["id"] in ids
            ):
                fail()
            ids.add(choice["id"])
        if c.get("current") is not None and (not text(c["current"]) or c["current"] not in ids):
            fail()
        if not c["confirmed"] and c.get("current") is not None:
            fail()


LIGHTING_PARAMETERS = {
    "StaticColor": {"Color1"}, "RainbowWave": {"Speed", "Direction"}, "Rainbow": {"Speed"},
    "ColorShift": {"Speed", "RandomColor", "Color1", "Color2"},
    "ColorWave": {"Speed", "Direction", "RandomColor", "Color1", "Color2"},
    "TypeLighting": {"Speed", "RandomColor", "Color1", "Color2"},
    "ColorPulse": {"Speed", "RandomColor", "Color1", "Color2"},
    "Rain": {"Speed", "RandomColor", "Color1", "Color2"}, "Visor": {"Speed", "RandomColor", "Color1", "Color2"},
    "SpiralRainbow": {"Speed", "Rotation"},
}


def validate_lighting(state, error):
    def fail():
        raise error("Invalid iCUE Lighting Link metadata")
    if not isinstance(state, dict) or type(state.get("available")) is not bool:
        fail()
    effects = state.get("effects")
    if not isinstance(effects, list) or len(effects) > 10:
        fail()
    seen = set()
    for item in effects:
        if (
            not isinstance(item, dict)
            or not isinstance(item.get("id"), str)
            or item["id"] not in LIGHTING_PARAMETERS
            or item["id"] in seen
        ):
            fail()
        seen.add(item["id"])
        if not isinstance(item.get("name"), str) or not 0 < len(item["name"]) <= 128:
            fail()
        params = item.get("parameters")
        if (
            not isinstance(params, list)
            or any(not isinstance(p, str) for p in params)
            or len(params) != len(set(params))
            or set(params) != LIGHTING_PARAMETERS[item["id"]]
        ):
            fail()
    active = state.get("effect")
    if active is not None and (not isinstance(active, str) or active not in LIGHTING_PARAMETERS):
        fail()
    params = state.get("parameters")
    if not isinstance(params, dict) or set(params) - LIGHTING_PARAMETERS.get(active, set()):
        fail()
    for key, value in params.items():
        if key.startswith("Color"):
            if not isinstance(value, str) or not re.fullmatch(r"#[0-9a-fA-F]{6}", value):
                fail()
        elif key == "RandomColor":
            if type(value) is not bool:
                fail()
        elif (
            type(value) is not int
            or not 0 <= value <= (2 if key == "Speed" else 3 if key == "Direction" else 1)
        ):
            fail()
