"""Chat column: history, sequence-aware composer, and streamed assistant replies.

The composer accepts text, FASTA files, and UniProt accessions. Detected
sequences become workspace ``Sequence`` objects (see ``session_objects``)
and are referenced inline with ``@Seq_A`` chips instead of taking over
the chat with their raw text.
"""

from __future__ import annotations

import html
import re
import time
from collections.abc import Callable, Iterable
from pathlib import Path

import streamlit as st

import sequence_detection
import session_objects
from mock import conversation

SubmitHandler = Callable[[str], tuple[str, Iterable[str]]]

_ASSETS_DIR = Path(__file__).resolve().parent.parent / "assets"
_USER_AVATAR_PATH = _ASSETS_DIR / "UserAvatar.png"
_BOT_AVATAR_PATH = _ASSETS_DIR / "BotIcon.png"
USER_AVATAR: str | None = str(_USER_AVATAR_PATH) if _USER_AVATAR_PATH.exists() else None
BOT_AVATAR: str | None = str(_BOT_AVATAR_PATH) if _BOT_AVATAR_PATH.exists() else None


# ---------------------------------------------------------------------------
# Streaming + small helpers
# ---------------------------------------------------------------------------


def _stream_tokens(text: str, delay: float = 0.012) -> Iterable[str]:
    for word in text.split(" "):
        yield word + " "
        time.sleep(delay)


_MENTION_RE = re.compile(r"@([A-Za-z0-9_]+)")

# Strip any ``<a class="bioseq-mention" ...>@Seq_A</a>`` markup that may
# have leaked into a backend reply. Without this the LLM can pick up the
# HTML format from a previous turn and start emitting it verbatim — we
# normalize back to plain ``@Seq_A`` before rendering and persisting.
_ANCHOR_MENTION_RE = re.compile(
    r'<a\s+[^>]*class="[^"]*bioseq-mention[^"]*"[^>]*>\s*@?([^<]+?)\s*</a>',
    re.IGNORECASE,
)


def _sanitize_mentions_html(text: str) -> str:
    if not text or "<a" not in text:
        return text or ""
    return _ANCHOR_MENTION_RE.sub(lambda m: "@" + m.group(1).strip(), text)


def _label_to_object_id() -> dict[str, str]:
    """Map every label and accession in the registry to its object id."""
    mapping: dict[str, str] = {}
    for object_id, obj in session_objects.get_objects().items():
        label = obj.get("label")
        if label:
            mapping[label] = object_id
        accession = obj.get("accession")
        if accession:
            mapping[accession] = object_id
    return mapping


def _mention_anchor(object_id: str, label: str) -> str:
    return (
        f'<a href="#mention-{object_id}" class="bioseq-mention" '
        f'data-object-id="{object_id}">@{html.escape(label)}</a>'
    )


def _render_user_text(text: str) -> None:
    """Render user-typed text verbatim with clickable ``@`` mentions.

    User input is HTML-escaped first (so any ``<``/``>``/``&`` they typed
    stays literal), and the whole body is wrapped in a ``pre-wrap`` div
    so multi-line pastes keep their original line breaks instead of
    being collapsed by markdown.
    """
    label_to_id = _label_to_object_id()
    escaped = html.escape(text or "")

    def replace(match: re.Match) -> str:
        label = match.group(1)
        object_id = label_to_id.get(label)
        if not object_id:
            return match.group(0)
        return _mention_anchor(object_id, label)

    rendered = _MENTION_RE.sub(replace, escaped)
    st.markdown(
        f'<div class="bioseq-msg-body">{rendered}</div>',
        unsafe_allow_html=True,
    )


def _render_assistant_text(text: str) -> None:
    """Render assistant text as markdown but with clickable ``@`` mentions.

    Backend replies are trusted markdown (no escape) — we inject the
    anchor HTML for mentions inline and let ``unsafe_allow_html`` pass
    it through while markdown still handles the rest of the formatting.
    Any anchor markup that the LLM may have emitted itself is stripped
    back to plain ``@<label>`` first.
    """
    label_to_id = _label_to_object_id()
    text = _sanitize_mentions_html(text or "")

    def replace(match: re.Match) -> str:
        label = match.group(1)
        object_id = label_to_id.get(label)
        if not object_id:
            return match.group(0)
        return _mention_anchor(object_id, label)

    rendered = _MENTION_RE.sub(replace, text)
    st.markdown(rendered, unsafe_allow_html=True)


def _mention_click(object_id: str) -> None:
    session_objects.set_selected(object_id)


def _deselect_click() -> None:
    session_objects.set_selected(None)


def _render_mention_bridge() -> None:
    """Render one hidden button per registered object + a JS click bridge.

    The buttons live inside a ``st-key-mention_bridge`` container that
    CSS collapses to zero height. When the user clicks an ``@Seq_A``
    anchor, the JS finds the matching ``.st-key-mention_btn_<id>``
    wrapper and synthesises a click on its hidden button — that fires
    the ``on_click`` callback and reruns Streamlit with the new
    ``selected_object_id``.

    A sibling ``mention_btn___deselect__`` button clears the selection
    when the user clicks the Session Objects panel background.

    The container and the JS iframe are emitted on *every* render —
    even when the registry is empty — so the DOM structure right after
    the chat_area stays identical across reruns. Inserting them
    in/out used to coincide with several other structural changes on
    the first user message and crashed React with ``removeChild``.
    """
    objects = session_objects.get_objects()

    with st.container(key="mention_bridge"):
        for object_id in objects.keys():
            st.button(
                "·",
                key=f"mention_btn_{object_id}",
                on_click=_mention_click,
                args=(object_id,),
            )
        # Deselect button is always present so the container has a
        # consistent minimum child count across renders.
        st.button(
            "·",
            key="mention_btn___deselect__",
            on_click=_deselect_click,
        )

    import streamlit.components.v1 as components
    components.html(_MENTION_BRIDGE_JS, height=0, width=0)


