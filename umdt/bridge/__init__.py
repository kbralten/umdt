"""UMDT Bridge - Soft-Gateway for Modbus Protocol Translation.

The Bridge acts as a transparent middleware between Modbus Masters and Slaves,
supporting protocol conversion (TCP <-> RTU), traffic inspection, and future
extensibility via hooks for logic injection, MQTT telemetry, and PCAP logging.
"""

from .bridge import Bridge
from .pipeline import BridgePipeline, HookContext, Request, Response
from .upstream import UpstreamServer
from .downstream import DownstreamClient
from .protocol import ModbusFrameParser, FrameType

__all__ = [
    "Bridge",
    "BridgePipeline",
    "HookContext",
    "Request",
    "Response",
    "UpstreamServer",
    "DownstreamClient",
    "ModbusFrameParser",
    "FrameType",
]
