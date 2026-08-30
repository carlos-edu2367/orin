from agentos.code_mode.models import CodeAutonomy, CodeModeSettings, CodeWorkKind, detect_code_request, explicitly_authorizes_git_push
from agentos.code_mode.prompt import code_mode_instructions


def test_detection_is_conservative_but_recognizes_common_engineering_requests():
    assert detect_code_request("escreva uma carta") is None
    assert detect_code_request("corrija o bug no frontend") is CodeWorkKind.BUGFIX
    assert detect_code_request("investigue por que o teste falha") is CodeWorkKind.INVESTIGATION
    assert detect_code_request("faça uma refatoração do endpoint") is CodeWorkKind.REFACTOR


def test_autonomy_keeps_production_deploy_behind_confirmation():
    prompt = code_mode_instructions(work_kind="implementation", autonomy=CodeAutonomy.FULL_AUTONOMY.value)
    assert "produção" in prompt
    assert "sempre exige" in prompt


def test_settings_default_to_safe_approval_and_opt_in_notifications():
    settings = CodeModeSettings()
    assert settings.autonomy is CodeAutonomy.APPROVAL_REQUIRED
    assert settings.requires_plan_approval is True
    assert settings.system_notifications is False


def test_explicit_push_authorization_is_narrow_and_activates_code_mode():
    assert detect_code_request("pode dar o git push agora") is CodeWorkKind.IMPLEMENTATION
    assert explicitly_authorizes_git_push("pode dar o git push agora") is True
    assert explicitly_authorizes_git_push("não faça git push") is False
    assert explicitly_authorizes_git_push("por que o git push falhou?") is False
