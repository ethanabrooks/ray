import asyncio
import logging

from dataclasses import dataclass
from itertools import islice
from typing import List, Dict, Union

import ray.dashboard.utils as dashboard_utils
import ray.dashboard.memory_utils as memory_utils
from ray.dashboard.modules.job.common import JobInfo
from ray.dashboard.datacenter import DataOrganizer

from ray.experimental.state.common import (
    filter_fields,
    ActorState,
    PlacementGroupState,
    NodeState,
    WorkerState,
    TaskState,
    ObjectState,
    RuntimeEnvState,
    ListApiOptions,
)
from ray.experimental.state.state_manager import StateDataSourceClient
from ray.runtime_env import RuntimeEnv

logger = logging.getLogger(__name__)


@dataclass(init=True)
class StateApiResult:
    # Returned data.
    data: Union[dict, List[dict], Dict[str, JobInfo]] = None
    # A list of warnings generated from the API that should be delivered to users.
    warnings: List[str] = None


GCS_QUERY_FAILURE_WARNING = (
    "Failed to query data from GCS. It is due to "
    "(1) GCS is unexpectedly failed. "
    "(2) GCS is overloaded by lots of work. "
    "(3) There's an uexpected network issues. Please check the GCS logs to "
    "find the root cause."
)
RAYLET_QUERY_FAILURE_WARNING = (
    "Failed to query data from some raylets. You might have data loss. "
    "Queryed {total} raylets "
    "and {network_failures} raylets failed to reply. It is due to "
    "(1) Raylet is unexpectedly failed. "
    "(2) Raylet is overloaded by lots of work. "
    "(3) There's an uexpected network issues. Please check the Raylet logs to "
    "find the root cause."
)
AGENT_QUERY_FAILURE_WARNING = (
    "Failed to query data from some Ray agents. You might have data loss. "
    "Queryed {total} Ray agents "
    "and {network_failures} Ray agents failed to reply. It is due to "
    "(1) Ray agent is unexpectedly failed. "
    "(2) Ray agent is overloaded by lots of work. "
    "(3) There's an uexpected network issues. Please check the Ray agent logs to "
    "find the root cause."
)


