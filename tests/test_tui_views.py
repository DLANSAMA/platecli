"""The TUI's view models and the widgets that draw them (T-1, the redesign).

The pure helpers in ``tui.services`` decide every word and number the dashboard
and monitor show, so most of this file needs no pilot. The pilot cases at the
bottom pin what only a running app can show: the theme, the filament colour on
a tray card, the AMS strip updating in place, and the layouts that must fit an
80x24 terminal.
"""

from __future__ import annotations

import argparse
from dataclasses import replace
from datetime import datetime

import pytest

pytest.importorskip("textual")

from rich.console import Console  # noqa: E402

from bambu_cli import context as _context  # noqa: E402
from bambu_cli.ams import tray_label  # noqa: E402
from bambu_cli.context import RuntimeContext, Settings  # noqa: E402
from bambu_cli.interactive.core import ams_slot_label  # noqa: E402
from bambu_cli.tui.app import PlateApp  # noqa: E402
from bambu_cli.tui.deps import TuiDeps  # noqa: E402
from bambu_cli.tui.screens.help import HELP_ROWS, HelpScreen, _balanced_split, help_grid  # noqa: E402
from bambu_cli.tui.services import (  # noqa: E402
    StatusSnapshot,
    TempHistory,
    TempView,
    ams_view,
    eta_clock,
    feed_text,
    job_name,
    job_view,
    printer_descriptor,
    state_badge,
    swatch_colors,
    temp_view,
    wifi_text,
)
from bambu_cli.tui.widgets.ams_strip import AmsStrip, TrayCard  # noqa: E402
from bambu_cli.tui.widgets.summary import SeparatedValue, summary_grid  # noqa: E402
from bambu_cli.tui.widgets.temps import spark_ceiling, spark_rows  # noqa: E402
from tests.tui_text import renderable_text, tree_text  # noqa: E402

NOW = datetime(2026, 11, 6, 16, 32)


def _tray(slot, ftype="PLA", color="F2F2F2", remain=80, active=False):
    return {"slot": slot, "type": ftype, "color": color, "remain": remain, "empty": not ftype, "active": active}


def _snap(state="RUNNING", ams=None, **raw):
    base = {
        "gcode_state": state,
        "mc_percent": 47,
        "mc_remaining_time": 83,
        "layer_num": 112,
        "total_layer_num": 240,
        "gcode_file": "benchy.gcode.3mf",
    }
    base.update(raw)
    return StatusSnapshot(ok=True, raw=base, ams=ams)


def _one_unit(active_slot=1, **unit):
    trays = [
        _tray(0),
        _tray(1, "PETG", "0A6AC8", 62),
        _tray(2, None, None, None),
        _tray(3, "TPU", "D9381E", -1),
    ]
    for tray in trays:
        tray["active"] = tray["slot"] == active_slot
    return {"active_tray": active_slot, "units": [{"id": 0, "humidity": 4, "temp": 27.0, "trays": trays, **unit}]}


# --- state names --------------------------------------------------------------


@pytest.mark.parametrize(
    ("state", "label", "tone"),
    [
        ("RUNNING", "Printing", "active"),
        ("PAUSE", "Paused", "paused"),
        ("PREPARE", "Preparing", "busy"),
        ("FINISH", "Finished", "done"),
        ("FAILED", "Failed", "error"),
        ("IDLE", "Idle", "idle"),
        ("running", "Printing", "active"),
    ],
)
def test_state_badge_says_the_state_in_words(state, label, tone):
    badge = state_badge(_snap(state))
    assert (badge.label, badge.tone) == (label, tone)


def test_unknown_and_missing_states_are_shown_not_hidden():
    assert state_badge(_snap("CALIBRATING_LIDAR")).label == "Calibrating_Lidar"
    assert state_badge(_snap("")).label == "Unknown"
    assert state_badge(StatusSnapshot(ok=False, error="x")).label == "Offline"


# --- time ---------------------------------------------------------------------


def test_eta_clock_names_the_day_when_the_print_ends_tomorrow():
    assert eta_clock(83, NOW) == "17:55"
    assert eta_clock(10 * 60, NOW) == "Sat 02:32"


@pytest.mark.parametrize("minutes", [None, "soon", 0, -5, float("nan"), 10**12, True])
def test_eta_clock_refuses_nonsense(minutes):
    assert eta_clock(minutes, NOW) is None


# --- job panel ----------------------------------------------------------------


