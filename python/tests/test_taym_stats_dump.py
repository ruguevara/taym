"""TAYM stats + dump smoke tests (pytest).

Not pixel-exact: checks the diagnostics run on the canonical sample and report
the key facts. Structural dump must round-trip-readable; timeline must name
the decoded action.
"""
from taym import write_taym
from taym.sample import build_model
from taym.stats import stats, format_stats
from taym.dump import structural, timeline


def test_stats_values():
    s = stats(build_model())
    assert s["file_bytes"] == 230
    assert s["frame_count"] == 2 and s["timer_count"] == 1 and s["chip_count"] == 1
    assert s["command_hist"]["START"] == 1 and s["command_hist"]["STOP"] == 1
    assert s["pool_elems"] == {"VU08": 2, "VU16": 0, "VU32": 2}
    assert s["active_frames_per_timer"] == [1]


def test_format_stats_runs():
    out = format_stats(build_model())
    assert "TAYM stats" in out and "START" in out


def test_structural_dump():
    out = structural(write_taym(build_model()))
    assert "TRAK[0]" in out and "frame_rate=50Hz" in out
    assert "CHIP_PERIOD" in out and "VU32" in out
    assert "[25, 75]" in out


def test_timeline_dump():
    out = timeline(build_model())
    assert "T0 START" in out and "R8<-lane0[15, 0]" in out
    assert "tlan0(ABS,period)[25, 75]" in out
    assert "T0 STOP" in out


def test_info_in_stats_and_dump():
    m = build_model()
    m.info = {"title": "My Tune", "author": "Someone"}
    s = stats(m)
    assert s["info"] == {"title": "My Tune", "author": "Someone"}
    out = format_stats(m)
    assert "INFO" in out and "title" in out and "My Tune" in out
    dout = structural(write_taym(m))
    assert "title = My Tune" in dout and "author = Someone" in dout


def test_structural_dump_drops_zero_resv():
    out = structural(write_taym(build_model()))
    assert "resv=" not in out  # canonical sample has all-zero reserved fields


def test_timeline_decode_tlan():
    # CHIP_PERIOD: raw periods plus effective Hz (clock/(divider*period)).
    out = timeline(build_model(), decode_tlan=True)
    assert "25p=" in out and "Hz" in out
    assert "[25, 75]" not in out  # raw values replaced by decoded ones


def test_timeline_frame_range():
    out = timeline(build_model(), first=1, last=1)
    assert "frames 1..1" in out
    assert "frame 1:" in out and "frame 0:" not in out


def test_drop_empty_timers():
    from taym import drop_empty_timers, spec
    from taym.model import Taym, Trak, Timr, Mods
    e, s = spec.CMD_EMPTY, spec.CMD_START
    # 2 frames x 3 timers, timer 1 EMPTY everywhere -> dropped, MODS re-strided.
    mods = [Mods(s), Mods(e), Mods(s), Mods(e), Mods(e), Mods(s)]
    t = Taym(trak=Trak(frame_rate_hz=50, frame_count=2),
             timers=[Timr(0), Timr(0), Timr(0)], mods=mods)
    r = drop_empty_timers(t)
    assert len(r.timers) == 2
    assert [m.command for m in r.mods] == [s, s, e, s]
    assert len(t.timers) == 3  # input untouched


if __name__ == "__main__":
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for t in tests:
        t()
        print(f"ok  {t.__name__}")
    print(f"\n{len(tests)} passed")
