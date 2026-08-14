# Timeout da captura visual do navegador conversacional

- O host isolado do navegador fazia `page.content()` e `page.screenshot()` na mesma observação. Como o screenshot do Playwright pode esperar fontes/animações e exceder o timeout em páginas complexas, a operação inteira falhava mesmo com o HTML disponível.
- A captura visual agora é best-effort: usa timeout próprio de 5 segundos e, em caso de timeout, retorna o HTML e metadados com screenshot vazio. A política de rede, o limite de tamanho e o processo Chromium isolado permanecem inalterados.
- Regressão coberta em `tests/unit/browser/test_conversation_worker.py`; validação executada: 123 testes passaram, 1 foi ignorado, e Chromium real abriu `https://example.com` com HTML e screenshot.
