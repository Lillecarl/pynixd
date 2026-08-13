# Reusable Wire Types

Shared types used as fields within requests and responses. These are not protocol operations themselves — they are `WireModel` or `WireString` subclasses that appear as field types in the operations above.

---

## WireString Types

Single length-prefixed UTF-8 strings on the wire.

### StorePath

```{eval-rst}
.. automodule:: nix_daemon_protocol.store_path
   :members:
```

### DerivedPath

```{eval-rst}
.. automodule:: nix_daemon_protocol.derived_path
   :members:
```

### DrvOutput

```{eval-rst}
.. automodule:: nix_daemon_protocol.drv_output
   :members:
```

### ContentAddress

```{eval-rst}
.. automodule:: nix_daemon_protocol.content_address
   :members:
```

### NARHash

```{eval-rst}
.. automodule:: nix_daemon_protocol.nar_hash
   :members:
```

### Signature

```{eval-rst}
.. automodule:: nix_daemon_protocol.signature
   :members:
```

---

## WireModel Types

### BasicDerivation

```{eval-rst}
.. automodule:: nix_daemon_protocol.basic_derivation
   :members:
```

### DerivationOutput

```{eval-rst}
.. automodule:: nix_daemon_protocol.derivation_output
   :members:
```

### UnkeyedValidPathInfo

```{eval-rst}
.. automodule:: nix_daemon_protocol.path_info
   :members:
```

### ValidPathInfo

```{eval-rst}
.. automodule:: nix_daemon_protocol.valid_path_info
   :members:
```

### BuildResult

```{eval-rst}
.. automodule:: nix_daemon_protocol.build_result
   :members:
```

### KeyedBuildResult

```{eval-rst}
.. automodule:: nix_daemon_protocol.keyed_build_result
   :members:
```

### OptMicroseconds

```{eval-rst}
.. automodule:: nix_daemon_protocol.opt_microseconds
   :members:
```

### Realisation

```{eval-rst}
.. automodule:: nix_daemon_protocol.realisation
   :members:
```

### Time

```{eval-rst}
.. automodule:: nix_daemon_protocol.wire_time
   :members:
```

---

## Stderr Stream Types

The Nix daemon stderr stream uses a tagged-union wire format: `[uint64 code][message body]...[uint64 STDERR_LAST]`.

```{eval-rst}
.. automodule:: nix_daemon_protocol.logs
   :members:
```