_MENTION_BRIDGE_JS = r"""
<script>
(function () {
  const win = window.parent;
  const doc = win.document;
  if (win.__bioseqMentionBridgeInstalled) return;
  win.__bioseqMentionBridgeInstalled = true;

  // Match any anchor with a ``data-object-id`` — covers both inline
  // ``.bioseq-mention`` chips in chat history and large
  // ``.bioseq-chip`` cards in the Session Objects bar. A click on the
  // bar background (anywhere inside ``.st-key-object_bar`` that is
  // not a chip or interactive control) clears the selection instead.
  doc.addEventListener('click', function (event) {
    if (!event.target.closest) return;
    const anchor = event.target.closest('a[data-object-id]');
    if (anchor) {
      event.preventDefault();
      const oid = anchor.getAttribute('data-object-id');
      if (!oid) return;
      const wrapper = doc.querySelector('.st-key-mention_btn_' + oid);
      if (!wrapper) return;
      const btn = wrapper.querySelector('button');
      if (btn) btn.click();
      return;
    }
    const bar = event.target.closest('.st-key-object_bar');
    if (!bar) return;
    // Don't deselect if the user clicked something interactive inside
    // the bar (e.g. a future button) — only the bare background.
    if (event.target.closest('button')) return;
    const wrapper = doc.querySelector('.st-key-mention_btn___deselect__');
    if (!wrapper) return;
    const btn = wrapper.querySelector('button');
    if (btn) btn.click();
  }, true);
})();
</script>
"""


def _render_user_message(msg: dict) -> None:
    with st.chat_message("user", avatar=USER_AVATAR):
        attachments = msg.get("attachments") or []
        if attachments:
            badges: list[str] = []
            for att in attachments:
                if att.get("kind") == "file":
                    badges.append(
                        f":blue-badge[file {att.get('file_name', 'file')}"
                        f" — {att.get('entry_count', 0)} entries]"
                    )
            if badges:
                st.markdown(" ".join(badges))
        _render_user_text(msg.get("content", ""))

        # Decide which Sequence objects to summarise below the message.
        # Preferred source: ``object_ids`` populated at submission time. If
        # missing (e.g. on a DB-restored session that only kept role+content),
        # fall back to scanning the message text for ``@Seq_X`` mentions and
        # resolving them against the current registry. This keeps the
        # attachment card visible across page reloads.
        object_ids = list(msg.get("object_ids") or [])
        if not object_ids:
            label_to_id = _label_to_object_id()
            seen: set[str] = set()
            for match in _MENTION_RE.finditer(msg.get("content") or ""):
                resolved = label_to_id.get(match.group(1))
                if resolved and resolved not in seen:
                    object_ids.append(resolved)
                    seen.add(resolved)

        for object_id in object_ids:
            obj = session_objects.get_object(object_id)
            if not obj:
                continue
            # Only Sequences get an attachment card under the bubble; a
            # standalone Protein mention is enough as an inline chip.
            if obj.get("kind") != "sequence":
                continue
            _render_inline_object_summary(obj)


def _render_inline_object_summary(obj: dict) -> None:
    """Render a compact attachment-card under the user message text.

    Lives inside the user's chat bubble — styled as a white pill on the
    light-blue bubble background so it reads as a clearly-distinct
    attachment without needing a second nested rectangle.
    """
    if obj["kind"] == "sequence":
        label = html.escape(obj.get("label") or "?")
        seq_type = html.escape((obj.get("sequence_type") or "UNKNOWN").upper())
        length = obj.get("length") or 0
        status = obj.get("status") or "draft"
        status_label = {
            "draft": "draft", "queued": "queued", "classifying": "classifying",
            "searching": "searching…", "ready": "ready", "not_searched": "not searched",
            "error": "error",
        }.get(status, status)
        matches = obj.get("matches") or []
        best_html = ""
        if matches:
            top = matches[0]
            protein = top.get("protein") or {}
            accession = protein.get("accession") or top.get("accession") or ""
            gene = protein.get("gene") or ""
            score = top.get("match_score")
            score_str = (
                f"{score:.0%}" if isinstance(score, float) and score <= 1
                else f"{score:.0f}%" if isinstance(score, (int, float)) else ""
            )
            best_bits: list[str] = []
            if accession:
                best_bits.append(html.escape(accession))
            if gene:
                best_bits.append(html.escape(gene))
            if score_str:
                best_bits.append(score_str)
            best_html = (
                '<span class="bioseq-attach-divider"></span>'
                '<span class="bioseq-attach-best-label">best:</span>'
                f'<span class="bioseq-attach-best">{" · ".join(best_bits)}</span>'
            )
        st.markdown(
            f'<div class="bioseq-attach-card">'
            f'<span class="bioseq-attach-label">{label}</span>'
            f'<span class="bioseq-attach-meta">{seq_type} · {length} aa</span>'
            f'<span class="bioseq-attach-status bioseq-attach-status-{status}">'
            f"{html.escape(status_label)}</span>"
            f"{best_html}"
            f"</div>",
            unsafe_allow_html=True,
        )
    elif obj["kind"] == "protein":
        accession = html.escape(obj.get("accession") or obj.get("label") or "?")
        gene = html.escape(obj.get("gene") or "")
        organism = html.escape(obj.get("organism") or "")
        bits = [bit for bit in (gene, organism) if bit]
        meta = " · ".join(bits) if bits else "UniProt entry"
        st.markdown(
            f'<div class="bioseq-attach-card">'
            f'<span class="bioseq-attach-label">{accession}</span>'
            f'<span class="bioseq-attach-meta">{meta}</span>'
            f'<span class="bioseq-attach-status bioseq-attach-status-protein">UniProt</span>'
            f"</div>",
            unsafe_allow_html=True,
        )


# ---------------------------------------------------------------------------
# Composer: file upload + sequence detection
# ---------------------------------------------------------------------------


def _handle_uploaded_files(files: list, message_id: str) -> tuple[list[str], list[str]]:
    """Ingest uploaded files into the registry. Returns (object_ids, warnings)."""
    object_ids: list[str] = []
    warnings: list[str] = []
    for file in files or []:
        try:
            raw = file.getvalue() if hasattr(file, "getvalue") else file.read()
        except Exception as exc:
            warnings.append(f"Could not read {getattr(file, 'name', 'file')}: {exc}")
            continue
        parsed = sequence_detection.parse_uploaded_file(getattr(file, "name", "uploaded.fasta"), raw)
        warnings.extend(parsed.get("warnings", []))
        for detected in parsed.get("raw_objects", []):
            obj = session_objects.upsert_sequence(
                normalized_sequence=detected.normalized_sequence,
                raw_sequence=detected.raw_sequence,
                sequence_type=detected.sequence_type,
                fasta_header=detected.fasta_header,
                source={
                    "type": "file",
                    "file_name": parsed["file_name"],
                    "message_id": message_id,
                },
                status="queued",
                warnings=detected.warnings,
                classification_reason=detected.reason,
                confidence=detected.confidence,
            )
            object_ids.append(obj["id"])
    return object_ids, warnings


