from __future__ import annotations

import pytest

from se.src.infrastructure.config.schemas import (
    ConfigSchema,
    DriverConfig,
    StorageSettings,
)
from se.src.infrastructure.storage.core.manager import StorageEngine
from se.src.infrastructure.storage.drivers.object_local.driver import (
    LocalObjectStorageDriver,
)


@pytest.mark.asyncio
async def test_f2_storage_engine_registers_object_local_driver(tmp_path):
    config = ConfigSchema(
        storage=StorageSettings(
            drivers={
                "object-local": DriverConfig(
                    enabled=True,
                    required=True,
                    options={"root": str(tmp_path / "assets")},
                )
            }
        )
    )
    engine = StorageEngine(config)
    await engine.connect()
    try:
        driver = engine.get_object_storage_driver()
        assert isinstance(driver, LocalObjectStorageDriver)
        assert engine.is_driver_available("object-local") is True
    finally:
        await engine.disconnect()
