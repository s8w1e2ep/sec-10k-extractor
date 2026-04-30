from extractor.status_detect import detect_status


def test_reserved_short_text():
    assert detect_status("6", "Item 6. [Reserved]") == "reserved"
    assert detect_status("6", "Item 6.\n\n[Reserved]") == "reserved"


def test_not_applicable_short():
    assert detect_status("4", "Item 4. Mine Safety Disclosures.\n\nNot applicable.") == "not_applicable"
    assert detect_status("1B", "Item 1B. Unresolved Staff Comments.\n\nNone.") == "not_applicable"


def test_incorporated_by_reference_typical():
    text = (
        "Item 10. Directors, Executive Officers and Corporate Governance.\n\n"
        "The information required by this Item is incorporated herein by reference "
        "to the registrant's definitive Proxy Statement for the 2024 Annual Meeting "
        "of Stockholders, under the captions 'Election of Directors' and "
        "'Information About Our Executive Officers'."
    )
    assert detect_status("10", text) == "incorporated_by_reference"


def test_extracted_long_content_with_ibr_phrase_inside():
    """IBR phrase appearing inside a long Item is NOT IBR (false-positive guard)."""
    text = (
        "Item 1A. Risk Factors.\n\n"
        + "Risk text. " * 200  # ~2200 chars
        + "Some terms are incorporated by reference into our credit agreements. "
        + "More risk text. " * 100
    )
    assert detect_status("1A", text) == "extracted"


def test_extracted_default_for_normal_business_section():
    text = (
        "Item 1. Business.\n\n"
        "Apple Inc. designs, manufactures, and markets smartphones, "
        "personal computers, tablets, wearables, and accessories. "
        + "Lots of business description. " * 100
    )
    assert detect_status("1", text) == "extracted"


def test_reserved_long_text_is_extracted():
    """If 'reserved' appears but content is long, it's actual content."""
    text = "Item 1A. Risk Factors.\n\n" + "Reserved capacity discussion. " * 100
    assert detect_status("1A", text) == "extracted"
