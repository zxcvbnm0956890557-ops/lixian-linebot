from models import OrderBatchExtraction, OrderExtraction
from order_parser import OrderParser, normalize_order_boundaries


class FakeResponses:
    def __init__(self, extraction):
        self.extraction = extraction

    def parse(self, **_kwargs):
        return type("Response", (), {"output_parsed": self.extraction})()


class FakeClient:
    def __init__(self, extraction):
        self.responses = FakeResponses(extraction)


def test_unique_explicit_name_can_fill_missing_recipient():
    extraction = OrderExtraction(
        customer_name="李明勳",
        recipient_name=None,
        recipient_phone="0979869999",
        recipient_address="台北市中山區松江路410號17F",
        five_jin_boxes=20,
        confidence=0.98,
    )
    parser = OrderParser(client=FakeClient(OrderBatchExtraction(orders=[extraction])))
    result = parser.parse("5斤20\n台北市中山區松江路410號17F，0979869999，李明勳\n管理員代收")
    assert result.orders[0].recipient_name == "李明勳"


def test_two_names_are_never_guessed():
    extraction = OrderExtraction(
        customer_name="王小美",
        recipient_name=None,
        recipient_phone="0979869999",
        recipient_address="台北市中山區松江路410號17F",
        five_jin_boxes=1,
        confidence=0.70,
    )
    parser = OrderParser(client=FakeClient(OrderBatchExtraction(orders=[extraction])))
    result = parser.parse("王小美\n李明勳\n0979869999\n台北市中山區松江路410號17F\n5斤1")
    assert result.orders[0].recipient_name is None


def test_phone_stuck_to_next_order_spec_gets_safe_boundary():
    raw = "彰化市中山路二段521號4樓\n09372369135斤4箱（27號配送）\n花如彬0905276555"
    normalized = normalize_order_boundaries(raw)
    assert "0937236913\n5斤4箱" in normalized
    assert "花如彬0905276555" in normalized


def test_unique_hard_signals_correct_ai_field_mixup():
    extraction = OrderExtraction(
        recipient_name="0979869999",
        recipient_phone="李明勳",
        recipient_address="台北市中山區松江路410號17 F，0979869999，李明勳",
        five_jin_boxes=0,
        confidence=0.55,
        ambiguities=["欄位順序不確定"],
    )
    parser = OrderParser(
        client=FakeClient(
            OrderBatchExtraction(
                orders=[extraction], batch_ambiguities=["欄位順序不確定"]
            )
        )
    )
    result = parser.parse(
        "5斤20\n台北市中山區松江路410號17 F，0979869999，李明勳\n管理員代收"
    )
    order = result.orders[0]
    assert order.recipient_name == "李明勳"
    assert order.recipient_phone == "0979869999"
    assert order.recipient_address == "台北市中山區松江路410號17 F"
    assert order.five_jin_boxes == 20
    assert order.confidence >= 0.95
    assert order.ambiguities == []
    assert result.batch_ambiguities == []


def test_gift_order_is_never_overridden_by_single_order_reconciliation():
    extraction = OrderExtraction(
        customer_name="王小美",
        recipient_name="李明勳",
        recipient_phone="0979869999",
        recipient_address="台北市中山區松江路410號17F",
        five_jin_boxes=1,
        sender_mode="customer",
        sender_name="王小美",
        confidence=0.75,
        ambiguities=["需確認寄件人"],
    )
    parser = OrderParser(client=FakeClient(OrderBatchExtraction(orders=[extraction])))
    result = parser.parse(
        "送朋友，寄件人用我的名字寄\n王小美\n收件人：李明勳\n0979869999\n"
        "台北市中山區松江路410號17F\n5斤1"
    )
    assert result.orders[0].sender_mode == "customer"
    assert result.orders[0].ambiguities == ["需確認寄件人"]
