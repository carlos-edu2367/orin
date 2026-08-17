# Compatibilidade da telemetria de contexto com redaction

## Descoberta

O cálculo de contexto estava funcionando no runtime, mas `sanitize_public_mapping` classificava chaves como `system_prompt_tokens` como sensíveis por conter `prompt` e `tokens`. O snapshot chegava ao frontend com `"[REDACTED]"`, então a validação numérica descartava todo o bloco e o chip permanecia em `Contexto —`.

## Correção

Contadores numéricos limitados da telemetria de contexto agora são explicitamente permitidos, enquanto prompt, conteúdo, credenciais e a chave genérica `token` continuam redigidos. O backend também reconstrói `system_prompt_tokens` em eventos legados quando o total e as demais categorias permitem uma inferência não negativa e determinística.

## Validação

- Contratos de atividades, janela de contexto e store de conversas: 25 testes passaram.
- Snapshot real local após reinício: `used_tokens=6771`, `limit_tokens=262144`, `system_prompt_tokens=1589`.
- `/readyz`: `ready`.
