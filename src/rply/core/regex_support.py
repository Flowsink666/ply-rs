"""Translate a conservative subset of CPython's parsed regex semantics.

The compiled Python pattern is authoritative. Unsupported constructs keep its
bound match method; acceptance by the Rust regex parser is never a safety test.
"""

import re
import warnings


def native_pattern(pattern, flags, compiled):
    if type(pattern) is not str or type(compiled) is not re.Pattern:
        return None, False, ""
    try:
        try:
            from re import _parser as parser
        except ImportError:  # Python 3.9 and 3.10
            import sre_parse as parser

        # Registration already emitted the original pattern's diagnostics once.
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", FutureWarning)
            warnings.simplefilter("ignore", DeprecationWarning)
            tree = parser.parse(pattern, flags)
    except (ImportError, AttributeError, TypeError, ValueError, RecursionError):
        # Private parser availability and optional translation limits must not
        # reject a pattern the authoritative Python compiler already accepted.
        return None, False, ""
    prefix = ""
    if not tree.state.flags & re.IGNORECASE:
        for operation, value in tree:
            if str(operation) != "LITERAL" or 0xD800 <= value <= 0xDFFF:
                break
            prefix += chr(value)
    try:
        return _render(tree, tree.state.flags), False, prefix
    except (_Unsupported, RecursionError):
        try:
            return _render(tree, tree.state.flags, ascii_input=True), True, prefix
        except (_Unsupported, RecursionError):
            return None, False, prefix


class _Unsupported(Exception):
    pass


def _literal(value):
    if 0xD800 <= value <= 0xDFFF:
        raise _Unsupported
    return r"\x{%x}" % value


def _category(category, flags, ascii_input):
    if not flags & re.ASCII and not ascii_input:
        raise _Unsupported
    categories = {
        "CATEGORY_DIGIT": "0-9",
        "CATEGORY_WORD": "A-Za-z0-9_",
        "CATEGORY_SPACE": r"\x20\t\n\r\f\v",
    }
    if ascii_input and not flags & re.ASCII:
        categories["CATEGORY_SPACE"] += r"\x1c-\x1f"
    name = str(category)
    if name in categories:
        return "[" + categories[name] + "]"
    positive = name.replace("CATEGORY_NOT_", "CATEGORY_")
    if positive in categories:
        return "[^" + categories[positive] + "]"
    raise _Unsupported


def _render(tree, flags, ascii_input=False):
    supported = re.ASCII | re.UNICODE | re.MULTILINE | re.DOTALL | re.VERBOSE
    if flags & ~supported:
        raise _Unsupported
    output = []
    for operation, argument in tree:
        name = str(operation)
        if name == "LITERAL":
            output.append(_literal(argument))
        elif name == "NOT_LITERAL":
            output.append("[^" + _literal(argument) + "]")
        elif name == "ANY":
            output.append("(?s:.)" if flags & re.DOTALL else ".")
        elif name == "IN":
            members = []
            negated = False
            for kind, value in argument:
                kind = str(kind)
                if kind == "NEGATE":
                    negated = True
                elif kind == "LITERAL":
                    members.append(_literal(value))
                elif kind == "RANGE":
                    members.append(_literal(value[0]) + "-" + _literal(value[1]))
                elif kind == "CATEGORY":
                    members.append(_category(value, flags, ascii_input))
                else:
                    raise _Unsupported
            output.append("[" + ("^" if negated else "") + "".join(members) + "]")
        elif name == "CATEGORY":
            output.append(_category(argument, flags, ascii_input))
        elif name == "SUBPATTERN":
            _, added, removed, child = argument
            child_flags = flags
            if added & (re.ASCII | re.UNICODE):
                child_flags &= ~(re.ASCII | re.UNICODE)
            output.append("(?:" + _render(child, (child_flags | added) & ~removed, ascii_input) + ")")
        elif name == "BRANCH":
            output.append("(?:" + "|".join(_render(branch, flags, ascii_input) for branch in argument[1]) + ")")
        elif name in {"MAX_REPEAT", "MIN_REPEAT"}:
            minimum, maximum, child = argument
            upper = "" if str(maximum) == "MAXREPEAT" else str(maximum)
            output.append("(?:" + _render(child, flags, ascii_input) + "){" + str(minimum) + "," + upper + "}"
                          + ("?" if name == "MIN_REPEAT" else ""))
        elif name == "AT":
            anchor = str(argument)
            if anchor == "AT_BEGINNING_STRING":
                output.append(r"\A")
            elif anchor == "AT_BEGINNING":
                output.append(r"(?m:^)" if flags & re.MULTILINE else r"\A")
            elif anchor == "AT_END_STRING":
                output.append(r"\z")
            elif anchor == "AT_END" and flags & re.MULTILINE:
                output.append(r"(?m:$)")
            elif anchor == "AT_BOUNDARY" and (flags & re.ASCII or ascii_input):
                output.append(r"(?-u:\b)")
            else:
                raise _Unsupported
        else:
            raise _Unsupported
    return "".join(output)
