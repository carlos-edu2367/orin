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
    """Cohesive PT-BR prosodic chunking:
    Short greetings/openers like 'Olá,' or 'meu nome é X,' must NOT be chopped into
    1-3 word micro-fragments that trigger Spanish/Italian accent drift in TTS models.
    """

    CANONICAL_INPUT = "<speak>Olá, meu nome é X, posso te auxiliar com várias coisas!</speak>"

    @staticmethod
    def _assert_canonical_chunks(chunks: list[ProsodicChunk]) -> None:
        assert len(chunks) == 1, f"Expected 1 cohesive chunk, got {len(chunks)}: {chunks}"

        c0 = chunks[0]
        assert c0.clean_text == "Olá, meu nome é X, posso te auxiliar com várias coisas"
        assert c0.tts_text == "Olá, meu nome é X, posso te auxiliar com várias coisas!"
        assert c0.boundary_kind == BoundaryKind.EXCLAMATION
        assert c0.is_first_in_block is True
        assert c0.is_last_in_block is True
        assert c0.chunk_index == 0

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
        assert len(chunks) == 1
        assert chunks[0].clean_text == "Oi, tudo bem com você"
        assert chunks[0].tts_text == "Oi, tudo bem com você?"

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
        assert len(chunks) == 1
        assert chunks[0].clean_text == "Claro, vamos resolver isso agora"
        assert chunks[0].tts_text == "Claro, vamos resolver isso agora."

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
        assert len(chunks) == 1
        assert chunks[0].clean_text == "Olá, [h"

    def test_multiple_speak_blocks_in_same_turn_separate_block_indices(self) -> None:
        parser = SpeakStreamParser()
        r1 = parser.feed("<speak>Vou rodar os testes agora.</speak>\nExecutando pytest...\n<speak>Pronto, tudo passou!</speak>")
        chunks = r1.chunks + parser.finish().chunks
        assert len(chunks) == 2
        assert chunks[0].block_index == 0
        assert chunks[0].clean_text == "Vou rodar os testes agora"
        assert chunks[0].is_last_in_block is True
        assert chunks[1].block_index == 1
        assert chunks[1].clean_text == "Pronto, tudo passou"
        assert chunks[1].is_first_in_block is True
        assert chunks[1].is_last_in_block is True


class TestPTBRProsodicGuardsAndNormalizer:
    def test_abbreviations_currencies_and_times_do_not_break_chunks_and_normalize(self) -> None:
        parser = SpeakStreamParser()
        text = "<speak>Sr. Carlos, o lote custa R$ 1.250,50 às 14h30 na Av. Paulista, tudo certo?</speak>"
        res = parser.feed(text)
        fin = parser.finish()
        chunks = res.chunks + fin.chunks

        assert len(chunks) == 2, f"Expected 2 chunks, got {chunks}"
        assert chunks[0].clean_text == "Sr. Carlos, o lote custa R$ 1.250,50 às 14h30 na Av. Paulista"
        assert "senhor Carlos" in chunks[0].tts_text
        assert "1250 reais e 50 centavos" in chunks[0].tts_text
        assert "14 horas e 30 minutos" in chunks[0].tts_text
        assert "avenida Paulista" in chunks[0].tts_text

        assert chunks[1].clean_text == "tudo certo"
        assert chunks[1].tts_text == "tudo certo?"
        assert chunks[1].boundary_kind == BoundaryKind.QUESTION
        assert chunks[1].is_last_in_block is True

    def test_non_standalone_openers_agglutinate_first_chunk(self) -> None:
        parser = SpeakStreamParser()
        res = parser.feed("<speak>Mas, pensando bem, vamos fazer isso agora.</speak>")
        chunks = res.chunks + parser.finish().chunks

        assert len(chunks) == 1
        assert chunks[0].clean_text == "Mas, pensando bem, vamos fazer isso agora"
        assert chunks[0].tts_text == "Mas, pensando bem, vamos fazer isso agora."

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
        for ch in "<speak>Espere só um pouquinho agora... Você está falando sério mesmo?! Vamos resolver isso juntos!!</speak>":
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

        tech = norm.normalize("A API e as APIs usam URL, URLs, SQL, MCP, MCPs, LLM, LLMs, TTS, STT e JSON na GPU e CPU com RAM.")
        assert "á pê i" in tech
        assert "á pê is" in tech
        assert "u érre éle" in tech
        assert "u érre éles" in tech
        assert "ésse quê éle" in tech
        assert "ême cê pê" in tech
        assert "ême cê pês" in tech
        assert "éle éle ême" in tech
        assert "éle éle êmes" in tech
        assert "tê tê ésse" in tech
        assert "ésse tê tê" in tech
        assert "gê pê u" in tech
        assert "cê pê u" in tech
        assert "rã" in tech


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


