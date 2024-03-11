# Copyright 2024 Tiexin
# See LICENSE file for licensing details.
#
# Learn more about testing at: https://juju.is/docs/sdk/testing

from glob import glob
import unittest
from unittest.mock import MagicMock, Mock, mock_open, patch

from lightkube import codecs
from lightkube.core.exceptions import ApiError
import ops
from ops.model import ActiveStatus, BlockedStatus, WaitingStatus
import ops.testing

from charm import ArgoRolloutsCharm


class _FakeResponse:
    """Fake an httpx response (since lightkube uses httpx) during testing only."""

    def __init__(self, code):
        self.code = code

    def json(self):
        return {"apiVersion": 1, "code": self.code, "message": "broken"}


class _FakeApiError(ApiError):
    """Simulate an ApiError during testing."""

    def __init__(self, code=400):
        super().__init__(response=_FakeResponse(code))


@patch("lightkube.core.client.GenericSyncClient", Mock)
@patch.object(ArgoRolloutsCharm, "version", "v1.6.6+737ca89")
@patch.object(ArgoRolloutsCharm, "_namespace", "test")
class TestCharm(unittest.TestCase):
    @patch.object(ArgoRolloutsCharm, "_namespace", "test")
    def setUp(self):
        self.harness = ops.testing.Harness(ArgoRolloutsCharm)
        self.addCleanup(self.harness.cleanup)
        self.harness.begin()
        self.harness.set_can_connect("argo-rollouts", True)

    def test_argo_rollouts_pebble_ready(self):
        expected_plan = {
            "services": {
                "argo-rollouts": {
                    "override": "replace",
                    "summary": "Argo Rollouts",
                    "command": "/bin/rollouts-controller",
                    "startup": "enabled",
                }
            },
        }

        self.harness.container_pebble_ready("argo-rollouts")
        self.assertEqual(self.harness.charm.container.get_plan().to_dict(), expected_plan)
        self.assertTrue(self.harness.charm.container.get_service("argo-rollouts").is_running())
        self.assertEqual(self.harness.model.unit.status, ops.ActiveStatus())
        self.assertEqual(self.harness.get_workload_version(), "v1.6.6+737ca89")

    @patch.object(ArgoRolloutsCharm, "_create_kubernetes_resources")
    def test_install_event_successful(self, create):
        self.harness.charm.on.install.emit()
        create.assert_called_once()

    @patch.object(
        ArgoRolloutsCharm, "_create_kubernetes_resources", Mock(side_effect=_FakeApiError)
    )
    def test_install_event_fail(self):
        with self.assertLogs("charm") as logs:
            self.harness.charm.on.install.emit()
            self.assertTrue(len(logs) > 0)
        self.assertEqual(
            self.harness.charm.unit.status, BlockedStatus("kubernetes resource creation failed")
        )

    @patch.object(ArgoRolloutsCharm, "_delete_kubernetes_resources")
    def test_remove_event_successful(self, delete):
        self.harness.charm.on.remove.emit()
        delete.assert_called_once()

    @patch.object(
        ArgoRolloutsCharm, "_delete_kubernetes_resources", Mock(side_effect=_FakeApiError)
    )
    def test_remove_event_fail(self):
        with self.assertLogs("charm") as logs:
            self.harness.charm.on.remove.emit()
            self.assertTrue(len(logs) > 0)

    @patch("charm.Client.apply")
    def test_create_kubernetes_resources_success(self, apply: MagicMock):
        self.harness.charm._context = {
            "namespace": "test",
            "app_name": "argo-rollouts",
        }

        result = self.harness.charm._create_kubernetes_resources()
        self.assertTrue(result)

        resources = []
        for manifest in glob("src/templates/*.yaml.j2"):
            with open(manifest) as f:
                resources.extend(list(codecs.load_all_yaml(f, self.harness.charm._context)))

        for resource in resources:
            apply.assert_any_call(resource)

    @patch("charm.Client.apply")
    @patch("charm.ApiError", _FakeApiError)
    def test_create_kubernetes_resources_failure(self, client: MagicMock):
        client.side_effect = _FakeApiError()
        with self.assertRaises(ApiError):
            self.harness.charm._create_kubernetes_resources()

        with self.assertLogs("charm", "DEBUG") as logs:
            try:
                self.harness.charm._create_kubernetes_resources()
            except ApiError:
                self.assertIn("failed to create resource:", ";".join(logs.output))

    @patch("charm.Client.delete")
    def test_delete_kubernetes_resources_success(self, delete: MagicMock):
        self.harness.charm._context = {
            "namespace": "test",
            "app_name": "argo-rollouts",
        }

        result = self.harness.charm._delete_kubernetes_resources()
        self.assertTrue(result)

        resources = []
        for manifest in glob("src/templates/*.yaml.j2"):
            with open(manifest) as f:
                resources.extend(list(codecs.load_all_yaml(f, self.harness.charm._context)))

        for resource in resources:
            if not resource.metadata.namespace:
                delete.assert_any_call(resource.__class__, resource.metadata.name)
            else:
                delete.assert_any_call(
                    resource.__class__,
                    name=resource.metadata.name,
                    namespace=resource.metadata.namespace,
                )

    @patch("charm.Client.delete")
    @patch("charm.ApiError", _FakeApiError)
    def test_delete_kubernetes_resources_failure(self, client: MagicMock):
        client.side_effect = _FakeApiError()
        with self.assertRaises(ApiError):
            self.harness.charm._delete_kubernetes_resources()

        with self.assertLogs("charm", "DEBUG") as logs:
            try:
                self.harness.charm._delete_kubernetes_resources()
            except ApiError:
                self.assertIn("failed to delete resource:", ";".join(logs.output))

    def _argo_rollouts_service(self, *, started=False) -> None:
        container = self.harness.charm.unit.get_container("argo-rollouts")
        layer = {"services": {"argo-rollouts": {}}}
        container.add_layer("argo-rollouts", layer, combine=True)
        if started:
            container.start("argo-rollouts")

    def test_argo_rollouts_status(self):
        self._argo_rollouts_service(started=True)
        self.harness.charm.unit.status = WaitingStatus()
        assert self.harness.charm._argo_rollouts_status() == ActiveStatus()

    def test_argo_rollouts_status_no_service(self):
        self.harness.charm.unit.status = BlockedStatus()
        assert self.harness.charm._argo_rollouts_status() == WaitingStatus(
            "Waiting for Argo Rollouts service"
        )


class TestCharmNamespaceProperty(unittest.TestCase):
    def tearDown(self):
        harness = ops.testing.Harness(ArgoRolloutsCharm)
        harness.cleanup()

    @patch("builtins.open", new_callable=mock_open, read_data="test")
    def test_property_namespace(self, mock):
        harness = ops.testing.Harness(ArgoRolloutsCharm)
        harness.begin()
        self.assertEqual(harness.charm._namespace, "test")
        mock.assert_called_with("/var/run/secrets/kubernetes.io/serviceaccount/namespace", "r")
