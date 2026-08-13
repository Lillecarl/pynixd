# Core Infrastructure

Base classes, serde contexts, protocol enums, and type aliases that underpin the entire serde system.

---

## Base Classes

### WireModel

The abstract Pydantic base class that auto-generates `to_writer()` and `from_reader()` from type annotations.

```{eval-rst}
.. automodule:: nix_daemon_protocol.wire_message
   :members: WireField, WireModel
```

### WireString

Abstract base for types that are a single length-prefixed UTF-8 string on the wire.

```{eval-rst}
.. automodule:: nix_daemon_protocol.wire_string
   :members:
```

### WireRequest / WireResponse

Operation base classes. `WireRequest` auto-registers each op in `WIRE_REGISTRY`.

```{eval-rst}
.. automodule:: nix_daemon_protocol.wire_ops
   :members:
```

---

## Serde Contexts

Context dataclasses passed through the serialization/deserialization pipeline.

```{eval-rst}
.. automodule:: nix_daemon_protocol.context
   :members:
```

---

## Protocol Enums

```{eval-rst}
.. automodule:: nix_daemon_protocol.protocol
   :members:
```

---

## Auth

```{eval-rst}
.. automodule:: nix_daemon_protocol.auth
   :members:
```

---

## IDs

```{eval-rst}
.. automodule:: nix_daemon_protocol.ids
   :members:
```

---

## Type Aliases

```{eval-rst}
.. automodule:: nix_daemon_protocol.aliases
   :members:
```
