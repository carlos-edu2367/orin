# Anexos de arquivo no chat — Design

## Objetivo

Permitir que a pessoa envie arquivos ao agente pelo composer, com ou sem texto,
e que o agente consiga lê-los mesmo quando o modelo do turno não enxerga
imagens. Arquivos visuais — imagens e PDFs escaneados — passam por uma leitura
visual própria do Orin quando o modelo escolhido é somente texto.

## Decisões estruturantes

1. **Um anexo é um arquivo do workspace.** O upload é promovido para
   `uploads/` dentro do workspace efetivo da conversa, e o agente o manipula com
   as ferramentas que já existem (`read_file`, `run_command`, artefatos,
   preview, download).
2. **O motor de leitura visual é um modelo com visão**, não um OCR nativo
   embutido. Não há dependência de Tesseract/ONNX no instalador, a qualidade em
   layout, tabela e manuscrito é superior, e a leitura pode ser 100% local
   quando o modelo escolhido é um Ollama local.
3. **A leitura é sob demanda, como ferramenta.** `view_file` é a porta única;
   o agente decide quando olhar, e a ferramenta serve para qualquer arquivo do
   workspace, não só para o anexo do turno.
4. **Modelo com visão recebe a imagem de verdade.** Quando o modelo do turno
   enxerga, a ferramenta injeta a imagem no contexto em vez de transcrevê-la.
5. **Modelo que não chama ferramentas recebe a transcrição pronta.** É a única
   exceção ao item 3, e reaproveita exatamente o mesmo pipeline.
6. **O modelo de leitura visual é escolhido automaticamente, com override.**

## Fluxo do arquivo

### Rotas

- `POST /v1/uploads` (multipart) grava em
  `data/uploads/staging/<user_id>/<upload_id>/<nome-saneado>`, detecta o tipo
  por *magic bytes* e extensão, valida tamanho e allowlist, e devolve
  `{upload_id, filename, media_type, kind, bytes}` com
  `kind ∈ {text, image, pdf, office}`.
- `DELETE /v1/uploads/{upload_id}` remove um arquivo antes do envio.
- `POST /v1/conversations` e `POST /v1/conversations/{id}/messages` aceitam
  `attachments: [upload_id]`. Permanecem JSON, com a idempotência atual
  preservada.

O staging existe porque na primeira mensagem a conversa ainda não existe, e
porque a pessoa precisa ver e remover o anexo antes de enviar.

### Promoção para o workspace

Ao criar o turno, o gateway resolve o workspace efetivo — `projects.workspace_id`
ou o próprio `conversation_id`, com `workspace_roots` quando há pasta local
anexada — e move cada arquivo do staging para `uploads/` dentro dele. Colisão de
nome vira `nota (2).pdf`.

A ordem é mover, depois criar o turno, e limpar os arquivos movidos se a criação
falhar. O inverso não funciona: o publisher pode reivindicar o turno em
milissegundos e o worker precisa encontrar o arquivo no disco.

Quando há pasta local anexada, o upload cai em `uploads/` dentro da pasta real
da pessoa. É a consequência direta de o agente poder usar `read_file` e
`run_command` no arquivo; o composer informa onde o arquivo será gravado.

Um coletor apaga o staging com mais de 24 horas.

### Persistência

A migration `0021_message_attachments` cria `conversation_message_attachments`
com `attachment_id`, `message_id`, `conversation_id`, `user_id`, `path`
(relativo ao workspace), `original_name`, `media_type`, `kind`, `bytes` e
`created_at`.

`PostgresChatStore.create()` recebe os anexos, insere as linhas e passa a
aceitar mensagem em branco quando existe pelo menos um anexo — hoje ela rejeita.
O título da conversa cai no nome do primeiro arquivo quando não há texto.

`get()` devolve `attachments` por mensagem, para o cliente reconstruir a
conversa após um reload. `history_for_turn()` faz o join e acrescenta ao
`content` daquela mensagem uma linha `[anexos: uploads/nota.pdf (PDF, 240 KB)]`,
para que o modelo continue sabendo que o arquivo existe em turnos posteriores.

## Pipeline de leitura

Pacote novo `src/agentos/reading/`, sem nenhum conhecimento de provider:

- `extract.py` — extração de texto nativo: pypdf para PDF, python-docx,
  openpyxl, python-pptx e texto puro. Devolve
  `ExtractedText(text, truncated, pages_without_text)`. Nenhum modelo é chamado
  aqui, e é por onde a maioria dos PDFs reais sai resolvida.
- `render.py` — pypdfium2 rasteriza apenas as páginas sem texto; Pillow
  normaliza a imagem (lado maior 1568px, JPEG q85) e recusa arquivos acima de um
  limite de pixels.
- `vision.py` — `VisionReader.transcribe(images, instruction) -> str`, uma
  chamada única, não-streaming, com timeout e saída limitada.
- `selection.py` — escolhe o modelo de leitura visual nesta ordem: override de
  Settings, mesmo provider do turno, Ollama local, qualquer outro modelo do
  catálogo autorizado cujo `input_modalities` contenha `image`. Sem candidato, o
  erro é explícito e acionável.

## A ferramenta

`view_file(path, pages?, question?)` em `agent_tools.py`, com a mesma fronteira
de segurança de `ConversationWorkspace.resolve`:

1. Texto, Office ou PDF com camada de texto devolvem o texto extraído, sem
   custo de modelo.
