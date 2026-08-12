# Pasta local como workspace do chat/projeto — Design

**Data:** 2026-08-12
**Escopo:** seleção de uma pasta da máquina como raiz de trabalho do agente, a
partir do chat ou do projeto, com seletor nativo do sistema operacional.

## Objetivo

Permitir que a pessoa aponte um chat ou projeto para uma pasta que já existe na
sua máquina — um repositório, uma pasta de documentos, um drive inteiro — e que
o agente passe a trabalhar ali, no lugar da pasta gerenciada pelo Orin.

## Modelo

Uma pasta local é uma **raiz alternativa para um `workspace_id`**.

O `workspace_id` efetivo já é a chave da resolução hoje: uma conversa solta usa
o próprio `conversation_id`; um chat dentro de um projeto usa o `workspace_id`
do projeto, que é como os chats de um projeto compartilham arquivos. A pasta
local é gravada contra essa mesma chave, então o botão define a pasta do
workspace *efetivo*: num chat solto afeta só aquele chat, num chat de projeto
afeta o projeto inteiro. A interface diz isso antes de confirmar.

Sem pasta gravada, o comportamento é o atual: `orin_paths().workspaces/<workspace_id>`.
Com pasta gravada, a pasta escolhida **é** a raiz — nenhum subdiretório é criado
abaixo dela.

Nada se move ao anexar. A pasta gerenciada continua intacta onde está, e
desanexar restaura o estado anterior com o conteúdo preservado. Copiar arquivos
para dentro de uma pasta que já é da pessoa — possivelmente um repositório git —
seria a única ação irreversível desta funcionalidade, e quase nunca é o que se
quer: o caso normal é "trabalhe nesse projeto que já existe". Quem quiser copiar
pede ao agente, que tem as ferramentas.

## Persistência

Tabela nova `workspace_roots`:

| Coluna | Tipo | Regra |
|---|---|---|
| `workspace_id` | texto | chave primária; é o workspace efetivo |
| `user_id` | texto | dono; toda leitura e escrita confere |
| `root_path` | texto | caminho absoluto normalizado |
| `created_at` | timestamp | |
| `updated_at` | timestamp | |

Uma tabela em vez de colunas em `projects` e `conversations` porque a chave é
exatamente o workspace efetivo: uma função de lookup serve o gateway, o worker
de chat e a API de configuração, com um caminho de código só. Migração nova na
sequência de `src/agentos/persistence/postgres/migrations/versions`.

## Resolução da raiz

`agentic/workspace.py` ganha duas coisas:

- `ConversationWorkspace.at_root(path)` — construtor alternativo que usa o
  caminho como raiz final, sem anexar `workspace_id` e sem criar a pasta.
- `resolve_workspace(workspace_id, *, managed_root, local_root)` — devolve o
  `ConversationWorkspace` certo para os dois casos.

O resto do `ConversationWorkspace` não muda. Containment, rejeição de `..` e de
symlink que aponta para fora continuam valendo, agora relativos à pasta
escolhida. As rotas de preview, download e "abrir no sistema" do spec de menções
de arquivo passam a funcionar dentro da pasta local sem alteração, porque saem
do mesmo helper.

Três pontos de fiação:

- `api/gateway.py` — `conversation_workspace()` consulta a pasta local do
  workspace efetivo antes de construir.
- `conversations/chat.py` — a query que monta o turno já traz
  `project_workspace_id`; passa a trazer também o `root_path` do workspace
  efetivo.
- `agentic/session.py` — `TurnSession` usa o `local_root` do turno quando ele
  existe.

## API

Três rotas de conversa, todas exigindo sessão autenticada, proteção CSRF e
autorização de mutação, e todas confirmando que a conversa pertence ao usuário
antes de qualquer efeito.

`POST /v1/conversations/{conversation_id}/workspace/inspect`

