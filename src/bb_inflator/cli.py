import glob
import logging
import os
import re
import shutil
import subprocess
import sys
import tempfile
from contextlib import redirect_stdout
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
    """Run kustomize build on a kustomization dir and merge values.yaml from ConfigMaps and Secrets in HelmRelease.valuesFrom order, decrypting SOPS-encrypted values inline."""
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
            "Parsing kustomize build output for ConfigMaps, Secrets, and HelmRelease..."
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
        # Recursively find and decrypt all secrets.enc.yaml files
        secret_files = find_secrets_files_recursive(kustomization_dir)
        logging.debug(f"Found secrets.enc.yaml files: {secret_files}")
        secrets = {}
        for secret_file in secret_files:
            try:
                logging.debug(f"Decrypting {secret_file} with sops...")
                sops_result = subprocess.run(
                    ["sops", "-d", secret_file],
                    capture_output=True,
                    text=True,
                    cwd=os.path.dirname(secret_file),
                )
                if sops_result.returncode != 0:
                    logging.warning(
                        f"Failed to decrypt {secret_file}: {sops_result.stderr}"
                    )
                    continue
                secret_docs = list(yaml.safe_load_all(sops_result.stdout))
                for doc in secret_docs:
                    if (
                        isinstance(doc, dict)
                        and doc.get("kind") == "Secret"
                        and "values.yaml" in doc.get("stringData", {})
                    ):
                        name = doc.get("metadata", {}).get("name", "<no-name>")
                        secrets[name] = doc["stringData"]["values.yaml"]
                        logging.debug(f"Found Secret: {name}")
            except Exception as e:
                logging.warning(f"Error processing {secret_file}: {e}")
                continue
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
        # Merge values.yaml from ConfigMaps and Secrets in order
        merged_values = {}
        for entry in values_from:
            kind = entry.get("kind")
            name = entry.get("name")
            if kind == "ConfigMap" and name in configmaps:
                values_yaml = configmaps[name]
                logging.debug(f"Merging values from ConfigMap: {name}")
                values_data = yaml.safe_load(values_yaml)
                if values_data:
                    merged_values = deep_merge(merged_values, values_data)
            elif kind == "Secret":
                # Try exact match first
                values_yaml = secrets.get(name)
                matched_secret = name
                # If not found, try prefix match
                if values_yaml is None:
                    for base_name, secret_val in secrets.items():
                        if name.startswith(base_name):
                            values_yaml = secret_val
                            matched_secret = base_name
                            logging.debug(f"Prefix match: {name} -> {base_name}")
                            break
                if values_yaml:
                    logging.debug(
                        f"Merging values from Secret: {matched_secret} (for {name})"
                    )
                    values_data = yaml.safe_load(values_yaml)
                    if values_data:
                        merged_values = deep_merge(merged_values, values_data)
        if not merged_values:
            console.print(
                "[red]No values.yaml data found in referenced ConfigMaps or Secrets.[/red]"
            )
            sys.exit(1)

        def str_presenter(dumper, data):
            if "\n" in data:
                return dumper.represent_scalar("tag:yaml.org,2002:str", data, style="|")
            return dumper.represent_scalar("tag:yaml.org,2002:str", data)

        yaml.add_representer(str, str_presenter)
        yaml_output = yaml.dump(merged_values, sort_keys=False)
        yaml_output = yaml_output.replace("\n\n", "\n")
        syntax = Syntax(yaml_output, "yaml", theme="monokai", line_numbers=False)
        console.print(syntax)
    except Exception as e:
        console.print(f"[red]Error: {e}[/red]")
        sys.exit(1)


def find_secrets_files_recursive(start_dir):
    """Recursively find all secrets.enc.yaml files in start_dir and its bases."""
    found = set()
    stack = [os.path.abspath(start_dir)]
    seen = set()
    while stack:
        current = stack.pop()
        if current in seen:
            continue
        seen.add(current)
        # Find secrets.enc.yaml in this dir
        for file in glob.glob(os.path.join(current, "secrets.enc.yaml")):
            found.add(os.path.abspath(file))
        # Recurse into local bases
        kustom_file = os.path.join(current, "kustomization.yaml")
        if not os.path.exists(kustom_file):
            kustom_file = os.path.join(current, "kustomization.yml")
        if not os.path.exists(kustom_file):
            continue
        with open(kustom_file, "r") as f:
            kustom = yaml.safe_load(f)
        for base in kustom.get("bases", []):
            if not base.startswith("git::"):
                base_path = os.path.normpath(os.path.join(current, base))
                stack.append(base_path)
    return list(found)


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


