class AssetError(RuntimeError):
    pass


class AssetNotFoundError(AssetError):
    pass


class AssetAccessDeniedError(AssetError):
    pass


class AssetStateError(AssetError):
    pass


class AssetStorageError(AssetError):
    pass


class AssetFinalizeError(AssetError):
    pass
