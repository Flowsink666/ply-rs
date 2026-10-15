"""Optional implementation regression for replacement lazy-token factories."""

import pytest
from ply_rs import LexerEngine


@pytest.mark.parametrize("replace_input", [False, True])
@pytest.mark.parametrize("factory_error", [False, True])
@pytest.mark.parametrize("text", ["?abc", "？abc"])
def test_error_token_factory_changes_preserve_progress(
    monkeypatch, replace_input, factory_error, text,
):
    import ply_rs.core.tokens as token_module

    lexer = LexerEngine()
    lexer.add_rule("INITIAL", "WORD", r"[a-z]+")
    saved = []
    lexer.set_error_func("INITIAL", saved.append)
    factory = token_module._LazyErrorToken

    def reentrant_factory(*args, **kwargs):
        if replace_input:
            lexer.input("abc")
        else:
            lexer.skip()
        if factory_error:
            raise RuntimeError("error-token factory failed")
        return factory(*args, **kwargs)

    monkeypatch.setattr(token_module, "_LazyErrorToken", reentrant_factory)
    lexer.input(text)
    if factory_error:
        with pytest.raises(RuntimeError, match="error-token factory failed"):
            lexer.token()
    assert [tok.value for tok in lexer.tokenize()] == ["abc"]
    if factory_error:
        assert saved == []
    else:
        assert len(saved) == 1
        assert saved[0].value == text
        assert (saved[0].lexpos, saved[0].end) == (0, len(text))
