from .base import GatewayBaseModel

class UserLoginSchema(GatewayBaseModel):
    email:str
    password:str
    
class UserCreateSchema(GatewayBaseModel):
    UserLoginSchema


    