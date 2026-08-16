"""A canonical view of a Nix store, and the difference between two of them.

The differential suite realises one derived path twice, in two separated
chroot stores, and then compares the stores. This module is what "compares"
means.

The comparison is over the store and not over the response. A `BuildResult` is
a summary, and the summary hides the failure that matters. A defect in a goal
system shows itself as a store that is wrong: a path that is valid and must
not be, a reference that is absent, an output addressed against the wrong
hash. A snapshot holds what the store says about every path in it, so the
comparison can see all three.

nanopynix reads both stores. It is the oracle for one arm only, but it is the
instrument for both, and that is safe. A defect in the reader moves both
snapshots the same way, so it cancels in the difference.
"""

from __future__ import annotations

import dataclasses
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from collections.abc import Collection, Iterable, Mapping

    from nanopynix.protocols import AsyncStore

# The two fields of `query_path_info` that a snapshot drops, and the reason for
# each. Everything else states what the path *is*, and two engines that agree
# on all of it built the same thing.
#
# `registration_time` is the wall clock at the moment the path entered the
# store. Two runs can never agree on it.
#
# `sigs` names the keys that signed the path. Each arm signs with its own
# store, so the sets differ for a reason that says nothing about either engine.
DROPPED_FIELDS = ("registration_time", "sigs")


@dataclasses.dataclass(frozen=True, slots=True)
class PathFacts:
    """What one store says about one path, with the volatile fields removed."""

    nar_hash: str
    nar_size: int
    references: tuple[str, ...]
    deriver: str | None
    ca: str | None
    ultimate: bool

    @classmethod
    def from_path_info(cls, info: Mapping[str, Any]) -> PathFacts:
        """Read the facts out of one `query_path_info` mapping."""
        return cls(
            nar_hash=str(info["nar_hash"]),
            nar_size=int(info["nar_size"]),
            # Sorted, because the order Nix reports references in is the order
            # of a SQLite query and is not a fact about the path.
            references=tuple(sorted(str(ref) for ref in info.get("references") or ())),
            deriver=_optional_str(info.get("deriver")),
            ca=_optional_str(info.get("ca")),
            # `ultimate` is true for a path this store built itself, and false
            # for one it substituted. It is kept, and not dropped as noise,
            # because "one engine built it and the other fetched it" is a real
            # difference between the two. The corpus turns substituters off so
            # that the field means one thing.
            ultimate=bool(info.get("ultimate", False)),
        )


@dataclasses.dataclass(frozen=True, slots=True)
class StoreSnapshot:
    """Every valid path of one store, and the facts of each."""

    paths: Mapping[str, PathFacts]

    def __len__(self) -> int:
        return len(self.paths)


@dataclasses.dataclass(frozen=True, slots=True)
class FieldDisagreement:
    """One field of one path, on which the two stores do not agree."""

    path: str
    field: str
    left: object
    right: object


@dataclasses.dataclass(frozen=True, slots=True)
class StoreDifference:
    """What separates two snapshots.

    Falsy when the two agree, so a test reads `assert not difference`.
    """

    only_in_left: tuple[str, ...]
    only_in_right: tuple[str, ...]
    disagreements: tuple[FieldDisagreement, ...]

    def __bool__(self) -> bool:
        return bool(self.only_in_left or self.only_in_right or self.disagreements)

    def describe(self, left_name: str = "left", right_name: str = "right") -> str:
        """A report a person can read, naming each store rather than a side."""
        if not self:
            return f"{left_name} and {right_name} agree"
        lines: list[str] = []
        if self.only_in_left:
            lines.append(f"{len(self.only_in_left)} path(s) only in {left_name}:")
            lines.extend(f"  {path}" for path in self.only_in_left)
        if self.only_in_right:
            lines.append(f"{len(self.only_in_right)} path(s) only in {right_name}:")
            lines.extend(f"  {path}" for path in self.only_in_right)
        if self.disagreements:
            lines.append(f"{len(self.disagreements)} field(s) disagree:")
            lines.extend(
                f"  {item.path}\n    {item.field}: {left_name}={item.left!r} {right_name}={item.right!r}"
                for item in self.disagreements
            )
        return "\n".join(lines)


async def take_snapshot(store: AsyncStore, *, only: Iterable[str] | None = None) -> StoreSnapshot:
    """Read every valid path of *store*, or only the named ones.

    `only` exists for the delta below, which asks a store about a set it
    already knows. Passing it saves one `query_all_valid_paths` over a store
    that may hold a large seed closure.
    """
    wanted = list(only) if only is not None else [str(path) for path in await store.query_all_valid_paths()]
    facts: dict[str, PathFacts] = {}
    for path in wanted:
        info = await store.query_path_info(path)
        facts[str(path)] = PathFacts.from_path_info(_as_mapping(info))
    return StoreSnapshot(paths=facts)


def delta(before: StoreSnapshot, after: StoreSnapshot) -> StoreSnapshot:
    """What *after* holds that *before* did not.

    The two arms do not start from identical stores, and they never will: one
    is driven by a real `nix-daemon` and the other by an in-process
    `LocalStore`, and each seeds its own store in its own way. Comparing the
    delta rather than the whole store removes that difference from the
    question, and leaves only what the build itself added.
    """
    return StoreSnapshot(paths={path: facts for path, facts in after.paths.items() if path not in before.paths})


def compare(
    left: StoreSnapshot,
    right: StoreSnapshot,
    *,
    ignore_fields: Collection[str] = (),
) -> StoreDifference:
    """The difference between two snapshots.

    `ignore_fields` drops named fields from the field comparison, and from
    that alone: a path present on one side and not the other is still a
    difference. Pass a field only when the two sides cannot agree on it by
    construction, and say why at the call site.
    """
    left_paths = set(left.paths)
    right_paths = set(right.paths)
    disagreements: list[FieldDisagreement] = []
    for path in sorted(left_paths & right_paths):
        left_facts = left.paths[path]
        right_facts = right.paths[path]
        if left_facts == right_facts:
            continue
        disagreements.extend(
            FieldDisagreement(
                path=path,
                field=field.name,
                left=getattr(left_facts, field.name),
                right=getattr(right_facts, field.name),
            )
            for field in dataclasses.fields(left_facts)
            if field.name not in ignore_fields and getattr(left_facts, field.name) != getattr(right_facts, field.name)
        )
    return StoreDifference(
        only_in_left=tuple(sorted(left_paths - right_paths)),
        only_in_right=tuple(sorted(right_paths - left_paths)),
        disagreements=tuple(disagreements),
    )


def _optional_str(value: object) -> str | None:
    if value is None or value == "":
        return None
    return str(value)


def _as_mapping(info: object) -> Mapping[str, Any]:
    """Accept either a mapping or an object with the same attribute names.

    `AsyncStore.query_path_info` is typed as returning a `PathInfo`, and the
    raw binding returns a dict. Both reach this module, so it reads whichever
    it is given rather than forcing one at the call site.
    """
    if isinstance(info, dict):
        return info
    return {
        name: getattr(info, name, None) for name in ("nar_hash", "nar_size", "references", "deriver", "ca", "ultimate")
    }
