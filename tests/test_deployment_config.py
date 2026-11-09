"""Deployment topology and local-service exposure regressions."""

import copy
import json
import subprocess
import tomllib
import unittest
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parent.parent


def render_compose():
    result = subprocess.run(
        [
            "docker",
            "compose",
            "--profile",
            "worker",
            "config",
            "--format",
            "json",
        ],
        cwd=PROJECT_ROOT,
        check=True,
        capture_output=True,
        text=True,
    )
    return json.loads(result.stdout)


def validate_local_service_bindings(config):
    for service_name, expected_port in (("postgres", 5432), ("redis", 6379)):
        service = config["services"][service_name]
        if service.get("network_mode") == "host":
            raise ValueError(f"{service_name} must not use host networking")
        ports = service.get("ports", [])
        if len(ports) != 1:
            raise ValueError(f"{service_name} must publish one loopback port")
        port = ports[0]
        if port.get("host_ip") != "127.0.0.1":
            raise ValueError(f"{service_name} must bind only to 127.0.0.1")
        if int(port.get("published", 0)) != expected_port:
            raise ValueError(f"{service_name} host port drifted")
        if int(port.get("target", 0)) != expected_port:
            raise ValueError(f"{service_name} container port drifted")


class LocalServiceBindingTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.config = render_compose()

    def test_postgresql_and_redis_publish_only_on_loopback(self):
        validate_local_service_bindings(self.config)

    def test_app_and_worker_keep_internal_service_dns(self):
        services = self.config["services"]
        for service_name in ("roster-scout", "worker"):
            environment = services[service_name]["environment"]
            with self.subTest(service=service_name, dependency="postgres"):
                self.assertIn("@postgres:5432/", environment["DATABASE_URL"])
            with self.subTest(service=service_name, dependency="redis"):
                self.assertIn("redis:6379/", environment["CELERY_BROKER_URL"])

    def test_validator_rejects_wildcard_bare_and_host_network_exposure(self):
        unsafe_variants = []
        wildcard = copy.deepcopy(self.config)
        wildcard["services"]["postgres"]["ports"][0]["host_ip"] = "0.0.0.0"
        unsafe_variants.append(("wildcard", wildcard))

        bare = copy.deepcopy(self.config)
        bare["services"]["redis"]["ports"][0].pop("host_ip", None)
        unsafe_variants.append(("bare", bare))

        host_network = copy.deepcopy(self.config)
        host_network["services"]["postgres"]["network_mode"] = "host"
        unsafe_variants.append(("host-network", host_network))

        for name, config in unsafe_variants:
            with self.subTest(name=name), self.assertRaises(ValueError):
                validate_local_service_bindings(config)

    def test_example_environment_marks_local_credentials_as_nonproduction(self):
        contents = (PROJECT_ROOT / ".env.example").read_text(encoding="utf-8")
        self.assertIn("loopback development-only", contents)
        self.assertIn("authenticated, TLS-protected DATABASE_URL", contents)
        self.assertNotIn("postgresql+psycopg://roster_scout:roster_scout@", contents)
        self.assertNotIn("redis://redis:6379", contents)


class BrowserlessCapacityConfigTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.compose = render_compose()
        with (PROJECT_ROOT / "fly.toml").open("rb") as handle:
            cls.fly = tomllib.load(handle)

    def test_app_and_worker_share_backend_namespace_and_account_limit(self):
        expected = {
            "BROWSERLESS_CAPACITY_BACKEND": "postgresql",
            "BROWSERLESS_CAPACITY_NAMESPACE": "roster-scout-browserless-account",
            "BROWSERLESS_ACCOUNT_MAX_SESSIONS": "2",
        }
        compose_services = self.compose["services"]
        for service_name in ("roster-scout", "worker"):
            environment = compose_services[service_name]["environment"]
            with self.subTest(platform="compose", service=service_name):
                self.assertEqual(
                    {key: environment.get(key) for key in expected},
                    expected,
                )
        self.assertEqual(
            {key: self.fly["env"].get(key) for key in expected},
            expected,
        )

    def test_worker_concurrency_is_bounded_by_account_limit(self):
        from scripts.run_worker import worker_command

        fly_environment = self.fly["env"]
        command = worker_command(fly_environment)
        self.assertEqual(command[-2:], ["--concurrency", "1"])
        with self.assertRaisesRegex(ValueError, "cannot exceed"):
            worker_command({
                **fly_environment,
                "CELERY_WORKER_CONCURRENCY": "2",
                "BROWSERLESS_ACCOUNT_MAX_SESSIONS": "1",
            })

    def test_fly_http_service_and_data_mount_are_app_only(self):
        self.assertEqual(self.fly["http_service"]["processes"], ["app"])
        self.assertEqual(len(self.fly["mounts"]), 1)
        self.assertEqual(self.fly["mounts"][0]["processes"], ["app"])
        self.assertIn("worker", self.fly["processes"])
        self.assertNotIn("gunicorn", self.fly["processes"]["worker"])

    def test_fly_config_contains_no_embedded_service_credentials(self):
        sensitive_names = {
            "BROWSERLESS_TOKEN",
            "DATABASE_URL",
            "CELERY_BROKER_URL",
        }
        self.assertTrue(sensitive_names.isdisjoint(self.fly["env"]))
        source = (PROJECT_ROOT / "fly.toml").read_text(encoding="utf-8")
        self.assertNotIn("postgresql://", source)
        self.assertNotIn("postgresql+psycopg://", source)
        self.assertNotIn("redis://", source)


if __name__ == "__main__":
    unittest.main()
