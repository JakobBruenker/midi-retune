"""
Retune Pianoteq (or any MTS-aware synth) to a Scala .scl tuning, over MIDI.

Why this exists
---------------
Pianoteq Stage has no Scala-import page, but its tuning menu
("Diapason -> External tuning (MIDI, MTS-ESP)") accepts the MIDI Tuning
Standard *real-time Single Note Tuning Change* message. This script reads a
.scl file and sends exactly that, retuning all 128 keys so that consecutive
keys play consecutive scale degrees ("N keys = one period/octave").

Signal chain:
    Keystation 88  ->  this script  ->  loopMIDI port  ->  Pianoteq

The script sends the tuning table once at startup, then passes your notes
straight through the same loopMIDI port. Full polyphony, native sustain.

Pianoteq setup (once):
    - Set the base Temperament to Equal Temperament (the MTS message alters
      the current tuning, so start from 12-ET).
    - Diapason dropdown -> External tuning (MIDI, MTS-ESP) -> "New notes only
      (no pitch-bending)".
    - Set Pianoteq's MIDI input device to the loopMIDI port.

Usage
-----
    python scl2pianoteq.py --list                 # show MIDI ports
    python scl2pianoteq.py                         # uses 19edo.scl, auto ports
    python scl2pianoteq.py mytuning.scl            # any Scala file
    python scl2pianoteq.py 31edo.scl --anchor 60   # anchor on middle C
    python scl2pianoteq.py --dump                  # print key->freq table, no MIDI
"""

import argparse
import math
import sys
import time

import mido

DEFAULT_SCL = "19edo.scl"
ANCHOR_NOTE = 69          # this physical key maps to scale degree 0 (the 1/1)
BASE_FREQ = 440.0         # frequency of that key
DEVICE_ID = 0x00          # Pianoteq only accepts MTS sysex with device ID 0
TUNING_PROGRAM = 0x00


# ---------- Scala parsing ----------

def parse_scl(path):
    """Return (description, period_ratio, step_ratios) from a .scl file.

    step_ratios has one entry per scale degree within a period (degree 0 = 1.0
    is implicit and included). period_ratio is the last listed pitch (the
    interval of repetition, usually 2/1).
    """
    raw = []
    with open(path, encoding="utf-8", errors="replace") as f:
        for line in f:
            s = line.strip()
            if s.startswith("!"):          # comment line
                continue
            raw.append(s)
    if len(raw) < 2:
        sys.exit(f"{path}: not a valid .scl file (too few lines).")

    description = raw[0]
    try:
        count = int(raw[1].split()[0])
    except ValueError:
        sys.exit(f"{path}: expected a note count, got {raw[1]!r}.")

    pitches = []
    for s in raw[2:]:
        if not s:
            continue
        token = s.split()[0]               # ignore trailing comments
        pitches.append(parse_pitch(token))
        if len(pitches) == count:
            break
    if len(pitches) != count:
        sys.exit(f"{path}: declared {count} pitches but found {len(pitches)}.")

    period = pitches[-1]                    # last entry = interval of repetition
    step_ratios = [1.0] + pitches[:-1]      # degrees 0 .. count-1
    return description, period, step_ratios


def parse_pitch(token):
    """A Scala pitch is cents (has a '.') or a ratio a/b (or integer a -> a/1)."""
    if "." in token:
        cents = float(token)
        return 2.0 ** (cents / 1200.0)
    if "/" in token:
        a, b = token.split("/")
        return float(a) / float(b)
    return float(int(token))               # bare integer = a/1


# ---------- tuning math ----------

def key_frequencies(period, step_ratios, anchor, base_freq):
    """Frequency for every MIDI key 0..127 under the 'consecutive keys =
    consecutive degrees' mapping."""
    n = len(step_ratios)
    freqs = []
    for key in range(128):
        d = key - anchor
        p = math.floor(d / n)              # which period
        idx = d - p * n                    # degree within the period
        freqs.append(base_freq * (period ** p) * step_ratios[idx])
    return freqs


def freq_to_mts_bytes(freq):
    """Frequency -> 3-byte MTS frequency data (semitone + 14-bit fraction)."""
    midi = 69 + 12 * math.log2(freq / 440.0)
    semitone = int(math.floor(midi))
    frac = midi - semitone                 # 0..1 of a semitone
    fourteen = int(round(frac * 16384))
    if fourteen >= 16384:                  # carry
        fourteen -= 16384
        semitone += 1
    semitone = max(0, min(127, semitone))
    yy = (fourteen >> 7) & 0x7F
    zz = fourteen & 0x7F
    return semitone & 0x7F, yy, zz


