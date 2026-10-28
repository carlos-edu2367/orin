from __future__ import annotations

import re
from dataclasses import dataclass, field
from enum import StrEnum


class BoundaryKind(StrEnum):
    COMMA = "comma"
    PERIOD = "period"
    EXCLAMATION = "exclamation"
    QUESTION = "question"
    SEMICOLON = "semicolon"
    COLON = "colon"
    ELLIPSIS = "ellipsis"
    EM_DASH = "em_dash"
    SOFT_SPLIT = "soft_split"
    FLUSH = "flush"


class VocalMarker(str):
    """String representing an inline vocal marker (e.g. '[hmm]') that compares equal
    to both bracketed ('[hmm]') and unbracketed ('hmm') forms."""

    @property
    def marker(self) -> str:
        return self.strip("[] \t\r\n").lower()

    def __eq__(self, other: object) -> bool:
        if isinstance(other, str):
            return self.strip("[] \t\r\n").lower() == other.strip("[] \t\r\n").lower()
        return super().__eq__(other)

    def __hash__(self) -> int:
        return hash(self.strip("[] \t\r\n").lower())


KNOWN_VOCAL_MARKERS: frozenset[str] = frozenset(
    {
        "hmm",
        "uh-huh",
        "aham",
        "sigh",
        "suspiro",
        "breath",
        "laugh",
        "chuckle",
        "riso",
        "risos",
        "riso leve",
        "gasp",
        "pausa",
        "pause",
        "pausa curta",
        "pausa longa",
        "uhm",
        "ahn",
        "clear_throat",
        "tosse",
        "pigam",
        "expira",
        "inspira",
    }
)

STAGE_DIRECTION_WORDS: frozenset[str] = frozenset(
    {
        "sorriso",
        "silêncio",
        "silencio",
        "calmo",
        "animado",
        "suave",
        "sério",
        "serio",
        "irônico",
        "ironico",
        "baixo",
        "baixinho",
        "alto",
        "enfático",
        "enfatico",
        "gentil",
        "empolgado",
        "curioso",
        "pensativo",
    }
)

STANDALONE_GREETING_OPENERS: frozenset[str] = frozenset(
    {
        "olá",
        "ola",
        "oi",
        "claro",
        "pronto",
        "opa",
        "alô",
        "alo",
        "perfeito",
        "certo",
        "beleza",
        "entendi",
        "sim",
        "não",
        "nao",
        "bom",
        "olha",
        "veja",
        "fala",
        "ei",
    }
)


def _is_stage_direction(raw_inner: str) -> bool:
    clean = raw_inner.strip()
    if not clean:
        return True
    if " " in clean:
        return True
    lower = clean.lower()
    if lower in STAGE_DIRECTION_WORDS:
        return True
    if len(lower) >= 5 and lower.endswith(("ando", "endo", "indo", "ondo")):
        return True
    return False


@dataclass(frozen=True, slots=True)
class ProsodicChunk:
    chunk_index: int
    clean_text: str
    tts_text: str
    boundary_kind: BoundaryKind
    is_first_in_block: bool = False
    is_last_in_block: bool = False
    block_index: int = 0
    raw_tts_text: str = ""
    normalized_text: str = ""
    inline_markers: tuple[str, ...] = ()


@dataclass(slots=True)
class SpeakParseResult:
    visual_delta: str = ""
    chunks: list[ProsodicChunk] = field(default_factory=list)
    inline_markers: list[str] = field(default_factory=list)
    speak_opened: bool = False
    speak_closed: bool = False
    speak_delta: str = ""
    used_zero_speak_fallback: bool = False


_FENCED_CODE_RE = re.compile(r"```[\s\S]*?```", re.MULTILINE)
_UNCLOSED_FENCED_CODE_RE = re.compile(r"```[\s\S]*$", re.MULTILINE)
_INLINE_CODE_LONG_RE = re.compile(r"`([^`\n]{28,})`")
_INLINE_CODE_SHORT_RE = re.compile(r"`([^`\n]+)`")
_URL_RE = re.compile(r"(https?://[^\s<>)\]]+|www\.[^\s<>)\]]+)", re.IGNORECASE)
_RAW_JSON_BLOCK_RE = re.compile(r"\{[^{}]*\"[^\"]+\"\s*:[^{}]*\}")
_HTML_TAG_RE = re.compile(r"</?[a-zA-Z][a-zA-Z0-9_-]*(?:\s[^>]*)?>")
_MULTI_SPACE_RE = re.compile(r"[ \t]{2,}")


def _replace_url_preserving_trailing_punct(match: re.Match[str]) -> str:
    raw_url = match.group(0)
    trailing = ""
    while raw_url and raw_url[-1] in ".,!?;:":
        trailing = raw_url[-1] + trailing
        raw_url = raw_url[:-1]
    return f" o link na tela{trailing} "


def strip_non_speakable_content(text: str, max_chars: int = 600) -> str:
    """Sanitizador Red-Team Pré-TTS:
    - Substitui blocos de código cercados ```...``` por ' deixei o código na tela '.
    - Substitui URLs por ' o link na tela '.
    - Remove JSON bruto e tags XML/HTML residuais.
    - Limita tamanho máximo por bloco.
    """
    if not text:
        return ""

    cleaned = _FENCED_CODE_RE.sub(" deixei o código na tela ", text)
    cleaned = _UNCLOSED_FENCED_CODE_RE.sub(" deixei o código na tela ", cleaned)
    cleaned = _INLINE_CODE_LONG_RE.sub(" deixei o código na tela ", cleaned)
    cleaned = _INLINE_CODE_SHORT_RE.sub(r"\1", cleaned)
    cleaned = _URL_RE.sub(_replace_url_preserving_trailing_punct, cleaned)
    cleaned = _RAW_JSON_BLOCK_RE.sub(" ", cleaned)
    cleaned = _HTML_TAG_RE.sub(" ", cleaned)
    cleaned = _MULTI_SPACE_RE.sub(" ", cleaned)
    cleaned = re.sub(r"\s+([.,!?;:])", r"\1", cleaned)
    cleaned = cleaned.strip()

    if max_chars > 0 and len(cleaned) > max_chars:
        truncated = cleaned[:max_chars]
        last_space = truncated.rfind(" ")
        if last_space > max_chars // 2:
            truncated = truncated[:last_space].rstrip(".,;:") + "."
        cleaned = truncated

    return cleaned


