"""The sound engine. This file makes and plays sound; it has no window code.

The sound is made by a chain of stages:

    oscillators -> filter -> envelope -> volume

Live playback, preview, rendering to a file and the waveform display all
run the same chain, so a new stage works everywhere automatically.
"""

import time

import numpy as np
import sounddevice as sd
import soundfile as sf
from scipy.signal import lfilter

SAMPLE_RATE = 44100
FADE_SECONDS = 0.01        # short fade when live sound starts and stops
MAX_VISIBLE_CYCLES = 100   # more waves than this cannot be drawn clearly
POINTS_PER_CYCLE = 20      # drawing detail for the waveform display


def make_waveform(count, frequency, waveform, initial_phase, sample_rate):
    """Make 'count' samples of one wave. Returns them and the next phase."""
    step = frequency / sample_rate
    phase_values = initial_phase + np.arange(count, dtype=np.float64) * step
    cycles = np.mod(phase_values, 1.0)

    if waveform == "Sine":
        audio = np.sin(2 * np.pi * cycles)
    elif waveform == "Square":
        audio = np.where(cycles < 0.5, 1.0, -1.0)
    elif waveform == "Sawtooth":
        audio = 2.0 * cycles - 1.0
    else:  # Triangle
        audio = 1.0 - 4.0 * np.abs(cycles - 0.5)

    next_phase = (initial_phase + count * step) % 1.0
    return audio.astype(np.float32), next_phase


# ---------------------------------------------------------------- stages

class Stage:
    """One step in the sound chain.

    A new chain (and so new stages) is made for every playback or render,
    so each stage can keep its own memory in 'self' without any leftovers.
    """

    live = True   # False = skipped during live sound (render/preview only)

    def __init__(self, settings, sample_rate, total_seconds):
        self.settings = settings            # always the current values
        self.sample_rate = sample_rate
        self.total_seconds = total_seconds  # length of the render, if any

    def process(self, audio):
        """Take a chunk of sound and return the changed chunk."""
        return audio


class Oscillators:
    """The start of the chain: oscillator 1 mixed with oscillator 2."""

    def __init__(self, settings, sample_rate, total_seconds):
        self.settings = settings
        self.sample_rate = sample_rate
        self.phase1 = 0.0
        self.phase2 = 0.0

    def generate(self, count):
        s = self.settings
        frequency1 = s["frequency"]
        # Every 12 semitones doubles the frequency (one octave).
        frequency2 = frequency1 * 2 ** (s["osc2_semitones"] / 12)

        audio1, self.phase1 = make_waveform(
            count, frequency1, s["waveform"], self.phase1, self.sample_rate
        )
        audio2, self.phase2 = make_waveform(
            count, frequency2, s["osc2_waveform"], self.phase2,
            self.sample_rate
        )

        # Dividing by (1 + mix) keeps the mixed sound from getting too loud.
        mix = s["osc2_mix"]
        return ((audio1 + mix * audio2) / (1 + mix)).astype(np.float32)


class LowPassFilter(Stage):
    """Removes high frequencies. Two-pole filter with resonance."""

    def __init__(self, settings, sample_rate, total_seconds):
        super().__init__(settings, sample_rate, total_seconds)
        self.state = np.zeros(2)   # the filter's memory between chunks

    def process(self, audio):
        s = self.settings
        if not s["filter_on"]:
            return audio

        cutoff = min(s["filter_cutoff"], self.sample_rate * 0.45)
        q = s["filter_resonance"]

        w0 = 2 * np.pi * cutoff / self.sample_rate
        alpha = np.sin(w0) / (2 * q)
        cos_w0 = np.cos(w0)

        b = np.array([(1 - cos_w0) / 2, 1 - cos_w0, (1 - cos_w0) / 2])
        a = np.array([1 + alpha, -2 * cos_w0, 1 - alpha])

        filtered, self.state = lfilter(b / a[0], a / a[0], audio,
                                       zi=self.state)
        return filtered.astype(np.float32)


