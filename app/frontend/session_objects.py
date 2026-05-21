"""Registry of user-visible workspace objects for the BioSeq chat.

Holds two kinds of objects in ``st.session_state.objects``:

- ``Sequence`` — a biological sequence the user pasted, typed, or
  uploaded. Each Sequence carries its own list of UniProt ``matches``
  (top-5 candidates) plus a locally-selected ``selected_match_index``.
- ``Protein`` — a UniProt card. Can appear either as one of the candidates
  for a Sequence, or directly when the user types a UniProt accession/ID.

Object ids are deterministic so the same sequence/accession does not
produce duplicates within a session. Labels (``Seq_A``, ``Seq_B``, ...)
are assigned by order of appearance.

The whole registry lives in ``st.session_state`` for the session-only
prototype; see ``serialize_for_persistence`` / ``apply_persisted`` for
the JSON shape we save into ``working_memory`` in Supabase.
"""

from __future__ import annotations

import hashlib
import re
from typing import Any, Iterable

import streamlit as st


_MENTION_TOKEN_RE = re.compile(r"@([A-Za-z0-9_]+)")


_OBJECTS_KEY = "objects"
_ORDER_KEY = "object_order"
_SELECTED_KEY = "selected_object_id"
_LABEL_COUNTER_KEY = "_seq_label_counter"
_PROTEIN_COUNTER_KEY = "_protein_label_counter"


def init_state() -> None:
    """Bootstrap registry keys in ``st.session_state``."""
    if _OBJECTS_KEY not in st.session_state:
        st.session_state[_OBJECTS_KEY] = {}
    if _ORDER_KEY not in st.session_state:
        st.session_state[_ORDER_KEY] = []
    if _SELECTED_KEY not in st.session_state:
        st.session_state[_SELECTED_KEY] = None
    if _LABEL_COUNTER_KEY not in st.session_state:
        st.session_state[_LABEL_COUNTER_KEY] = 0


# ---------------------------------------------------------------------------
# Id and label generation
# ---------------------------------------------------------------------------


def sequence_hash(normalized_sequence: str) -> str:
    """Return a short stable hash of a normalized sequence.

    Used to keep ``seq_*`` ids stable so the same sequence pasted twice
    re-uses the existing object instead of creating a duplicate.
    """
    digest = hashlib.sha1(normalized_sequence.encode("utf-8")).hexdigest()
    return digest[:12]


def make_sequence_id(normalized_sequence: str) -> str:
    return f"seq_{sequence_hash(normalized_sequence)}"


def make_protein_id(accession: str) -> str:
    return f"protein_{accession.upper()}"


def _next_letter_label() -> str:
    """Return the next ``Seq_A`` / ``Seq_B`` / ... / ``Seq_AA`` label."""
    init_state()
    index = int(st.session_state.get(_LABEL_COUNTER_KEY, 0))
    st.session_state[_LABEL_COUNTER_KEY] = index + 1
    return f"Seq_{_letters_for_index(index)}"


def _letters_for_index(index: int) -> str:
    """0 -> A, 25 -> Z, 26 -> AA, 27 -> AB, ..."""
    letters: list[str] = []
    n = index
    while True:
        letters.append(chr(ord("A") + (n % 26)))
        n = n // 26 - 1
        if n < 0:
            break
    return "".join(reversed(letters))


# ---------------------------------------------------------------------------
# Basic registry ops
# ---------------------------------------------------------------------------


def get_objects() -> dict[str, Any]:
    init_state()
    return st.session_state[_OBJECTS_KEY]


def get_order() -> list[str]:
    init_state()
    return st.session_state[_ORDER_KEY]


def get_selected_id() -> str | None:
    init_state()
    return st.session_state[_SELECTED_KEY]


