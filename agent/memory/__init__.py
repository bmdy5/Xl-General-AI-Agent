import re

_CJK_RE = re.compile(r"([一-鿿㐀-䶿])")


def cjk_space(text: str) -> str:
    """CJK 字符间插入空格，供 unicode61 tokenizer 逐字分词。"""
    if not text:
        return ""
    return _CJK_RE.sub(r" \1 ", text)