# TODO(sang): Move the class to state/state_manager.py.
# TODO(sang): Remove *State and replaces with Pydantic or protobuf
# (depending on API interface standardization).
class StateAPIManager:
    """A class to query states from data source, caches, and post-processes
    the entries.
    """

    def __init__(self, state_data_source_client: StateDataSourceClient):
        self._client = state_data_source_client

    @property
    def data_source_client(self):
        return self._client

    def _filter(self, filters: str, data: list):
        kvs = filters.split(",")
        kv_filters = {}
        for kv in kvs:
            result = kv.split("=")
            if len(result) == 2:
                kv_filters[result[0]] = result[1]

        matched_result = []
        for d in data:
            match = True
            for column, value in kv_filters.items():
                data = d.get(column)
                if not data:
                    match = False
                    break

                if data != value:
                    match = False
            if match:
                matched_result.append(d)

        return matched_result

    async def list_actors(self, *, option: ListApiOptions) -> StateApiResult:
        """List all actor information from the cluster.

        Returns:
            {actor_id -> actor_data_in_dict}
            actor_data_in_dict's schema is in ActorState
        """
        reply = await self._client.get_all_actor_info(timeout=option.timeout)
        if not reply:
            return StateApiResult(warnings=[GCS_QUERY_FAILURE_WARNING])

        result = []
        for message in reply.actor_table_data:
            data = self._message_to_dict(
                message=message, fields_to_decode=["actor_id", "owner_id", "raylet_id"]
            )
            data = filter_fields(data, ActorState)
            result.append(data)

        result = self._filter(option.filter, result)
        # Sort to make the output deterministic.
        result.sort(key=lambda entry: entry["actor_id"])
        return StateApiResult(
            data={d["actor_id"]: d for d in islice(result, option.limit)}
        )

    async def list_placement_groups(self, *, option: ListApiOptions) -> StateApiResult:
        """List all placement group information from the cluster.

        Returns:
            {pg_id -> pg_data_in_dict}
            pg_data_in_dict's schema is in PlacementGroupState
        """
        reply = await self._client.get_all_placement_group_info(timeout=option.timeout)
        if not reply:
            return StateApiResult(warnings=[GCS_QUERY_FAILURE_WARNING])

        result = []
        for message in reply.placement_group_table_data:

            data = self._message_to_dict(
                message=message,
                fields_to_decode=["placement_group_id"],
            )
            data = filter_fields(data, PlacementGroupState)
            result.append(data)

        result = self._filter(option.filter, result)
        # Sort to make the output deterministic.
        result.sort(key=lambda entry: entry["placement_group_id"])
        return StateApiResult(
            data={d["placement_group_id"]: d for d in islice(result, option.limit)}
        )

    async def list_nodes(self, *, option: ListApiOptions) -> StateApiResult:
        """List all node information from the cluster.

        Returns:
            {node_id -> node_data_in_dict}
            node_data_in_dict's schema is in NodeState
        """
        reply = await self._client.get_all_node_info(timeout=option.timeout)
        if not reply:
            return StateApiResult(warnings=[GCS_QUERY_FAILURE_WARNING])

        result = []
        for message in reply.node_info_list:
            data = self._message_to_dict(message=message, fields_to_decode=["node_id"])
            data = filter_fields(data, NodeState)
            result.append(data)

        result = self._filter(option.filter, result)
        # Sort to make the output deterministic.
        result.sort(key=lambda entry: entry["node_id"])
        return StateApiResult(
            data={d["node_id"]: d for d in islice(result, option.limit)}
        )

    async def list_workers(self, *, option: ListApiOptions) -> StateApiResult:
        """List all worker information from the cluster.

        Returns:
            {worker_id -> worker_data_in_dict}
            worker_data_in_dict's schema is in WorkerState
        """
        reply = await self._client.get_all_worker_info(timeout=option.timeout)
        if not reply:
            return StateApiResult(warnings=[GCS_QUERY_FAILURE_WARNING])

        result = []
        for message in reply.worker_table_data:
            data = self._message_to_dict(
                message=message, fields_to_decode=["worker_id", "raylet_id"]
            )
            data["worker_id"] = data["worker_address"]["worker_id"]
            data = filter_fields(data, WorkerState)
            result.append(data)

        result = self._filter(option.filter, result)
        # Sort to make the output deterministic.
        result.sort(key=lambda entry: entry["worker_id"])
        return StateApiResult(
            data={d["worker_id"]: d for d in islice(result, option.limit)}
        )

    def list_jobs(self, *, option: ListApiOptions) -> StateApiResult:
        # TODO(sang): Support limit & timeout & async calls.
        result = self._client.get_job_info()
        result = self._filter(option.filter, result)
        if not result:
            return StateApiResult(warnings=[GCS_QUERY_FAILURE_WARNING])
        return StateApiResult(data=result)

    async def list_tasks(self, *, option: ListApiOptions) -> StateApiResult:
        """List all task information from the cluster.

        Returns:
            {task_id -> task_data_in_dict}
            task_data_in_dict's schema is in TaskState
        """
        raylet_ids = self._client.get_all_registered_raylet_ids()
        replies = await asyncio.gather(
            *[
                self._client.get_task_info(node_id, timeout=option.timeout)
                for node_id in raylet_ids
            ],
        )

        network_failures = 0
        result = []
        for reply in replies:
            if not reply:
                network_failures += 1
                continue

            tasks = reply.task_info_entries
            for task in tasks:
                data = self._message_to_dict(
                    message=task,
                    fields_to_decode=["task_id"],
                )
                data = filter_fields(data, TaskState)
                result.append(data)

        warnings = (
            [
                RAYLET_QUERY_FAILURE_WARNING.format(
                    total=len(raylet_ids), network_failures=network_failures
                )
            ]
            if network_failures
            else None
        )

        result = self._filter(option.filter, result)
        # Sort to make the output deterministic.
        result.sort(key=lambda entry: entry["task_id"])
        return StateApiResult(
            data={d["task_id"]: d for d in islice(result, option.limit)},
            warnings=warnings,
        )

    async def list_objects(self, *, option: ListApiOptions) -> StateApiResult:
        """List all object information from the cluster.

        Returns:
            {object_id -> object_data_in_dict}
            object_data_in_dict's schema is in ObjectState
        """
        raylet_ids = self._client.get_all_registered_raylet_ids()
        replies = await asyncio.gather(
            *[
                self._client.get_object_info(node_id, timeout=option.timeout)
                for node_id in raylet_ids
            ]
        )

        network_failures = 0
        worker_stats = []
        for reply in replies:
            if not reply:
                network_failures += 1
                continue

            for core_worker_stat in reply.core_workers_stats:
                # NOTE: Set preserving_proto_field_name=False here because
                # `construct_memory_table` requires a dictionary that has
                # modified protobuf name
                # (e.g., workerId instead of worker_id) as a key.
                worker_stats.append(
                    self._message_to_dict(
                        message=core_worker_stat,
                        fields_to_decode=["object_id"],
                        preserving_proto_field_name=False,
                    )
                )

        result = []
        memory_table = memory_utils.construct_memory_table(worker_stats)
        for entry in memory_table.table:
            data = entry.as_dict()
            # `construct_memory_table` returns object_ref field which is indeed
            # object_id. We do transformation here.
            # TODO(sang): Refactor `construct_memory_table`.
            data["object_id"] = data["object_ref"]
            del data["object_ref"]
            data = filter_fields(data, ObjectState)
            result.append(data)

        warnings = (
            [
                RAYLET_QUERY_FAILURE_WARNING.format(
                    total=len(raylet_ids), network_failures=network_failures
                )
            ]
            if network_failures
            else None
        )

        result = self._filter(option.filter, result)
        # Sort to make the output deterministic.
        result.sort(key=lambda entry: entry["object_id"])
        return StateApiResult(
            data={d["object_id"]: d for d in islice(result, option.limit)},
            warnings=warnings,
        )

    async def list_runtime_envs(self, *, option: ListApiOptions) -> StateApiResult:
        """List all runtime env information from the cluster.

        Returns:
            A list of runtime env information in the cluster.
            We don't have id -> data mapping like other API because runtime env
            doesn't have unique ids.
        """
        agent_ids = self._client.get_all_registered_agent_ids()
        replies = await asyncio.gather(
            *[
                self._client.get_runtime_envs_info(node_id, timeout=option.timeout)
                for node_id in agent_ids
            ]
        )

        result = []
        network_failures = 0
        for reply in replies:
            if not reply:
                network_failures += 1
                continue

            states = reply.runtime_env_states
            for state in states:
                data = self._message_to_dict(message=state, fields_to_decode=[])
                data["runtime_env"] = RuntimeEnv.deserialize(
                    data["runtime_env"]
                ).to_dict()
                data = filter_fields(data, RuntimeEnvState)
                result.append(data)

        warnings = (
            [
                AGENT_QUERY_FAILURE_WARNING.format(
                    total=len(agent_ids), network_failures=network_failures
                )
            ]
            if network_failures
            else None
        )

        result = self._filter(option.filter, result)
        # Sort to make the output deterministic.
        result.sort(key=lambda entry: entry["ref_cnt"])
        return StateApiResult(
            data=list(islice(result, option.limit)), warnings=warnings
        )

    async def summary(self):
        op = ListApiOptions(timeout=30, limit=1000, filter="")
        results = await asyncio.gather(
            *[
                self.list_actors(option=op),
                self.list_tasks(option=op),
                self.list_workers(option=op),
            ]
        )
        actors, tasks, workers = results
        all_node_summary = await DataOrganizer.get_all_node_summary()
        # logger.info(all_node_summary)

        from collections import defaultdict

        node_to_summary = defaultdict(dict)

        for node_summary in all_node_summary:
            logger.info(node_summary)
            node_id = node_summary["raylet"]["nodeId"]
            node_to_summary[node_id]["cpu"] = f"{node_summary['cpu']}%"
            disk = node_summary["disk"]["/"]
            node_to_summary[node_id]["disk_utilization"] = f"{disk['percent']}%"
            node_to_summary[node_id][
                "disk_read"
            ] = f"{round(node_summary['diskIoSpeed'][0] / (1024 ^ 2), 3)} MB"
            node_to_summary[node_id][
                "disk_write"
            ] = f"{round(node_summary['diskIoSpeed'][1] / (1024 ^ 2), 3)} MB"
            node_to_summary[node_id][
                "network_sent_speed"
            ] = f"{round(node_summary['networkSpeed'][0] / (1024), 3)} KB"
            node_to_summary[node_id][
                "network_recv_speed"
            ] = f"{round(node_summary['networkSpeed'][1] / (1024), 3)} KB"
            node_to_summary[node_id]["mem_utilization"] = f"{node_summary['mem'][2]}%"
            object_store_percent = node_summary["raylet"].get(
                "object_store_used_memory", 0
            ) / (
                node_summary["raylet"].get("object_store_used_memory", 0)
                + node_summary["raylet"]["object_store_available_memory"]
            )
            node_to_summary[node_id][
                "object_store_utilization"
            ] = f"{round(object_store_percent * 100, 3)}%"

        for actor in actors.data.values():
            logger.info(actor)
            logger.info(actor["address"])
            node_id = actor["address"]["raylet_id"]
            if "actors" not in node_to_summary[node_id]:
                node_to_summary[node_id]["actors"] = defaultdict(int)
            if "actors" not in node_to_summary:
                node_to_summary["actors"] = {}
            if actor["class_name"] not in node_to_summary["actors"]:
                node_to_summary["actors"][actor["class_name"]] = {}
            node_to_summary["actors"][actor["class_name"]] = defaultdict(int)
            node_to_summary[node_id]["actors"]["cnt"] += 1
            node_to_summary["actors"][actor["class_name"]]["cnt"] += 1
            if actor["class_name"] not in node_to_summary[node_id]["actors"]:
                node_to_summary[node_id]["actors"][actor["class_name"]] = defaultdict(
                    int
                )
            if actor["state"] == "DEPENDENCIES_UNREADY":
                node_to_summary[node_id]["actors"][actor["class_name"]][
                    "dep_unready_cnt"
                ] += 1
                node_to_summary["actors"][actor["class_name"]]["dep_unready_cnt"] += 1
            elif actor["state"] == "PENDING_CREATION":
                node_to_summary[node_id]["actors"][actor["class_name"]][
                    "pending_cnt"
                ] += 1
                node_to_summary["actors"][actor["class_name"]]["pending_cnt"] += 1
            elif actor["state"] == "ALIVE":
                node_to_summary[node_id]["actors"][actor["class_name"]][
                    "alive_cnt"
                ] += 1
                node_to_summary["actors"][actor["class_name"]]["alive_cnt"] += 1
            elif actor["state"] == "RESTARTING":
                node_to_summary[node_id]["actors"][actor["class_name"]][
                    "restarting_cnt"
                ] += 1
                node_to_summary["actors"][actor["class_name"]]["restarting_cnt"] += 1
            elif actor["state"] == "DEAD":
                node_to_summary[node_id]["actors"][actor["class_name"]]["dead_cnt"] += 1
                node_to_summary["actors"][actor["class_name"]]["dead_cnt"] += 1

        for task in tasks.data.values():
            logger.info(task)
            if task["type"] == "ACTOR_CREATION_TASK":
                continue
            if "tasks" not in node_to_summary:
                node_to_summary["tasks"] = {}
            if task["name"] not in node_to_summary["tasks"]:
                node_to_summary["tasks"][task["name"]] = defaultdict(int)
            node_to_summary["tasks"][task["name"]]["cnt"] += 1
            if task["scheduling_state"] == "WAITING_FOR_DEPENDENCIES":
                node_to_summary["tasks"][task["name"]]["wait_for_dep_cnt"] += 1
            elif task["scheduling_state"] == "SCHEDULED":
                node_to_summary["tasks"][task["name"]]["scheduled_cnt"] += 1
            elif task["scheduling_state"] == "FINISHED":
                node_to_summary["tasks"][task["name"]]["finished_cnt"] += 1

        for worker in workers.data.values():
            logger.info(worker)
            node_id = worker["worker_address"]["raylet_id"]
            if worker["is_alive"]:
                if "num_workers" not in node_to_summary[node_id]:
                    node_to_summary[node_id]["num_workers"] = 0
                node_to_summary[node_id]["num_workers"] += 1

        logger.info(node_to_summary)
        return node_to_summary

    def _message_to_dict(
        self,
        *,
        message,
        fields_to_decode: List[str],
        preserving_proto_field_name: bool = True,
    ) -> dict:
        return dashboard_utils.message_to_dict(
            message,
            fields_to_decode,
            including_default_value_fields=True,
            preserving_proto_field_name=preserving_proto_field_name,
        )
