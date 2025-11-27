async def on_start(ctx):
    ctx.log.info("mock_counter started")
    ctx.state['count'] = 0

    async def tick():
        while True:
            ctx.state['count'] += 1
            await ctx.write_register(unit=1, address=1000, value=ctx.state['count'])
            await ctx.sleep(1)

    ctx.schedule_task(tick())

async def on_request(request, ctx):
    # Short-circuit reads for a special address
    if request.function_code == 3 and getattr(request, 'address', None) == 9999:
        return ctx.make_response_exception(request, exception_code=1)
    # Passthrough - return the request unchanged
    return request
