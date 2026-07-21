import csv

from blackcat import BLACKCAT_HEADERS, build_blackcat_rows, export_blackcat_csv, split_packages
from models import CleanOrder


def sample_order(five=0, ten=0):
    return CleanOrder(
        customer_name="李明勳",
        recipient_name="李明勳",
        recipient_phone="0979869999",
        recipient_address="臺北市中山區松江路410號17F",
        five_jin_boxes=five,
        ten_jin_boxes=ten,
        sender_name="李鮮",
        sender_phone="0986184111",
        sender_address="南投縣國姓鄉中正路三段224-10號",
        note="管理員代收",
        receipt_note="",
        confidence=0.97,
    )


def test_all_seven_package_specs():
    expected = {
        (1, 0): "5斤+1",
        (2, 0): "5斤+2",
        (4, 0): "5斤+4",
        (0, 1): "10斤+1",
        (0, 2): "10斤+2",
        (1, 1): "5斤+1、10斤+1",
        (2, 1): "5斤+2、10斤+1",
    }
    for quantities, label in expected.items():
        packages = split_packages(*quantities)
        assert len(packages) == 1
        assert packages[0].label == label


def test_twenty_five_jin_boxes_become_five_shipments():
    packages = split_packages(20, 0)
    assert len(packages) == 5
    assert all(package.label == "5斤+4" for package in packages)


def test_blackcat_csv_has_bom_and_27_columns(tmp_path):
    rows = build_blackcat_rows(sample_order(five=4), "TEST-001")
    output = export_blackcat_csv(rows, tmp_path / "blackcat.csv")
    assert output.read_bytes().startswith(b"\xef\xbb\xbf")
    with output.open(encoding="utf-8-sig", newline="") as handle:
        parsed = list(csv.reader(handle))
    assert len(BLACKCAT_HEADERS) == 27
    assert all(len(row) == 27 for row in parsed)
    assert parsed[1][0] == "李明勳"
    assert parsed[1][14] == "李鮮"
