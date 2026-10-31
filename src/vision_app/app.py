from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path
from types import SimpleNamespace

from .chat.controller import ChatController
from .code_workspace.checkpoints import CheckpointManager
from .config import AppConfig
from .credentials import get_credential_store
from .extensions.host import ExtensionHost
from .extensions.skills import SkillManager
from .history import HistoryStore
from .knowledge.index import KnowledgeIndex
from .library import LibraryStore
from .logging_setup import AuditLog, setup_logging
from .paths import AppPaths, get_paths
from .projects import ProjectStore
from .python_helper import RUN_PYTHON_HELPER_ARG, run_python_script
from .server.manager import LlamaServerManager
from .single_instance import single_instance_lock_for
from .sync_service import SyncService
from .telemetry.needs_log import NeedsLog
from .tools.context import ToolContext
from .tools.registry import ToolRegistry
from .tools.sandbox import PathSandbox
from .transport.client import ChatClient
from .transport.endpoints import EndpointResolver
from .updater.downloads import activate_pending_model
from .updates.service import LAUNCHER_VERSION, UpdateService


def _update_root(paths: AppPaths) -> Path:
    override = os.environ.get("VISION_UPDATE_ROOT") or os.environ.get("VISION_LAUNCHER_ROOT")
    if override:
        return Path(override).expanduser()
    install_root = paths.installation_root
    if install_root is not None and (
        (install_root / "current.json").is_file() or (install_root / "versions").is_dir()
    ):
        return install_root
    # Source and legacy non-launcher installs stage update data under app
    # data.  A versioned launcher install returns its outer root above.
    return paths.updates_dir


def build_services(
    paths: AppPaths | None = None,
    *,
    dev_channel: bool = False,
    offline: bool = False,
) -> SimpleNamespace:
    paths = paths or get_paths()
    paths.ensure()
    logger = setup_logging(paths)
    config = AppConfig.load(paths)
    try:
        activate_pending_model(paths, config)
    except Exception:
        logger.exception("Pending model activation failed; keeping the current model configuration")
    credentials = get_credential_store()
    history = HistoryStore(paths.history_db)
    projects = ProjectStore(paths.history_db)
    knowledge = KnowledgeIndex(paths.history_db)
    library = LibraryStore(paths.history_db, paths.library_dir)
    checkpoints = CheckpointManager(paths.checkpoints_dir)
    needs_log = NeedsLog(
        paths.needs_log,
        model_sha_provider=lambda: config.installed_model_sha,
    )
    audit = AuditLog(paths.audit_log)
    registry = ToolRegistry(audit)
    update_service = UpdateService(
        root=_update_root(paths),
        paths=paths,
        config=config,
        dev_channel=dev_channel or config.update_channel == "dev",
        offline=offline,
        manifest_url=os.environ.get("VISION_UPDATE_MANIFEST_URL") or None,
        config_url=os.environ.get("VISION_UPDATE_CONFIG_URL") or None,
        model_api_url=os.environ.get("VISION_UPDATE_MODEL_API_URL") or None,
        token_provider=lambda: credentials.get("HF_TOKEN"),
        on_config_applied=lambda remote: registry.apply_schema_overrides(remote.tool_schemas),
        launcher_version=os.environ.get("VISION_LAUNCHER_VERSION") or LAUNCHER_VERSION,
    )
    update_service.apply_active_config()
    skill_manager = SkillManager(paths.skills_dir)
    sandbox = PathSandbox(paths.workspace, extra_read_roots=[paths.skills_dir])

    def checkpoint(path: Path) -> None:
        checkpoints.snapshot(path, tool_context.conversation_id)

    tool_context = ToolContext(
        paths=paths,
        history=history,
        sandbox=sandbox,
        skill_loader=skill_manager.load,
        skill_resource_reader=skill_manager.read_resource,
        checkpoint_callback=checkpoint,
    )
    client = ChatClient()
    resolver = EndpointResolver(
        config,
        remote_api_key_provider=lambda: credentials.get("REMOTE_API_KEY"),
    )
    controller = ChatController(
        config=config,
        history=history,
        client=client,
        endpoint_resolver=resolver,
        registry=registry,
        tool_context=tool_context,
        needs_log=needs_log,
        system_prompt_provider=lambda: update_service.system_prompt,
    )
    def _mcp_sampler(messages: list[dict[str, str]], max_tokens: int) -> str:
        # Sampling always targets the managed local model only — never a
        # remote/cloud endpoint — and only runs after per-server opt-in plus
        # a per-request user confirmation in the MCP client.
        import httpx as _httpx

        response = _httpx.post(
            f"http://127.0.0.1:{config.local_port}/v1/chat/completions",
            json={"messages": messages, "stream": False, "max_tokens": max_tokens},
            timeout=180.0,
        )
        response.raise_for_status()
        payload = response.json()
        choices = payload.get("choices") or []
        if not choices:
            return ""
        message = choices[0].get("message") or {}
        return str(message.get("content") or "")

    extension_host = ExtensionHost(
        secret_resolver=credentials.get,
        sampler=_mcp_sampler,
        roots_provider=lambda: [(paths.workspace.as_uri(), "VISION workspace")],
        default_cwd=paths.workspace,
    )
    sync_service = SyncService(
        config=config,
        needs_log=needs_log,
        update_service=update_service,
        # The only token source is the platform credential store (Windows
        # Credential Manager in production).  No token is bundled or read
        # from a plaintext config/environment fallback here.
        token_provider=lambda: credentials.get("HF_TOKEN"),
    )
    return SimpleNamespace(
        paths=paths, config=config, logger=logger, credentials=credentials,
        history=history, projects=projects, knowledge=knowledge, library=library, checkpoints=checkpoints,
        needs_log=needs_log, audit=audit, registry=registry, skill_manager=skill_manager,
        sandbox=sandbox, tool_context=tool_context, client=client, resolver=resolver,
        controller=controller, server_manager=LlamaServerManager(), extension_host=extension_host,
        update_service=update_service, sync_service=sync_service,
    )