def set_selected(object_id: str | None) -> None:
    init_state()
    if object_id is not None and object_id not in st.session_state[_OBJECTS_KEY]:
        return
    st.session_state[_SELECTED_KEY] = object_id
    # A re-select (or any selection change) clears Top-5 preview state on
    # every Sequence so at most one ghost spawn chip can ever exist, and
    # so the previously-previewed alternative doesn't linger when the
    # user moves focus elsewhere.
    for obj in st.session_state[_OBJECTS_KEY].values():
        if isinstance(obj, dict) and obj.get("kind") == "sequence":
            anchor = _anchored_index(obj)
            matches = obj.get("matches") or []
            if 0 <= anchor < len(matches):
                obj["selected_match_index"] = anchor
    if object_id and object_id in st.session_state[_OBJECTS_KEY]:
        obj = st.session_state[_OBJECTS_KEY][object_id]
        if obj.get("kind") == "protein":
            # When the user opens a Protein through a Sequence candidate
            # the caller should pass through ``set_last_origin`` so the
            # alignment in Protein Inspector uses the right sequence.
            pass


def get_object(object_id: str) -> dict[str, Any] | None:
    init_state()
    return st.session_state[_OBJECTS_KEY].get(object_id)


def protein_tooltip(obj: dict[str, Any] | None) -> str:
    """Return the human-readable protein name used as a hover tooltip.

    For a Sequence the source is ``matches[selected].protein.name`` (e.g.
    "Hemoglobin subunit epsilon"). For a Protein it's the card's
    ``protein.name`` or ``display_name`` when those differ from the bare
    accession. Returns "" when no nice name is available.
    """
    if not isinstance(obj, dict):
        return ""
    kind = obj.get("kind")
    if kind == "sequence":
        matches = obj.get("matches") or []
        idx = _anchored_index(obj)
        if 0 <= idx < len(matches):
            protein = (matches[idx] or {}).get("protein") or {}
            return str(protein.get("name") or "").strip()
        return ""
    if kind == "protein":
        card = obj.get("card") or {}
        card_protein = card.get("protein") if isinstance(card, dict) else None
        name = ""
        if isinstance(card_protein, dict):
            name = str(card_protein.get("name") or "").strip()
        if not name:
            name = str(obj.get("display_name") or "").strip()
        accession = str(obj.get("accession") or "").strip().upper()
        if name and name.upper() != accession:
            return name
    return ""


def _anchored_index(obj: dict[str, Any]) -> int:
    """Index of the match that defines this card's identity.

    Defaults to ``anchored_match_index`` (set on fork from the Top-5
    switcher). Falls back to ``selected_match_index`` for legacy objects
    that pre-date the anchor split, and finally to 0.
    """
    for key in ("anchored_match_index", "selected_match_index"):
        try:
            value = obj.get(key)
        except AttributeError:
            value = None
        if value is not None:
            try:
                return int(value)
            except (TypeError, ValueError):
                continue
    return 0


def display_label(obj: dict[str, Any] | None) -> str:
    """Return the user-visible label for an object.

    For a Sequence that has been matched, this is the UniProt entry name
    of the **anchored** match (the one the card was created for — top-1
    by default, or the match the user chose when spawning a fork from
    Top 5). Browsing alternative matches inside the card does NOT rename
    the chip — the anchor is the card's identity. Falls back to the
    original ``Seq_A`` label if the entry name is missing.
    """
    if not isinstance(obj, dict):
        return ""
    fallback = str(obj.get("label") or obj.get("id") or "")
    if obj.get("kind") != "sequence":
        return fallback
    matches = obj.get("matches") or []
    idx = _anchored_index(obj)
    if 0 <= idx < len(matches):
        protein = (matches[idx] or {}).get("protein") or {}
        entry = str(protein.get("entry_name") or "").strip()
        if entry:
            return entry
    return fallback


def _matchable_tokens(obj: dict[str, Any]) -> set[str]:
    """Tokens by which an object can be addressed via @mention.

    Used by ``rewrite_mentions`` to find the right object for a given
    ``@<token>`` so the title can render the **current** label even if the
    user originally typed an older name.
    """
    tokens: set[str] = set()
    if not isinstance(obj, dict):
        return tokens
    for key in ("label", "accession"):
        value = str(obj.get(key) or "").strip()
        if value:
            tokens.add(value)
    if obj.get("kind") == "sequence":
        matches = obj.get("matches") or []
        idx = _anchored_index(obj)
        if 0 <= idx < len(matches):
            protein = (matches[idx] or {}).get("protein") or {}
            for key in ("entry_name", "gene", "accession"):
                value = str(protein.get(key) or "").strip()
                if value:
                    tokens.add(value)
    elif obj.get("kind") == "protein":
        card = obj.get("card") or {}
        if isinstance(card, dict):
            for key in ("entry_name", "gene"):
                value = str(card.get(key) or "").strip()
                if value:
                    tokens.add(value)
    return tokens


