# Backend em execução anterior à correção de inspeção de plugins

## Diagnóstico

O processo que atende `127.0.0.1:8000` foi iniciado às 13:38 de 2026-08-16. O arquivo
`src/agentos/plugins/fetcher.py` que contém a correção para colisão de digest foi atualizado às
14:07. Como o processo Python já havia carregado os módulos antes dessa alteração, ele continuou
usando a implementação antiga.

O cache local contém `data/plugins/superpowers/6.3.0` com digest
`96b9f53dbf65c3a8706bcf3e55b8f947e7aad541ffa2d82cf536f1aa0dd7d75c`, enquanto uma busca real ao
GitHub retornou a versão `6.3.0` com digest diferente. O fetcher antigo rejeita essa colisão e o
`PluginService` a reduz à mensagem genérica `plugin could not be inspected`.

## Próxima ação operacional

Reiniciar o runtime/launcher do Orin para carregar o código corrigido. A nova implementação
preserva o cache antigo e salva o digest novo em um diretório versionado pelo digest.