def build_mts_messages(freqs, chunk=32):
    """One or more MTS real-time Single Note Tuning Change sysex messages
    covering all 128 keys (ll is 7-bit, so we batch)."""
    messages = []
    for start in range(0, 128, chunk):
        keys = range(start, min(start + chunk, 128))
        body = []
        for k in keys:
            xx, yy, zz = freq_to_mts_bytes(freqs[k])
            body += [k & 0x7F, xx, yy, zz]
        # F0 7F <dev> 08 02 <prog> <count> [kk xx yy zz]*count F7
        data = [0x7F, DEVICE_ID, 0x08, 0x02, TUNING_PROGRAM, len(list(keys))] + body
        messages.append(mido.Message("sysex", data=data))
    return messages


# ---------- ports ----------

def pick_port(names, requested, keywords, kind):
    if requested is not None:
        try:
            return names[int(requested)]
        except (ValueError, IndexError):
            pass
        for n in names:
            if requested.lower() in n.lower():
                return n
        sys.exit(f"No {kind} port matching {requested!r}. Use --list.")
    for kw in keywords:
        for n in names:
            if kw.lower() in n.lower():
                return n
    return None


def freq_to_pitchbend(freq, bend_range):
    """Frequency -> (nearest 12-TET note, pitchwheel -8192..8191) for bend_range semitones."""
    midi = 69 + 12 * math.log2(freq / 440.0)
    note = int(round(midi))
    offset = midi - note
    bend = int(round(offset / bend_range * 8192))
    return max(0, min(127, note)), max(-8192, min(8191, bend))


def run_mpe(freqs, args):
    """Retune via per-note MPE pitch bend. Requires Pianoteq's MPE mode ON."""
    out_name = pick_port(mido.get_output_names(), args.out,
                         ["loopmidi", "loop", "gs wavetable", "wavetable"], "output")
    if out_name is None:
        sys.exit("No output port found. Open loopMIDI and create a port.")
    in_name = pick_port(mido.get_input_names(), args.inp, ["keystation"], "input")
    if in_name is None:
        sys.exit("No keyboard input found.")

    port_out = mido.open_output(out_name)
    print(f"Output: {out_name}")
    print(f"Input : {in_name}")
    print(f"MODE  : MPE, bend range {args.bend_range} semitones")
    print("Enable MPE in Pianoteq. Ctrl+C to stop.\n", flush=True)

    members = list(range(1, 16))   # MIDI channels 2-16 (master = channel 1 / index 0)

    def cc(ch, ctrl, val):
        port_out.send(mido.Message("control_change", channel=ch, control=ctrl, value=val))

    def set_rpn(ch, lsb, value):
        cc(ch, 101, 0); cc(ch, 100, lsb); cc(ch, 6, value); cc(ch, 38, 0)

    # MPE Configuration Message: lower zone, 15 member channels (RPN 6 on master)
    set_rpn(0, 6, len(members))
    # pitch-bend sensitivity on every member channel
    for ch in members:
        set_rpn(ch, 0, int(args.bend_range))

    active = {}                    # incoming key -> (channel, sent 12-TET note)
    free = list(members)
    busy = []                      # channels in use, FIFO for voice stealing

    with mido.open_input(in_name) as port_in:
        try:
            for msg in port_in:
                if msg.type == "note_on" and msg.velocity > 0:
                    if msg.note in active:
                        continue
                    if free:
                        ch = free.pop(0)
                    else:
                        ch = busy.pop(0)
                        old = next((k for k, v in active.items() if v[0] == ch), None)
                        if old is not None:
                            _, on = active.pop(old)
                            port_out.send(mido.Message("note_off", channel=ch, note=on))
                    busy.append(ch)
                    note12, bend = freq_to_pitchbend(freqs[msg.note], args.bend_range)
                    port_out.send(mido.Message("pitchwheel", channel=ch, pitch=bend))
                    port_out.send(mido.Message("note_on", channel=ch, note=note12,
                                               velocity=msg.velocity))
                    active[msg.note] = (ch, note12)

                elif msg.type == "note_off" or (msg.type == "note_on" and msg.velocity == 0):
                    if msg.note in active:
                        ch, note12 = active.pop(msg.note)
                        port_out.send(mido.Message("note_off", channel=ch, note=note12))
                        if ch in busy:
                            busy.remove(ch)
                        free.append(ch)

                elif msg.type == "control_change" and msg.control in (1, 64):
                    for ch in members:
                        cc(ch, msg.control, msg.value)
        except KeyboardInterrupt:
            print("\nStopped.")
        finally:
            for ch in range(16):
                cc(ch, 123, 0)


