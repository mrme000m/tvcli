import unittest
from pathlib import Path

import yaml

from prime_stack.config import FLEET_PRESET_FILES, FLEET_PRESETS

PRESET_SRC = Path(__file__).resolve().parents[2] / "presets"


class VendoredPresetTests(unittest.TestCase):
    def test_every_preset_dir_is_registered(self):
        dirs = sorted(p.name for p in PRESET_SRC.iterdir() if p.is_dir())
        self.assertEqual(sorted(FLEET_PRESETS), dirs,
                         "bootstrapping/presets/ and FLEET_PRESETS must stay in sync")

    def test_preset_files_exist_and_parse(self):
        for name in FLEET_PRESETS:
            for f in FLEET_PRESET_FILES:
                path = PRESET_SRC / name / f
                self.assertTrue(path.is_file(), f"{name}/{f} missing")
                self.assertGreater(path.stat().st_size, 0, f"{name}/{f} empty")
            doc = yaml.safe_load((PRESET_SRC / name / "preset.yml").read_text())
            for key in ("name", "description", "order"):
                self.assertIn(key, doc, f"{name}/preset.yml missing {key}")
            self.assertIsInstance(doc["order"], int)

    def test_agent_cordis_has_persona_and_skill_surface(self):
        for name in FLEET_PRESETS:
            text = (PRESET_SRC / name / "agent.cordis.yml").read_text()
            for marker in ("- id: persona", "skill-filesystem", "tool-skill"):
                self.assertIn(marker, text, f"{name}/agent.cordis.yml missing {marker}")


if __name__ == "__main__":
    unittest.main()