class PTBRTextNormalizer:
    """Normalizador prosódico PT-BR para preparação de texto enviado ao TTS."""

    ABBREVIATION_MAP: dict[str, str] = {
        "sr.": "senhor",
        "sra.": "senhora",
        "srta.": "senhorita",
        "dr.": "doutor",
        "dra.": "doutora",
        "prof.": "professor",
        "profa.": "professora",
        "eng.": "engenheiro",
        "av.": "avenida",
        "etc.": "etcétera",
        "ex.": "exemplo",
        "obs.": "observação",
        "ltda.": "limitada",
        "pág.": "página",
        "págs.": "páginas",
        "aprox.": "aproximadamente",
        "tel.": "telefone",
        "cel.": "celular",
        "e.u.a.": "Estados Unidos",
    }

    TECH_ACRONYMS: dict[str, str] = {
        "APIs": "á pê is",
        "API": "á pê i",
        "URLs": "u érre éles",
        "URL": "u érre éle",
        "SQL": "ésse quê éle",
        "MCPs": "ême cê pês",
        "MCP": "ême cê pê",
        "LLMs": "éle éle êmes",
        "LLM": "éle éle ême",
        "TTS": "tê tê ésse",
        "STT": "ésse tê tê",
        "JSON": "jeison",
        "CPU": "cê pê u",
        "GPU": "gê pê u",
        "RAM": "rã",
    }

    _BRL_RE = re.compile(
        r"R\$\s*(\d{1,3}(?:\.\d{3})+|\d+)(?:,(\d{1,2}))?\b"
    )
    _USD_RE = re.compile(
        r"U?S\$\s*(\d{1,3}(?:\.\d{3})+|\d+)(?:,(\d{1,2}))?\b"
    )
    _EUR_RE = re.compile(
        r"€\s*(\d{1,3}(?:\.\d{3})+|\d+)(?:,(\d{1,2}))?\b"
    )
    _TIME_H_RE = re.compile(
        r"\b([01]?\d|2[0-3])h(?:([0-5]\d)(?:min)?)?\b", re.IGNORECASE
    )
    _TIME_COLON_RE = re.compile(
        r"\b([01]?\d|2[0-3]):([0-5]\d)\b"
    )
    _PERCENT_RE = re.compile(
        r"\b(\d{1,3}(?:\.\d{3})+|\d+)(?:,(\d+))?\s*%"
    )
    _THOUSANDS_DECIMAL_RE = re.compile(
        r"(?<![vV\d.])\b(\d{1,3}(?:\.\d{3})+),(\d+)\b"
    )
    _THOUSANDS_ONLY_RE = re.compile(
        r"(?<![vV\d.])\b(\d{1,3}(?:\.\d{3})+)\b(?!\.\d)"
    )
    _DECIMAL_RE = re.compile(
        r"\b(\d+),(\d+)\b"
    )

    @classmethod
    def _format_currency(
        cls,
        int_raw: str,
        dec_raw: str | None,
        singular: str,
        plural: str,
    ) -> str:
        val = int(int_raw.replace(".", ""))
        cents = int(dec_raw.ljust(2, "0")[:2]) if dec_raw else 0
        parts: list[str] = []
        if val > 0 or cents == 0:
            unit = singular if val == 1 else plural
            parts.append(f"{val} {unit}")
        if cents > 0:
            c_unit = "centavo" if cents == 1 else "centavos"
            parts.append(f"{cents} {c_unit}")
        return " e ".join(parts)

    @classmethod
    def _format_time(cls, hour_str: str, min_str: str | None) -> str:
        hour = int(hour_str)
        minute = int(min_str) if min_str else 0
        if hour == 1:
            h_part = "uma hora"
        elif hour == 2:
            h_part = "2 horas"
        else:
            h_part = f"{hour} horas"

        if minute == 0:
            return h_part
        m_unit = "minuto" if minute == 1 else "minutos"
        return f"{h_part} e {minute} {m_unit}"

    def normalize(self, text: str) -> str:
        if not text:
            return ""

        out = text

        # 1. Moedas (R$, US$, €)
        out = self._BRL_RE.sub(
            lambda m: self._format_currency(m.group(1), m.group(2), "real", "reais"),
            out,
        )
        out = self._USD_RE.sub(
            lambda m: self._format_currency(m.group(1), m.group(2), "dólar", "dólares"),
            out,
        )
        out = self._EUR_RE.sub(
            lambda m: self._format_currency(m.group(1), m.group(2), "euro", "euros"),
            out,
        )

        # 2. Horários (14h30, 1h, 14:30)
        out = self._TIME_H_RE.sub(
            lambda m: self._format_time(m.group(1), m.group(2)),
            out,
        )
        out = self._TIME_COLON_RE.sub(
            lambda m: self._format_time(m.group(1), m.group(2)),
            out,
        )

        # 3. Porcentagens (3,5% -> 3 vírgula 5 por cento)
        def _repl_percent(m: re.Match[str]) -> str:
            int_part = m.group(1).replace(".", "")
            dec_part = m.group(2)
            if dec_part:
                return f"{int_part} vírgula {dec_part} por cento"
            return f"{int_part} por cento"

        out = self._PERCENT_RE.sub(_repl_percent, out)

        # 4. Números com milhar e decimal
        out = self._THOUSANDS_DECIMAL_RE.sub(
            lambda m: f"{m.group(1).replace('.', '')} vírgula {m.group(2)}",
            out,
        )
        out = self._THOUSANDS_ONLY_RE.sub(
            lambda m: m.group(1).replace(".", ""),
            out,
        )
        out = self._DECIMAL_RE.sub(
            lambda m: f"{m.group(1)} vírgula {m.group(2)}",
            out,
        )

        # 5. Abreviações PT-BR (Sr., Dra., Av., etc., E.U.A.)
        # Preserva pontuação seguinte se houver
        for abbr, expanded in sorted(
            self.ABBREVIATION_MAP.items(), key=lambda kv: len(kv[0]), reverse=True
        ):
            pattern = re.compile(
                r"(?<!\w)" + re.escape(abbr),
                re.IGNORECASE,
            )
            out = pattern.sub(expanded, out)

        # 6. Siglas técnicas (API, URL, SQL, MCP, LLM, TTS, STT, JSON, CPU, GPU, RAM)
        for acronym, spoken in sorted(
            self.TECH_ACRONYMS.items(), key=lambda kv: len(kv[0]), reverse=True
        ):
            pattern = re.compile(r"\b" + re.escape(acronym) + r"\b")
            out = pattern.sub(spoken, out)

        out = _MULTI_SPACE_RE.sub(" ", out).strip()
        return out


