#!/usr/bin/env python3
# Copyright 2024 Tiexin
# See LICENSE file for licensing details.
import asyncio
import logging
import platform
from pathlib import Path

import pytest
import requests
import yaml
from lightkube import Client
from lightkube.resources.apiextensions_v1 import CustomResourceDefinition
from lightkube.resources.core_v1 import ConfigMap, Secret, Service, ServiceAccount
from lightkube.resources.rbac_authorization_v1 import (
    ClusterRole,
    ClusterRoleBinding,
)
from pytest_operator.plugin import OpsTest
from tenacity import retry
from tenacity.stop import stop_after_attempt
from tenacity.wait import wait_exponential as wexp

logger = logging.getLogger(__name__)

METADATA = yaml.safe_load(Path("./charmcraft.yaml").read_text())
APP_NAME = METADATA["name"]


@pytest.mark.abort_on_fail
async def test_build_and_deploy(ops_test: OpsTest):
    charm = await ops_test.build_charm(".")
    resources = {
        "argo-rollouts-image": METADATA["resources"]["argo-rollouts-image"]["upstream-source"],
    }
    constraints = {"arch": "arm64" if platform.machine() in ("aarch64", "arm64") else "amd64"}

    await asyncio.gather(
        ops_test.model.deploy(
            charm,
            application_name=APP_NAME,
            resources=resources,
            constraints=constraints,
        ),
        ops_test.model.wait_for_idle(
            apps=[APP_NAME], status="active", raise_on_blocked=True, timeout=60 * 5
        ),
    )
    assert ops_test.model.applications[APP_NAME].units[0].workload_status == "active"


@pytest.mark.abort_on_fail
async def test_kubernetes_resources_created(ops_test: OpsTest):
    client = Client()
    client.get(ConfigMap, name="argo-rollouts-config", namespace=ops_test.model_name)
    client.get(Secret, name="argo-rollouts-notification-secret", namespace=ops_test.model_name)
    client.get(Service, name="argo-rollouts-metrics", namespace=ops_test.model_name)
    client.get(ServiceAccount, name="argo-rollouts", namespace=ops_test.model_name)
    client.get(ClusterRole, name="argo-rollouts")
    client.get(ClusterRole, name="argo-rollouts-aggregate-to-admin")
    client.get(ClusterRole, name="argo-rollouts-aggregate-to-edit")
    client.get(ClusterRole, name="argo-rollouts-aggregate-to-view")
    client.get(ClusterRoleBinding, name="argo-rollouts")
    client.get(CustomResourceDefinition, name="analysisruns.argoproj.io")
    client.get(CustomResourceDefinition, name="analysistemplates.argoproj.io")
    client.get(CustomResourceDefinition, name="clusteranalysistemplates.argoproj.io")
    client.get(CustomResourceDefinition, name="experiments.argoproj.io")
    client.get(CustomResourceDefinition, name="rollouts.argoproj.io")


@pytest.mark.abort_on_fail
@retry(wait=wexp(multiplier=2, min=1, max=30), stop=stop_after_attempt(10), reraise=True)
async def test_argo_rollouts_is_up(ops_test: OpsTest):
    status = await ops_test.model.get_status()
    address = status["applications"]["argo-rollouts"]["public-address"]
    response = requests.get(f"http://{address}:8090/metrics")
    assert response.status_code == 200
