"""Exact unit conversion via sympy's unit system."""

SKILL = {
    "name": "convert_units",
    "description": (
        "Convert between units exactly (length, mass, temperature, speed, "
        "volume, data sizes, time). Use for ANY unit-conversion question. "
        "Examples: value=5, from_unit='mile', to_unit='km'; "
        "value=72, from_unit='fahrenheit', to_unit='celsius'."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "value": {"type": "number", "description": "The numeric value"},
            "from_unit": {"type": "string", "description": "Unit to convert from"},
            "to_unit": {"type": "string", "description": "Unit to convert to"},
        },
        "required": ["value", "from_unit", "to_unit"],
    },
}

_ALIASES = {
    "km": "kilometer", "kms": "kilometer", "m": "meter", "cm": "centimeter",
    "mm": "millimeter", "mi": "mile", "miles": "mile", "ft": "foot",
    "feet": "foot", "in": "inch", "inches": "inch", "yd": "yard",
    "kg": "kilogram", "g": "gram", "mg": "milligram", "lb": "pound",
    "lbs": "pound", "pounds": "pound", "oz": "ounce",
    "l": "liter", "liters": "liter", "ml": "milliliter", "gal": "gallon",
    "gallons": "gallon",
    "s": "second", "sec": "second", "min": "minute", "h": "hour",
    "hr": "hour", "hours": "hour", "days": "day",
    "mph": "mile/hour", "kph": "kilometer/hour", "kmh": "kilometer/hour",
    "c": "celsius", "f": "fahrenheit", "k": "kelvin",
    "gb": "gigabyte", "mb": "megabyte", "kb": "kilobyte", "tb": "terabyte",
    "gib": "gibibyte", "mib": "mebibyte",
}

_DATA = {"byte": 1, "kilobyte": 10**3, "megabyte": 10**6, "gigabyte": 10**9,
         "terabyte": 10**12, "kibibyte": 2**10, "mebibyte": 2**20,
         "gibibyte": 2**30, "tebibyte": 2**40, "bit": 0.125}

_TEMPS = ("celsius", "fahrenheit", "kelvin")


def _norm(u: str) -> str:
    u = u.strip().lower()
    if u in _ALIASES:
        return _ALIASES[u]
    if u in _TEMPS or u in _DATA:
        return u
    if u.endswith("s") and len(u) > 2:
        singular = u[:-1]
        if singular in _ALIASES:
            return _ALIASES[singular]
        if singular in _TEMPS or singular in _DATA:
            return singular
        import sympy.physics.units as su
        if getattr(su, u, None) is None and getattr(su, singular, None) is not None:
            return singular
    return u


def _temp(value: float, f: str, t: str) -> float:
    to_c = {"celsius": lambda v: v,
            "fahrenheit": lambda v: (v - 32) * 5 / 9,
            "kelvin": lambda v: v - 273.15}
    from_c = {"celsius": lambda v: v,
              "fahrenheit": lambda v: v * 9 / 5 + 32,
              "kelvin": lambda v: v + 273.15}
    return from_c[t](to_c[f](value))


def run(args: dict) -> str:
    value = float(args["value"])
    f, t = _norm(str(args["from_unit"])), _norm(str(args["to_unit"]))

    if f in _TEMPS and t in _TEMPS:
        result = _temp(value, f, t)
        return f"{value:g} {f} = {round(result, 6):g} {t}"

    if f in _DATA and t in _DATA:
        result = value * _DATA[f] / _DATA[t]
        return f"{value:g} {f} = {result:g} {t}"

    import sympy.physics.units as u
    from sympy import nsimplify
    from sympy.physics.units import convert_to

    def unit_of(name):
        # "mile/hour" style compound units
        if "/" in name:
            num, den = name.split("/", 1)
            return unit_of(num.strip()) / unit_of(den.strip())
        obj = getattr(u, name, None)
        if obj is None:
            raise ValueError(f"unknown unit '{name}'")
        return obj

    try:
        result = convert_to(nsimplify(value) * unit_of(f), unit_of(t))
        return f"{value:g} {f} = {str(result.evalf(10)).replace('*', ' ')}"
    except ValueError as e:
        return f"Error: {e}"
    except Exception as e:
        return f"Error converting {f} -> {t}: {e}"
