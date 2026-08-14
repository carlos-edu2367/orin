# Auditoria — Fluxo de navegação conversacional

**Status:** Fases 0, 1, 2 e 3 implementadas e validadas contra `food.neectify.com`, `duckduckgo.com` e `example.com`/`example.org` reais. Ver seções "Implementação" ao final. Fase 3 tem escopo deliberadamente reduzido — ver "O que ficou de fora da Fase 3".
**Data:** 2026-08-14
**Sintoma relatado:** o agente abre a mesma página várias vezes, gera capturas repetidas e não avança. "É como se ele tirasse a captura mas nunca as visse."
**Escopo:** `agentos.agentic.browser_tools`, `agentos.agentic.agent_tools` (ferramentas `browse_page` / `browser_*`), `agentos.browser.conversation_worker`, `agentos.browser.security`, `agentos.agentic.runtime`, `agentos.agentic.session`, `ChatWorker`, `BrowserActivityCard`.
**Método:** leitura do caminho completo; reprodução real contra `https://food.neectify.com` e `https://duckduckgo.com` com Chromium provisionado, medindo tempos, bytes de captura, texto extraído, sobrevivência de query string e recuperação após timeout.

## Conclusão

O mecanismo funciona: navegar, extrair HTML e capturar PNG funcionam de verdade (146 KB de HTML, 473 KB de PNG, 6.632 caracteres de texto em 1–3 s com o navegador quente). O que está quebrado é tudo **em volta** do mecanismo:

1. o agente navega para uma **URL diferente da que pediu**, sem ser avisado;
2. o agente **não consegue clicar em nada**, porque o texto que ele recebe não contém nenhum atributo com que montar um seletor;
3. o agente **não recebe a imagem** na maioria das configurações, embora a descrição da ferramenta o incentive a capturar.

Com essas três, o comportamento observado é o único comportamento possível: navegar → capturar → não conseguir interagir → tentar de novo. O loop de 220 atividades não é um bug de loop, é a consequência.

## Achados priorizados

| # | Severidade | Achado | Evidência |
| --- | --- | --- | --- |
| 1 | P0 | Query string descartada silenciosamente antes de navegar | reprodução real |
| 2 | P0 | Cache de `browse_page` colide URLs diferentes e reemite artefato | leitura + reprodução |
| 3 | P0 | Nenhum seletor é obtível a partir do que o modelo vê | reprodução real |
| 4 | P0 | Um único timeout mata o navegador pelo resto do turno | reprodução real |
| 5 | P1 | `model_sees_images` desliga por omissão do catálogo | leitura |
| 6 | P1 | O system prompt não menciona o navegador | leitura |
| 7 | P1 | A imagem chega ao modelo rotulada como "arquivo" | leitura |
| 8 | P1 | Falha de captura é invisível para o modelo e para a interface | leitura |
| 9 | P2 | Subagentes paralelos compartilham uma única aba | leitura |
| 10 | P2 | O estado do navegador morre no fim de cada turno | leitura |
| 11 | P2 | Falha de bootstrap do processo filho tem diagnóstico enganoso | reprodução acidental |
| 12 | P3 | Política de rede recusa `http` e portas ≠ 443 com mensagem opaca | reprodução |

---

### P0-1 — A query string é descartada silenciosamente antes de navegar

`validate_url()` termina retornando `sanitize_url(value)`, e `sanitize_url` reconstrói a URL com query e fragment **vazios** (`security.py:44`). O host de navegação usa exatamente esse retorno como alvo do `page.goto` (`conversation_worker.py:152-163`).

Reprodução:

```
REQUESTED : https://duckduckgo.com/html/?q=neectify+food
LANDED ON : https://html.duckduckgo.com/html/
TITLE     : DuckDuckGo HTML: Private Search Without JavaScript
TEXT[:200]: About DuckDuckGo
```

O agente pediu uma busca e recebeu o formulário vazio — com status de sucesso. Nenhuma busca, nenhum filtro, nenhuma paginação, nenhum `?id=` funciona. E o pior: **o agente não é informado**. Ele recebe uma observação bem-sucedida de uma página errada, não encontra o que procurava e tenta de novo. Este é, sozinho, o gerador mais provável do loop observado.

