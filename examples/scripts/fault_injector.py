import random

async def on_request(request, ctx):
    # Only target unit 5
    if getattr(request, 'unit_id', None) != 5:
        return None

    # 10% chance to drop the request (simulate packet loss)
    if random.random() < 0.10:
        ctx.log.warning("Dropping request for unit 5 to simulate packet loss")
        return ctx.make_response_exception(request, exception_code=0xFF)

    # 50-100ms jitter
    await ctx.sleep(random.uniform(0.05, 0.10))
    return None