def rewrite_mentions(text: str, objects: dict[str, Any] | None) -> str:
    """Replace every ``@<token>`` in ``text`` with the resolved label.

    Pure function — operates on a plain ``objects`` dict, not session
    state. Used both for live chat rendering (against ``st.session_state``)
    and for sidebar titles of past sessions (against the persisted
    ``bioseq_workspace.objects`` snapshot of each row).

    Tokens that don't match any object are kept verbatim, so prose
    survives unchanged.
    """
    if not text or not isinstance(objects, dict) or not objects:
        return text or ""

    def replace(match: re.Match[str]) -> str:
        token = match.group(1)
        for obj in objects.values():
            if not isinstance(obj, dict):
                continue
            if token in _matchable_tokens(obj):
                resolved = display_label(obj) or token
                return f"@{resolved}"
        return match.group(0)

    return _MENTION_TOKEN_RE.sub(replace, text)


def list_objects(kind: str | None = None) -> list[dict[str, Any]]:
    init_state()
    objects = st.session_state[_OBJECTS_KEY]
    ordered: list[dict[str, Any]] = []
    for object_id in st.session_state[_ORDER_KEY]:
        obj = objects.get(object_id)
        if not obj:
            continue
        if kind is None or obj.get("kind") == kind:
            ordered.append(obj)
    return ordered


def remove_object(object_id: str) -> None:
    init_state()
    st.session_state[_OBJECTS_KEY].pop(object_id, None)
    if object_id in st.session_state[_ORDER_KEY]:
        st.session_state[_ORDER_KEY].remove(object_id)
    if st.session_state[_SELECTED_KEY] == object_id:
        st.session_state[_SELECTED_KEY] = (
            st.session_state[_ORDER_KEY][-1]
            if st.session_state[_ORDER_KEY]
            else None
        )


def clear_all() -> None:
    init_state()
    st.session_state[_OBJECTS_KEY] = {}
    st.session_state[_ORDER_KEY] = []
    st.session_state[_SELECTED_KEY] = None
    st.session_state[_LABEL_COUNTER_KEY] = 0


# ---------------------------------------------------------------------------
# Sequence helpers
# ---------------------------------------------------------------------------


def upsert_sequence(
    *,
    normalized_sequence: str,
    raw_sequence: str = "",
    sequence_type: str = "UNKNOWN",
    fasta_header: str | None = None,
    source: dict[str, Any] | None = None,
    status: str = "draft",
    label: str | None = None,
    protein_sequence: str | None = None,
    warnings: list[str] | None = None,
    classification_reason: str | None = None,
    confidence: float | None = None,
) -> dict[str, Any]:
    """Create or update a Sequence object by stable hash. Returns the object."""
    init_state()
    object_id = make_sequence_id(normalized_sequence)
    objects = st.session_state[_OBJECTS_KEY]
    order = st.session_state[_ORDER_KEY]

    existing = objects.get(object_id)
    if existing is None:
        new_label = label or _next_letter_label()
        seq_obj: dict[str, Any] = {
            "id": object_id,
            "kind": "sequence",
            "label": new_label,
            "display_name": new_label,
            "source": source or {"type": "pasted_text", "file_name": None, "message_id": None},
            "fasta_header": fasta_header,
            "sequence_type": sequence_type,
            "raw_sequence": raw_sequence or normalized_sequence,
            "normalized_sequence": normalized_sequence,
            "protein_sequence": protein_sequence
            if protein_sequence is not None
            else (normalized_sequence if sequence_type == "PROTEIN" else None),
            "length": len(normalized_sequence),
            "status": status,
            "matches": [],
            "selected_match_index": 0,
            # ``anchored_match_index`` defines the card's identity (chip
            # label, @mention resolution). ``selected_match_index`` is
            # mutable previewing inside the protein-card switcher.
            "anchored_match_index": 0,
            "warnings": warnings or [],
            "classification_reason": classification_reason or "",
            "confidence": confidence,
        }
        objects[object_id] = seq_obj
        order.append(object_id)
        return seq_obj

    # Update existing object — preserve label/order, refresh metadata.
    if source and source not in (existing.get("sources") or []):
        existing.setdefault("sources", []).append(source)
    if fasta_header and not existing.get("fasta_header"):
        existing["fasta_header"] = fasta_header
    if sequence_type and sequence_type != "UNKNOWN":
        existing["sequence_type"] = sequence_type
    if status and status != "draft":
        existing["status"] = status
    if protein_sequence and not existing.get("protein_sequence"):
        existing["protein_sequence"] = protein_sequence
    if warnings:
        merged = list(existing.get("warnings") or [])
        for warn in warnings:
            if warn not in merged:
                merged.append(warn)
        existing["warnings"] = merged
    if confidence is not None:
        existing["confidence"] = confidence
    return existing


