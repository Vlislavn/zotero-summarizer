"""Validation reference paths follow the current machine, not an author's home."""
import ast
import sysconfig
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch


class PortableValidationPathsTest(unittest.TestCase):
    def test_reference_paths_follow_home_and_interpreter(self):
        root = Path(__file__).resolve().parents[1]
        cases = [("test_method_validation_client.py", "SOURCE"),
                 ("test_practitioner_runtime16.py", "SOURCE"),
                 ("test_method_validation_client.py", "SITE")]
        with tempfile.TemporaryDirectory() as temporary:
            home = Path(temporary)
            site = home / "site-packages"
            transport = home / ".pi/agent/skills/biomni-codex-subscription/scripts/pi_codex_transport.py"
            with patch.dict("os.environ", {"HOME": str(home)}), patch.object(
                sysconfig, "get_paths", return_value={"purelib": str(site)}
            ):
                for filename, name in cases:
                    with self.subTest(filename=filename, constant=name):
                        path = root / "tests" / filename
                        tree = ast.parse(path.read_text())
                        definitions = [node for node in tree.body if isinstance(node, ast.Assign)
                                       and any(isinstance(target, ast.Name) and target.id == name
                                               for target in node.targets)]
                        self.assertEqual(len(definitions), 1)
                        actual = eval(compile(ast.Expression(definitions[0].value), str(path), "eval"),
                                      {"Path": Path, "sysconfig": sysconfig})
                        self.assertEqual(actual, site if name == "SITE" else transport)


if __name__ == "__main__":
    unittest.main()
