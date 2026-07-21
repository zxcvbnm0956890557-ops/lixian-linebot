from models import OrderExtraction
from validation import validate_extraction


def base_extraction(**updates):
    values = {
        "customer_name": "王小美",
        "recipient_name": "李明勳",
        "recipient_phone": "0979869999",
        "recipient_address": "臺北市中山區松江路410號17F",
        "five_jin_boxes": 4,
        "confidence": 0.95,
    }
    values.update(updates)
    return OrderExtraction(**values)


def test_normal_order_uses_fixed_farm_sender(monkeypatch):
    monkeypatch.setenv("FARM_SENDER_NAME", "李鮮")
    result = validate_extraction(base_extraction(sender_mode="farm"))
    assert result.issues == ()
    assert result.order.sender_name == "李鮮"


def test_gift_changes_only_sender_name(monkeypatch):
    monkeypatch.setenv("FARM_SENDER_PHONE", "0986184111")
    monkeypatch.setenv("FARM_SENDER_ADDRESS", "南投縣國姓鄉中正路三段224-10號")
    result = validate_extraction(
        base_extraction(sender_mode="customer", sender_name=None)
    )
    assert result.issues == ()
    assert result.order.sender_name == "王小美"
    assert result.order.sender_phone == "0986184111"
    assert result.order.sender_address == "南投縣國姓鄉中正路三段224-10號"


def test_phone_cannot_pass_as_name():
    result = validate_extraction(base_extraction(recipient_name="0979869999"))
    assert result.order is None
    assert "收件人姓名缺少或格式不合理" in result.issues


def test_low_confidence_never_auto_accepts():
    result = validate_extraction(base_extraction(confidence=0.79))
    assert result.order is None
    assert "AI 判讀信心不足，需人工確認" in result.issues