@cli.command()
@click.argument(
    "kustomization_dir", type=click.Path(exists=True, file_okay=False, dir_okay=True)
)
def print_secret_values(kustomization_dir):
    """Print all decrypted values.yaml from secrets.enc.yaml files."""
    try:
        secret_files = find_secrets_files_recursive(kustomization_dir)
        logging.debug(f"Found secrets.enc.yaml files: {secret_files}")
        found = False
        for secret_file in secret_files:
            try:
                logging.debug(f"Decrypting {secret_file} with sops...")
                sops_result = subprocess.run(
                    ["sops", "-d", os.path.basename(secret_file)],
                    capture_output=True,
                    text=True,
                    cwd=os.path.dirname(secret_file),
                )
                if sops_result.returncode != 0:
                    logging.warning(
                        f"Failed to decrypt {secret_file}: {sops_result.stderr}"
                    )
                    continue
                secret_docs = list(yaml.safe_load_all(sops_result.stdout))
                for doc in secret_docs:
                    if (
                        isinstance(doc, dict)
                        and doc.get("kind") == "Secret"
                        and "values.yaml" in doc.get("stringData", {})
                    ):
                        name = doc.get("metadata", {}).get("name", "<no-name>")
                        values_yaml = doc["stringData"]["values.yaml"]
                        console.print(f"[bold green]Secret: {name}[/bold green]")
                        syntax = Syntax(
                            values_yaml, "yaml", theme="monokai", line_numbers=False
                        )
                        console.print(syntax)
                        found = True
            except Exception as e:
                logging.warning(f"Error processing {secret_file}: {e}")
                continue
        if not found:
            console.print("[red]No decrypted values.yaml found in any secrets.[/red]")
            sys.exit(1)
    except Exception as e:
        console.print(f"[red]Error: {e}[/red]")
        sys.exit(1)


@cli.command()
@click.argument(
    "kustomization_dir", type=click.Path(exists=True, file_okay=False, dir_okay=True)
)
def helm_template_with_values(kustomization_dir):
    """Render Helm chart from Git repo using merged values.yaml from kustomization."""
    import io
    import shutil
    import tempfile

    logging.debug(f"Extracting merged values.yaml from {kustomization_dir}")
    buf = io.StringIO()
    try:
        with redirect_stdout(buf):
            extract_values_from_kustomization.callback(kustomization_dir)
        merged_values_yaml = buf.getvalue()
        print("==== Merged values.yaml to be written ====")
        print(merged_values_yaml)
        print("==========================================")
        # Sanitize: remove control characters except newlines and tabs
        merged_values_yaml = re.sub(r"[^\x09\x0A\x0D\x20-\x7E]", "", merged_values_yaml)
        logging.debug(f"Sanitized merged values.yaml:\n{merged_values_yaml}")
    except Exception as e:
        console.print(f"[red]Failed to extract merged values.yaml: {e}[/red]")
        sys.exit(1)
    # Parse base repo/ref/subdir
    try:
        repo_url, ref, subdir = parse_kustomization_for_git_info(kustomization_dir)
        logging.debug(f"Parsed base repo: {repo_url}, ref: {ref}, subdir: {subdir}")
    except Exception as e:
        console.print(f"[red]Failed to parse base repo info: {e}[/red]")
        sys.exit(1)
    # Clone the repo
    if git is None:
        console.print(
            "[red]GitPython is required for this command. Install with: uv add gitpython[/red]"
        )
        sys.exit(1)
    temp_repo_dir = tempfile.mkdtemp(prefix="bb-inflator-repo-")
    temp_values_file = tempfile.NamedTemporaryFile("w+", delete=False, suffix=".yaml")
    try:
        console.print(f"[green]Cloning {repo_url}@{ref}...[/green]")
        repo = git.Repo.clone_from(repo_url, temp_repo_dir)
        repo.git.checkout(ref)
        chart_path = os.path.join(temp_repo_dir, subdir) if subdir else temp_repo_dir
        logging.debug(f"Chart path for helm template: {chart_path}")
        # Write merged values.yaml to temp file
        temp_values_file.write(merged_values_yaml)
        temp_values_file.flush()
        # Run helm template
        console.print("[green]Running helm template...[/green]")
        result = subprocess.run(
            ["helm", "template", "bigbang", chart_path, "-f", temp_values_file.name],
            capture_output=True,
            text=True,
        )
        if result.returncode != 0:
            console.print(f"[red]helm template failed:[/red]\n{result.stderr}")
            sys.exit(result.returncode)
        console.print(result.stdout)
    except Exception as e:
        console.print(f"[red]Error: {e}[/red]")
        sys.exit(1)
    finally:
        shutil.rmtree(temp_repo_dir)
        temp_values_file.close()
        try:
            os.unlink(temp_values_file.name)
        except Exception:
            pass


