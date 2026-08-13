# Refatoração da barra lateral de chat e home

## Contexto

Em 2026-08-13, a navegação compartilhada por `Home` e `ChatPage` foi reorganizada para manter as ações principais disponíveis mesmo quando o histórico cresce.

## Decisões

- `ProjectNavigation` agora separa cabeçalho/abas, lista de histórico rolável e rodapé de ações.
- `Nova conversa` fica fixa na aba Chats; na Home ela limpa o composer e os anexos pendentes, e no chat navega para `/`.
- `Novo projeto` fica fixa na aba Projetos e continua abrindo o fluxo existente de criação.
- `Ações agendadas` é um link fixo para `/schedules` disponível nas duas abas.
- A coluna e a rolagem independente do histórico do chat foram preservadas; abaixo de 900px, a sidebar continua oculta conforme o contrato visual existente.
- Estados ativos, contadores, estados vazios, indicadores de estado e foco/hover foram adicionados sem alterar contratos de API ou persistência.

## Validação

- Suíte frontend: 43 arquivos e 263 testes aprovados.
- Build de produção: `npm run build` aprovado.
- Inspeção visual local em 1280x720 e breakpoint de 800x720 aprovada.

## Riscos restantes

- A inspeção visual usou o frontend Vite sem o backend local em execução; portanto, estados com dados reais foram cobertos por testes e estados vazios, não por uma sessão autenticada real.
- A ação fixa só aparece em desktop porque a sidebar continua seguindo o breakpoint existente de 900px.
