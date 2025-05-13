#!/bin/bash

set -o errexit  # Exit immediately if a command exits with a non-zero status
set -o nounset  # Treat unset variables as an error and exit immediately
set -o pipefail # Return the exit status of the last command in the pipeline that failed

# Usage function
usage() {
    echo "Usage: $0 --kustomize-directory <dir> [--name <component-name>] [--list-names]"
    exit 1
}

# Parse arguments
NAME=""
KUSTOMIZE_DIR=""
LIST_NAMES=false
while [[ $# -gt 0 ]]; do
    case $1 in
        --name)
            NAME="$2"
            shift 2
            ;;
        --kustomize-directory)
            KUSTOMIZE_DIR="$2"
            shift 2
            ;;
        --list-names)
            LIST_NAMES=true
            shift 1
            ;;
        *)
            usage
            ;;
    esac
done

if [[ -z "$KUSTOMIZE_DIR" ]]; then
    echo "Error: --kustomize-directory is required."
    usage
fi

# Always run ./build-bb-manifest.sh first.
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
"$SCRIPT_DIR/build-bb-manifest.sh" "$KUSTOMIZE_DIR"

if $LIST_NAMES; then
    echo "Available --name options:"
    yq -r 'select(.kind=="GitRepository") | .metadata.name' ./generated/bigbang-manifests.yaml | grep -v '^---$'
    exit 0
fi

if [[ -z "$NAME" ]]; then
    usage
fi

# Get the GitRepo reference and tag from the bigbang manifest for the given name.
REPO_URL=$(yq "select(.kind==\"GitRepository\" and .metadata.name==\"$NAME\") | .spec.url" \
    ./generated/bigbang-manifests.yaml)
# Try to get tag first, if not found try branch
TAG=$(yq "select(.kind==\"GitRepository\" and .metadata.name==\"$NAME\") | .spec.ref.tag // .spec.ref.branch" \
    ./generated/bigbang-manifests.yaml)

# Extract repo name from URL (e.g., istio-controlplane from .../istio-controlplane.git)
REPO_NAME=$(basename -s .git "$REPO_URL")

# Create repos directory if it doesn't exist
mkdir -p ./repos

# Check if repo already exists
if [ -d "./repos/$REPO_NAME" ]; then
    echo "$REPO_NAME repo already exists, updating..."
    (
        cd "./repos/$REPO_NAME"
        git fetch
        git checkout "$TAG"
    )
else
    echo "Cloning $REPO_NAME repo..."
    git clone "$REPO_URL" "./repos/$REPO_NAME"
    (
        cd "./repos/$REPO_NAME"
        git checkout "$TAG"
    )
fi

# Build the manifest using helm template
# Get the chart path from the HelmRelease
CHART_PATH=$(yq "select(.kind==\"HelmRelease\" and .metadata.name==\"$NAME\") | .spec.chart.spec.chart" ./generated/bigbang-manifests.yaml)

helm template "$NAME" "./repos/$REPO_NAME/$CHART_PATH" -f ./generated/values.yaml > ./generated/$NAME-manifests.yaml