def _ingest_text_sequences(text: str, message_id: str) -> tuple[str, list[str]]:
    """Detect sequences in user text and register them.

    Returns ``(display_text, object_ids)``. ``display_text`` has long raw
    sequences replaced with ``@Seq_A`` mentions.
    """
    detection = sequence_detection.detect_from_text(text)
    object_ids: list[str] = []
    label_mapping: dict[int, str] = {}
    for index, detected in enumerate(detection.sequences):
        if not detected.normalized_sequence:
            continue
        obj = session_objects.upsert_sequence(
            normalized_sequence=detected.normalized_sequence,
            raw_sequence=detected.raw_sequence,
            sequence_type=detected.sequence_type,
            fasta_header=detected.fasta_header,
            source={
                "type": "pasted_text",
                "file_name": None,
                "message_id": message_id,
            },
            status="queued",
            warnings=detected.warnings,
            classification_reason=detected.reason,
            confidence=detected.confidence,
        )
        object_ids.append(obj["id"])
        label_mapping[index] = obj["label"]

    display_text = detection.display_text or text
    for index, label in label_mapping.items():
        display_text = display_text.replace(f"@SEQ_PLACEHOLDER_{index}", f"@{label}")

    # Also surface UniProt mentions as @labels for the LLM to resolve.
    for ref in detection.uniprot_refs:
        token = ref.token
        if f"@{token}" not in display_text:
            display_text = display_text.replace(token, f"@{token}", 1)

    return display_text, object_ids


# ---------------------------------------------------------------------------
# Submission flow
# ---------------------------------------------------------------------------


def _stage_submission(text: str, files: list) -> bool:
    """Phase-1 of submission: ingest the message and stash a pending run.

    The actual backend call happens on the *next* render so the user's
    message and a "working" placeholder are visible while we wait.
    Returns True iff something was queued.
    """
    if not text.strip() and not files:
        return False

    message_id = f"msg_{len(st.session_state.messages):04d}"
    file_object_ids, file_warnings = _handle_uploaded_files(files or [], message_id)
    display_text, text_object_ids = _ingest_text_sequences(text or "", message_id)
    all_object_ids = list(dict.fromkeys(file_object_ids + text_object_ids))

    if file_warnings:
        st.session_state.backend_warnings.extend(file_warnings)

    if all_object_ids and session_objects.get_selected_id() is None:
        session_objects.set_selected(all_object_ids[0])

    for object_id in all_object_ids:
        session_objects.set_sequence_status(object_id, "searching")

    attachments = []
    for file in files or []:
        attachments.append(
            {
                "kind": "file",
                "file_name": getattr(file, "name", "uploaded.fasta"),
                "entry_count": sum(1 for oid in file_object_ids if oid in all_object_ids),
            }
        )

    st.session_state.messages.append(
        {
            "role": "user",
            "content": display_text,
            "object_ids": all_object_ids,
            "attachments": attachments,
            "id": message_id,
        }
    )

    st.session_state["pending_run"] = {
        "raw_text": text or "",
        "display_text": display_text,
        "message_id": message_id,
    }
    return True


def _run_pending(on_submit: SubmitHandler | None) -> None:
    """Phase-2: execute the queued backend call and render the reply.

    Renders an assistant chat bubble directly below the user message
    with a live "working…" status that swaps to the final reply when
    the backend returns. The reply is rendered via
    ``_render_assistant_text`` — the same function the main loop uses
    for historical messages — so the DOM produced here matches the DOM
    produced on the next rerun. This used to be ``st.write_stream`` +
    a forced ``st.rerun`` afterwards, but that combination emitted a
    different DOM in the streaming pass than in the post-rerun pass and
    Streamlit's reconciler crashed React with
    ``Failed to execute 'removeChild' on 'Node'`` on the transition.
    """
    pending = st.session_state.pop("pending_run", None)
    if not pending:
        return

    # Run the backend FIRST and only emit the chat_message bubble after
    # we have the final reply. The bubble's DOM shape must match what
    # the main history loop produces for the same message on subsequent
    # renders — otherwise Streamlit's reconciler crashes React with
    # ``removeChild`` when the bubble's children change shape between
    # first paint and the next rerun. Previously we wrapped the bubble
    # around an ``st.empty()`` placeholder for a "Thinking…" indicator,
    # which made the first-render structure
    # ``chat_message > placeholder + markdown`` while later renders
    # produced ``chat_message > markdown``.
    #
    # No spinner is opened here — the real backend already shows one
    # from inside ``_handle_vector_db_submission`` via ``st.spinner``.
    # Adding another at this layer rendered the indicator twice.
    try:
        if on_submit is None:
            reply, reveals = conversation.route(
                pending["raw_text"], st.session_state.conv_state
            )
        else:
            reply, reveals = on_submit(
                pending["display_text"] or pending["raw_text"]
            )
    except Exception as exc:  # surface the failure inline rather than crashing the app
        reply = f"**Backend error:** {exc}"
        reveals = set()
    reply = _sanitize_mentions_html(reply)

    with st.chat_message("assistant", avatar=BOT_AVATAR):
        _render_assistant_text(reply)

    st.session_state.card_sections_revealed.update(reveals)
    if on_submit is None and (
        st.session_state.conv_state.step >= 1
        and st.session_state.candidates is None
        and st.session_state.on_first_search is not None
    ):
        st.session_state.on_first_search()
    st.session_state.messages.append({"role": "assistant", "content": reply})
    # No ``st.rerun`` here. The right column is rendered *after*
    # ``chat.render`` in ``main()`` and will pick up the session_state
    # mutations the backend just applied in the same script run.