def main():
    ap = argparse.ArgumentParser(description="Send a Scala tuning to Pianoteq via MTS over MIDI.")
    ap.add_argument("scl", nargs="?", default=DEFAULT_SCL, help="Scala .scl file")
    ap.add_argument("--list", action="store_true", help="list MIDI ports and exit")
    ap.add_argument("--dump", action="store_true", help="print key->frequency table and exit")
    ap.add_argument("--in", dest="inp", help="input port: index or name substring")
    ap.add_argument("--out", dest="out", help="output (loopMIDI) port: index or name substring")
    ap.add_argument("--anchor", type=int, default=ANCHOR_NOTE,
                    help=f"key mapped to scale degree 0 (default {ANCHOR_NOTE})")
    ap.add_argument("--base-freq", type=float, default=BASE_FREQ,
                    help=f"frequency of the anchor key (default {BASE_FREQ})")
    ap.add_argument("--once", action="store_true",
                    help="send tuning then exit (don't pass notes through)")
    ap.add_argument("--mpe", action="store_true",
                    help="retune via per-note MPE pitch bend instead of MTS sysex")
    ap.add_argument("--bend-range", type=float, default=48.0,
                    help="MPE pitch-bend range in semitones (default 48)")
    args = ap.parse_args()

    if args.list:
        print("INPUTS:")
        for i, n in enumerate(mido.get_input_names()):
            print(f"  [{i}] {n}")
        print("OUTPUTS:")
        for i, n in enumerate(mido.get_output_names()):
            print(f"  [{i}] {n}")
        return

    desc, period, steps = parse_scl(args.scl)
    freqs = key_frequencies(period, steps, args.anchor, args.base_freq)
    period_cents = 1200 * math.log2(period)
    print(f"Scale : {args.scl}  ({desc})")
    print(f"Layout: {len(steps)} keys / period, period = {period_cents:.2f} cents, "
          f"anchor key {args.anchor} = {args.base_freq} Hz")

    if args.dump:
        for k in range(max(0, args.anchor - 2), min(128, args.anchor + len(steps) + 2)):
            print(f"  key {k:3d} -> {freqs[k]:8.3f} Hz")
        return

    if args.mpe:
        run_mpe(freqs, args)
        return

    messages = build_mts_messages(freqs)

    outputs = mido.get_output_names()
    out_name = pick_port(outputs, args.out, ["loopmidi", "loop", "gs wavetable", "wavetable"], "output")
    if out_name is None:
        sys.exit("No output port found. Open loopMIDI and create a port, then --list.")
    port_out = mido.open_output(out_name)
    print(f"Output: {out_name}")

    # Pianoteq drops MTS data sent too fast, so space the chunks out.
    for m in messages:
        port_out.send(m)
        time.sleep(0.05)
    print(f"Sent tuning table ({len(messages)} sysex messages, device id {DEVICE_ID}).")

    if args.once:
        print("Done (--once). Pianoteq is now retuned.")
        return

    inputs = mido.get_input_names()
    in_name = pick_port(inputs, args.inp, ["keystation"], "input")
    if in_name is None:
        print("No keyboard input found; tuning sent. Passing nothing through.")
        print("(Point Pianoteq at the loopMIDI port and re-run, or use --once.)")
        return
    print(f"Input : {in_name}")
    print("Passing notes through, resending tuning every 2s. Ctrl+C to stop.\n", flush=True)

    # Refresh the tuning by sending ONE small chunk at a time, spread out, so
    # we never burst (Pianoteq drops fast bursts) and never block note forwarding.
    chunk_idx = 0
    last_chunk = time.monotonic()
    with mido.open_input(in_name) as port_in:
        try:
            while True:
                # forward notes immediately (low latency)
                for msg in port_in.iter_pending():
                    if msg.type not in ("clock", "active_sensing"):
                        port_out.send(msg)
                now = time.monotonic()
                if now - last_chunk >= 0.25:
                    port_out.send(messages[chunk_idx % len(messages)])
                    chunk_idx += 1
                    last_chunk = now
                time.sleep(0.001)
        except KeyboardInterrupt:
            print("\nStopped.")
        finally:
            for ch in range(16):
                port_out.send(mido.Message("control_change", channel=ch, control=123, value=0))


if __name__ == "__main__":
    main()
