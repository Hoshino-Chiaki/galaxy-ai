"""Render existing AI Markdown as Feishu post messages within the wire-size limit."""
import json
import re


# Feishu allows 30 KB for post messages. Leave room for the reply envelope and
# server-side formatting, counting JSON escaping as well as UTF-8 bytes.
POST_BYTES = 24_000
FENCE = re.compile(r'^ {0,3}(`{3,}|~{3,})(.*)$')


def markdown_post(text):
    return {'msg_type': 'post', 'content': json.dumps(
        {'zh_cn': {'content': [[{'tag': 'md', 'text': text}]]}}, ensure_ascii=False)}


def _fits(text):
    return len(json.dumps(markdown_post(text), ensure_ascii=False).encode('utf-8')) <= POST_BYTES


def _fence_after(line, fence):
    match = FENCE.match(line.rstrip('\r\n'))
    if not match:
        return fence
    marker, suffix = match.groups()
    if fence is None:
        if marker[0] == '`' and '`' in suffix:
            return None
        # An abnormally large opener cannot safely be repeated in each post.
        if not _fits(line + '\n' + marker + ' ' * 128):
            return None
        return marker, line.rstrip('\r\n')
    if marker[0] == fence[0][0] and len(marker) >= len(fence[0]) and not suffix.strip():
        return None
    return fence


def markdown_posts(text):
    """Keep ordinary Markdown intact; split large replies at block boundaries.

    A code block larger than a message is closed and reopened in the next post.
    Original text is retained, including long lines, Chinese, emoji and escapes.
    """
    if not text:
        return
    blocks, block, fence = [], '', None
    for line in text.splitlines(keepends=True):
        block += line
        fence = _fence_after(line, fence)
        if not line.strip() and fence is None:
            blocks.append(block)
            block = ''
    if block:
        blocks.append(block)

    pending = ''
    for block in blocks:
        if _fits(pending + block):
            pending += block
            continue
        if pending:
            yield markdown_post(pending)
            pending = ''
        if _fits(block):
            pending = block
            continue

        # Oversized blocks are split by line, with a bounded fallback for one
        # oversized line. Code-fence state follows the source, not added wrappers.
        part, fence = '', None
        for line in block.splitlines(keepends=True):
            next_fence = _fence_after(line, fence)
            closing = '\n' + next_fence[0] if next_fence else ''
            if not _fits(part + line + closing) and part:
                yield markdown_post(part + ('\n' + fence[0] if fence else ''))
                part = fence[1] + '\n' if fence else ''
            while not _fits(part + line + closing):
                low, high = 0, len(line)
                while low < high:
                    middle = (low + high + 1) // 2
                    if _fits(part + line[:middle] + closing):
                        low = middle
                    else:
                        high = middle - 1
                yield markdown_post(part + line[:low] + closing)
                line = line[low:]
                part = next_fence[1] + '\n' if next_fence else ''
            part += line
            fence = next_fence
        pending = part
    if pending:
        yield markdown_post(pending)