class Envelope(Stage):
    """Fade in (attack) and fade out (release) over the whole render."""

    live = False

    def __init__(self, settings, sample_rate, total_seconds):
        super().__init__(settings, sample_rate, total_seconds)
        self.position = 0   # how many samples have been processed so far

    def process(self, audio):
        s = self.settings
        times = (self.position + np.arange(len(audio))) / self.sample_rate
        self.position += len(audio)

        gain = np.ones(len(audio))
        if s["attack"] > 0:
            gain = np.minimum(gain, times / s["attack"])
        if s["release"] > 0 and self.total_seconds is not None:
            gain = np.minimum(
                gain, (self.total_seconds - times) / s["release"]
            )

        return (audio * np.clip(gain, 0.0, 1.0)).astype(np.float32)


class Volume(Stage):
    """Master volume."""

    def process(self, audio):
        return audio * self.settings["volume"]


# The order here is the order of the chain. New effects go in this list.
EFFECT_STAGES = [LowPassFilter, Envelope, Volume]


class SoundChain:
    """The oscillators followed by all the effect stages."""

    def __init__(self, settings, sample_rate=SAMPLE_RATE,
                 total_seconds=None, live=False):
        self.source = Oscillators(settings, sample_rate, total_seconds)
        self.stages = [
            stage(settings, sample_rate, total_seconds)
            for stage in EFFECT_STAGES
            if stage.live or not live
        ]

    def process(self, count):
        """Make the next 'count' samples of sound."""
        audio = self.source.generate(count)
        for stage in self.stages:
            audio = stage.process(audio)
        return np.clip(audio, -1.0, 1.0).astype(np.float32)


# ------------------------------------------------- rendering and display

def render_sound(settings):
    """Make the complete sound for the chosen render duration."""
    duration = settings["duration"]
    count = int(SAMPLE_RATE * duration)
    return SoundChain(settings, SAMPLE_RATE, duration).process(count)


def display_sound(settings):
    """Make the sound for the waveform display.

    Returns (times, audio), or None if the render holds too many waves
    to draw. The chain is run at a lower sample rate chosen so that every
    wave gets about 20 points, so the whole render fits on the screen.
    """
    duration = settings["duration"]
    cycles = settings["frequency"] * duration

    if cycles > MAX_VISIBLE_CYCLES:
        return None

    count = max(int(cycles * POINTS_PER_CYCLE), 200)
    display_rate = count / duration

    audio = SoundChain(settings, display_rate, duration).process(count)
    times = np.linspace(0, duration, count, endpoint=False)
    return times, audio


# ------------------------------------------------------------- file input

def save_wav(path, audio):
    sf.write(path, audio, SAMPLE_RATE, subtype="PCM_16")


def load_audio(path):
    """Read an audio file. Returns (audio, sample_rate)."""
    return sf.read(path, dtype="float32")


# ---------------------------------------------------------------- playback

class AudioOutput:
    """Plays sound: live, as a looping preview, or an opened file."""

    def __init__(self, settings):
        self.settings = settings
        self.stream = None
        self.chain = None
        self.gain = 0.0     # current fade level of live sound (0 to 1)
        self.target = 1.0   # where the fade level is heading

    def start_live(self):
        self.stop()
        self.chain = SoundChain(self.settings, SAMPLE_RATE, live=True)
        self.gain = 0.0
        self.target = 1.0

        self.stream = sd.OutputStream(
            samplerate=SAMPLE_RATE,
            channels=1,
            dtype="float32",
            callback=self._callback,
            blocksize=0,
        )
        try:
            self.stream.start()
        except Exception:
            self.stream = None
            raise

    def play(self, audio, sample_rate=SAMPLE_RATE, loop=False):
        self.stop()
        sd.play(audio, sample_rate, loop=loop)

    def stop(self):
        if self.stream is not None:
            self.target = 0.0      # fade out first, then switch off
            time.sleep(0.05)
            try:
                self.stream.stop()
                self.stream.close()
            except Exception:
                pass
            self.stream = None

        sd.stop()

    def _callback(self, outdata, frames, time_info, status):
        """Called by the audio system while live playback is running."""
        audio = self.chain.process(frames)

        # Slide the fade level toward its target, one step per sample.
        step = 1.0 / (SAMPLE_RATE * FADE_SECONDS)
        if self.target > self.gain:
            change = step
        elif self.target < self.gain:
            change = -step
        else:
            change = 0.0

        gains = np.clip(
            self.gain + change * np.arange(1, frames + 1), 0.0, 1.0
        )
        self.gain = float(gains[-1])

        outdata[:, 0] = audio * gains
