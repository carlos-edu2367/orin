# Modelos personalizados por provider

## Decisão

O catálogo de modelos continua sendo por usuário e provider, mas agora cada linha possui `is_custom`. O endpoint de refresh remove somente linhas descobertas (`is_custom = false`), preservando modelos cadastrados pelo usuário. O cadastro manual usa a mesma tabela e, portanto, o mesmo caminho de autorização usado por conversas, agendas e runtime.

## Contrato

- `POST /v1/providers/{provider}/models` cadastra `{model_id, display_name?}` para qualquer provider suportado.
- `DELETE /v1/providers/{provider}/custom-models/{model_id}` remove somente linhas manuais.
- Ambos exigem autenticação, autorização, rate limit, CSRF/idempotência para mutações e escopo do usuário.
- A tela ativa de detalhes do provider mostra o formulário, identifica modelos manuais e permite removê-los.
- A validação de tarefas agendadas aplica o mesmo filtro de origem e habilitação para não aceitar um modelo de outra modalidade do Ollama.

## Ollama

Modelos manuais guardam o `base_url` atual. A consulta continua filtrando por origem, então um modelo inserido em Ollama Cloud não aparece ao trocar para Ollama Local, e vice-versa. O modelo `deepseek-v4-flash` pode ser cadastrado mesmo que o upstream só liste `deepseek-v4-flash:preview`.

## Validação

Backend: `1813 passed, 69 skipped`; frontend: `390 passed`; build TypeScript/Vite passou. A cobertura inclui persistência antes do primeiro refresh, sobrevivência ao refresh upstream, rotas autenticadas e formulário do provider.