`sanitize_url` está correta para o que o nome diz — exibir/logar sem vazar segredo em query param. O erro é usá-la como valor de navegação.

### P0-2 — O cache de `browse_page` colide URLs diferentes e reemite o mesmo artefato

`agent_tools.py:964` usa `navigation_key = _safe_display_url(target)`, e `_safe_display_url` (`browser_tools.py:42-50`) também zera query e fragment. Confirmado:

```
display key a: https://x.com/s     (de https://x.com/s?q=a)
display key b: https://x.com/s     (de https://x.com/s?q=b)
```

Duas URLs distintas compartilham uma entrada de cache: a segunda devolve o `ToolOutcome` da primeira **sem tocar no navegador**.

E é isto que explica literalmente a sua captura de tela: no cache hit, o `ToolOutcome` devolvido carrega o mesmo `payload["artifacts"]`, e `emit_lifecycle` (`session.py:486-501`) grava um `ARTIFACT_CREATED` novo a cada chamada. Daí `Criou browser-captures/fb6e1d…5e5.png` repetido dezenas de vezes com **o mesmo hash**: não são várias capturas, é uma captura reanunciada. A timeline sugere trabalho onde não houve nenhum.

### P0-3 — O agente não consegue interagir: nenhum seletor é obtível

`_TextExtractor` (`agent_tools.py:105-141`) ignora `attrs` por completo. O modelo recebe prosa: sem `id`, `class`, `name`, `href`, `role`, `type`, `placeholder`, `data-*`. Do outro lado, `_single()` (`conversation_worker.py:40-44`) exige `locator.count() == 1`.

Reprodução contra o site real, com seletores plausíveis que um modelo tentaria:

```
[click 'button']       FAILED -> selector must match exactly one visible element
[click 'a[href]']      FAILED -> selector must match exactly one visible element
[click 'nav a']        FAILED -> selector must match exactly one visible element
[click '#nonexistent'] FAILED -> selector must match exactly one visible element
```

Quatro tentativas, quatro falhas, **e a mesma mensagem para "não existe" e para "casou com 23 elementos"**. O modelo não tem nem o seletor nem o sinal de correção. Não existe caminho pelo qual ele descubra um seletor válido a partir do que recebeu.

Este é o núcleo do "tira a captura e não faz nada": ele consegue navegar e capturar, não consegue interagir, então repete a única coisa que funciona.

### P0-4 — Um único timeout mata o navegador pelo resto do turno

`_request` (`conversation_worker.py:266-278`), ao estourar o `poll`, chama `_terminate()`, marca `_closed = True` e mata o processo. Não há respawn em lugar nenhum. Reprodução:

```
call 1 -> browser operation timed out
call 2 -> RuntimeError: browser session is closed
call 3 -> RuntimeError: browser session is closed
```

Pior, o orçamento está **aritmeticamente invertido**. O pai concede 35 s. O filho, numa navegação, pode gastar `goto` 30 s + `load` 5 s + `networkidle` 5 s + `screenshot` 5 s ≈ 45 s — mais o custo de subida do Chromium na primeira chamada (medido entre 2,6 s e 10 s, porque o `launch` acontece no filho antes do primeiro comando). Ou seja: o pai mata o filho antes que o filho consiga estourar o próprio timeout. O tratamento gracioso do filho (`{"ok": False, "error": "browser operation timed out"}`, recuperável) é **código morto** para navegação lenta.

Resultado prático: um site lento derruba o navegador e todas as ferramentas de browser passam a falhar até o fim do turno.

### P1-5 — `model_sees_images` desliga por omissão do catálogo

`_model_sees_images` (`chat.py:239-244`) devolve `False` sempre que a linha do catálogo estiver ausente ou não atualizada. Compare com o vizinho imediato `_model_calls_tools` (`:246-254`), que **deliberadamente** assume permissivo quando a lista vem vazia, com o comentário "um catálogo não atualizado não pode desabilitar ferramentas silenciosamente". A mesma preocupação não foi aplicada às imagens.

