from sqlalchemy import create_engine

from agentos.persistence.postgres.agent_memory import PostgresAgentMemoryStore
from agentos.persistence.postgres.schema import metadata


def _store(**kwargs):
    engine = create_engine("sqlite://")
    metadata.create_all(engine)
    return PostgresAgentMemoryStore(engine, "user:1", **kwargs)


def test_save_records_the_kind_confidence_and_source():
    store = _store()
    store.save("o build é pnpm", ("comando",), kind="operational", confidence=0.7, source="mechanical")

    row = store.recent(limit=1)[0]
    assert (row["kind"], row["confidence"], row["source"]) == ("operational", 0.7, "mechanical")


def test_a_contradicting_fact_supersedes_the_older_one_instead_of_deleting_it():
    store = _store()
    first = store.save("o gerenciador de pacotes é npm", kind="operational")
    second = store.save("o gerenciador de pacotes é pnpm", kind="operational")

    assert second["superseded"] == [first["memory_id"]]
    assert [row["fact"] for row in store.relevant("gerenciador de pacotes")] == [
        "o gerenciador de pacotes é pnpm"
    ]


def test_a_different_kind_does_not_supersede():
    store = _store()
    store.save("o gerenciador de pacotes é npm", kind="operational")
    second = store.save("o gerenciador de pacotes é npm", kind="preference")

    assert second["superseded"] == []


# Twenty genuinely independent sentences, sharing at most one real word with
# any other entry. Facts that instead differ only by a trailing digit (or any
# other <3-char token, which `_terms` drops) read as near-duplicates of one
# another and legitimately supersede down to one row -- that collapsing is the
# behaviour under test elsewhere, not a bug to work around here.
_UNRELATED_FACTS = (
    "a nota fiscal do cliente sai automaticamente no fechamento do mes",
    "a contagem de estoque acontece toda sexta feira as oito horas",
    "as entregas atrasam quando o transportador muda de rota",
    "os relatorios contabeis sao revisados pelo escritorio externo",
    "a comissao de vendas paga no dia quinze de cada mes",
    "os dados de clientes ficam guardados num sistema proprio",
    "os fornecedores enviam boletos direto para o setor financeiro",
    "as compras acima de dez mil exigem duas assinaturas distintas",
    "a producao para durante a manutencao preventiva mensal",
    "o controle de qualidade usa amostragem por lote de fabrica",
    "o time de pessoas cuida do recrutamento de novos funcionarios",
    "o departamento juridico revisa todos os contratos assinados",
    "as campanhas de propaganda rodam em ciclos trimestrais fixos",
    "o suporte responde chamados em ate vinte e quatro horas",
    "o caixa fecha diariamente as dezoito horas em ponto",
    "a revisao interna acontece duas vezes durante o ano",
    "o time de politicas revisa quem tem acesso ao sistema",
    "a equipe de rede monitora tentativas de acesso indevido",
    "os servidores proprios ficam hospedados num predio local",
    "as rotas de entrega mudam conforme o transito da cidade",
)


def test_relevant_always_reserves_room_for_the_strongest_preferences():
    store = _store()
    store.save("prefiro respostas curtas", kind="preference", confidence=0.9)
    for fact in _UNRELATED_FACTS:
        store.save(fact, kind="fact")

    facts = [row["fact"] for row in store.relevant("nota fiscal cliente", limit=12)]

    assert "prefiro respostas curtas" in facts
    assert len(facts) == 12


def test_relevant_ranks_by_the_task_not_by_recency():
    store = _store()
    store.save("o deploy usa fly.io", kind="fact")
    for fact in _UNRELATED_FACTS:
        store.save(fact, kind="fact")

    assert "o deploy usa fly.io" in [row["fact"] for row in store.relevant("como fazer o deploy", limit=12)]


def test_relevant_prefers_project_scope_over_global_on_a_tie():
    engine = create_engine("sqlite://")
    metadata.create_all(engine)
    PostgresAgentMemoryStore(engine, "user:1").save("o build usa make", kind="operational")
    scoped = PostgresAgentMemoryStore(engine, "user:1", project_id="project:a")
    scoped.save("o build usa make aqui", kind="operational")

    assert scoped.relevant("build", limit=1)[0]["fact"] == "o build usa make aqui"
