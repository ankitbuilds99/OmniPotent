"""Deployment artefacts must match the application they deploy.

Everything under `deploy/`, plus `Dockerfile`, `docker-compose.yml` and the
release workflows, is only useful if it agrees with the code. These tests check
the agreements that a YAML linter cannot:

* every Helm `include` resolves to a `define` in the same chart,
* every environment-variable key the manifests set is a real `Settings` field,
* every probe / scrape path is a real FastAPI route (and the scrape path really
  speaks the Prometheus text format),
* every file a manifest points at exists on disk.

They are static + in-process only: no cluster, no Docker, no network.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest
import yaml

from app.config import Settings

ROOT = Path(__file__).resolve().parents[1]
CHART = ROOT / "deploy" / "helm" / "devops-copilot"
APPLICATIONSET = ROOT / "deploy" / "argocd" / "applicationset.yaml"
K8S = ROOT / "deploy" / "kubernetes"
MIGRATION_JOB = K8S / "base" / "migration-job.yaml"
MIGRATION_ROLLBACK_JOB = K8S / "base" / "migration-rollback-job.yaml"
DOCKERFILE = ROOT / "Dockerfile"
COMPOSE = ROOT / "docker-compose.yml"
WORKFLOWS = ROOT / ".github" / "workflows"

# Env vars that are legitimately not pydantic Settings fields: they are consumed
# by the interpreter, by another container in the pod, or interpolated into a
# neighbouring value by Kubernetes' $(VAR) expansion.
NON_SETTINGS_ENV = {
    "PYTHONUNBUFFERED",
    "PYTHONPATH",
    "PORT",
    "REDIS_PASSWORD",
    "POSTGRES_PASSWORD",
    "POSTGRES_USER",
    "POSTGRES_DB",
    "DB_HOST",
    "DB_PORT",
    "DB_USER",
    "GF_SECURITY_ADMIN_PASSWORD",
    "GF_USERS_ALLOW_SIGN_UP",
    "COLLECTOR_OTLP_ENABLED",
    # OpenTelemetry SDK reads these directly from the environment.
    "OTEL_SERVICE_NAME",
    "OTEL_EXPORTER_OTLP_PROTOCOL",
    "OTEL_EXPORTER_OTLP_ENDPOINT",
    # Datadog / Sentry SDKs likewise.
    "DD_TRACE_ENABLED",
    "DD_LLMOBS_ENABLED",
}


def _settings_env_names() -> set[str]:
    return {name.upper() for name in Settings.model_fields}


def _assert_known_env(keys: set[str], where: str) -> None:
    known = _settings_env_names() | NON_SETTINGS_ENV
    unknown = sorted(k for k in keys if k not in known)
    assert not unknown, (
        f"{where} sets env vars that nothing in app/ reads "
        f"(not a Settings field, not an allow-listed SDK var): {unknown}"
    )


# --------------------------------------------------------------------------- #
# app routes (imported once; the API module is expensive to import)
# --------------------------------------------------------------------------- #


@pytest.fixture(scope="module")
def api_routes() -> set[str]:
    from app.api import main as api

    return {getattr(r, "path", "") for r in api.app.routes}


# --------------------------------------------------------------------------- #
# Helm chart deploy/helm/devops-copilot — the chart ArgoCD points at
# --------------------------------------------------------------------------- #


def _chart_template_files() -> list[Path]:
    return sorted((CHART / "templates").glob("*"))


def test_helm_every_include_resolves_to_a_define():
    """`helm template` aborts on an include with no matching define."""
    defines: set[str] = set()
    for path in _chart_template_files():
        defines |= set(re.findall(r'define\s+"([^"]+)"', path.read_text()))

    missing: dict[str, list[str]] = {}
    for path in _chart_template_files():
        for name in re.findall(r'include\s+"([^"]+)"', path.read_text()):
            if name not in defines:
                missing.setdefault(name, []).append(path.name)

    assert not missing, (
        "Helm templates include names that no template defines "
        f"(chart would fail to render): {missing}. Defined: {sorted(defines)}"
    )


def _yaml_keys_ignoring_templating(text: str, indent: int) -> set[str]:
    """SCREAMING_SNAKE keys at a fixed indent, skipping `{{ ... }}` lines."""
    pat = re.compile(r"^ {%d}([A-Z][A-Z0-9_]*):" % indent)
    return {m.group(1) for line in text.splitlines() if (m := pat.match(line))}


def test_helm_configmap_keys_are_real_settings():
    keys = _yaml_keys_ignoring_templating((CHART / "templates" / "configmap.yaml").read_text(), 2)
    assert keys, "configmap template has no data keys"
    _assert_known_env(keys, "deploy/helm/devops-copilot/templates/configmap.yaml")


def test_helm_deployment_env_keys_are_real_settings():
    text = (CHART / "templates" / "deployment.yaml").read_text()
    keys = set(re.findall(r"^\s*- name: ([A-Z][A-Z0-9_]*)$", text, re.MULTILINE))
    assert keys, "deployment template declares no explicit env vars"
    _assert_known_env(keys, "deploy/helm/devops-copilot/templates/deployment.yaml")


def test_helm_secret_keys_are_real_settings():
    keys = _yaml_keys_ignoring_templating((CHART / "templates" / "secret.yaml").read_text(), 2)
    _assert_known_env(keys, "deploy/helm/devops-copilot/templates/secret.yaml")
    # Settings refuses to construct in production without these two.
    assert "COPILOT_API_TOKEN" in keys, (
        "COPILOT_API_TOKEN is mandatory when COPILOT_ENV=production "
        "(Settings._fail_closed_in_production) but the chart Secret cannot supply it"
    )
    assert "COPILOT_SECRET_KEY" in keys, (
        "COPILOT_SECRET_KEY is mandatory when COPILOT_MULTI_TENANT=true in production"
    )


def test_helm_probe_paths_are_real_routes(api_routes):
    values = yaml.safe_load((CHART / "values.yaml").read_text())
    for probe in ("livenessProbe", "readinessProbe"):
        path = values[probe]["httpGet"]["path"]
        assert path in api_routes, f"{probe} points at {path!r}, which is not an API route"
    # A readiness probe that cannot fail is not a readiness probe: /healthz is the
    # liveness endpoint and always returns 200.
    assert values["readinessProbe"]["httpGet"]["path"] == "/readyz"


def test_helm_prometheus_scrape_path_speaks_prometheus(api_routes):
    values = yaml.safe_load((CHART / "values.yaml").read_text())
    path = values["podAnnotations"]["prometheus.io/path"]
    assert path in api_routes, f"prometheus.io/path={path!r} is not an API route"

    from fastapi.testclient import TestClient

    from app.api import main as api

    with TestClient(api.app) as client:
        resp = client.get(path)
    assert resp.status_code == 200, f"GET {path} -> {resp.status_code}"
    ctype = resp.headers.get("content-type", "")
    assert ctype.startswith("text/plain"), (
        f"prometheus.io/path={path!r} returns {ctype!r}; Prometheus needs the text "
        "exposition format (see /metrics/slo)"
    )


def test_helm_image_repository_is_the_published_image():
    """The chart must pull the image CI actually publishes."""
    values = yaml.safe_load((CHART / "values.yaml").read_text())
    kustomization = yaml.safe_load((K8S / "kustomization.yaml").read_text())
    published = {img["name"] for img in kustomization["images"]}
    assert values["image"]["repository"] in published, (
        f"chart image.repository={values['image']['repository']!r} is not one of the "
        f"images the pipeline publishes: {sorted(published)}"
    )


# --------------------------------------------------------------------------- #
# ArgoCD ApplicationSet
# --------------------------------------------------------------------------- #


def _applicationset() -> dict:
    docs = [d for d in yaml.safe_load_all(APPLICATIONSET.read_text()) if d]
    appsets = [d for d in docs if d.get("kind") == "ApplicationSet"]
    assert len(appsets) == 1, f"expected one ApplicationSet, got {[d.get('kind') for d in docs]}"
    return appsets[0]


def _generator_elements(appset: dict) -> tuple[list[dict], list[dict]]:
    matrix = appset["spec"]["generators"][0]["matrix"]["generators"]
    return matrix[0]["list"]["elements"], matrix[1]["list"]["elements"]


def test_applicationset_value_files_exist():
    appset = _applicationset()
    envs, components = _generator_elements(appset)
    value_files = appset["spec"]["template"]["spec"]["source"]["helm"]["valueFiles"]

    missing = []
    for component in components:
        chart = ROOT / component["path"]
        candidates = list(value_files)
        if component.get("valuesFile"):
            candidates.append(component["valuesFile"])
        for candidate in candidates:
            for env in envs:
                resolved = candidate.replace("{{env}}", env["env"])
                if not (chart / resolved).is_file():
                    missing.append(f"{component['path']}/{resolved}")

    assert not missing, (
        "ApplicationSet references Helm value files that do not exist; ArgoCD fails "
        f"the whole Application on a missing values file: {sorted(set(missing))}"
    )


def test_applicationset_has_no_unsubstituted_templating():
    """ApplicationSet uses fasttemplate `{{key}}`; handlebars/go blocks stay literal."""
    appset = _applicationset()
    envs, components = _generator_elements(appset)
    available = set()
    for element in envs + components:
        available |= set(element)

    rendered = yaml.safe_dump(appset["spec"]["template"])
    assert "{{#" not in rendered and "{{/" not in rendered, (
        "handlebars block helpers are not supported by an ApplicationSet without "
        "goTemplate: true — the literal string ends up in the rendered Application"
    )
    used = set(re.findall(r"\{\{([a-zA-Z_][a-zA-Z0-9_]*)\}\}", rendered))
    unknown = sorted(used - available)
    assert not unknown, (
        f"ApplicationSet template uses {unknown}, which no generator element provides; "
        "fasttemplate leaves unknown placeholders in the output verbatim"
    )


# --------------------------------------------------------------------------- #
# Kubernetes migration Job
# --------------------------------------------------------------------------- #


def _migration_docs() -> list[dict]:
    return [d for d in yaml.safe_load_all(MIGRATION_JOB.read_text()) if d]


def _job_docs() -> list[dict]:
    """Both migration Jobs: the PreSync one and the break-glass rollback."""
    docs = list(_migration_docs())
    docs += [d for d in yaml.safe_load_all(MIGRATION_ROLLBACK_JOB.read_text()) if d]
    return docs


def _base_cluster_objects() -> list[dict]:
    """Every object the base kustomization actually creates."""
    kustomization = yaml.safe_load((K8S / "kustomization.yaml").read_text())
    objects: list[dict] = []
    for resource in kustomization["resources"]:
        objects += [d for d in yaml.safe_load_all((K8S / resource).read_text()) if d]
    return objects


def test_dockerfile_ships_alembic_and_migrations():
    """The migration Job runs `alembic upgrade head` inside the app image."""
    text = DOCKERFILE.read_text()
    copies = re.findall(r"^COPY\s+(.*)$", text, re.MULTILINE)
    copied = " ".join(copies)
    assert "alembic.ini" in copied, "Dockerfile never COPYs alembic.ini"
    assert "migrations" in copied, "Dockerfile never COPYs migrations/"

    install = re.search(r"pip install[^\n]*-e\s+\"?\.\[([^\]]+)\]", text)
    assert install, "could not find the editable pip install in the Dockerfile"
    extras = {e.strip() for e in install.group(1).split(",")}
    assert "migrations" in extras, (
        "alembic only ships in the 'migrations' extra (pyproject.toml), so "
        f"`alembic upgrade head` is not installed in the image; extras={sorted(extras)}"
    )


def test_migration_job_uses_the_published_image():
    kustomization = yaml.safe_load((K8S / "kustomization.yaml").read_text())
    published = {img["name"] for img in kustomization["images"]}
    for doc in _job_docs():
        for container in doc["spec"]["template"]["spec"]["containers"]:
            repo = container["image"].rsplit(":", 1)[0]
            if repo.startswith("postgres"):
                continue
            assert repo in published, (
                f"{doc['metadata']['name']} uses image {container['image']!r}, which "
                f"kustomize never rewrites (images: {sorted(published)})"
            )


def test_migration_job_runs_as_the_image_user():
    """runAsUser must match the UID the Dockerfile creates and chowns /app to."""
    uid = re.search(r"useradd[^\n]*--uid\s+(\d+)", DOCKERFILE.read_text())
    assert uid, "Dockerfile no longer creates a fixed-UID user"
    expected = int(uid.group(1))
    for doc in _job_docs():
        ctx = doc["spec"]["template"]["spec"]["securityContext"]
        assert ctx["runAsUser"] == expected, (
            f"{doc['metadata']['name']} runs as UID {ctx['runAsUser']} but the image "
            f"is built for UID {expected}"
        )
        assert ctx["fsGroup"] == expected


def _pod_specs(doc: dict) -> list[dict]:
    kind = doc.get("kind")
    if kind in ("Deployment", "Job", "StatefulSet", "DaemonSet"):
        return [doc["spec"]["template"]["spec"]]
    if kind == "CronJob":
        return [doc["spec"]["jobTemplate"]["spec"]["template"]["spec"]]
    return []


def test_base_pods_only_reference_objects_the_kustomization_creates():
    """A Secret/ConfigMap/ServiceAccount name that nothing creates is a dead pod.

    Kubernetes does not reject the manifest: the Job is admitted and then sits in
    CreateContainerConfigError (missing Secret key) or is refused by the
    ServiceAccount admission controller, which is only visible in `kubectl
    describe`. The migration Job pulled DB credentials from a Secret called
    `omnipotent-db-credentials` and ran as ServiceAccount `omnipotent-api`;
    neither has ever existed in this kustomization.
    """
    objects = _base_cluster_objects()
    created: dict[str, set[str]] = {}
    for obj in objects:
        created.setdefault(obj["kind"], set()).add(obj["metadata"]["name"])
    secret_keys = {
        obj["metadata"]["name"]: set(obj.get("stringData", {})) | set(obj.get("data", {}))
        for obj in objects
        if obj["kind"] == "Secret"
    }
    configmap_keys = {
        obj["metadata"]["name"]: set(obj.get("data", {}))
        for obj in objects
        if obj["kind"] == "ConfigMap"
    }
    # `omnipotent-secrets` is intentionally NOT in the kustomization: it is
    # provisioned out of band (external-secret.yaml via ESO, or a filled-in copy
    # of secret.yaml.example applied manually). The example file documents the
    # contract, so its keys count as created.
    for obj in yaml.safe_load_all((K8S / "secret.yaml.example").read_text()):
        if obj and obj.get("kind") == "Secret":
            secret_keys[obj["metadata"]["name"]] = set(obj.get("stringData", {})) | set(
                obj.get("data", {})
            )

    problems: list[str] = []
    for obj in objects:
        where = f"{obj['kind']}/{obj['metadata']['name']}"
        for pod in _pod_specs(obj):
            sa = pod.get("serviceAccountName")
            if sa and sa not in created.get("ServiceAccount", set()):
                problems.append(f"{where}: serviceAccountName {sa!r} is never created")
            for container in pod.get("initContainers", []) + pod.get("containers", []):
                for source in container.get("envFrom", []):
                    for field, kind, names in (
                        ("secretRef", "Secret", secret_keys),
                        ("configMapRef", "ConfigMap", configmap_keys),
                    ):
                        ref = source.get(field)
                        if ref and ref["name"] not in names:
                            problems.append(f"{where}: envFrom {kind} {ref['name']!r} missing")
                for env in container.get("env", []):
                    value_from = env.get("valueFrom") or {}
                    for field, kind, names in (
                        ("secretKeyRef", "Secret", secret_keys),
                        ("configMapKeyRef", "ConfigMap", configmap_keys),
                    ):
                        ref = value_from.get(field)
                        if not ref:
                            continue
                        if ref["name"] not in names:
                            problems.append(
                                f"{where}: {env['name']} reads {kind} {ref['name']!r}, "
                                "which no manifest creates"
                            )
                        elif ref["key"] not in names[ref["name"]]:
                            problems.append(
                                f"{where}: {env['name']} reads key {ref['key']!r} of "
                                f"{kind} {ref['name']!r}, which that {kind} does not set"
                            )

    assert not problems, "dangling references in deploy/kubernetes: " + "; ".join(problems)


def test_migration_job_is_reachable_from_kustomize():
    kustomization = yaml.safe_load((K8S / "kustomization.yaml").read_text())
    resources = kustomization["resources"]
    for resource in resources:
        if (K8S / resource).resolve() == MIGRATION_JOB.resolve():
            break
    else:
        pytest.fail(
            "deploy/kubernetes/base/migration-job.yaml is not in the kustomization "
            f"resources ({resources}); nothing ever applies it"
        )


def test_kustomize_resources_all_exist():
    for path in (K8S, K8S / "overlays" / "staging", K8S / "overlays" / "production"):
        kustomization = yaml.safe_load((path / "kustomization.yaml").read_text())
        for resource in kustomization.get("resources", []):
            assert (path / resource).exists(), f"{path}/kustomization.yaml -> missing {resource}"


# --------------------------------------------------------------------------- #
# docker-compose local stack
# --------------------------------------------------------------------------- #


def _compose() -> dict:
    return yaml.safe_load(COMPOSE.read_text())


def test_compose_healthcheck_hits_the_api_not_the_spa(api_routes):
    """`/health` is swallowed by the SPA catch-all, so the check passes even when
    every API route is broken. The probe must name a real API route."""
    test = _compose()["services"]["copilot"]["healthcheck"]["test"]
    url = next(part for part in test if part.startswith("http"))
    path = "/" + url.split("/", 3)[3]
    assert path in api_routes, (
        f"compose healthcheck curls {path!r}, which is not an API route — the SPA "
        "catch-all answers it with index.html, so the check can never fail"
    )


def test_compose_env_keys_are_real_settings():
    for name, service in _compose()["services"].items():
        env = service.get("environment") or {}
        keys = set(env) if isinstance(env, dict) else {e.split("=", 1)[0] for e in env}
        _assert_known_env(keys, f"docker-compose.yml service {name!r}")


def test_compose_bind_mount_sources_exist():
    missing = []
    for name, service in _compose()["services"].items():
        for volume in service.get("volumes", []):
            if not isinstance(volume, str) or not volume.startswith("."):
                continue
            source = volume.split(":", 1)[0]
            if not (ROOT / source).exists():
                missing.append(f"{name}: {source}")
    assert not missing, (
        "docker-compose bind-mounts host paths that do not exist; Docker silently "
        f"creates a directory where a file was meant to be: {missing}"
    )


def test_prometheus_config_scrapes_the_exposition_endpoint(api_routes):
    """The monitoring profile is only useful if Prometheus can parse what it scrapes.

    /metrics returns JSON for the dashboard; only /metrics/slo speaks the text
    exposition format.
    """
    cfg = yaml.safe_load((ROOT / "deploy" / "prometheus" / "prometheus.yml").read_text())
    jobs = {job["job_name"]: job for job in cfg["scrape_configs"]}

    api_job = jobs["omnipotent"]
    path = api_job.get("metrics_path", "/metrics")
    assert path in api_routes, f"prometheus scrapes {path!r}, which is not an API route"
    assert path == "/metrics/slo", (
        f"prometheus scrapes {path!r}; only /metrics/slo returns Prometheus text "
        "exposition format, /metrics returns dashboard JSON"
    )

    # The target must be the compose service name, resolvable on the shared network.
    services = _compose()["services"]
    targets = [t for sc in api_job["static_configs"] for t in sc["targets"]]
    assert any(t.split(":")[0] in services for t in targets), (
        f"prometheus targets {targets} name no docker-compose service ({sorted(services)})"
    )


def test_prometheus_rule_files_match_the_compose_mount():
    """rule_files must name the in-container path compose mounts alerts.yaml at."""
    cfg = yaml.safe_load((ROOT / "deploy" / "prometheus" / "prometheus.yml").read_text())
    mounted = {
        volume.split(":")[1]
        for volume in _compose()["services"]["prometheus"]["volumes"]
        if isinstance(volume, str) and volume.startswith(".")
    }
    for rule_file in cfg.get("rule_files", []):
        assert rule_file in mounted, (
            f"rule_files entry {rule_file!r} is not mounted into the prometheus "
            f"container; compose mounts {sorted(mounted)}"
        )


# --------------------------------------------------------------------------- #
# Workflows
# --------------------------------------------------------------------------- #


def _workflow(name: str) -> dict:
    return yaml.safe_load((WORKFLOWS / name).read_text())


def test_migration_workflow_path_filters_exist():
    workflow = _workflow("database-migrations.yml")
    # `on` is parsed as the boolean True by YAML 1.1.
    triggers = workflow.get("on") or workflow[True]
    missing = []
    for trigger in ("push", "pull_request"):
        for pattern in triggers[trigger]["paths"]:
            prefix = pattern.split("*", 1)[0].rstrip("/")
            if not (ROOT / prefix).exists():
                missing.append(f"{trigger}: {pattern}")
    assert not missing, (
        "database-migrations.yml filters on paths that do not exist, so the job "
        f"never runs for those changes: {missing}"
    )


def test_migration_workflow_does_not_reference_a_nonexistent_versions_dir():
    lines = (WORKFLOWS / "database-migrations.yml").read_text().splitlines()
    # Comments are allowed to describe the bug; only live commands matter.
    text = "\n".join(line for line in lines if not line.lstrip().startswith("#"))
    assert not (ROOT / "alembic").exists(), "repo layout changed; update this test"
    assert "alembic/versions" not in text, (
        "the drift-detection and PR-comment steps glob alembic/versions/, but "
        "revisions live in migrations/versions/ — both steps silently no-op"
    )


def test_deploy_workflow_smoke_tests_hit_the_api(api_routes):
    text = (WORKFLOWS / "deploy.yml").read_text()
    # The API host comes from vars.STAGING_API_URL / vars.PROD_API_URL, so the
    # smoke tests curl "$API_URL/<path>"; literal https:// hosts still count.
    paths = re.findall(r"""curl -sf (?:https://[^/\s"]+|"\$API_URL)(/[^\s;"')]*)""", text)
    assert paths, "deploy.yml has no smoke-test curl"
    for path in paths:
        assert path in api_routes, (
            f"deploy.yml smoke-tests {path!r}, which the SPA catch-all answers with "
            "index.html — the deploy is declared healthy even if the API is down"
        )


def test_dev_extra_covers_what_the_test_suite_imports():
    """ci.yml runs `uv sync --extra dev` and then the whole suite."""
    pyproject = ROOT / "pyproject.toml"
    text = pyproject.read_text()
    dev = re.search(r"^dev = \[(.*?)^\]", text, re.MULTILINE | re.DOTALL)
    assert dev, "could not find the dev extra in pyproject.toml"
    names = {re.split(r"[><=\[]", n.strip().strip('",'))[0] for n in dev.group(1).split("\n")}
    for required in ("alembic", "aiosqlite"):
        assert required in names, (
            f"tests/test_alembic_migrations.py imports {required} at module scope, but "
            f"the 'dev' extra CI installs does not include it: {sorted(n for n in names if n)}"
        )
