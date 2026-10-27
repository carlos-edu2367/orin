from __future__ import annotations

import pytest

from agentos.agentic.speak_stream import (
    BoundaryKind,
    ProsodicChunk,
    PTBRProsodicChunker,
    PTBRTextNormalizer,
    SpeakStreamParser,
    strip_non_speakable_content,
)


class TestCanonicalUserExample:
    """Requisito inegociável do usuário:
    '<speak>Olá, meu nome é X, posso te auxiliar com várias coisas!</speak>'
    DEVE produzir exatamente 3 ProsodicChunks:
      0: 'Olá' (tts_text='Olá,', boundary_kind=COMMA, is_first_in_block=True)
      1: 'meu nome é X' (tts_text='meu nome é X,', boundary_kind=COMMA)
      2: 'posso te auxiliar com várias coisas' (tts_text='posso te auxiliar com várias coisas!', boundary_kind=EXCLAMATION, is_last_in_block=True)
    """

    CANONICAL_INPUT = "<speak>Olá, meu nome é X, posso te auxiliar com várias coisas!</speak>"

    @staticmethod
    def _assert_canonical_chunks(chunks: list[ProsodicChunk]) -> None:
        assert len(chunks) == 3, f"Expected 3 chunks, got {len(chunks)}: {chunks}"

        c0, c1, c2 = chunks
        assert c0.clean_text == "Olá"
        assert c0.tts_text == "Olá,"
        assert c0.boundary_kind == BoundaryKind.COMMA
        assert c0.is_first_in_block is True
        assert c0.is_last_in_block is False
        assert c0.chunk_index == 0

        assert c1.clean_text == "meu nome é X"
        assert c1.tts_text == "meu nome é X,"
        assert c1.boundary_kind == BoundaryKind.COMMA
        assert c1.is_first_in_block is False
        assert c1.is_last_in_block is False
        assert c1.chunk_index == 1

        assert c2.clean_text == "posso te auxiliar com várias coisas"
        assert c2.tts_text == "posso te auxiliar com várias coisas!"
        assert c2.boundary_kind == BoundaryKind.EXCLAMATION
        assert c2.is_first_in_block is False
        assert c2.is_last_in_block is True
        assert c2.chunk_index == 2

    def test_canonical_example_single_delta(self) -> None:
        parser = SpeakStreamParser()
        res = parser.feed(self.CANONICAL_INPUT)
        finish_res = parser.finish()
        all_chunks = res.chunks + finish_res.chunks

        self._assert_canonical_chunks(all_chunks)
        assert res.speak_opened is True
        assert res.speak_closed is True
        assert (res.visual_delta + finish_res.visual_delta) == (
            "Olá, meu nome é X, posso te auxiliar com várias coisas!"
        )

    def test_canonical_example_character_by_character(self) -> None:
        parser = SpeakStreamParser()
        collected_chunks: list[ProsodicChunk] = []
        visual_parts: list[str] = []
        opened_count = 0
        closed_count = 0

        for ch in self.CANONICAL_INPUT:
            res = parser.feed(ch)
            collected_chunks.extend(res.chunks)
            visual_parts.append(res.visual_delta)
            if res.speak_opened:
                opened_count += 1
            if res.speak_closed:
                closed_count += 1

        fin = parser.finish()
        collected_chunks.extend(fin.chunks)
        visual_parts.append(fin.visual_delta)

        self._assert_canonical_chunks(collected_chunks)
        assert opened_count == 1
        assert closed_count == 1
        assert "".join(visual_parts) == "Olá, meu nome é X, posso te auxiliar com várias coisas!"

    @pytest.mark.parametrize(
        "tokens",
        [
            ["<", "sp", "eak>", "Ol", "á,", " meu ", "nome é", " X,", " posso te ", "auxiliar com ", "várias coisas!", "</s", "peak>"],
            ["<speak >", "Olá, meu", " nome é X, posso", " te auxiliar com várias coisas", "!</speak>"],
            ["<SPEAK>", "Olá,", " ", "meu nome é X,", " ", "posso te auxiliar com várias coisas!", "</SPEAK>"],
        ],
    )
    def test_canonical_example_subword_tokens(self, tokens: list[str]) -> None:
        parser = SpeakStreamParser()
        collected: list[ProsodicChunk] = []
        for tok in tokens:
            res = parser.feed(tok)
            collected.extend(res.chunks)
        collected.extend(parser.finish().chunks)
        self._assert_canonical_chunks(collected)


