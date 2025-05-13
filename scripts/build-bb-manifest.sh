#!/bin/bash

set -o errexit  # Exit immediately if a command exits with a non-zero status
set -o nounset  # Treat unset variables as an error and exit immediately
set -o pipefail # Return the exit status of the last command in the pipeline that failed

# Get the directory to build from the user
echo "This script extracts manifest.yaml from BigBang kustomization directories."
# Get the kustomize directory from the first argument
KUSTOMIZE_DIR="${1:-}"

# If no argument was provided, show usage and exit
if [ -z "$KUSTOMIZE_DIR" ]; then
    echo "Usage: $0 <kustomize_directory>"
    exit 1
fi

# Validate the input
if [ ! -d "$KUSTOMIZE_DIR" ]; then
    echo "Error: Directory '$KUSTOMIZE_DIR' does not exist."
    exit 1
fi

# Check if kustomization.yaml exists
if [ ! -f "$KUSTOMIZE_DIR/kustomization.yaml" ] && [ ! -f "$KUSTOMIZE_DIR/kustomization.yml" ]; then
    echo "Error: No kustomization.yaml or kustomization.yml found in '$KUSTOMIZE_DIR'."
    exit 1
fi

# Ensure output and repos directories exist
mkdir -p ./generated
mkdir -p ./repos

# Run kustomize build on the directory
echo "Building manifest from kustomization directory: $KUSTOMIZE_DIR"
echo "Running kustomize build..."
kustomize build "$KUSTOMIZE_DIR" > ./generated/manifest.yaml

# Check if the command was successful
if [ $? -ne 0 ]; then
    echo "Error: Failed to build manifest from kustomization directory."
    exit 1
fi

echo "Manifest extraction completed successfully."

# Now let's build the bb values.yaml

# First decode the secrets into plain yaml using sops
sops -d $KUSTOMIZE_DIR/secrets.enc.yaml | yq eval '.stringData."values.yaml"' \
    | yq \
    > ./generated/env-secrets.yaml
sops -d $KUSTOMIZE_DIR-base/secrets.enc.yaml | yq eval '.stringData."values.yaml"' \
    | yq \
    > ./generated/common-secrets.yaml

# Now apply in order of bigbang helmrelease precedence.
#
# Here's the order of precedence from the bigbang HelmRelease:
#   valuesFrom:
#     - kind: Secret
#       name: terraform
#       optional: true
#     - kind: Secret
#       name: common-bb-t6c5mb78tc
#     - kind: ConfigMap
#       name: common-9gcmk9fdkt
#     - kind: Secret
#       name: environment-bb-bd6bhm86f6
#     - kind: ConfigMap
#       name: environment-cc7hhg7f2b
# 
# Replicate it here:
yq ea '. as $item ireduce ({}; . * $item )' \
    ./generated/env-secrets.yaml \
    $KUSTOMIZE_DIR-base/configmap.yaml \
    ./generated/common-secrets.yaml \
    $KUSTOMIZE_DIR/configmap.yaml \
    > ./generated/values.yaml

# Now let's clone the bigbang repo locally. The exact version is set in the base 
# kustomization.yaml file.

# Extract the tag from kustomization.yaml (from either location)
TAG=$(yq eval '.bases[] | select(.) | split("?ref=") | .[1]' ${KUSTOMIZE_DIR}-base/kustomization.yaml | head -n1)

# Check if bigbang repo already exists
if [ -d "./repos/bigbang" ]; then
    echo "BigBang repo already exists, updating..."
    (
        cd ./repos/bigbang
        git fetch
        git checkout "$TAG"
    )
else
    echo "Cloning BigBang repo..."
    git clone https://repo1.dso.mil/big-bang/bigbang.git ./repos/bigbang
    (
        cd ./repos/bigbang
        git checkout "$TAG"
    )
fi

# Now let's use the values.yaml to build the BigBang Helm Chart manifests.
echo "Running helm template with BigBang values..."
helm template bigbang ./repos/bigbang/chart -f ./generated/values.yaml > ./generated/bigbang-manifests.yaml

# Check if the command was successful
if [ $? -ne 0 ]; then
    echo "Error: Failed to generate BigBang manifests with helm template."
    exit 1
fi

echo "BigBang manifests generated successfully at ./generated/bigbang-manifests.yaml"
