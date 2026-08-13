# Ollama Native Tool History Compatibility

## Objetivo

Corrigir a segunda rodada do loop agentic para Ollama Cloud e Ollama local quando o modelo chama ferramentas. O primeiro pedido já chega ao endpoint nativo `/api/chat`; o problema está no histórico reutilizado depois que a ferramenta termina.

## Evidência encontrada

OpenRouter usa o contrato OpenAI-compatible e recebe `tool_calls[].function.arguments` como string JSON, além de resultados com `tool_call_id`. O transporte Ollama usa `/api/chat`, cujo contrato nativo espera argumentos como objeto e resultados de ferramenta identificados por `tool_name`. O runtime atual gera um único formato não-Anthropic, portanto a primeira rodada passa e a rodada seguinte envia um histórico incompatível.

## Decisão de arquitetura

A adaptação ficará em `HTTPProviderStreamTransport._ollama_request`, na fronteira do provider. O runtime continuará armazenando uma representação interna estável e OpenAI-compatible; apenas o payload enviado ao Ollama será convertido. Isso evita condicionais de protocolo no loop agentic e preserva o comportamento já validado de OpenRouter, OpenAI e OmniRoute.

Para cada mensagem assistant com `tool_calls`, o adaptador converterá argumentos JSON string válidos para objeto e criará um mapa temporário de `id` para nome da ferramenta. Para cada mensagem `role=tool`, usará esse mapa para enviar `tool_name`; mensagens sem chamada de ferramenta permanecerão semanticamente iguais. A conversão será tolerante a argumentos já estruturados e nunca deverá vazar credenciais ou texto de erro.

## Tratamento de erros e compatibilidade

O normalizador NDJSON continuará responsável por converter a resposta Ollama em eventos tipados. O `PROVIDER_STREAM_FAILED` seguirá sendo o código público para falhas de transporte/iteração, mas a regressão será coberta antes dessa camada para provar que uma rodada completa de ferramenta alcança a segunda resposta. Nenhuma validação existente, limite, isolamento por usuário ou tratamento de credenciais será removido.

## Testes

Será adicionado um teste de transporte que captura duas requisições: a primeira produz uma tool call e a segunda deve conter `function.arguments` como objeto e `tool_name` no resultado. O teste também verificará que a forma OpenRouter continua usando string JSON. A suíte focada agentic e a suíte Python completa serão executadas antes do commit.
