"""Validation of the iCUE SDK device data sent by the PC."""
def validate_icue(data, error):
    def fail():
        raise error("Invalid iCUE metadata")
    if (
        not isinstance(data, dict)
        or any(type(data.get(k)) is not bool for k in ("enabled", "connected", "control_enabled"))
    ):
        fail()
    if not isinstance(data.get("status"), str) or len(data["status"]) > 1024:
        fail()
    devices = data.get("devices")
    if not isinstance(devices, list) or len(devices) > 64:
        fail()
    seen = set()
    for d in devices:
        if not isinstance(d, dict):
            fail()
        for key in ("id", "model"):
            if not isinstance(d.get(key), str) or len(d[key]) > 128:
                fail()
        if not d["id"] or d["id"] in seen:
            fail()
        seen.add(d["id"])
        if (
            type(d.get("channels")) is not int
            or not 0 <= d["channels"] <= 512
            or type(d.get("type")) is not int
        ):
            fail()
        leds = d.get("leds")
        properties = d.get("properties")
        if (
            not isinstance(leds, list)
            or len(leds) > 512
            or not isinstance(properties, list)
            or len(properties) > 7
        ):
            fail()
        identifiers = set()
        for led in leds:
            if (
                not isinstance(led, dict)
                or type(led.get("id")) is not int
                or not 0 <= led["id"] <= 4294967295
                or led["id"] in identifiers
            ):
                fail()
            identifiers.add(led["id"])
            rgb = led.get("rgb")
            if (
                rgb is not None
                and (
                    not isinstance(rgb, list)
                    or len(rgb) != 3
                    or any(type(v) is not int or not 0 <= v <= 255 for v in rgb)
                )
            ):
                fail()
        identifiers = set()
        for p in properties:
            if (
                not isinstance(p, dict)
                or type(p.get("id")) is not int
                or p["id"] not in (2, 3, 4, 5, 6, 7, 9)
                or p["id"] in identifiers
            ):
                fail()
            identifiers.add(p["id"])
            if (
                not isinstance(p.get("name"), str)
                or len(p["name"]) > 128
                or type(p.get("writable")) is not bool
            ):
                fail()
            if type(p.get("value")) not in (bool, int) or not -2147483648 <= p["value"] <= 2147483647:
                fail()
            if (
                p["writable"]
                and (
                    (p["id"] in (2, 3, 4) and type(p["value"]) is not bool)
                    or (p["id"] == 5 and type(p["value"]) is not int)
                    or p["id"] not in (2, 3, 4, 5)
                )
            ):
                fail()