def set_sequence_status(object_id: str, status: str) -> None:
    obj = get_object(object_id)
    if obj and obj.get("kind") == "sequence":
        obj["status"] = status


def set_sequence_matches(object_id: str, matches: list[dict[str, Any]]) -> None:
    obj = get_object(object_id)
    if not obj or obj.get("kind") != "sequence":
        return
    obj["matches"] = list(matches)
    # Reset to top-1 if previous selection is out of range.
    if obj.get("selected_match_index", 0) >= len(matches):
        obj["selected_match_index"] = 0


def set_sequence_selected_match(object_id: str, index: int) -> None:
    obj = get_object(object_id)
    if not obj or obj.get("kind") != "sequence":
        return
    matches = obj.get("matches") or []
    if not matches:
        obj["selected_match_index"] = 0
        return
    obj["selected_match_index"] = max(0, min(int(index), len(matches) - 1))


# --- Spawn-suggestion plumbing ------------------------------------------
#
# When a Sequence card's ``selected_match_index`` diverges from its
# ``anchored_match_index`` the user is previewing an alternative match
# and we offer them a ghost chip to spawn it as a new independent card.
# The ghost chip's click is routed through the same mention bridge that
# handles regular ``@<id>`` clicks — we just use a synthetic object id
# (``SPAWN_PREFIX + "<parent_id>__idx<N>"``) and dispatch on the prefix
# inside the bridge callback.


SPAWN_PREFIX = "__spawn__"


def make_spawn_token(parent_id: str, match_index: int) -> str:
    """Synthetic object-id used by the mention bridge to dispatch a spawn."""
    return f"{SPAWN_PREFIX}{parent_id}__idx{int(match_index)}"


def parse_spawn_token(token: str) -> tuple[str, int] | None:
    """Inverse of :func:`make_spawn_token`. Returns ``None`` if not a spawn id."""
    if not token or not token.startswith(SPAWN_PREFIX):
        return None
    rest = token[len(SPAWN_PREFIX):]
    sep = rest.rfind("__idx")
    if sep <= 0:
        return None
    parent_id = rest[:sep]
    try:
        idx = int(rest[sep + len("__idx"):])
    except (TypeError, ValueError):
        return None
    return parent_id, idx


def compute_spawn_suggestions() -> list[tuple[dict[str, Any], int, str, str]]:
    """List ghost-chip targets for the current registry.

    Returns ``[(parent_obj, match_index, entry_name, accession), ...]``
    for every Sequence whose preview differs from its anchor. Caller
    decides how to render — both the object bar (ghost chip) and the
    mention bridge (hidden buttons) use this same list.
    """
    init_state()
    out: list[tuple[dict[str, Any], int, str, str]] = []
    for obj in list_objects():
        if obj.get("kind") != "sequence":
            continue
        matches = obj.get("matches") or []
        if not matches:
            continue
        anchored = _anchored_index(obj)
        try:
            selected = int(obj.get("selected_match_index") or 0)
        except (TypeError, ValueError):
            selected = 0
        if selected == anchored:
            continue
        if selected < 0 or selected >= len(matches):
            continue
        chosen = matches[selected] or {}
        protein = (chosen.get("protein") or {}) if isinstance(chosen, dict) else {}
        entry = str(protein.get("entry_name") or "").strip()
        accession = str(
            protein.get("accession")
            or (chosen.get("accession") if isinstance(chosen, dict) else "")
            or ""
        ).strip()
        if not entry and not accession:
            continue
        # Prefer entry_name as the chip's primary label, accession as the
        # secondary "ID" line. Either may be missing; ghost chip handles
        # blanks.
        out.append((obj, selected, entry or accession, accession))
    return out


