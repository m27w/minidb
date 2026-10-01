import pytest

from minidb.tokenizer import SQLSyntaxError, TokenType, tokenize


def kinds(sql):
    return [(t.type, t.value) for t in tokenize(sql)[:-1]]


def test_keywords_are_case_insensitive_and_identifiers_lowercased():
    assert kinds("select Name FROM Users") == [
        (TokenType.KEYWORD, "SELECT"),
        (TokenType.IDENT, "name"),
        (TokenType.KEYWORD, "FROM"),
        (TokenType.IDENT, "users"),
    ]


def test_numbers_strings_and_symbols():
    assert kinds("x >= 3.5 AND y <> 'it''s'") == [
        (TokenType.IDENT, "x"),
        (TokenType.SYMBOL, ">="),
        (TokenType.NUMBER, 3.5),
        (TokenType.KEYWORD, "AND"),
        (TokenType.IDENT, "y"),
        (TokenType.SYMBOL, "!="),
        (TokenType.STRING, "it's"),
    ]


def test_comments_are_skipped():
    assert kinds("SELECT -- everything\n *") == [
        (TokenType.KEYWORD, "SELECT"),
        (TokenType.SYMBOL, "*"),
    ]


def test_unterminated_string_reports_position():
    with pytest.raises(SQLSyntaxError) as info:
        tokenize("SELECT 'oops")
    assert info.value.position == 7


def test_unexpected_character():
    with pytest.raises(SQLSyntaxError):
        tokenize("SELECT @")