Com `model_sees_images = False`, `_browser_outcome` (`agent_tools.py:1009`) grava a PNG no workspace e **não anexa nada** para o modelo. A descrição da ferramenta, porém, diz "return its rendered text plus one private screenshot visible in the chat" e "Capture the current browser screen **for the user**". Ou seja: o sistema instrui o modelo a produzir imagens que ele próprio não consome, e não lhe diz isso.

É exatamente a sua frase: "é como se ele tirasse a captura mas nunca as visse". Na maioria das configurações, ele realmente nunca as vê.

### P1-6 — O system prompt não menciona o navegador

`build_system_prompt` (`session.py:179-279`) tem blocos para ferramentas, `ask_user`, Skills, workspace, ambiente, subagentes, ledger e memórias. **Não há bloco de navegador.** Nenhuma orientação de fluxo: que `browser_observe` devolve texto a ser lido, que o seletor sai do HTML observado, que repetir `browse_page` na mesma URL não produz informação nova, que interagir exige observar antes.

### P1-7 — A imagem, quando enviada, chega rotulada como arquivo

`_tool_result_messages` (`runtime.py:623-625`) anexa a imagem como mensagem de usuário com o texto fixo `"Conteúdo visual do arquivo solicitado:"`. Para uma captura de navegador é errado e confuso: não diz que é a página atual, não diz qual URL, e sugere que o usuário pediu um arquivo.

### P1-8 — Falha de captura é invisível

Quando o `screenshot` estoura os 5 s, `_observation_for_page` devolve `screenshot: ""` (best-effort, por design). Mas então `_browser_outcome` simplesmente não grava artefato nem anexa imagem, **e o texto devolvido não menciona nada**. O modelo não sabe que a captura falhou; a interface fica permanentemente em "Navegador em atividade; a captura aparecerá ao concluir a ação" (`BrowserActivityCard.tsx:36`).

Além disso, `len(image) > MAX_SCREENSHOT_BYTES` levanta `ValueError` (`conversation_worker.py:74-75`), o que descarta **também o HTML** de uma observação boa por um limite cosmético.

### P2-9 — Subagentes paralelos compartilham uma única aba

`_toolset(subagents=False)` (`session.py:751-768`) passa `browser=self.browser` — a mesma instância. `_ask_agents` (`session.py:692`) roda até 4 subagentes em threads paralelas. O `Lock` de `IsolatedConversationBrowser` serializa as *chamadas*, mas não o *estado da aba*: A navega, B navega para outro lugar, o clique seguinte de A cai na página de B.

Pior: cada toolset tem seu próprio `_last_browser_navigation_url`, então o cache de um agente pode afirmar uma página que a aba abandonou há muito tempo. Bate com a sua captura: `Researcher` e `Researcher_Food` criados, "2 ações de ferramentas — Falhou", delegação cancelada.

### P2-10 — O estado do navegador morre no fim do turno

O navegador é criado por turno em `_runtime_for` (`chat.py:450`) e fechado em `runtime.close()` → `toolset.close()`. Um login ou fluxo de várias etapas nunca sobrevive a duas mensagens do usuário: o turno seguinte recebe uma aba em branco.

### P2-11 — Falha de bootstrap tem diagnóstico enganoso

O pai não verifica liveness do filho. Ao provocar acidentalmente uma falha de bootstrap do `spawn`, o pai esperou os **35 s inteiros** e relatou `browser operation timed out` — para um processo que nunca chegou a existir. Chamadas seguintes vazam `EOFError`/`BrokenPipeError` crus pelo handler genérico de `invoke`, e o modelo lê `EOFError:` sem explicação.

Relacionado: `playwright_available()` (`conversation_worker.py:30-31`) só checa o *pacote Python*, não se o binário do Chromium foi provisionado. Pacote presente + binário ausente = um processo por turno que sobe e morre.

### P3-12 — Política de rede estreita, com mensagem opaca

