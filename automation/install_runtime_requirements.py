from __future__ import annotations

import argparse
import os
import subprocess
import sys
from pathlib import Path


VERIFY_IMPORT_CODE = (
	"import importlib; "
	"modules = ('selenium', 'openpyxl', 'pypdf'); "
	"[importlib.import_module(name) for name in modules]; "
	"print('Verified imports: ' + ', '.join(modules))"
)


def _repo_root() -> Path:
	return Path(__file__).resolve().parents[1]


def _default_requirements_path() -> Path:
	return _repo_root() / "automation" / "requirements-selenium.txt"


def _default_venv_path() -> Path:
	return _repo_root() / ".venv"


def _venv_python_path(venv_path: Path) -> Path:
	if os.name == "nt":
		return venv_path / "Scripts" / "python.exe"
	return venv_path / "bin" / "python"


def _format_command(command: list[str]) -> str:
	command_parts = [str(part) for part in command]
	if os.name == "nt":
		return subprocess.list2cmdline(command_parts)
	return " ".join(command_parts)


def _run_command(command: list[str], dry_run: bool) -> None:
	formatted_command = _format_command(command)
	print(f"[run] {formatted_command}")
	if dry_run:
		return
	subprocess.run(command, check=True)


def _python_has_pip(python_path: Path, dry_run: bool) -> bool:
	if dry_run:
		return True
	result = subprocess.run(
		[str(python_path), "-m", "pip", "--version"],
		check=False,
		capture_output=True,
		text=True,
	)
	return result.returncode == 0


def _ensure_pip_available(python_path: Path, dry_run: bool) -> None:
	if _python_has_pip(python_path, dry_run=dry_run):
		return
	print(f"pip was not available for {python_path}. Bootstrapping with ensurepip...")
	_run_command([str(python_path), "-m", "ensurepip", "--upgrade"], dry_run=dry_run)


def _ensure_virtual_environment(venv_path: Path, dry_run: bool) -> Path:
	python_path = _venv_python_path(venv_path)
	if python_path.exists():
		print(f"Using existing virtual environment: {venv_path}")
		return python_path

	print(f"Creating virtual environment: {venv_path}")
	_run_command([sys.executable, "-m", "venv", str(venv_path)], dry_run=dry_run)
	return python_path


def _verify_requirements_file(requirements_path: Path) -> None:
	if requirements_path.is_file():
		return
	raise FileNotFoundError(f"Requirements file not found: {requirements_path}")


def _install_python_packages(python_path: Path, requirements_path: Path, dry_run: bool) -> None:
	_ensure_pip_available(python_path, dry_run=dry_run)
	_run_command(
		[str(python_path), "-m", "pip", "install", "--upgrade", "pip", "setuptools", "wheel"],
		dry_run=dry_run,
	)
	_run_command(
		[str(python_path), "-m", "pip", "install", "-r", str(requirements_path)],
		dry_run=dry_run,
	)


def _verify_installed_runtime(python_path: Path, dry_run: bool) -> None:
	if dry_run:
		print("Dry run enabled: skipping import verification.")
		return
	_run_command([str(python_path), "-c", VERIFY_IMPORT_CODE], dry_run=False)


def _find_edge_path() -> Path | None:
	if os.name != "nt":
		return None

	candidate_paths = []
	for env_name in ("PROGRAMFILES", "PROGRAMFILES(X86)", "LOCALAPPDATA"):
		base_path = os.environ.get(env_name)
		if not base_path:
			continue
		candidate_paths.append(Path(base_path) / "Microsoft" / "Edge" / "Application" / "msedge.exe")

	for candidate_path in candidate_paths:
		if candidate_path.exists():
			return candidate_path
	return None


def _print_environment_notes(venv_path: Path, using_current_python: bool) -> None:
	edge_path = _find_edge_path()
	if edge_path is not None:
		print(f"Microsoft Edge detected: {edge_path}")
	else:
		print("Warning: Microsoft Edge was not found in the standard Windows install locations.")
		print("The automation uses Edge, so install Edge before running the Selenium workflow.")

	if using_current_python:
		print("Installed packages into the current Python interpreter.")
		print(f"Current interpreter: {sys.executable}")
		return

	activate_command = f"{venv_path}\\Scripts\\Activate.ps1" if os.name == "nt" else f"source {venv_path}/bin/activate"
	print("Setup complete for the project virtual environment.")
	print(f"Activate it with: {activate_command}")
	print("Then run the workflow with: python function/tools.py")


def _build_argument_parser() -> argparse.ArgumentParser:
	parser = argparse.ArgumentParser(
		description="Create or reuse the project virtual environment and install the Python packages required by the dashboard automation.",
	)
	parser.add_argument(
		"--requirements",
		default=str(_default_requirements_path()),
		help="Path to the pip requirements file. Defaults to automation/requirements-selenium.txt.",
	)
	parser.add_argument(
		"--venv",
		default=str(_default_venv_path()),
		help="Path to the virtual environment to create or reuse. Defaults to .venv at the repo root.",
	)
	parser.add_argument(
		"--current-python",
		action="store_true",
		help="Install packages into the interpreter running this script instead of creating or reusing the repo .venv.",
	)
	parser.add_argument(
		"--dry-run",
		action="store_true",
		help="Print the commands that would run without changing the environment.",
	)
	return parser


def main() -> int:
	parser = _build_argument_parser()
	args = parser.parse_args()

	requirements_path = Path(args.requirements).expanduser().resolve()
	venv_path = Path(args.venv).expanduser().resolve()
	_verify_requirements_file(requirements_path)

	if args.current_python:
		python_path = Path(sys.executable).resolve()
		print(f"Using current interpreter: {python_path}")
	else:
		python_path = _ensure_virtual_environment(venv_path, dry_run=args.dry_run)

	_install_python_packages(python_path, requirements_path, dry_run=args.dry_run)
	_verify_installed_runtime(python_path, dry_run=args.dry_run)
	_print_environment_notes(venv_path, using_current_python=args.current_python)
	return 0


if __name__ == "__main__":
	raise SystemExit(main())