class TestLiveCallRegressionsAndRealTimeFallback:
    def test_bracket_emphasis_and_extended_vocal_markers_do_not_leak_brackets(self) -> None:
        parser = SpeakStreamParser()
        stream = [
            "<speak>[E]i! ",
            "[alô]? ",
            "[riso leve] Tudo bem por aí? ",
            "[suspiro] [sorrindo] [em tom amigável] Bora lá!</speak>",
        ]
        chunks: list[ProsodicChunk] = []
        markers = []
        visual = ""
        for token in stream:
            res = parser.feed(token)
            visual += res.visual_delta
            chunks.extend(res.chunks)
        fin = parser.finish()
        visual += fin.visual_delta
        chunks.extend(fin.chunks)

        marker_names = [m.marker for c in chunks for m in c.inline_markers]
        assert "riso leve" in marker_names
        assert "suspiro" in marker_names

        assert "[" not in visual
        assert "]" not in visual
        assert "Ei!" in visual
        assert "alô?" in visual
        assert "sorrindo" not in visual
        assert "em tom amigável" not in visual

        combined_clean = " ".join(c.clean_text for c in chunks)
        combined_norm = " ".join(c.normalized_text for c in chunks)
        assert "[" not in combined_clean
        assert "]" not in combined_clean
        assert "[" not in combined_norm
        assert "]" not in combined_norm
        assert "Ei" in combined_clean
        assert "alô" in combined_clean
        assert "sorrindo" not in combined_clean
        assert "amigável" not in combined_clean

    def test_zero_speak_fallback_joins_multiple_paragraphs_and_cleans_markdown_up_to_1200_chars(self) -> None:
        parser = SpeakStreamParser()
        long_markdown = (
            "Eu trabalho direto com você nos seus projetos locais.\n\n"
            "### 1. Mão na massa no código\n"
            "• Eu leio, crio e edito arquivos direto na sua máquina.\n"
            "• Rodo comandos e testes no **terminal** para validar tudo.\n\n"
            "### 2. Pesquisa e memória\n"
            "- Busco referências atualizadas na web quando necessário.\n"
            "- Guardo o contexto importante entre as nossas conversas.\n\n"
            "```bash\npytest -q\n```"
        )
        parser.feed(long_markdown)
        fin = parser.finish(live_voice_enabled=True)
        assert fin.used_zero_speak_fallback is True
        assert len(fin.chunks) >= 4
        spoken_all = " ".join(c.clean_text for c in fin.chunks)
        assert "Mão na massa no código" in spoken_all
        assert "Eu leio, crio e edito arquivos direto na sua máquina" in spoken_all
        assert "Rodo comandos e testes no terminal para validar tudo" in spoken_all
        assert "Pesquisa e memória" in spoken_all
        assert "Guardo o contexto importante entre as nossas conversas" in spoken_all
        assert "###" not in spoken_all
        assert "•" not in spoken_all
        assert "**" not in spoken_all
        assert "pytest" not in spoken_all

    def test_partial_speak_fallback_speaks_unspoken_body_after_short_colon_intro(self) -> None:
        parser = SpeakStreamParser()
        partial_speak_response = (
            "<speak>Aqui vai a tradução direta do que faço:</speak>\n\n"
            "### 1. Mão na massa no seu código\n"
            "• Eu leio e edito arquivos na sua máquina.\n"
            "• Rodo testes no terminal para conferir se passou."
        )
        r1 = parser.feed(partial_speak_response)
        fin = parser.finish(live_voice_enabled=True)
        all_chunks = r1.chunks + fin.chunks
        spoken_all = " ".join(c.clean_text for c in all_chunks)
        assert "Aqui vai a tradução direta do que faço" in spoken_all
        assert "Mão na massa no seu código" in spoken_all
        assert "Eu leio e edito arquivos na sua máquina" in spoken_all
        assert "Rodo testes no terminal para conferir se passou" in spoken_all
        assert "###" not in spoken_all
        assert "•" not in spoken_all

    def test_realtime_streaming_fallback_activates_during_feed_when_live_voice_enabled(self) -> None:
        parser = SpeakStreamParser(live_voice_enabled=True)
        tokens = [
            "Estou te ouvindo perfeitamente, Carlos! ",
            "Pode me dizer no que você quer trabalhar agora mesmo, ",
            "que eu já olho os arquivos do projeto com você.",
        ]
        streamed_chunks: list[ProsodicChunk] = []
        opened_during_feed = False
        for tok in tokens:
            res = parser.feed(tok)
            if res.speak_opened:
                opened_during_feed = True
            streamed_chunks.extend(res.chunks)

        assert opened_during_feed is True
        assert len(streamed_chunks) >= 1
        fin = parser.finish(live_voice_enabled=True)
        all_chunks = streamed_chunks + fin.chunks
        full_spoken = " ".join(c.clean_text for c in all_chunks)
        assert "Estou te ouvindo perfeitamente" in full_spoken
        assert "arquivos do projeto com você" in full_spoken

    def test_chunker_gear1_does_not_split_single_non_greeting_word_comma_and_gear2_avoids_micro_comma_fragments(self) -> None:
        assert PTBRProsodicChunker.GEAR_1_MIN_CHARS == 28
        assert PTBRProsodicChunker.GEAR_1_MIN_WORDS == 5
        assert PTBRProsodicChunker.COMMA_MIN_CHARS == 48
        assert PTBRProsodicChunker.COMMA_MIN_WORDS == 8
        assert PTBRProsodicChunker.STRONG_MIN_CHARS == 24
        assert PTBRProsodicChunker.STRONG_MIN_WORDS == 4
        assert PTBRProsodicChunker.GEAR_1_SOFT_MAX_CHARS == 110
        assert PTBRProsodicChunker.GEAR_2_SOFT_MAX_CHARS == 160

        parser = SpeakStreamParser()
        res = parser.feed(
            "<speak>Hoje, vamos analisar o seu código com bastante cuidado, sem pressa, para garantir que tudo funcione bem.</speak>"
        )
        fin = parser.finish()
        chunks = res.chunks + fin.chunks
        assert len(chunks) >= 1
        assert chunks[0].clean_text != "Hoje"
        assert chunks[0].clean_text.startswith("Hoje, vamos analisar")
        for c in chunks:
            assert c.clean_text != "sem pressa"

        # Short cognates ("Pronto, vou verificar.", "Claro! Estou aqui para ajudar.", "Em resumo: tudo está funcionando.")
        # must NEVER be split into 1-2 word micro-fragments that trigger Spanish/Italian accent drift!
        for sample, expected_clean in [
            ("<speak>Pronto, vou verificar.</speak>", "Pronto, vou verificar"),
            ("<speak>Claro! Estou aqui para ajudar.</speak>", "Claro! Estou aqui para ajudar"),
            ("<speak>Em resumo: tudo está funcionando.</speak>", "Em resumo: tudo está funcionando"),
            ("<speak>Pronto! Já resolvi o problema para você.</speak>", "Pronto! Já resolvi o problema para você"),
        ]:
            p = SpeakStreamParser()
            ch_list = p.feed(sample).chunks + p.finish().chunks
            assert len(ch_list) == 1, f"Expected 1 cohesive chunk for {sample!r}, got {ch_list}"
            assert ch_list[0].clean_text == expected_clean

    def test_realtime_fallback_stops_at_fenced_code_and_partial_fallback_ignores_pure_code_blocks(self) -> None:
        parser = SpeakStreamParser(live_voice_enabled=True)
        chunks: list[ProsodicChunk] = []
        for tok in [
            "Claro, Carlos! Preparei o trecho em Python para você testar agora mesmo.\n\n",
            "```python\ndef secret_impl():\n    return 42\n```",
        ]:
            chunks.extend(parser.feed(tok).chunks)
        chunks.extend(parser.finish(live_voice_enabled=True).chunks)
        spoken = " ".join(c.clean_text for c in chunks)
        assert "Preparei o trecho em Python" in spoken
        assert "secret_impl" not in spoken

        parser2 = SpeakStreamParser(live_voice_enabled=True)
        r2 = parser2.feed("<speak>Veja o código abaixo:</speak>\n\n```python\ndef only_code():\n    return 99\n```")
        f2 = parser2.finish(live_voice_enabled=True)
        assert f2.chunks == []
        spoken2 = " ".join(c.clean_text for c in r2.chunks + f2.chunks)
        assert "only_code" not in spoken2

