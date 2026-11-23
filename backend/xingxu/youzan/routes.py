"""Youzan-only administration routes and permission checks."""
from typing import Annotated
from fastapi import APIRouter, Depends
from ..auth import AuthFailure
from .schemas import YouzanInput, RefreshInput, ShopInput, RenewalInput, VerificationInput


def create_router(youzan, identity, token):
    router = APIRouter(prefix='/api/v1/admin/youzan')

    def youzan_administrator(value: Annotated[dict, Depends(identity)]):
        if "youzan:configure" not in value["user"]["permissions"]:
            raise AuthFailure("forbidden", "没有访问权限。", 403)
        return value["user"]


    @router.get("")
    def youzan_settings(user: Annotated[dict, Depends(youzan_administrator)],
                        value: Annotated[str, Depends(token)]):
        return youzan.summary(user, verification_token=value)


    @router.put("")
    async def save_youzan(body: YouzanInput, user: Annotated[dict, Depends(youzan_administrator)],
                         value: Annotated[str, Depends(token)]):
        result = youzan.save(user, body)
        if body.prepare_login:
            return await youzan.start(user, 'connect', value, interactive_verification=body.interactive_verification)
        return result


    @router.get("/shops")
    async def youzan_shops(user: Annotated[dict, Depends(youzan_administrator)], value: Annotated[str, Depends(token)]):
        return await youzan.shops(user, value)


    @router.put("/shop")
    def select_youzan_shop(body: ShopInput, user: Annotated[dict, Depends(youzan_administrator)]):
        return youzan.select_shop(user, body.kdt_id)


    @router.post("/refresh", status_code=202)
    async def refresh_youzan(body: RefreshInput, user: Annotated[dict, Depends(youzan_administrator)],
                            value: Annotated[str, Depends(token)]):
        return await youzan.start(user, body.kind, value)


    @router.post("/verification", status_code=202)
    async def verify_youzan(body: VerificationInput, user: Annotated[dict, Depends(youzan_administrator)],
                            value: Annotated[str, Depends(token)]):
        return youzan.submit_verification(user, body, value)

    @router.post("/cancel")
    async def cancel_youzan(user: Annotated[dict, Depends(youzan_administrator)]):
        return await youzan.cancel(user)

    @router.put('/renewal')
    async def configure_renewal(body: RenewalInput, user: Annotated[dict, Depends(youzan_administrator)]):
        if youzan.renewal is None:
            raise AuthFailure('unavailable', '自动刷新服务不可用。', 503)
        return youzan.renewal.configure(user, body)

    return router
