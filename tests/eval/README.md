# Avaliação de qualidade de software

`software_quality_cases.json` é a matriz de dez pedidos ponta a ponta para os modelos baratos alvo. Cada caso deve iniciar em um workspace gerenciado vazio.

Para cada combinação caso/modelo, registre os payloads estruturados de `verify_project` e, quando `frontend` for verdadeiro, de `verify_frontend`. A nota mecânica é a soma dos indicadores abaixo; não atribua nota por impressão visual.

| Indicador | Evidência |
| --- | --- |
| instalação | `verify_project.payload.steps` contém `install` executado e aprovado, quando a receita a oferece |
| typecheck, lint, build e teste | toda etapa detectada foi executada e aprovada; `all_passed` é `true` |
| servidor | processo `npm run dev` ou equivalente permanece ativo e expõe a URL usada na verificação |
| renderização | `verify_frontend.payload.all_ok` é `true` para cada rota declarada |

## Baseline v0.2.25 (pré-Fases 4–5)

Ainda não executada neste checkout: não há credenciais/configuração dos modelos alvo nem um runner que envie conversas reais. Este arquivo fixa a matriz e o método para que a primeira execução seja comparável e não invente um avaliador paralelo. Registre, por modelo, data, commit, dez resultados por indicador e a média antes de atribuir ganho às Fases 4 ou 5.
