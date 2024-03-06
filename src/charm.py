#!/usr/bin/env python3
# Copyright 2024 Tiexin
# See LICENSE file for licensing details.

"""Charmed Operator for Argo Rollouts.

Upstream doc: https://argoproj.github.io/argo-rollouts/
"""

import logging
import re
import requests
import traceback

from charms.grafana_k8s.v0.grafana_dashboard import GrafanaDashboardProvider
from charms.loki_k8s.v0.loki_push_api import LogProxyConsumer
from charms.prometheus_k8s.v0.prometheus_scrape import MetricsEndpointProvider

from glob import glob
from lightkube import Client, codecs
from lightkube.core.exceptions import ApiError
from ops.charm import CharmBase, PebbleReadyEvent
from ops.main import main
from ops.model import ActiveStatus, BlockedStatus, MaintenanceStatus, WaitingStatus
from ops.pebble import LayerDict, Layer


logger = logging.getLogger(__name__)


class ArgoRolloutsOperatorCharm(CharmBase):
    """Charmed Operator for Argo Rollouts."""

    def __init__(self, *args):
        super().__init__(*args)

        self.pebble_service_name = "argo-rollouts"
        self.container = self.unit.get_container("argo-rollouts")
        self._context = {"namespace": self._namespace, "app_name": self.app.name}

        self.framework.observe(
            self.on.argo_rollouts_pebble_ready, self._argo_rollouts_pebble_ready
        )
        self.framework.observe(self.on.install, self._on_install_or_upgrade)
        self.framework.observe(self.on.upgrade_charm, self._on_install_or_upgrade)
        self.framework.observe(self.on.remove, self._on_remove)

        self._prometheus_scraping = MetricsEndpointProvider(
            self,
            relation_name="metrics-endpoint",
            jobs=[{"static_configs": [{"targets": ["*:8090"]}]}],
            refresh_event=self.on.config_changed,
        )
        self._grafana_dashboards = GrafanaDashboardProvider(
            self, relation_name="grafana-dashboard"
        )
        self._logging = LogProxyConsumer(
            self, relation_name="log-proxy", log_files=["argo-rollouts.log"]
        )

    @property
    def _namespace(self) -> str:
        with open("/var/run/secrets/kubernetes.io/serviceaccount/namespace", "r") as f:
            return f.read().strip()

    def _on_install_or_upgrade(self, event) -> None:
        self.unit.status = MaintenanceStatus("creating kubernetes resources")
        try:
            self._create_kubernetes_resources()
        except ApiError:
            logger.error(traceback.format_exc())
            self.unit.status = BlockedStatus("kubernetes resource creation failed")

    def _create_kubernetes_resources(self) -> bool:
        client = Client(field_manager="argo-rollouts-operator-manager")
        for manifest in glob("src/templates/*.yaml.j2"):
            with open(manifest) as f:
                for resource in codecs.load_all_yaml(f, context=self._context):
                    try:
                        client.apply(resource)
                    except ApiError:
                        logger.debug("failed to create resource: %s.", str(resource.to_dict()))
                        raise
        return True

    def _argo_rollouts_pebble_ready(self, event: PebbleReadyEvent) -> None:
        self.unit.status = MaintenanceStatus("Assembling pod spec")

        if not self._configure_argo_rollouts_pebble_layer():
            self.unit.status = WaitingStatus("Waiting for Pebble in workload container")
        else:
            self._evaluate_argo_rollouts_status()

    def _configure_argo_rollouts_pebble_layer(self) -> bool:
        if not self.container.can_connect():
            return False

        new_layer = self._pebble_layer.to_dict()
        services = self.container.get_plan().to_dict().get("services", {})
        if services != new_layer["services"]:
            self.container.add_layer("argo-rollouts", self._pebble_layer, combine=True)
            logger.info("Added updated layer 'argo_rollouts' to Pebble plan")
            self.container.restart(self.pebble_service_name)
            logger.info(f"Restarted '{self.pebble_service_name}' service")

        self.unit.set_workload_version(self.version)
        self._handle_ports()
        return True

    def _evaluate_argo_rollouts_status(self):
        container = self.unit.get_container("argo-rollouts")
        service = container.can_connect() and container.get_services().get(
            self.pebble_service_name
        )
        if service and service.is_running():
            self.unit.status = ActiveStatus()

    @property
    def _pebble_layer(self) -> LayerDict:
        # https://github.com/argoproj/argo-rollouts/blob/master/Dockerfile#L98C1-L98C42
        # ENTRYPOINT [ "/bin/rollouts-controller" ]
        cmd = "/bin/rollouts-controller"

        pebble_layer = {
            "summary": "Argo Rollouts service",
            "description": "pebble config layer for Argo Rollouts",
            "services": {
                self.pebble_service_name: {
                    "override": "replace",
                    "summary": "Argo Rollouts",
                    "command": cmd,
                    "startup": "enabled",
                }
            },
        }

        return Layer(pebble_layer)

    def _on_remove(self, event):
        self.unit.status = MaintenanceStatus("deleting kubernetes resources")
        try:
            self._delete_kubernetes_resources()
        except ApiError:
            logger.error(traceback.format_exc())
            self.unit.status = BlockedStatus("kubernetes resource deletion failed")

    def _delete_kubernetes_resources(self) -> bool:
        client = Client()
        for manifest in glob("src/templates/*.yaml.j2"):
            with open(manifest) as f:
                for resource in codecs.load_all_yaml(f, context=self._context):
                    try:
                        if not resource.metadata.namespace:
                            client.delete(resource.__class__, resource.metadata.name)
                        else:
                            client.delete(
                                resource.__class__,
                                name=resource.metadata.name,
                                namespace=resource.metadata.namespace,
                            )
                    except ApiError:
                        logger.debug("failed to delete resource: %s.", str(resource.to_dict()))
                        raise
        return True

    @property
    def version(self) -> str:
        """Argo Rollouts controller's version."""
        if self.container.can_connect() and self.container.get_services(self.pebble_service_name):
            try:
                version = self._request_version()
                logger.info(f"applicaiton version: {version}")
                return version
            except Exception as e:
                logger.warning("unable to get version from API: %s", str(e))
                logger.exception(e)
        return ""

    def _request_version(self) -> str:
        version_pattern = re.compile(
            'argo_rollouts_controller_info{version="(v[0-9]+[.][0-9]+[.][0-9]+[+0-9a-f]*)"'
        )
        timeout = 10
        argo_rollouts_metrics_port = 8090

        raw_metrics_text = requests.get(
            f"http://localhost:{argo_rollouts_metrics_port}/metrics", timeout=timeout
        ).text

        m = version_pattern.search(raw_metrics_text)
        return m.groups()[0]

    def _handle_ports(self):
        metrics_port = 8090
        self.unit.set_ports(metrics_port)


if __name__ == "__main__":
    main(ArgoRolloutsOperatorCharm)