def test_running_job_view_reads_like_a_sentence():
    view = job_view(_snap(ams=_one_unit()), NOW)
    assert view.name == "benchy"
    assert view.percent == 47
    assert view.lines == ("1h 23m left · done about 17:55", "Layer 112 of 240", "PETG from A2")


def test_paused_finished_and_failed_keep_their_percent():
    assert job_view(_snap("PAUSE"), NOW).lines[0] == "Paused · 1h 23m left"
    finished = job_view(_snap("FINISH", mc_percent=100, mc_remaining_time=0), NOW)
    assert (finished.percent, finished.lines[0]) == (100, "Finished")
    failed = job_view(_snap("FAILED", mc_percent=63), NOW)
    assert failed.percent == 63
    assert failed.lines[0] == "This print failed"
    assert "screen" in failed.lines[-1]


def test_zero_minutes_left_means_finishing_only_near_the_end():
    assert job_view(_snap(mc_percent=96, mc_remaining_time=0), NOW).lines[0] == "Finishing up"
    # At the start the printer reports 0 before its estimate is in.
    assert job_view(_snap(mc_percent=0, mc_remaining_time=0), NOW).lines[0] == "Time left unknown"


@pytest.mark.parametrize(
    ("raw", "absent"),
    [
        ({"mc_remaining_time": 1e308}, "1e"),
        ({"mc_remaining_time": -10}, "left ·"),
        ({"layer_num": -5, "total_layer_num": -5}, "Layer"),
        ({"layer_num": 10**9, "total_layer_num": 0}, "Layer"),
    ],
)
def test_absurd_readings_are_not_shown(raw, absent):
    lines = " ".join(job_view(_snap(**raw), NOW).lines)
    assert absent not in lines
    assert len(lines) < 120


def test_missing_or_new_states_are_not_called_ready():
    assert job_view(_snap(None), NOW).lines == ("Waiting for the printer's state",)
    assert job_view(_snap("LIDAR_SCAN"), NOW).lines == ("Printer state: Lidar_Scan",)


def test_layer_line_copes_with_unknown_totals():
    assert job_view(_snap(total_layer_num=0), NOW).lines[1] == "Layer 112"
    assert "Layer" not in " ".join(job_view(_snap(layer_num=0, total_layer_num=0), NOW).lines)


def test_preparing_has_no_percent_to_mislead_with():
    view = job_view(_snap("PREPARE", mc_percent=0), NOW)
    assert view.percent is None
    assert view.lines == ("Getting ready to print", "benchy")


def test_idle_printer_is_not_named_after_its_last_job():
    view = job_view(_snap("IDLE", ams=_one_unit(active_slot=0)), NOW)
    assert view.name == ""
    assert view.percent is None
    assert view.lines == ("Ready for a print", "Loaded: PLA from A1")


def test_idle_without_any_ams_data_says_only_ready():
    assert job_view(_snap("IDLE"), NOW).lines == ("Ready for a print",)


def test_offline_view_carries_the_reason():
    view = job_view(StatusSnapshot(ok=False, error=""), NOW)
    assert view.badge.label == "Offline"
    assert view.lines == ("Can't reach the printer", "No status came back.")


def test_job_name_prefers_the_subtask_and_strips_paths_and_suffixes():
    assert job_name(_snap(subtask_name="Benchy v2")) == "Benchy v2"
    assert job_name(_snap(gcode_file="/sdcard/cache/cube.GCODE.3MF")) == "cube"
    assert job_name(_snap(gcode_file="C:\\models\\plate.gcode")) == "plate"
    assert job_name(_snap(gcode_file=".gcode")) == ".gcode"  # never an empty name from a suffix alone
    assert job_name(StatusSnapshot(ok=False)) == ""


def test_wifi_text_spaces_the_unit_and_stays_short():
    assert wifi_text(_snap(wifi_signal="-48dBm")) == "Wi-Fi -48 dBm"
    assert wifi_text(_snap(wifi_signal="-48 dBm")) == "Wi-Fi -48 dBm"
    assert wifi_text(_snap()) is None
    long = wifi_text(_snap(wifi_signal="x" * 500))
    assert long is not None and len(long) <= len("Wi-Fi ") + 12 and long.endswith("…")


# --- AMS ------------------------------------------------------------------------


def test_tray_labels_match_bambu_studio():
    assert [tray_label(0, slot) for slot in range(4)] == ["A1", "A2", "A3", "A4"]
    assert tray_label(1, 2) == "B3"
    assert tray_label(128, 0) == "128·1"  # an AMS HT has no letter
    assert ams_slot_label(6) == "AMS B3"
    assert ams_slot_label(512) == "AMS 128·1"


