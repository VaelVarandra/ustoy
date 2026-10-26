
import json
import tkinter as tk
from tkinter import ttk, filedialog, messagebox
from matplotlib.figure import Figure
from matplotlib.backends.backend_tkagg import FigureCanvasTkAgg
from scipy.signal import lfilter


import numpy as np
import sounddevice as sd
import soundfile as sf
import time

SAMPLE_RATE = 44100
MAX_VISIBLE_CYCLES = 100

# Settings dictionary shared by the interface and audio engine
settings = {
    "waveform": "Sine",
    "frequency": 440.0,
    "volume": 0.25,
    "duration": 5.0,
    "attack": 0.01,
    "release": 0.01,
    "osc2_waveform": "Sine",
    "osc2_semitones": 0.0,
    "osc2_mix": 0.0,
    "filter_on": False,
    "filter_cutoff": 2000.0,
    "filter_resonance": 0.71,
    }

def slider_to_cutoff(position):
    """Turn a slider position (0 to 1) into a cutoff in Hz (20 to 20000)."""
    return 20.0 * 1000.0 ** position


def cutoff_to_slider(hz):
    """The opposite: turn a cutoff in Hz into a slider position."""
    return float(np.log(hz / 20.0) / np.log(1000.0))

audio_stream = None
phase = 0.0
phase2 = 0.0
filter_state = np.zeros(2)
live_gain = 0.0
live_target = 1.0
opened_audio = None
opened_rate = SAMPLE_RATE


def make_waveform(count, frequency, waveform, initial_phase=0.0):
    """Generate a waveform and return it with the next phase."""
    phase_values = (
        initial_phase
        + np.arange(count, dtype=np.float64) * frequency / SAMPLE_RATE
    )
    cycles = np.mod(phase_values, 1.0)

    if waveform == "Sine":
        audio = np.sin(2 * np.pi * cycles)
    elif waveform == "Square":
        audio = np.where(cycles < 0.5, 1.0, -1.0)
    elif waveform == "Sawtooth":
        audio = 2.0 * cycles - 1.0
    else:  # Triangle
        audio = 1.0 - 4.0 * np.abs(cycles - 0.5)

    next_phase = (
        initial_phase + count * frequency / SAMPLE_RATE
    ) % 1.0

    return audio.astype(np.float32), next_phase


def make_sound(count, phase1=0.0, phase2=0.0, frequency=None):
    """Mix oscillator 1 and 2. Returns the sound and the next phase of each."""
    if frequency is None:
        frequency = settings["frequency"]

    audio1, next1 = make_waveform(
        count, frequency, settings["waveform"], phase1
    )

    # Every 12 semitones doubles the frequency (one octave).
    frequency2 = frequency * 2 ** (settings["osc2_semitones"] / 12)

    audio2, next2 = make_waveform(
        count, frequency2, settings["osc2_waveform"], phase2
    )

    # Dividing by (1 + mix) keeps the mixed sound from getting too loud.
    mix = settings["osc2_mix"]
    audio = (audio1 + mix * audio2) / (1 + mix)

    return audio.astype(np.float32), next1, next2


def make_filter_coefficients(cutoff):
    """Numbers that describe a two-pole low-pass filter."""
    cutoff = min(cutoff, SAMPLE_RATE * 0.45)
    q = settings["filter_resonance"]

    w0 = 2 * np.pi * cutoff / SAMPLE_RATE
    alpha = np.sin(w0) / (2 * q)
    cos_w0 = np.cos(w0)

    b = np.array([(1 - cos_w0) / 2, 1 - cos_w0, (1 - cos_w0) / 2])
    a = np.array([1 + alpha, -2 * cos_w0, 1 - alpha])

    return b / a[0], a / a[0]


def apply_filter(audio, state=None, cutoff=None):
    """Low-pass filter a sound. Returns the sound and the filter's memory."""
    if state is None:
        state = np.zeros(2)

    if not settings["filter_on"]:
        return audio, state

    if cutoff is None:
        cutoff = settings["filter_cutoff"]

    b, a = make_filter_coefficients(cutoff)
    filtered, state = lfilter(b, a, audio, zi=state)

    return filtered.astype(np.float32), state

FADE_SECONDS = 0.01

