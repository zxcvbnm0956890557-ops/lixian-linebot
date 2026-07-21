from heuristics import extract_signals


def test_extracts_order_independent_signals():
    text = "雲林縣斗六市崙南路128號\n10斤1\n0978-006-578\n江珈儀"
    signals = extract_signals(text)
    assert signals.phones == ("0978006578",)
    assert signals.ten_jin_boxes == 1
    assert signals.five_jin_boxes == 0
    assert signals.address_lines == ("雲林縣斗六市崙南路128號",)
    assert signals.name_candidates == ("江珈儀",)


def test_accepts_common_quantity_typing_styles():
    signals = extract_signals("5斤32\n5斤＋4\n10斤：2箱")
    assert signals.five_jin_boxes == 36
    assert signals.ten_jin_boxes == 2


def test_finds_phone_even_when_fields_share_one_line():
    signals = extract_signals("台北市中山區松江路410號17F，0979869999，李明勳")
    assert signals.phones == ("0979869999",)
    assert len(signals.address_lines) == 1
    assert signals.name_candidates == ("李明勳",)


def test_note_is_not_treated_as_name():
    signals = extract_signals("李明勳\n管理員代收")
    assert signals.name_candidates == ("李明勳",)