def copy_and_rewrite_kustomization(src_dir, dest_dir, base_path_map, repo_local_base):
    """Recursively copy kustomization dir and all local bases, rewriting bases to local paths."""
    import os
    import shutil

    import yaml

    os.makedirs(dest_dir, exist_ok=True)
    kustom_file = os.path.join(src_dir, "kustomization.yaml")
    if not os.path.exists(kustom_file):
        kustom_file = os.path.join(src_dir, "kustomization.yml")
    if not os.path.exists(kustom_file):
        raise FileNotFoundError(
            f"No kustomization.yaml or kustomization.yml found in {src_dir}"
        )
    with open(kustom_file, "r") as f:
        kustom = yaml.safe_load(f)
    new_bases = []
    for base in kustom.get("bases", []):
        if base.startswith("git::"):
            new_bases.append(repo_local_base)
            logging.debug(f"Replaced git:: base {base} with {repo_local_base}")
        elif not base.startswith("git::"):
            # Local base: copy recursively
            abs_base = os.path.normpath(os.path.join(src_dir, base))
            rel_base = os.path.relpath(abs_base, start=base_path_map["root_src"])
            dest_base = os.path.join(base_path_map["root_dest"], rel_base)
            copy_and_rewrite_kustomization(
                abs_base, dest_base, base_path_map, repo_local_base
            )
            new_bases.append(rel_base)
            logging.debug(
                f"Rewrote local base {base} to {rel_base} and copied to {dest_base}"
            )
    kustom["bases"] = new_bases
    # Copy all files except kustomization.yaml/yml
    for item in os.listdir(src_dir):
        s = os.path.join(src_dir, item)
        d = os.path.join(dest_dir, item)
        if os.path.isdir(s):
            if item not in [".git", "__pycache__"]:
                shutil.copytree(s, d, dirs_exist_ok=True)
        elif not item.startswith("kustomization.yaml") and not item.startswith(
            "kustomization.yml"
        ):
            shutil.copy2(s, d)
    # Write the rewritten kustomization.yaml
    with open(os.path.join(dest_dir, "kustomization.yaml"), "w") as f:
        yaml.dump(kustom, f, sort_keys=False)


@cli.command()
@click.argument(
    "kustomization_dir", type=click.Path(exists=True, file_okay=False, dir_okay=True)
)
def kustomize_build_with_local_base(kustomization_dir):
    """Recursively copy all local bases and the base repo to the current directory, rewrite all bases to local paths, and run kustomize build in the cwd."""
    import os
    import shutil
    import sys

    logging.debug(f"Parsing base repo/ref/subdir from {kustomization_dir}")
    try:
        repo_url, ref, subdir = parse_kustomization_for_git_info(kustomization_dir)
        logging.debug(f"Parsed base repo: {repo_url}, ref: {ref}, subdir: {subdir}")
    except Exception as e:
        console.print(f"[red]Failed to parse base repo info: {e}[/red]")
        sys.exit(1)
    if git is None:
        console.print(
            "[red]GitPython is required for this command. Install with: uv add gitpython[/red]"
        )
        sys.exit(1)
    repo_local_base = os.path.abspath("cloned-bigbang-base")
    try:
        # Clone the repo to cwd/cloned-bigbang-base
        if os.path.exists(repo_local_base):
            shutil.rmtree(repo_local_base)
        console.print(
            f"[green]Cloning {repo_url}@{ref} to {repo_local_base}...[/green]"
        )
        repo = git.Repo.clone_from(repo_url, repo_local_base)
        repo.git.checkout(ref)
        if subdir:
            repo_local_base = os.path.join(repo_local_base, subdir)
        # Recursively copy and rewrite all local bases to cwd
        root_src = os.path.abspath(kustomization_dir)
        root_dest = os.path.abspath(os.getcwd())
        base_path_map = {"root_src": root_src, "root_dest": root_dest}
        copy_and_rewrite_kustomization(
            root_src, root_dest, base_path_map, repo_local_base
        )
        # Run kustomize build in cwd
        console.print(f"[green]Running kustomize build in {root_dest}...[/green]")
        result = subprocess.run(
            ["kustomize", "build", root_dest], capture_output=True, text=True
        )
        if result.returncode != 0:
            console.print(f"[red]kustomize build failed:[/red]\n{result.stderr}")
            sys.exit(result.returncode)
        console.print(result.stdout)
    except Exception as e:
        console.print(f"[red]Error: {e}[/red]")
        sys.exit(1)


if __name__ == "__main__":
    cli()
