# SoundLab

A simple synthesizer. Start it with:

    python main.py

Install the libraries once (with your virtual environment active):

    pip install -r requirements.txt

## The files

| File | What it does |
|------|--------------|
| `main.py` | The window, the waveform display, and the buttons (open, save, play). |
| `panels.py` | Builds the boxes and their sliders from the parameter table. |
| `parameters.py` | **Every setting, described once.** |
| `engine.py` | Makes and plays the sound. No window code. |

## How to add things

**A new setting** (for example a second volume knob)
1. In `parameters.py`, add one `slider(...)`, `choice(...)` or `toggle(...)`
   line to a panel in `PANELS`.
2. Use it in `engine.py` as `self.settings["its_key"]`.

The slider, label, default value, project saving/loading and range checks
all come from that one line.

**A new box** (panel)
- Add `("Panel title", [ ...its settings... ])` to `PANELS`.
- It is placed in the grid automatically. To change how many boxes fit
  side by side, change `PANEL_COLUMNS` in `main.py`.

**A new effect** (for example echo)
1. In `engine.py`, write a class based on `Stage` with a `process(self, audio)`
   method that returns the changed sound. Keep anything it must remember
   between chunks (like a delay buffer) in `self`.
2. Add the class to the `EFFECT_STAGES` list. The order of the list is the
   order of the chain.
3. Add its settings as a panel in `parameters.py`.

Live sound, preview, rendering and the waveform display all use the same
chain, so the new effect works in all of them.

Set `live = False` on a stage that should only apply to renders and
previews (like the envelope).

## Old project files

Project files saved by the earlier single-file version (`ustoy.py`) still
open, because the setting names are the same.
