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


def test_accepts_taiwan_landline_with_area_code():
    signals = extract_signals("王素卿\n電話：04-7510163\n彰化市自強南路451巷7號10樓")
    assert signals.phones == ("047510163",)


def test_extracts_atomic_fields_from_one_line():
    signals = extract_signals("台北市中山區松江路410號17 F，0979869999，李明勳")
    assert signals.address_lines == ("台北市中山區松江路410號17 F",)
    assert signals.phones == ("0979869999",)
    assert signals.name_candidates == ("李明勳",)


def test_extracts_name_attached_to_phone():
    signals = extract_signals("花如彬0905276555\n新北市汐止區大同路一段337巷16弄42號\n5斤4箱")
    assert signals.name_candidates == ("花如彬",)


def test_delivery_note_is_not_a_name():
    signals = extract_signals("王素卿\n中午12:00前到貨")
    assert signals.name_candidates == ("王素卿",)