def fork_sequence_with_match(parent_id: str, match_index: int) -> str | None:
    """Spawn a new Sequence card from a non-anchored match of ``parent_id``.

    The user is viewing ``parent`` (anchored on match A) and clicks an
    alternative match B in the Top-5 switcher. To keep the original
    discussion intact, we create an independent Sequence object that:

      - references the same underlying sequence body (raw / normalized /
        protein) so alignment views still work;
      - carries only the chosen match in its ``matches`` list (the other
        four candidates are dropped — the fork represents a commitment to
        this single protein, not a parallel exploration);
      - anchors itself on that match so the chip label, ``@mention``
        resolution and tooltip all reflect the new identity (e.g.
        ``@HBA_HUMAN`` for a fork off ``@INS_HUMAN``).

    The parent's ``selected_match_index`` is reset to its anchor so the
    user returns to a clean state after spawning. Returns the new object
    id, or ``None`` if the fork could not be performed (bad parent /
    out-of-range index).
    """
    init_state()
    parent = get_object(parent_id)
    if not parent or parent.get("kind") != "sequence":
        return None
    matches = list(parent.get("matches") or [])
    if not matches:
        return None
    try:
        idx = int(match_index)
    except (TypeError, ValueError):
        return None
    if idx < 0 or idx >= len(matches):
        return None

    chosen = matches[idx] or {}
    protein = (chosen.get("protein") or {}) if isinstance(chosen, dict) else {}
    entry_name = str(protein.get("entry_name") or "").strip()
    accession = str(
        protein.get("accession") or (chosen.get("accession") if isinstance(chosen, dict) else "") or ""
    ).strip()
    suffix = entry_name or accession or f"alt{idx}"
    fork_id = f"{parent_id}__{suffix}"

    objects = st.session_state[_OBJECTS_KEY]
    order = st.session_state[_ORDER_KEY]
    if fork_id in objects:
        # Already spawned — just hand the existing fork back to the caller.
        return fork_id

    new_label = entry_name or accession or _next_letter_label()
    fork_obj: dict[str, Any] = {
        "id": fork_id,
        "kind": "sequence",
        "label": new_label,
        "display_name": new_label,
        "source": {
            "type": "forked_from_match",
            "parent_sequence_id": parent_id,
            "match_index": idx,
        },
        "fasta_header": parent.get("fasta_header"),
        "sequence_type": parent.get("sequence_type") or "UNKNOWN",
        "raw_sequence": parent.get("raw_sequence") or "",
        "normalized_sequence": parent.get("normalized_sequence") or "",
        "protein_sequence": parent.get("protein_sequence"),
        "length": parent.get("length") or 0,
        "status": "ready",
        "matches": [dict(chosen) if isinstance(chosen, dict) else chosen],
        "selected_match_index": 0,
        "anchored_match_index": 0,
        "warnings": [],
        "classification_reason": parent.get("classification_reason") or "",
        "confidence": parent.get("confidence"),
    }
    objects[fork_id] = fork_obj
    order.append(fork_id)

    # Reset parent's preview to its anchor so the user returns to a
    # clean state after spawning a new card.
    parent_anchor = _anchored_index(parent)
    if 0 <= parent_anchor < len(parent.get("matches") or []):
        parent["selected_match_index"] = parent_anchor

    return fork_id


# ---------------------------------------------------------------------------
# Protein helpers
# ---------------------------------------------------------------------------