`NetworkPolicy` usa `allowed_schemes=("https",)` e `allowed_ports=(443,)`. Então `http://` devolve `scheme denied` e `https://host:8443` devolve `port denied` — mensagens sem contexto. Note que `_public_url` em `agent_tools.py` **aceita** `http`, então o agente é convidado a um caminho que o host recusa depois.

**Não confirmei problema de desempenho no route guard.** Medi: 29 requisições interceptadas, 0,02 s somados em `validate_url`, 0 bloqueios no site real. O `getaddrinfo` bloqueante por requisição é risco latente apenas em páginas que tocam muitos hosts novos e não cacheados; hoje não é gargalo. Registro para não superdimensionar.

---

## Correção proposta

### Fase 0 — parar de mentir para o agente (P0)

1. **Separar validar de exibir.** `validate_url` passa a devolver a URL com query e fragment preservados (normalizando apenas esquema/host/porta, com limite de tamanho). `sanitize_url` permanece como está, mas usada só para exibição, log e ledger — que é onde o segredo em query param realmente importa.
2. **Chave de cache = URL normalizada completa** (esquema+host+porta+path+query). E, no cache hit, marcar `payload["cached"] = True` e **não** reemitir `ARTIFACT_CREATED`. A timeline passa a mostrar trabalho real.
3. **Dar alças ao modelo.** Na observação, devolver junto do texto um inventário de elementos interativos, montado no filho com um único `evaluate`:
   `[e12] button "Criar minha loja" (#cta-top)` / `[e13] input[email] name="email" placeholder="seu@email"`.
   As ferramentas de interação passam a aceitar `ref="e12"` além de CSS. No extrator de texto, preservar `href` em links e `name/id/type/placeholder/label` em campos.
   Em `_single`, distinguir os dois casos: `matched nothing` vs `matched 23 elements — narrow it or use ref=`.
4. **Corrigir a inversão de orçamento e recuperar do timeout.** O orçamento do filho tem de ser estritamente menor que o do pai (ex.: `goto` 20 s + `load` 3 s + `networkidle` 2 s + `screenshot` 5 s ≈ 30 s contra 45 s do pai). E, no timeout do pai, **respawnar** o filho em vez de latch `_closed`, devolvendo ao modelo um erro recuperável ("a página demorou demais; o navegador foi reiniciado"). Subir o Chromium sob demanda, para o custo de launch não ser cobrado do orçamento da primeira navegação.

### Fase 1 — fazer o agente enxergar (P1)

5. `_model_sees_images` assume `True` quando a linha do catálogo está ausente, espelhando a decisão já tomada em `_model_calls_tools`. Quando for genuinamente `False`, encaminhar a captura pelo `visual_reader` que já existe (modelo de visão descreve em texto) — ou parar de capturar para o modelo e **dizer isso na saída da ferramenta**.
6. Adicionar um bloco `## Navegador` ao `build_system_prompt`: observar antes de interagir; o seletor sai da observação; repetir `browse_page` na mesma URL não traz informação nova; usar `ref=` do inventário; o que fazer quando um seletor casa com vários elementos.
7. Rotular a imagem pelo que ela é: `"Captura da página atual (<url>):"`.
8. Reportar falha de captura explicitamente no `content` e no `payload`; degradar o limite de tamanho (reduzir qualidade / recortar) em vez de levantar `ValueError` e perder o HTML.

### Fase 2 — isolamento e ciclo de vida (P2)

9. Uma aba por agente: um pool com N `BrowserContext` no mesmo processo filho, chaveado por `agent_id`. Alternativa mais barata e honesta: recusar o navegador a subagentes paralelos enquanto o pool não existir.
10. Manter a sessão viva entre turnos da mesma conversa, com expiração por ociosidade, para que login e fluxos de várias etapas sobrevivam.
11. Checar `process.is_alive()` antes de esperar o poll inteiro; verificar o **binário** do Chromium, não só o pacote; e mensagem acionável: "Chromium não provisionado — rode `scripts/install-browser.ps1`".