2. Imagem ou página escaneada, com modelo do turno que enxerga: o `ToolOutcome`
   carrega as imagens e o runtime emite uma mensagem `user` com blocos de imagem
   logo após o resultado da ferramenta.
3. Imagem ou página escaneada, com modelo somente texto: `VisionReader`
   transcreve e a ferramenta devolve o texto, com atividade visível
   ("Leitura visual · nota.pdf p.2 · qwen2.5-vl").

A mensagem `user` extra existe porque resultado de ferramenta com imagem só
funciona na Anthropic; uma mensagem `user` logo em seguida funciona nos três
transportes suportados.

## Projeção multimodal por provider

`provider_content.py`, ao lado dos builders atuais, converte a representação
neutra `{"type": "image", "media_type": ..., "data": ...}`:

| Provider | Formato |
| --- | --- |
| Anthropic | `{"type": "image", "source": {"type": "base64", ...}}` |
| OpenAI-compat | `{"type": "image_url", "image_url": {"url": "data:...;base64,..."}}` |
| Ollama | mensagem com `images: [base64]` |

`_with_cached_tail` já trata `content` em lista, então o cache da Anthropic
segue funcionando.

## Exceção do modelo sem tool-calling

`TurnSession` lê `capabilities` e `input_modalities` na mesma consulta a
`provider_model_catalog` que já resolve o context window do turno. Se o modelo
não chama ferramentas e o turno tem anexo visual, o mesmo pipeline roda antes da
primeira chamada ao provider e o texto é injetado junto da mensagem do usuário.

## Interface

O composer ganha um botão de anexo ao lado do botão de pasta, arrastar-e-soltar
sobre a área do chat e colagem de imagem do clipboard. Cada arquivo vira um chip
com miniatura local, nome, tamanho, progresso, remoção e erro individual com
repetição. O envio passa a ser permitido sem texto quando há anexo.

O aviso do composer depende do modelo escolhido:

| Modelo do turno | Aviso |
| --- | --- |
| Enxerga | nenhum |
| Texto, chama ferramentas | "Este modelo não enxerga; o Orin vai ler com *&lt;modelo&gt;*." |
| Texto, sem ferramentas | o mesmo, mais "a leitura acontece antes do envio" |
| Nenhum modelo de visão disponível | "Nenhum modelo de leitura visual disponível" com atalho para Settings |

A mensagem do usuário renderiza os anexos com `WorkspaceFileCard`, que já tem
preview, download e abrir no sistema, acrescentando miniatura para imagem. A
leitura visual aparece na timeline como atividade agrupada, como as demais
ferramentas. Settings ganha um seletor "Modelo de leitura visual" com a opção
*Automático*.

## Limites e segurança

- 10 arquivos por mensagem, 25 MB por arquivo, 50 MB por turno, 4 imagens por
  chamada de `view_file`, 20 páginas de PDF por chamada.
- Allowlist de tipos: texto e código, `png`, `jpg`, `webp`, `gif`, `pdf`,
  `docx`, `xlsx`, `pptx`. Validada por *magic bytes*, não por extensão.
- Executáveis e tipos não reconhecidos são recusados: o workspace tem
  `run_command` real. Zip fica fora da v1 pelo mesmo motivo — extração recursiva
  é superfície de ataque própria.
- Nome saneado contra travessia, caracteres de controle e nomes reservados do
  Windows.
- A rota de upload usa a mesma autenticação de peer loopback e a mesma proteção
  de mutação das demais rotas POST, e o arquivo passa pela quota de workspace
  que já existe.
- A transcrição envia o conteúdo do arquivo ao provider do modelo de leitura. Se
  ele for de nuvem, o arquivo sai da máquina. Por isso o modo *Automático*
  prefere um Ollama local quando existir, e o aviso do composer sempre nomeia o
  modelo que vai ler.

## Faseamento

1. Upload, staging, promoção, persistência do anexo, envio sem texto, chips no
   composer e cards na mensagem. Já entrega valor: o agente lê texto, código e
   csv com o `read_file` existente.
2. `extract.py` e `view_file` para PDF com camada de texto e Office. Sem modelo
   e sem dependência binária — pypdf, python-docx, openpyxl e python-pptx são
   wheels puros.
3. Projeção multimodal nativa em `provider_content.py` para modelo com visão.
4. `VisionReader`, seleção, Settings e rasterização de PDF escaneado com
   pypdfium2 e Pillow — os únicos binários novos.
5. Pré-execução da leitura para modelo sem tool-calling.

## Testes

- **Unitários**: saneamento e colisão de nome; allowlist por conteúdo; promoção
  staging→workspace, inclusive com pasta local; mensagem em branco com anexo;
  título derivado do arquivo; projeção de imagem nos três providers; as quatro
  regras de seleção do modelo visual e o caso sem candidato; extração por
  formato; fallback de página sem texto; limites.
- **Integração** com provider falso: turno completo com imagem em modelo com
  visão, verificando a mensagem `user` extra, e em modelo somente texto,
  verificando a transcrição.
- **Frontend**: chips, colagem, arrastar-e-soltar, envio sem texto, aviso por
  capacidade do modelo, e e2e com backend mockado.
- **Segurança**: travessia pelo nome do arquivo, symlink, tipo mascarado por
  extensão, arquivo acima do limite, bomb de pixels.
