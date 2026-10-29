"""个人助理：程序检查有没有漏归类的消息。"""

from app import digest as dg


def test_check_finds_missing_and_bad_ids():
    doc = {
        "must_do": [{"ids": [1], "task": "交电费", "deadline": "2026-10-28"}, {"ids": [2], "task": "回邮件", "deadline": "下周"}],
        "later": [],
        "fyi": [{"ids": [3]}],
        "ignore": [9],
    }
    issues = dg.check(doc, 4)
    assert any("第 4 条" in i for i in issues)
    assert any("截止日期格式" in i for i in issues)
    assert any("不存在的编号" in i for i in issues)
    assert dg.check({"must_do": [{"ids": [1, 2]}], "ignore": [3]}, 3) == []
