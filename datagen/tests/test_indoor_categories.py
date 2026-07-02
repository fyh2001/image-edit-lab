"""室内家居类别白名单单测（纯 Python，不依赖 Blender）。"""
from datagen.worker.assets.indoor_categories import is_indoor


def test_keeps_household_objects():
    for c in ["bowl", "casserole", "painting", "spice_rack", "tray", "book", "lamp",
              "pillow", "vase", "desk", "chair", "television_set", "potted_plant"]:
        assert is_indoor(c), f"{c} 应被判为室内"


def test_drops_animals_vehicles_outdoor():
    for c in ["giant_panda", "dalmatian", "vulture", "domestic_ass", "dirt_bike",
              "solar_array", "bullhorn", "cymbal", "halter_top", "necktie", "sweatband"]:
        assert not is_indoor(c), f"{c} 不应被判为室内"


def test_normalization_strips_parenthetical():
    assert is_indoor("date_(fruit)")
    assert is_indoor("truffle_(chocolate)")
    assert is_indoor("statue_(sculpture)")


def test_multiword_lvis_categories():
    # LVIS 多词规范类名应精确命中
    for c in ["wine_glass", "coffee_table", "teddy_bear", "potted_plant", "alarm_clock"]:
        assert is_indoor(c), c


def test_empty_or_none():
    assert not is_indoor(None)
    assert not is_indoor("")
