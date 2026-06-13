# midi-retune

Play **any tuning** — 19-TET, 31-TET, quarter-tones, Bohlen-Pierce, just intonation — from an ordinary 12-key-per-octave MIDI keyboard, by retuning notes on the fly and forwarding them to a synth.

A normal MIDI keyboard sends 12 keys per octave at fixed 12-TET pitches. `midi-retune` reads a [Scala](https://www.huygens-fokker.org/scala/scl_format.html) `.scl` tuning file and remaps the keys so that **consecutive keys play consecutive scale degrees** — so e.g. 19-TET puts a full octave under 19 keys instead of 12. It retunes either via the **MIDI Tuning Standard (MTS)** sysex or via **per-note MPE pitch bend**, whichever your synth supports.

```
MIDI keyboard  →  midi-retune  →  virtual MIDI port  →  your synth
```

## Requirements

- Python 3.8+
- [`mido`](https://mido.readthedocs.io/) + [`python-rtmidi`](https://pypi.org/project/python-rtmidi/) — `pip install -r requirements.txt`
- A **virtual MIDI port** so the script can hand notes to your synth:
  - Windows: [loopMIDI](https://www.tobias-erichsen.de/software/loopmidi.html) (create a port with the `+` button — it starts empty)
  - macOS: enable the **IAC Driver** in Audio MIDI Setup
  - Linux: an ALSA virtual port (e.g. `snd-virmidi`) or a JACK MIDI port

## Quick start

```bash
pip install -r requirements.txt

# see your MIDI ports
python midi_retune.py --list

# play 19-TET, retuning via MPE pitch bend (range 2 semitones)
python midi_retune.py 19edo.scl --mpe --bend-range 2
```

The script auto-picks an input port containing "keystation" and an output port containing "loop"/"loopMIDI" (or falls back to the Windows GS Wavetable synth). Override with `--in` / `--out` (index or name substring).

## Two retuning modes

### MPE pitch bend (`--mpe`) — most compatible
Each sounding note is sent on its own MIDI channel and bent to its exact pitch. Works with any synth that does per-note / MPE pitch bend (Pianoteq, Surge XT, most modern softsynths). Polyphony is capped at 15 simultaneous notes (one channel each).

```bash
python midi_retune.py 31edo.scl --mpe --bend-range 2
```

**`--bend-range` must match your synth's pitch-bend range.** If notes barely retune (adjacent microtones collapse to the same pitch), the range is set too high; if intervals are wildly exaggerated, it's too low. Common values: `2` (default GM / many synths), `48` (MPE spec default).

### MTS sysex (default, no `--mpe`) — cleaner where supported
Sends one MIDI Tuning Standard table that retunes all 128 keys, then passes notes straight through. Unlimited polyphony, native sustain, no channel juggling — **if** your synth honours MTS-over-MIDI.

```bash
python midi_retune.py 19edo.scl          # send table + pass notes through
python midi_retune.py 19edo.scl --once   # send table only, then exit
```

## Pianoteq notes (learned the hard way)

- **Pianoteq 8+**: MTS works. Set base Temperament = Equal, then Diapason ▸ *External tuning (MIDI, MTS-ESP)* ▸ *New notes only*. The script uses **device ID 0** (required) and throttles the sysex (Pianoteq drops fast bursts).
- **Pianoteq 7 (incl. Stage)**: MTS-over-MIDI is **not** honoured — use `--mpe`. Pianoteq 7's pitch-bend range is **±2 semitones**, so run with `--bend-range 2`. Set the MIDI input device to your loopMIDI port and Notes Channel = Any.

## Options

| Flag | Meaning |
|------|---------|
| `scl` (positional) | Scala `.scl` file (default `19edo.scl`) |
| `--mpe` | retune via per-note MPE pitch bend instead of MTS |
| `--bend-range N` | MPE pitch-bend range in semitones (default 48; use 2 for Pianoteq 7 / GM) |
| `--anchor N` | MIDI key mapped to scale degree 0 / the 1/1 (default 69 = A4) |
| `--base-freq F` | frequency of the anchor key in Hz (default 440) |
| `--in` / `--out` | input / output port (index or name substring) |
| `--once` | (MTS mode) send the tuning table and exit |
| `--dump` | print the key→frequency table and exit |
| `--list` | list MIDI ports and exit |

## Included scales

`12edo.scl`, `19edo.scl`, `24edo.scl` (quarter-tones), `31edo.scl`, `pythagorean.scl` (12-tone, pure 3/2 fifths), `bohlen-pierce.scl` (13 equal divisions of the 3/1 tritave). Drop in any other `.scl` file — cents and ratio entries are both supported.

Note: a scale's 1/1 lands on the `--anchor` key (default key 69 = A4). For a C-based Pythagorean centering, add `--anchor 60`.

## License

GPL-3.0 — see [LICENSE](LICENSE).
