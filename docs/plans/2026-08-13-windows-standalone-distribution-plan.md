# Plano: distribuição Windows standalone do Orin

## Objetivo

Publicar uma primeira distribuição para **Windows 10/11 x64** que uma pessoa
instala por PowerShell e usa sem ter Python, Node.js, Docker, PostgreSQL ou
Redis instalados. A instalação cria a configuração local segura, disponibiliza
`orin` no `PATH` e, ao terminar, pergunta se deve criar um atalho que abre a
versão desktop.

Comando público proposto (o repositório remoto confirmado usa o proprietário
`carlos-edu2367`, com hífen):

```powershell
irm https://github.com/carlos-edu2367/orin/releases/latest/download/install.ps1 | iex
```

Também documentaremos a alternativa auditável, sem execução remota direta:

```powershell
irm https://github.com/carlos-edu2367/orin/releases/latest/download/install.ps1 -OutFile .\install-orin.ps1
Get-Content .\install-orin.ps1
.\install-orin.ps1
```

## Estado confirmado no código

- O launcher já é um binário multi-comando em potencial: os serviços são
  iniciados por `orin internal-service <nome>`, portanto um `orin.exe`
  congelado pode se relançar sem um interpretador Python externo.
- `OrinPaths` já mantém configuração, dados, logs, cache e estado de execução
  fora da instalação. Isso permite trocar uma versão sem tocar em conversas,
  workspaces ou credenciais cifradas.
- A interface já é servida pela API em loopback e o Electron é somente uma
  casca segura sobre essa mesma interface; não haverá uma segunda aplicação
  web nem carregamento de `file://`.
- A persistência atual é PostgreSQL e a fila atual é Redis/ARQ. O publisher e
  o worker são processos separados. Remover apenas Redis não basta para uma
  instalação sem Docker: PostgreSQL também precisaria continuar externo.
- Não há workflow GitHub Actions neste checkout. O atual
  `scripts/install-orin.ps1` prepara um ambiente de desenvolvimento e exige
  Python, Node e Docker; ele não será reaproveitado como instalador publicado.
- O browser dos agentes é provisionado hoje pelo Playwright. Seu Chromium deve
  ser empacotado; baixar o browser no computador do usuário não atende ao
  objetivo.
- OmniRoute é opcional, mas hoje é um programa Node externo. Não podemos
  deixar a opção standalone pedir npm/Node ao usuário.

## Decisões de arquitetura propostas

### 1. Banco local e fila: SQLite, não serviços embarcados

Usar um único banco SQLite em
`%LOCALAPPDATA%\Orin\data\orin.db`, configurado com WAL, `foreign_keys=ON`,
`busy_timeout` e conexões curtas. A primeira versão terá **um processo local
de worker** e concorrência de turnos limitada de forma explícita e testada; o
agente continua sem limite de ações/interações, mas não disputará o arquivo do
banco com oito execuções paralelas.

O banco será a fila durável:

- a API cria o turno e a linha de dispatch na mesma transação;
- o worker faz polling com backoff e reivindica um turno por transição atômica
  de estado, sem Redis e sem ARQ;
- os heartbeats, cancelamento, recuperação após crash e watchdog continuam
  persistidos no banco;
- API e worker permanecem separados, preservando a regra de que uma chamada
  longa ao provider não bloqueia HTTP/SSE.

Não usar uma fila somente em memória. Isso perderia turnos em reinício e
invalidaria os contratos atuais de recuperação. Não embarcar PostgreSQL/Redis
como processos ocultos: isso aumenta manutenção, binários, portas e superfície
de atualização sem necessidade para o perfil local de usuário único.

Os adaptadores podem manter seus nomes atuais durante a migração para reduzir
o diff, mas os contratos e a composição passarão a ser neutros de dialeto. O
trabalho inclui uma auditoria de cada uso PostgreSQL-específico, das migrations
e das condições de corrida antes de declarar compatibilidade SQLite.

### 2. Um pacote versionado e imutável por release

Cada release conterá, no mínimo:

```text
Orin-<versão>-windows-x64.zip
  Orin Desktop.exe
  resources/
    runtime/
      orin.exe
      _internal/                 # runtime Python congelado e dependências nativas
      web/                       # frontend compilado
      migrations/                # schema SQLite versionado
      playwright/                # Chromium usado pelos agentes
      [omniroute/ se suportado]
```

O pacote é instalado em
`%LOCALAPPDATA%\Programs\Orin\<versão>`. Um ponteiro `current` e o shim
`%LOCALAPPDATA%\Orin\bin\orin.cmd` levam sempre ao mesmo release. O atalho
de área de trabalho deve apontar para
`...\Programs\Orin\current\resources\runtime\orin.exe --desktop`, e não
diretamente para `Orin Desktop.exe`: assim o `Supervisor` continua sendo a
única autoridade que inicia API, worker, readiness e Electron.

O Electron recebe o runtime em `extraResources`; o perfil instalado resolve
migrations, web e executável relativo a `process.resourcesPath`, nunca a um
checkout, `node_modules` ou diretório atual.

