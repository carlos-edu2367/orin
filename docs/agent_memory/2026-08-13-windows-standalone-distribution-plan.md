# Plano da distribuição Windows standalone

## Decisão proposta

A distribuição Windows inicial do Orin deve trocar o conjunto PostgreSQL +
Redis/ARQ por SQLite local em WAL e uma fila persistente no próprio banco. A
API e um único worker continuam processos separados; não usar fila em memória
nem embarcar Docker/serviços ocultos.

## Evidência no checkout

- `src/agentos/launcher/services.py` é o seam atual de Docker/PostgreSQL/Redis.
- O launcher já se relança por `orin internal-service`, e `OrinPaths` separa
  estado do diretório de instalação, portanto é compatível com PyInstaller e
  atualização versionada.
- Electron já abre a aplicação da API em loopback e deve continuar sob controle
  do Supervisor; o atalho desktop deve chamar `orin.exe --desktop`, não abrir
  apenas `Orin Desktop.exe`.
- O instalador de `scripts/install-orin.ps1` é de desenvolvimento e exige
  Python, Node e Docker. O instalador publicado precisa ser um fluxo separado.
- Não existem workflows GitHub Actions neste checkout.
- Playwright Chromium e OmniRoute são dependências de runtime a tratar: o
  Chromium deve ir no artefato; OmniRoute só pode ser incluído se houver pacote
  redistribuível e reproduzível, sem pedir Node ao usuário.

## Contratos de release

- Uma release é um pacote único e versionado contendo Electron, `orin.exe`,
  frontend, migrations e runtimes nativos; `current` e o shim `orin.cmd` apontam
  para a mesma versão.
- `install.ps1` verifica hash, faz staging/promoção/rollback, inicializa uma
  chave de cifragem local e pergunta ao final se deve criar `Orin Desktop.lnk`.
- O app apenas recomenda updates através de manifesto de GitHub Releases, com
  cache/timeout; não substitui binários em background.
