import unittest

from wtclient.cli import build_parser
from wtclient.query import load_json_arg


class TestParser(unittest.TestCase):
    def test_open_api_subcommand(self):
        args = build_parser().parse_args(["open_api", "GET", "/open_api/exchanges"])
        self.assertEqual(args.surface, "open_api")
        self.assertEqual(args.path, "/open_api/exchanges")

    def test_session_transport_choice(self):
        args = build_parser().parse_args(["session", "GET", "/x", "--transport", "browser"])
        self.assertEqual(args.transport, "browser")

    def test_grid_action(self):
        args = build_parser().parse_args(["grid", "list", "--transport", "browser"])
        self.assertEqual(args.action, "list")


class TestLoadJsonArg(unittest.TestCase):
    def test_json_and_file(self):
        import tempfile
        from pathlib import Path
        self.assertEqual(load_json_arg('{"a": 1}'), {"a": 1})
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp) / "body.json"
            p.write_text('[1,2,3]')
            self.assertEqual(load_json_arg("@" + str(p)), [1, 2, 3])


if __name__ == "__main__":
    unittest.main()