def _run_qt(
    argv: list[str],
    *,
    dev_channel: bool = False,
    offline: bool = False,
    paths: AppPaths | None = None,
) -> int:
    from PySide6.QtWidgets import QApplication, QMessageBox

    from .constants import PRODUCT_NAME
    from .ui.main_window import MainWindow
    from .ui.theme import stylesheet

    app = QApplication([sys.argv[0], *[arg for arg in argv if arg != "--qt"]])
    app.setApplicationName(PRODUCT_NAME)
    services = build_services(paths, dev_channel=dev_channel, offline=offline)
    app.setStyleSheet(stylesheet(services.config.theme))
    try:
        window = MainWindow(services)
    except Exception as exc:
        QMessageBox.critical(None, "VISION could not start", str(exc))
        return 1
    window.show()
    services.sync_service.start()
    code = app.exec()
    try:
        services.sync_service.stop()
    except Exception:
        services.logger.exception("Could not flush or upload the feedback queue during shutdown")
    return int(code)


def main(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    if RUN_PYTHON_HELPER_ARG in args:
        index = args.index(RUN_PYTHON_HELPER_ARG)
        if index != 0 or len(args) != 2:
            print(f"Usage: {Path(sys.argv[0]).name} {RUN_PYTHON_HELPER_ARG} <script.py>", file=sys.stderr)
            return 2
        return run_python_script(Path(args[1]))
    parser = argparse.ArgumentParser(prog="VISION", add_help=True)
    parser.add_argument("--qt", action="store_true", help="Use the legacy Qt interface")
    parser.add_argument("--debug", action="store_true", help="Enable WebView debug features")
    parser.add_argument("--dev-channel", action="store_true", help="Use the development update channel")
    parser.add_argument("--offline", action="store_true", help="Disable update network checks for this launch")
    parser.add_argument(
        "--selftest",
        action="store_true",
        help="Run the post-update self-check (writes result.json) and exit 0/1 without opening the UI",
    )
    options, _unknown = parser.parse_known_args(args)
    if options.selftest:
        from .selftest import run_selftest

        return run_selftest()

    paths = get_paths()
    paths.ensure()
    instance_lock = single_instance_lock_for(paths)
    if not instance_lock.acquire():
        print("VISION is already running for this data folder.", file=sys.stderr)
        return 0
    try:
        if options.qt:
            return _run_qt(
                args,
                dev_channel=options.dev_channel,
                offline=options.offline,
                paths=paths,
            )

        from .web_bridge import run_web_app

        services = build_services(paths, dev_channel=options.dev_channel, offline=options.offline)
        return run_web_app(services, debug=options.debug)
    finally:
        instance_lock.release()


if __name__ == "__main__":
    raise SystemExit(main())
