import contextlib
import importlib
import io
import json
import sys
import tempfile
import unittest
from pathlib import Path

from jsonschema import Draft202012Validator

from galaxy_cli.feishu_capabilities import DOMAINS
from galaxy_cli.cli import main
from galaxy_cli.registry import ToolError, ToolRegistry, load_registry


class CliContractTests(unittest.TestCase):
    def run_cli(self, *arguments):
        stdout, stderr = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
            code = main(list(arguments))
        return code, stdout.getvalue(), stderr.getvalue()

    def test_help_catalog_and_self_described_output_match(self):
        code, stdout, stderr = self.run_cli("help")
        self.assertEqual(code, 0)
        self.assertEqual(stderr, "")
        result = json.loads(stdout)
        self.assertEqual(set(result), {"version", "data"})
        self.assertEqual(len(result["data"]), 19 + len(DOMAINS) + int(any(t["tool"] == "youzan.product-search" for t in result["data"])))
        self.assertEqual(set(result["data"][0]), {"tool", "category", "description"})
        self.assertEqual({t["tool"] for t in result["data"]} | {"youzan.product-search"}, {"feishu.authorized-users", "time.now", "feishu.search", "feishu.document-get", "feishu.folder-list", "feishu.file-download", "excel.create", "file.publish", "youzan.material-category", "youzan.material-list", "youzan.material-upload", "youzan.product-get", "youzan.product-options", "youzan.product-group", "youzan.product-update", "youzan.product-search", "youzan.customer-get", "youzan.customer-search", "youzan.customer-count", "youzan.business-report"} | {"feishu."+d for d in DOMAINS})
        self.assertLess(len(stdout), 128000)
        self.assertNotIn("input_schema", stdout)
        self.assertNotIn("output_schema", stdout)
        Draft202012Validator(load_registry().get("help").describe()["output_schema"]).validate(result)
        code, output, _ = self.run_cli("help", "help")
        self.assertEqual(code, 0)
        self.assertEqual(json.loads(output)["data"][0]["tool"], "help")

    def test_missing_tool_invalid_json_and_unexpected_parameters_fail_cleanly(self):
        for arguments, expected in [(("missing",), "unknown_tool"),
                                    (("help", "missing"), "unknown_tool"),
                                    (("help", "--input", "{broken"), "invalid_json"),
                                    (("help", "--input", "[]"), "invalid_input"),
                                    (("help", "--input", '{"extra":"secret-example-value"}'), "invalid_input"),
                                    (("help", "--unknown"), "invalid_arguments")]:
            with self.subTest(arguments=arguments):
                code, stdout, stderr = self.run_cli(*arguments)
                self.assertNotEqual(code, 0)
                result = json.loads(stdout)
                self.assertIn("error", result)
                self.assertEqual(result["error"]["code"], expected)
                self.assertNotIn("secret-example-value", stdout + stderr)

    def test_text_and_json_input_forms(self):
        code, output, _ = self.run_cli("help", "--format", "text")
        self.assertEqual(code, 0)
        self.assertIn("youzan", output)
        self.assertIn("customer-search", output)
        self.assertNotIn("schema", output)
        code, output, _ = self.run_cli("help", "--input", '{"tool":"help"}')
        self.assertEqual(code, 0)
        self.assertEqual(json.loads(output)["data"][0]["tool"], "help")

    def test_registry_extension_automatically_appears_in_help(self):
        registry = load_registry()
        registry.register(name="orders.get", description="A temporary test tool", input_schema={"type": "object"},
                          output_schema={"type": "object"}, handler=lambda _: {"result": "test-only"})
        catalog = registry.invoke("help", {})
        self.assertEqual({t["tool"] for t in catalog["data"]} | {"youzan.product-search"}, {"feishu.authorized-users", "time.now", "orders.get", "feishu.search", "feishu.document-get", "feishu.folder-list", "feishu.file-download", "excel.create", "file.publish", "youzan.material-category", "youzan.material-list", "youzan.material-upload", "youzan.product-get", "youzan.product-options", "youzan.product-group", "youzan.product-update", "youzan.product-search", "youzan.customer-get", "youzan.customer-search", "youzan.customer-count", "youzan.business-report"} | {"feishu."+d for d in DOMAINS})
        single = registry.invoke("help", {"tool": "orders.get"})
        self.assertEqual(single["data"], [{"tool": "orders.get", "category": None, "description": "A temporary test tool"}])

    def test_new_tool_modules_are_discovered_without_a_manual_import_list(self):
        with tempfile.TemporaryDirectory() as directory:
            package = Path(directory) / "galaxy_registry_fixture"
            package.mkdir()
            (package / "__init__.py").write_text("")
            (package / "example.py").write_text('''def register(registry):
    @registry.tool(name="example", description="Discovered test tool", input_schema={"type": "object"}, output_schema={"type": "object"})
    def example(arguments):
        return {"discovered": True}
''')
            sys.path.insert(0, directory)
            importlib.invalidate_caches()
            try:
                registry = load_registry("galaxy_registry_fixture")
                self.assertEqual(registry.invoke("example", {}), {"discovered": True})
            finally:
                sys.path.remove(directory)
                for name in list(sys.modules):
                    if name.startswith("galaxy_registry_fixture"):
                        del sys.modules[name]

    def test_duplicate_registrations_and_invalid_results_are_rejected(self):
        registry = ToolRegistry()
        definition = {"name": "example", "description": "Example", "input_schema": {"type": "object"},
                      "output_schema": {"type": "integer"}, "handler": lambda _: "secret-example-output"}
        registry.register(**definition)
        with self.assertRaises(ToolError) as duplicate:
            registry.register(**definition)
        self.assertEqual(duplicate.exception.code, "duplicate_tool")
        with self.assertRaises(ToolError) as output:
            registry.invoke("example", {})
        self.assertEqual(output.exception.code, "invalid_output")
        self.assertNotIn("secret-example-output", output.exception.message)


if __name__ == "__main__":
    unittest.main()