def _reset_conversation() -> None:
    """Reset = start a brand-new session.

    We deliberately keep ``user_id`` (cookie identity) so the sidebar history
    survives. The previous ``session_id`` row in ``public.chat_sessions`` is
    left intact as part of the user's history; we just mint a new one.
    """
    import session_identity  # noqa: WPS433  (avoid circular import at module load)

    for k in (
        "messages",
        "conv_state",
        "candidates",
        "selected_candidate_idx",
        "card_sections_revealed",
        "pending_assistant",
        "vector_db_result",
        "query_protein_sequence",
        "objects",
        "object_order",
        "selected_object_id",
        "_seq_label_counter",
    ):
        st.session_state.pop(k, None)
    session_identity.start_new_session(reason="chat_reset_button")


# ---------------------------------------------------------------------------
# Composer rendering
# ---------------------------------------------------------------------------


def render(on_first_search, on_submit: SubmitHandler | None = None) -> None:
    """Render the chat column."""
    st.session_state.on_first_search = on_first_search
    session_objects.init_state()

    with st.container(key="chat_toolbar"):
        head_col, reset_col = st.columns([5, 1], vertical_alignment="center")
        with head_col:
            st.markdown("<div class='chat-title'>Conversation</div>", unsafe_allow_html=True)
        with reset_col:
            if st.button(
                "Reset",
                help="Clear the conversation and start over",
                key="chat_reset_btn",
            ):
                _reset_conversation()
                st.rerun()

    has_user_message = any(m["role"] == "user" for m in st.session_state.messages)
    # IMPORTANT: keep the chat_area widget *identical* across reruns.
    # Previously we switched between ``st.container(border=False)`` (no
    # height) and ``st.container(height=540, border=False)`` depending on
    # whether the user had spoken yet. Streamlit treats those as
    # different widget types and re-mounts the subtree on the transition
    # — and the first message of a fresh session simultaneously inserts
    # the mention bridge below it and removes the demo-chip columns,
    # producing a reconciliation pass dense enough to crash React with
    # ``Failed to execute 'removeChild' on 'Node'``. Pinning the height
    # makes the container stable; the empty area below the welcome
    # message in the empty state is a small UX cost for crash-free
    # session switches and first turns.
    chat_area = st.container(height=540, border=False, key="chat_area")

    with chat_area:
        for msg in st.session_state.messages:
            if msg["role"] == "user":
                _render_user_message(msg)
            else:
                with st.chat_message("assistant", avatar=BOT_AVATAR):
                    _render_assistant_text(msg["content"])

        # If a submission is pending (user message already in history),
        # render the assistant bubble inline below it with a live status
        # that swaps to the streamed reply when the backend returns.
        if st.session_state.get("pending_run"):
            _run_pending(on_submit)

        # Legacy stream slot — kept for callers that still set
        # ``pending_assistant`` directly (e.g. the demo chip).
        pending = st.session_state.pop("pending_assistant", None)
        if pending:
            with st.chat_message("assistant", avatar=BOT_AVATAR):
                st.write_stream(_stream_tokens(pending))
            st.session_state.messages.append({"role": "assistant", "content": pending})

        # Demo chip lives inside chat_area so its appearance/disappearance
        # only mutates *children* of the chat_area container, never the
        # positions of siblings after it (mention bridge, composer). That
        # keeps Streamlit's reconciliation localised and avoids the
        # cascading structural change that crashed React on the first
        # message of a fresh session.
        if not has_user_message:
            chip_cols = st.columns([1, 4, 1])
            with chip_cols[1]:
                if st.button(
                    "✨  Try the demo sequence — UNC5C (Human)",
                    width="stretch",
                    key="try_example_chip",
                ):
                    if _stage_submission(conversation.example_first_message(), []):
                        st.rerun()

    # Hidden bridge buttons so JS can dispatch clicks on inline @mentions
    # into the Streamlit event loop. Rendered after the chat history so
    # the click handlers attach to the latest registry state.
    _render_mention_bridge()

    _render_composer(on_submit)


def _render_composer(on_submit: SubmitHandler | None) -> None:
    """Composer with a live sequence-detection preview above the input.

    Uses ``st.chat_input`` (auto-resizing textarea, embedded send button,
    built-in ``+`` upload icon via ``accept_file="multiple"``). On top of
    that we inject a small piece of JavaScript that listens to the
    textarea's ``input`` events and renders a preview badge above the
    input. The preview slot has a reserved height so the layout never
    jumps between "no preview" and "PROTEIN detected" states.
    """
    # Autocomplete dropdown — populated by JS when the input contains an
    # unfinished ``@token`` pattern. Hidden via ``:empty`` CSS otherwise.
    st.markdown(
        '<div id="bioseq-autocomplete" class="bioseq-autocomplete-slot"></div>',
        unsafe_allow_html=True,
    )
    # Reserved-height preview slot above the composer. Content is
    # populated by the JS injected below — server-side this stays as a
    # neutral hint until the first keystroke.
    st.markdown(
        '<div id="bioseq-live-preview" class="bioseq-preview-slot">'
        '<span class="bioseq-preview-hint">'
        "Type or paste a sequence / UniProt accession to see a live preview here."
        "</span>"
        "</div>",
        unsafe_allow_html=True,
    )

    user_input = st.chat_input(
        "Paste a sequence, ask a question, or attach a FASTA file…",
        accept_file="multiple",
        file_type=["fa", "fasta", "faa", "txt"],
    )

    _inject_live_preview_js()

    if user_input is None:
        return

    if isinstance(user_input, str):
        text = user_input
        files: list = []
    else:
        text = getattr(user_input, "text", "") or ""
        files = list(getattr(user_input, "files", []) or [])

    if not text.strip() and not files:
        return

    if _stage_submission(text, files):
        st.rerun()


