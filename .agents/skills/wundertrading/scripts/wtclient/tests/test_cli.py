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



class TestExchangesParser(unittest.TestCase):
    def test_exchanges_profiles(self):
        args = build_parser().parse_args(["exchanges", "profiles"])
        self.assertEqual(args.surface, "exchanges")
        self.assertEqual(args.action, "profiles")
        self.assertFalse(args.execute)

    def test_exchanges_limits(self):
        args = build_parser().parse_args(["exchanges", "limits"])
        self.assertEqual(args.action, "limits")

    def test_exchanges_create_paper(self):
        args = build_parser().parse_args(
            ["exchanges", "create-paper", "demo-hype", "--family", "HYPERLIQUID"]
        )
        self.assertEqual(args.action, "create-paper")
        self.assertEqual(args.name, "demo-hype")
        self.assertEqual(args.family, "HYPERLIQUID")
        self.assertFalse(args.execute)
        self.assertEqual(args.trade_mode, "hedge_mode")
        self.assertEqual(args.margin_mode, "cross")

    def test_exchanges_create_paper_execute(self):
        args = build_parser().parse_args(
            ["exchanges", "create-paper", "demo-hype", "--family", "HYPERLIQUID", "--execute"]
        )
        self.assertTrue(args.execute)

    def test_exchanges_ensure_spec(self):
        args = build_parser().parse_args(
            ["exchanges", "ensure", "--spec", '{"hyperliquid":["demo-hype"]}']
        )
        self.assertEqual(args.action, "ensure")
        self.assertEqual(args.spec, '{"hyperliquid":["demo-hype"]}')
        self.assertFalse(args.execute)


class TestExchangesCliDryRun(unittest.TestCase):
    """Dry runs must print the planned body WITHOUT touching secrets."""

    def test_create_paper_dry_run(self, capsys=None):
        import contextlib
        import io

        from wtclient.cli import main

        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            code = main(["exchanges", "create-paper", "demo-hype", "--family", "HYPERLIQUID"])
        self.assertEqual(code, 0)
        out = buf.getvalue()
        self.assertIn("dry run", out)
        self.assertIn("demo-hype", out)
        self.assertIn('"exchangeFamily": "HYPERLIQUID"', out)
        self.assertIn('"paperTrading": true', out)
        # placeholder 32-hex keys, never real ones
        self.assertRegex(out, r'"api": "[0-9a-f]{32}"')

    def test_ensure_dry_run(self):
        import contextlib
        import io

        from wtclient.cli import main

        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            code = main(["exchanges", "ensure", "--spec", '{"hyperliquid":["demo-hype"]}'])
        self.assertEqual(code, 0)
        out = buf.getvalue()
        self.assertIn("dry run", out)
        self.assertIn("HYPERLIQUID", out)


if __name__ == "__main__":
    unittest.main()