class PTBRProsodicChunker:
    """Segmentador Prosódico PT-BR em Duas Marchas (Two-Gear Adaptive Chunking)."""

    PT_BR_ABBREVIATIONS: frozenset[str] = frozenset(
        {
            "sr.",
            "sra.",
            "srta.",
            "dr.",
            "dra.",
            "prof.",
            "profa.",
            "eng.",
            "av.",
            "r.",
            "al.",
            "pç.",
            "est.",
            "rod.",
            "etc.",
            "ex.",
            "obs.",
            "pág.",
            "págs.",
            "cap.",
            "fig.",
            "vol.",
            "núm.",
            "n.º",
            "ltda.",
            "s.a.",
            "cia.",
            "jr.",
            "pe.",
            "st.",
            "sta.",
            "sto.",
            "tel.",
            "cel.",
            "cep.",
            "cpf.",
            "cnpj.",
            "rg.",
            "min.",
            "máx.",
            "aprox.",
            "ref.",
            "cod.",
            "cód.",
            "depto.",
            "dep.",
            "sec.",
            "art.",
            "inc.",
            "par.",
            "fl.",
            "fls.",
            "e.u.a.",
            "o.n.u.",
            "i.a.",
        }
    )

    FORBIDDEN_CHUNK_ENDINGS: frozenset[str] = frozenset(
        {
            "o",
            "a",
            "os",
            "as",
            "um",
            "uma",
            "uns",
            "umas",
            "de",
            "do",
            "da",
            "dos",
            "das",
            "em",
            "no",
            "na",
            "nos",
            "nas",
            "num",
            "numa",
            "para",
            "pra",
            "pro",
            "pros",
            "pras",
            "com",
            "sem",
            "por",
            "pelo",
            "pela",
            "pelos",
            "pelas",
            "ao",
            "aos",
            "à",
            "às",
            "que",
            "se",
            "me",
            "te",
            "lhe",
            "nos",
            "vos",
            "e",
            "ou",
            "mas",
            "porque",
            "porquê",
            "pois",
            "como",
            "quando",
            "enquanto",
            "é",
            "são",
            "foi",
            "está",
            "estou",
            "estamos",
            "estão",
            "vou",
            "vai",
            "vamos",
            "vão",
            "não",
        }
    )

    NON_STANDALONE_OPENERS: frozenset[str] = frozenset(
        {
            "mas",
            "porém",
            "contudo",
            "entretanto",
            "todavia",
            "no entanto",
            "ou seja",
            "aliás",
            "ademais",
            "portanto",
            "assim",
            "então",
            "pois",
            "e",
            "ou",
            "porque",
            "como",
            "quando",
            "se",
            "embora",
            "ora",
            "enfim",
            "afinal",
        }
    )

    SOFT_SPLIT_CONJUNCTIONS: tuple[str, ...] = (
        " para que ",
        " já que ",
        " visto que ",
        " desde que ",
        " porque ",
        " enquanto ",
        " quando ",
        " embora ",
        " porém ",
        " contudo ",
        " portanto ",
        " mas ",
    )

    GEAR_1_SOFT_MAX_CHARS: int = 110
    GEAR_2_SOFT_MAX_CHARS: int = 160
    GEAR_1_MIN_CHARS: int = 28
    GEAR_1_MIN_WORDS: int = 5
    COMMA_MIN_CHARS: int = 48
    COMMA_MIN_WORDS: int = 8
    GEAR_2_COMMA_MIN_CHARS: int = 48
    GEAR_2_COMMA_MIN_WORDS: int = 8
    STRONG_MIN_CHARS: int = 24
    STRONG_MIN_WORDS: int = 4
    GEAR_2_STRONG_MIN_CHARS: int = 24

    def __init__(
        self,
        normalizer: PTBRTextNormalizer | None = None,
        *,
        block_index: int = 0,
    ) -> None:
        self.normalizer = normalizer or PTBRTextNormalizer()
        self.block_index = block_index
        self._buffer: str = ""
        self._chunk_index: int = 0
        self._first_chunk_words: int = 0
        self._pending_markers: list[str] = []

    def reset_for_new_block(self, block_index: int | None = None) -> None:
        if block_index is not None:
            self.block_index = block_index
        else:
            self.block_index += 1
        self._buffer = ""
        self._chunk_index = 0
        self._first_chunk_words = 0
        self._pending_markers.clear()

    def add_inline_marker(self, marker: str) -> None:
        self._pending_markers.append(VocalMarker(marker))

    def _has_unclosed_construct(self) -> bool:
        # Se houver número ímpar de ``` no buffer, estamos no meio de um bloco de código
        if self._buffer.count("```") % 2 == 1:
            return True
        # Se o buffer termina no meio de uma URL (sem espaço após http://...)
        if re.search(r"(?:https?://|www\.)\S*$", self._buffer, re.IGNORECASE):
            return True
        return False

    def _sanitize_completed_constructs_in_buffer(self) -> None:
        if "```" in self._buffer and self._buffer.count("```") >= 2:
            self._buffer = _FENCED_CODE_RE.sub(" deixei o código na tela ", self._buffer)
        # Substitui URLs que já foram seguidas por espaço
        self._buffer = re.sub(
            r"(https?://[^\s<>)\]]+|www\.[^\s<>)\]]+)(?=\s)",
            _replace_url_preserving_trailing_punct,
            self._buffer,
            flags=re.IGNORECASE,
        )

    @classmethod
    def _is_abbreviation_at(cls, text: str, dot_end: int) -> bool:
        """Verifica se o ponto terminando em `dot_end` pertence a uma abreviação PT-BR ou sigla."""
        prefix = text[:dot_end]
        m = re.search(r"([A-Za-zÀ-ÿ0-9.ºª]+)$", prefix)
        if not m:
            return False
        token = m.group(1).lower()
        if token in cls.PT_BR_ABBREVIATIONS:
            return True
        # Siglas com pontos como E.U.A. ou S.A.
        if re.match(r"^(?:[a-zà-ÿ]\.){2,}$", token):
            return True
        # Versão como v1. ou numeração de lista como 1. / 2.
        if re.match(r"^v\d+\.$", token) or re.match(r"^\d{1,3}\.$", token):
            return True
        return False

    @staticmethod
    def _classify_boundary(punct_run: str) -> BoundaryKind:
        if "..." in punct_run or "…" in punct_run or ".." in punct_run:
            return BoundaryKind.ELLIPSIS
        if "?" in punct_run:
            return BoundaryKind.QUESTION
        if "!" in punct_run:
            return BoundaryKind.EXCLAMATION
        if "," in punct_run:
            return BoundaryKind.COMMA
        if ";" in punct_run:
            return BoundaryKind.SEMICOLON
        if ":" in punct_run:
            return BoundaryKind.COLON
        if "—" in punct_run:
            return BoundaryKind.EM_DASH
        return BoundaryKind.PERIOD

    @classmethod
    def _extract_words(cls, text: str) -> list[str]:
        return re.findall(r"[\wÀ-ÿ]+", text)

    def _check_punctuation_split(
        self,
        candidate_clean: str,
        boundary_kind: BoundaryKind,
    ) -> bool:
        words = self._extract_words(candidate_clean)
        if boundary_kind in {
            BoundaryKind.COMMA,
            BoundaryKind.SEMICOLON,
            BoundaryKind.COLON,
            BoundaryKind.EM_DASH,
        }:
            return (
                len(candidate_clean) >= self.COMMA_MIN_CHARS
                and len(words) >= self.COMMA_MIN_WORDS
            )
        return (
            len(candidate_clean) >= self.STRONG_MIN_CHARS
            and len(words) >= self.STRONG_MIN_WORDS
        )

    @classmethod
    def _violates_clitic_or_opener_guards(cls, candidate_clean: str) -> bool:
        words = cls._extract_words(candidate_clean)
        if not words:
            return True
        last_word = words[-1].lower()
        if last_word in cls.FORBIDDEN_CHUNK_ENDINGS:
            return True
        opener_norm = " ".join(w.lower() for w in words)
        if opener_norm in cls.NON_STANDALONE_OPENERS:
            return True
        return False

    def _build_chunk(
        self,
        clean_raw: str,
        punct_run: str,
        boundary_kind: BoundaryKind,
        *,
        is_last_in_block: bool,
    ) -> ProsodicChunk:
        sanitized_clean = strip_non_speakable_content(clean_raw).strip()
        if not sanitized_clean:
            sanitized_clean = clean_raw.strip()

        raw_tts = (
            sanitized_clean
            if (punct_run == "." and sanitized_clean.endswith("."))
            else f"{sanitized_clean}{punct_run}"
        )
        normalized_body = self.normalizer.normalize(sanitized_clean)
        # Se a abreviação final já tinha ponto (ex: "etc.") e o boundary era PERIOD sem outro ponto:
        if punct_run:
            tts_text = (
                f"{normalized_body.rstrip('.')}{punct_run}"
                if punct_run.startswith(".")
                else f"{normalized_body}{punct_run}"
            )
        else:
            tts_text = normalized_body

        markers = tuple(self._pending_markers)
        self._pending_markers.clear()

        if self._chunk_index == 0:
            self._first_chunk_words = len(self._extract_words(sanitized_clean))

        chunk = ProsodicChunk(
            chunk_index=self._chunk_index,
            block_index=self.block_index,
            clean_text=sanitized_clean,
            tts_text=tts_text,
            raw_tts_text=raw_tts,
            normalized_text=normalized_body,
            boundary_kind=boundary_kind,
            is_first_in_block=(self._chunk_index == 0),
            is_last_in_block=is_last_in_block,
            inline_markers=markers,
        )
        self._chunk_index += 1
        return chunk

    def feed(self, text_delta: str) -> list[ProsodicChunk]:
        if not text_delta:
            return []
        self._buffer += text_delta
        if self._has_unclosed_construct():
            return []
        self._sanitize_completed_constructs_in_buffer()
        return self._scan_and_extract(flushing=False)

    def flush(self) -> list[ProsodicChunk]:
        if not self._buffer.strip():
            self._buffer = ""
            return []
        self._buffer = strip_non_speakable_content(self._buffer)
        if not self._buffer.strip():
            self._buffer = ""
            return []
        return self._scan_and_extract(flushing=True)

    def _scan_and_extract(self, *, flushing: bool) -> list[ProsodicChunk]:
        emitted: list[ProsodicChunk] = []

        while self._buffer.strip():
            split_found = False
            buf_len = len(self._buffer)
            i = 0

            while i < buf_len:
                ch = self._buffer[i]
                if ch not in ",.!?;:…—":
                    i += 1
                    continue

                # Agrupa sequências de pontuação terminal (..., ?!, !!, etc.)
                p_end = i + 1
                if ch in ".!?…":
                    while p_end < buf_len and self._buffer[p_end] in ".!?…":
                        p_end += 1
                punct_run = self._buffer[i:p_end]

                # Guarda de número decimal / milhar / hora / versão (ex: 1.250,50, 14:30, v1.2)
                if punct_run in {".", ",", ":"}:
                    prev_ch = self._buffer[i - 1] if i > 0 else ""
                    next_ch = self._buffer[p_end] if p_end < buf_len else ""
                    if prev_ch.isdigit() and (next_ch.isdigit() or (not flushing and p_end == buf_len)):
                        i = p_end
                        continue

                # Guarda de abreviação PT-BR (Sr., Dra., Av., etc., E.U.A.)
                if punct_run == "." and self._is_abbreviation_at(self._buffer, p_end):
                    # Se estamos no fim absoluto do bloco no flush(), não há texto depois
                    if not (flushing and not self._buffer[p_end:].strip()):
                        i = p_end
                        continue

                # Regra de Lookahead: no streaming normal (not flushing), só fecha uma fronteira
                # quando já chegou pelo menos 1 caractere não-espaço da próxima palavra/frase!
                # Assim sabemos se este chunk é ou não o último do bloco <speak>.
                remainder_after_punct = self._buffer[p_end:]
                has_more_text_after = bool(remainder_after_punct.strip())
                if not flushing and not has_more_text_after:
                    break

                candidate_clean = self._buffer[:i].strip()
                if not candidate_clean:
                    i = p_end
                    continue

                boundary_kind = self._classify_boundary(punct_run)
                is_last = flushing and not has_more_text_after

                if not is_last:
                    # Verifica guardas de palavras átonas proclíticas e conectivos isolados
                    if self._violates_clitic_or_opener_guards(candidate_clean):
                        i = p_end
                        continue
                    if not self._check_punctuation_split(candidate_clean, boundary_kind):
                        i = p_end
                        continue

                # Para "etc." no fim da frase no flush, preserva o ponto na clean_text
                if is_last and punct_run == "." and self._is_abbreviation_at(self._buffer, p_end):
                    candidate_clean = self._buffer[:p_end].strip()

                chunk = self._build_chunk(
                    candidate_clean,
                    punct_run,
                    boundary_kind,
                    is_last_in_block=is_last,
                )
                emitted.append(chunk)
                self._buffer = self._buffer[p_end:].lstrip()
                split_found = True
                break

            if split_found:
                continue

            # Se não achou pontuação de corte, verifica Soft Syntactic Split para frases longas
            soft_limit = (
                self.GEAR_1_SOFT_MAX_CHARS
                if self._chunk_index == 0
                else self.GEAR_2_SOFT_MAX_CHARS
            )
            soft_chunk = self._try_soft_syntactic_split(soft_limit, flushing=flushing)
            if soft_chunk is not None:
                emitted.append(soft_chunk)
                continue

            # No flush final, emite qualquer texto restante sem pontuação terminal
            if flushing and self._buffer.strip():
                rem = self._buffer.strip()
                self._buffer = ""
                emitted.append(
                    self._build_chunk(
                        rem,
                        "",
                        BoundaryKind.FLUSH,
                        is_last_in_block=True,
                    )
                )
            break

        return emitted

    def _try_soft_syntactic_split(
        self,
        soft_limit: int,
        *,
        flushing: bool,
    ) -> ProsodicChunk | None:
        # Só avalia até a última palavra completa (antes do último espaço) se não estiver em flush
        if not flushing:
            last_space = self._buffer.rfind(" ")
            if last_space <= 0:
                return None
            working = self._buffer[:last_space]
        else:
            working = self._buffer

        if len(working.strip()) <= soft_limit:
            return None

        # 1. Procura conjunção segura (" porque ", " mas ", " enquanto ", " quando ", " para que ")
        lower_working = working.lower()
        best_split_idx: int | None = None
        for conj in self.SOFT_SPLIT_CONJUNCTIONS:
            idx = lower_working.find(conj, 16)
            if idx != -1 and idx <= soft_limit + 25:
                prefix = working[:idx].strip()
                if prefix and not self._violates_clitic_or_opener_guards(prefix):
                    best_split_idx = idx
                    break

        # 2. Se não encontrou conjunção, procura o último espaço seguro que não termine em palavra proibida
        if best_split_idx is None:
            space_indices = [m.start() for m in re.finditer(r"\s+", working)]
            # Prefere espaços próximos ao soft_limit (primeiro <= soft_limit de trás pra frente, depois > soft_limit)
            le_indices = [s for s in reversed(space_indices) if 18 <= s <= soft_limit]
            gt_indices = [s for s in space_indices if s > soft_limit]
            for s_idx in le_indices + gt_indices:
                prefix = working[:s_idx].strip()
                if prefix and not self._violates_clitic_or_opener_guards(prefix):
                    best_split_idx = s_idx
                    break

        if best_split_idx is None:
            return None

        prefix_clean = self._buffer[:best_split_idx].strip()
        remainder = self._buffer[best_split_idx:].lstrip()
        if not remainder and flushing:
            return None

        self._buffer = remainder
        return self._build_chunk(
            prefix_clean,
            "",
            BoundaryKind.SOFT_SPLIT,
            is_last_in_block=False,
        )


