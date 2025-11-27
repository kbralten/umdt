async def upstream_response_hook(response, ctx):
    # Suppose serial number is returned at register address 123
    if getattr(response, 'pdu', None) and getattr(response, 'address', None) == 123:
        # Overwrite PDU bytes with zeros (example - use correct PDU handling)
        try:
            response.pdu.data = b'\x00' * len(response.pdu.data)
        except Exception:
            # best-effort: ignore if structure unexpected
            ctx.log.warning("Could not mask response pdu; unexpected structure")
        else:
            ctx.log.info("Masked serial register in response to master")
    return response