### 3. Instalação, configuração e recuperação

O novo `install.ps1`, publicado como asset do release, deve:

1. Obter o manifesto versionado da release desejada ou da última estável.
2. Baixar o ZIP para um diretório temporário, verificar SHA-256 e, nas releases
   assinadas, validar a assinatura Authenticode e o certificado esperado.
3. Extrair em `<versão>.staging`, verificar a presença e a versão de
   `orin.exe`, `Orin Desktop.exe`, web, migrations e Chromium.
4. Executar uma inicialização não interativa do próprio `orin.exe`. Ela cria
   as raízes por usuário, o arquivo de configuração com URL SQLite e uma chave
   Fernet nova; a chave fica restrita ao usuário e nenhuma chave de provider é
   pedida ou gravada pelo instalador.
5. Promover a pasta somente após essas verificações. Na atualização, conservar
   a instalação anterior até a nova ficar ativa; se a promoção falhar, restaurar
   `current` e informar o caminho do log.
6. Criar/atualizar o shim `orin.cmd` e o `PATH` de usuário sem duplicar entradas.
7. Perguntar exatamente no terminal, após a promoção:

   ```text
   Do you want to create a desktop shortcut that opens Orin Desktop automatically? [Y/n]
   ```

   Uma resposta afirmativa cria/atualiza `Orin Desktop.lnk` no Desktop real do
   usuário (inclusive quando redirecionado pelo OneDrive), com target e
   argumentos fixos, sem interpolar conteúdo fornecido pelo usuário.

A opção `-Version`, `-NoDesktopShortcut`, `-Force` e `-Uninstall` será
documentada. O uninstall remove binário, ponteiro, shim e atalho somente após
confirmação, e preserva dados/configuração por padrão; uma remoção de dados
será uma opção separada e explícita.

### 4. Atualizações recomendadas, não automáticas

O Electron consultará o manifesto de GitHub Releases após ficar pronto e no
máximo uma vez por 24 horas. A consulta terá timeout curto, cache local e falha
silenciosa: não atrasa a abertura, não coleta conteúdo de conversas e não envia
credenciais. Se a versão estável for maior, a aplicação mostra um aviso com a
versão e uma ação **Update Orin**; ela abre/invoca o instalador versionado.

Não haverá atualização automática de binários em background nesta primeira
versão. Isso evita trocar executáveis enquanto API/worker ainda escrevem no
banco SQLite e mantém a atualização verificável pelo mesmo fluxo de hash,
assinatura, staging e rollback da instalação inicial. `orin update` chamará o
mesmo fluxo depois de parar cooperativamente a instância em execução.

### 5. OmniRoute e demais runtimes opcionais

OmniRoute continua uma integração opcional do usuário instalada por npm. Ele
não faz parte do artefato standalone, não é requisito do Orin e não bloqueia
readiness. A interface mantém a configuração/autostart já existente apenas
quando o executável npm está disponível; caso contrário, mostra a orientação de
instalação sem baixar Node ou OmniRoute automaticamente.

Providers remotos e Ollama externo continuam integrações escolhidas pelo
usuário, não requisitos da instalação do Orin. Credenciais continuam apenas na
UI e cifradas no banco local.

## Fases de implementação

### Fase A — spike de persistência local e contratos

1. Inventariar operações dependentes de PostgreSQL e executar as migrations em
   SQLite descartável. Corrigir DDL, tipos JSON/datas, `FOR UPDATE`, defaults e
   consultas específicas de dialeto.
2. Criar a fábrica de engine SQLite com pragmas e testar API + worker em três
   processos reais no Windows.
3. Substituir ARQ/publisher por um worker de fila SQLite: claim atômico,
   cancelamento, recuperação de crash, heartbeats e scheduler devem permanecer
   funcionais.
4. Trocar `/readyz`, probes, status de launcher e splash desktop para reportar
   `SQLite`, migration e worker, removendo Docker/PostgreSQL/Redis como
   pré-requisitos do perfil instalado.
5. Remover `arq`, `redis` e `psycopg` das dependências de distribuição somente
   após os testes de regressão confirmarem que não há import/runtime residual.

**Gate:** testes unitários e integração SQLite, criação de turno, streaming,
cancelamento, reinício durante turno, recuperação, anexos, browser e tarefas
agendadas passam sem Redis, Docker ou PostgreSQL instalados.

### Fase B — runtime congelado

1. Fixar uma única fonte de versão: tag `vX.Y.Z`, `pyproject.toml` e
   `desktop/package.json` devem coincidir no CI.
2. Adicionar especificação PyInstaller `onedir` para `orin.exe`, incluindo
   migrations, frontend compilado, bibliotecas nativas de leitura de documentos
   e browser Playwright/Chromium.
3. Ajustar `RuntimeProfile` e carregamento de recursos para layout congelado;
   validar `orin.exe --version`, `--help`, `--no-browser` e `--desktop` em uma
   pasta sem checkout, `.venv`, Node ou Docker.
