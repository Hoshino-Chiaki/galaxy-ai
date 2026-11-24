import json

import pytest

from xingxu.feishu.bot_messages import POST_BYTES, markdown_posts


def texts(posts):
    result = []
    for post in posts:
        assert post['msg_type'] == 'post'
        assert len(json.dumps(post, ensure_ascii=False).encode('utf-8')) <= POST_BYTES
        rows = json.loads(post['content'])['zh_cn']['content']
        assert len(rows) == 1 and len(rows[0]) == 1 and rows[0][0]['tag'] == 'md'
        result.append(rows[0][0]['text'])
    return result


def test_report_markdown_is_sent_intact_without_text_conversion():
    source = ('## 今日经营日报\n\n**收入** ¥128.50\n\n'
              '| 店铺 | 订单 |\n| --- | --- |\n| 旗舰店 | 3 |\n\n'
              '- [x] 已查询\n- [ ] 待核对\n\n> 截至查询时刻\n\n'
              '```python\nprint("你好 🌈")\n```\n\n'
              '[下载附件](https://example.com/report.xlsx)')
    assert texts(list(markdown_posts(source))) == [source]


def test_long_report_splits_between_complete_paragraphs_and_keeps_tables_together():
    paragraph = '经营数据 🌈 **收入** ¥128.50。' * 200 + '\n\n'
    table = '| 店铺 | 订单 |\n| --- | --- |\n| 旗舰店 | 3 |\n\n'
    source = paragraph + table + paragraph * 4
    chunks = texts(list(markdown_posts(source)))
    assert len(chunks) > 1 and ''.join(chunks) == source
    assert sum(table in chunk for chunk in chunks) == 1
    assert all(chunk.endswith('\n\n') for chunk in chunks)


@pytest.mark.parametrize('character', ['订单🌈', '\\"\x01'])
def test_giant_single_line_is_complete_and_uses_encoded_payload_size(character):
    source = character * 15000
    chunks = texts(list(markdown_posts(source)))
    assert len(chunks) > 1 and ''.join(chunks) == source


@pytest.mark.parametrize('marker', ['```', '~~~~'])
def test_long_code_blocks_are_closed_and_reopened_with_language(marker):
    line = 'print("订单 🌈")\n'
    source = marker + 'python\n' + line * 5000 + marker + '\n'
    chunks = texts(list(markdown_posts(source)))
    assert len(chunks) > 1
    assert all(chunk.startswith(marker + 'python\n') for chunk in chunks)
    assert all(chunk.rstrip().endswith(marker) for chunk in chunks)
    assert sum(chunk.count(line) for chunk in chunks) == 5000


def test_extreme_fence_opener_does_not_stall_chunking():
    source = '```' + 'python' * 15000 + '\nbody\n```'
    assert ''.join(texts(list(markdown_posts(source)))) == source


def test_empty_reply_still_does_not_send_an_empty_message():
    assert list(markdown_posts('')) == []