_LIVE_PREVIEW_JS = r"""
<script>
(function () {
  const win = window.parent;
  const doc = win.document;

  // -------- Detection (1:1 port of sequence_detection.py) --------
  // Server-side detection lives in app/frontend/sequence_detection.py.
  // This block mirrors its functions one-for-one so the live preview and
  // the post-submit registry never disagree. If you change one, change
  // the other — the helper names match on purpose to make diffing easy.
  const DNA_STRICT = new Set(['A', 'C', 'G', 'T']);
  const NUCLEOTIDE_AMBIGUOUS = new Set('ACGTUNRYSWKMBDHV'.split(''));
  const PROTEIN_EXTENDED = new Set('ACDEFGHIKLMNPQRSTVWYXBZJUO'.split(''));
  const PROTEIN_STANDARD = new Set('ACDEFGHIKLMNPQRSTVWY'.split(''));
  const PROTEIN_ONLY = new Set(
    Array.from(PROTEIN_STANDARD).filter((c) => !NUCLEOTIDE_AMBIGUOUS.has(c))
  );
  const ALLOWED_AA = (function () {
    const s = new Set();
    PROTEIN_EXTENDED.forEach((c) => s.add(c));
    NUCLEOTIDE_AMBIGUOUS.forEach((c) => s.add(c));
    s.add('*');
    return s;
  })();
  const MIN_LEN = 30;
  const SEQ_RUN_RE_SRC = '[ACDEFGHIKLMNPQRSTVWYBJUOZX*\\-\\s\\d.]{' + MIN_LEN + ',}';
  const AMINO_LETTER_RE = /[ACDEFGHIKLMNPQRSTVWYBJUOZX*]/i;
  const ACC_RE = /^(?:[OPQ][0-9][A-Z0-9]{3}[0-9]|[A-NR-Z][0-9](?:[A-Z][A-Z0-9]{2}[0-9]){1,2})$/;
  const MNEMONIC_RE = /^[A-Z0-9]{1,10}_[A-Z0-9]{1,5}$/;

  // normalize_sequence
  function normalize(raw) {
    if (!raw) return '';
    const lines = raw.split('\n').filter((ln) => !ln.startsWith('>'));
    let body = lines.join('\n').replace(/[\s\-.0-9]/g, '').toUpperCase();
    let out = '';
    for (let i = 0; i < body.length; i++) {
      if (ALLOWED_AA.has(body[i])) out += body[i];
    }
    return out;
  }

  // classify_sequence (preview-trimmed: returns {type, length} or null)
  function classify(seq) {
    if (!seq) return null;
    const letters = new Set(seq);
    for (const c of PROTEIN_ONLY) {
      if (letters.has(c)) return { type: 'PROTEIN', length: seq.length };
    }
    let allDna = true;
    for (const c of letters) { if (!DNA_STRICT.has(c)) { allDna = false; break; } }
    if (allDna) return seq.length >= MIN_LEN ? { type: 'DNA', length: seq.length } : null;
    if (letters.has('U') && !letters.has('T')) {
      return seq.length >= MIN_LEN ? { type: 'RNA', length: seq.length } : null;
    }
    let allAmb = true;
    for (const c of letters) { if (!NUCLEOTIDE_AMBIGUOUS.has(c)) { allAmb = false; break; } }
    if (allAmb) return seq.length >= MIN_LEN ? { type: 'DNA', length: seq.length } : null;
    let allProt = true;
    for (const c of letters) { if (!PROTEIN_EXTENDED.has(c)) { allProt = false; break; } }
    if (allProt) return seq.length >= MIN_LEN ? { type: 'PROTEIN', length: seq.length } : null;
    return null;
  }

  // _split_inline_fasta_header
  function splitInlineFastaHeader(line) {
    if (!line.startsWith('>')) return [line, ''];
    let i = line.length;
    while (i > 0) {
      const ch = line[i - 1];
      const isSpace = /\s/.test(ch);
      const isAaUpper = ch === ch.toUpperCase() && ch !== ch.toLowerCase() && PROTEIN_EXTENDED.has(ch);
      if (isSpace || isAaUpper) { i -= 1; continue; }
      break;
    }
    if (i >= line.length) return [line, ''];
    const body = line.slice(i).trim();
    if (!body) return [line, ''];
    if (body.replace(/\s/g, '').length < MIN_LEN) return [line, ''];
    return [line.slice(0, i).replace(/\s+$/, ''), body];
  }

  // parse_fasta
  function parseFasta(text) {
    if (!text || text.indexOf('>') === -1) return [];
    const entries = [];
    let header = null;
    let body = [];
    const lines = text.split('\n');
    for (const line of lines) {
      if (line.startsWith('>')) {
        if (header !== null) {
          entries.push({ header, body: body.join('\n').trim() });
        }
        const split = splitInlineFastaHeader(line);
        header = split[0].trim();
        body = split[1] ? [split[1]] : [];
      } else if (header !== null) {
        body.push(line);
      }
    }
    if (header !== null) {
      entries.push({ header, body: body.join('\n').trim() });
    }
    return entries.filter((e) => e.body);
  }

  function spanOverlapsAny(span, spans) {
    const s = span[0], e = span[1];
    for (const sp of spans) { if (!(e <= sp[0] || s >= sp[1])) return true; }
    return false;
  }

  // detect_from_text (preview-trimmed: flat {kind, ...} items for renderPreview)
  function detect(text) {
    const out = [];
    if (!text) return out;

    const redactSpans = [];
    const fastaEntries = parseFasta(text);
    if (fastaEntries.length > 0) {
      const firstHdr = text.indexOf('>');
      if (firstHdr !== -1) redactSpans.push([firstHdr, text.length]);
      for (const entry of fastaEntries) {
        const norm = normalize(entry.body);
        const c = classify(norm);
        if (c) out.push({ kind: 'sequence', type: c.type, length: c.length });
      }
    } else {
      // Blank-line segmentation so a sequence run can't bleed into the
      // user's question above or below it.
      const segments = [];
      let cursor = 0;
      const blankRe = /\n[ \t]*\n/g;
      let bm;
      while ((bm = blankRe.exec(text)) !== null) {
        segments.push([cursor, bm.index]);
        cursor = bm.index + bm[0].length;
      }
      segments.push([cursor, text.length]);

      const runRe = new RegExp(SEQ_RUN_RE_SRC, 'gi');
      const raw = [];
      for (const seg of segments) {
        const segText = text.slice(seg[0], seg[1]);
        if (!segText.trim()) continue;
        runRe.lastIndex = 0;
        let m;
        while ((m = runRe.exec(segText)) !== null) {
          let lo = m.index;
          let hi = m.index + m[0].length;
          while (lo < hi && !AMINO_LETTER_RE.test(segText[lo])) lo++;
          while (hi > lo && !AMINO_LETTER_RE.test(segText[hi - 1])) hi--;
          if (hi - lo < MIN_LEN / 2) continue;
          const norm = normalize(segText.slice(lo, hi));
          if (norm.length < MIN_LEN) continue;
          const c = classify(norm);
          if (!c) continue;
          raw.push({ type: c.type, length: c.length, span: [seg[0] + lo, seg[0] + hi] });
        }
      }

      // Merge adjacent detections separated only by whitespace — FASTA
      // wraps and blank lines inside one sequence aren't real boundaries.
      const merged = [];
      for (const det of raw) {
        const prev = merged.length ? merged[merged.length - 1] : null;
        if (prev && text.slice(prev.span[1], det.span[0]).trim() === '') {
          const newSpan = [prev.span[0], det.span[1]];
          const c = classify(normalize(text.slice(newSpan[0], newSpan[1])));
          if (c) {
            prev.type = c.type;
            prev.length = c.length;
            prev.span = newSpan;
          }
        } else {
          merged.push({ type: det.type, length: det.length, span: det.span.slice() });
        }
      }
      for (const det of merged) {
        out.push({ kind: 'sequence', type: det.type, length: det.length });
        redactSpans.push(det.span);
      }
    }

    // UniProt accessions / mnemonics — suppressed inside redacted spans.
    const tokenRe = /@?([A-Za-z0-9_]{4,15})/g;
    let m;
    while ((m = tokenRe.exec(text)) !== null) {
      const token = m[1];
      const upper = token.toUpperCase();
      if (upper.startsWith('SEQ_') && /^SEQ_[A-Z]+$/.test(upper)) continue;
      if (!ACC_RE.test(upper) && !MNEMONIC_RE.test(upper)) continue;
      const start = m.index + (m[0].startsWith('@') ? 1 : 0);
      const end = start + token.length;
      if (spanOverlapsAny([start, end], redactSpans)) continue;
      out.push({ kind: 'uniprot', token });
    }
    return out;
  }

  function renderPreview(text) {
    const slot = doc.getElementById('bioseq-live-preview');
    if (!slot) return;
    const findings = detect(text || '');
    if (!findings.length) {
      slot.innerHTML =
        '<span class="bioseq-preview-hint">Type or paste a sequence / UniProt accession to see a live preview here.</span>';
      return;
    }
    const html = findings.map((f) => {
      if (f.kind === 'sequence') {
        return (
          '<span class="bioseq-preview-badge bioseq-preview-badge-seq">' +
          f.type + ' sequence · ' + f.length + ' aa · will attach on send' +
          '</span>'
        );
      }
      return (
        '<span class="bioseq-preview-badge bioseq-preview-badge-uniprot">' +
        'UniProt ' + f.token + '</span>'
      );
    }).join(' ');
    slot.innerHTML = html;
  }

  function findChatInput() {
    // Streamlit's chat_input has evolved across versions: older builds
    // expose a real <textarea>, newer ones a contenteditable <div>.
    // Try every shape we know about, plus aria-label fallbacks for
    // future builds that might rename testids.
    return (
      doc.querySelector('[data-testid="stChatInput"] textarea')
      || doc.querySelector('textarea[data-testid="stChatInputTextArea"]')
      || doc.querySelector('[data-testid="stChatInput"] [contenteditable="true"]')
      || doc.querySelector('div[class*="stChatInput"] textarea')
      || doc.querySelector('div[class*="stChatInput"] [contenteditable="true"]')
      || doc.querySelector('textarea[aria-label*="sequence" i]')
      || doc.querySelector('textarea[aria-label*="Paste" i]')
      || doc.querySelector('textarea[aria-label*="ask" i]')
    );
  }

  function readValue(el) {
    if (!el) return '';
    if (el.tagName === 'TEXTAREA' || el.tagName === 'INPUT') return el.value || '';
    return el.innerText || el.textContent || '';
  }

  // ---- Helpers: insert text into the chat input ----
  function focusInput() {
    const input = findChatInput();
    if (!input) return null;
    if (doc.activeElement !== input) input.focus();
    return input;
  }

  function setNativeValue(input, value) {
    // React-controlled inputs need the native setter + dispatched event
    // so Streamlit's onChange listener picks the new value up.
    if (input.tagName === 'TEXTAREA' || input.tagName === 'INPUT') {
      const proto = Object.getPrototypeOf(input);
      const setter = Object.getOwnPropertyDescriptor(proto, 'value');
      if (setter && setter.set) {
        setter.set.call(input, value);
        return;
      }
      input.value = value;
    } else {
      input.textContent = value;
    }
  }

  function insertAtCaret(text) {
    const input = focusInput();
    if (!input) return;
    // execCommand('insertText') is the most reliable cross-browser way
    // to insert at the caret AND fire the proper input events. Still
    // works in all current browsers despite being marked deprecated.
    let ok = false;
    try {
      ok = doc.execCommand('insertText', false, text);
    } catch (e) { ok = false; }
    if (!ok) {
      const current = readValue(input);
      setNativeValue(input, current + text);
      input.dispatchEvent(new Event('input', { bubbles: true }));
    }
  }

  function replaceLastMention(label) {
    const input = focusInput();
    if (!input) return;
    const current = readValue(input);
    const m = /(?:^|\s)@([A-Za-z0-9_]*)$/.exec(current);
    let prefix;
    if (m) {
      // Cut the partial '@xxx' at end and replace with the chosen label.
      prefix = current.slice(0, current.length - m[1].length - 1);
    } else {
      prefix = current.length && !/\s$/.test(current) ? current + ' ' : current;
    }
    const newValue = prefix + '@' + label + ' ';
    setNativeValue(input, newValue);
    if (input.tagName === 'TEXTAREA' || input.tagName === 'INPUT') {
      input.selectionStart = input.selectionEnd = newValue.length;
    }
    input.dispatchEvent(new Event('input', { bubbles: true }));
    input.focus();
  }

  // ---- Autocomplete rendering ----
  function collectChipLabels() {
    const out = [];
    doc.querySelectorAll('a.bioseq-chip[data-object-id]').forEach(function (chip) {
      const label = (chip.getAttribute('data-label') || '').trim();
      if (label && !out.includes(label)) out.push(label);
    });
    return out;
  }

  function detectPartial(text) {
    const m = /(?:^|\s)@([A-Za-z0-9_]*)$/.exec(text || '');
    return m ? m[1] : null;
  }

  function renderAutocomplete(text) {
    const slot = doc.getElementById('bioseq-autocomplete');
    if (!slot) return;
    const partial = detectPartial(text);
    if (partial === null) {
      slot.innerHTML = '';
      return;
    }
    const lowered = partial.toLowerCase();
    const labels = collectChipLabels()
      .filter(function (l) { return l.toLowerCase().startsWith(lowered); })
      .slice(0, 8);
    if (!labels.length) {
      slot.innerHTML = '';
      return;
    }
    slot.innerHTML =
      '<span class="bioseq-autocomplete-hint">Mention:</span>' +
      labels.map(function (l) {
        return '<button type="button" class="bioseq-autocomplete-item" data-label="' +
               l + '">@' + l + '</button>';
      }).join('');
  }

  // ---- @mention highlighting inside the chat input ----
  // A "mirror" div is positioned exactly over the textarea. The
  // textarea's own text is made transparent via CSS (caret + selection
  // still visible); the mirror renders the same text with @<label>
  // runs wrapped in a styled span. Caret/character widths stay aligned
  // because the mirror inherits font/padding/border from the textarea
  // and the mention span uses the same font-weight as the rest.
  const MIRROR_STYLES = [
    'fontFamily','fontSize','fontWeight','fontStyle','lineHeight',
    'letterSpacing','wordSpacing','tabSize','textTransform','textIndent',
    'paddingTop','paddingRight','paddingBottom','paddingLeft',
    'borderTopWidth','borderRightWidth','borderBottomWidth','borderLeftWidth',
    'borderTopStyle','borderRightStyle','borderBottomStyle','borderLeftStyle'
  ];
  function escapeHtml(s) {
    return (s || '').replace(/[&<>"']/g, function (c) {
      return ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'})[c];
    });
  }
  function ensureMirror() {
    const input = findChatInput();
    if (!input) return null;
    // Mirror overlay only works with the textarea variant — the CSS that
    // makes input text transparent is scoped to textareas, so applying
    // the mirror to a contenteditable would render the text twice.
    if (input.tagName !== 'TEXTAREA') return null;
    // The mirror lives at document.body level (NOT as a child of the
    // textarea's parent) and is positioned with position:fixed from the
    // textarea's bounding rect. Appending it inside the Streamlit-
    // managed parent used to crash React with "removeChild" errors
    // whenever Streamlit reconciled the chat input — most reliably on
    // session switch. At body level the node is outside React's tree.
    //
    // Sweep up any stale mirrors that an earlier build (or a hot
    // reload of the old code) might have left attached inside the
    // Streamlit DOM. Without this we'd end up with two visible mirrors
    // after the upgrade and the old one would still poison React.
    doc.querySelectorAll('.bioseq-input-mirror').forEach(function (m) {
      if (m.parentElement !== doc.body) m.remove();
    });
    let mirror = doc.body.querySelector(':scope > .bioseq-input-mirror');
    if (!mirror) {
      mirror = doc.createElement('div');
      mirror.className = 'bioseq-input-mirror';
      mirror.setAttribute('aria-hidden', 'true');
      doc.body.appendChild(mirror);
    }
    return { input: input, mirror: mirror };
  }
  function syncMirrorBox(input, mirror) {
    const cs = win.getComputedStyle(input);
    for (let i = 0; i < MIRROR_STYLES.length; i++) {
      const k = MIRROR_STYLES[i];
      mirror.style[k] = cs[k];
    }
    mirror.style.borderColor = 'transparent';
    const rect = input.getBoundingClientRect();
    if (rect.width <= 0 || rect.height <= 0) {
      mirror.style.display = 'none';
      return;
    }
    mirror.style.display = '';
    mirror.style.top = rect.top + 'px';
    mirror.style.left = rect.left + 'px';
    mirror.style.width = rect.width + 'px';
    mirror.style.height = rect.height + 'px';
    mirror.scrollTop = input.scrollTop;
    mirror.scrollLeft = input.scrollLeft;
  }
  function renderInputHighlight(value) {
    const ctx = ensureMirror();
    if (!ctx) return;
    syncMirrorBox(ctx.input, ctx.mirror);
    const labels = {};
    collectChipLabels().forEach(function (l) { labels[l] = true; });
    const re = /@([A-Za-z0-9_]+)/g;
    const text = value || '';
    let html = '';
    let last = 0;
    let m;
    while ((m = re.exec(text)) !== null) {
      html += escapeHtml(text.slice(last, m.index));
      if (labels[m[1]]) {
        html += '<span class="bioseq-mention-tok">@' + escapeHtml(m[1]) + '</span>';
      } else {
        html += escapeHtml(m[0]);
      }
      last = m.index + m[0].length;
    }
    html += escapeHtml(text.slice(last));
    // Textarea reserves space for an implicit trailing line; mirror needs the same.
    if (text.endsWith('\n') || text === '') html += '&nbsp;';
    ctx.mirror.innerHTML = html;
  }

  // ---- Re-install the poller on every iframe load ----
  // Critical: setInterval is scheduled on the PARENT window so the
  // timer survives the iframe being destroyed by a Streamlit rerun.
  // But the callback closes over functions defined inside THIS iframe.
  // If we just kept the original interval, its closures would become
  // dead the moment the iframe was thrown away. Instead, clear any
  // previous interval and install a new one with the current iframe's
  // (live) closures. We also keep direct ``input``/``paste`` listeners
  // on the textarea so updates fire instantly without waiting for the
  // next poll tick.
  if (win.__bioseqLivePreviewIntervalId) {
    try { win.clearInterval(win.__bioseqLivePreviewIntervalId); } catch (e) {}
  }
  let lastValue = null;
  let lastWidth = 0;
  let lastHeight = 0;
  function syncFromInput(value) {
    renderPreview(value);
    renderAutocomplete(value);
    renderInputHighlight(value);
  }
  // Cheaper sync that only re-positions the mirror over the textarea —
  // used when the textarea resizes (e.g. Streamlit's autosize fires
  // after a paste) but the *text* hasn't changed, so we don't need to
  // re-tokenise the @mentions.
  function syncMirrorBoxOnly() {
    const ctx = ensureMirror();
    if (ctx) syncMirrorBox(ctx.input, ctx.mirror);
  }
  function pollTick() {
    try {
      const el = findChatInput();
      if (!el) return;
      const value = readValue(el);
      const rect = el.getBoundingClientRect();
      const sizeChanged = rect.width !== lastWidth || rect.height !== lastHeight;
      const textChanged = value !== lastValue;
      if (textChanged) {
        lastValue = value;
        lastWidth = rect.width;
        lastHeight = rect.height;
        syncFromInput(value);
      } else if (sizeChanged) {
        // Common case: paste enlarged the textarea via Streamlit's
        // autosize, but our paste handler already pushed lastValue ahead
        // of the resize. Without this branch the mirror would stay
        // clipped to the pre-paste height until the next keystroke.
        lastWidth = rect.width;
        lastHeight = rect.height;
        syncMirrorBoxOnly();
      }
    } catch (e) {
      // Iframe is mid-teardown; the next rerun will install a fresh interval.
    }
  }
  win.__bioseqLivePreviewIntervalId = win.setInterval(pollTick, 120);

  // Direct event listeners for instant feedback (poll is the backup).
  function attachDirectListeners() {
    const el = findChatInput();
    if (!el) return false;
    if (el.__bioseqDirectListenersAttached) return true;
    el.__bioseqDirectListenersAttached = true;
    const handler = function () {
      const value = readValue(el);
      if (value !== lastValue) {
        lastValue = value;
        syncFromInput(value);
      }
    };
    el.addEventListener('input', handler);
    el.addEventListener('keyup', handler);
    // Paste/cut: read the new value after the browser has applied the
    // edit, then schedule a few extra mirror-box syncs to catch
    // Streamlit's textarea autosize, which runs *after* the input event
    // and grows the textarea over the next few frames. Without these
    // follow-ups the mirror stays sized to the pre-paste height and
    // clips the second line onward.
    el.addEventListener('paste', function () {
      win.setTimeout(handler, 0);
      win.setTimeout(syncMirrorBoxOnly, 16);
      win.setTimeout(syncMirrorBoxOnly, 80);
      win.setTimeout(syncMirrorBoxOnly, 240);
    });
    el.addEventListener('cut', function () {
      win.setTimeout(handler, 0);
      win.setTimeout(syncMirrorBoxOnly, 16);
      win.setTimeout(syncMirrorBoxOnly, 80);
    });
    el.addEventListener('scroll', syncMirrorBoxOnly);

    // ResizeObserver picks up Streamlit's autosize the moment it runs,
    // which is what fully eliminates the "paste then it's clipped until
    // you type" symptom. The 120ms poll is the slow fallback for
    // browsers/Streamlit builds where the observer never fires.
    if (typeof win.ResizeObserver === 'function') {
      try {
        const ro = new win.ResizeObserver(function () {
          const rect = el.getBoundingClientRect();
          lastWidth = rect.width;
          lastHeight = rect.height;
          syncMirrorBoxOnly();
        });
        ro.observe(el);
      } catch (e) {}
    }
    return true;
  }
  attachDirectListeners();
  // Prime the mirror once on first render so it appears even before the
  // user types (textarea text is transparent, so without this the empty
  // state would just look like a bare caret).
  renderInputHighlight(readValue(findChatInput()));

  // ---- Install interaction handlers ONCE per page ----
  if (!win.__bioseqInteractionsInstalled) {
    win.__bioseqInteractionsInstalled = true;

    // Chip insert-button: paste @label into the chat input. Capture +
    // stopImmediatePropagation so the click doesn't bubble up to the
    // chip anchor (which would otherwise trigger object selection).
    doc.addEventListener('click', function (event) {
      const btn = event.target.closest && event.target.closest('.bioseq-chip-insert');
      if (!btn) return;
      event.preventDefault();
      event.stopPropagation();
      event.stopImmediatePropagation();
      const label = btn.getAttribute('data-label');
      if (!label) return;
      focusInput();
      insertAtCaret('@' + label + ' ');
    }, true);

    // Autocomplete item click — replace the trailing partial mention.
    doc.addEventListener('click', function (event) {
      const item = event.target.closest && event.target.closest('.bioseq-autocomplete-item');
      if (!item) return;
      event.preventDefault();
      event.stopPropagation();
      const label = item.getAttribute('data-label');
      if (label) replaceLastMention(label);
    }, true);

    // Drag-and-drop: chips can be dropped onto the chat input.
    doc.addEventListener('dragstart', function (event) {
      const chip = event.target.closest && event.target.closest('a.bioseq-chip[data-object-id]');
      if (!chip) return;
      const label = (chip.getAttribute('data-label') || '').trim();
      if (!label) return;
      try {
        event.dataTransfer.setData('text/plain', '@' + label + ' ');
        event.dataTransfer.effectAllowed = 'copy';
      } catch (e) {}
      chip.classList.add('is-dragging');
    });

    doc.addEventListener('dragend', function () {
      doc.querySelectorAll('a.bioseq-chip.is-dragging').forEach(function (c) {
        c.classList.remove('is-dragging');
      });
      const ta = findChatInput();
      if (ta) ta.classList.remove('bioseq-drop-target');
    });

    function isOverInput(target) {
      if (!target || !target.closest) return false;
      return !!target.closest('[data-testid="stChatInput"]');
    }

    doc.addEventListener('dragover', function (event) {
      if (!isOverInput(event.target)) return;
      event.preventDefault();
      try { event.dataTransfer.dropEffect = 'copy'; } catch (e) {}
      const ta = findChatInput();
      if (ta) ta.classList.add('bioseq-drop-target');
    });

    doc.addEventListener('dragleave', function (event) {
      if (isOverInput(event.target)) return;
      const ta = findChatInput();
      if (ta) ta.classList.remove('bioseq-drop-target');
    });

    doc.addEventListener('drop', function (event) {
      if (!isOverInput(event.target)) return;
      event.preventDefault();
      const ta = findChatInput();
      if (ta) ta.classList.remove('bioseq-drop-target');
      let text = '';
      try { text = event.dataTransfer.getData('text/plain') || ''; } catch (e) {}
      if (!text) return;
      focusInput();
      insertAtCaret(text);
    });
  }

  // Immediate render so the slot reflects current input even before the
  // first poll tick fires.
  try {
    const initial = readValue(findChatInput());
    syncFromInput(initial);
  } catch (e) {}
})();
</script>
"""


def _inject_live_preview_js() -> None:
    import streamlit.components.v1 as components

    components.html(_LIVE_PREVIEW_JS, height=0, width=0)