4. Empacotar o host Electron com o runtime como `extraResources`, mantendo
   isolamento de contexto, navegação somente a loopback e shutdown cooperativo.
5. Testar OmniRoute instalado por npm e sua indisponibilidade explícita sem
   afetar o funcionamento standalone do Orin.

**Gate:** uma VM Windows limpa instala e inicia `orin` e `orin --desktop` sem
nenhuma ferramenta de desenvolvimento no `PATH`.

### Fase C — instalador e atualização

1. Implementar o script de instalação publicado, manifestos, SHA-256,
   validação Authenticode quando disponível, staging, promoção e rollback.
2. Implementar a inicialização segura de configuração e o `.lnk` opcional.
3. Adicionar `orin update`, `orin uninstall` e UX de recomendação de atualização
   no Electron com cache/timeout e sem auto-download.
4. Testar instalação limpa, instalação repetida, downgrade recusado, atualização,
   download corrompido, hash inválido, processo em execução, rollback e
   uninstall preservando dados.

### Fase D — GitHub Actions e publicação

1. Criar CI de pull request no Windows: testes Python, frontend, sintaxe do
   Electron, compatibilidade SQLite e verificação de versões.
2. Criar workflow de release por tag protegida: build determinístico a partir de
   lockfiles, PyInstaller, Electron, assembly do ZIP, smoke test em diretório
   isolado, hashes, SBOM e publicação em GitHub Releases.
3. Usar GitHub Secrets protegidos para assinatura de código; releases de teste
   podem ser marcadas não assinadas de modo explícito, nunca simulando uma
   assinatura válida.
4. Publicar `install.ps1`, `release.json`, ZIP, checksums, notas de release e
   instruções de rollback como assets da mesma tag.

## Arquivos e áreas previstos

- `src/agentos/launcher/`: ambiente, serviços, probes, supervisor, CLI e status
  desktop passam de Docker/Redis/PostgreSQL para o perfil SQLite local.
- `src/agentos/workers/`, `src/agentos/conversations/chat.py` e composição de
  produção: nova fila durável SQLite e adaptação de concorrência/recuperação.
- `src/agentos/persistence/`: migrations e adaptadores tornados compatíveis com
  SQLite; testes novos de semântica e concorrência.
- `src/agentos/installation/`, `desktop/` e novo diretório de build: localização
  de recursos congelados e `extraResources` do Electron.
- `scripts/`: novo instalador de distribuição; o instalador atual de checkout
  permanece claramente identificado como desenvolvimento.
- `.github/workflows/`: CI e release. `README.md` e `docs/LAUNCHER.md`: novos
  requisitos, instalação, privacidade da checagem de update e recuperação.

## Critérios de aceite da primeira release

- Em Windows limpo, o comando de instalação funciona sem Python, Node, Docker,
  PostgreSQL ou Redis pré-instalados.
- O script configura os diretórios e a chave de criptografia antes do primeiro
  launch, sem expor ou solicitar chaves de provider no terminal.
- A pergunta de atalho aparece ao final e o atalho abre o fluxo desktop real.
- `orin`, `orin --desktop`, browser, anexos, conversas persistentes, cancelamento,
  recuperação e tarefas agendadas funcionam sobre SQLite.
- A API permanece limitada a `127.0.0.1`; atualização e instalador não enfraquecem
  autenticação loopback, autorização, logs redigidos ou isolamento de dados.
- A aplicação recomenda uma release mais nova sem bloquear o uso e sem instalar
  nada sozinha.
- A release é reproduzível no GitHub Actions, tem hash, smoke test e rollback;
  uma falha de download/instalação deixa a versão anterior e os dados intactos.

## Riscos que precisam de validação antes de prometer prazo

- SQLite é adequado ao perfil local de usuário único, mas WAL ainda serializa
  escritores. A concorrência máxima final deve ser decidida por testes com
  streaming, tool activity e subagentes, não por equivalência com os oito jobs
  atuais de ARQ.
- O conjunto de migrations atual foi escrito para PostgreSQL. O spike da Fase A
  pode revelar uma migração que exige bifurcação ou um baseline SQLite novo;
  não é seguro assumir que as migrations existentes rodarão sem mudanças.
- Chromium/Playwright, bibliotecas de documentos e Electron tornarão o pacote
  grande. O workflow precisa medir tamanho e verificar todos os binários numa
  VM limpa, em vez de omiti-los para reduzir o download.
- Assinatura Authenticode requer certificado e segredo de assinatura fora do
  repositório. Sem ela, SmartScreen e a cadeia de confiança da instalação ficam
  mais fracos; hash sozinho detecta corrupção, mas não substitui assinatura.
- A política final de OmniRoute depende de seu artefato redistribuível. Esse é
  um gate de escopo, não uma dependência que deve ser escondida do usuário.

## Fora do escopo desta primeira entrega

- macOS e Linux;
- sincronização/multiusuário e servidor acessível pela rede;
- atualização automática em background;
- migração automática de dados de uma instalação PostgreSQL/Docker existente.
  Se for necessária, será um comando separado, com backup e confirmação.