def upsert_protein(
    *,
    accession: str,
    display_name: str | None = None,
    gene: str = "",
    organism: str = "",
    uniprot_id: str | None = None,
    card: dict[str, Any] | None = None,
    linked_sequence_id: str | None = None,
    match_score: float | None = None,
    last_origin_sequence_id: str | None = None,
) -> dict[str, Any]:
    """Create or update a Protein object keyed by accession."""
    init_state()
    accession = (accession or "").upper().strip()
    if not accession:
        raise ValueError("Protein accession is required")

    object_id = make_protein_id(accession)
    objects = st.session_state[_OBJECTS_KEY]
    order = st.session_state[_ORDER_KEY]
    existing = objects.get(object_id)

    if existing is None:
        protein_obj: dict[str, Any] = {
            "id": object_id,
            "kind": "protein",
            "label": accession,
            "display_name": display_name or uniprot_id or accession,
            "accession": accession,
            "uniprot_id": uniprot_id or "",
            "gene": gene or "",
            "organism": organism or "",
            "linked_sequence_ids": (
                [linked_sequence_id] if linked_sequence_id else []
            ),
            "card": card or {},
            "match_score": match_score,
            "last_origin_sequence_id": last_origin_sequence_id or linked_sequence_id,
        }
        objects[object_id] = protein_obj
        order.append(object_id)
        return protein_obj

    if display_name and not existing.get("display_name"):
        existing["display_name"] = display_name
    if uniprot_id and not existing.get("uniprot_id"):
        existing["uniprot_id"] = uniprot_id
    if gene and not existing.get("gene"):
        existing["gene"] = gene
    if organism and not existing.get("organism"):
        existing["organism"] = organism
    if card:
        # New card always wins — backend may have loaded richer detail.
        existing["card"] = card
    if match_score is not None:
        existing["match_score"] = match_score
    if linked_sequence_id:
        linked = existing.setdefault("linked_sequence_ids", [])
        if linked_sequence_id not in linked:
            linked.append(linked_sequence_id)
    if last_origin_sequence_id:
        existing["last_origin_sequence_id"] = last_origin_sequence_id
    return existing


def set_protein_last_origin(accession: str, sequence_id: str | None) -> None:
    obj = get_object(make_protein_id(accession))
    if obj and obj.get("kind") == "protein":
        obj["last_origin_sequence_id"] = sequence_id


# ---------------------------------------------------------------------------
# objects_patch (backend -> frontend)
# ---------------------------------------------------------------------------


def apply_objects_patch(patch: dict[str, Any] | None) -> None:
    """Apply an ``ObjectsPatch`` from the backend to the local registry.

    Patch shape::

        {
            "upsert": {"<id>": <object>, ...},
            "remove": ["<id>", ...],
            "set_selected": "<id>" | null
        }

    ``upsert`` values are merged at the top level (new keys overwrite old);
    nested fields like ``matches`` are replaced as whole lists (the backend
    always sends the full top-5 list, not deltas).
    """
    if not patch or not isinstance(patch, dict):
        return
    init_state()
    objects = st.session_state[_OBJECTS_KEY]
    order = st.session_state[_ORDER_KEY]

    for object_id in patch.get("remove") or []:
        objects.pop(object_id, None)
        if object_id in order:
            order.remove(object_id)
        if st.session_state[_SELECTED_KEY] == object_id:
            st.session_state[_SELECTED_KEY] = None

    upsert = patch.get("upsert") or {}
    for object_id, payload in upsert.items():
        if not isinstance(payload, dict):
            continue
        existing = objects.get(object_id)
        if existing is None:
            new_obj = dict(payload)
            # Seed the identity-anchor field for sequences arriving fresh
            # from the backend (the backend doesn't track it). Without this
            # the chip label would silently drift to whatever the user is
            # previewing in the Top-5 switcher.
            if new_obj.get("kind") == "sequence" and "anchored_match_index" not in new_obj:
                try:
                    new_obj["anchored_match_index"] = int(new_obj.get("selected_match_index") or 0)
                except (TypeError, ValueError):
                    new_obj["anchored_match_index"] = 0
            objects[object_id] = new_obj
            if object_id not in order:
                order.append(object_id)
        else:
            existing.update(payload)
            if existing.get("kind") == "sequence" and "anchored_match_index" not in existing:
                try:
                    existing["anchored_match_index"] = int(existing.get("selected_match_index") or 0)
                except (TypeError, ValueError):
                    existing["anchored_match_index"] = 0

    if "set_selected" in patch:
        target = patch["set_selected"]
        if target is None:
            return
        if target in objects:
            st.session_state[_SELECTED_KEY] = target


