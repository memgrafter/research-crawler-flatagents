from __future__ import annotations

from typing import Any, Dict

from flatmachines.flatmachine import FlatMachine
from flatmachines.backends import make_uri


class LeaseFlatMachine(FlatMachine):
    """FlatMachine variant that propagates parent lock to peer machines.

    This ensures peer machine executions also use DB-backed lease locking instead of
    creating local file locks.
    """

    async def _launch_and_write(
        self,
        machine_name: str,
        child_id: str,
        input_data: Dict[str, Any],
    ) -> Any:
        target_config, peer_config_dir = self._resolve_machine_config(machine_name)

        peer = self.__class__(
            config_dict=target_config,
            persistence=self.persistence,
            lock=self.lock,
            result_backend=self.result_backend,
            agent_registry=self.agent_registry,
            _config_dir=peer_config_dir,
            _execution_id=child_id,
            _parent_execution_id=self.execution_id,
            _profiles_dict=self._profiles_dict,
            _profiles_file=self._profiles_file,
        )

        try:
            result = await peer.execute(input=input_data)
            uri = make_uri(child_id, "result")
            await self.result_backend.write(uri, result)
            return result
        except Exception as e:
            uri = make_uri(child_id, "result")
            await self.result_backend.write(uri, {"_error": str(e), "_error_type": type(e).__name__})
            raise
