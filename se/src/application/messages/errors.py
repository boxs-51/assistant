class MessagePersistenceError(RuntimeError):
    pass


class MessageAccessDeniedError(MessagePersistenceError):
    pass


class MessageNotFoundError(MessagePersistenceError):
    pass


class NonCanonicalAssetContentError(MessagePersistenceError):
    pass


class MessageAssetStateError(MessagePersistenceError):
    pass
