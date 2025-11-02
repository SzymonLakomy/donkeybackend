from ninja import Router

from donkeybackend.security import DRFJWTAuth

api = Router(tags=["schedule"], auth=DRFJWTAuth())
