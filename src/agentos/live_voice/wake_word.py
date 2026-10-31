"""Multi-accent Wake Word engine for "Orin" (and optional custom companion names).

Combines:
1. Vocabulary prompt biasing (`DEFAULT_WAKE_PROMPT_HINT`) for Whisper/OpenAI/Groq decoders.
2. Multi-accent phonetic normalization (`phonetic_skeleton_ptbr`) covering Brazilian regional
   accents (retroflex/caipira/paulista R, aspirated carioca/nordestino R, alveolar tap/trill
   sulista R, mineiro vowel reduction `o -> u`, diphthongized onset `ou/au/ho`, nasal coda
   `-in/-im/-en/-em/-yn/-ing/-inho`) and English/international pronunciations.
3. Curated STT confusion matrix & strict collision-stopword guard (`STOPWORDS_COLLISION`) so
   everyday Portuguese words (`olha`, `hora`, `agora`, `ordem`, `onde`, `outro`...) never
   false-trigger the wake word.
"""
from __future__ import annotations

import re
import unicodedata
from collections.abc import Sequence
from dataclasses import dataclass

DEFAULT_WAKE_PROMPT_HINT = "Assistente Orin. Oi Orin."

# Multi-word and single-word vocative greetings that commonly precede the wake word.
WAKE_GREETING_MULTIWORD: tuple[str, ...] = (
    "bom dia",
    "boa tarde",
    "boa noite",
    "e ai",
    "fala ai",
    "oi oi",
    "ei ei",
)

WAKE_GREETING_SINGLE: frozenset[str] = frozenset(
    {
        "oi",
        "ola",
        "ei",
        "hey",
        "hi",
        "fala",
        "o",
        "oh",
        "ok",
        "okay",
        "ai",
        "escuta",
        "ouve",
        "acorda",
        "bora",
        "yo",
    }
)

# Exact 1-token normalized variants produced by Whisper / Cloud / Browser STT across accents.
ORIN_SINGLE_TOKEN_VARIANTS: frozenset[str] = frozenset(
    {
        "orin",
        "orinz",
        "orins",
        "orim",
        "orimz",
        "orims",
        "oryn",
        "orinn",
        "orrin",
        "oreen",
        "orinho",
        "orinzinho",
        "ourin",
        "ourim",
        "aurin",
        "aurim",
        "aoryn",
        "horin",
        "horim",
        "horyn",
        "orem",
        "oren",
        "oran",
        "oring",
        "ori",
        "orion",
        "urin",
        "urim",
        "uri",
        "olin",
        "olim",
        "olhin",
        "odin",
        "ronin",
        "arin",
        "erin",
    }
)

# 2-token split transcriptions produced when STT splits the initial vowel and retroflex/aspirated R.
ORIN_TWO_TOKEN_VARIANTS: frozenset[str] = frozenset(
    {
        "o rin",
        "oh rin",
        "o rim",
        "oh rim",
        "all in",
        "hall in",
        "aw in",
        "or in",
    }
)

# Everyday Portuguese words that start with `o-`/`or-`/`ol-`/`ag-` and must NEVER trigger the wake word.
STOPWORDS_COLLISION: frozenset[str] = frozenset(
    {
        "hora",
        "horas",
        "horario",
        "horarios",
        "agora",
        "olha",
        "olhe",
        "olho",
        "olhos",
        "olhar",
        "olhei",
        "olhando",
        "ouro",
        "ouros",
        "outro",
        "outra",
        "outros",
        "outras",
        "onde",
        "ontem",
        "ordem",
        "ordens",
        "origem",
        "origens",
        "original",
        "originais",
        "orientar",
        "orientacao",
        "orcamento",
        "orcamentos",
        "orelha",
        "orelhas",
        "orgulho",
        "orquestra",
        "orla",
        "ornamento",
        "oracao",
        "oracoes",
        "orfao",
        "orgao",
        "orgaos",
        "orvalho",
        "orquidea",
        "ola",
        "oi",
        "ok",
        "okay",
        "ou",
        "ora",
        "oras",
    }
)


@dataclass(frozen=True, slots=True)
class WakeWordMatch:
    detected: bool
    transcript: str
    wake_phrase: str = ""
    command_remainder: str = ""
    confidence: float = 0.0
    matched_variant: str = ""

    def to_dict(self) -> dict[str, object]:
        return {
            "detected": self.detected,
            "matched": self.detected,
            "transcript": self.transcript,
            "wake_phrase": self.wake_phrase,
            "command_remainder": self.command_remainder,
            "confidence": round(float(self.confidence), 3),
            "matched_variant": self.matched_variant,
            "wake_variant": self.matched_variant,
        }


def _strip_accents(text: str) -> str:
    norm = unicodedata.normalize("NFD", text.lower())
    return "".join(ch for ch in norm if unicodedata.category(ch) != "Mn")


