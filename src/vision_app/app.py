from __future__ import annotations

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
from .logging_setup import AuditLog, setup_logging
from .paths import AppPaths, get_paths
from .projects import ProjectStore
from .server.manager import LlamaServerManager
from .telemetry.needs_log import NeedsLog
from .telemetry.uploader import upload_in_background
from .tools.context import ToolContext
from .tools.registry import ToolRegistry
from .tools.sandbox import PathSandbox
from .transport.client import ChatClient
from .transport.endpoints import EndpointResolver
from .updater.downloads import activate_pending_model


def build_services(paths: AppPaths | None = None) -> SimpleNamespace:
    paths = paths or get_paths()
    paths.ensure()
    config = AppConfig.load(paths)
    try:
        activate_pending_model(paths, config)
    except Exception:
        pass
    logger = setup_logging(paths)
    credentials = get_credential_store()
    history = HistoryStore(paths.history_db)
    projects = ProjectStore(paths.history_db)
    knowledge = KnowledgeIndex(paths.history_db)
    checkpoints = CheckpointManager(paths.checkpoints_dir)
    needs_log = NeedsLog(paths.needs_log)
    audit = AuditLog(paths.audit_log)
    registry = ToolRegistry(audit)
    skill_manager = SkillManager(paths.skills_dir)
    sandbox = PathSandbox(paths.workspace, extra_read_roots=[paths.skills_dir])

    def checkpoint(path: Path) -> None:
        checkpoints.snapshot(path, tool_context.conversation_id)

    tool_context = ToolContext(
        paths=paths,
        history=history,
        sandbox=sandbox,
        skill_loader=skill_manager.load,
        checkpoint_callback=checkpoint,
    )
    client = ChatClient()
    resolver = EndpointResolver(config, remote_api_key=credentials.get("REMOTE_API_KEY"))
    # Resolve the remote key at selection time so Settings changes take effect without recreating services.
    original_cloud = resolver.cloud_endpoint

    def cloud_with_current_key(model: str = ""):
        resolver.remote_api_key = credentials.get("REMOTE_API_KEY")
        return original_cloud(model)

    resolver.cloud_endpoint = cloud_with_current_key  # type: ignore[method-assign]
    controller = ChatController(
        config=config,
        history=history,
        client=client,
        endpoint_resolver=resolver,
        registry=registry,
        tool_context=tool_context,
        needs_log=needs_log,
    )
    extension_host = ExtensionHost(secret_resolver=credentials.get)
    return SimpleNamespace(
        paths=paths, config=config, logger=logger, credentials=credentials,
        history=history, projects=projects, knowledge=knowledge, checkpoints=checkpoints,
        needs_log=needs_log, audit=audit, registry=registry, skill_manager=skill_manager,
        sandbox=sandbox, tool_context=tool_context, client=client, resolver=resolver,
        controller=controller, server_manager=LlamaServerManager(), extension_host=extension_host,
    )


def _run_qt(argv: list[str]) -> int:
    from PySide6.QtWidgets import QApplication, QMessageBox

    from .constants import PRODUCT_NAME
    from .ui.main_window import MainWindow
    from .ui.theme import stylesheet

    app = QApplication([sys.argv[0], *[arg for arg in argv if arg != "--qt"]])
    app.setApplicationName(PRODUCT_NAME)
    services = build_services()
    app.setStyleSheet(stylesheet(services.config.theme))
    try:
        window = MainWindow(services)
    except Exception as exc:
        QMessageBox.critical(None, "VISION could not start", str(exc))
        return 1
    window.show()
    code = app.exec()
    try:
        services.needs_log.flush()
        if services.config.telemetry_opt_in:
            upload_in_background(services.needs_log, services.credentials.get("HF_TOKEN"))
    except Exception:
        pass
    return int(code)


def main(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    if "--qt" in args:
        return _run_qt(args)

    from .web_bridge import run_web_app

    services = build_services()
    return run_web_app(services, debug="--debug" in args)


if __name__ == "__main__":
    raise SystemExit(main())
