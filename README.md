## CLI Usage

This project provides a CLI tool to work with BigBang Helm chart manifests.

### Extract `values.yaml` from a ConfigMap manifest

You can extract the `values.yaml` key from a Kubernetes ConfigMap manifest and output it as YAML:

```sh
cat manifest.yaml | python -m bb_inflator.cli extract-values
```

Or from a file:

```sh
python -m bb_inflator.cli extract-values --input manifest.yaml
```

The output will be syntax-highlighted YAML, suitable for piping into `yq` or saving to a file.

### Inflate BigBang manifests from a Git repo

Clone a BigBang repo at a specific ref and render all manifests using kustomize:

```sh
python -m bb_inflator.cli inflate --repo-url https://repo1.dso.mil/big-bang/bigbang.git --ref 2.52.0 --subdir base
```

- `--repo-url`: The Git repository to clone (required)
- `--ref`: The branch, tag, or commit to checkout (required)
- `--subdir`: Subdirectory within the repo to run kustomize build (optional, default is repo root)

The output will be all rendered manifests, suitable for further processing with `yq` or saving to a file.

### Inflate from a kustomization directory

You can also inflate manifests by pointing the CLI at a kustomization directory. The CLI will parse the base git repo and ref from the kustomization.yaml and inflate accordingly:

```sh
python -m bb_inflator.cli inflate-from-kustomization <path-to-kustomization-dir>
```

This will automatically:
- Parse the `bases` entry in your `kustomization.yaml` for the git repo and ref
- Clone the repo and checkout the correct ref
- Run `kustomize build` on the correct subdirectory
- Output all manifests to stdout
