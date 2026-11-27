"""Compatibility checks for CJSL exception-classification hooks."""

import importlib
import inspect
import unittest
from importlib.metadata import version
from types import CodeType


class OpenpyxlCompatibilityTests(unittest.TestCase):
    def test_exception_classification_hooks(self):
        hooks = (
            ("openpyxl.reader.excel", "ExcelReader.read"),
            ("openpyxl.descriptors.base", "Set.__set__"),
            ("openpyxl.worksheet._reader", "_cast_number"),
        )

        installed = version("openpyxl")
        guidance = (
            f"Openpyxl {installed}: incompatible exception "
            "classification hook. Install the tested dependencies "
            "from requirements.txt and review dependency upgrades."
        )

        for module_name, attribute_path in hooks:
            with self.subTest(
                module=module_name, hook=attribute_path
            ):
                try:
                    hook = importlib.import_module(module_name)
                    for attribute in attribute_path.split("."):
                        hook = getattr(hook, attribute)
                except (ImportError, AttributeError) as exc:
                    self.fail(f"{guidance} {exc}")

                self.assertTrue(
                    inspect.isfunction(hook), guidance
                )
                self.assertEqual(
                    hook.__module__, module_name, guidance
                )
                self.assertIsInstance(
                    getattr(hook, "__code__", None),
                    CodeType,
                    guidance,
                )


if __name__ == "__main__":
    unittest.main()
