import logging
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

import click
import yaml
from rich.console import Console
from rich.syntax import Syntax

try:
    import git
except ImportError:
    git = None

console = Console()


@click.group()
@click.option("--debug", is_flag=True, help="Enable debug logging")
def cli(debug):
    """BigBang Inflator CLI"""
    logging.basicConfig(
        level=logging.DEBUG if debug else logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
    )
    if debug:
        logging.debug("Debug logging is enabled.")


@cli.command()
@click.option(
    "--input",
    "-i",
    "input_file",
    type=click.File("r"),
    default="-",
    help="YAML manifest file (default: stdin)",
)
def extract_values(input_file):
    """Extract values.yaml from a ConfigMap manifest and output as YAML."""
    try:
        manifest = yaml.safe_load(input_file)
        values_yaml = manifest.get("data", {}).get("values.yaml")
        if values_yaml is None:
            console.print("[red]No values.yaml key found in data.[/red]")
            sys.exit(1)
        # Parse the values.yaml string as YAML
        values_data = yaml.safe_load(values_yaml)
        yaml_output = yaml.dump(values_data, sort_keys=False)
        # Strip all double newlines
        yaml_output = yaml_output.replace("\n\n", "\n")
        syntax = Syntax(yaml_output, "yaml", theme="monokai", line_numbers=False)
        console.print(syntax)
    except Exception as e:
        console.print(f"[red]Error: {e}[/red]")
        sys.exit(1)


@cli.command()
@click.option(
    "--repo-url",
    required=True,
    help="Git repository URL to clone (e.g., https://repo1.dso.mil/big-bang/bigbang.git)",
)
@click.option(
    "--ref",
    required=True,
    help="Git reference (branch, tag, or commit) to checkout (e.g., 2.52.0)",
)
@click.option(
    "--subdir",
    default="",
    help="Subdirectory within the repo to run kustomize build (default: repo root)",
)
def inflate(repo_url, ref, subdir):
    """Clone a BigBang repo, checkout ref, and run kustomize build on it."""
    if git is None:
        console.print(
            "[red]GitPython is required for this command. Install with: uv add gitpython[/red]"
        )
        sys.exit(1)
    temp_dir = tempfile.mkdtemp(prefix="bb-inflator-")
    try:
        console.print(f"[green]Cloning {repo_url}@{ref}...[/green]")
        repo = git.Repo.clone_from(repo_url, temp_dir)
        repo.git.checkout(ref)
        kustomize_path = Path(temp_dir) / subdir if subdir else Path(temp_dir)
        console.print(f"[green]Running kustomize build in {kustomize_path}...[/green]")
        result = subprocess.run(
            ["kustomize", "build", str(kustomize_path)], capture_output=True, text=True
        )
        if result.returncode != 0:
            console.print(f"[red]kustomize build failed:[/red]\n{result.stderr}")
            sys.exit(result.returncode)
        console.print(result.stdout)
    except Exception as e:
        console.print(f"[red]Error: {e}[/red]")
        sys.exit(1)
    finally:
        shutil.rmtree(temp_dir)


def parse_kustomization_for_git_info(kustomization_path, seen=None):
    """Recursively parse kustomization.yaml to extract base git repo URL and ref."""
    if seen is None:
        seen = set()
    kustom_file = os.path.join(kustomization_path, "kustomization.yaml")
    if not os.path.exists(kustom_file):
        kustom_file = os.path.join(kustomization_path, "kustomization.yml")
    if not os.path.exists(kustom_file):
        logging.error(
            "No kustomization.yaml or kustomization.yml found in the specified directory."
        )
        raise FileNotFoundError(
            "No kustomization.yaml or kustomization.yml found in the specified directory."
        )
    logging.debug(f"Using kustomization file: {kustom_file}")
    with open(kustom_file, "r") as f:
        kustom = yaml.safe_load(f)
    logging.debug(f"Parsed kustomization.yaml: {kustom}")
    bases = kustom.get("bases", [])
    logging.debug(f"bases: {bases}")
    for base in bases:
        logging.debug(f"Checking base: {base}")
        if base.startswith("git::"):
            # Found the git base!
            base_stripped = base[len("git::") :]
            logging.debug(f"Stripped base: {base_stripped}")
            if "?ref=" in base_stripped:
                url_part, ref_part = base_stripped.split("?ref=")
                logging.debug(f"url_part: {url_part}, ref_part: {ref_part}")
                # Robustly split repo_url and subdir
                if url_part.startswith("https://") or url_part.startswith("http://"):
                    proto_sep = url_part.find("://") + 3
                    subdir_sep = url_part.find("//", proto_sep)
                    if subdir_sep != -1:
                        repo_url = url_part[:subdir_sep]
                        subdir = url_part[subdir_sep + 2 :]
                    else:
                        repo_url = url_part
                        subdir = ""
                else:
                    # fallback for other cases
                    if "//" in url_part:
                        repo_url, subdir = url_part.split("//", 1)
                    else:
                        repo_url = url_part
                        subdir = ""
                repo_url = repo_url.rstrip("/")
                subdir = subdir.lstrip("/")
                ref = ref_part
                logging.debug(
                    f"Parsed repo_url: {repo_url}, subdir: {subdir}, ref: {ref}"
                )
            else:
                repo_url = base_stripped
                subdir = ""
                ref = None
                logging.debug(f"Parsed repo_url (no ref): {repo_url}")
            return repo_url, ref, subdir
        elif not base.startswith("git::"):
            # Local base, recurse
            local_base_path = os.path.normpath(os.path.join(kustomization_path, base))
            logging.debug(f"Recursing into local base: {local_base_path}")
            if local_base_path in seen:
                logging.error(f"Circular base reference detected: {local_base_path}")
                continue
            seen.add(local_base_path)
            try:
                return parse_kustomization_for_git_info(local_base_path, seen)
            except Exception as e:
                logging.debug(f"Failed to parse base {local_base_path}: {e}")
                continue
    # Optionally, try to get ref from patchesStrategicMerge if not found
    ref = None
    repo_url = None
    subdir = ""
    patches = kustom.get("patchesStrategicMerge", [])
    logging.debug(f"patchesStrategicMerge: {patches}")
    for patch in patches:
        if isinstance(patch, str) and "kind: GitRepository" in patch:
            lines = patch.splitlines()
            for i, line in enumerate(lines):
                if "tag:" in line:
                    ref = line.split("tag:")[1].strip().strip('"')
                    logging.debug(f"Found ref in patch: {ref}")
    if not repo_url or not ref:
        logging.error(
            f"Could not parse git repo URL and ref from kustomization.yaml. repo_url: {repo_url}, ref: {ref}"
        )
        raise ValueError("Could not parse git repo URL and ref from kustomization.yaml")
    logging.debug(f"Returning repo_url: {repo_url}, ref: {ref}, subdir: {subdir}")
    return repo_url, ref, subdir


