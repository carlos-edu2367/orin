# Plano de release: Orin Desktop + CLI via GitHub Actions

## Estado confirmado

- O comando público `orin` vem de `pyproject.toml` e hoje é instalado a partir
  de um checkout por `scripts/install-orin.ps1`.
- O host Electron tem sua configuração em `desktop/package.json` e já gera um
  instalador NSIS localmente.
- O launcher congelado ainda não existe e não há `.github/workflows` neste
  repositório.
- No perfil instalado, o launcher espera encontrar `Orin Desktop.exe` ao lado
  de `orin.exe`. Isso define o layout que o empacotamento final deve produzir.

## Objetivo do release

Uma única versão publicada deve instalar, atualizar e manter juntos:

```text
%LOCALAPPDATA%\Programs\Orin\<version>\
  orin.exe
  Orin Desktop.exe
  web\
  docker-compose.yml
  runtime assets necessários
```

O shim de terminal continua pequeno e estável:

```text
%LOCALAPPDATA%\Orin\bin\orin.cmd  ->  ...\Programs\Orin\current\orin.exe
```

Assim, `orin`, `orin --desktop` e o Electron sempre pertencem ao mesmo release.
O instalador não deve baixar ou atualizar apenas um dos dois executáveis.

## Pré-requisitos antes de automatizar o empacotamento

1. Definir uma fonte única de versão e uma verificação obrigatória no CI.
   Inicialmente, o workflow deve falhar se a tag `vX.Y.Z`,
   `pyproject.toml [project].version` e `desktop/package.json.version` não forem
   exatamente `X.Y.Z`.
2. Criar a especificação PyInstaller para `orin.exe`. Ela deve incluir o pacote
   Python, migrations Alembic, `frontend/dist`, `docker-compose.yml` e qualquer
   asset que `RuntimeProfile` resolve em um perfil instalado. O teste do
   artefato deve executar `orin.exe --version` e `orin.exe --help`.
3. Fixar o layout de distribuição acima e testar, em uma pasta temporária, que
   `orin.exe --desktop` localiza `Orin Desktop.exe`, a interface servida pela API
   e o Compose sem depender do checkout, de `.venv` ou de `node_modules`.
4. Decidir a política de assinatura de código Windows. O workflow não deve
   armazenar certificado, senha ou token no repositório; use secrets protegidos
   do GitHub e permita build não assinado somente para versões de teste.
5. Criar o manifesto de release com versão, URLs e SHA-256 de cada artefato.
   O instalador de terminal deve verificar o hash antes de ativar a instalação.

## Workflows propostos

### CI de pull request

- Windows: testes unitários Python, build de `frontend/dist`, testes/lint do
  frontend e checagem de sintaxe do Electron.
- Validar que os dois manifests de versão coincidem.
- Opcionalmente, executar `electron-builder --dir` para detectar regressões de
  empacotamento sem publicar artefatos.

### Release por tag `vX.Y.Z`

1. Repetir todas as validações do CI.
2. Gerar o frontend de produção com lockfile.
3. Congelar `orin.exe` com PyInstaller no layout aprovado.
4. Gerar o host/instalador Electron com `electron-builder`.
5. Montar um pacote único versionado que contenha ambos os executáveis e seus
   assets compartilhados.
6. Executar smoke test do pacote montado: `orin --version`, `orin --desktop`
   com Docker disponível, `/healthz`, `/readyz`, encerramento e ausência de
   filhos do launcher após `orin stop`.
7. Gerar SHA-256, publicar os artefatos e o manifesto em GitHub Releases.
8. Publicar o instalador PowerShell versionado como asset do release e, se for
   desejado um endpoint estável, somente redirecionar para esse asset/manifest.

## Instalador de terminal proposto

Criar um novo `install.ps1` de distribuição; não reutilizar o atual
`scripts/install-orin.ps1`, pois este prepara um ambiente de desenvolvimento.
O novo script deve:

1. obter o manifesto de uma tag solicitada ou da última versão estável;
2. baixar o pacote único e verificar SHA-256;
3. extrair em `%LOCALAPPDATA%\Programs\Orin\<version>.staging`;
4. validar `orin.exe --version` e que `Orin Desktop.exe` está presente;
5. promover a pasta para `<version>` e mover/atualizar `current` de forma
   atômica e recuperável;
6. criar ou atualizar `%LOCALAPPDATA%\Orin\bin\orin.cmd` e o PATH do usuário;
7. manter a versão anterior até a nova passar nas validações, permitindo
   rollback sem perder config, logs ou dados em `%APPDATA%`/`%LOCALAPPDATA%`.

O comando de instalação poderá ser apresentado após a publicação do asset, por
exemplo `irm <URL-versionada-do-install.ps1> | iex`; a documentação deve também
oferecer download, inspeção e execução local do script para ambientes que não
aceitam one-liners remotos.

## Critérios de aceite

- A tag, CLI e Electron exibem a mesma versão.
- `orin` e `orin --desktop` funcionam após instalação sem Python, Node, checkout
  ou `node_modules` do desenvolvedor.
- A atualização nunca combina `orin.exe` de uma versão com Electron de outra.
- Falha de download, hash, extração ou smoke test mantém a versão anterior
  ativa.
- Docker Desktop continua sendo requisito externo e nunca é encerrado pelo
  instalador, CLI ou Electron.

## Fora do escopo deste plano

Não inclui PostgreSQL/Redis embarcados, remoção do Docker, atualização
automática em background, macOS/Linux ou publicação do workflow antes de o
artefato PyInstaller e o layout de runtime serem definidos e testados.