def apply_envelope(audio, attack, release, total_seconds):
    """Fade in over 'attack' seconds and out over 'release' seconds."""
    times = np.linspace(0.0, total_seconds, len(audio), endpoint=False)
    gain = np.ones(len(audio))

    if attack > 0:
        gain = np.minimum(gain, times / attack)
    if release > 0:
        gain = np.minimum(gain, (total_seconds - times) / release)

    gain = np.clip(gain, 0.0, 1.0)
    return (audio * gain).astype(np.float32)

def generate_audio(duration):
    """Generate a complete sound for offline rendering."""
    count = int(SAMPLE_RATE * duration)

    audio, _, _ = make_sound(count)
    audio, _ = apply_filter(audio)
    audio = audio * settings["volume"]

    audio = apply_envelope(
        audio, settings["attack"], settings["release"], duration
    )
    return np.clip(audio, -1.0, 1.0)

def audio_callback(outdata, frames, time_info, status):
    """Called by the audio system while live playback is running."""
    global phase, phase2, live_gain, filter_state

    volume = settings["volume"]

    audio, phase, phase2 = make_sound(frames, phase, phase2)
    audio, filter_state = apply_filter(audio, filter_state)

    # Slide the fade level toward its target, one small step per sample.
    step = 1.0 / (SAMPLE_RATE * FADE_SECONDS)

    if live_target > live_gain:
        change = step
    elif live_target < live_gain:
        change = -step
    else:
        change = 0.0

    gains = live_gain + change * np.arange(1, frames + 1)
    gains = np.clip(gains, 0.0, 1.0)
    live_gain = float(gains[-1])

    outdata[:, 0] = np.clip(audio * volume * gains, -1.0, 1.0)


def start_live():
    global audio_stream, phase, phase2, live_gain, live_target
    global filter_state

    stop_audio()
    phase = 0.0
    phase2 = 0.0
    filter_state = np.zeros(2)
    live_gain = 0.0
    live_target = 1.0

    try:
        audio_stream = sd.OutputStream(
            samplerate=SAMPLE_RATE,
            channels=1,
            dtype="float32",
            callback=audio_callback,
            blocksize=0,
        )
        audio_stream.start()
        status_text.set("Live playback running")
    except Exception as error:
        audio_stream = None
        messagebox.showerror(
            "Audio error",
            f"Could not start audio playback:\n{error}",
        )


def stop_audio():
    global audio_stream, live_target

    if audio_stream is not None:
        # Fade out first, then switch the stream off.
        live_target = 0.0
        time.sleep(0.05)

        try:
            audio_stream.stop()
            audio_stream.close()
        except Exception:
            pass
        audio_stream = None

    sd.stop()
    status_text.set("Stopped")

def preview_render():
    stop_audio()
    audio = generate_audio(settings["duration"])

    try:
        sd.play(audio, SAMPLE_RATE, loop=True)
        status_text.set(
            f"Looping preview ({settings['duration']:.1f} s) - press Stop to end"
        )
    except Exception as error:
        messagebox.showerror(
            "Audio error",
            f"Could not play preview:\n{error}",
        )

def render_wav():
    audio = generate_audio(settings["duration"])

    path = filedialog.asksaveasfilename(
        title="Save generated sound",
        defaultextension=".wav",
        filetypes=[("WAV audio", "*.wav")],
        initialfile="sound.wav",
    )

    if not path:
        return

    try:
        sf.write(path, audio, SAMPLE_RATE, subtype="PCM_16")
        status_text.set(f"Saved: {path}")
    except Exception as error:
        messagebox.showerror("Save error", str(error))


def open_wav():
    global opened_audio, opened_rate

    stop_audio()

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
        opened_audio, opened_rate = sf.read(path, dtype="float32")
        status_text.set(f"Opened (not playing): {path}")
    except Exception as error:
        messagebox.showerror("Open error", str(error))


def play_opened():
    if opened_audio is None:
        messagebox.showinfo("No file", "Open an audio file first.")
        return

    stop_audio()

    try:
        sd.play(opened_audio, opened_rate)
        status_text.set("Playing opened file")
    except Exception as error:
        messagebox.showerror(
            "Audio error",
            f"Could not play file:\n{error}",
        )

def save_project():
    path = filedialog.asksaveasfilename(
        title="Save project settings",
        defaultextension=".json",
        filetypes=[("ustoy project", "*.json")],
        initialfile="my_sound.json",
    )

    if not path:
        return

    try:
        with open(path, "w", encoding="utf-8") as file:
            json.dump(settings, file, indent=4)
        status_text.set(f"Project saved: {path}")
    except Exception as error:
        messagebox.showerror("Save error", str(error))