@pytest.mark.parametrize(
    ("color", "expected"),
    [
        ("FFFF00", ("#FFFF00", "#0D1117")),  # yellow gets dark text
        ("0000FF", ("#0000FF", "#F5F7FA")),  # blue gets light text
        ("#f2f2f2", ("#F2F2F2", "#0D1117")),
        ("000000", ("#000000", "#F5F7FA")),
        ("0A6AC8FF", ("#0A6AC8", "#F5F7FA")),
    ],
)
def test_swatch_colors_pick_readable_ink(color, expected):
    assert swatch_colors(color) == expected


@pytest.mark.parametrize("color", [None, "", "zzzzzz", "12345", 0xFFFFFF, "red"])
def test_swatch_colors_reject_anything_that_is_not_a_colour(color):
    assert swatch_colors(color) is None


def test_ams_view_names_trays_and_cleans_readings():
    view = ams_view(_snap(ams=_one_unit()))
    assert view is not None
    (unit,) = view.units
    assert (unit.title, unit.detail) == ("AMS A", "humidity 4 · 27 °C")
    a1, a2, a3, a4 = unit.trays
    assert (a1.label, a1.material, a1.swatch, a1.remain) == ("A1", "PLA", "#F2F2F2", 80)
    assert a2.active and a2.swatch == "#0A6AC8"
    assert a3.empty and a3.material == "" and a3.swatch is None and a3.remain is None
    assert a4.remain is None  # -1: the printer cannot measure it


def test_ams_view_second_unit_external_spool_and_bad_readings():
    ams = _one_unit()
    ams["units"].append({"id": 1, "humidity": None, "temp": 0, "trays": [_tray(0, "ABS", "bad", 250)]})
    ams["external_tray"] = _tray(254, "PLA-CF", "1A1A1A", None, active=True)
    view = ams_view(_snap(ams=ams))
    assert view is not None
    assert [unit.title for unit in view.units] == ["AMS A", "AMS B"]
    b1 = view.units[1].trays[0]
    assert (b1.label, b1.swatch, b1.remain) == ("B1", None, 100)  # junk colour dropped, 250% clamped
    assert view.units[1].detail == ""  # no humidity, and an AMS lite's 0 °C is no reading
    assert view.external is not None and view.external.label == "Ext"
    assert view.trays[-1] is view.external
    assert feed_text(_snap(ams=ams)) == "PETG from A2"


def test_ams_view_skips_malformed_units_and_trays():
    ams = {"units": ["x", {"id": 0, "trays": ["y", _tray(1)]}, None]}
    view = ams_view(_snap(ams=ams))
    assert view is not None
    assert [tray.label for tray in view.trays] == ["A2"]


def test_ams_ht_units_get_a_plain_numbered_title():
    view = ams_view(_snap(ams={"units": [{"id": 128, "trays": [_tray(0)]}]}))
    assert view is not None
    assert view.units[0].title == "AMS 128"
    assert view.units[0].trays[0].label == "128·1"


def test_feed_text_names_the_external_spool_and_nothing_when_idle():
    external_only = {"units": [], "external_tray": _tray(254, "PETG", "FFFFFF", None, active=True)}
    assert feed_text(_snap(ams=external_only)) == "PETG from the external spool"
    assert feed_text(_snap(ams={"units": [], "external_tray": _tray(254, active=False)})) is None
    assert ams_view(_snap(ams={"units": [], "external_tray": None})) is None
    assert ams_view(StatusSnapshot(ok=False)) is None


# --- heaters ------------------------------------------------------------------


@pytest.mark.parametrize(
    ("current", "target", "caption", "changing"),
    [
        (219.6, 220, "target 220 °C", None),
        (150, 220, "heating to 220 °C", "heating"),
        (90, 60, "cooling to 60 °C", "cooling"),
        (25, None, "heater off", None),
    ],
)
def test_temp_caption_says_what_the_heater_is_doing(current, target, caption, changing):
    view = TempView(current, target)
    assert (view.caption, view.changing) == (caption, changing)


def test_temp_view_treats_a_zero_target_as_off_and_junk_as_unknown():
    view = temp_view(_snap(nozzle_temper="hot", nozzle_target_temper=0), "nozzle_temper", "nozzle_target_temper")
    assert (view.current, view.target, view.value) == (None, None, "—")
    assert temp_view(StatusSnapshot(ok=False), "a", "b") == TempView(None, None)
    absurd = temp_view(_snap(bed_temper=1e308, bed_target_temper=1e308), "bed_temper", "bed_target_temper")
    assert absurd == TempView(None, None)


