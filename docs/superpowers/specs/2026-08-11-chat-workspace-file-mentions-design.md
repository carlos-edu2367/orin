# Menções de arquivos do workspace no chat — Design

## Objetivo

Permitir que agentes façam referência a arquivos do workspace em suas respostas
e que a pessoa usuária abra um preview, faça download ou abra o arquivo no
aplicativo padrão do sistema operacional.

## Escopo e experiência

O agente passa a receber instruções para mencionar os arquivos que produziu,
priorizando entregáveis finais e incluindo scripts auxiliares quando forem
relevantes. A sintaxe canônica será Markdown:

```markdown
[Abrir index.html](workspace://index.html)
[Baixar relatorio.pdf](workspace://relatorio.pdf)
```

O cliente reconhece apenas o esquema `workspace:` em mensagens de assistente.
Ele o transforma em um cartão compacto com nome, tipo e três ações: Preview,
Baixar e Abrir no sistema. Links Markdown normais conservam o comportamento
atual. O preview é um painel/modal do chat: HTML abre em iframe sem permissões
de script ou mesma origem; PDF, imagens, texto e mídia usam o visualizador
adequado do navegador; tipos sem prévia exibem uma mensagem e preservam as duas
outras ações.

Além da instrução ao agente, cada chamada concluída de `write_file`,
`edit_file` ou `run_command` compara o inventário seguro do workspace antes e
depois da operação. Arquivos novos ou alterados são emitidos como atividades de
artefato ligadas ao turno. Ao terminar a resposta, o cliente mostra esses
artefatos como cartões de apoio, deduplicados contra menções explícitas. Isso
garante que, por exemplo, `gerar_pdf.py` e o PDF criado pelo comando sejam
acessíveis mesmo se o agente só mencionar o PDF.

## Backend e segurança

Novas rotas autenticadas de conversa servem um arquivo por caminho relativo e
acionam a abertura local. Antes de qualquer acesso, a rota confirma que a
conversa pertence ao usuário autenticado, resolve o workspace efetivo (da
conversa ou do projeto) e usa `ConversationWorkspace.resolve`; caminhos vazios,
absolutos, com travessia e symlinks externos retornam erro sem tocar no disco.

`GET /v1/conversations/{conversation_id}/files/{path}` responde com o conteúdo
e `Content-Type` obtido de uma lista de MIME confiável, `X-Content-Type-Options:
nosniff` e `Content-Disposition` `inline` ou `attachment` conforme a ação.
`POST /v1/conversations/{conversation_id}/files/{path}/open` exige a mesma
sessão, proteção CSRF e autorização de mutação, e abre exclusivamente o arquivo
resolvido no aplicativo padrão do sistema. Falhas de arquivo inexistente,
formato não pré-visualizável ou abertura local são respostas explícitas e
mostradas no cartão; não viram caminhos absolutos ou detalhes do host no chat.

O servidor não lista nem expõe o workspace inteiro por esta API. A detecção de
artefatos usa apenas os nomes relativos obtidos da varredura do próprio
`ConversationWorkspace`, possui limite de entradas e tamanho, e nunca tenta
ler o conteúdo binário para classificá-lo.

## Componentes

- `ConversationWorkspace`: inventário raso/recursivo limitado e metadados de
  arquivo para que a detecção e as rotas compartilhem a mesma fronteira segura.
- `AgentToolset`: captura o snapshot antes/depois de ferramentas que podem
  escrever e inclui `artifacts` relativos no `ToolOutcome`.
- `TurnSession`: publica cada artefato detectado como atividade pública;
  `build_system_prompt` documenta a sintaxe `workspace://` e pede a menção de
  entregáveis finais.
- API de conversa: valida propriedade da conversa, resolve o workspace
  compartilhado de projeto quando aplicável, serve/download e solicita abertura
  local.
- Cliente de conversas: normaliza artefatos dos eventos e constrói URLs da API
  sem aceitar caminhos absolutos.
- `WorkspaceFileCard` e `WorkspaceFilePreview`: renderizam menções e artefatos
  detectados, com controles acessíveis e tratamento de erro.

## Fluxo de dados

1. Um agente cria `index.html` por `write_file` ou gera `relatorio.pdf` com
   `run_command`.
2. A ferramenta devolve os caminhos relativos detectados; o runtime registra
   uma atividade `artifact.created` para cada arquivo novo/modificado.
3. O agente responde com a menção Markdown do resultado relevante.
4. O renderizador converte `workspace://...` em um cartão e a linha do tempo
   acrescenta os artefatos detectados que não tenham cartão equivalente.
5. Preview e download usam a rota de leitura; Abrir no sistema chama a rota
   protegida de abertura e mostra sucesso ou uma falha compreensível.

## Testes e critérios de aceite

- Testes unitários Python cobrem inventário/diferença, rejeição de travessia e
  symlink, metadados de artefatos e instrução de prompt.
- Testes de API cobrem propriedade, workspace de projeto, MIME/disposition,
  download, abertura protegida por CSRF e ausência de caminhos do host nos
  erros.
- Testes React cobrem menção `workspace://`, preview seguro de HTML/PDF, ações
  de download/abertura, fallback de tipo desconhecido e deduplicação de
  artefatos da timeline.
- Um agente que crie `index.html` deve permitir visualizá-lo no chat; um agente
  que escreva e execute `gerar_pdf.py` deve tornar tanto `relatorio.pdf` quanto
  o script acessíveis no chat, sem qualquer arquivo fora do workspace poder ser
  servido ou aberto.

## Decisões

- A menção explícita continua sendo a forma principal de comunicar o resultado;
  a detecção automática é uma proteção contra omissões, não uma lista completa
  de arquivos.
- O preview de HTML permanece isolado mesmo para conteúdo criado pelo agente,
  pois é código não confiável no mesmo contexto da interface.
- Abertura no sistema é uma ação explícita iniciada pela pessoa usuária e só
  existe no servidor local autenticado; browsers remotos podem continuar usando
  preview/download.
