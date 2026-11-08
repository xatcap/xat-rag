from xat_rag_sqlite.lexical import index_text, lexical_tokens, match_expression


def test_compound_names_give_whole_parts_and_camel_parts() -> None:
    tokens = lexical_tokens("com.shop.OrderNotFoundException")
    assert tokens[0] == "com_shop_ordernotfoundexception"
    assert {"com", "shop", "ordernotfoundexception", "order", "not", "found", "exception"} <= set(tokens)


def test_jira_keys_paths_and_snake_case() -> None:
    assert "proj_123" in lexical_tokens("PROJ-123")
    assert {"src_orderservice_java", "orderservice", "order", "service", "java"} <= set(
        lexical_tokens("src/OrderService.java")
    )
    assert {"retry_count", "retry", "count"} <= set(lexical_tokens("retry_count"))
    assert lexical_tokens("4f2a9c1") == ["4f2a9c1"]


def test_match_expression_is_quoted_and_safe() -> None:
    expression = match_expression(["NEAR(a b)", 'x" OR "y', "a-b:c"])
    assert expression is not None
    for token in expression.split(" OR "):
        assert token.startswith('"')
        assert token.endswith('"')
    assert match_expression(["!!!", ""]) is None


def test_index_text_joins_tokens() -> None:
    assert index_text("Foo-Bar") == "foo_bar foo bar"


def test_match_expression_applies_the_token_cap() -> None:
    expression = match_expression([f"term{i}" for i in range(200)], max_tokens=64)
    assert expression is not None
    assert expression.count(" OR ") == 63
