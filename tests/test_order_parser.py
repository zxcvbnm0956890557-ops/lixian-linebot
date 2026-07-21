from models import OrderExtraction
from order_parser import OrderParser


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
    parser = OrderParser(client=FakeClient(extraction))
    result = parser.parse("5斤20\n台北市中山區松江路410號17F，0979869999，李明勳\n管理員代收")
    assert result.recipient_name == "李明勳"


def test_two_names_are_never_guessed():
    extraction = OrderExtraction(
        customer_name="王小美",
        recipient_name=None,
        recipient_phone="0979869999",
        recipient_address="台北市中山區松江路410號17F",
        five_jin_boxes=1,
        confidence=0.70,
    )
    parser = OrderParser(client=FakeClient(extraction))
    result = parser.parse("王小美\n李明勳\n0979869999\n台北市中山區松江路410號17F\n5斤1")
    assert result.recipient_name is None
