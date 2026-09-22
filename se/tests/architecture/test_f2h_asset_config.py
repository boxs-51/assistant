from se.src.infrastructure.config.schemas import (
    AssetStorageSettings,
    ConfigSchema,
)


def test_f2h_asset_storage_driver_is_explicitly_configurable():
    config = ConfigSchema(
        assets=AssetStorageSettings(storage_driver="object-custom")
    )
    assert config.assets.storage_driver == "object-custom"


def test_f2h_default_asset_storage_driver_remains_local_for_dev():
    assert ConfigSchema().assets.storage_driver == "object-local"
