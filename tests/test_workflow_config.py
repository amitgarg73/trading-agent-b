"""
Validates GitHub Actions workflow YAML configuration for Strategy B.

Catches secret-name mistakes that actionlint cannot. Rules:
  - Supabase env vars must map to secrets.SUPABASE_URL / SUPABASE_KEY (no suffix)
  - No step should reference Strategy C secrets (SUPABASE_URL_C, TENANT_ID_C)
"""
import yaml
from pathlib import Path

WORKFLOWS_DIR = Path(__file__).parent.parent / ".github" / "workflows"


def _load_workflows() -> dict[str, dict]:
    return {
        f.name: yaml.safe_load(f.read_text())
        for f in WORKFLOWS_DIR.glob("*.yml")
    }


def _step_envs(workflow: dict) -> list[tuple[str, str, str]]:
    """Yield (step_name, env_key, env_value) for every step env entry."""
    results = []
    for job in (workflow.get("jobs") or {}).values():
        for step in job.get("steps") or []:
            for key, value in (step.get("env") or {}).items():
                name = step.get("name") or step.get("uses") or "unnamed"
                results.append((name, key, str(value or "")))
    return results


class TestSupabaseSecretNames:

    def test_no_strategy_c_supabase_secret(self):
        """B workflows must not reference Strategy C Supabase secrets, except daily_report.yml
        which intentionally queries all three strategy databases."""
        for wf_name, wf in _load_workflows().items():
            if wf_name == "daily_report.yml":
                continue  # cross-strategy report — intentionally uses all three DBs
            for step_name, key, value in _step_envs(wf):
                assert "secrets.SUPABASE_URL_C" not in value, (
                    f"{wf_name} / '{step_name}': references secrets.SUPABASE_URL_C "
                    f"— Strategy B uses secrets.SUPABASE_URL"
                )
                assert "secrets.SUPABASE_KEY_C" not in value, (
                    f"{wf_name} / '{step_name}': references secrets.SUPABASE_KEY_C "
                    f"— Strategy B uses secrets.SUPABASE_KEY"
                )

    def test_no_tenant_id_c_secret(self):
        """B workflows must not reference TENANT_ID_C (Strategy C only)."""
        for wf_name, wf in _load_workflows().items():
            for step_name, key, value in _step_envs(wf):
                assert "secrets.TENANT_ID_C" not in value, (
                    f"{wf_name} / '{step_name}': references secrets.TENANT_ID_C "
                    f"— this secret belongs to Strategy C"
                )