class TestSpeakStreamParserAdversarial:
    def test_broken_tags_and_redundant_nested_speak_tags(self) -> None:
        parser = SpeakStreamParser()
        tokens = ["<", "sp", "eak>", "Oi, ", "<speak>", "tudo bem com você?", "</s", "peak>", "</speak>"]
        chunks: list[ProsodicChunk] = []
        visual = ""
        for t in tokens:
            r = parser.feed(t)
            chunks.extend(r.chunks)
            visual += r.visual_delta
        fin = parser.finish()
        chunks.extend(fin.chunks)
        visual += fin.visual_delta

        assert visual == "Oi, tudo bem com você?"
        assert len(chunks) == 2
        assert chunks[0].clean_text == "Oi"
        assert chunks[1].clean_text == "tudo bem com você"

    def test_mathematical_less_than_inside_speak(self) -> None:
        parser = SpeakStreamParser()
        tokens = ["<speak>", "O valor é ", "<", " 10 reais hoje!", "</speak>"]
        chunks: list[ProsodicChunk] = []
        visual = ""
        for t in tokens:
            r = parser.feed(t)
            chunks.extend(r.chunks)
            visual += r.visual_delta
        fin = parser.finish()
        chunks.extend(fin.chunks)
        visual += fin.visual_delta

        assert visual == "O valor é < 10 reais hoje!"
        assert len(chunks) == 1
        assert "< 10" in chunks[0].clean_text or "menor que 10" in chunks[0].tts_text

    def test_inline_vocal_markers_extracted_and_stripped_from_speech(self) -> None:
        parser = SpeakStreamParser()
        tokens = ["<speak>", "[h", "mm] ", "Claro, ", "[sigh]", " vamos resolver isso agora.", "</speak>"]
        chunks: list[ProsodicChunk] = []
        markers: list[str] = []
        visual = ""
        for t in tokens:
            r = parser.feed(t)
            chunks.extend(r.chunks)
            markers.extend(r.inline_markers)
            visual += r.visual_delta
        fin = parser.finish()
        chunks.extend(fin.chunks)
        markers.extend(fin.inline_markers)
        visual += fin.visual_delta

        assert "[hmm]" in markers
        assert "hmm" in markers
        assert "[sigh]" in markers
        assert "[hmm]" not in visual
        assert "[sigh]" not in visual
        assert len(chunks) == 2
        assert chunks[0].clean_text == "Claro"
        assert chunks[1].clean_text == "vamos resolver isso agora"

    def test_unclosed_speak_flushed_on_non_text_boundary_and_finish(self) -> None:
        parser = SpeakStreamParser()
        r1 = parser.feed("<speak>Deixa eu verificar os logs do servidor")
        assert r1.speak_opened is True
        assert r1.chunks == []

        r_tool = parser.on_non_text_boundary(reason="tool_call")
        assert r_tool.speak_closed is True
        assert len(r_tool.chunks) == 1
        assert r_tool.chunks[0].clean_text == "Deixa eu verificar os logs do servidor"
        assert r_tool.chunks[0].is_last_in_block is True

    def test_zero_speak_fallback_when_live_voice_enabled_and_llm_forgets_tags(self) -> None:
        parser = SpeakStreamParser()
        r1 = parser.feed("Perfeito! Já atualizei a configuração do banco para você.\n\n```json\n{\"ok\": true}\n```")
        assert r1.chunks == []

        fin = parser.finish(live_voice_enabled=True)
        assert fin.used_zero_speak_fallback is True
        assert fin.speak_opened is True
        assert fin.speak_closed is True
        assert len(fin.chunks) >= 1
        combined = " ".join(c.clean_text for c in fin.chunks)
        assert "Perfeito" in combined
        assert "atualizei a configuração" in combined
        assert '"ok"' not in combined

    def test_double_angle_brackets_and_partial_marker_before_close_tag(self) -> None:
        parser = SpeakStreamParser()
        r = parser.feed("<<speak>Olá, [h</speak>")
        fin = parser.finish()
        chunks = r.chunks + fin.chunks
        assert r.speak_opened is True
        assert r.speak_closed is True
        assert len(chunks) == 2
        assert chunks[0].clean_text == "Olá"
        assert chunks[1].clean_text == "[h"

    def test_multiple_speak_blocks_in_same_turn_separate_block_indices(self) -> None:
        parser = SpeakStreamParser()
        r1 = parser.feed("<speak>Vou rodar os testes agora.</speak>\nExecutando pytest...\n<speak>Pronto, tudo passou!</speak>")
        chunks = r1.chunks + parser.finish().chunks
        assert len(chunks) == 3
        assert chunks[0].block_index == 0
        assert chunks[0].clean_text == "Vou rodar os testes agora"
        assert chunks[0].is_last_in_block is True
        assert chunks[1].block_index == 1
        assert chunks[1].clean_text == "Pronto"
        assert chunks[1].is_first_in_block is True
        assert chunks[2].block_index == 1
        assert chunks[2].clean_text == "tudo passou"
        assert chunks[2].is_last_in_block is True