def test_temp_history_skips_failed_fetches_and_keeps_a_window():
    history = TempHistory(size=3)
    for value in (20, 40, 60, 80):
        history.add(_snap(nozzle_temper=value, bed_temper=value / 2))
        history.add(StatusSnapshot(ok=False))
    assert history.series("nozzle") == [40, 60, 80]
    assert history.series("bed") == [20, 30, 40]
    assert history.series("chamber") == []
    history.add(_snap(nozzle_temper=1e308, bed_temper=float("inf")))
    assert history.series("nozzle") == [40, 60, 80]  # a spoofed reading never sets the scale


def test_sparkline_is_drawn_on_a_fixed_scale_newest_on_the_right():
    rows = spark_rows([300, 150, 0], width=5, height=2, ceiling=300)
    assert rows == ["  █  ", "  ██ "]
    # A heater holding its target does not wobble across the full height.
    steady = spark_rows([219, 220, 221], width=3, height=1, ceiling=spark_ceiling([219, 220, 221], 220, 300))
    assert len(set(steady[0])) == 1
    assert spark_ceiling([350], 220, 300) == 350
    assert spark_rows([], width=0, height=2, ceiling=300) == ["", ""]


# --- the summary grid -----------------------------------------------------------


def _render(renderable, width):
    console = Console(width=width, no_color=True, highlight=False)
    with console.capture() as capture:
        console.print(renderable)
    return capture.get()


def test_summary_values_wrap_between_parts_never_inside_one():
    rows = [("Material", "PLA  ·  Quality: standard (0.20mm)  ·  Supports: no")]
    narrow = _render(summary_grid(rows), 40)
    assert "Supports: no" in narrow
    assert "Quality: standard (0.20mm)" in narrow
    wide = _render(summary_grid(rows), 90)
    assert "PLA  ·  Quality: standard (0.20mm)  ·  Supports: no" in wide


def test_summary_part_wider_than_the_column_still_shows_in_full():
    value = SeparatedValue(["a-really-long-model-file-name-with-no-spaces.stl", "x"])
    assert "".join(_render(value, 20).split()) == "a-really-long-model-file-name-with-no-spaces.stlx"


def test_summary_markup_shaped_values_stay_verbatim():
    out = _render(summary_grid([("File", "a[/b]c  ·  [remix]")]), 60)
    assert "a[/b]c  ·  [remix]" in out


# --- help -----------------------------------------------------------------------


def test_help_columns_balance_and_keep_every_key():
    heights = [len(rows) + 1 for _name, rows in HELP_ROWS]
    cut = _balanced_split(heights)
    assert 0 < cut < len(heights)
    text = renderable_text(help_grid(), width=74)
    for section, rows in HELP_ROWS:
        assert section in text
        for _key, description in rows:
            assert description in text
    assert "escape" not in text  # spelled "esc", as the footer does


# --- the printer line ---------------------------------------------------------


@pytest.fixture
def _restore_context():
    saved = _context.get_current()
    yield
    _context.set_current(saved)


@pytest.mark.usefixtures("_restore_context")
def test_printer_descriptor_says_model_and_address_or_why_not():
    def use(simulation=False, **settings):
        _context.set_current(RuntimeContext(settings=replace(Settings(), **settings), simulation=simulation))
        return printer_descriptor()

    assert use(printer_model="P1S", printer_ip="192.168.1.23") == "P1S · 192.168.1.23"
    assert use(printer_model="P1S", printer_ip="0.0.0.0") == "P1S · not set up"
    assert use(printer_model="A1", printer_ip="10.0.0.2", simulation=True) == "Simulated A1"


# --- pilot ----------------------------------------------------------------------


class _Scripted:
    def __init__(self, snapshots):
        self.snapshots = list(snapshots)
        self.calls = 0

    def fetch(self, args):
        self.calls += 1
        return self.snapshots[min(self.calls - 1, len(self.snapshots) - 1)]


def _app(snapshots):
    args = argparse.Namespace(cmd="tui", sim=False, json=False, verbose=False)
    return PlateApp(args, TuiDeps(status_provider=_Scripted(snapshots)))


async def test_app_uses_the_platecli_theme():
    app = _app([_snap()])
    async with app.run_test() as pilot:
        await pilot.pause()
        assert app.theme == "platecli"
        assert "platecli-light" in app.available_themes