### Fase 3 — liberar o agente para fazer tudo (pedido explícito)

O bloqueio de submit hoje é uma lista fixa em código (`_is_form_submission_control`, `Enter` fora de `_PRESS_KEYS`, recusa de campo `password`). Lista fixa não escala: ela bloqueia o caso legítimo e não cobre o caso perigoso que ninguém enumerou. A proposta é trocar **lista fixa por nível de capacidade + consentimento**.

**Nível de capacidade** na configuração da conversa/usuário:

| Nível | O que libera |
| --- | --- |
| `read` | hoje: navegar, observar, capturar |
| `interact` | clicar, preencher, selecionar, marcar — sem submit |
| `full` | submit, `Enter`, upload, download, JS eval, múltiplas abas, cookies/perfil persistente |

**Mudanças concretas para `full`:**

- remover `_is_form_submission_control` do caminho de clique; adicionar `browser_submit(selector)` explícito; incluir `Enter` em `_PRESS_KEYS`;
- substituir a recusa fixa de campo `password` por **injeção de segredo**: o agente referencia `secret://nome`, o filho substitui pelo valor no momento do `fill`, e o segredo nunca entra no contexto do modelo;
- alargar a política de rede: permitir `http` e portas arbitrárias — **mantendo incondicional** a barreira de IP privado/loopback/link-local, que protege a rede do próprio usuário, não o site remoto;
- adicionar os verbos que o fluxo já precisa: `browser_back`, `browser_scroll`, `browser_wait_for(selector)`, `browser_new_tab` / `browser_switch_tab`, `browser_upload`, `browser_download`, e `browser_eval` só em `full`;
- persistir `storage_state` por conversa, para que um login sobreviva;
- auditar toda ação de nível `full` no ledger de ferramentas com URL, seletor e valores redigidos — a tubulação já existe.

**Duas coisas que eu recomendo manter incondicionais**, e a razão não é paternalismo:

1. **A barreira de IP privado/SSRF.** O alvo dela é o roteador, o `localhost` e a LAN do usuário — não o site que ele quer automatizar. Liberar submit não exige liberar isso.
2. **Confirmação via `ask_user` antes de um submit irreversível ou financeiro** (pagamento, exclusão, envio de mensagem em nome do usuário). O motivo é técnico: **o conteúdo da página é entrada não confiável no contexto do modelo**. Um site hostil pode escrever instruções na própria página e convencer o agente a submeter algo. A confirmação é a única defesa que sobrevive a isso, e `ask_user` já existe, já é batched e já pausa o turno — o encaixe é direto: o `ask_user` mostra URL, campos visíveis do formulário e valores a enviar.

Fora esses dois, tudo vira configuração. A decisão é sua; se quiser `full` sem confirmação nenhuma, o desenho acima suporta isso mudando um default.

## Ordem recomendada

Fase 0 primeiro, e sozinha já muda o comportamento observado: sem query descartada, sem cache colidido e com seletores obteníveis, o loop navegar→capturar→navegar deixa de ser a única saída disponível ao modelo. Fase 1 é barata e resolve o "ele nunca vê a captura". Fases 2 e 3 são trabalho de arquitetura e podem ser planejadas depois.

## Implementação — Fase 0

Todos os quatro achados P0 foram corrigidos, com testes novos (`tests/unit/browser/test_network_policy.py`, `tests/unit/browser/test_conversation_worker.py`, `tests/unit/agentic/test_agent_tools.py`) e validação real repetida contra `https://food.neectify.com` e `https://duckduckgo.com/html/`. Suíte completa: `1271 passed, 3 skipped`.

