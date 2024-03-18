# Manual Testing

This test validates the rollouts works as expected.

---

## 1 Requirements

- `kubectl` with `argo-rollouts` plugin installed ([see install guide here](https://argoproj.github.io/argo-rollouts/installation/#kubectl-plugin-installation))

---

## 2 Steps

### 2.1 Deploying a Rollout

For mac M1 users:

Run the following command to deploy the initial Rollout and Service:

```bash
kubectl apply -f rollout.yaml
kubectl apply -f service.yaml
```

For linux/amd64 users, edit `rollout.yaml` and change the image to `image: argoproj/rollouts-demo:blue`.

To watch the rollout as it deploys, run the `get rollout --watch` command from plugin:

```bash
kubectl argo rollouts get rollout rollouts-demo --watch
```
### 2.2 Updating a Rollout

For mac M1 users, run the command to update the image:

```bash
kubectl argo rollouts set image rollouts-demo \
  rollouts-demo=vnovoselskiy/rollouts-demo:yellow
```

For linux/amd64 users, update the image tag from the above command to `argoproj/rollouts-demo:yellow`.

During the rollout, you can run the same `kubectl argo rollouts get rollout rollouts-demo --watch` command to see the status.

### 2.3 Promoting a Rollout

Run the following command to promote the rollout:

```bash
kubectl argo rollouts promote rollouts-demo
```

During the automated promation rolling out process, you can run the same `kubectl argo rollouts get rollout rollouts-demo --watch` command to see the status.

---

## 3 Clean Up

```bash
kubectl delete -f service.yaml
kubectl delete -f rollout.yaml
````