def build_wake_prompt_hint(custom_names: Sequence[str] | None = None) -> str:
    """Build a Whisper/STT `initial_prompt` biasing towards 'Orin' and any custom companion names."""
    extras: list[str] = []
    for raw in custom_names or ():
        clean = str(raw or "").strip()
        if clean and clean.lower() != "orin" and clean.lower() != "orin friend" and len(clean) <= 40:
            extras.append(clean)
    if not extras:
        return DEFAULT_WAKE_PROMPT_HINT
    joined_extras = ", ".join(f"{name}, Oi {name}" for name in extras[:3])
    return f"{DEFAULT_WAKE_PROMPT_HINT} {joined_extras}."[:400]


def phonetic_skeleton_ptbr(word: str) -> str:
    """Convert a single word into an accent-invariant PT-BR/EN phonetic skeleton.

    Canonical target for 'Orin' across regional accents is `'ORN'`.
    Requires initial vowel after optional silent 'h' to be 'o', 'u', or 'au'/'ou'/'ao'
    (never bare 'a' or 'e'), a front/high vowel ('i', 'e', 'y') between 'r/l' and 'n/m',
    and no trailing vowel after 'n/m' unless it is a valid diminutive ('-inho'/'-zinho').
    """
    w = re.sub(r"[^a-z]", "", _strip_accents(word))
    if not w:
        return ""
    w = re.sub(r"^h+", "", w)
    if not w:
        return ""
    if w.endswith("zinho") and len(w) > 6:
        w = w[:-5]
    elif (w.endswith("inho") or w.endswith("inha")) and len(w) > 5:
        w = w[:-4] + "in"

    w = re.sub(r"(?<=[nm])[sz]+$", "", w)
    w = re.sub(r"(.)\1+", r"\1", w)

    for prefix in ("our", "aur", "aor", "or", "ur", "olh", "ol"):
        if w.startswith(prefix):
            w = "or" + w[len(prefix) :]
            break

    for suffix in ("ing", "inh", "ion", "yn", "im", "in", "em", "en"):
        if w.endswith(suffix) and len(w) > len(suffix) + 1:
            w = w[: -len(suffix)] + "in"
            break

    if w in {"orin", "ori"}:
        return "ORN"
    return w.upper()


@dataclass(frozen=True, slots=True)
class _TokenSpan:
    norm: str
    start: int
    end: int


def _tokenize_with_spans(raw: str) -> list[_TokenSpan]:
    # Words include letters and digits; hyphens inside compound names are split into tokens.
    spans: list[_TokenSpan] = []
    for m in re.finditer(r"[\wÀ-ÿ]+", raw, flags=re.UNICODE):
        token_raw = m.group(0)
        norm = _strip_accents(token_raw)
        if norm:
            spans.append(_TokenSpan(norm=norm, start=m.start(), end=m.end()))
    return spans


def _clean_remainder(raw: str, cut_index: int, custom_names: Sequence[str] | None = None) -> str:
    tail = raw[cut_index:]
    # Strip leading punctuation/separators after the wake phrase (",", "!", ":", ".", "-", "—", "?")
    cleaned = re.sub(r"^[\s,.;:!?—–\-]+", "", tail).strip()
    # If Whisper echoed the wake phrase twice (e.g. "Ei Orin, ei Orin." or "Orin, ô Orin."), strip the echo.
    for _ in range(2):
        if not cleaned:
            break
        nested = _match_once(cleaned, custom_names=custom_names)
        if not nested.detected:
            break
        cleaned = nested.command_remainder
    # If the remainder is only a trailing punctuation mark or single greeting word ("oi.", "ei."), drop it.
    norm_rem = _strip_accents(re.sub(r"[^\wÀ-ÿ\s]", "", cleaned)).strip()
    if norm_rem in WAKE_GREETING_SINGLE or norm_rem in WAKE_GREETING_MULTIWORD:
        return ""
    return cleaned


def _match_custom_name_tokens(
    tokens: list[_TokenSpan],
    start_idx: int,
    custom_names: Sequence[str],
) -> tuple[int, str, float] | None:
    for cname in custom_names:
        c_tokens = [t.norm for t in _tokenize_with_spans(cname)]
        if not c_tokens:
            continue
        n = len(c_tokens)
        if start_idx + n <= len(tokens):
            window = [tokens[start_idx + i].norm for i in range(n)]
            if window == c_tokens:
                return n, " ".join(c_tokens), 0.98
    return None