- **P0-1 (query descartada):** `security.py` ganhou `navigable_url()`, que preserva query e fragment (só remove credenciais), usada por `validate_url()` como valor de navegação. `sanitize_url()` continua igual, agora documentada como exclusiva para exibição/log. Validado: `https://duckduckgo.com/html/?q=neectify+food` agora chega em `https://html.duckduckgo.com/html/?q=neectify+food` com a query intacta.
- **P0-2 (cache colide URLs):** nova `_cache_key_url()` em `browser_tools.py`, que mantém a query (ao contrário de `_safe_display_url`, mantida só para exibição). `browse_page` e o ramo `observe` de `_browser_call` agora chaveiam por ela. Em cache hit, `_cached_navigation_outcome()` marca `payload["cached"] = True` e remove `artifacts` do payload, então a timeline para de reanunciar a mesma captura.
- **P0-3 (nenhum seletor obtível):** `conversation_worker.py` ganhou um script injetado (`_ELEMENT_INVENTORY_SCRIPT`) que marca cada elemento interativo visível com `data-orin-ref` e devolve uma linha descritiva por elemento (`[e12] button "Criar minha loja" href=...`) a cada observação. `_selector()` resolve `ref:eN` para `[data-orin-ref="eN"]`; `_single()` agora distingue "nenhum elemento" de "múltiplos elementos" na mensagem de erro. `_browser_outcome` em `agent_tools.py` anexa essa lista ao texto devolvido ao modelo, e as descrições das ferramentas `browse_page`/`browser_*` explicam o uso de `ref:eN`. Validado: 26 elementos detectados em `food.neectify.com`, e um clique real em `ref:e2` navegou a âncora `#como-funciona` — as quatro tentativas de seletor CSS "adivinhado" que falhavam antes da correção agora têm alternativa funcional.
- **P0-4 (timeout mata a sessão; orçamento invertido):** os tempos do filho foram reduzidos e amarrados a uma única constante (`WORST_CASE_OPERATION_MS`); o timeout do pai é derivado dela com margem (`default_parent_timeout_seconds()`), então os dois nunca voltam a divergir silenciosamente. `IsolatedConversationBrowser` agora reencarna o processo filho (`_recycle`) quando uma chamada estoura o timeout ou o pipe cai, em vez de fechar a sessão de vez; só `close()` explícito é permanente. A ferramenta recebe um erro recuperável ("a página demorou demais... a aba foi reiniciada") e pode tentar de novo na mesma resposta.

## Implementação — Fase 1

Os quatro achados P1 foram corrigidos com testes novos e sem regressão (suíte completa após esta fase: `1284 passed, 3 skipped`).

- **P1-5 (visão condicionada ao catálogo):** `_model_sees_images` em `chat.py` agora espelha exatamente `_model_calls_tools`: um catálogo ausente ou sem `input_modalities` é permissivo (`True`), e só uma lista explícita que omite `"image"` continua `False`. O teste que antes fixava o comportamento errado (`test_model_sees_images_is_false_when_the_catalog_has_no_row`) foi reescrito para `test_model_sees_images_stays_true_when_the_catalog_is_unrefreshed_or_empty`.
- **P1-6 (sem bloco de navegador no prompt):** `build_system_prompt` em `session.py` ganhou `## Browser`, condicionado a `browse_page` estar disponível: observar antes de interagir, `ref:eN` como seletor, `browse_page` repetido na mesma URL não traz novidade, como reagir a seletor com 0 ou vários matches, e (Fase 3) `browser_scroll`/`browser_wait_for`/`browser_back` e a regra de confirmação de `browser_submit`.
- **P1-7 (imagem rotulada como "arquivo"):** `_tool_result_messages` em `runtime.py` agora escolhe a legenda pelo nome da ferramenta (`result["name"]`, já disponível no dicionário de resultado): para as ferramentas de navegador vira `"Captura da página atual (<url>):"`; para tudo mais, mantém `"Conteúdo visual do arquivo solicitado:"`.
- **P1-8 (falha de captura silenciosa; PNG grande descarta o HTML):** `conversation_worker.py` ganhou `_capture_screenshot()`: timeout vira `screenshot_error` sem exceção; PNG acima do limite tenta de novo como JPEG comprimido antes de desistir; se ainda estourar, devolve screenshot vazio + `screenshot_error`, nunca perde o HTML. `agent_tools.py` reporta esse erro no `content` e no `payload["screenshot_error"]`, e usa a extensão/`media_type` corretos (`.jpg` quando a captura caiu para JPEG).