def open_project():
    path = filedialog.askopenfilename(
        title="Open project settings",
        filetypes=[("ustoy project", "*.json")],
    )

    if not path:
        return

    try:
        with open(path, "r", encoding="utf-8") as file:
            loaded = json.load(file)

        waveform = loaded.get("waveform", "Sine")
        frequency = float(loaded.get("frequency", 440))
        volume = float(loaded.get("volume", 0.25))
        duration = float(loaded.get("duration", 5))
        attack = float(loaded.get("attack", 0.01))
        release = float(loaded.get("release", 0.01))
        osc2_waveform = loaded.get("osc2_waveform", "Sine")
        osc2_semitones = float(loaded.get("osc2_semitones", 0))
        osc2_mix = float(loaded.get("osc2_mix", 0))
        filter_on = bool(loaded.get("filter_on", False))
        filter_cutoff = float(loaded.get("filter_cutoff", 2000))
        filter_resonance = float(loaded.get("filter_resonance", 0.71))

        if waveform not in ("Sine", "Square", "Sawtooth", "Triangle"):
            raise ValueError("Unknown waveform.")
        if not 10 <= frequency <= 2000:
            raise ValueError("Frequency must be 10–2000 Hz.")
        if not 0 <= volume <= 1:
            raise ValueError("Volume must be between 0 and 1.")
        if not 0.1 <= duration <= 60:
            raise ValueError("Duration must be 0.1–60 seconds.")
        if not 0 <= attack <= 2:
            raise ValueError("Attack must be 0–2 seconds.")
        if not 0 <= release <= 5:
            raise ValueError("Release must be 0–5 seconds.")
        if osc2_waveform not in ("Sine", "Square", "Sawtooth", "Triangle"):
            raise ValueError("Unknown waveform for oscillator 2.")
        if not -12 <= osc2_semitones <= 12:
            raise ValueError("Pitch offset must be -12 to 12 semitones.")
        if not 0 <= osc2_mix <= 1:
            raise ValueError("Oscillator 2 mix must be between 0 and 1.")
        if not 20 <= filter_cutoff <= 20000:
            raise ValueError("Filter cutoff must be 20–20000 Hz.")
        if not 0.5 <= filter_resonance <= 10:
            raise ValueError("Filter resonance must be 0.5–10.")

        settings.update(
            waveform=waveform,
            frequency=frequency,
            volume=volume,
            duration=duration,
            attack=attack,
            release=release,
            osc2_waveform=osc2_waveform,
            osc2_semitones=osc2_semitones,
            osc2_mix=osc2_mix,
            filter_on=filter_on,
            filter_cutoff=filter_cutoff,
            filter_resonance=filter_resonance,
        )

        waveform_var.set(waveform)
        frequency_var.set(frequency)
        volume_var.set(volume)
        duration_var.set(duration)
        attack_var.set(attack)
        release_var.set(release)
        osc2_waveform_var.set(osc2_waveform)
        osc2_semitones_var.set(osc2_semitones)
        osc2_mix_var.set(osc2_mix)
        filter_on_var.set(filter_on)
        filter_cutoff_var.set(cutoff_to_slider(filter_cutoff))
        filter_resonance_var.set(filter_resonance)
        update_waveform()
        status_text.set(f"Project loaded: {path}")

    except Exception as error:
        messagebox.showerror("Project error", str(error))


def update_labels(*args):
    settings["waveform"] = waveform_var.get()
    settings["frequency"] = float(frequency_var.get())
    settings["volume"] = float(volume_var.get())
    settings["duration"] = float(duration_var.get())
    settings["attack"] = float(attack_var.get())
    settings["release"] = float(release_var.get())

    frequency_label.config(
        text=f"{settings['frequency']:.0f} Hz"
    )
    volume_label.config(
        text=f"{settings['volume']:.2f}"
    )
    duration_label.config(
        text=f"{settings['duration']:.1f} seconds"
    )
    attack_label.config(text=f"{settings['attack']:.2f} s")
    release_label.config(text=f"{settings['release']:.2f} s")
    settings["osc2_waveform"] = osc2_waveform_var.get()
    settings["osc2_semitones"] = float(osc2_semitones_var.get())
    settings["osc2_mix"] = float(osc2_mix_var.get())

    osc2_semitones_label.config(
        text=f"{settings['osc2_semitones']:+.2f} semitones"
    )
    osc2_mix_label.config(text=f"{settings['osc2_mix']:.2f}")

    settings["filter_on"] = bool(filter_on_var.get())
    settings["filter_cutoff"] = slider_to_cutoff(
        float(filter_cutoff_var.get())
    )
    settings["filter_resonance"] = float(filter_resonance_var.get())

    filter_cutoff_label.config(
        text=f"{settings['filter_cutoff']:.0f} Hz"
    )
    filter_resonance_label.config(
        text=f"{settings['filter_resonance']:.2f}"
    )



