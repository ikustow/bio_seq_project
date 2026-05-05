"""Scripted conversation engine for the BioSeq mock UI.

Keeps the UI deterministic — every user message is routed to the next expected
turn by simple keyword heuristics, and each turn declares which card sections
it reveals.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

EXAMPLE_SEQUENCE = (
    "MRKGLRATAARCGLGLGYLLQMLVLPALALLSASGTGSAAQDDDFFHELPETFPSDPPEPLPHFLIEPEEA"
    "YIVKNKPVNLYCKASPATQIYFKCNSEWVHQKDHIVDERVDETSGLIVREVSIEISRQQVEELFGPEDYW"
    "CQCVAWSSAGTTKSRKAYVRIAYLRKTFEQEPLGKEVSLEQEVLLQCRPPEGIPVAEVEWLKNEDIIDPV"
    "EDRNFYITIDHNLIIKQARLSDTANYTCVAKNIVAKRKSTTATVIVYVNGGWSTWTEWSVCNSRCGRGYQ"
    "KRTRTCTNPAPLNGGAFCEGQSVQKIACTTLCPVDGRWTPWSKWSTCGTECTHWRRRECTAPAPKNGGKD"
    "CDGLVLQSKNCTDGL"
)
EXAMPLE_QUESTION = "what is the best match for species Human?"

WELCOME_MESSAGE = (
    "Hi! I'm **BioSeq Investigator** — paste a DNA or protein FASTA sequence "
    "and ask me a question about it. I'll search public bioinformatics "
    "databases and show you an evidence-grounded answer.\n\n"
    "No sequence handy? Click **Try example** below to see how it works."
)


@dataclass(frozen=True)
class Turn:
    id: str
    keywords: tuple[str, ...]
    assistant: str
    reveals: tuple[str, ...] = ()


# Turn 1 — the initial search. Triggered by any substantial first user message.
_TURN_1 = Turn(
    id="search",
    keywords=(),  # matched positionally as the first real turn
    assistant=(
        "Great — I'll start searching the databases for this sequence…\n\n"
        "**Best match:** Netrin receptor UNC5C (*Protein unc-5 homolog C*), "
        "`UniProt: O95185` · *Homo sapiens*.\n\n"
        "**Function:** Receptor for netrin required for axon guidance. "
        "Mediates axon repulsion of neuronal growth cones in the developing "
        "nervous system upon ligand binding. NTN1/Netrin-1 binding might cause "
        "dissociation of UNC5C from polymerized TUBB3 in microtubules and "
        "thereby lead to increased microtubule dynamics and axon repulsion "
        "[PubMed:28483977]. It also acts as a dependence receptor required "
        "for apoptosis induction when not associated with netrin ligand.\n\n"
        "Do you want me to explain this in simpler language?"
    ),
    reveals=("header", "keyfacts", "function", "structure", "keywords"),
)

# Turn 2 — the plain-language explainer.
_TURN_2 = Turn(
    id="explain_simple",
    keywords=("yes", "simpler", "easier", "simple", "explain", "plain"),
    assistant=(
        "Sure — here is UNC5C in plain language:\n\n"
        "Think of it as a **\"brick\"** and a **\"No Entry\" sign** for growing nerves.\n\n"
        "- **No signal (no netrin) →** the protein tells the cell *\"you can't build "
        "here, get lost\"*. The cell triggers programmed death.\n"
        "- **Signal (netrin present) →** the protein tells the growing nerve "
        "*\"turn around\"*. The nerve changes direction instead of dying.\n\n"
        "**Bottom line:** it helps the nervous system build the right \"roads\" "
        "and remove the wrong ones.\n\n"
        "Do you have any extra questions regarding this protein?"
    ),
    reveals=("domains",),
)

# Turn 3 — diseases.
_TURN_3 = Turn(
    id="diseases",
    keywords=("disease", "diseases", "pathogen", "pathology", "illness", "condition"),
    assistant=(
        "Yes — UNC5C is linked to **Alzheimer disease**. "
        "There are several peer-reviewed publications supporting this association "
        "[PubMed:25419706] [PubMed:27068745].\n\n"
        "Do you want me to share more details about the disease?"
    ),
    reveals=("disease",),
)

# Turn 4 — disease deep-dive.
_TURN_4 = Turn(
    id="disease_details",
    keywords=("yes", "tell", "more", "detail", "share", "resource", "describe"),
    assistant=(
        "**Alzheimer disease** is a neurodegenerative disorder characterised by "
        "progressive dementia, loss of cognitive abilities, and deposition of "
        "fibrillar amyloid proteins as intraneuronal neurofibrillary tangles, "
        "extracellular amyloid plaques and vascular amyloid deposits. The major "
        "constituents of these plaques are neurotoxic amyloid-beta protein 40 "
        "and amyloid-beta protein 42, produced by the proteolysis of the "
        "transmembrane APP protein. The cytotoxic C-terminal fragments (CTFs) "
        "and the caspase-cleaved products, such as C31, are also implicated in "
        "neuronal death [PubMed:25419706] [PubMed:27068745].\n\n"
        "Note: disease susceptibility may be associated with variants affecting "
        "the UNC5C gene — see the variant list in the card on the right."
    ),
    reveals=("references",),
)

TURNS: list[Turn] = [_TURN_1, _TURN_2, _TURN_3, _TURN_4]

_NEGATIVE_TOKENS = ("no", "not", "don't", "dont", "nope")


@dataclass
class ConversationState:
    step: int = 0
    revealed: set[str] = field(default_factory=set)


def _contains_any(text: str, tokens: tuple[str, ...]) -> bool:
    t = text.lower()
    return any(k in t for k in tokens)


def _is_example_like(text: str) -> bool:
    """A pasted FASTA-ish message — long and mostly uppercase letters."""
    stripped = text.strip()
    if len(stripped) < 40:
        return False
    letters = [c for c in stripped if c.isalpha()]
    if not letters:
        return False
    upper_ratio = sum(c.isupper() for c in letters) / len(letters)
    return upper_ratio > 0.7


def route(user_text: str, state: ConversationState) -> tuple[str, tuple[str, ...]]:
    """Return (assistant_reply, sections_to_reveal) for the given user message.

    Advances `state.step` on match. Non-matching input returns a demo-mode
    notice and leaves state untouched.
    """
    text = (user_text or "").strip()

    # First turn is always the search, regardless of wording — the user is
    # expected to paste a sequence + question.
    if state.step == 0:
        turn = _TURN_1
        state.step = 1
        state.revealed.update(turn.reveals)
        return turn.assistant, turn.reveals

    # Subsequent turns: try in order, starting from current step.
    for idx in range(state.step, len(TURNS)):
        turn = TURNS[idx]
        if not turn.keywords:
            continue
        if _contains_any(text, turn.keywords):
            # Turn 4 accepts "no, tell me more" — negatives are fine there.
            # Turn 2 requires an affirmative — reject bare negations.
            if idx == 1 and _contains_any(text, _NEGATIVE_TOKENS) and not _contains_any(
                text, ("yes", "please", "sure", "ok", "okay")
            ):
                continue
            state.step = idx + 1
            state.revealed.update(turn.reveals)
            return turn.assistant, turn.reveals

    return (
        "I'm in **demo mode** right now, so I can only follow the scripted "
        "conversation. Try asking about the protein's **diseases**, request a "
        "**simpler explanation**, or click **Reset** to start over.",
        (),
    )


def example_first_message() -> str:
    return f"Sequence:\n{EXAMPLE_SEQUENCE}\n\nQuestion: {EXAMPLE_QUESTION}"


def fasta_detected(text: str) -> bool:
    return _is_example_like(text)


def welcome() -> str:
    return WELCOME_MESSAGE
