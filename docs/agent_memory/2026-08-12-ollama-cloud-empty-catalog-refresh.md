# Ollama Cloud: catálogo vazio após isolamento

- O isolamento por `base_url` corretamente oculta catálogos antigos ou de outro modo do Ollama.
- A tela de configurações só mostrava `Atualizar catálogo` para OmniRoute; por isso uma configuração Cloud existente podia ficar sem modelos depois da invalidação, mesmo com a credencial armazenada.
- O catálogo agora exibe `Atualizar catálogo` para Ollama e usa o endpoint de refresh com a credencial armazenada. A chave continua write-only e não é reexibida.
- O teste de UI cobre Cloud habilitado, campo de chave vazio, refresh e presença de um modelo Cloud após a atualização.
