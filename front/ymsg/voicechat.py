import asyncio

from core.backend import Backend

def register(backend: Backend) -> None:
	from util.misc import ProtocolRunner
	
	# TODO: Implement UDP ports
	# https://imfreedom.org/wiki/Yahoo#Network
	backend.add_runner(ProtocolRunner('0.0.0.0', 5000, ListenerVoiceChat))
	backend.add_runner(ProtocolRunner('0.0.0.0', 5001, ListenerVoiceChat))

class ListenerVoiceChat(asyncio.Protocol):
	def connection_made(self, transport: asyncio.BaseTransport) -> None:
		print("Voice chat connection_made")
	
	def connection_lost(self, exc: Exception) -> None:
		print("Voice chat connection_lost")
	
	def data_received(self, data: bytes) -> None:
		print("Voice chat data_received", data)
