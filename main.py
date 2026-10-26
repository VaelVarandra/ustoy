"""SoundLab - a simple synthesizer. Run this file to start the program."""

import json
import tkinter as tk
from tkinter import filedialog, messagebox, ttk

from matplotlib.backends.backend_tkagg import FigureCanvasTkAgg
from matplotlib.figure import Figure

from engine import (
    SAMPLE_RATE, AudioOutput, display_sound, load_audio,
    render_sound, save_wav,
)
from panels import Panel
from parameters import PANELS, check_settings, default_settings

PANEL_COLUMNS = 3   # panels are placed in a grid this many boxes wide


class App:
    def __init__(self):
        self.root = tk.Tk()
        self.root.title("SoundLab - Simple Synthesizer")

        self.settings = default_settings()
        self.audio = AudioOutput(self.settings)
        self.opened_audio = None
        self.opened_rate = SAMPLE_RATE
        self.status = tk.StringVar(value="Ready")
        self.panels = []

        self.build_window()
        self.on_change()
        self.root.protocol("WM_DELETE_WINDOW", self.close)

    def run(self):
        self.root.mainloop()

    # ------------------------------------------------------------ window

    def build_window(self):
        root = self.root

        # Status bar first, so it always stays visible at the bottom.
        ttk.Label(
            root, textvariable=self.status, relief="sunken", anchor="w"
        ).pack(side="bottom", fill="x", padx=10, pady=(0, 10))

        # The control panels, placed automatically in a grid.
        panel_area = ttk.Frame(root)
        panel_area.pack(side="top", fill="x")
        for column in range(PANEL_COLUMNS):
            panel_area.columnconfigure(column, weight=1, uniform="panels")

        # Buttons are added to the panel with the matching title.
        buttons = {
            "Audio output": [
                ("▶  Start live sound", self.start_live),
                ("▶  Preview render (loop)", self.preview_render),
                ("■  Stop audio", self.stop_audio),
                ("Render and save WAV", self.render_wav),
            ],
            "Files": [
                ("Open audio file", self.open_audio_file),
                ("▶  Play opened file", self.play_opened),
                ("Save project settings", self.save_project),
                ("Open project settings", self.open_project),
            ],
        }

        for index, (title, params) in enumerate(PANELS):
            panel = Panel(
                panel_area, title, params, buttons.get(title, []),
                self.on_change,
            )
            panel.grid(
                row=index // PANEL_COLUMNS, column=index % PANEL_COLUMNS,
                padx=8, pady=8, sticky="nsew",
            )
            self.panels.append(panel)

        # The waveform display takes all the space that is left.
        box = ttk.LabelFrame(root, text="Waveform display", padding=10)
        box.pack(side="top", fill="both", expand=True, padx=10, pady=10)

        figure = Figure(figsize=(7, 2.2), dpi=100)
        self.plot = figure.add_subplot(111)
        figure.subplots_adjust(left=0.09, right=0.98, top=0.88, bottom=0.22)
        self.plot.set_xlabel("Time (ms)")
        self.plot.set_ylabel("Amplitude")
        self.plot.grid(True, alpha=0.25)
        self.wave_line, = self.plot.plot([], [], linewidth=1.6)

        self.canvas = FigureCanvasTkAgg(figure, master=box)
        self.canvas.get_tk_widget().pack(fill="both", expand=True)

        # Fit the window to its contents, but never taller than the screen.
        # The waveform display is the part that shrinks if space runs out.
        root.update_idletasks()
        width = root.winfo_reqwidth()
        height = min(root.winfo_reqheight(), root.winfo_screenheight() - 100)
        root.geometry(f"{width}x{height}")
        root.minsize(width, min(height, 620))

    def on_change(self, *args):
        """Called whenever any control is moved."""
        for panel in self.panels:
            for key, control in panel.controls.items():
                self.settings[key] = control.get()
        self.update_display()

    def update_display(self):
        s = self.settings
        duration = s["duration"]
        shown = display_sound(s)

        if shown is not None:
            times, audio = shown
            self.wave_line.set_data(times * 1000, audio)
            self.plot.set_title("Sound output")
        else:
            # Too many waves to draw: show the volume shape instead.
            attack, release, volume = s["attack"], s["release"], s["volume"]
            if attack + release > duration:
                peak_time = attack * duration / (attack + release)
                peak = volume * duration / (attack + release)
                xs = [0, peak_time, duration, peak_time, 0]
                ys = [0, peak, 0, -peak, 0]
            else:
                xs = [0, attack, duration - release, duration,
                      duration - release, attack, 0]
                ys = [0, volume, volume, 0, -volume, -volume, 0]

            self.wave_line.set_data([x * 1000 for x in xs], ys)
            cycles = s["frequency"] * duration
            self.plot.set_title(
                f"Volume shape ({cycles:.0f} waves, too many to draw)"
            )

        self.plot.set_xlim(0, duration * 1000)
        self.plot.set_ylim(-1.05, 1.05)
        self.canvas.draw_idle()

    # ---------------------------------------------------------- playback

    def start_live(self):
        try:
            self.audio.start_live()
            self.status.set("Live playback running")
        except Exception as error:
            messagebox.showerror(
                "Audio error",
                f"Could not start audio playback:\n{error}",
            )

    def preview_render(self):
        try:
            self.audio.play(render_sound(self.settings), loop=True)
            self.status.set(
                f"Looping preview ({self.settings['duration']:.1f} s)"
                " - press Stop to end"
            )
        except Exception as error:
            messagebox.showerror(
                "Audio error", f"Could not play preview:\n{error}"
            )

    def stop_audio(self):
        self.audio.stop()
        self.status.set("Stopped")

    def render_wav(self):
        path = filedialog.asksaveasfilename(
            title="Save generated sound",
            defaultextension=".wav",
            filetypes=[("WAV audio", "*.wav")],
            initialfile="sound.wav",
        )
        if not path:
            return

        try:
            save_wav(path, render_sound(self.settings))
            self.status.set(f"Saved: {path}")
        except Exception as error:
            messagebox.showerror("Save error", str(error))

    # ------------------------------------------------------------- files

    def open_audio_file(self):
        self.audio.stop()

        path = filedialog.askopenfilename(
            title="Open audio file",
            filetypes=[
                ("Audio files", "*.wav *.flac *.ogg"),
                ("WAV files", "*.wav"),
                ("All files", "*.*"),
            ],
        )
        if not path:
            return

        try:
            self.opened_audio, self.opened_rate = load_audio(path)
            self.status.set(f"Opened (not playing): {path}")
        except Exception as error:
            messagebox.showerror("Open error", str(error))

    def play_opened(self):
        if self.opened_audio is None:
            messagebox.showinfo("No file", "Open an audio file first.")
            return

        try:
            self.audio.play(self.opened_audio, self.opened_rate)
            self.status.set("Playing opened file")
        except Exception as error:
            messagebox.showerror(
                "Audio error", f"Could not play file:\n{error}"
            )

    def save_project(self):
        path = filedialog.asksaveasfilename(
            title="Save project settings",
            defaultextension=".json",
            filetypes=[("SoundLab project", "*.json")],
            initialfile="my_sound.json",
        )
        if not path:
            return

        try:
            with open(path, "w", encoding="utf-8") as file:
                json.dump(self.settings, file, indent=4)
            self.status.set(f"Project saved: {path}")
        except Exception as error:
            messagebox.showerror("Save error", str(error))

    def open_project(self):
        path = filedialog.askopenfilename(
            title="Open project settings",
            filetypes=[("SoundLab project", "*.json")],
        )
        if not path:
            return

        try:
            with open(path, "r", encoding="utf-8") as file:
                clean = check_settings(json.load(file))
        except Exception as error:
            messagebox.showerror("Project error", str(error))
            return

        # Update in place: the audio engine uses this same dictionary.
        self.settings.update(clean)
        for panel in self.panels:
            for key, control in panel.controls.items():
                control.set(self.settings[key])

        self.update_display()
        self.status.set(f"Project loaded: {path}")

    def close(self):
        self.audio.stop()
        self.root.destroy()


if __name__ == "__main__":
    App().run()