class TestPTBRProsodicGuardsAndNormalizer:
    def test_abbreviations_currencies_and_times_do_not_break_chunks_and_normalize(self) -> None:
        parser = SpeakStreamParser()
        text = "<speak>Sr. Carlos, o lote custa R$ 1.250,50 às 14h30 na Av. Paulista, tudo certo?</speak>"
        res = parser.feed(text)
        fin = parser.finish()
        chunks = res.chunks + fin.chunks

        assert len(chunks) == 3, f"Expected 3 chunks, got {chunks}"
        assert chunks[0].clean_text == "Sr. Carlos"
        assert "senhor Carlos" in chunks[0].tts_text

        assert chunks[1].clean_text == "o lote custa R$ 1.250,50 às 14h30 na Av. Paulista"
        assert "1250 reais e 50 centavos" in chunks[1].tts_text
        assert "14 horas e 30 minutos" in chunks[1].tts_text
        assert "avenida Paulista" in chunks[1].tts_text

        assert chunks[2].clean_text == "tudo certo"
        assert chunks[2].tts_text == "tudo certo?"
        assert chunks[2].boundary_kind == BoundaryKind.QUESTION
        assert chunks[2].is_last_in_block is True

    def test_non_standalone_openers_agglutinate_first_chunk(self) -> None:
        parser = SpeakStreamParser()
        res = parser.feed("<speak>Mas, pensando bem, vamos fazer isso agora.</speak>")
        chunks = res.chunks + parser.finish().chunks

        assert len(chunks) == 2
        assert chunks[0].clean_text == "Mas, pensando bem"
        assert chunks[0].tts_text == "Mas, pensando bem,"
        assert chunks[1].clean_text == "vamos fazer isso agora"
        assert chunks[1].tts_text == "vamos fazer isso agora."

    def test_forbidden_chunk_endings_never_leave_dangling_proclitics(self) -> None:
        chunker = PTBRProsodicChunker()
        # Frase longa sem pontuação onde o corte por tamanho cairia numa preposição/conjunção se não protegido
        long_clause = (
            "Estou analisando cuidadosamente todos os arquivos de "
            "configuração do sistema porque precisamos garantir estabilidade total hoje."
        )
        chunks = chunker.feed(long_clause) + chunker.flush()
        assert len(chunks) >= 2
        for c in chunks:
            last_word = c.clean_text.rstrip(".,!?;:").split()[-1].lower()
            assert last_word not in PTBRProsodicChunker.FORBIDDEN_CHUNK_ENDINGS

    def test_multiple_punctuation_ellipsis_and_interrobang_preserved(self) -> None:
        parser = SpeakStreamParser()
        chunks: list[ProsodicChunk] = []
        for ch in "<speak>Espere um pouco... Sério isso?! Vamos lá!!</speak>":
            chunks.extend(parser.feed(ch).chunks)
        chunks.extend(parser.finish().chunks)

        assert len(chunks) == 3
        assert chunks[0].boundary_kind == BoundaryKind.ELLIPSIS
        assert chunks[0].tts_text.endswith("...")
        assert chunks[1].boundary_kind == BoundaryKind.QUESTION
        assert chunks[1].tts_text.endswith("?!")
        assert chunks[2].boundary_kind == BoundaryKind.EXCLAMATION
        assert chunks[2].tts_text.endswith("!!")

    def test_ptbr_text_normalizer_expansions(self) -> None:
        norm = PTBRTextNormalizer()
        assert "1250 reais e 50 centavos" in norm.normalize("R$ 1.250,50")
        assert "10 dólares" in norm.normalize("US$ 10")
        assert "14 horas e 30 minutos" in norm.normalize("14h30")
        assert "uma hora" in norm.normalize("1h")
        assert "3 vírgula 5 por cento" in norm.normalize("3,5%")
        assert "senhor" in norm.normalize("Sr. Silva")
        assert "doutora" in norm.normalize("Dra. Ana")
        assert "avenida" in norm.normalize("Av. Brasil")
        assert "etcétera" in norm.normalize("livros, cadernos, etc.")

        tech = norm.normalize("A API usa SQL, MCP, LLM, TTS, STT e JSON na GPU e CPU com RAM.")
        assert "A P I" in tech
        assert "S Q L" in tech
        assert "M C P" in tech
        assert "L L M" in tech
        assert "T T S" in tech
        assert "S T T" in tech
        assert "G P U" in tech
        assert "C P U" in tech


class TestStripNonSpeakableContent:
    def test_replaces_code_blocks_and_urls(self) -> None:
        raw = (
            "Olha o exemplo:\n```python\ndef hello():\n    return 42\n```\n"
            "E a documentação está em https://docs.orin.ai/guia/v1."
        )
        cleaned = strip_non_speakable_content(raw)
        assert "deixei o código na tela" in cleaned
        assert "def hello" not in cleaned
        assert "o link na tela" in cleaned
        assert "https://" not in cleaned

    def test_speak_parser_strips_code_and_urls_inside_speak_tag(self) -> None:
        parser = SpeakStreamParser()
        res = parser.feed(
            "<speak>Pronto, ```python\nprint('oi')\n``` já ajustei, veja em https://localhost:8000/test!</speak>"
        )
        chunks = res.chunks + parser.finish().chunks
        combined = " ".join(c.tts_text for c in chunks)
        assert "deixei o código na tela" in combined
        assert "print" not in combined
        assert "o link na tela" in combined
        assert "https://" not in combined