## Implementação — Fase 2

Os três achados P2 foram implementados e validados com Chromium real (suíte completa: `1299 passed, 3 skipped`).

- **P2-9 (aba compartilhada entre subagentes):** o host (`_host()`) agora mantém um dicionário de `_AgentPageState` chaveado por `agent_key`, cada um com sua própria `Page` (dentro do mesmo `BrowserContext`, então cookies/localStorage continuam compartilhados como em abas reais do mesmo perfil) e seu próprio cache de navegação. `IsolatedConversationBrowser` e a nova classe `AgentBrowserView` (em `browser_tools.py`) propagam `agent_key` em cada chamada; `TurnSession._toolset()` cria uma `AgentBrowserView` por agente (`main_agent_id` para o principal, `agent_id` do subagente em `_ask_agent`). `AgentBrowserView` não expõe `close()` de propósito — a view de um agente nunca pode derrubar a sessão que outro agente (ou um turno futuro) ainda está usando. Validado: dois `agent_key` navegando para `example.com`/`example.org` simultaneamente permanecem isolados; teto de `MAX_AGENT_PAGES=6` abas por processo, com erro recuperável ao estourar.
- **P2-10 (estado não sobrevive entre turnos):** nova `ConversationBrowserRegistry` em `browser_tools.py` mantém um `IsolatedConversationBrowser` por `conversation_id` vivo entre turnos, com expiração por ociosidade (`idle_seconds`, padrão 30 min) e teto de sessões concorrentes (`max_sessions`, padrão 6, com despejo do menos recentemente usado). `ChatWorker` adquire do registry em vez de criar direto; `run()` chama `registry.release(turn)` no fim de cada turno para não deixar um turno longo ser despejado por engano por outra conversa. O `close()` do toolset por turno virou um no-op para o navegador compartilhado (porque `AgentBrowserView` não tem `close()`); só o registry fecha o processo Chromium de fato, no despejo por ociosidade/teto ou em `discard()`/`close_all()`. Validado: duas aquisições sucessivas do registry devolvem a mesma instância e a segunda "observação" ainda vê a página da primeira, sem navegar de novo.
- **P2-11 (diagnóstico de bootstrap enganoso):** o pai agora faz polling em fatias de até 1s (`_poll_until_ready`) checando `process.is_alive()` a cada fatia, em vez de esperar o timeout inteiro para um processo que já morreu — a falha de bootstrap é detectada em ~1s, não em 35s. O filho distingue Chromium não provisionado (mensagem `"Executable doesn't exist"` do Playwright) de outra falha de motor, com mensagem acionável apontando para `scripts/install-browser.ps1`.

## Implementação — Fase 3 (escopo reduzido — ver seção seguinte)

Suíte completa após esta fase: `1328 passed, 3 skipped`, validada contra `food.neectify.com`, `duckduckgo.com/html/`, `example.com` e um IP privado (para confirmar que a barreira de SSRF continua incondicional).

