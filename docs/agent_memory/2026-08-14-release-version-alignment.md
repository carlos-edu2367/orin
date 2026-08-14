# Release 0.1.6 e versão do launcher

- `pyproject.toml` e `desktop/package.json` declaravam `0.1.6`, mas o `uv.lock` ainda registrava o pacote editável como `0.1.3` e o ambiente local tinha `agentos-0.1.4.dist-info`.
- O launcher usa `importlib.metadata.version("agentos")`; por isso o ambiente antigo fazia o executável instalado imprimir `orin 0.1.4 (installed)` mesmo estando no diretório de instalação `0.1.6`.
- A correção foi regenerar o lock com `uv lock` e sincronizar o ambiente com `uv sync --frozen --all-groups`. O build congelado passou a imprimir `orin 0.1.6 (installed)`.
- A tag `v0.1.6` já existia no remoto; por isso a nova entrega será `0.1.7`, sem sobrescrever uma release publicada. O diretório `data/` continua fora do commit por conter estado local do usuário.
