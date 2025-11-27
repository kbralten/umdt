async def ingress_hook(request, ctx):
    # Map master address range 40000-40010 -> slave range 1000-1010
    if 40000 <= getattr(request, 'address', 0) <= 40010:
        offset = request.address - 40000
        request.address = 1000 + offset
        ctx.log.debug("Translated request address to %s", request.address)
    return request
