# 🛠️ Usage

## ⚡ TL;DR

Most common usage:

```sh
# Replace "istio-controlplane" with any package you want to see manifests for...
./scripts/get-manifests.sh --kustomize-directory <dir> --name istio-controlplane
```

## Prerequisites ⚙️

- [kustomize](https://kubectl.docs.kubernetes.io/installation/kustomize/)
- [sops](https://github.com/mozilla/sops)
- [yq](https://github.com/mikefarah/yq)
- [helm](https://helm.sh/)
- [git](https://git-scm.com/)

## Scripts 📂

### 1. `build-bb-manifest.sh` 🚀

Extracts and builds manifests from a BigBang kustomization directory, decodes secrets, merges values, clones the BigBang repo, and generates Helm manifests.

**Usage:**

```sh
./scripts/build-bb-manifest.sh <kustomize_directory>
```

- `<kustomize_directory>`: Path to the directory containing your `kustomization.yaml`.

**Outputs:**

- `./generated/manifest.yaml`
- `./generated/values.yaml`
- `./generated/bigbang-manifests.yaml`

---

### 2. `get-manifests.sh` 📦

Fetches and builds manifests for a specific BigBang component by name, or lists available component names.

**Usage:**

```sh
./scripts/get-manifests.sh --kustomize-directory <dir> --name <component-name>
```

- `--kustomize-directory <dir>`: Path to the kustomize directory (**required**).
- `--name <component-name>`: Name of the component to fetch (**required** unless using `--list-names`).
- `--list-names`: List available component names.

**Examples:**

- List available component names:

  ```sh
  ./scripts/get-manifests.sh --kustomize-directory <dir> --list-names
  ```

- Get manifests for a specific component:

  ```sh
  ./scripts/get-manifests.sh --kustomize-directory <dir> --name istio-controlplane
  ```

**Outputs:**

- `./generated/<component-name>-manifests.yaml`

---
