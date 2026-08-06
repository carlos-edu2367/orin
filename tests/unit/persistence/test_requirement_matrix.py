from agentos.persistence import TransactionalPersistence


def test_canonical_port_has_only_the_four_rfc601_operations():
    public_methods = {
        name
        for name in dir(TransactionalPersistence)
        if not name.startswith("_")
    }

    assert public_methods == {"transact", "read", "scan", "inspect_commit"}
