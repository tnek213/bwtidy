from typing import Any

import pytest

from tests.support.docs_shots import IMAGES, ROOT, ShotError, check_name, clip, images_dir, shot

VIEW = {"width": 1600, "height": 900}
BAR = {"x": 0, "y": 0, "width": 1600, "height": 64}
BUTTON = {"x": 100.4, "y": 200.6, "width": 50, "height": 30}
OTHER = {"x": 300, "y": 250, "width": 40, "height": 20}


def test_the_images_go_to_docs_images():
    assert IMAGES == ROOT / "docs" / "images"


def test_the_shots_go_to_the_directory_the_script_names_else_to_docs_images(monkeypatch, tmp_path):
    monkeypatch.delenv("BWTIDY_DOCS_SHOTS_DIR", raising=False)
    assert images_dir() == IMAGES
    monkeypatch.setenv("BWTIDY_DOCS_SHOTS_DIR", str(tmp_path))
    assert images_dir() == tmp_path


def test_a_window_is_the_viewport():
    assert clip("window", [("button", BUTTON)], VIEW) == {
        "x": 0,
        "y": 0,
        "width": 1600,
        "height": 900,
    }


def test_a_band_takes_the_full_width_and_the_margin_above_and_below():
    assert clip("band", [("button", BUTTON)], VIEW, 10) == {
        "x": 0,
        "y": 190,
        "width": 1600,
        "height": 51,
    }


def test_a_band_takes_two_margins():
    rect = clip("band", [("button", BUTTON), ("other", OTHER)], VIEW, (20, 5))
    assert rect == {"x": 0, "y": 180, "width": 1600, "height": 95}


def test_a_box_is_the_union_plus_the_margin_on_every_side():
    rect = clip("box", [("button", BUTTON), ("other", OTHER)], VIEW, 8)
    assert rect == {"x": 92, "y": 192, "width": 256, "height": 86}


def test_the_rectangle_is_clamped_to_the_viewport():
    assert clip("band", [("bar", BAR)], VIEW, 8) == {"x": 0, "y": 0, "width": 1600, "height": 72}
    corner = {"x": 1580, "y": 880, "width": 20, "height": 20}
    assert clip("box", [("corner", corner)], VIEW, 16) == {
        "x": 1564,
        "y": 864,
        "width": 36,
        "height": 36,
    }


@pytest.mark.parametrize(
    "box",
    [
        {"x": 1590, "y": 10, "width": 20, "height": 20},
        {"x": 10, "y": -30, "width": 20, "height": 20},
        {"x": 10, "y": 1000, "width": 20, "height": 20},
    ],
)
def test_an_element_outside_the_viewport_is_an_error_with_its_name(box):
    for frame in ("window", "band", "box"):
        with pytest.raises(ShotError, match="the menu button is outside the viewport"):
            clip(frame, [("button", BUTTON), ("the menu button", box)], VIEW)


def test_a_hidden_element_is_an_error_with_its_name():
    with pytest.raises(ShotError, match="the dialog is not visible"):
        clip("box", [("the dialog", None)], VIEW)


def test_a_band_or_box_needs_an_element():
    for frame in ("band", "box"):
        with pytest.raises(ShotError, match="at least one element"):
            clip(frame, [], VIEW)


def test_only_a_band_takes_two_margins():
    with pytest.raises(ShotError, match="only a band"):
        clip("box", [("button", BUTTON)], VIEW, (4, 8))


def test_an_unknown_frame_is_an_error():
    frame: Any = "page"
    with pytest.raises(ShotError, match="unknown frame"):
        clip(frame, [("button", BUTTON)], VIEW)


@pytest.mark.parametrize("name", ["app-bar", "menu-2", "x"])
def test_a_name_of_lowercase_letters_digits_and_hyphens_is_taken(name):
    taken: dict[str, str] = {}
    check_name(name, "test_a", taken)
    assert taken == {name: "test_a"}


@pytest.mark.parametrize("name", ["App-bar", "app_bar", "app bar", "app.png", "", "app/bar"])
def test_any_other_name_fails(name):
    with pytest.raises(ShotError, match="lowercase letters, digits and hyphens"):
        check_name(name, "test_a", {})


def test_a_name_taken_twice_fails_with_both_tests():
    taken: dict[str, str] = {}
    check_name("app-bar", "test_x.py::test_a", taken)
    with pytest.raises(ShotError, match=r"by test_x.py::test_a and test_y.py::test_b"):
        check_name("app-bar", "test_y.py::test_b", taken)


def test_outside_a_docs_run_a_shot_does_nothing(monkeypatch):
    monkeypatch.delenv("BWTIDY_DOCS_SHOTS", raising=False)
    ## no page at all: it must not touch one
    page: Any = None
    shot(page, "Not a valid name")
