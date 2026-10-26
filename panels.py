"""The boxes (panels) of the window.

A panel builds its own controls from the list of settings it is given in
parameters.py, so adding a setting never means writing new window code.
"""

import math
import tkinter as tk
from tkinter import ttk


class ParamControl:
    """One setting: its label, value readout, and the widget that edits it."""

    def __init__(self, parent, param, row, on_change):
        self.param = param
        self.on_change = on_change
        self.readout = None
        self.rows = 2   # how many grid rows this control uses

        if param.kind == "toggle":
            self.rows = 1
            self.var = tk.BooleanVar(value=param.default)
            ttk.Checkbutton(
                parent, text=param.label, variable=self.var,
                command=self._changed,
            ).grid(row=row, column=0, columnspan=2, sticky="w", pady=(6, 0))
            return

        ttk.Label(parent, text=param.label).grid(
            row=row, column=0, sticky="w", pady=(6, 0)
        )

        if param.kind == "choice":
            self.var = tk.StringVar(value=param.default)
            menu = ttk.Combobox(
                parent, textvariable=self.var,
                values=param.choices, state="readonly",
            )
            menu.grid(row=row + 1, column=0, columnspan=2,
                      sticky="ew", pady=(2, 0))
            menu.bind("<<ComboboxSelected>>", self._changed)
            return

        # A slider. A "log" slider moves from 0 to 1 and the real value is
        # worked out from that, so each step multiplies instead of adds.
        self.readout = ttk.Label(parent)
        self.readout.grid(row=row, column=1, sticky="e", pady=(6, 0))

        self.var = tk.DoubleVar()
        self._set_slider(param.default)

        if param.log:
            low, high, step = 0.0, 1.0, 0.0001
        else:
            low, high, step = param.minimum, param.maximum, param.step

        tk.Scale(
            parent, variable=self.var, from_=low, to=high, resolution=step,
            orient="horizontal", showvalue=False, command=self._changed,
        ).grid(row=row + 1, column=0, columnspan=2, sticky="ew")

        self._update_readout()

    def get(self):
        """The current value of this setting."""
        param = self.param
        if param.kind == "toggle":
            return bool(self.var.get())
        if param.kind == "choice":
            return self.var.get()

        raw = float(self.var.get())
        if param.log:
            return param.minimum * (param.maximum / param.minimum) ** raw
        return raw

    def set(self, value):
        """Move the control to show a value (used when loading a project)."""
        kind = self.param.kind
        if kind == "toggle":
            self.var.set(bool(value))
        elif kind == "choice":
            self.var.set(value)
        else:
            self._set_slider(value)
        self._update_readout()

    def _set_slider(self, value):
        param = self.param
        if param.log:
            ratio = math.log(value / param.minimum)
            self.var.set(ratio / math.log(param.maximum / param.minimum))
        else:
            self.var.set(value)

    def _update_readout(self):
        if self.readout is not None:
            self.readout.config(text=self.param.fmt.format(self.get()))

    def _changed(self, *args):
        self._update_readout()
        self.on_change()


class Panel(ttk.LabelFrame):
    """One box in the window: a title, its controls, and optional buttons."""

    def __init__(self, parent, title, params, buttons, on_change):
        super().__init__(parent, text=title, padding=12)
        self.columnconfigure(0, weight=1)

        self.controls = {}   # setting key -> its ParamControl
        row = 0

        for param in params:
            control = ParamControl(self, param, row, on_change)
            self.controls[param.key] = control
            row += control.rows

        for text, command in buttons:
            ttk.Button(self, text=text, command=command).grid(
                row=row, column=0, columnspan=2, sticky="ew", pady=4
            )
            row += 1
