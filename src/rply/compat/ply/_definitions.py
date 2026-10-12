"""Collect PLY definitions from modules, dictionaries, or class instances."""


def definition_namespace(source):
    if isinstance(source, dict):
        return dict(source)
    configuration = {"tokens", "literals", "states", "precedence", "start"}
    return {
        name: getattr(source, name)
        for name in dir(source)
        if name in configuration or name.startswith(("t_", "p_"))
    }