def update_waveform(*args):
    update_labels()

    frequency = settings["frequency"]
    duration = settings["duration"]
    volume = settings["volume"]
    attack = settings["attack"]
    release = settings["release"]
    cycles = frequency * duration

    if cycles <= MAX_VISIBLE_CYCLES:
        # Draw the real wave, about 20 points per cycle.
        sample_count = max(int(cycles * 20), 200)

        times = np.linspace(
            0, duration, sample_count, endpoint=False
        )

        # Make exactly this many cycles fit in the points.
        display_frequency = cycles * SAMPLE_RATE / sample_count

        audio, _, _ = make_sound(
            sample_count, frequency=display_frequency
        )

        # The display is stretched in time, so stretch the cutoff too.
        display_cutoff = (
            settings["filter_cutoff"] * display_frequency / frequency
        )
        audio, _ = apply_filter(audio, cutoff=display_cutoff)

        audio = apply_envelope(
            audio * volume, attack, release, duration
        )

        wave_line.set_data(times * 1000, audio)
        plot.set_title("Oscillator output")
    else:
        # Too many waves to draw: show the volume shape instead.
        if attack + release > duration:
            peak_time = attack * duration / (attack + release)
            peak = volume * duration / (attack + release)
            xs = [0, peak_time, duration, peak_time, 0]
            ys = [0, peak, 0, -peak, 0]
        else:
            xs = [0, attack, duration - release, duration,
                  duration - release, attack, 0]
            ys = [0, volume, volume, 0, -volume, -volume, 0]

        wave_line.set_data([x * 1000 for x in xs], ys)
        plot.set_title(
            f"Volume shape ({cycles:.0f} waves, too many to draw)"
        )

    plot.set_xlim(0, duration * 1000)
    plot.set_ylim(-1.05, 1.05)
    wave_canvas.draw_idle()
    
# ---------------- GUI ----------------

root = tk.Tk()
root.title("ustoy - Simple Synthesizer")
root.geometry("760x1080")
root.minsize(600, 1080)

waveform_var = tk.StringVar(value=settings["waveform"])
frequency_var = tk.DoubleVar(value=settings["frequency"])
volume_var = tk.DoubleVar(value=settings["volume"])
duration_var = tk.DoubleVar(value=settings["duration"])
attack_var = tk.DoubleVar(value=settings["attack"])
release_var = tk.DoubleVar(value=settings["release"])
osc2_waveform_var = tk.StringVar(value=settings["osc2_waveform"])
osc2_semitones_var = tk.DoubleVar(value=settings["osc2_semitones"])
osc2_mix_var = tk.DoubleVar(value=settings["osc2_mix"])
filter_on_var = tk.BooleanVar(value=settings["filter_on"])
filter_cutoff_var = tk.DoubleVar(
    value=cutoff_to_slider(settings["filter_cutoff"])
)
filter_resonance_var = tk.DoubleVar(value=settings["filter_resonance"])
status_text = tk.StringVar(value="Ready")

root.columnconfigure(0, weight=1)
root.columnconfigure(1, weight=1)
root.rowconfigure(4, weight=1)

# Box 1: Oscillator
oscillator = ttk.LabelFrame(
    root, text="1. Oscillator", padding=12
)
oscillator.grid(
    row=0, column=0, padx=10, pady=10, sticky="nsew"
)
oscillator.columnconfigure(0, weight=1)

ttk.Label(oscillator, text="Waveform").grid(
    row=0, column=0, sticky="w"
)

waveform_menu = ttk.Combobox(
    oscillator,
    textvariable=waveform_var,
    values=["Sine", "Square", "Sawtooth", "Triangle"],
    state="readonly",
)
waveform_menu.grid(
    row=1, column=0, sticky="ew", pady=(4, 12)
)
waveform_menu.bind("<<ComboboxSelected>>", update_waveform)