_OPEN_SPEAK_FULL_RE = re.compile(r"^<\s*speak(?:\s[^>]*)?>$", re.IGNORECASE)
_CLOSE_SPEAK_FULL_RE = re.compile(r"^<\s*/\s*speak\s*>$", re.IGNORECASE)
_INLINE_MARKER_FULL_RE = re.compile(r"^\[\s*([a-zA-ZÀ-ÿ0-9_-]+(?:\s+[a-zA-ZÀ-ÿ0-9_-]+)*)\s*\]$")


def _is_potential_speak_tag_prefix(buf: str) -> bool:
    """Retorna True enquanto `buf` (iniciando com `<`) puder se tornar `<speak...>` ou `</speak>`."""
    if not buf.startswith("<"):
        return False
    if len(buf) > 64 or "\n" in buf or "<" in buf[1:]:
        return False
    if ">" in buf:
        return bool(_OPEN_SPEAK_FULL_RE.match(buf) or _CLOSE_SPEAK_FULL_RE.match(buf))

    inner = buf[1:]
    # Tags <speak> ou </speak> não começam com espaço + dígito/símbolo matemático (ex: "< 10", "<= 5")
    if not inner:
        return True
    if inner[0].isspace():
        return False

    if inner.startswith("/"):
        target = "/speak"
        after_slash = inner[1:].lstrip()
        if not after_slash:
            return True
        check = "/" + after_slash.lower()
        if len(check) <= len(target):
            return target.startswith(check)
        return check.startswith(target) and check[len(target) :].isspace()

    target = "speak"
    low = inner.lower()
    if len(low) <= len(target):
        return target.startswith(low)
    # Após "<speak", só é válido se tiver espaço para atributos (ex: "<speak lang='pt'>")
    return low.startswith(target) and low[len(target)].isspace()