# ---------------------------------------------------------------------------
# Serialization for LLM / backend / persistence
# ---------------------------------------------------------------------------


def to_compact_summary(obj: dict[str, Any]) -> dict[str, Any]:
    """Compact JSON for LLM context (no full protein card)."""
    if obj["kind"] == "sequence":
        matches = obj.get("matches") or []
        selected_idx = int(obj.get("selected_match_index") or 0)
        chosen_match: dict[str, Any] | None = None
        if matches and 0 <= selected_idx < len(matches):
            m = matches[selected_idx]
            chosen_match = {
                "accession": m.get("accession"),
                "gene": (m.get("protein") or {}).get("gene"),
                "name": (m.get("protein") or {}).get("name"),
                "score": m.get("match_score"),
                "rank": m.get("rank", selected_idx),
            }
        return {
            "id": obj["id"],
            "kind": "sequence",
            "label": obj["label"],
            "sequence_type": obj.get("sequence_type"),
            "length": obj.get("length"),
            "status": obj.get("status"),
            "fasta_header": obj.get("fasta_header"),
            "selected_match": chosen_match,
            "selected_match_index": selected_idx,
            "matches_count": len(matches),
        }
    if obj["kind"] == "protein":
        return {
            "id": obj["id"],
            "kind": "protein",
            "label": obj["label"],
            "accession": obj.get("accession"),
            "uniprot_id": obj.get("uniprot_id"),
            "gene": obj.get("gene"),
            "organism": obj.get("organism"),
            "linked_sequence_ids": obj.get("linked_sequence_ids") or [],
        }
    return {"id": obj.get("id"), "kind": obj.get("kind")}


def registry_summaries() -> list[dict[str, Any]]:
    return [to_compact_summary(obj) for obj in list_objects()]


def serialize_for_request() -> dict[str, Any]:
    """Build the full ``objects`` dict the frontend sends to backend."""
    return {oid: dict(obj) for oid, obj in get_objects().items()}


def serialize_for_persistence() -> dict[str, Any]:
    """JSON-safe snapshot for ``working_memory.bioseq_workspace``."""
    return {
        "objects": serialize_for_request(),
        "object_order": list(get_order()),
        "selected_object_id": get_selected_id(),
        "_seq_label_counter": int(st.session_state.get(_LABEL_COUNTER_KEY, 0) or 0),
    }


def apply_persisted(snapshot: dict[str, Any] | None) -> None:
    """Restore registry from a previously serialized snapshot."""
    init_state()
    if not snapshot or not isinstance(snapshot, dict):
        return
    objects = snapshot.get("objects")
    order = snapshot.get("object_order")
    selected = snapshot.get("selected_object_id")
    counter = snapshot.get("_seq_label_counter")
    if isinstance(objects, dict):
        st.session_state[_OBJECTS_KEY] = {
            str(k): dict(v) for k, v in objects.items() if isinstance(v, dict)
        }
    if isinstance(order, list):
        st.session_state[_ORDER_KEY] = [
            oid for oid in order if oid in st.session_state[_OBJECTS_KEY]
        ]
    if isinstance(selected, str) and selected in st.session_state[_OBJECTS_KEY]:
        st.session_state[_SELECTED_KEY] = selected
    if isinstance(counter, int):
        st.session_state[_LABEL_COUNTER_KEY] = counter


# ---------------------------------------------------------------------------
# Convenience: bulk upsert of matches as Proteins
# ---------------------------------------------------------------------------


def ingest_matches_as_proteins(
    sequence_id: str,
    matches: Iterable[dict[str, Any]],
) -> None:
    """For each match in a Sequence's top-5, ensure a Protein registry entry."""
    for match in matches:
        protein = match.get("protein") or {}
        accession = protein.get("accession") or match.get("accession")
        if not accession:
            continue
        upsert_protein(
            accession=str(accession),
            display_name=protein.get("name") or accession,
            gene=protein.get("gene") or "",
            organism=protein.get("organism_scientific") or protein.get("organism") or "",
            uniprot_id=protein.get("uniprot_id") or "",
            card=protein,
            linked_sequence_id=sequence_id,
            match_score=match.get("match_score"),
        )