- **Nível de capacidade:** `browser_capability_from_environment()` lê `AGENTOS_BROWSER_CAPABILITY` (`read`/`interact`/`full`; padrão e valor desconhecido caem em `interact`, que é exatamente o comportamento que a ferramenta sempre teve). Não existe UI ou coluna de banco para isso ainda — é uma variável de ambiente única para toda a instalação, mesmo padrão já usado por `search_client_from_environment()`. Propagada por `TurnSession.browser_capability` → `AgentToolset.browser_capability`, e para o processo filho via `IsolatedConversationBrowser(capability=...)` → `_host(connection, capability)`.
- **`browser_submit` em dois passos:** só é registrada como ferramenta quando `full`. A primeira chamada (sem `confirmed=true`) nunca clica — o host lê o formulário (`_describe_form`, via `locator.evaluate`) e devolve action/method/valores de cada campo visível (senha sempre mascarada como `[hidden]`) sem tocar na página; a segunda chamada, com `confirmed=true`, clica de verdade. O texto devolvido ao modelo instrui explicitamente a mostrar o preview ao usuário via `ask_user` antes de confirmar, e avisa que o conteúdo da própria página não é aprovação válida. O gate real está no host (`action == "submit"` só clica se `command.get("confirmed") is True`), não só no prompt — então mesmo que o modelo "esqueça" de perguntar, nada é submetido sem o argumento explícito. Validado de ponta a ponta contra o formulário de busca do DuckDuckGo: preview sem navegar, depois confirmado com resultado real de busca.
- **`Enter`:** fora do enum do schema e recusado pelo handler (`browser_press`) fora de `full`; disponível nos dois lugares em `full`.
- **Política de rede mais ampla em `full`:** `_policy_for("full")` libera `http` e remove a restrição de porta (`allowed_ports=()`), mantendo a barreira de IP privado/loopback/link-local **incondicional** — ela vive em `validate_url`, fora de qualquer campo de `NetworkPolicy`. Validado: em `full`, `http://example.com/` é permitido e `http://127.0.0.1:9999/` continua recusado.
- **Novos verbos, disponíveis em qualquer nível** (não são mutações de conteúdo, só ajudam a navegar): `browser_back` (`page.go_back`, não reenvia formulário), `browser_scroll` (`page.mouse.wheel`, ~800px, devolve observação fresca — elementos abaixo/acima da dobra ficam invisíveis e não clicáveis até rolar), `browser_wait_for` (espera um elemento atingir um estado — `visible`/`hidden`/`attached`/`detached` — em vez do modelo tentar `browser_observe` em loop; usa `.first.wait_for()`, então não exige match único como clique/preenchimento).

## O que ficou de fora da Fase 3 (decisão deliberada, não descuido)

A proposta original também pedia upload/download, múltiplas abas por agente, `browser_eval`, injeção de segredo para senha e `storage_state` persistido em disco. Não implementei essas cinco, e o motivo é o mesmo em todas: cada uma precisa de infraestrutura nova que merece desenho próprio, não uma adição apressada dentro desta already-grande passada.

- **Injeção de segredo (`secret://nome`) para preencher senha:** exige um cofre de segredos novo — Orin hoje criptografa credencial de provider (`api_key_ciphertext`), não há um cofre genérico de "segredo nomeado pelo usuário" para o agente referenciar. Construir isso mal (guardando em texto puro, por exemplo) seria pior que não ter a funcionalidade. Continua recusado: `fill` em campo `type=password` levanta erro.
- **`storage_state` persistido em disco entre reinícios do app:** a Fase 2 já resolve "sobrevive entre turnos" (o processo Chromium fica vivo em memória enquanto a conversa está ativa). Persistir cookies/sessão em disco para sobreviver a um *restart* do Orin é guardar credencial de sessão em repouso — quero a mesma criptografia já usada para credencial de provider, não uma versão nova e menos revisada.
- **`browser_new_tab` / `browser_switch_tab`:** o modelo de `agent_key` (uma aba por agente) já existe e funciona; abas extras *dentro* de um mesmo agente pedem uma segunda dimensão de chaveamento (`agent_key` + `tab_id`) que toca `_AgentPageState`, o cliente e `agent_tools.py` de novo — dá para encaixar depois sem redesenhar o que já foi feito.
- **`browser_upload` / `browser_download`:** fronteira com o sistema de arquivos do workspace; upload precisa decidir de onde no workspace o agente pode ler, download precisa do mesmo limite de tamanho/artefato que a captura de tela já usa. Mecanicamente parecido com o que existe, mas quis não empilhar mais uma decisão de design nesta passada.
- **`browser_eval` (JavaScript arbitrário):** deliberadamente o mais arriscado dos cinco — é um primitivo de leitura/escrita irrestrita da página, mais perto de "sandbox escape" do que os outros verbos. Prefiro que isso seja uma decisão discutida à parte, não um item entre vários numa lista.

Nenhum desses cinco está bloqueando o resto: capacidade `full` sem eles já cobre login manual assistido, preenchimento e envio de formulário com confirmação, e navegação mais livre — só não cobre esses cinco casos específicos.