def _match_once(
    transcript: str,
    *,
    custom_names: Sequence[str] | None = None,
) -> WakeWordMatch:
    raw = (transcript or "").strip()
    if not raw:
        return WakeWordMatch(detected=False, transcript="")

    tokens = _tokenize_with_spans(raw)
    if not tokens:
        return WakeWordMatch(detected=False, transcript=raw)

    # 1. Strip optional leading vocative greeting(s) at the start of the utterance.
    idx = 0
    while idx < len(tokens):
        if idx + 1 < len(tokens):
            two = f"{tokens[idx].norm} {tokens[idx + 1].norm}"
            if two in WAKE_GREETING_MULTIWORD:
                idx += 2
                continue
        # Only strip single-word "o" / "oh" as a greeting if it is NOT part of a 2-token wake variant ("o rin", "oh rin")
        if tokens[idx].norm in {"o", "oh"} and idx + 1 < len(tokens):
            pair = f"{tokens[idx].norm} {tokens[idx + 1].norm}"
            if pair in ORIN_TWO_TOKEN_VARIANTS:
                break
        if tokens[idx].norm in WAKE_GREETING_SINGLE:
            idx += 1
            continue
        break

    if idx >= len(tokens):
        return WakeWordMatch(detected=False, transcript=raw)

    # 2. Check custom companion names first if provided
    if custom_names:
        custom_hit = _match_custom_name_tokens(tokens, idx, custom_names)
        if custom_hit is not None:
            consumed, matched_var, conf = custom_hit
            end_span = tokens[idx + consumed - 1].end
            wake_phrase = raw[:end_span].strip(" ,.;:!?—–-")
            remainder = re.sub(r"^[\s,.;:!?—–\-]+", "", raw[end_span:]).strip()
            return WakeWordMatch(
                detected=True,
                transcript=raw,
                wake_phrase=wake_phrase,
                command_remainder=remainder,
                confidence=conf,
                matched_variant=matched_var,
            )

    # 3. Check 2-token split variants ("o rin", "oh rin", "all in", "hall in")
    if idx + 1 < len(tokens):
        pair = f"{tokens[idx].norm} {tokens[idx + 1].norm}"
        if pair in ORIN_TWO_TOKEN_VARIANTS:
            end_span = tokens[idx + 1].end
            wake_phrase = raw[:end_span].strip(" ,.;:!?—–-")
            remainder = re.sub(r"^[\s,.;:!?—–\-]+", "", raw[end_span:]).strip()
            return WakeWordMatch(
                detected=True,
                transcript=raw,
                wake_phrase=wake_phrase,
                command_remainder=remainder,
                confidence=0.90,
                matched_variant=pair,
            )

    # 4. Check single-token candidate at `tokens[idx]`
    candidate = tokens[idx].norm
    if candidate in STOPWORDS_COLLISION:
        return WakeWordMatch(detected=False, transcript=raw)

    confidence = 0.0
    if candidate == "orin":
        confidence = 0.99
    elif candidate in ORIN_SINGLE_TOKEN_VARIANTS:
        confidence = 0.92
    else:
        # Phonetic skeleton check for unseen spelling variations of the same accent phonemes
        skel = phonetic_skeleton_ptbr(candidate)
        if skel == "ORN" and 3 <= len(candidate) <= 8:
            confidence = 0.86

    if confidence >= 0.80:
        end_span = tokens[idx].end
        wake_phrase = raw[:end_span].strip(" ,.;:!?—–-")
        remainder = re.sub(r"^[\s,.;:!?—–\-]+", "", raw[end_span:]).strip()
        return WakeWordMatch(
            detected=True,
            transcript=raw,
            wake_phrase=wake_phrase,
            command_remainder=remainder,
            confidence=confidence,
            matched_variant=candidate,
        )

    return WakeWordMatch(detected=False, transcript=raw)


def match_wake_word(
    transcript: str,
    *,
    custom_names: Sequence[str] | None = None,
) -> WakeWordMatch:
    """Evaluate whether `transcript` starts with the wake word 'Orin' (or a custom name),
    tolerating regional PT-BR and international accents while rejecting everyday collisions.
    """
    first = _match_once(transcript, custom_names=custom_names)
    if not first.detected or not first.command_remainder:
        return first
    cleaned_rem = _clean_remainder(first.command_remainder, 0, custom_names=custom_names)
    if cleaned_rem == first.command_remainder:
        return first
    return WakeWordMatch(
        detected=first.detected,
        transcript=first.transcript,
        wake_phrase=first.wake_phrase,
        command_remainder=cleaned_rem,
        confidence=first.confidence,
        matched_variant=first.matched_variant,
    )


__all__ = [
    "DEFAULT_WAKE_PROMPT_HINT",
    "ORIN_SINGLE_TOKEN_VARIANTS",
    "ORIN_TWO_TOKEN_VARIANTS",
    "STOPWORDS_COLLISION",
    "WakeWordMatch",
    "build_wake_prompt_hint",
    "match_wake_word",
    "phonetic_skeleton_ptbr",
]
