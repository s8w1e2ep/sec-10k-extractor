from datetime import date

from extractor.canonical_items import (
    CANONICAL_ITEMS,
    expected_items_for_period,
    fuzzy_match_title,
    get_canonical_item,
    part_sort_key,
    sort_key,
)


def test_catalog_has_23_items():
    """7 in Part I + 9 in Part II + 5 in Part III + 2 in Part IV = 23 sections.

    16 main item numbers (1, 2, 3, ..., 16) but with sub-items A/B/C, total 23.
    """
    assert len(CANONICAL_ITEMS) == 23


def test_sort_key_orders_correctly():
    nums = ["1", "1A", "1B", "1C", "2", "9A", "9B", "9C", "10", "16"]
    assert sorted(nums, key=sort_key) == nums


def test_part_sort_key_keeps_parts_separate():
    items = [("II", "5"), ("I", "16"), ("I", "1"), ("IV", "15"), ("III", "10")]
    sorted_items = sorted(items, key=lambda t: part_sort_key(*t))
    assert [t[0] for t in sorted_items] == ["I", "I", "II", "III", "IV"]


def test_period_filter_excludes_pre_2023_cybersecurity():
    items_2022 = expected_items_for_period(date(2022, 12, 31))
    nums_2022 = {it.item_number for it in items_2022}
    assert "1C" not in nums_2022
    assert "9C" in nums_2022  # 9C is FY 2021+

    items_2023 = expected_items_for_period(date(2023, 12, 31))
    nums_2023 = {it.item_number for it in items_2023}
    assert "1C" in nums_2023


def test_period_filter_excludes_pre_2021_9c():
    items_2020 = expected_items_for_period(date(2020, 12, 31))
    nums_2020 = {it.item_number for it in items_2020}
    assert "9C" not in nums_2020


def test_period_filter_none_returns_full_catalog():
    assert expected_items_for_period(None) == list(CANONICAL_ITEMS)


def test_fuzzy_match_title():
    item = fuzzy_match_title("Risk Factors")
    assert item is not None
    assert item.item_number == "1A"

    item = fuzzy_match_title("MD&A")
    assert item is not None
    assert item.item_number == "7"

    item = fuzzy_match_title("Selected Financial Data")
    assert item is not None
    assert item.item_number == "6"


def test_fuzzy_match_returns_none_for_garbage():
    assert fuzzy_match_title("xyzzzy nonsense", threshold=80) is None


def test_get_canonical_item_case_insensitive():
    assert get_canonical_item("1a") is not None
    assert get_canonical_item("1A") is not None
    assert get_canonical_item("99") is None
