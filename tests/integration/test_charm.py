#!/usr/bin/env python3
# Copyright 2024 Tiexin
# See LICENSE file for licensing details.

import logging
from pathlib import Path
import platform
import shlex

from lightkube import Client
from lightkube.resources.core_v1 import ConfigMap, Secret, Service, ServiceAccount
from lightkube.resources.rbac_authorization_v1 import (
    ClusterRole,
    ClusterRoleBinding,
)
from lightkube.resources.apiextensions_v1 import CustomResourceDefinition
import pytest
from pytest_operator.plugin import OpsTest
import requests
from tenacity import retry
from tenacity.stop import stop_after_attempt
from tenacity.wait import wait_exponential as wexp
import yaml

logger = logging.getLogger(__name__)

METADATA = yaml.safe_load(Path("./charmcraft.yaml").read_text())
APP_NAME = METADATA["name"]


@pytest.mark.abort_on_fail
async def test_build_and_deploy(ops_test: OpsTest):
    """Build the charm-under-test and deploy it together with related charms.

    Assert on the unit status before any relations/configurations take place.
    """
    # detect CPU architecture
    m = platform.machine()
    arch = "amd64"
    if m in ("aarch64", "arm64"):
        arch = "arm64"

    # build and deploy charm from local source folder
    logger.info("Building charm...")
    charm = await ops_test.build_charm(".")

    bundles = [Path("tests/data/charm.yaml")]
    context = {
        "arch": arch,
        "charm": charm.resolve(),
        "model_name": ops_test.model_name,
        "resources": {
            "argo-rollouts-image": METADATA["resources"]["argo-rollouts-image"]["upstream-source"],
        },
    }
    (bundle,) = await ops_test.async_render_bundles(*bundles, **context)

    logger.info("Deploy Charm...")
    model = ops_test.model_full_name
    cmd = f"juju deploy -m {model} {bundle} --trust"
    rc, stdout, stderr = await ops_test.run(*shlex.split(cmd))
    assert rc == 0, f"Bundle deploy failed: {(stderr or stdout).strip()}"
    logger.info(stdout)

    # issuing dummy update_status just to trigger an event
    async with ops_test.fast_forward():
        await ops_test.model.wait_for_idle(apps=["argo-rollouts"], status="active", timeout=60 * 5)
        assert ops_test.model.applications["argo-rollouts"].units[0].workload_status == "active"


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