@cli.command()
@click.argument(
    "kustomization_dir", type=click.Path(exists=True, file_okay=False, dir_okay=True)
)
def inflate_from_kustomization(kustomization_dir):
    """Inflate manifests from a kustomization directory by parsing its base git repo and ref."""
    try:
        repo_url, ref, subdir = parse_kustomization_for_git_info(kustomization_dir)
        console.print(
            f"[green]Parsed repo: {repo_url}, ref: {ref}, subdir: {subdir}[/green]"
        )
        # Reuse inflate logic
        ctx = click.get_current_context()
        ctx.invoke(inflate, repo_url=repo_url, ref=ref, subdir=subdir)
    except Exception as e:
        console.print(f"[red]Error: {e}[/red]")
        sys.exit(1)


@cli.command()
@click.argument(
    "kustomization_dir", type=click.Path(exists=True, file_okay=False, dir_okay=True)
)
def extract_values_from_kustomization(kustomization_dir):
    """Run kustomize build on a kustomization dir and merge values.yaml from ConfigMaps in HelmRelease.valuesFrom order."""
    try:
        logging.debug(f"Running kustomize build in {kustomization_dir}")
        result = subprocess.run(
            ["kustomize", "build", str(kustomization_dir)],
            capture_output=True,
            text=True,
        )
        if result.returncode != 0:
            console.print(f"[red]kustomize build failed:[/red]\n{result.stderr}")
            sys.exit(result.returncode)
        # Parse the multi-document YAML output
        logging.debug(
            "Parsing kustomize build output for ConfigMaps and HelmRelease..."
        )
        docs = list(yaml.safe_load_all(result.stdout))
        # Find all ConfigMaps with values.yaml
        configmaps = {}
        for doc in docs:
            if (
                isinstance(doc, dict)
                and doc.get("kind") == "ConfigMap"
                and "values.yaml" in doc.get("data", {})
            ):
                name = doc.get("metadata", {}).get("name", "<no-name>")
                configmaps[name] = doc["data"]["values.yaml"]
                logging.debug(f"Found ConfigMap: {name}")
        # Find HelmRelease and its valuesFrom order
        helmrelease = None
        for doc in docs:
            if (
                isinstance(doc, dict)
                and doc.get("kind") == "HelmRelease"
                and doc.get("metadata", {}).get("name") == "bigbang"
            ):
                helmrelease = doc
                break
        if not helmrelease:
            console.print(
                "[red]No HelmRelease named 'bigbang' found in kustomize output.[/red]"
            )
            sys.exit(1)
        values_from = helmrelease.get("spec", {}).get("valuesFrom", [])
        logging.debug(f"HelmRelease valuesFrom: {values_from}")
        # Merge values.yaml from ConfigMaps in order
        merged_values = {}
        for entry in values_from:
            if entry.get("kind") == "ConfigMap":
                cm_name = entry.get("name")
                if cm_name in configmaps:
                    values_yaml = configmaps[cm_name]
                    logging.debug(f"Merging values from ConfigMap: {cm_name}")
                    values_data = yaml.safe_load(values_yaml)
                    if values_data:
                        merged_values = deep_merge(merged_values, values_data)
        if not merged_values:
            console.print(
                "[red]No values.yaml data found in referenced ConfigMaps.[/red]"
            )
            sys.exit(1)
        yaml_output = yaml.dump(merged_values, sort_keys=False)
        yaml_output = yaml_output.replace("\n\n", "\n")
        syntax = Syntax(yaml_output, "yaml", theme="monokai", line_numbers=False)
        console.print(syntax)
    except Exception as e:
        console.print(f"[red]Error: {e}[/red]")
        sys.exit(1)


def deep_merge(a, b):
    """Recursively merge dict b into dict a."""
    if not isinstance(a, dict) or not isinstance(b, dict):
        return b
    result = dict(a)
    for k, v in b.items():
        if k in result and isinstance(result[k], dict) and isinstance(v, dict):
            result[k] = deep_merge(result[k], v)
        else:
            result[k] = v
    return result


if __name__ == "__main__":
    cli()