Sem `path` no corpo, abre o seletor nativo e inspeciona o que foi escolhido. Com
`path`, inspeciona o caminho digitado. A resposta é a mesma nos dois casos:

```json
{"path": "D:/projetos/site", "exists": true, "is_directory": true,
 "writable": true, "entry_count": 12, "risk": "none"}
```

Cancelar o diálogo responde `{"cancelled": true}`. O seletor nativo só é aceito
em requisição loopback; de outra máquina a resposta é `dialog_unavailable`, e é
nesse caso que o campo de texto assume.

`PUT /v1/conversations/{conversation_id}/workspace`

Corpo `{"path": "...", "acknowledged_risk": false}`. Grava a pasta. Se o risco
for diferente de `none` e `acknowledged_risk` não vier, responde 409 com o código
de risco e **não grava** — é o que dispara a segunda confirmação no cliente.

`DELETE /v1/conversations/{conversation_id}/workspace`

Desanexa e volta para a pasta gerenciada. Idempotente.

`GET /v1/conversations/{conversation_id}` passa a incluir:

```json
{"workspace": {"kind": "local", "path": "D:/projetos/site",
  "folder_name": "site", "scope": "project", "project_name": "Site novo"}}
```

para o botão pintar o estado certo no primeiro paint, sem uma requisição extra.

## Seletor nativo

O navegador não entrega caminho absoluto — nem `showDirectoryPicker()` entrega —
então quem abre o diálogo é o backend, que roda na mesma máquina. É o mesmo
precedente de `os.startfile` em `agentic/file_preview.py`.

O diálogo roda em **subprocesso com timeout**, nunca no processo da API, para que
uma janela esquecida aberta não prenda um worker:

- Windows: PowerShell em `-STA` com `System.Windows.Forms.FolderBrowserDialog`.
- macOS: `osascript -e 'choose folder'`.
- Linux: `zenity --file-selection --directory`, com `kdialog --getexistingdirectory`
  como segunda tentativa.

Saída vazia ou código de cancelamento vira `cancelled`. Binário ausente, timeout
ou falha de execução viram `dialog_unavailable`, com o campo de texto como
alternativa visível na interface — o que mantém a funcionalidade utilizável em
sessão sem desktop, navegador remoto ou diálogo que abriu atrás da janela.

## Risco, sem bloqueio

A escolha da pasta nunca é recusada por política. A máquina é da pessoa, e pedir
para o agente analisar o disco inteiro é um caso legítimo. O que muda com o
risco é o peso da confirmação.

Classificação:

| Código | Quando |
|---|---|
| `drive_root` | raiz de drive ou do filesystem (`C:\`, `/`) |
| `system` | `C:\Windows`, `Program Files`, `/etc`, `/usr`, `/bin`, `/System`, `/Library` |
| `home_root` | exatamente o diretório home do usuário |
| `orin_data` | dentro do diretório de dados do próprio Orin |
| `none` | qualquer outra pasta |

Risco `none` confirma num passo. Qualquer outro exige um segundo passo que nomeia
a consequência de forma concreta — "o agente vai poder criar, editar e apagar
arquivos em `C:\`, com shell real, sem pedir permissão a cada passo" — e um botão
que nomeia a ação em vez de um "OK" com foco padrão. O objetivo é que a escolha
ampla seja possível e deliberada, nunca acidental.

Pasta inexistente, caminho que não é diretório e pasta sem permissão de escrita
não são política e não têm o que confirmar: são erro simples, com mensagem
direta. A funcionalidade não cria pastas.

## Interface

Um botão na barra do composer, junto dos seletores de provider e modelo: glyph
de pasta com rótulo. Sem pasta local, o rótulo é "Pasta". Com pasta local, é o
nome da pasta, truncado, com o caminho completo no `title`.

O clique abre um popover pequeno ancorado no botão, com o estado atual, o botão
"Escolher pasta…", um campo de caminho com "Usar" e, quando há pasta anexada,
"Remover". A confirmação mostra o caminho completo e o `entry_count` do primeiro
nível, para a pessoa reconhecer a pasta que escolheu antes de confirmar. Um
caminho de risco troca o conteúdo do popover pelo estado de confirmação descrito
acima. Num chat de projeto, a confirmação nomeia o projeto e
diz que a pasta vale para todos os chats dele.

O caminho absoluto aparece na interface, contrariando a regra de não expor
caminho do host que vale para o spec de menções de arquivo. A regra lá protege
contra vazamento de detalhe do servidor em mensagem de erro; aqui a pasta é da
pessoa e foi escolhida por ela, e esconder o caminho só dificultaria conferir que
a pasta certa foi selecionada.

## O agente

`build_system_prompt` já recebe `workspace_hint`. Com pasta local, a dica passa a
dizer que o workspace é uma pasta real da pessoa, com arquivos que já eram dela,
e que o agente não deve reorganizar, mover ou apagar nada que não tenha sido
pedido. É uma frase, e é o que separa "trabalhe nesse repositório" de um agente
arrumando a casa por conta própria.

## Fluxo

1. A pessoa clica no botão de pasta no composer e escolhe "Escolher pasta…".
2. O backend abre o seletor nativo em subprocesso e devolve caminho, metadados e
   risco. Se o diálogo não estiver disponível, o campo de texto assume.
3. Risco `none` confirma direto; risco maior mostra a confirmação nomeada.
4. `PUT` grava a pasta contra o `workspace_id` efetivo.
5. O próximo turno resolve a raiz para a pasta escolhida; `write_file`,
   `edit_file`, `run_command`, busca no workspace e as rotas de arquivo passam a
   operar ali.
6. "Remover" apaga o registro e o chat volta à pasta gerenciada, com o conteúdo
   anterior intacto.

## Testes e critérios de aceite

Python, unidade: normalização e validação de caminho; classificação de risco por
plataforma; `resolve_workspace` devolvendo a pasta local sem anexar
`workspace_id`; fallback gerenciado inalterado quando não há registro;
containment e rejeição de symlink continuando válidos sob raiz local; ownership
do workspace na leitura e na escrita.

Python, seletor nativo: cancelamento vira `cancelled`; timeout e binário ausente
viram `dialog_unavailable`; o diálogo nunca roda no processo da API.

API: autenticação e CSRF nas três rotas; diálogo nativo recusado fora de
loopback; 409 com código de risco quando falta `acknowledged_risk`; gravação com
o reconhecimento presente; `DELETE` restaurando a pasta gerenciada e sendo
idempotente; chat de projeto resolvendo a raiz do projeto; `GET` da conversa
trazendo o bloco `workspace`.

React: os dois estados do rótulo do botão; fluxo do popover; confirmação de risco
exigindo clique explícito no botão nomeado; campo de texto disponível quando o
diálogo não está; erros de pasta inexistente e sem permissão renderizados.

Aceite: apontar um chat para uma pasta com um projeto existente e pedir uma
alteração deve produzir a alteração naquela pasta, visível fora do Orin; apontar
para `C:\` deve ser possível e exigir a confirmação nomeada; remover a pasta deve
devolver o chat aos arquivos que ele tinha antes.

## Decisões

- A pasta substitui a raiz em vez de ser montada ao lado dela: é o que "workspace
  local para o agente trabalhar" descreve, e cabe na arquitetura trocando apenas
  a raiz resolvida, sem tocar em nenhuma ferramenta.
- A pasta é presa ao workspace efetivo, não a uma segunda regra de resolução:
  chats de um projeto continuam compartilhando arquivos, que é o motivo de o
  projeto existir.
- Nenhuma pasta é bloqueada. Risco alto muda a confirmação, não a permissão.
- O diálogo nativo é restrito a loopback; o campo de caminho é o que mantém a
  funcionalidade utilizável quando ele não está disponível.
