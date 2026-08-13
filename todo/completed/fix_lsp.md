# Fix LSP error for _SSHStoreMixin record_success/record_failure

**Problem:** OpenCode's LSP flagged `self.record_success()` and `self.record_failure()` in `_SSHStoreMixin` as unknown attributes, because the mixin didn't inherit from `Store` — it was a standalone class used via `class SSHSubprocessStore(_SSHStoreMixin, Store)`. The methods exist on `Store`, but pyright couldn't resolve them through the mixin alone. `# type: ignore[attr-defined]` suppressed pyright CLI but not the in-editor LSP.

**Fix:** Changed `_SSHStoreMixin` to inherit from `Store`, and removed the redundant `Store` base from `SSHSubprocessStore` and `SSHSocketStore`. This makes the MRO a clean linear chain (`SSHSubprocessStore -> _SSHStoreMixin -> Store -> ABC`) instead of a diamond, and the methods are now statically resolvable.

**Result:** 0 pyright errors across the entire package. No `# type: ignore` needed.