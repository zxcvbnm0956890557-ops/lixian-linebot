from worker import split_test_reply_prefix


def test_test_prefix_enables_reply_and_is_removed_before_parsing():
    enabled, text = split_test_reply_prefix("#測試\n5斤1\n王小明")
    assert enabled is True
    assert text == "5斤1\n王小明"


def test_normal_order_remains_silent():
    enabled, text = split_test_reply_prefix("5斤1\n王小明")
    assert enabled is False
    assert text == "5斤1\n王小明"
