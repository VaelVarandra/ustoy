"""All settings of SoundLab, described once.

Each setting (a "parameter") is one line in PANELS below. From that single
line the program builds the slider, the label, the default value, and the
checks used when a project file is loaded.

To add a setting: add one line here, then use settings["its_key"] in
engine.py. Nothing else needs to change.
"""

from dataclasses import dataclass

WAVEFORMS = ["Sine", "Square", "Sawtooth", "Triangle"]


@dataclass
class Param:
    key: str            # name used in settings and in saved project files
    label: str          # text shown next to the control
    default: object     # starting value
    kind: str = "slider"    # "slider", "choice" or "toggle"
    minimum: float = 0.0
    maximum: float = 1.0
    step: float = 0.01
    log: bool = False       # True = slider steps multiply instead of add
    fmt: str = "{:.2f}"     # how the value is shown, e.g. "{:.0f} Hz"
    choices: list = None    # list of options for kind="choice"


def slider(key, label, default, minimum, maximum,
           step=0.01, fmt="{:.2f}", log=False):
    return Param(key, label, default, "slider",
                 minimum, maximum, step, log, fmt)


def choice(key, label, default, choices):
    return Param(key, label, default, "choice", choices=choices)


def toggle(key, label, default):
    return Param(key, label, default, "toggle")


# The panels, in the order they appear in the window.
# Each entry is (panel title, [its settings]).
PANELS = [
    ("Oscillator 1", [
        choice("waveform", "Waveform", "Sine", WAVEFORMS),
        slider("frequency", "Frequency", 440.0, 10, 2000, 1, "{:.0f} Hz"),
    ]),
    ("Oscillator 2", [
        choice("osc2_waveform", "Waveform", "Sine", WAVEFORMS),
        slider("osc2_semitones", "Pitch offset", 0.0, -12, 12,
               0.01, "{:+.2f} semitones"),
        slider("osc2_mix", "Mix", 0.0, 0, 1),
    ]),
    ("Low-pass filter", [
        toggle("filter_on", "Filter on", False),
        slider("filter_cutoff", "Cutoff", 2000.0, 20, 20000,
               fmt="{:.0f} Hz", log=True),
        slider("filter_resonance", "Resonance", 0.71, 0.5, 10),
    ]),
    ("Volume and envelope", [
        slider("volume", "Master volume", 0.25, 0, 1),
        slider("attack", "Attack (fade in)", 0.01, 0, 2, 0.01, "{:.2f} s"),
        slider("release", "Release (fade out)", 0.01, 0, 5, 0.01, "{:.2f} s"),
    ]),
    ("Audio output", [
        slider("duration", "Render duration", 5.0, 0.1, 60, 0.1, "{:.1f} s"),
    ]),
    ("Files", []),
]

ALL_PARAMS = [param for _, params in PANELS for param in params]


def default_settings():
    """A fresh settings dictionary with every default value."""
    return {param.key: param.default for param in ALL_PARAMS}


def check_settings(data):
    """Check settings loaded from a project file.

    Returns a clean settings dictionary, or raises ValueError with a
    message that explains what is wrong. Missing settings get defaults.
    """
    if not isinstance(data, dict):
        raise ValueError("This is not a SoundLab project file.")

    clean = {}
    for param in ALL_PARAMS:
        value = data.get(param.key, param.default)
        name = f"{param.label} ({param.key})"

        if param.kind == "slider":
            value = float(value)
            if not param.minimum <= value <= param.maximum:
                raise ValueError(
                    f"{name} must be between "
                    f"{param.minimum:g} and {param.maximum:g}."
                )
        elif param.kind == "choice":
            if value not in param.choices:
                raise ValueError(f"Unknown value for {name}: {value}")
        else:
            value = bool(value)

        clean[param.key] = value

    return clean
