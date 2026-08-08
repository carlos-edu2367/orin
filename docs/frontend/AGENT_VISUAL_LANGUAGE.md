# Agent Visual Language

## Identidade abstrata

Cada agent é uma forma geométrica, não avatar humano: núcleo (papel), anel (estado), marca (identidade) e accent (diferenciação). A mesma assinatura deve existir em ícone 2D, rail e cena 3D.

| Parte | 2D | 3D | Semântica |
| --- | --- | --- | --- |
| Núcleo | losango/círculo/polígono | malha equivalente | Identidade estável do agent. |
| Anel | stroke/halo | emissive ring | Atividade derivada. |
| Conexão | linha fina | spline/beam | Relação confirmada, não canal permanente. |
| Pulso | ponto que percorre linha | partícula instanciada | Fato de delegação/mensagem/retorno. |

## Estados visuais

| Visual | Derivação |
| --- | --- |
| Idle | agent conhecido sem execution ativa observável. |
| Queued/running/waiting/paused/terminal | estado `ExecutionState` persistido. |
| Using tool | Tool `RUNNING` projetada para aquela execution. |
| Communicating | janela transitória após evento explícito de mensagem/delegação/retorno. |
| Degraded | stream/auth/reconciliação indisponível; não atribuir ao agent. |

## Grafo

O modo compacto mostra no máximo os participantes observados na execution corrente. Clique usa `layoutId` para expandir em um grafo R3F. Nós e arestas são inseridos apenas a partir de projection de delegation/message; child execution isolada não basta para desenhar uma mensagem. Limitar a 12 nós na cena principal; o restante vira contador e lista acessível.