ttk.Label(oscillator, text="Frequency").grid(
    row=2, column=0, sticky="w"
)

frequency_label = ttk.Label(oscillator)
frequency_label.grid(row=2, column=1, sticky="e")

tk.Scale(
    oscillator,
    variable=frequency_var,
    from_=10,
    to=2000,
    resolution=1,
    orient="horizontal",
    command=update_waveform,
).grid(row=3, column=0, columnspan=2, sticky="ew")

# Box 2: Sound shaping
shaping = ttk.LabelFrame(
    root, text="2. Sound shaping", padding=12
)
shaping.grid(
    row=0, column=1, padx=10, pady=10, sticky="nsew"
)
shaping.columnconfigure(0, weight=1)

ttk.Label(shaping, text="Master volume").grid(
    row=0, column=0, sticky="w"
)

volume_label = ttk.Label(shaping)
volume_label.grid(row=0, column=1, sticky="e")

tk.Scale(
    shaping,
    variable=volume_var,
    from_=0.0,
    to=1.0,
    resolution=0.01,
    orient="horizontal",
    command=update_waveform,
).grid(row=1, column=0, columnspan=2, sticky="ew")



ttk.Label(shaping, text="Attack (fade in)").grid(
    row=2, column=0, sticky="w"
)

attack_label = ttk.Label(shaping)
attack_label.grid(row=2, column=1, sticky="e")

tk.Scale(
    shaping,
    variable=attack_var,
    from_=0.0,
    to=2.0,
    resolution=0.01,
    orient="horizontal",
    command=update_waveform,
).grid(row=3, column=0, columnspan=2, sticky="ew")

ttk.Label(shaping, text="Release (fade out)").grid(
    row=4, column=0, sticky="w"
)

release_label = ttk.Label(shaping)
release_label.grid(row=4, column=1, sticky="e")

tk.Scale(
    shaping,
    variable=release_var,
    from_=0.0,
    to=5.0,
    resolution=0.01,
    orient="horizontal",
    command=update_waveform,
).grid(row=5, column=0, columnspan=2, sticky="ew")

# Box 3: Playback and rendering
output = ttk.LabelFrame(
    root, text="3. Audio output", padding=12
)
output.grid(
    row=1, column=0, padx=10, pady=10, sticky="nsew"
)
output.columnconfigure(0, weight=1)

ttk.Label(output, text="Render duration").grid(
    row=0, column=0, sticky="w"
)

duration_label = ttk.Label(output)
duration_label.grid(row=0, column=1, sticky="e")

tk.Scale(
    output,
    variable=duration_var,
    from_=0.1,
    to=60,
    resolution=0.1,
    orient="horizontal",
    command=update_waveform,
).grid(row=1, column=0, columnspan=2, sticky="ew")

ttk.Button(
    output, text="▶  Start live sound", command=start_live
).grid(row=2, column=0, sticky="ew", pady=4)

ttk.Button(
    output, text="▶  Preview render", command=preview_render
).grid(row=3, column=0, sticky="ew", pady=4)

ttk.Button(
    output, text="■  Stop audio", command=stop_audio
).grid(row=4, column=0, sticky="ew", pady=4)

ttk.Button(
    output, text="Render and save WAV", command=render_wav
).grid(row=5, column=0, sticky="ew", pady=4)

# Box 4: Files and projects
files = ttk.LabelFrame(
    root, text="4. Files and projects", padding=12
)
files.grid(
    row=1, column=1, padx=10, pady=10, sticky="nsew"
)
files.columnconfigure(0, weight=1)

ttk.Button(
    files, text="Open audio file", command=open_wav
).grid(row=0, column=0, sticky="ew", pady=4)

ttk.Button(
    files, text="▶  Play opened file", command=play_opened
).grid(row=1, column=0, sticky="ew", pady=4)

ttk.Button(
    files, text="Save project settings", command=save_project
).grid(row=2, column=0, sticky="ew", pady=4)

ttk.Button(
    files, text="Open project settings", command=open_project
).grid(row=3, column=0, sticky="ew", pady=4)

# Box 6: Oscillator 2
oscillator2 = ttk.LabelFrame(
    root, text="6. Oscillator 2 (mixed with oscillator 1)", padding=12
)
oscillator2.grid(
    row=2, column=0, columnspan=2,
    padx=10, pady=10, sticky="nsew"
)
oscillator2.columnconfigure(0, weight=1)
oscillator2.columnconfigure(1, weight=2)
oscillator2.columnconfigure(2, weight=2)

