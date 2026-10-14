"""Value storage shared by compatibility tokens with deferred error suffixes."""


class _DeferredTokenValue:
    """Retain the core error token without reading its original input suffix."""

    __slots__ = ("token",)

    def __init__(self, token):
        self.token = token


class _TokenValueStorage:
    """Share private storage without occupying a user's subtype slot name."""

    __slots__ = ("__value",)


def _get_value(token):
    try:
        value = token._TokenValueStorage__value
    except AttributeError:
        # Match a deleted public slot instead of exposing the storage name.
        raise AttributeError(
            f"{type(token).__name__!r} object has no attribute 'value'"
        ) from None
    # This marker is private and created with its exact type. isinstance can
    # consult an arbitrary user's __class__ property when reading plain values.
    if type(value) is _DeferredTokenValue:
        materialized = value.token.value
        # Reading a str subclass can run user code. Preserve assignments and
        # deletions performed by that code rather than overwriting them.
        if getattr(token, "_TokenValueStorage__value", None) is value:
            token._TokenValueStorage__value = materialized
            return materialized
        return _get_value(token)
    return value


def _set_value(token, value):
    token._TokenValueStorage__value = value


def _delete_value(token):
    try:
        del token._TokenValueStorage__value
    except AttributeError:
        raise AttributeError(
            f"{type(token).__name__!r} object has no attribute 'value'"
        ) from None


_value_property = property(_get_value, _set_value, _delete_value)


def _public_token_state(token, fields):
    """Serialize public slots, materializing a suffix only for this consumer."""
    fields = list(fields)
    # An inherited __getstate__ must retain subclass state just as the default
    # slot protocol does, including private slots with their mangled names.
    for cls in type(token).__mro__:
        slots = cls.__dict__.get("__slots__", ())
        if isinstance(slots, str):
            slots = (slots,)
        for name in slots:
            if name in {"__dict__", "__weakref__"}:
                continue
            if name.startswith("__") and not name.endswith("__"):
                class_name = cls.__name__.lstrip("_")
                if class_name:
                    name = f"_{class_name}{name}"
            if name == "_TokenValueStorage__value":
                continue
            if name not in fields:
                fields.append(name)
    # A subclass may provide its own public value descriptor, with storage
    # unrelated to the default property's private slot.
    value_descriptor = next(
        cls.__dict__["value"] for cls in type(token).__mro__
        if "value" in cls.__dict__
    )
    state = {}
    for name in fields:
        if name == "value" and value_descriptor is _value_property:
            try:
                object.__getattribute__(token, "_TokenValueStorage__value")
            except AttributeError:
                continue
            # An existing lazy value's AttributeError comes from materializing
            # its suffix and must propagate, rather than omit the public slot.
            state[name] = getattr(token, name)
        else:
            try:
                state[name] = getattr(token, name)
            except AttributeError:
                # Match the default slot protocol for deleted fields and
                # overridden public descriptors, reading each field once.
                continue
    # Use the original public slot names: old token pickles also restore value
    # through the property, without serializing the hidden core token/owner.
    try:
        dictionary = object.__getattribute__(token, "__dict__")
    except AttributeError:
        dictionary = None
    return dictionary or None, state
