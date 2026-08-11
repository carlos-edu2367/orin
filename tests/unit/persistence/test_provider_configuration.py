from sqlalchemy import create_engine, select

from agentos.persistence.postgres.provider_configuration import PostgresProviderConfigurationAdapter
from agentos.persistence.postgres.schema import metadata, provider_configurations
from agentos.persistence.provider_secrets import ProviderSecretCipher


def test_omniroute_configuration_persists_an_empty_gateway_key_encrypted() -> None:
    engine = create_engine("sqlite://")
    metadata.create_all(engine)
    cipher = ProviderSecretCipher("unit-test-provider-key")
    configuration = PostgresProviderConfigurationAdapter(engine, cipher=cipher)

    saved = configuration.configure({
        "provider": "omniroute",
        "user_id": "local-user",
        "enabled": True,
        "api_key": "",
        "base_url": "http://127.0.0.1:20128/v1",
    })

    with engine.connect() as connection:
        stored = connection.execute(
            select(provider_configurations.c.api_key_ciphertext).where(
                provider_configurations.c.user_id == "local-user",
                provider_configurations.c.provider == "omniroute",
            )
        ).scalar_one()

    assert saved["enabled"] is True
    assert stored.startswith("enc:v1:")
    assert cipher.decrypt(stored) == ""
