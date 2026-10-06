import os
import sys
from unittest.mock import patch, MagicMock

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "src")))

import vhid_backend as vb  # noqa: E402
import destinations as dest  # noqa: E402


def _completed(returncode=0, stdout="", stderr=""):
    m = MagicMock()
    m.returncode = returncode
    m.stdout = stdout
    m.stderr = stderr
    return m


# ---- map_keys ------------------------------------------------------------

def test_map_keys_modifiers_and_letters():
    assert vb.map_keys(["cmd", "a"]) == "leftCommand+a"
    assert vb.map_keys(["ctrl", "shift", "4"]) == "leftControl+leftShift+4"


def test_map_keys_specials():
    assert vb.map_keys(["enter"]) == "return_or_enter"
    assert vb.map_keys(["escape"]) == "escape"
    assert vb.map_keys(["f5"]) == "f5"


def test_map_keys_unmapped_returns_none():
    assert vb.map_keys(["hyperspace"]) is None
    assert vb.map_keys(["cmd", "nope"]) is None


# ---- command construction (mock the ssh/vhid subprocess) -----------------

def test_type_text_builds_ssh_vhid_type():
    with patch("vhid_backend.subprocess.run", return_value=_completed(0, "")) as run:
        ok, _ = vb.type_text("dima@h", "Gagarin0!")
    assert ok
    argv = run.call_args[0][0]
    assert argv[0] == "ssh" and "dima@h" in argv
    assert argv[-3:] == ["vhid", "type", "Gagarin0!"]


def test_press_builds_chord():
    with patch("vhid_backend.subprocess.run", return_value=_completed(0, "")) as run:
        vb.press("dima@h", "return_or_enter")
    assert run.call_args[0][0][-2:] == ["press", "return_or_enter"]


def test_click_builds_flags_and_coords():
    with patch("vhid_backend.subprocess.run", return_value=_completed(0, "")) as run:
        vb.click("dima@h", 640, 480, button="right", times=2, modifiers="leftCommand")
    argv = run.call_args[0][0]
    assert "click" in argv and "640" in argv and "480" in argv
    assert "--button" in argv and "right" in argv
    assert "--times" in argv and "2" in argv
    assert "--modifiers" in argv and "leftCommand" in argv


def test_scroll_and_move_and_drag():
    with patch("vhid_backend.subprocess.run", return_value=_completed(0, "")) as run:
        vb.move("dima@h", 10, 20)
        assert run.call_args[0][0][-3:] == ["move", "10", "20"]
        vb.scroll("dima@h", 10, 20, 3)
        assert run.call_args[0][0][-3:] == ["--vertical", "3"] or "scroll" in run.call_args[0][0]
        vb.drag("dima@h", 1, 2, 3, 4)
        assert run.call_args[0][0][-5:] == ["drag", "1", "2", "3", "4"]


def test_is_ready_parses_doctor():
    with patch("vhid_backend.subprocess.run", return_value=_completed(0, "ready")):
        assert vb.is_ready("dima@h") is True
    with patch("vhid_backend.subprocess.run", return_value=_completed(0, "not ready\nenable driver")):
        assert vb.is_ready("dima@h") is False


def test_failure_returns_false_not_raise():
    with patch("vhid_backend.subprocess.run", return_value=_completed(1, "", "devices not up")):
        ok, detail = vb.type_text("dima@h", "x")
    assert ok is False and "devices not up" in detail


# ---- destinations.input_backend -----------------------------------------

def test_input_backend_selects_vhid_and_rfb():
    reg = ('{"default":"d","destinations":{'
           '"d":{"host":"h","input":"vhid","ssh":"dima@10.10.10.91"},'
           '"r":{"host":"h2"}}}')
    with patch.dict(os.environ, {"MACOS_DESTINATIONS": reg,
                                 "MACOS_DESTINATIONS_FILE": "/nonexistent"}):
        os.environ.pop("MACOS_HOST", None)
        assert dest.input_backend("d") == ("vhid", "dima@10.10.10.91")
        assert dest.input_backend("r") == ("rfb", None)


def test_input_backend_vhid_without_ssh_is_rfb():
    reg = '{"default":"d","destinations":{"d":{"host":"h","input":"vhid"}}}'
    with patch.dict(os.environ, {"MACOS_DESTINATIONS": reg,
                                 "MACOS_DESTINATIONS_FILE": "/nonexistent"}):
        os.environ.pop("MACOS_HOST", None)
        assert dest.input_backend("d") == ("rfb", None)
