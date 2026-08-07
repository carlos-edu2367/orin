# RFC 404 — Terminal Resource Design

**Data:** 2026-08-07  
**Status:** fechado para implementação nesta sessão  
**Escopo:** gate RFC 404 integrado às portas existentes de RFC 402, 403, 603, 601, 602 e 103.

## Decisões

1. O pacote final é `agentos.terminal`, com modelos públicos imutáveis, `TerminalPort`, portas internas de adapter/supervisor e um `TerminalService` de referência.
2. `TerminalOperationContext` repete o binding completo exigido pela RFC 404. Toda operação compara o binding completo contra a sessão e revalida o `ResourceManager` antes do efeito.
3. O chamador fornece somente `WorkspacePath`; não há caminho físico, PID ou handle nativo nas portas. A resolução física, quando necessária, fica em `LocalTerminalAdapter` por uma porta privada do adapter.
4. O Resource Manager permanece autoridade de catálogo, lease, capability, isolamento, fencing, expiry, revoke, release e cleanup. O Terminal não cria catálogo, lease ou policy paralelos.
5. `ReferenceTerminalAdapter` é o adapter padrão: não cria processos reais, permite resultados determinísticos configuráveis e exercita buffer, input, stream, cancelamento, árvore e cleanup sem depender do host.
6. `LocalTerminalAdapter` é opcional e isolado: usa `subprocess.Popen(..., shell=False, start_new_session=True)` somente na boundary operacional, com ambiente allowlisted, cwd físico resolvido internamente, leitores limitados e término do grupo de processos.
7. Uma sessão aceita no máximo um comando foreground. O comando é aceito somente em `READY`; resultado terminal de um comando não é reexecutado após efeito `UNKNOWN`.
8. O buffer mantém somente metadados públicos e chunks limitados em memória do adapter. Overflow marca `HEAD_DROPPED`; quando um `ArtifactManager` é fornecido, o serviço pode publicar a saída por referência autorizada, nunca bytes em Events ou persistência.
9. Eventos são emitidos apenas após fatos confirmados, com `EventEnvelope`, sequência por `execution_id` e payload mínimo: IDs, status, contagens, uso e razões categóricas.
10. Snapshots duráveis contêm somente IDs, binding, cwd lógico, status, policy version, uso, checkpoints, metadata do buffer e referências de output. Não contêm comando bruto, input, environment, secret, PID como autoridade, handle ou cwd físico.
11. Escritas duráveis usam `TransactionalPersistence.transact` através de `TerminalPersistenceJournal`; `UNKNOWN` é preservado e reconciliado por `inspect_commit`, sem retry cego.
12. Close confirmado exige árvore encerrada e release confirmado. Falha de cleanup/release produz `RECOVERY_REQUIRED`/`UNKNOWN`; nunca é reportada como `CLOSED` bem-sucedida.

## Componentes e fluxo

```text
TerminalPort
  -> TerminalService
     -> ResourceManager (lease/capability/fence)
     -> Workspace/Filesystem boundary (WorkspacePath + root revalidation)
     -> TerminalAdapter + ProcessSupervisor
     -> TerminalPersistenceJournal (snapshot + outbox)
     -> ArtifactManager opcional (overflow por referência)
```

`create` autoriza `TERMINAL_SESSION`, valida Workspace e cria uma sessão `READY`. `execute` revalida o binding, registra `RUNNING`, chama o adapter uma única vez por idempotency key e estabiliza o outcome. `write_input`, `stream`, `inspect`, `request_cancel` e `close` revalidam contexto, lease, sessão e comando. Cancelamento usa cooperative, interrupt, terminate e kill, avançando somente depois de confirmação do supervisor. Reconciliation observa ownership da árvore e converte perda de controle em `TerminalSessionLost` e `RECOVERY_REQUIRED`.

## Tratamento de efeitos

- Rejeição anterior ao adapter: `NOT_APPLIED`, erro categorizado sem texto sensível.
- Falha confirmada do adapter: `APPLIED` ou `NOT_APPLIED` conforme receipt; comando não é repetido automaticamente.
- Falha depois do envio/commit não observado: `UNKNOWN`, checkpoint para reconciliação e sem alegação de sucesso.
- Input repetido com mesma chave e fingerprint: retorna receipt original; chave com payload diferente: conflito.
- Close repetido: retorna receipt terminal original; close com binding diferente: rejeição sem revelar existência.

## Segurança e limites

Comandos, input, output, environment references e cwd físico são tratados como sensíveis. `repr`, erros, eventos, persistência e scans não exibem seus valores. Limits são validados e limitam TTL, timeout, processos, memória, CPU, bytes, chunks e input. O adapter local não usa `shell=True`, `os.system`, HTTP, banco ou execução arbitrária por transporte.

## Verificação prevista

Os testes cobrirão contratos, transições, ownership, lease/fence/TTL, idempotência, cwd, output limitado, leakage, input, cancelamento escalonado, árvore, UNKNOWN, cleanup/quarantine, persistência/outbox, Artifact overflow, restart/reconcile e regressão do repositório. A matriz final só marcará itens como concluídos após comandos executados nesta sessão.