ttk.Label(oscillator2, text="Waveform").grid(
    row=0, column=0, sticky="w"
)

osc2_menu = ttk.Combobox(
    oscillator2,
    textvariable=osc2_waveform_var,
    values=["Sine", "Square", "Sawtooth", "Triangle"],
    state="readonly",
)
osc2_menu.grid(row=1, column=0, sticky="ew", padx=(0, 12))
osc2_menu.bind("<<ComboboxSelected>>", update_waveform)

ttk.Label(oscillator2, text="Pitch offset").grid(
    row=0, column=1, sticky="w"
)

osc2_semitones_label = ttk.Label(oscillator2)
osc2_semitones_label.grid(row=0, column=1, sticky="e", padx=(0, 12))

tk.Scale(
    oscillator2,
    variable=osc2_semitones_var,
    from_=-12,
    to=12,
    resolution=0.01,
    orient="horizontal",
    command=update_waveform,
).grid(row=1, column=1, sticky="ew", padx=(0, 12))

ttk.Label(oscillator2, text="Mix").grid(
    row=0, column=2, sticky="w"
)

osc2_mix_label = ttk.Label(oscillator2)
osc2_mix_label.grid(row=0, column=2, sticky="e")

tk.Scale(
    oscillator2,
    variable=osc2_mix_var,
    from_=0.0,
    to=1.0,
    resolution=0.01,
    orient="horizontal",
    command=update_waveform,
).grid(row=1, column=2, sticky="ew")


# Box 7: Filter
filter_box = ttk.LabelFrame(
    root, text="7. Low-pass filter", padding=12
)
filter_box.grid(
    row=3, column=0, columnspan=2,
    padx=10, pady=10, sticky="nsew"
)
filter_box.columnconfigure(0, weight=1)
filter_box.columnconfigure(1, weight=2)
filter_box.columnconfigure(2, weight=2)

ttk.Checkbutton(
    filter_box,
    text="Filter on",
    variable=filter_on_var,
    command=update_waveform,
).grid(row=1, column=0, sticky="w")

ttk.Label(filter_box, text="Cutoff").grid(
    row=0, column=1, sticky="w"
)

filter_cutoff_label = ttk.Label(filter_box)
filter_cutoff_label.grid(row=0, column=1, sticky="e", padx=(0, 12))

tk.Scale(
    filter_box,
    variable=filter_cutoff_var,
    from_=0.0,
    to=1.0,
    resolution=0.001,
    showvalue=False,
    orient="horizontal",
    command=update_waveform,
).grid(row=1, column=1, sticky="ew", padx=(0, 12))

ttk.Label(filter_box, text="Resonance").grid(
    row=0, column=2, sticky="w"
)

filter_resonance_label = ttk.Label(filter_box)
filter_resonance_label.grid(row=0, column=2, sticky="e")

tk.Scale(
    filter_box,
    variable=filter_resonance_var,
    from_=0.5,
    to=10.0,
    resolution=0.01,
    orient="horizontal",
    command=update_waveform,
).grid(row=1, column=2, sticky="ew")



# Box 5: Live waveform display
waveform_box = ttk.LabelFrame(
    root, text="5. Waveform display", padding=10
)
waveform_box.grid(
    row=4, column=0, columnspan=2,
    padx=10, pady=10, sticky="nsew"
)

figure = Figure(figsize=(7, 2.2), dpi=100)
plot = figure.add_subplot(111)
figure.subplots_adjust(
    left=0.09, right=0.98, top=0.88, bottom=0.22
)

plot.set_title("Oscillator output")
plot.set_xlabel("Time (ms)")
plot.set_ylabel("Amplitude")
plot.set_ylim(-1.05, 1.05)
plot.grid(True, alpha=0.25)

wave_line, = plot.plot([], [], linewidth=1.6)

wave_canvas = FigureCanvasTkAgg(
    figure, master=waveform_box
)
wave_canvas.get_tk_widget().pack(
    fill="both", expand=True
)






# Status bar
ttk.Label(
    root, textvariable=status_text, relief="sunken", anchor="w"
).grid(
    row=5, column=0, columnspan=2,
    padx=10, pady=(0, 10), sticky="ew"
)

update_waveform()


def close_app():
    stop_audio()
    root.destroy()


root.protocol("WM_DELETE_WINDOW", close_app)
root.mainloop()
