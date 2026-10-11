"""Compare token streams and parser results against reference SLY."""

import pytest

from rply.compat import sly
from tests.fixtures.sly_calculator import build_calculator

pytestmark = pytest.mark.differential


@pytest.mark.parametrize("expression", [
    "1 + 2 * 3", "(1 + 2) * 3", "100 - 50 - 20 - 5",
    "10 * 20 + 30 * 40", "((2 + 3) * (4 + 5)) / 9",
])
def test_sly_matches_upstream(upstream_sly, expression):
    OurLexer, OurParser = build_calculator(sly)
    ReferenceLexer, ReferenceParser = build_calculator(upstream_sly)
    ours, reference = OurLexer(), ReferenceLexer()
    our_tokens = [(t.type, t.value, t.lineno, t.index) for t in ours.tokenize(expression)]
    reference_tokens = [(t.type, t.value, t.lineno, t.index) for t in reference.tokenize(expression)]
    assert our_tokens == reference_tokens
    assert OurParser().parse(ours.tokenize(expression)) == ReferenceParser().parse(reference.tokenize(expression))