def _is_potential_inline_marker_prefix(buf: str) -> bool:
    """Retorna True enquanto `buf` (iniciando com `[`) puder ser um marcador vocal inline,
    rubrica de direção de cena ou colchete de ênfase dentro de `<speak>`."""
    if not buf.startswith("["):
        return False
    if len(buf) > 40 or "\n" in buf:
        return False
    if "]" in buf:
        return bool(_INLINE_MARKER_FULL_RE.match(buf))

    inner = buf[1:]
    if not inner:
        return True
    return bool(re.match(r"^[a-zA-ZÀ-ÿ0-9_\-\s]+$", inner))


def _clean_streaming_fallback_text(text: str) -> str:
    if not text:
        return ""
    s = re.sub(r"(?m)^\s*#{1,6}\s+", "", text)
    s = re.sub(r"(?m)^\s*[-*•]\s+", "", s)
    s = s.replace("**", "").replace("__", "")
    s = re.sub(r"(?<!`)`(?!`)", "", s)
    return s


class SpeakStreamParser:
    """FSM Token-a-Token Tolerante a Falhas para extração de `<speak>...</speak>`,
    marcadores vocais inline (`[hmm]`, `[sigh]`, `[breath]`, `[laugh]`, `[riso leve]`, `[suspiro]`) e
    acionamento incremental do `PTBRProsodicChunker`.
    """

    REALTIME_FALLBACK_CHAR_THRESHOLD: int = 60

    def __init__(
        self,
        normalizer: PTBRTextNormalizer | None = None,
        *,
        live_voice_enabled: bool = False,
    ) -> None:
        self.normalizer = normalizer or PTBRTextNormalizer()
        self.chunker = PTBRProsodicChunker(normalizer=self.normalizer, block_index=0)
        self._live_voice_enabled: bool = bool(live_voice_enabled)
        self._in_speak: bool = False
        self._auto_speak_fallback: bool = False
        self._tag_buffer: str = ""
        self._marker_buffer: str = ""
        self._speak_blocks_count: int = 0
        self._total_chunks_emitted: int = 0
        self._accumulated_visual_text: list[str] = []
        self._spoken_text_accum: list[str] = []
        self._after_last_speak_text: list[str] = []

    @property
    def in_speak(self) -> bool:
        return self._in_speak

    @property
    def speak_blocks_count(self) -> int:
        return self._speak_blocks_count

    def feed(self, delta: str) -> SpeakParseResult:
        result = SpeakParseResult()
        if not delta:
            return result

        visual_chars: list[str] = []
        speak_chars: list[str] = []

        def _flush_speak_chars() -> None:
            if speak_chars:
                s_delta = "".join(speak_chars)
                speak_chars.clear()
                if self._auto_speak_fallback:
                    if "```" in s_delta or "```" in (self.chunker._buffer + s_delta):
                        combined_buf = self.chunker._buffer + s_delta
                        before_fence = combined_buf.split("```", 1)[0]
                        self.chunker._buffer = ""
                        cleaned_before = _clean_streaming_fallback_text(before_fence).rstrip("`")
                        if cleaned_before:
                            self._spoken_text_accum.append(cleaned_before)
                            result.speak_delta += cleaned_before
                            new_chunks = self.chunker.feed(cleaned_before)
                            if new_chunks:
                                result.chunks.extend(new_chunks)
                                self._total_chunks_emitted += len(new_chunks)
                        flushed = self.chunker.flush()
                        if flushed:
                            result.chunks.extend(flushed)
                            self._total_chunks_emitted += len(flushed)
                        self._in_speak = False
                        self._auto_speak_fallback = False
                        result.speak_closed = True
                        return
                    s_delta = _clean_streaming_fallback_text(s_delta)
                if s_delta:
                    self._spoken_text_accum.append(s_delta)
                    result.speak_delta += s_delta
                    new_chunks = self.chunker.feed(s_delta)
                    if new_chunks:
                        result.chunks.extend(new_chunks)
                        self._total_chunks_emitted += len(new_chunks)

        for ch in delta:
            # 1. Se estamos acumulando um possível marcador inline `[hmm]`, rubrica `[sorrindo]` ou `[E]i`
            if self._marker_buffer:
                candidate_marker = self._marker_buffer + ch
                if _is_potential_inline_marker_prefix(candidate_marker):
                    self._marker_buffer = candidate_marker
                    if self._marker_buffer.endswith("]"):
                        m = _INLINE_MARKER_FULL_RE.match(self._marker_buffer)
                        self._marker_buffer = ""
                        if m:
                            raw_inner = re.sub(r"\s+", " ", m.group(1).strip())
                            lower_inner = raw_inner.lower()
                            if lower_inner in KNOWN_VOCAL_MARKERS:
                                marker_val = VocalMarker(f"[{lower_inner}]")
                                result.inline_markers.append(marker_val)
                                self.chunker.add_inline_marker(marker_val)
                            elif _is_stage_direction(raw_inner):
                                # Rubrica de direção de cena (ex.: [sorrindo], [em tom amigável]) -> removida da fala e do visual
                                pass
                            else:
                                # Colchete de ênfase em letra/palavra (ex.: [E]i!, [alô]?) -> remove apenas '[' e ']'
                                visual_chars.append(raw_inner)
                                if self._in_speak:
                                    speak_chars.append(raw_inner)
                                elif self._speak_blocks_count > 0:
                                    self._after_last_speak_text.append(raw_inner)
                    continue
                else:
                    # Não era um marcador válido; devolve o prefixo anterior ao fluxo
                    # e deixa `ch` ser reavaliado abaixo (ex.: se `ch == '<'` em `[h</speak>`).
                    fallback = self._marker_buffer
                    self._marker_buffer = ""
                    visual_chars.append(fallback)
                    if self._in_speak:
                        speak_chars.append(fallback)
                    elif self._speak_blocks_count > 0:
                        self._after_last_speak_text.append(fallback)

            # 2. Se estamos acumulando uma possível tag `<speak>` ou `</speak>`
            if self._tag_buffer:
                candidate_tag = self._tag_buffer + ch
                if _is_potential_speak_tag_prefix(candidate_tag):
                    self._tag_buffer = candidate_tag
                    if self._tag_buffer.endswith(">"):
                        tag_str = self._tag_buffer
                        self._tag_buffer = ""
                        if _OPEN_SPEAK_FULL_RE.match(tag_str):
                            if not self._in_speak:
                                _flush_speak_chars()
                                self._in_speak = True
                                self._auto_speak_fallback = False
                                self._after_last_speak_text.clear()
                                result.speak_opened = True
                                self.chunker.reset_for_new_block(self._speak_blocks_count)
                                self._speak_blocks_count += 1
                            elif self._auto_speak_fallback:
                                _flush_speak_chars()
                                self._auto_speak_fallback = False
                                self._after_last_speak_text.clear()
                            # Tags <speak> aninhadas redundantes são ignoradas silenciosamente
                        elif _CLOSE_SPEAK_FULL_RE.match(tag_str):
                            if self._in_speak:
                                _flush_speak_chars()
                                flushed = self.chunker.flush()
                                if flushed:
                                    result.chunks.extend(flushed)
                                    self._total_chunks_emitted += len(flushed)
                                self._in_speak = False
                                self._auto_speak_fallback = False
                                result.speak_closed = True
                            # Tags </speak> órfãs/redundantes são ignoradas silenciosamente
                    continue
                else:
                    # Não era `<speak>` nem `</speak>` (ex: `< 10 reais` ou `<<speak>`)
                    fallback_tag = self._tag_buffer
                    self._tag_buffer = ""
                    visual_chars.append(fallback_tag)
                    if self._in_speak:
                        speak_chars.append(fallback_tag)
                    elif self._speak_blocks_count > 0:
                        self._after_last_speak_text.append(fallback_tag)

            # 3. Início de possível tag `<`
            if ch == "<":
                self._tag_buffer = "<"
                continue

            # 4. Início de possível marcador vocal `[` (dentro de `<speak>`)
            if ch == "[" and self._in_speak:
                self._marker_buffer = "["
                continue

            # 5. Caractere comum
            visual_chars.append(ch)
            if self._in_speak:
                speak_chars.append(ch)
            elif self._speak_blocks_count > 0:
                self._after_last_speak_text.append(ch)

        _flush_speak_chars()

        if visual_chars:
            v_delta = "".join(visual_chars)
            # Evita espaço duplo deixado por remoção de marcador inline como "[hmm] "
            v_delta = re.sub(r"  +", " ", v_delta)
            result.visual_delta = v_delta
            self._accumulated_visual_text.append(v_delta)

        if self._auto_speak_fallback:
            result.used_zero_speak_fallback = True

        if (
            self._live_voice_enabled
            and self._speak_blocks_count == 0
            and not self._in_speak
            and not self._tag_buffer
        ):
            accum_plain = "".join(self._accumulated_visual_text)
            if "```" not in accum_plain and len(accum_plain.strip()) >= self.REALTIME_FALLBACK_CHAR_THRESHOLD:
                self._auto_speak_fallback = True
                self._in_speak = True
                self.chunker.reset_for_new_block(self._speak_blocks_count)
                self._speak_blocks_count += 1
                result.speak_opened = True
                result.used_zero_speak_fallback = True
                cleaned_init = _clean_streaming_fallback_text(accum_plain)
                if cleaned_init:
                    self._spoken_text_accum.append(cleaned_init)
                    result.speak_delta = cleaned_init
                    init_chunks = self.chunker.feed(cleaned_init)
                    if init_chunks:
                        result.chunks.extend(init_chunks)
                        self._total_chunks_emitted += len(init_chunks)

        return result

    def on_non_text_boundary(self, reason: str = "tool_call") -> SpeakParseResult:
        """Fecha implicitamente `<speak>` aberto quando ocorre uma fronteira não-textual
        (ex: `TOOL_CALL`) e dá flush no `PTBRProsodicChunker`."""
        _ = reason
        result = SpeakParseResult()

        if self._marker_buffer:
            self._marker_buffer = ""

        if self._tag_buffer:
            # Descarta fragmento de tag incompleto no corte
            if not _is_potential_speak_tag_prefix(self._tag_buffer):
                result.visual_delta += self._tag_buffer
                if self._in_speak:
                    result.chunks.extend(self.chunker.feed(self._tag_buffer))
            self._tag_buffer = ""

        if self._in_speak:
            flushed = self.chunker.flush()
            if flushed:
                result.chunks.extend(flushed)
                self._total_chunks_emitted += len(flushed)
            self._in_speak = False
            self._auto_speak_fallback = False
            result.speak_closed = True

        return result

    def finish(self, live_voice_enabled: bool = False) -> SpeakParseResult:
        """Finaliza o stream do turno. Se `<speak>` ficou aberto, fecha e dá flush.
        Se `live_voice_enabled=True` e o turno teve 0 blocos `<speak>`, aplica o
        `ZeroSpeakFallback`. Se teve `<speak>` parcial (ex.: intro curta terminada em `:`),
        fala também o corpo conversacional restante.
        """
        was_auto_fallback = self._auto_speak_fallback
        result = self.on_non_text_boundary(reason="finish")
        if was_auto_fallback:
            result.used_zero_speak_fallback = True

        effective_live = bool(live_voice_enabled or self._live_voice_enabled)

        if (
            effective_live
            and self._speak_blocks_count == 0
            and self._total_chunks_emitted == 0
        ):
            full_visual = "".join(self._accumulated_visual_text).strip()
            fallback_text = self._extract_zero_speak_fallback(full_visual)
            if fallback_text:
                self.chunker.reset_for_new_block(0)
                self._speak_blocks_count = 1
                chunks = self.chunker.feed(fallback_text) + self.chunker.flush()
                if chunks:
                    result.chunks.extend(chunks)
                    self._total_chunks_emitted += len(chunks)
                    result.speak_opened = True
                    result.speak_closed = True
                    result.used_zero_speak_fallback = True
                    result.speak_delta = fallback_text
        elif effective_live and self._speak_blocks_count > 0 and not was_auto_fallback:
            spoken_so_far = "".join(self._spoken_text_accum).strip()
            unspoken_raw = "".join(self._after_last_speak_text).strip()
            if unspoken_raw:
                unspoken_clean = self._extract_zero_speak_fallback(unspoken_raw, max_chars=1200)
                non_code_total = len(spoken_so_far) + len(unspoken_clean)
                spoken_ends_with_colon = spoken_so_far.rstrip().endswith(":")
                if unspoken_clean and (
                    spoken_ends_with_colon
                    or (
                        non_code_total > 0
                        and (len(spoken_so_far) / non_code_total) < 0.35
                        and len(unspoken_clean) >= 60
                    )
                ):
                    self.chunker.reset_for_new_block(self._speak_blocks_count)
                    self._speak_blocks_count += 1
                    extra_chunks = self.chunker.feed(unspoken_clean) + self.chunker.flush()
                    if extra_chunks:
                        result.chunks.extend(extra_chunks)
                        self._total_chunks_emitted += len(extra_chunks)
                        result.speak_opened = True
                        result.speak_closed = True
                        result.used_zero_speak_fallback = True
                        result.speak_delta = (
                            f"{result.speak_delta} {unspoken_clean}".strip()
                            if result.speak_delta
                            else unspoken_clean
                        )

        return result

    @staticmethod
    def _extract_zero_speak_fallback(full_text: str, max_chars: int = 1200) -> str:
        if not full_text:
            return ""
        # Remove blocos de código cercados e JSONs antes de extrair o texto conversacional
        without_code = _FENCED_CODE_RE.sub("\n\n", full_text)
        without_code = _UNCLOSED_FENCED_CODE_RE.sub("\n\n", without_code)
        without_code = _RAW_JSON_BLOCK_RE.sub(" ", without_code).strip()

        cleaned_lines: list[str] = []
        for raw_line in without_code.splitlines():
            line = raw_line.strip()
            if not line:
                continue
            if line.startswith(("```", "|", "---", "===")):
                continue
            line = re.sub(r"^#{1,6}\s+", "", line).strip()
            line = re.sub(r"^(?:[-*•]|\d+\))\s+", "", line).strip()
            line = re.sub(r"\*\*([^*]+)\*\*", r"\1", line)
            line = re.sub(r"__([^_]+)__", r"\1", line)
            line = re.sub(r"\*([^*]+)\*", r"\1", line)
            line = re.sub(r"`([^`]+)`", r"\1", line)
            line = re.sub(r"\s+", " ", line).strip()
            if not line:
                continue
            if line[-1] not in ".!?…:;":
                line = f"{line}."
            elif line[-1] == ":":
                line = f"{line[:-1]}."
            cleaned_lines.append(line)

        if not cleaned_lines:
            return ""

        combined = " ".join(cleaned_lines)
        return strip_non_speakable_content(combined, max_chars=max_chars)