async def test_tray_card_paints_the_filament_colour():
    app = _app([_snap(ams=_one_unit())])
    async with app.run_test() as pilot:
        await pilot.pause()
        cards = list(app.screen.query(TrayCard))
        assert [card.tray.label for card in cards] == ["A1", "A2", "A3", "A4"]
        drawn = cards[1].render()
        swatch = drawn.spans[0].style  # the first row is the swatch, material on it
        assert swatch.bgcolor.name == "#0a6ac8"
        assert swatch.color.name == "#f5f7fa"  # light ink on a dark blue
        assert cards[1].has_class("-active")
        assert "▶ A2" in tree_text(cards[1])
        assert "empty" in tree_text(cards[2])


async def test_ams_strip_updates_in_place_and_rebuilds_when_the_layout_changes():
    two_units = _one_unit()
    two_units["units"].append({"id": 1, "humidity": 3, "temp": 25.0, "trays": [_tray(0, "ASA", "222222", 40)]})
    swapped = _one_unit()
    swapped["units"][0]["trays"][0] = _tray(0, "PLA Silk", "C0C0C0", 75)
    app = _app([_snap(ams=_one_unit()), _snap(ams=swapped), _snap(ams=two_units), _snap(ams=None)])
    async with app.run_test() as pilot:
        await pilot.pause()
        strip = app.screen.query_one("#ams-panel", AmsStrip)
        first = strip.query(TrayCard).first()

        await pilot.press("r")
        await pilot.pause()
        assert strip.query(TrayCard).first() is first  # same layout: updated, not remounted
        assert "PLA Silk" in tree_text(strip)

        await pilot.press("r")
        await pilot.pause()
        assert "AMS B" in tree_text(strip)
        assert "ASA" in tree_text(strip)

        await pilot.press("r")
        await pilot.pause()
        assert "No AMS or external spool reported" in tree_text(strip)


async def test_ams_note_follows_the_printer_coming_back_without_an_ams():
    app = _app([StatusSnapshot(ok=False, error="x"), _snap("IDLE", ams=None), StatusSnapshot(ok=False, error="x")])
    async with app.run_test() as pilot:
        await pilot.pause()
        strip = app.screen.query_one("#ams-panel", AmsStrip)
        assert "AMS unavailable while offline" in tree_text(strip)
        await pilot.press("r")
        await pilot.pause()
        assert "No AMS or external spool reported" in tree_text(strip)
        await pilot.press("r")
        await pilot.pause()
        assert "AMS unavailable while offline" in tree_text(strip)


async def test_printer_strings_are_shown_verbatim_in_the_top_bar_and_cards():
    tray = _tray(0, "PLA[/b]", "FFFF00", 50, active=True)
    snap = _snap("ODD[/b]STATE", ams={"active_tray": 0, "units": [{"id": 0, "trays": [tray]}]}, wifi_signal="[/i]x")
    app = _app([snap])
    async with app.run_test() as pilot:
        await pilot.pause()
        bar = tree_text(app.screen.query_one("#topbar"))
        assert "[/i]x" in bar
        assert "Odd[/B]State" in bar
        assert "PLA[/b]" in tree_text(app.screen.query_one(TrayCard))


async def test_sparkline_and_cards_render_at_zero_size():
    from bambu_cli.tui.widgets.temps import Spark

    app = _app([_snap(ams=_one_unit())])
    async with app.run_test(size=(12, 6)) as pilot:
        await pilot.pause()
        for spark in app.screen.query(Spark):
            spark.render()
        for card in app.screen.query(TrayCard):
            card.render()
    assert spark_rows([100], width=0, height=0, ceiling=0) == [""]


async def test_offline_dashboard_says_so_everywhere():
    app = _app([StatusSnapshot(ok=False, error="Timed out")])
    async with app.run_test() as pilot:
        await pilot.pause()
        text = tree_text(app.screen)
        assert "Offline" in text
        assert "Can't reach the printer" in text
        assert "Timed out" in text
        assert "AMS unavailable while offline" in text


async def test_help_fits_80x24_without_scrolling():
    app = _app([_snap()])
    async with app.run_test(size=(80, 24)) as pilot:
        await pilot.pause()
        app.push_screen(HelpScreen())
        await pilot.pause()
        body = app.screen.query_one("#help-body")
        assert body.outer_size.width <= 80
        assert body.max_scroll_y == 0
        assert "Keys" == str(body.border_title)
        assert not app.screen.query("#help-title")  # the title is the border's alone
